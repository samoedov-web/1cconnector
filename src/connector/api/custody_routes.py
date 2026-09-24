"""API Routes для депозитарной сверки."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional
from connector.db import get_db
from connector.custody.service import CustodyReconciliationService, ReconConfig
from connector.models import ReconciliationRun
from decimal import Decimal

router = APIRouter(prefix="/api/v1/custody/reconciliation", tags=["custody"])

@router.get("/runs")
async def get_runs(source_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    service = CustodyReconciliationService(db)
    runs = await service.get_runs(source_id)
    return [{"id": r.id, "source_id": r.source_id, "period_from": r.period_from, "status": "completed"} for r in runs]

@router.post("/runs")
async def create_run(source_id: str, period_from: datetime, period_to: datetime, db: AsyncSession = Depends(get_db)):
    service = CustodyReconciliationService(db)
    config = ReconConfig(tolerance_abs=Decimal("0.01"), accept_aggregates=True)
    try:
        run = await service.run_reconciliation(source_id, period_from, period_to, config)
        return {"id": run.id, "status": "completed", "results_count": len(run.results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
