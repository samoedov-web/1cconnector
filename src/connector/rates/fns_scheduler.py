"""Persistent FNS scheduler lease management.

Atomic acquisition is implemented with a conditional SQL UPDATE.
No blockchain, accounting or 1C operations are performed here.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy import update, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from connector.models import FnsMonitoringTarget


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _validate_lease_owner(owner: str) -> None:
    """Require a unique UUID token for each lease acquisition."""
    from uuid import UUID

    if not isinstance(owner, str):
        raise ValueError("Invalid lease owner")

    try:
        token = UUID(owner)
    except (ValueError, AttributeError):
        raise ValueError("Lease owner must be a UUID") from None

    if token.version != 4 or str(token) != owner:
        raise ValueError("Lease owner must be a canonical UUID4")


async def acquire_fns_lease(
    session: AsyncSession,
    *,
    target_id: int,
    owner: str,
    now: datetime,
    lease_seconds: int = 300,
) -> bool:
    """Atomically claim an enabled and due FNS target.

    The caller owns the database transaction and must commit
    the lease before starting external I/O.
    """
    _aware(now, "now")

    _validate_lease_owner(owner)

    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")

    table = FnsMonitoringTarget

    statement = (
        update(table)
        .where(table.id == target_id)
        .where(table.enabled.is_(True))
        .where(
            (table.next_check_at.is_(None))
            | (table.next_check_at <= now)
        )
        .where(
            (table.lease_expires_at.is_(None))
            | (table.lease_expires_at <= now)
        )
        .values(
            lease_owner=owner,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
        .returning(table.id)
    )

    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None



async def complete_fns_lease(
    session: AsyncSession,
    *,
    target_id: int,
    owner: str,
    now: datetime,
) -> bool:
    """Complete a successful refresh while still owning its lease.

    Caller must commit evidence and scheduler state atomically.
    """
    _aware(now, "now")

    _validate_lease_owner(owner)

    table = FnsMonitoringTarget

    # Lock the target row before calculating the next schedule.
    # PostgreSQL FOR UPDATE serializes competing completions.
    target = await session.get(
        FnsMonitoringTarget,
        target_id,
        with_for_update=True,
        populate_existing=True,
    )

    if target is None:
        return False

    if target.refresh_interval_seconds <= 0:
        raise ValueError("Invalid refresh interval")

    next_check = now + timedelta(
        seconds=target.refresh_interval_seconds
    )

    statement = (
        update(table)
        .where(table.id == target_id)
        .where(table.lease_owner == owner)
        .where(table.lease_expires_at > now)
        .where(table.enabled.is_(True))
        .values(
            last_checked_at=now,
            next_check_at=next_check,
            lease_owner=None,
            lease_expires_at=None,
        )
        .returning(table.id)
        .execution_options(synchronize_session=False)
    )

    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None


async def release_fns_lease(
    session: AsyncSession,
    *,
    target_id: int,
    owner: str,
    now: datetime,
) -> bool:
    """Release an active owned lease without advancing schedule."""
    _aware(now, "now")

    _validate_lease_owner(owner)

    table = FnsMonitoringTarget

    statement = (
        update(table)
        .where(table.id == target_id)
        .where(table.lease_owner == owner)
        .where(table.lease_expires_at > now)
        .values(
            lease_owner=None,
            lease_expires_at=None,
        )
        .returning(table.id)
    )

    result = await session.execute(statement)
    return result.scalar_one_or_none() is not None



async def get_due_fns_targets(
    session: AsyncSession,
    *,
    now: datetime,
) -> tuple[FnsMonitoringTarget, ...]:
    """Return enabled, due, unlocked FNS targets.

    Read-only discovery. Actual acquisition requires
    acquire_fns_lease() in a separate committed transaction.
    """
    _aware(now, "now")

    table = FnsMonitoringTarget

    statement = (
        select(table)
        .where(table.enabled.is_(True))
        .where(
            or_(
                table.next_check_at.is_(None),
                table.next_check_at <= now,
            )
        )
        .where(
            or_(
                table.lease_expires_at.is_(None),
                table.lease_expires_at <= now,
            )
        )
        .order_by(table.id)
    )

    result = await session.scalars(statement)
    return tuple(result.all())



def validate_fns_target_config(
    target: FnsMonitoringTarget,
) -> None:
    """Fail closed for incomplete FNS quotation selectors.

    Selector values must originate from explicit configuration.
    Never guess an organizer or trading pair.
    """
    if target.source_id != "fns-krptrate":
        raise ValueError("Unsupported FNS source")

    if not target.base_asset or not target.base_asset.strip():
        raise ValueError("FNS base asset is required")

    if not target.organizer or not target.organizer.strip():
        raise ValueError("FNS organizer is required")

    if not target.trading_pair or not target.trading_pair.strip():
        raise ValueError("FNS trading pair is required")

    if target.base_asset != target.base_asset.upper():
        raise ValueError("FNS base asset must be uppercase")

    if target.trading_pair != target.trading_pair.upper():
        raise ValueError("FNS trading pair must be uppercase")
