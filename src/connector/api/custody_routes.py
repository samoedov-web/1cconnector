"""API routes для депозитарной сверки."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from connector.custody.engine import ReconConfig
from connector.custody.service import CustodyReconciliationService
from connector.db import get_session
from connector.models import ReconciliationRun


router = APIRouter(
    prefix="/api/v1/custody/reconciliation",
    tags=["custody"],
)


class ReconciliationRunRequest(BaseModel):
    source_id: str
    period_from: datetime
    period_to: datetime


@router.get("/runs")
async def get_runs(
    source_id: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
):
    service = CustodyReconciliationService(db)
    runs = await service.get_runs(source_id)

    return [
        {
            "id": run.id,
            "source_id": run.source_id,
            "period_from": run.period_from,
            "period_to": run.period_to,
            "started_at": run.started_at,
            "stale": run.stale,
            "status": "stale" if run.stale else "completed",
        }
        for run in runs
    ]


@router.post("/runs")
async def create_run(
    payload: ReconciliationRunRequest,
    mode: str = Query(default="shadow"),
    db: AsyncSession = Depends(get_session),
):
    if mode not in {"off", "shadow", "active"}:
        raise HTTPException(
            status_code=422,
            detail="mode must be one of: off, shadow, active",
        )

    if mode == "off":
        raise HTTPException(
            status_code=409,
            detail="Reconciliation is disabled in off mode",
        )

    if payload.period_from > payload.period_to:
        raise HTTPException(
            status_code=422,
            detail="period_from must not be after period_to",
        )

    service = CustodyReconciliationService(db)
    config = ReconConfig(
        tolerance_abs=Decimal("0.01"),
        accept_aggregates=True,
    )

    try:
        run = await service.run_reconciliation(
            payload.source_id,
            payload.period_from,
            payload.period_to,
            config,
        )

        # Service flushes the run/results; commit is the API transaction boundary.
        await db.commit()

        result = await db.execute(
            select(ReconciliationRun)
            .options(selectinload(ReconciliationRun.results))
            .where(ReconciliationRun.id == run.id)
        )
        loaded_run = result.scalar_one()

        return {
            "id": loaded_run.id,
            "status": "completed",
            "stale": loaded_run.stale,
            "results_count": len(loaded_run.results),
            "mode": mode,
        }

    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
