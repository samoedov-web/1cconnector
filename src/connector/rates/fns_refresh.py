"""FNS tax quotation refresh under a persistent scheduler lease.

Never creates RateSnapshot, accounting entries or 1C documents.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession
from connector.models import FnsMonitoringTarget

from connector.rates.fns_scheduler import (
    complete_fns_lease,
    validate_fns_target_config,
)
from connector.rates.fns_tax_service import (
    FnsTaxIntelligenceService,
    FnsTaxQuoteResult,
)


class FnsLeaseLostError(RuntimeError):
    """The refresh worker no longer owns an active lease."""


async def run_fns_refresh(
    session: AsyncSession,
    *,
    service: FnsTaxIntelligenceService,
    target_id: int,
    base_asset: str,
    owner: str,
    as_of: datetime,
    observed_at: datetime,
    clock: Callable[[], datetime] | None = None,
) -> FnsTaxQuoteResult:
    """Collect FNS evidence and complete lease atomically.

    Caller must acquire and commit the lease before calling.
    This function owns the final DB commit/rollback.
    """

    try:
        target = await session.get(
            FnsMonitoringTarget, target_id
        )

        if target is None:
            raise FnsLeaseLostError("FNS target not found")

        if (
            target.base_asset != base_asset.upper()
            or target.source_id != "fns-krptrate"
        ):
            raise ValueError("FNS target/source mismatch")

        validate_fns_target_config(target)

        result = await service.collect(
            session,
            base_asset=base_asset,
            as_of=as_of,
            observed_at=observed_at,
        )

        completed_at = (
            clock() if clock is not None
            else datetime.now(timezone.utc)
        )

        if (
            completed_at.tzinfo is None
            or completed_at.utcoffset() is None
        ):
            raise ValueError("clock must return timezone-aware datetime")

        completed = await complete_fns_lease(
            session,
            target_id=target_id,
            owner=owner,
            now=completed_at,
        )

        if not completed:
            raise FnsLeaseLostError(
                "FNS lease expired or ownership changed"
            )

        await session.commit()
        return result

    except BaseException:
        await session.rollback()
        raise
