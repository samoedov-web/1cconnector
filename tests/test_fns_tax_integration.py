"""Integration tests for FNS tax intelligence."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from connector.models import MarketQuoteEvidence, RateSnapshot
from connector.rates.fns_evidence_store import FnsEvidenceStore
from connector.rates.fns_provider import FnsKrptrateProvider
from connector.rates.fns_tax_service import FnsTaxIntelligenceService

from tests.test_fns_provider import URL, make_archive


NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_fns_tax_service_persists_real_evidence(
    async_session,
    tmp_path,
):
    store = FnsEvidenceStore(tmp_path / "fns-evidence")
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

    service = FnsTaxIntelligenceService(provider, store)

    result = await service.collect(
        async_session,
        base_asset="BTC",
        as_of=NOW,
        observed_at=NOW,
    )

    evidence = await async_session.get(
        MarketQuoteEvidence,
        result.evidence_id,
    )

    assert evidence is not None
    assert evidence.source == "fns-krptrate"
    assert evidence.base_asset == "BTC"
    assert evidence.quote_currency == "RUB"
    assert evidence.rate == Decimal("7100000.25")

    assert store.read_verified(
        archive.sha256
    ) == archive.raw_zip

    assert store.read_verified(
        archive.json_sha256
    ) == archive.raw_json

    rate_count = await async_session.scalar(
        select(func.count()).select_from(RateSnapshot)
    )
    assert rate_count == 0


@pytest.mark.asyncio
async def test_fns_tax_service_repeat_preserves_originals(
    async_session,
    tmp_path,
):
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

    service = FnsTaxIntelligenceService(provider, store)

    first = await service.collect(
        async_session,
        base_asset="BTC",
        as_of=NOW,
        observed_at=NOW,
    )

    second = await service.collect(
        async_session,
        base_asset="BTC",
        as_of=NOW,
        observed_at=NOW,
    )

    assert first.quote.evidence_hash == second.quote.evidence_hash
    assert first.evidence_id != second.evidence_id

    assert store.read_verified(archive.sha256) == archive.raw_zip
    assert store.read_verified(
        archive.json_sha256
    ) == archive.raw_json

    objects = [
        p for p in (tmp_path / "vault").rglob("*")
        if p.is_file()
    ]
    assert len(objects) == 2

    evidence_count = await async_session.scalar(
        select(func.count()).select_from(MarketQuoteEvidence)
    )
    assert evidence_count == 2


@pytest.mark.asyncio
async def test_fns_tax_service_rejects_corrupted_original(
    async_session,
    tmp_path,
):
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

    service = FnsTaxIntelligenceService(provider, store)

    first = await service.collect(
        async_session,
        base_asset="BTC",
        as_of=NOW,
        observed_at=NOW,
    )

    assert first.evidence_id is not None

    original_path = store._path(archive.json_sha256)
    original_path.write_bytes(b"corrupted evidence")

    with pytest.raises(ValueError, match="checksum mismatch"):
        await service.collect(
            async_session,
            base_asset="BTC",
            as_of=NOW,
            observed_at=NOW,
        )

    evidence_count = await async_session.scalar(
        select(func.count()).select_from(MarketQuoteEvidence)
    )
    assert evidence_count == 1
