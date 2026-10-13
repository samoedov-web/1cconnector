"""Fail-closed FNS selector gate tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from connector.models import FnsMonitoringTarget
from connector.rates.fns_scheduler import acquire_fns_lease
from connector.rates.fns_refresh import run_fns_refresh


NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field",
    ["organizer", "trading_pair"],
)
async def test_missing_selector_blocks_provider(
    async_session,
    missing_field,
):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
        organizer="Binance",
        trading_pair="BTCUSDT",
        enabled=True,
        refresh_interval_seconds=3600,
    )
    setattr(target, missing_field, None)

    async_session.add(target)
    await async_session.flush()

    owner = str(uuid4())
    assert await acquire_fns_lease(
        async_session,
        target_id=target.id,
        owner=owner,
        now=NOW,
    )

    service = SimpleNamespace(collect=AsyncMock())

    with pytest.raises(ValueError, match=missing_field.replace("_", " ")):
        await run_fns_refresh(
            async_session,
            service=service,
            target_id=target.id,
            base_asset="BTC",
            owner=owner,
            as_of=NOW,
            observed_at=NOW + timedelta(seconds=10),
            clock=lambda: NOW + timedelta(seconds=10),
        )

    service.collect.assert_not_awaited()
