"""Tests for FNS monitoring scheduler state."""

import pytest
from sqlalchemy import select

from connector.models import FnsMonitoringTarget


@pytest.mark.asyncio
async def test_fns_monitoring_target_defaults(async_session):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
    )

    async_session.add(target)
    await async_session.flush()

    assert target.id is not None
    assert target.enabled is False
    assert target.refresh_interval_seconds == 86400
    assert target.last_checked_at is None
    assert target.next_check_at is None
    assert target.lease_owner is None
    assert target.lease_expires_at is None


@pytest.mark.asyncio
async def test_fns_monitoring_target_persistence(async_session):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
        enabled=True,
        refresh_interval_seconds=3600,
    )

    async_session.add(target)
    await async_session.flush()

    stored = await async_session.scalar(
        select(FnsMonitoringTarget).where(
            FnsMonitoringTarget.base_asset == "BTC"
        )
    )

    assert stored is not None
    assert stored.source_id == "fns-krptrate"
    assert stored.enabled is True
    assert stored.refresh_interval_seconds == 3600
