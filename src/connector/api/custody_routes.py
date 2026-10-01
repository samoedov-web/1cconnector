"""API депозитарной сверки.

Canonical API объединяет:
- режимы off / shadow / active;
- источник и health/capabilities;
- загрузку выписок;
- запуск и просмотр сверки;
- ручной разбор расхождений;
- аудит и RBAC.

Интеграция shadow-регистра 1С подключается отдельным слоем.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Регистрирует mock-depo в custody registry.
import connector.custody.mock_adapter  # noqa: F401

from connector.config import settings
from connector.custody.engine import ReconConfig
from connector.custody.onec import queue_reconciliation_info
from connector.custody.report import (
    DISCREPANCY_STATUSES,
    STATUS_RU,
    reconciliation_report_data,
)
from connector.custody.service import CustodyReconciliationService
from connector.custody.store import store_statement
from connector.db import get_session
from connector.models import (
    AuditLog,
    ReconciliationResult,
    ReconciliationRun,
)
from connector.security import (
    CurrentUser,
    require_operator,
    require_reader,
)
from connector.sources.registry import (
    create_custody_source,
    custody_source_codes,
)


router = APIRouter(
    prefix="/api/v1/custody/reconciliation",
    tags=["custody"],
)


def _adapter():
    return create_custody_source(
        settings.custody_source_id,
        fixtures_dir=settings.custody_fixtures_dir,
    )


class PeriodRequest(BaseModel):
    period_from: datetime
    period_to: datetime


class ReconciliationRunRequest(PeriodRequest):
    # Сохраняем совместимость с текущим UI:
    # если source_id не передан — используется активный source из Settings.
    source_id: Optional[str] = None

    tolerance_abs: Decimal = Decimal("0.01")
    tolerance_rel: Decimal = Decimal(0)
    date_window_hours: int = Field(default=24, ge=1)
    accept_aggregates: bool = True


class ResolveRequest(BaseModel):
    note: str = ""
    ledger_tx_ids: list[int] = Field(default_factory=list)


def _run_row(run: ReconciliationRun) -> dict:
    counts: dict[str, int] = {}

    for result in run.results:
        counts[result.status] = counts.get(result.status, 0) + 1

    return {
        "id": run.id,
        "source_id": run.source_id,
        "period_from": run.period_from,
        "period_to": run.period_to,
        "started_at": run.started_at,
        "stale": run.stale,
        "status": "stale" if run.stale else "completed",
        "counts": counts,
        "discrepancies": sum(
            count
            for status, count in counts.items()
            if status in DISCREPANCY_STATUSES
        ),
    }


@router.get("/sources")
async def sources(
    user: CurrentUser = Depends(require_reader),
) -> dict:
    adapter = _adapter()
    caps = adapter.capabilities()

    return {
        "registered": custody_source_codes(),
        "active": settings.custody_source_id,
        "capabilities": {
            "has_tx_hash": caps.has_tx_hash,
            "has_counterparty": caps.has_counterparty,
            "has_network": caps.has_network,
            "entry_granularity": caps.entry_granularity.value,
        },
        "health": (await adapter.health()).__dict__,
    }


@router.post("/fetch")
async def fetch_statements(
    payload: PeriodRequest,
    db: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_operator),
) -> dict:
    if payload.period_from > payload.period_to:
        raise HTTPException(
            status_code=422,
            detail="period_from must not be after period_to",
        )

    adapter = _adapter()

    try:
        statements = await adapter.fetch_statements(
            payload.period_from,
            payload.period_to,
        )
    except ConnectionError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Депозитарий недоступен: {exc}. "
                "Повторите загрузку позже."
            ),
        ) from None

    stored = 0

    for statement in statements:
        entries = await adapter.fetch_entries(statement.statement_id)

        await store_statement(
            db,
            statement,
            entries,
        )

        stored += 1

    db.add(
        AuditLog(
            actor=user.username,
            action="custody_statements_fetched",
            entity="custody_statement",
            entity_id=settings.custody_source_id,
            details={"statements": stored},
        )
    )

    await db.commit()

    return {"statements": stored}


@router.get("/runs")
async def get_runs(
    source_id: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
):
    stmt = (
        select(ReconciliationRun)
        .options(selectinload(ReconciliationRun.results))
        .order_by(ReconciliationRun.id.desc())
        .limit(50)
    )

    if source_id is not None:
        stmt = stmt.where(
            ReconciliationRun.source_id == source_id
        )

    result = await db.execute(stmt)
    runs = result.scalars().all()

    return [_run_row(run) for run in runs]


@router.post("/runs")
async def create_run(
    payload: ReconciliationRunRequest,
    mode: str = Query(default="shadow"),
    db: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_operator),
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

    source_id = payload.source_id or settings.custody_source_id

    config = ReconConfig(
        tolerance_abs=payload.tolerance_abs,
        tolerance_rel=payload.tolerance_rel,
        date_window=timedelta(
            hours=payload.date_window_hours,
        ),
        accept_aggregates=payload.accept_aggregates,
    )

    service = CustodyReconciliationService(db)

    try:
        run = await service.run_reconciliation(
            source_id,
            payload.period_from,
            payload.period_to,
            config,
        )

        if mode == "shadow":
            await queue_reconciliation_info(db, run)

        db.add(
            AuditLog(
                actor=user.username,
                action="reconciliation_run",
                entity="reconciliation_run",
                entity_id=str(run.id),
                details={
                    "mode": mode,
                    "config": run.config,
                },
            )
        )

        await db.commit()

        result = await db.execute(
            select(ReconciliationRun)
            .options(
                selectinload(ReconciliationRun.results)
            )
            .where(ReconciliationRun.id == run.id)
        )

        loaded_run = result.scalar_one()

        row = _run_row(loaded_run)
        row["mode"] = mode

        return row

    except HTTPException:
        raise

    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@router.get("/runs/{run_id}")
async def run_detail(
    run_id: int,
    db: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> dict:
    data = await reconciliation_report_data(
        db,
        run_id,
    )

    if data is None:
        raise HTTPException(
            status_code=404,
            detail="Запуск сверки не найден",
        )

    return data


@router.get("/runs/{run_id}/results")
async def run_results(
    run_id: int,
    db: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> list[dict]:
    run = await db.scalar(
        select(ReconciliationRun)
        .options(
            selectinload(ReconciliationRun.results)
        )
        .where(ReconciliationRun.id == run_id)
    )

    if run is None:
        raise HTTPException(
            status_code=404,
            detail="Запуск сверки не найден",
        )

    return [
        {
            "id": result.id,
            "status": result.status,
            "status_ru": STATUS_RU.get(
                result.status,
                result.status,
            ),
            "rule": result.rule,
            "ledger_tx_ids": result.ledger_tx_ids,
            "custody_entry_ids": result.custody_entry_ids,
            "detail": result.detail,
        }
        for result in run.results
    ]


@router.post("/results/{result_id}/resolve")
async def resolve_result(
    result_id: int,
    payload: ResolveRequest,
    db: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_operator),
) -> dict:
    result = await db.get(
        ReconciliationResult,
        result_id,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Строка сверки не найдена",
        )

    if result.status not in DISCREPANCY_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Строка в статусе "
                f"«{STATUS_RU.get(result.status, result.status)}» "
                "не требует разбора"
            ),
        )

    previous_status = result.status

    result.status = "manual"
    result.rule = "manual"

    if payload.ledger_tx_ids:
        result.ledger_tx_ids = sorted(
            set(result.ledger_tx_ids)
            | set(payload.ledger_tx_ids)
        )

    result.detail = result.detail | {
        "resolved_by": user.username,
        "previous_status": previous_status,
        "note": payload.note,
    }

    db.add(
        AuditLog(
            actor=user.username,
            action="reconciliation_resolved",
            entity="reconciliation_result",
            entity_id=str(result_id),
            details={
                "previous_status": previous_status,
                "ledger_tx_ids": payload.ledger_tx_ids,
                "note": payload.note,
            },
        )
    )

    await db.commit()

    return {"status": "manual"}
