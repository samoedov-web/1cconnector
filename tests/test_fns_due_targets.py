"""FNS scheduler due-target selection tests."""

from datetime import datetime, timedelta, timezone

import pytest

from connector.models import FnsMonitoringTarget
from connector.rates.fns_scheduler import get_due_fns_targets


NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


async def add_target(
    session,
    *,
    asset="BTC",
    enabled=True,
    next_check_at=None,
    lease_expires_at=None,
):
    target = FnsMonitoringTarget(
        base_asset=asset,
        source_id="fns-krptrate",
        enabled=enabled,
        refresh_interval_seconds=3600,
        next_check_at=next_check_at,
        lease_expires_at=lease_expires_at,
    )
    session.add(target)
    await session.flush()
    return target


@pytest.mark.asyncio
async def test_never_checked_is_due(async_session):
    target = await add_target(async_session)

    due = await get_due_fns_targets(
        async_session, now=NOW
    )

    assert [x.id for x in due] == [target.id]


@pytest.mark.asyncio
async def test_past_schedule_is_due(async_session):
    target = await add_target(
        async_session,
        next_check_at=NOW - timedelta(seconds=1),
    )

    due = await get_due_fns_targets(
        async_session, now=NOW
    )

    assert [x.id for x in due] == [target.id]


@pytest.mark.asyncio
async def test_future_schedule_is_not_due(async_session):
    await add_target(
        async_session,
        next_check_at=NOW + timedelta(seconds=1),
    )

    assert await get_due_fns_targets(
        async_session, now=NOW
    ) == ()


@pytest.mark.asyncio
async def test_disabled_target_is_not_due(async_session):
    await add_target(async_session, enabled=False)

    assert await get_due_fns_targets(
        async_session, now=NOW
    ) == ()


@pytest.mark.asyncio
async def test_active_lease_excludes_target(async_session):
    await add_target(
        async_session,
        lease_expires_at=NOW + timedelta(seconds=30),
    )

    assert await get_due_fns_targets(
        async_session, now=NOW
    ) == ()


@pytest.mark.asyncio
async def test_expired_lease_is_due(async_session):
    target = await add_target(
        async_session,
        lease_expires_at=NOW - timedelta(seconds=1),
    )

    due = await get_due_fns_targets(
        async_session, now=NOW
    )

    assert [x.id for x in due] == [target.id]
