"""Read-only FNS tax quotation provider."""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta
from typing import Callable
from urllib.request import Request, urlopen

from connector.rates.fns import FnsKrptrateParser
from connector.rates.fns_evidence_store import FnsEvidenceStore
from connector.rates.fns_discovery import (
    PASSPORT_URL,
    discover_fns_archive,
)
from connector.rates.fns_transport import (
    FnsArchive,
    fetch_fns_archive,
)
from connector.rates.market import (
    MarketQuoteResult,
    MarketValuationSource,
)


def fetch_fns_passport() -> str:
    """Fetch a bounded official HTML passport."""
    request = Request(
        PASSPORT_URL,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError("FNS passport HTTP failure")
        if response.geturl() != PASSPORT_URL:
            raise ValueError("Unexpected passport redirect")
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError("FNS passport exceeds size limit")
    return payload.decode("utf-8")


class FnsKrptrateProvider(MarketValuationSource):
    """Tax-only provider: passport → ZIP → JSON → quote.

    The permitted maximum age must be supplied by the caller.
    It is a product control, not an asserted FNS accounting rule.
    """

    source_name = "fns-krptrate"
    valuation_context = "tax"

    def __init__(
        self,
        *,
        max_quote_age: timedelta,
        organizer: str | None = None,
        trading_pair: str | None = None,
        passport_loader: Callable[[], str] = fetch_fns_passport,
        archive_loader: Callable[[str], FnsArchive] = fetch_fns_archive,
        evidence_store: FnsEvidenceStore | None = None,
        require_persistence: bool = False,
    ):
        if max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be positive")
        self.max_quote_age = max_quote_age
        self.organizer = organizer
        self.trading_pair = trading_pair
        self.passport_loader = passport_loader
        self.archive_loader = archive_loader
        self.evidence_store = evidence_store
        self.require_persistence = require_persistence
        if require_persistence and evidence_store is None:
            raise ValueError(
                "Persistent evidence store is required"
            )

    def _get_quote(
        self,
        base_asset: str,
        quote_currency: str,
        as_of: datetime,
    ) -> MarketQuoteResult:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if quote_currency.upper() != "RUB":
            raise LookupError("FNS provider is RUB-only")

        html = self.passport_loader()
        url = discover_fns_archive(html)
        archive = self.archive_loader(url)

        if archive.source_url != url:
            raise ValueError("FNS archive source mismatch")
        if hashlib.sha256(archive.raw_zip).hexdigest() != archive.sha256:
            raise ValueError("FNS ZIP evidence mismatch")
        if hashlib.sha256(archive.raw_json).hexdigest() != archive.json_sha256:
            raise ValueError("FNS JSON evidence mismatch")

        stored = None
        if self.evidence_store is not None:
            # Any storage failure prevents returning a usable quote.
            stored = self.evidence_store.save_archive(archive)

            if (
                stored.zip_sha256 != archive.sha256
                or stored.json_sha256 != archive.json_sha256
            ):
                raise ValueError("Stored FNS evidence mismatch")

            self.evidence_store.read_verified(
                stored.zip_sha256
            )
            self.evidence_store.read_verified(
                stored.json_sha256
            )

        parser = FnsKrptrateParser(
            source_url=url,
            evidence_hash=archive.sha256,
        )
        quote = parser.parse(
            archive.raw_json,
            base_asset=base_asset,
            as_of=as_of,
            organizer=self.organizer,
            trading_pair=self.trading_pair,
        )

        age = as_of - quote.quote_as_of
        if age < timedelta(0) or age > self.max_quote_age:
            raise LookupError(
                "FNS quotation exceeds configured age limit"
            )

        from dataclasses import replace
        return replace(
            quote,
            raw_payload={
                **quote.raw_payload,
                "_official_archive": {
                    "source_url": archive.source_url,
                    "zip_sha256": archive.sha256,
                    "json_sha256": archive.json_sha256,
                    "member_name": archive.member_name,
                    "passport_url": PASSPORT_URL,
                    "persistence_status": (
                        "verified"
                        if stored is not None
                        else "not_persisted"
                    ),
                    "storage_scheme": (
                        "sha256"
                        if stored is not None
                        else None
                    ),
                },
            },
        )

    async def get_market_quote(
        self,
        base_asset: str,
        quote_currency: str,
        as_of: datetime,
    ) -> MarketQuoteResult:
        return await asyncio.to_thread(
            self._get_quote,
            base_asset,
            quote_currency,
            as_of,
        )
