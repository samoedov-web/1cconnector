from datetime import datetime, timedelta, timezone

import pytest

from connector.models import RegistryMonitoringTarget
from connector.services.registry_monitoring import get_due_registry_targets


NOW = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)


async def add_target(
    session,
    *,
    subject,
    enabled=True,
    next_check_at=None,
):
    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference=subject,
        registry_type="ois",
        source_id="mock",
        enabled=enabled,
        refresh_interval_seconds=3600,
        next_check_at=next_check_at,
    )
    session.add(target)
    await session.flush()
    return target


@pytest.mark.asyncio
async def test_never_checked_target_is_due(async_session):
    target = await add_target(
        async_session,
        subject="TARGET-1",
        next_check_at=None,
    )

    due = await get_due_registry_targets(
        async_session,
        now=NOW,
    )

    assert [item.id for item in due] == [target.id]


@pytest.mark.asyncio
async def test_past_target_is_due(async_session):
    target = await add_target(
        async_session,
        subject="TARGET-1",
        next_check_at=NOW - timedelta(seconds=1),
    )

    due = await get_due_registry_targets(
        async_session,
        now=NOW,
    )

    assert [item.id for item in due] == [target.id]


@pytest.mark.asyncio
async def test_exact_due_time_is_due(async_session):
    target = await add_target(
        async_session,
        subject="TARGET-1",
        next_check_at=NOW,
    )

    due = await get_due_registry_targets(
        async_session,
        now=NOW,
    )

    assert [item.id for item in due] == [target.id]


@pytest.mark.asyncio
async def test_future_target_is_not_due(async_session):
    await add_target(
        async_session,
        subject="TARGET-1",
        next_check_at=NOW + timedelta(seconds=1),
    )

    due = await get_due_registry_targets(
        async_session,
        now=NOW,
    )

    assert due == ()


@pytest.mark.asyncio
async def test_disabled_target_is_not_due(async_session):
    await add_target(
        async_session,
        subject="TARGET-1",
        enabled=False,
        next_check_at=None,
    )

    due = await get_due_registry_targets(
        async_session,
        now=NOW,
    )

    assert due == ()


@pytest.mark.asyncio
async def test_due_targets_are_deterministically_sorted(async_session):
    first = await add_target(
        async_session,
        subject="TARGET-1",
        next_check_at=None,
    )
    second = await add_target(
        async_session,
        subject="TARGET-2",
        next_check_at=NOW - timedelta(hours=1),
    )
    await add_target(
        async_session,
        subject="TARGET-3",
        next_check_at=NOW + timedelta(hours=1),
    )

    due = await get_due_registry_targets(
        async_session,
        now=NOW,
    )

    assert [item.id for item in due] == [
        first.id,
        second.id,
    ]
