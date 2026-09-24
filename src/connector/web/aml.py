"""API AML-контроля (фаза 3 aml-спеки; шаги 4–5 регламента).

Матрица доступа:
- просмотр списков — все аутентифицированные роли (казначей видит
  «одобрено к отправке», комплаенс — очередь review);
- решение по aml_review — только комплаенс-офицер или админ; полная
  запись решения — в аудит-логе, краткая — на самом платеже;
- отметка «отправил» — казначей или админ (решение владельца по
  сценарию 4): фиксирует момент отправки для таймера обнаружения.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.aml.flow import InvalidTransition, transition
from connector.db import get_session
from connector.models import (
    AuditLog,
    Contract,
    Counterparty,
    ExpectedPayment,
    ExpectedPaymentStatus as EPS,
    Invoice,
)
from connector.security import (
    CurrentUser,
    require_compliance,
    require_reader,
    require_treasurer,
)

router = APIRouter(prefix="/api/v1/aml", tags=["aml"])


async def _payment_row(session: AsyncSession, payment: ExpectedPayment) -> dict:
    invoice = await session.get(
        Invoice, payment.invoice_id, options=[selectinload(Invoice.contract)]
    )
    contract: Contract | None = invoice.contract if invoice else None
    counterparty = (
        await session.get(Counterparty, contract.counterparty_id) if contract else None
    )
    screening = payment.aml_screening
    return {
        "id": payment.id,
        "status": payment.status.value,
        "invoice_number": invoice.number if invoice else "",
        "contract_number": contract.number if contract else "",
        "counterparty": counterparty.name if counterparty else "",
        "amount": str(payment.amount),
        "currency": payment.currency,
        "to_address": payment.to_address,
        "network": payment.network,
        "risk_score": screening.risk_score if screening else None,
        "categories": screening.categories if screening else [],
        "screened_at": (
            screening.screened_at.isoformat() if screening else None
        ),
        "decided_by": payment.decided_by,
        "decision_note": payment.decision_note,
        "status_changed_at": payment.status_changed_at.isoformat()
        if payment.status_changed_at else None,
        "sent_marked_at": payment.sent_marked_at.isoformat()
        if payment.sent_marked_at else None,
        "sent_marked_by": payment.sent_marked_by,
    }


@router.get("/payments")
async def list_payments(
    status: EPS | None = None,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> list[dict]:
    stmt = (
        select(ExpectedPayment)
        .options(selectinload(ExpectedPayment.aml_screening))
        .order_by(ExpectedPayment.id.desc())
        .limit(200)
    )
    if status is not None:
        stmt = stmt.where(ExpectedPayment.status == status)
    payments = (await session.execute(stmt)).scalars().all()
    return [await _payment_row(session, p) for p in payments]


class DecisionAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class DecisionIn(BaseModel):
    action: DecisionAction
    note: str = ""


@router.post("/payments/{payment_id}/decide")
async def decide(
    payment_id: int,
    data: DecisionIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_compliance),
) -> dict:
    """Решение комплаенс-офицера по среднему риску (шаг 4 регламента)."""
    payment = await session.get(ExpectedPayment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Ожидаемый платёж не найден")
    new_status = (
        EPS.AML_APPROVED if data.action == DecisionAction.APPROVE else EPS.AML_REJECTED
    )
    try:
        transition(payment, new_status)
    except InvalidTransition:
        raise HTTPException(
            status_code=400,
            detail=f"Платёж в статусе «{payment.status.value}» не ожидает решения "
            "комплаенса — решению подлежит только aml_review",
        ) from None
    payment.decided_by = user.username
    payment.decision_note = data.note
    session.add(
        AuditLog(
            actor=user.username,
            action="aml_decision",
            entity="expected_payment",
            entity_id=str(payment.id),
            details={
                "action": data.action.value,
                "new_status": new_status.value,
                "note": data.note,
                "to_address": payment.to_address,
            },
        )
    )
    await session.commit()
    return {"status": new_status.value}


@router.post("/payments/{payment_id}/mark-sent")
async def mark_sent(
    payment_id: int,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_treasurer),
) -> dict:
    """Отметка казначея «отправил» (решение владельца по сценарию 4).

    Статус не меняется — его меняет только индексер, обнаружив
    транзакцию; отметка задаёт точку отсчёта таймера «не обнаружена
    за N минут» (алерт казначею — проверить вручную).
    """
    payment = await session.get(ExpectedPayment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Ожидаемый платёж не найден")
    if payment.status != EPS.AML_APPROVED:
        raise HTTPException(
            status_code=400,
            detail=f"Платёж в статусе «{payment.status.value}» — отметить "
            "отправку можно только по одобренному платежу",
        )
    if payment.sent_marked_at is not None:
        raise HTTPException(
            status_code=400,
            detail="Отправка уже отмечена "
            f"({payment.sent_marked_by}, {payment.sent_marked_at.isoformat()})",
        )
    payment.sent_marked_at = datetime.now(timezone.utc)
    payment.sent_marked_by = user.username
    session.add(
        AuditLog(
            actor=user.username,
            action="aml_marked_sent",
            entity="expected_payment",
            entity_id=str(payment.id),
            details={"to_address": payment.to_address,
                     "amount": str(payment.amount)},
        )
    )
    await session.commit()
    return {"sent_marked_at": payment.sent_marked_at.isoformat()}
