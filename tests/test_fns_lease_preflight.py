"""FNS lease preflight tests."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from connector.models import FnsMonitoringTarget
from connector.rates.fns_scheduler import (
    acquire_fns_lease,
    verify_fns_lease,
)


NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_owned_active_lease_is_valid(async_session):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
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

    assert await verify_fns_lease(
        async_session,
        target_id=target.id,
        owner=owner,
        now=NOW + timedelta(seconds=10),
    )


@pytest.mark.asyncio
async def test_wrong_owner_is_rejected(async_session):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
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

    assert not await verify_fns_lease(
        async_session,
        target_id=target.id,
        owner=str(uuid4()),
        now=NOW + timedelta(seconds=10),
    )


@pytest.mark.asyncio
async def test_expired_lease_is_rejected(async_session):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
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

    assert not await verify_fns_lease(
        async_session,
        target_id=target.id,
        owner=owner,
        now=NOW + timedelta(seconds=31),
    )
