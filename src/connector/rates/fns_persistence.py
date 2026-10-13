"""Verified FNS quote persistence boundary.

Original archive bytes must already exist in the evidence vault.
Database transaction ownership belongs to the caller.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from connector.rates.fns_evidence_store import FnsEvidenceStore
from connector.rates.market import (
    MarketQuoteResult,
    persist_market_quote_evidence,
)


async def persist_verified_fns_quote(
    session: AsyncSession,
    quote: MarketQuoteResult,
    store: FnsEvidenceStore,
    *,
    observed_at: datetime,
):
    """Verify stored originals before inserting MarketQuoteEvidence."""
    if quote.source != "fns-krptrate":
        raise ValueError("Not an FNS quotation")
    if quote.valuation_context != "tax":
        raise ValueError("FNS quotation must be tax-only")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")

    metadata = quote.raw_payload.get("_official_archive")
    evidence = quote.raw_payload.get("_evidence")

    if not isinstance(metadata, dict) or not isinstance(evidence, dict):
        raise ValueError("Missing FNS provenance metadata")

    if metadata.get("persistence_status") != "verified":
        raise ValueError("FNS original evidence not persisted")
    if metadata.get("storage_scheme") != "sha256":
        raise ValueError("Unsupported evidence storage scheme")

    zip_hash = metadata.get("zip_sha256")
    json_hash = metadata.get("json_sha256")
    member_name = metadata.get("member_name")

    if not isinstance(zip_hash, str) or not isinstance(json_hash, str):
        raise ValueError("Missing original evidence hashes")
    if not isinstance(member_name, str):
        raise ValueError("Missing archive member name")

    raw_zip = store.read_verified(zip_hash)
    raw_json = store.read_verified(json_hash)

    if quote.evidence_hash != json_hash:
        raise ValueError("Quotation/JSON evidence hash mismatch")
    if evidence.get("processed_payload_sha256") != json_hash:
        raise ValueError("Processed JSON evidence hash mismatch")
    if evidence.get("upstream_archive_sha256") != zip_hash:
        raise ValueError("Archive provenance hash mismatch")
    if hashlib.sha256(raw_json).hexdigest() != json_hash:
        raise ValueError("JSON evidence hash mismatch")

    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].filename != member_name:
            raise ValueError("Archive membership mismatch")
        if archive.read(members[0]) != raw_json:
            raise ValueError("Archive contents mismatch")

    rows = json.loads(raw_json.decode("utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError("Invalid FNS JSON source")

    source_row = {
        key: value for key, value in quote.raw_payload.items()
        if not key.startswith("_")
    }

    if source_row not in rows:
        raise ValueError("Quoted row absent from stored FNS source")

    from connector.rates.fns import _parse_datetime, _parse_decimal

    if (
        str(source_row.get("kodVal", "")).upper() != quote.base_asset
        or source_row.get("isAnnulled") != 0
        or _parse_datetime(source_row["kursData"]) != quote.quote_as_of
        or _parse_decimal(
            source_row["krptKurs"], field="krptKurs"
        ) != quote.rate
        or quote.quote_currency != "RUB"
    ):
        raise ValueError("Quote differs from original FNS row")

    return await persist_market_quote_evidence(
        session, quote, observed_at=observed_at
    )
