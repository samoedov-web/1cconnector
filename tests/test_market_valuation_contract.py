from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from connector.models import MarketQuoteEvidence
from connector.rates.market import (
    MarketQuoteResult,
    MarketValuationSource,
    persist_market_quote_evidence,
)


QUOTE_TIME = datetime(
    2026,
    10,
    6,
    9,
    30,
    tzinfo=timezone.utc,
)


class FakeVedMarketSource(MarketValuationSource):
    source_name = "fake-ved-market"
    valuation_context = "ved"

    async def get_market_quote(
        self,
        base_asset,
        quote_currency,
        as_of,
    ):
        if (
            base_asset.upper(),
            quote_currency.upper(),
        ) != ("USDT", "USD"):
            raise LookupError(
                f"unsupported market pair "
                f"{base_asset}/{quote_currency}"
            )

        return MarketQuoteResult(
            base_asset="USDT",
            quote_currency="USD",
            rate=Decimal("0.9998"),
            quote_as_of=as_of,
            source=self.source_name,
            source_url="https://example.invalid/usdt-usd",
            methodology_version="ved-market-v1",
            valuation_context=self.valuation_context,
            raw_payload={
                "pair": "USDT/USD",
                "close": "0.9998",
            },
            evidence_hash="a" * 64,
        )


@pytest.mark.asyncio
async def test_market_source_returns_normalized_decimal_quote():
    source = FakeVedMarketSource()

    result = await source.get_market_quote(
        "USDT",
        "USD",
        QUOTE_TIME,
    )

    assert result.base_asset == "USDT"
    assert result.quote_currency == "USD"
    assert result.rate == Decimal("0.9998")
    assert isinstance(result.rate, Decimal)
    assert result.valuation_context == "ved"
    assert result.methodology_version == "ved-market-v1"


@pytest.mark.asyncio
async def test_market_source_fails_closed_for_unsupported_pair():
    source = FakeVedMarketSource()

    with pytest.raises(
        LookupError,
        match="unsupported market pair",
    ):
        await source.get_market_quote(
            "USDT",
            "RUB",
            QUOTE_TIME,
        )


@pytest.mark.asyncio
async def test_market_quote_result_persists_as_evidence(
    async_session,
):
    source = FakeVedMarketSource()

    result = await source.get_market_quote(
        "USDT",
        "USD",
        QUOTE_TIME,
    )

    observed_at = datetime(
        2026,
        10,
        6,
        9,
        31,
        tzinfo=timezone.utc,
    )

    evidence = await persist_market_quote_evidence(
        async_session,
        result,
        observed_at=observed_at,
    )

    stored = await async_session.scalar(
        select(MarketQuoteEvidence).where(
            MarketQuoteEvidence.id == evidence.id
        )
    )

    assert stored is not None
    assert stored.base_asset == "USDT"
    assert stored.quote_currency == "USD"
    assert stored.rate == Decimal("0.9998")
    assert stored.methodology_version == "ved-market-v1"
    assert stored.evidence_hash == "a" * 64
    assert stored.raw_payload["pair"] == "USDT/USD"
    assert stored.raw_payload["_valuation_context"] == "ved"
    assert stored.observed_at == observed_at


@pytest.mark.asyncio
async def test_persistence_does_not_create_rate_snapshot(
    async_session,
):
    from sqlalchemy import func

    from connector.models import RateSnapshot

    result = MarketQuoteResult(
        base_asset="BTC",
        quote_currency="RUB",
        rate=Decimal("10000000"),
        quote_as_of=QUOTE_TIME,
        source="fns-tax-example",
        source_url=None,
        methodology_version="tax-market-v1",
        valuation_context="tax",
        raw_payload={"krptKurs": "10000000"},
        evidence_hash="b" * 64,
    )

    await persist_market_quote_evidence(
        async_session,
        result,
        observed_at=QUOTE_TIME,
    )

    rate_snapshot_count = await async_session.scalar(
        select(func.count()).select_from(RateSnapshot)
    )

    evidence_count = await async_session.scalar(
        select(func.count()).select_from(MarketQuoteEvidence)
    )

    assert evidence_count == 1
    assert rate_snapshot_count == 0


def test_tax_and_ved_contexts_are_explicitly_distinct():
    tax_result = MarketQuoteResult(
        base_asset="BTC",
        quote_currency="RUB",
        rate=Decimal("10000000"),
        quote_as_of=QUOTE_TIME,
        source="fns-krptrate",
        source_url=None,
        methodology_version="fns-tax-v1",
        valuation_context="tax",
        raw_payload={},
        evidence_hash="c" * 64,
    )

    ved_result = MarketQuoteResult(
        base_asset="USDT",
        quote_currency="USD",
        rate=Decimal("1"),
        quote_as_of=QUOTE_TIME,
        source="approved-market-source",
        source_url=None,
        methodology_version="ved-market-v1",
        valuation_context="ved",
        raw_payload={},
        evidence_hash="d" * 64,
    )

    assert tax_result.valuation_context == "tax"
    assert ved_result.valuation_context == "ved"
    assert tax_result.valuation_context != ved_result.valuation_context
