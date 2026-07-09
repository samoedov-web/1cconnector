"""Синхронизация справочников из 1С (первичная привязка GUID).

Фоновое задание расширения 1С периодически выгружает в коннектор свои
справочники: контрагентов, договоры ВЭД и открытые счета (инвойсы) с их
GUID. Коннектор делает upsert по onec_ref — после этого автоматчинг
привязывает транзакции к объектам, на которые расширение сможет сослаться
GUID-ом при создании документов.

Правила upsert:
- ключ — onec_ref (GUID 1С); объект без GUID эндпоинтом не создаётся;
- договор ссылается на контрагента его GUID-ом, инвойс — на договор;
  ссылка на невыгруженный объект — ошибка всей пачки (400), чтобы 1С
  не отправляла справочники частями в неверном порядке;
- у инвойса обновляются реквизиты (номер, сумма, валюта, окно оплаты),
  но НЕ paid_amount/status — оплаты учитывает коннектор.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.db import get_session
from connector.models import AuditLog, Contract, Counterparty, Invoice
from connector.onec.api import require_exchange_token

router = APIRouter(prefix="/api/v1/onec", tags=["1c-exchange"])


class CounterpartyIn(BaseModel):
    onec_ref: str = Field(min_length=1)
    name: str


class ContractIn(BaseModel):
    onec_ref: str = Field(min_length=1)
    counterparty_ref: str = Field(min_length=1)
    number: str
    registration_number: str = ""  # учётный номер (валютный контроль)
    currency: str


class InvoiceIn(BaseModel):
    onec_ref: str = Field(min_length=1)
    contract_ref: str = Field(min_length=1)
    number: str
    amount: Decimal
    currency: str
    due_from: datetime | None = None
    due_to: datetime | None = None


class SyncIn(BaseModel):
    counterparties: list[CounterpartyIn] = []
    contracts: list[ContractIn] = []
    invoices: list[InvoiceIn] = []


async def apply_sync(session: AsyncSession, data: SyncIn) -> dict[str, int]:
    counts = {"counterparties": 0, "contracts": 0, "invoices": 0}

    for cp in data.counterparties:
        existing = await session.scalar(
            select(Counterparty).where(Counterparty.onec_ref == cp.onec_ref)
        )
        if existing is None:
            session.add(Counterparty(name=cp.name, onec_ref=cp.onec_ref))
        else:
            existing.name = cp.name
        counts["counterparties"] += 1
    await session.flush()

    for c in data.contracts:
        counterparty = await session.scalar(
            select(Counterparty).where(Counterparty.onec_ref == c.counterparty_ref)
        )
        if counterparty is None:
            raise HTTPException(
                status_code=400,
                detail=f"Контрагент {c.counterparty_ref} для договора {c.number}"
                " не выгружен — отправьте контрагентов в той же пачке",
            )
        existing = await session.scalar(
            select(Contract).where(Contract.onec_ref == c.onec_ref)
        )
        if existing is None:
            session.add(
                Contract(
                    counterparty_id=counterparty.id,
                    number=c.number,
                    registration_number=c.registration_number,
                    currency=c.currency,
                    onec_ref=c.onec_ref,
                )
            )
        else:
            existing.counterparty_id = counterparty.id
            existing.number = c.number
            existing.registration_number = c.registration_number
            existing.currency = c.currency
        counts["contracts"] += 1
    await session.flush()

    for inv in data.invoices:
        contract = await session.scalar(
            select(Contract).where(Contract.onec_ref == inv.contract_ref)
        )
        if contract is None:
            raise HTTPException(
                status_code=400,
                detail=f"Договор {inv.contract_ref} для счёта {inv.number}"
                " не выгружен — отправьте договоры в той же пачке",
            )
        existing = await session.scalar(
            select(Invoice).where(Invoice.onec_ref == inv.onec_ref)
        )
        if existing is None:
            session.add(
                Invoice(
                    contract_id=contract.id,
                    number=inv.number,
                    amount=inv.amount,
                    currency=inv.currency,
                    due_from=inv.due_from,
                    due_to=inv.due_to,
                    onec_ref=inv.onec_ref,
                )
            )
        else:
            existing.contract_id = contract.id
            existing.number = inv.number
            existing.amount = inv.amount
            existing.currency = inv.currency
            existing.due_from = inv.due_from
            existing.due_to = inv.due_to
        counts["invoices"] += 1
    await session.flush()
    return counts


@router.post("/sync")
async def sync_directories(
    data: SyncIn,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_exchange_token),
) -> dict:
    counts = await apply_sync(session, data)
    session.add(
        AuditLog(
            actor="1c-exchange",
            action="directories_synced",
            entity="sync",
            entity_id="-",
            details=counts,
        )
    )
    await session.commit()
    return {"synced": counts}
