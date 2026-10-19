"""Single-target FNS execution tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from connector.models import Base, FnsMonitoringTarget
from connector.rates.fns_worker_runner import execute_fns_target

NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(
            engine, expire_on_commit=False
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_successful_execution(factory, tmp_path):
    async with factory() as session:
        target = FnsMonitoringTarget(
            base_asset="BTC",
            source_id="fns-krptrate",
            organizer="TEST",
            trading_pair="BTCRUB",
            enabled=True,
            refresh_interval_seconds=3600,
        )
        session.add(target)
        await session.commit()
        target_id = target.id

    service = SimpleNamespace(
        collect=AsyncMock(
            return_value=SimpleNamespace(evidence_id=123)
        )
    )

    from connector.rates.fns_evidence_store import FnsEvidenceStore

    result = await execute_fns_target(
        factory,
        target_id=target_id,
        evidence_store=FnsEvidenceStore(
            tmp_path / "evidence"
        ),
        max_quote_age=timedelta(hours=48),
        clock=lambda: NOW + timedelta(seconds=10),
        service_factory=lambda target, **kwargs: service,
    )

    assert result is True
    service.collect.assert_awaited_once()

    async with factory() as session:
        current = await session.get(
            FnsMonitoringTarget, target_id
        )
        assert current.lease_owner is None
        assert current.lease_expires_at is None
        assert current.last_checked_at is not None
        assert current.next_check_at is not None



@pytest.mark.asyncio
async def test_provider_failure_defers_retry(factory, tmp_path):
    from connector.rates.fns_evidence_store import FnsEvidenceStore

    async with factory() as session:
        target = FnsMonitoringTarget(
            base_asset="BTC",
            source_id="fns-krptrate",
            organizer="TEST",
            trading_pair="BTCRUB",
            enabled=True,
            refresh_interval_seconds=3600,
        )
        session.add(target)
        await session.commit()
        target_id = target.id

    service = SimpleNamespace(
        collect=AsyncMock(
            side_effect=LookupError("FNS unavailable")
        )
    )

    failed_at = NOW + timedelta(seconds=10)

    with pytest.raises(LookupError, match="FNS unavailable"):
        await execute_fns_target(
            factory,
            target_id=target_id,
            evidence_store=FnsEvidenceStore(
                tmp_path / "evidence"
            ),
            max_quote_age=timedelta(hours=48),
            clock=lambda: failed_at,
            service_factory=lambda target, **kwargs: service,
            retry_seconds=300,
        )

    service.collect.assert_awaited_once()

    async with factory() as session:
        current = await session.get(
            FnsMonitoringTarget, target_id
        )

        assert current.lease_owner is None
        assert current.lease_expires_at is None
        assert current.last_checked_at is None
        assert current.next_check_at is not None

        scheduled = current.next_check_at.replace(
            tzinfo=timezone.utc
        )
        assert scheduled == failed_at + timedelta(seconds=300)



@pytest.mark.asyncio
async def test_existing_lease_blocks_execution(factory, tmp_path):
    from connector.rates.fns_evidence_store import FnsEvidenceStore
    from connector.rates.fns_worker_runner import claim_fns_target

    async with factory() as session:
        target = FnsMonitoringTarget(
            base_asset="BTC",
            source_id="fns-krptrate",
            organizer="TEST",
            trading_pair="BTCRUB",
            enabled=True,
            refresh_interval_seconds=3600,
        )
        session.add(target)
        await session.commit()
        target_id = target.id

    first_owner = await claim_fns_target(
        factory,
        target_id=target_id,
        now=NOW,
        lease_seconds=300,
    )
    assert first_owner is not None

    service = SimpleNamespace(collect=AsyncMock())

    executed = await execute_fns_target(
        factory,
        target_id=target_id,
        evidence_store=FnsEvidenceStore(tmp_path / "evidence"),
        max_quote_age=timedelta(hours=48),
        clock=lambda: NOW + timedelta(seconds=10),
        service_factory=lambda target, **kwargs: service,
    )

    assert executed is False
    service.collect.assert_not_awaited()

    async with factory() as session:
        current = await session.get(FnsMonitoringTarget, target_id)
        assert current.lease_owner == first_owner
        assert current.next_check_at is None
