"""FNS target binding regression tests."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from connector.models import FnsMonitoringTarget
from connector.rates.fns_refresh import (
    FnsLeaseLostError,
    run_fns_refresh,
)


NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_wrong_asset_rejected_before_provider(async_session):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
        enabled=True,
        refresh_interval_seconds=3600,
    )
    async_session.add(target)
    await async_session.flush()

    service = SimpleNamespace(collect=AsyncMock())

    with pytest.raises(ValueError, match="target/source mismatch"):
        await run_fns_refresh(
            async_session,
            service=service,
            target_id=target.id,
            base_asset="ETH",
            owner=str(uuid4()),
            as_of=NOW,
            observed_at=NOW,
        )

    service.collect.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_source_rejected_before_provider(async_session):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="unapproved-source",
        enabled=True,
        refresh_interval_seconds=3600,
    )
    async_session.add(target)
    await async_session.flush()

    service = SimpleNamespace(collect=AsyncMock())

    with pytest.raises(ValueError, match="target/source mismatch"):
        await run_fns_refresh(
            async_session,
            service=service,
            target_id=target.id,
            base_asset="BTC",
            owner=str(uuid4()),
            as_of=NOW,
            observed_at=NOW,
        )

    service.collect.assert_not_awaited()
