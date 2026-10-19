"""Isolated execution primitives for FNS tax monitoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pathlib import Path
from typing import Callable

from connector.models import FnsMonitoringTarget
from connector.rates.fns_evidence_store import FnsEvidenceStore
from connector.rates.fns_refresh import run_fns_refresh
from connector.rates.fns_worker_service import build_fns_target_service


from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from connector.rates.fns_scheduler import (
    acquire_fns_lease,
    release_fns_lease,
    fail_fns_lease,
)


async def claim_fns_target(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    target_id: int,
    now: datetime,
    lease_seconds: int = 300,
) -> str | None:
    """Claim and commit one lease before any external I/O.

    A new UUID4 is generated on every acquisition attempt.
    The return value is an owner token, or None if not claimed.
    """
    owner = str(uuid4())

    async with session_factory() as session:
        claimed = await acquire_fns_lease(
            session,
            target_id=target_id,
            owner=owner,
            now=now,
            lease_seconds=lease_seconds,
        )
        if claimed:
            await session.commit()
            return owner

        await session.rollback()
        return None



async def release_claimed_fns_target(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    target_id: int,
    owner: str,
    now: datetime,
) -> bool:
    """Release only a currently owned active lease.

    Opens a fresh transaction after the failed refresh
    has rolled back its evidence transaction.
    """
    async with session_factory() as session:
        released = await release_fns_lease(
            session,
            target_id=target_id,
            owner=owner,
            now=now,
        )
        if released:
            await session.commit()
            return True

        await session.rollback()
        return False



async def defer_failed_fns_target(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    target_id: int,
    owner: str,
    now: datetime,
    retry_seconds: int = 300,
) -> bool:
    """Commit a bounded retry delay for an active owned lease."""
    async with session_factory() as session:
        deferred = await fail_fns_lease(
            session,
            target_id=target_id,
            owner=owner,
            now=now,
            retry_seconds=retry_seconds,
        )
        if deferred:
            await session.commit()
            return True

        await session.rollback()
        return False



async def execute_fns_target(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    target_id: int,
    evidence_store: FnsEvidenceStore,
    max_quote_age: timedelta,
    clock: Callable[[], datetime] | None = None,
    service_factory: Callable | None = None,
    lease_seconds: int = 300,
    retry_seconds: int = 300,
) -> bool:
    """Execute one due target under a committed persistent lease.

    Returns False when no lease can be obtained.
    Raises provider/validation errors after scheduling retry.
    """
    now = clock or (lambda: datetime.now(timezone.utc))

    owner = await claim_fns_target(
        session_factory,
        target_id=target_id,
        now=now(),
        lease_seconds=lease_seconds,
    )
    if owner is None:
        return False

    try:
        async with session_factory() as session:
            target = await session.get(
                FnsMonitoringTarget, target_id
            )
            if target is None:
                raise LookupError("FNS target disappeared")

            make_service = (
                service_factory
                if service_factory is not None
                else build_fns_target_service
            )
            service = make_service(
                target,
                evidence_store=evidence_store,
                max_quote_age=max_quote_age,
            )

            observed_at = now()
            await run_fns_refresh(
                session,
                service=service,
                target_id=target_id,
                base_asset=target.base_asset,
                owner=owner,
                as_of=observed_at,
                observed_at=observed_at,
                preflight_clock=now,
                clock=now,
            )
        return True

    except Exception:
        # The refresh transaction has already rolled back.
        # A separate owned-lease transaction schedules retry.
        try:
            await defer_failed_fns_target(
                session_factory,
                target_id=target_id,
                owner=owner,
                now=now(),
                retry_seconds=retry_seconds,
            )
        except Exception:
            # Never hide the original provider/validation failure.
            import logging
            logging.getLogger(__name__).exception(
                "Unable to defer failed FNS target %s",
                target_id,
            )
        raise
