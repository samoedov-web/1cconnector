"""FNS lease completion and release regression tests."""

from datetime import datetime, timedelta, timezone

import pytest

from connector.models import FnsMonitoringTarget
from connector.rates.fns_scheduler import (
    acquire_fns_lease,
    complete_fns_lease,
    release_fns_lease,
)


NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


async def target_with_lease(session, *, enabled=True):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
        enabled=enabled,
        refresh_interval_seconds=3600,
    )
    session.add(target)
    await session.flush()
    acquired = await acquire_fns_lease(
        session,
        target_id=target.id,
        owner="11111111-1111-4111-8111-111111111111",
        now=NOW,
    )
    assert acquired is enabled
    return target


@pytest.mark.asyncio
async def test_complete_success(async_session):
    target = await target_with_lease(async_session)

    done = await complete_fns_lease(
        async_session,
        target_id=target.id,
        owner="11111111-1111-4111-8111-111111111111",
        now=NOW + timedelta(seconds=20),
    )

    assert done is True
    await async_session.refresh(target)
    assert target.lease_owner is None
    assert target.lease_expires_at is None
    assert target.last_checked_at is not None


@pytest.mark.asyncio
async def test_complete_updates_next_schedule(async_session):
    target = await target_with_lease(async_session)
    finished = NOW + timedelta(seconds=20)

    assert await complete_fns_lease(
        async_session,
        target_id=target.id,
        owner="11111111-1111-4111-8111-111111111111",
        now=finished,
    )

    await async_session.refresh(target)

    assert target.next_check_at.replace(
        tzinfo=timezone.utc
    ) == finished + timedelta(seconds=3600)


@pytest.mark.asyncio
async def test_release_does_not_advance_schedule(async_session):
    target = await target_with_lease(async_session)

    released = await release_fns_lease(
        async_session,
        target_id=target.id,
        owner="11111111-1111-4111-8111-111111111111",
        now=NOW + timedelta(seconds=30),
    )

    assert released is True
    await async_session.refresh(target)
    assert target.lease_owner is None
    assert target.lease_expires_at is None
    assert target.last_checked_at is None
    assert target.next_check_at is None


@pytest.mark.asyncio
async def test_wrong_owner_cannot_complete_or_release(async_session):
    target = await target_with_lease(async_session)

    assert not await complete_fns_lease(
        async_session,
        target_id=target.id,
        owner="22222222-2222-4222-8222-222222222222",
        now=NOW + timedelta(seconds=20),
    )

    assert not await release_fns_lease(
        async_session,
        target_id=target.id,
        owner="22222222-2222-4222-8222-222222222222",
        now=NOW + timedelta(seconds=20),
    )

    await async_session.refresh(target)
    assert target.lease_owner == "11111111-1111-4111-8111-111111111111"


@pytest.mark.asyncio
async def test_expired_lease_cannot_complete(async_session):
    target = await target_with_lease(async_session)

    done = await complete_fns_lease(
        async_session,
        target_id=target.id,
        owner="11111111-1111-4111-8111-111111111111",
        now=NOW + timedelta(seconds=301),
    )

    assert done is False


@pytest.mark.asyncio
async def test_disabled_target_cannot_complete(async_session):
    target = await target_with_lease(async_session)

    target.enabled = False
    await async_session.flush()

    done = await complete_fns_lease(
        async_session,
        target_id=target.id,
        owner="11111111-1111-4111-8111-111111111111",
        now=NOW + timedelta(seconds=20),
    )

    assert done is False
