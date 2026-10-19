"""Transactional FNS worker lease release tests."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from connector.models import Base, FnsMonitoringTarget
from connector.rates.fns_worker_runner import (
    claim_fns_target,
    release_claimed_fns_target,
    defer_failed_fns_target,
)


NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield async_sessionmaker(
            engine, expire_on_commit=False
        )
    finally:
        await engine.dispose()


async def make_target(factory):
    async with factory() as session:
        target = FnsMonitoringTarget(
            base_asset="BTC",
            source_id="fns-krptrate",
            enabled=True,
            refresh_interval_seconds=3600,
        )
        session.add(target)
        await session.commit()
        return target.id


@pytest.mark.asyncio
async def test_owner_releases_lease(factory):
    target_id = await make_target(factory)
    owner = await claim_fns_target(
        factory, target_id=target_id, now=NOW
    )
    assert owner is not None

    assert await release_claimed_fns_target(
        factory,
        target_id=target_id,
        owner=owner,
        now=NOW + timedelta(seconds=10),
    )

    async with factory() as session:
        target = await session.get(
            FnsMonitoringTarget, target_id
        )
        assert target.lease_owner is None
        assert target.lease_expires_at is None
        assert target.next_check_at is None


@pytest.mark.asyncio
async def test_wrong_owner_cannot_release(factory):
    target_id = await make_target(factory)
    owner = await claim_fns_target(
        factory, target_id=target_id, now=NOW
    )
    assert owner is not None

    assert not await release_claimed_fns_target(
        factory,
        target_id=target_id,
        owner=str(uuid4()),
        now=NOW + timedelta(seconds=10),
    )

    async with factory() as session:
        target = await session.get(
            FnsMonitoringTarget, target_id
        )
        assert target.lease_owner == owner


@pytest.mark.asyncio
async def test_expired_lease_not_released(factory):
    target_id = await make_target(factory)
    owner = await claim_fns_target(
        factory,
        target_id=target_id,
        now=NOW,
        lease_seconds=30,
    )
    assert owner is not None

    assert not await release_claimed_fns_target(
        factory,
        target_id=target_id,
        owner=owner,
        now=NOW + timedelta(seconds=31),
    )

    async with factory() as session:
        target = await session.get(
            FnsMonitoringTarget, target_id
        )
        assert target.lease_owner == owner



@pytest.mark.asyncio
async def test_failed_target_retry_is_committed(factory):
    target_id = await make_target(factory)
    owner = await claim_fns_target(
        factory, target_id=target_id, now=NOW
    )
    assert owner is not None

    failed_at = NOW + timedelta(seconds=10)
    assert await defer_failed_fns_target(
        factory,
        target_id=target_id,
        owner=owner,
        now=failed_at,
        retry_seconds=300,
    )

    async with factory() as session:
        target = await session.get(
            FnsMonitoringTarget, target_id
        )
        assert target.lease_owner is None
        assert target.lease_expires_at is None
        assert target.last_checked_at is None
        assert target.next_check_at.replace(
            tzinfo=timezone.utc
        ) == failed_at + timedelta(seconds=300)


@pytest.mark.asyncio
async def test_wrong_owner_cannot_defer_failed_target(factory):
    target_id = await make_target(factory)
    owner = await claim_fns_target(
        factory, target_id=target_id, now=NOW
    )
    assert owner is not None

    assert not await defer_failed_fns_target(
        factory,
        target_id=target_id,
        owner=str(uuid4()),
        now=NOW + timedelta(seconds=10),
    )

    async with factory() as session:
        target = await session.get(
            FnsMonitoringTarget, target_id
        )
        assert target.lease_owner == owner
        assert target.next_check_at is None
