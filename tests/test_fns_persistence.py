"""FNS evidence → existing MarketQuoteEvidence integration."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, func

from connector.models import MarketQuoteEvidence
from connector.rates.fns_evidence_store import FnsEvidenceStore
from connector.rates.fns_persistence import persist_verified_fns_quote
from connector.rates.fns_provider import FnsKrptrateProvider
from tests.test_fns_provider import URL, make_archive


async def valid_quote(tmp_path):
    store = FnsEvidenceStore(tmp_path / "vault")
    archive = make_archive()
    provider = FnsKrptrateProvider(
        max_quote_age=timedelta(days=1),
        organizer="Binance",
        trading_pair="BTCUSDT",
        passport_loader=lambda: f'<a href="{URL}">ZIP</a>',
        archive_loader=lambda _: archive,
        evidence_store=store,
        require_persistence=True,
    )
    as_of = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    quote = await provider.get_market_quote("BTC", "RUB", as_of)
    return quote, store, as_of


@pytest.mark.asyncio
async def test_persist_verified_fns_quote(
    async_session, tmp_path
):
    quote, store, observed_at = await valid_quote(tmp_path)

    evidence = await persist_verified_fns_quote(
        async_session, quote, store, observed_at=observed_at
    )

    stored = await async_session.scalar(
        select(MarketQuoteEvidence).where(
            MarketQuoteEvidence.id == evidence.id
        )
    )
    assert stored is not None
    assert stored.evidence_hash == quote.evidence_hash
    assert stored.rate == quote.rate
    assert stored.raw_payload["_official_archive"][
        "persistence_status"
    ] == "verified"


@pytest.mark.asyncio
async def test_reject_unpersisted_quote(
    async_session, tmp_path
):
    quote, store, observed_at = await valid_quote(tmp_path)
    payload = {
        **quote.raw_payload,
        "_official_archive": {
            **quote.raw_payload["_official_archive"],
            "persistence_status": "not_persisted",
        },
    }
    invalid = replace(quote, raw_payload=payload)

    with pytest.raises(ValueError, match="not persisted"):
        await persist_verified_fns_quote(
            async_session, invalid, store,
            observed_at=observed_at,
        )


@pytest.mark.asyncio
async def test_reject_tampered_quote_without_db_write(
    async_session, tmp_path
):
    quote, store, observed_at = await valid_quote(tmp_path)
    invalid = replace(
        quote, evidence_hash="0" * 64
    )

    before = await async_session.scalar(
        select(func.count()).select_from(MarketQuoteEvidence)
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        await persist_verified_fns_quote(
            async_session, invalid, store,
            observed_at=observed_at,
        )

    after = await async_session.scalar(
        select(func.count()).select_from(MarketQuoteEvidence)
    )
    assert before == after


@pytest.mark.asyncio
async def test_reject_missing_original_files(
    async_session, tmp_path
):
    quote, store, observed_at = await valid_quote(tmp_path)
    missing_store = FnsEvidenceStore(tmp_path / "empty")

    with pytest.raises(FileNotFoundError):
        await persist_verified_fns_quote(
            async_session, quote, missing_store,
            observed_at=observed_at,
        )
