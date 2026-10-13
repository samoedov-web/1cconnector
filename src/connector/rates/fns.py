"""Native parser for official FNS digital-currency market quote dataset.

Dataset:
7707329152-krptrate — Сведения о динамике рыночных котировок цифровых валют.

Important boundary:
- this source is tax-context evidence;
- krptKurs is already the FNS-published RUB quotation;
- no additional CBR conversion is applied here;
- unsupported assets fail closed;
- trading-pair suffixes do not create synthetic asset quotes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from connector.rates.market import (
    MarketQuoteResult,
    MarketValuationSource,
)


EXPECTED_FIELDS = {
    "kursData",
    "kodVal",
    "naimIst",
    "krptKurs",
    "torgPara",
    "kursValuta",
    "isAnnulled",
}


def _parse_decimal(
    value: str,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(value).replace(",", ".")
        )
        if not result.is_finite() or result <= 0:
            raise ValueError(
                f"FNS krptrate {field} must be finite and positive"
            )
        return result
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"FNS krptrate invalid decimal "
            f"in {field}: {value!r}"
        ) from exc


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.strptime(
            value,
            "%d.%m.%Y %H:%M:%S",
        )
    except ValueError as exc:
        raise ValueError(
            f"FNS krptrate invalid kursData: {value!r}"
        ) from exc

    # Dataset carries calendar date/time without explicit offset.
    # We normalize deterministically to UTC for internal evidence timestamps.
    return parsed.replace(tzinfo=timezone.utc)


def _validate_row(row: dict) -> None:
    actual = set(row)

    if actual != EXPECTED_FIELDS:
        missing = sorted(
            EXPECTED_FIELDS - actual
        )
        extra = sorted(
            actual - EXPECTED_FIELDS
        )

        raise ValueError(
            "FNS krptrate schema mismatch: "
            f"missing={missing}, extra={extra}"
        )


class FnsKrptrateParser:
    """Parse native FNS krptrate JSON rows."""

    methodology_version = "fns-krptrate-v4.0"

    def __init__(
        self,
        *,
        source_url: str,
        evidence_hash: str,
    ):
        self.source_url = source_url
        # Optional upstream archive hash supplied by acquisition metadata.
        # It does not authenticate the processed JSON bytes.
        self.upstream_archive_sha256 = evidence_hash

    def parse(
        self,
        payload: bytes,
        *,
        base_asset: str,
        as_of: datetime,
        organizer: str | None = None,
        trading_pair: str | None = None,
    ) -> MarketQuoteResult:
        try:
            text = payload.decode("utf-8-sig")
            rows = json.loads(text)
        except Exception as exc:
            raise ValueError(
                "FNS krptrate payload is not valid UTF-8 JSON"
            ) from exc

        if not isinstance(rows, list):
            raise ValueError(
                "FNS krptrate top-level payload must be a list"
            )

        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError(
                "FNS krptrate as_of must be timezone-aware"
            )

        normalized_asset = base_asset.upper()

        candidates: list[
            tuple[datetime, dict]
        ] = []

        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(
                    "FNS krptrate row must be an object"
                )

            _validate_row(row)

            if row["isAnnulled"] != 0:
                continue

            # Exact asset match only.
            # Example: DOGEUSDT does not imply a USDT quote.
            if (
                str(row["kodVal"]).upper()
                != normalized_asset
            ):
                continue

            if (
                organizer is not None
                and row["naimIst"] != organizer
            ):
                continue

            if (
                trading_pair is not None
                and str(row["torgPara"]).upper()
                != trading_pair.upper()
            ):
                continue

            quote_time = _parse_datetime(
                row["kursData"]
            )

            if quote_time > as_of:
                continue

            candidates.append(
                (
                    quote_time,
                    row,
                )
            )

        if not candidates:
            raise LookupError(
                "FNS krptrate has no active exact asset "
                f"record for {normalized_asset} "
                f"at or before {as_of.isoformat()}"
            )

        latest_time = max(
            quote_time
            for quote_time, _ in candidates
        )

        latest_rows = [
            row
            for quote_time, row in candidates
            if quote_time == latest_time
        ]

        # FNS may publish several organizers and/or trading pairs
        # for the same asset and date. The parser must not invent
        # its own aggregation rule.
        if len(latest_rows) != 1:
            raise LookupError(
                "FNS krptrate has multiple active records for "
                f"{normalized_asset} at "
                f"{latest_time.isoformat()}; "
                "organizer or trading_pair must be specified"
            )

        row = latest_rows[0]

        rate = _parse_decimal(
            row["krptKurs"],
            field="krptKurs",
        )

        kurs_valuta = _parse_decimal(
            row["kursValuta"],
            field="kursValuta",
        )

        return MarketQuoteResult(
            base_asset=normalized_asset,
            quote_currency="RUB",
            rate=rate,
            quote_as_of=latest_time,
            source="fns-krptrate",
            source_url=self.source_url,
            methodology_version=(
                self.methodology_version
            ),
            valuation_context="tax",
            raw_payload={
                **row,
                "_evidence": {
                    "processed_payload_sha256": hashlib.sha256(
                        payload
                    ).hexdigest(),
                    "processed_payload_kind": "json_rows",
                    "upstream_archive_sha256": (
                        self.upstream_archive_sha256
                    ),
                    "upstream_archive_verified": False,
                },
                "_normalized": {
                    "krptKurs": str(rate),
                    "kursValuta": str(
                        kurs_valuta
                    ),
                },
            },
            evidence_hash=hashlib.sha256(payload).hexdigest(),
        )


class FrozenFnsKrptrateSource(
    MarketValuationSource
):
    """Fixture-backed FNS source for deterministic tests/dev."""

    source_name = "fns-krptrate"
    valuation_context = "tax"

    def __init__(
        self,
        fixture_path: Path,
        *,
        source_url: str,
        evidence_hash: str,
        organizer: str | None = None,
        trading_pair: str | None = None,
    ):
        self.fixture_path = fixture_path
        self.organizer = organizer
        self.trading_pair = trading_pair

        self.parser = FnsKrptrateParser(
            source_url=source_url,
            evidence_hash=evidence_hash,
        )

    async def get_market_quote(
        self,
        base_asset: str,
        quote_currency: str,
        as_of: datetime,
    ) -> MarketQuoteResult:
        if quote_currency.upper() != "RUB":
            raise LookupError(
                "FNS krptrate publishes "
                "normalized RUB quotations only"
            )

        payload = self.fixture_path.read_bytes()

        return self.parser.parse(
            payload,
            base_asset=base_asset,
            as_of=as_of,
            organizer=self.organizer,
            trading_pair=self.trading_pair,
        )
