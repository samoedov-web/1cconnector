"""Tests for persistent FNS scheduler leases."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from connector.models import FnsMonitoringTarget
from connector.rates.fns_scheduler import acquire_fns_lease


NOW = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)


async def add_target(session, *, enabled=True):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
        enabled=enabled,
        refresh_interval_seconds=3600,
    )
    session.add(target)
    await session.flush()
    return target


@pytest.mark.asyncio
async def test_first_lease_acquisition(async_session):
    target = await add_target(async_session)

    acquired = await acquire_fns_lease(
        async_session,
        target_id=target.id,
        owner="11111111-1111-4111-8111-111111111111",
        now=NOW,
    )

    assert acquired is True
    await async_session.refresh(target)
    assert target.lease_owner == "11111111-1111-4111-8111-111111111111"
    assert target.lease_expires_at.replace(
        tzinfo=timezone.utc
    ) == NOW + timedelta(seconds=300)


@pytest.mark.asyncio
async def test_active_lease_prevents_reacquisition(async_session):
    target = await add_target(async_session)

    first = await acquire_fns_lease(
        async_session,
        target_id=target.id,
        owner="11111111-1111-4111-8111-111111111111",
        now=NOW,
    )

    second = await acquire_fns_lease(
        async_session,
        target_id=target.id,
        owner="22222222-2222-4222-8222-222222222222",
        now=NOW + timedelta(seconds=30),
    )

    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed(async_session):
    target = await add_target(async_session)

    first = await acquire_fns_lease(
        async_session,
        target_id=target.id,
        owner="11111111-1111-4111-8111-111111111111",
        now=NOW,
        lease_seconds=60,
    )

    second = await acquire_fns_lease(
        async_session,
        target_id=target.id,
        owner="22222222-2222-4222-8222-222222222222",
        now=NOW + timedelta(seconds=61),
        lease_seconds=60,
    )

    assert first is True
    assert second is True

    await async_session.refresh(target)
    assert target.lease_owner == "22222222-2222-4222-8222-222222222222"


@pytest.mark.asyncio
async def test_disabled_target_cannot_be_claimed(async_session):
    target = await add_target(async_session, enabled=False)

    acquired = await acquire_fns_lease(
        async_session,
        target_id=target.id,
        owner="11111111-1111-4111-8111-111111111111",
        now=NOW,
    )

    assert acquired is False
