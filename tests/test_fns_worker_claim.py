"""Transactional FNS worker claim tests."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from connector.models import Base

from connector.models import FnsMonitoringTarget
from connector.rates.fns_worker_runner import claim_fns_target


NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


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
async def test_successful_claim_is_committed(async_engine):
    factory = async_sessionmaker(
        async_engine, expire_on_commit=False
    )
    target_id = await make_target(factory)

    owner = await claim_fns_target(
        factory, target_id=target_id, now=NOW
    )

    assert owner is not None
    assert UUID(owner).version == 4

    async with factory() as session:
        target = await session.get(
            FnsMonitoringTarget, target_id
        )
        assert target.lease_owner == owner
        assert target.lease_expires_at is not None


@pytest.mark.asyncio
async def test_second_claim_is_rejected(async_engine):
    factory = async_sessionmaker(
        async_engine, expire_on_commit=False
    )
    target_id = await make_target(factory)

    first = await claim_fns_target(
        factory, target_id=target_id, now=NOW
    )
    second = await claim_fns_target(
        factory,
        target_id=target_id,
        now=NOW + timedelta(seconds=1),
    )

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_expired_claim_gets_new_owner(async_engine):
    factory = async_sessionmaker(
        async_engine, expire_on_commit=False
    )
    target_id = await make_target(factory)

    first = await claim_fns_target(
        factory,
        target_id=target_id,
        now=NOW,
        lease_seconds=30,
    )
    second = await claim_fns_target(
        factory,
        target_id=target_id,
        now=NOW + timedelta(seconds=31),
        lease_seconds=30,
    )

    assert first is not None
    assert second is not None
    assert first != second

    async with factory() as session:
        target = await session.get(
            FnsMonitoringTarget, target_id
        )
        assert target.lease_owner == second



@pytest_asyncio.fixture
async def async_engine():
    """Isolated SQLite engine for independent session tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield engine
    finally:
        await engine.dispose()
