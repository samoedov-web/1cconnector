"""FNS retry scheduling tests."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from connector.models import FnsMonitoringTarget
from connector.rates.fns_scheduler import (
    acquire_fns_lease,
    fail_fns_lease,
)

NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


async def claimed(session, lease_seconds=300):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
        enabled=True,
        refresh_interval_seconds=3600,
    )
    session.add(target)
    await session.flush()
    owner = str(uuid4())
    assert await acquire_fns_lease(
        session, target_id=target.id, owner=owner,
        now=NOW, lease_seconds=lease_seconds,
    )
    return target.id, owner


@pytest.mark.asyncio
async def test_failure_defers_retry(async_session):
    tid, owner = await claimed(async_session)
    at = NOW + timedelta(seconds=10)
    assert await fail_fns_lease(
        async_session, target_id=tid, owner=owner,
        now=at, retry_seconds=300,
    )
    async_session.expire_all()
    target = await async_session.get(FnsMonitoringTarget, tid)
    assert target.lease_owner is None
    assert target.lease_expires_at is None
    assert target.last_checked_at is None
    assert target.next_check_at.replace(
        tzinfo=timezone.utc
    ) == at + timedelta(seconds=300)


@pytest.mark.asyncio
async def test_wrong_owner_cannot_defer(async_session):
    tid, owner = await claimed(async_session)
    assert not await fail_fns_lease(
        async_session, target_id=tid,
        owner=str(uuid4()), now=NOW,
    )
    async_session.expire_all()
    target = await async_session.get(FnsMonitoringTarget, tid)
    assert target.lease_owner == owner
    assert target.next_check_at is None


@pytest.mark.asyncio
async def test_expired_lease_cannot_defer(async_session):
    tid, owner = await claimed(async_session, lease_seconds=5)
    assert not await fail_fns_lease(
        async_session, target_id=tid, owner=owner,
        now=NOW + timedelta(seconds=10),
    )
    async_session.expire_all()
    target = await async_session.get(FnsMonitoringTarget, tid)
    assert target.lease_owner == owner
    assert target.next_check_at is None


@pytest.mark.asyncio
async def test_invalid_retry_seconds(async_session):
    tid, owner = await claimed(async_session)
    with pytest.raises(ValueError, match="retry_seconds"):
        await fail_fns_lease(
            async_session, target_id=tid,
            owner=owner, now=NOW, retry_seconds=0,
        )
