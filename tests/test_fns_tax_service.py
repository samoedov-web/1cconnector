"""Tests for FNS tax intelligence orchestration."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from connector.rates.fns_tax_service import FnsTaxIntelligenceService


NOW = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)


def make_provider(context="tax", quote=None, error=None):
    provider = SimpleNamespace(
        valuation_context=context,
        get_market_quote=AsyncMock(),
    )
    if error is not None:
        provider.get_market_quote.side_effect = error
    else:
        provider.get_market_quote.return_value = quote
    return provider


def make_quote(
    context="tax",
    source="fns-krptrate",
    asset="BTC",
    currency="RUB",
):
    return SimpleNamespace(
        valuation_context=context,
        source=source,
        base_asset=asset,
        quote_currency=currency,
    )


@pytest.mark.asyncio
async def test_collect_success():
    quote = make_quote()
    provider = make_provider(quote=quote)
    store = object()
    service = FnsTaxIntelligenceService(provider, store)

    with patch(
        "connector.rates.fns_tax_service."
        "persist_verified_fns_quote",
        new_callable=AsyncMock,
    ) as persist:
        persist.return_value = SimpleNamespace(id=42)

        result = await service.collect(
            object(),
            base_asset="BTC",
            as_of=NOW,
            observed_at=NOW,
        )

    assert result.evidence_id == 42
    assert result.quote is quote
    provider.get_market_quote.assert_awaited_once_with(
        "BTC", "RUB", NOW
    )
    persist.assert_awaited_once()


def test_reject_ved_provider():
    with pytest.raises(ValueError, match="Tax-only"):
        FnsTaxIntelligenceService(
            make_provider(context="ved"),
            object(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quote, expected",
    [
        (make_quote(context="ved"), "valuation context"),
        (make_quote(source="untrusted"), "quotation source"),
        (make_quote(asset="ETH"), "quotation asset"),
        (make_quote(currency="USD"), "quotation currency"),
    ],
)
async def test_reject_invalid_quote(quote, expected):
    service = FnsTaxIntelligenceService(
        make_provider(quote=quote),
        object(),
    )

    with patch(
        "connector.rates.fns_tax_service."
        "persist_verified_fns_quote",
        new_callable=AsyncMock,
    ) as persist:
        with pytest.raises(ValueError, match=expected):
            await service.collect(
                object(),
                base_asset="BTC",
                as_of=NOW,
                observed_at=NOW,
            )
        persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failure_is_propagated():
    service = FnsTaxIntelligenceService(
        make_provider(error=LookupError("FNS unavailable")),
        object(),
    )

    with pytest.raises(LookupError, match="FNS unavailable"):
        await service.collect(
            object(),
            base_asset="BTC",
            as_of=NOW,
            observed_at=NOW,
        )


@pytest.mark.asyncio
async def test_persistence_failure_is_propagated():
    service = FnsTaxIntelligenceService(
        make_provider(quote=make_quote()),
        object(),
    )

    with patch(
        "connector.rates.fns_tax_service."
        "persist_verified_fns_quote",
        new_callable=AsyncMock,
    ) as persist:
        persist.side_effect = ValueError("Corrupted evidence")

        with pytest.raises(ValueError, match="Corrupted evidence"):
            await service.collect(
                object(),
                base_asset="BTC",
                as_of=NOW,
                observed_at=NOW,
            )


@pytest.mark.asyncio
async def test_naive_timestamps_rejected():
    provider = make_provider(quote=make_quote())
    service = FnsTaxIntelligenceService(provider, object())
    naive = datetime(2026, 10, 13, 9, 0)

    with pytest.raises(ValueError, match="as_of"):
        await service.collect(
            object(),
            base_asset="BTC",
            as_of=naive,
            observed_at=NOW,
        )

    with pytest.raises(ValueError, match="observed_at"):
        await service.collect(
            object(),
            base_asset="BTC",
            as_of=NOW,
            observed_at=naive,
        )

    provider.get_market_quote.assert_not_awaited()
