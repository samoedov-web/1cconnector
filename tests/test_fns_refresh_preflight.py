"""Preflight lease enforcement before FNS provider I/O."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from connector.models import FnsMonitoringTarget
from connector.rates.fns_refresh import (
    FnsLeaseLostError,
    run_fns_refresh,
)
from connector.rates.fns_scheduler import acquire_fns_lease


NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["wrong_owner", "expired"])
async def test_invalid_lease_blocks_provider(
    async_session,
    case,
):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
        organizer="Binance",
        trading_pair="BTCUSDT",
        enabled=True,
        refresh_interval_seconds=3600,
    )
    async_session.add(target)
    await async_session.flush()

    owner = str(uuid4())
    assert await acquire_fns_lease(
        async_session,
        target_id=target.id,
        owner=owner,
        now=NOW,
        lease_seconds=30,
    )

    caller = str(uuid4()) if case == "wrong_owner" else owner
    current = (
        NOW + timedelta(seconds=31)
        if case == "expired"
        else NOW + timedelta(seconds=10)
    )

    service = SimpleNamespace(collect=AsyncMock())

    with pytest.raises(
        FnsLeaseLostError,
        match="before provider request",
    ):
        await run_fns_refresh(
            async_session,
            service=service,
            target_id=target.id,
            base_asset="BTC",
            owner=caller,
            as_of=NOW,
            observed_at=NOW,
            clock=lambda: current,
        )

    service.collect.assert_not_awaited()
