"""API вкладки «Сверка» (фаза 5 depository-спеки).

Поток оператора: загрузить выписки от адаптера → запустить сверку →
посмотреть результаты → разобрать расхождения вручную (переиспользуется
паттерн очереди ручного разбора: оператор связывает, решение в аудит-логе).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Импорт регистрирует мок-адаптер в реестре custody-источников.
import connector.custody.mock_adapter  # noqa: F401
from connector.config import settings
from connector.custody.onec import queue_reconciliation_info
from connector.custody.reconciliation import ReconConfig
from connector.custody.report import DISCREPANCY_STATUSES, STATUS_RU
from connector.custody.service import run_reconciliation
from connector.custody.store import store_statement
from connector.db import get_session
from connector.models import AuditLog, ReconciliationResult, ReconciliationRun
from connector.security import CurrentUser, require_operator, require_reader
from connector.sources.registry import create_custody_source, custody_source_codes

router = APIRouter(prefix="/api/v1/reconciliation", tags=["reconciliation"])


def _adapter():
    return create_custody_source(
        settings.custody_source_id, fixtures_dir=settings.custody_fixtures_dir
    )


@router.get("/sources")
async def sources(user: CurrentUser = Depends(require_reader)) -> dict:
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


class PeriodIn(BaseModel):
    period_from: datetime
    period_to: datetime


@router.post("/fetch")
async def fetch_statements(
    data: PeriodIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_operator),
) -> dict:
    """Загрузить выписки адаптера за период в журнал неизменяемости."""
    adapter = _adapter()
    try:
        statements = await adapter.fetch_statements(data.period_from, data.period_to)
    except ConnectionError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Депозитарий недоступен: {exc}. Повторите загрузку позже.",
        ) from None
    stored = 0
    for statement in statements:
        entries = await adapter.fetch_entries(statement.statement_id)
        await store_statement(session, statement, entries)
        stored += 1
    session.add(
        AuditLog(
            actor=user.username,
            action="custody_statements_fetched",
            entity="custody_statement",
            entity_id=settings.custody_source_id,
            details={"statements": stored},
        )
    )
    await session.commit()
    return {"statements": stored}


class RunIn(PeriodIn):
    tolerance_abs: Decimal = Decimal(0)
    tolerance_rel: Decimal = Decimal(0)
    date_window_hours: int = Field(default=24, ge=1)
    accept_aggregates: bool = True


def _run_row(run: ReconciliationRun) -> dict:
    counts: dict[str, int] = {}
    for result in run.results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return {
        "id": run.id,
        "source_id": run.source_id,
        "period_from": run.period_from.isoformat(),
        "period_to": run.period_to.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "stale": run.stale,
        "counts": counts,
        "discrepancies": sum(
            n for status, n in counts.items() if status in DISCREPANCY_STATUSES
        ),
    }


@router.post("/runs")
async def create_run(
    data: RunIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_operator),
) -> dict:
    config = ReconConfig(
        tolerance_abs=data.tolerance_abs,
        tolerance_rel=data.tolerance_rel,
        date_window=timedelta(hours=data.date_window_hours),
        accept_aggregates=data.accept_aggregates,
    )
    run = await run_reconciliation(
        session, settings.custody_source_id, data.period_from, data.period_to, config
    )
    # Режим shadow: информационная запись в 1С (регистр сведений).
    await queue_reconciliation_info(session, run)
    session.add(
        AuditLog(
            actor=user.username,
            action="reconciliation_run",
            entity="reconciliation_run",
            entity_id=str(run.id),
            details={"config": run.config},
        )
    )
    await session.commit()
    return _run_row(run)


@router.get("/runs")
async def list_runs(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> list[dict]:
    runs = (
        (
            await session.execute(
                select(ReconciliationRun)
                .options(selectinload(ReconciliationRun.results))
                .order_by(ReconciliationRun.id.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [_run_row(run) for run in runs]


@router.get("/runs/{run_id}")
async def run_detail(
    run_id: int,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> dict:
    from connector.custody.report import reconciliation_report_data

    data = await reconciliation_report_data(session, run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Запуск сверки не найден")
    return data


class ResolveIn(BaseModel):
    note: str = ""
    ledger_tx_ids: list[int] = []


@router.post("/results/{result_id}/resolve")
async def resolve_result(
    result_id: int,
    data: ResolveIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_operator),
) -> dict:
    """Ручной разбор расхождения: оператор связывает/принимает строку."""
    result = await session.get(ReconciliationResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Строка сверки не найдена")
    if result.status not in DISCREPANCY_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Строка в статусе «{STATUS_RU.get(result.status, result.status)}» "
            "не требует разбора",
        )
    previous_status = result.status
    result.status = "manual"
    result.rule = "manual"
    if data.ledger_tx_ids:
        result.ledger_tx_ids = sorted(set(result.ledger_tx_ids) | set(data.ledger_tx_ids))
    result.detail = result.detail | {
        "resolved_by": user.username,
        "previous_status": previous_status,
        "note": data.note,
    }
    session.add(
        AuditLog(
            actor=user.username,
            action="reconciliation_resolved",
            entity="reconciliation_result",
            entity_id=str(result_id),
            details={"previous_status": previous_status,
                     "ledger_tx_ids": data.ledger_tx_ids, "note": data.note},
        )
    )
    await session.commit()
    return {"status": "manual"}
