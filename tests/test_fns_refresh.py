"""FNS refresh transaction boundary tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from connector.models import (
    FnsMonitoringTarget,
    MarketQuoteEvidence,
    RateSnapshot,
)
from connector.rates.fns_refresh import (
    FnsLeaseLostError,
    run_fns_refresh,
)
from connector.rates.fns_scheduler import acquire_fns_lease


NOW = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)


async def make_target(session, *, lease_seconds=300):
    target = FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
        organizer="Binance",
        trading_pair="BTCUSDT",
        enabled=True,
        refresh_interval_seconds=3600,
    )
    session.add(target)
    await session.flush()

    owner = str(uuid4())
    assert await acquire_fns_lease(
        session,
        target_id=target.id,
        owner=owner,
        now=NOW,
        lease_seconds=lease_seconds,
    )
    return target, owner


def fake_service():
    service = SimpleNamespace()
    service.collect = AsyncMock(
        return_value=SimpleNamespace(evidence_id=123)
    )
    return service


@pytest.mark.asyncio
async def test_successful_refresh_commits(async_session):
    target, owner = await make_target(async_session)
    service = fake_service()

    result = await run_fns_refresh(
        async_session,
        service=service,
        target_id=target.id,
        base_asset="BTC",
        owner=owner,
        as_of=NOW,
        observed_at=NOW + timedelta(seconds=10),
        clock=lambda: NOW + timedelta(seconds=10),
    )

    assert result.evidence_id == 123

    await async_session.refresh(target)
    assert target.lease_owner is None
    assert target.next_check_at is not None


@pytest.mark.asyncio
async def test_lost_lease_rolls_back(async_session):
    target, owner = await make_target(
        async_session, lease_seconds=5
    )
    service = fake_service()

    with pytest.raises(FnsLeaseLostError):
        await run_fns_refresh(
            async_session,
            service=service,
            target_id=target.id,
            base_asset="BTC",
            owner=owner,
            as_of=NOW,
            observed_at=NOW + timedelta(seconds=10),
            clock=lambda: NOW + timedelta(seconds=10),
        )


@pytest.mark.asyncio
async def test_provider_error_propagates(async_session):
    target, owner = await make_target(async_session)
    service = SimpleNamespace(
        collect=AsyncMock(
            side_effect=LookupError("FNS unavailable")
        )
    )

    with pytest.raises(LookupError, match="FNS unavailable"):
        await run_fns_refresh(
            async_session,
            service=service,
            target_id=target.id,
            base_asset="BTC",
            owner=owner,
            as_of=NOW,
            observed_at=NOW + timedelta(seconds=10),
            clock=lambda: NOW + timedelta(seconds=10),
        )


@pytest.mark.asyncio
async def test_refresh_failure_does_not_create_rate_snapshot(
    async_session,
):
    target, owner = await make_target(
        async_session, lease_seconds=5
    )
    service = fake_service()

    with pytest.raises(FnsLeaseLostError):
        await run_fns_refresh(
            async_session,
            service=service,
            target_id=target.id,
            base_asset="BTC",
            owner=owner,
            as_of=NOW,
            observed_at=NOW + timedelta(seconds=10),
            clock=lambda: NOW + timedelta(seconds=10),
        )

    count = await async_session.scalar(
        select(func.count()).select_from(RateSnapshot)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_refresh_calls_service_once(async_session):
    target, owner = await make_target(async_session)
    service = fake_service()

    await run_fns_refresh(
        async_session,
        service=service,
        target_id=target.id,
        base_asset="BTC",
        owner=owner,
        as_of=NOW,
        observed_at=NOW + timedelta(seconds=10),
        clock=lambda: NOW + timedelta(seconds=10),
    )

    service.collect.assert_awaited_once()



@pytest.mark.asyncio
async def test_delayed_provider_loses_lease(async_session):
    target, owner = await make_target(
        async_session,
        lease_seconds=30,
    )
    service = fake_service()

    # Quote observation occurred while lease was valid,
    # but provider returned after the lease expired.
    with pytest.raises(FnsLeaseLostError):
        await run_fns_refresh(
            async_session,
            service=service,
            target_id=target.id,
            base_asset="BTC",
            owner=owner,
            as_of=NOW,
            observed_at=NOW + timedelta(seconds=10),
            clock=lambda: NOW + timedelta(seconds=31),
        )

    service.collect.assert_awaited_once()


@pytest.mark.asyncio
async def test_naive_completion_clock_is_rejected(async_session):
    target, owner = await make_target(async_session)
    service = fake_service()

    with pytest.raises(
        ValueError,
        match="clock must return timezone-aware",
    ):
        await run_fns_refresh(
            async_session,
            service=service,
            target_id=target.id,
            base_asset="BTC",
            owner=owner,
            as_of=NOW,
            observed_at=NOW + timedelta(seconds=10),
            clock=lambda: datetime(2026, 10, 13, 10, 0),
        )
