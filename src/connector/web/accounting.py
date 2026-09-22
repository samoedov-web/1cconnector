"""API учётных операций панели: переоценка на отчётную дату (v1)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.accounting.revaluation import run_revaluation
from connector.db import get_session
from connector.models import AuditLog, Revaluation
from connector.security import CurrentUser, require_operator, require_reader
from connector.worker import build_rate_service

router = APIRouter(prefix="/api/v1/accounting", tags=["accounting"])


class RevaluationIn(BaseModel):
    as_of: datetime


class RevaluationOut(BaseModel):
    id: int
    asset: str
    as_of: datetime
    quantity: str
    book_cost_rub: str
    market_rub: str
    difference_rub: str
    delta_rub: str


def _out(r: Revaluation) -> RevaluationOut:
    return RevaluationOut(
        id=r.id,
        asset=r.asset.symbol,
        as_of=r.as_of,
        quantity=str(r.quantity),
        book_cost_rub=str(r.book_cost_rub),
        market_rub=str(r.market_rub),
        difference_rub=str(r.difference_rub),
        delta_rub=str(r.delta_rub),
    )


@router.post("/revaluations", response_model=list[RevaluationOut])
async def create_revaluation(
    data: RevaluationIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_operator),
) -> list[RevaluationOut]:
    """Переоценка всех активов на дату; повторный запуск дублей не создаёт."""
    created = await run_revaluation(session, build_rate_service(), data.as_of)
    for r in created:
        await session.refresh(r, ["asset"])
    session.add(
        AuditLog(
            actor=user.username,
            action="revaluation_run",
            entity="revaluation",
            entity_id=data.as_of.date().isoformat(),
            details={"created": len(created)},
        )
    )
    await session.commit()
    return [_out(r) for r in created]


@router.get("/revaluations", response_model=list[RevaluationOut])
async def list_revaluations(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> list[RevaluationOut]:
    rows = (
        (
            await session.execute(
                select(Revaluation)
                .options(selectinload(Revaluation.asset))
                .order_by(Revaluation.as_of.desc(), Revaluation.id.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [_out(r) for r in rows]
