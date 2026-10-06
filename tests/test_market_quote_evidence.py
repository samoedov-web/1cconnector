from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from connector.models import MarketQuoteEvidence


@pytest.mark.asyncio
async def test_market_quote_evidence_can_be_persisted(
    async_session,
):
    quote_time = datetime(
        2026,
        10,
        6,
        9,
        30,
        tzinfo=timezone.utc,
    )

    evidence = MarketQuoteEvidence(
        base_asset="USDT",
        quote_currency="USD",
        rate=Decimal("0.9998"),
        quote_as_of=quote_time,
        source="official-market-dataset",
        source_url="https://example.invalid/dataset",
        methodology_version="market-quote-v1",
        raw_payload={
            "asset": "USDT",
            "currency": "USD",
            "price": "0.9998",
        },
        evidence_hash="a" * 64,
        observed_at=quote_time,
    )

    async_session.add(evidence)
    await async_session.flush()

    stored = await async_session.scalar(
        select(MarketQuoteEvidence).where(
            MarketQuoteEvidence.id == evidence.id
        )
    )

    assert stored is not None
    assert stored.base_asset == "USDT"
    assert stored.quote_currency == "USD"
    assert stored.rate == Decimal("0.9998")
    assert stored.methodology_version == "market-quote-v1"
    assert stored.evidence_hash == "a" * 64
    assert stored.raw_payload["price"] == "0.9998"


@pytest.mark.asyncio
async def test_market_quote_evidence_is_not_transaction_scoped(
    async_session,
):
    quote_time = datetime(
        2026,
        10,
        6,
        9,
        30,
        tzinfo=timezone.utc,
    )

    evidence = MarketQuoteEvidence(
        base_asset="USDT",
        quote_currency="USD",
        rate=Decimal("1"),
        quote_as_of=quote_time,
        source="official-market-dataset",
        source_url=None,
        methodology_version="market-quote-v1",
        raw_payload={"price": "1"},
        evidence_hash="b" * 64,
        observed_at=quote_time,
    )

    async_session.add(evidence)
    await async_session.flush()

    assert evidence.id is not None

    # External quote evidence may be reused/referenced by multiple accounting
    # calculations and therefore must not require a transaction_id itself.
    assert not hasattr(MarketQuoteEvidence, "transaction_id")


def test_market_quote_evidence_has_explicit_methodology_and_hash():
    columns = MarketQuoteEvidence.__table__.columns

    assert "methodology_version" in columns
    assert "raw_payload" in columns
    assert "evidence_hash" in columns
    assert "observed_at" in columns
