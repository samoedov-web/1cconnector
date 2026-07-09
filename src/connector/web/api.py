"""API веб-панели администратора: кошельки, очередь ручного разбора, мониторинг.

MVP-объём: управление отслеживаемыми адресами, просмотр очереди pending-матчей,
ручная привязка с запоминанием правила, health-check. RBAC-роли из моделей
(admin / operator / auditor) подключаются middleware-ом аутентификации панели.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.db import get_session
from connector.models import (
    AuditLog,
    CounterpartyAddress,
    Match,
    MatchState,
    Network,
    Transaction,
    Wallet,
)

router = APIRouter(prefix="/api/v1", tags=["admin"])


class WalletIn(BaseModel):
    network_code: str
    address: str
    label: str = ""
    backfill_from: datetime | None = None


class WalletOut(BaseModel):
    id: int
    network_code: str
    address: str
    label: str
    enabled: bool


@router.post("/wallets", response_model=WalletOut)
async def add_wallet(data: WalletIn, session: AsyncSession = Depends(get_session)) -> WalletOut:
    network = await session.scalar(select(Network).where(Network.code == data.network_code))
    if network is None:
        raise HTTPException(status_code=404, detail=f"Сеть {data.network_code} не настроена")
    wallet = Wallet(
        network_id=network.id,
        address=data.address,
        label=data.label,
        backfill_from=data.backfill_from,
    )
    session.add(wallet)
    session.add(
        AuditLog(
            actor="admin",  # TODO: из сессии пользователя панели
            action="wallet_added",
            entity="wallet",
            entity_id=data.address,
            details={"network": data.network_code, "backfill_from": str(data.backfill_from)},
        )
    )
    await session.commit()
    return WalletOut(
        id=wallet.id,
        network_code=network.code,
        address=wallet.address,
        label=wallet.label,
        enabled=wallet.enabled,
    )


class PendingMatchOut(BaseModel):
    match_id: int
    transaction_id: int
    tx_hash: str
    amount: str
    counterparty_id: int | None


@router.get("/matching/pending", response_model=list[PendingMatchOut])
async def pending_queue(session: AsyncSession = Depends(get_session)) -> list[PendingMatchOut]:
    """Очередь ручного разбора: всё, что не сматчилось автоматически."""
    rows = (
        await session.execute(
            select(Match, Transaction)
            .join(Transaction, Match.transaction_id == Transaction.id)
            .where(Match.state == MatchState.PENDING)
            .order_by(Match.id)
        )
    ).all()
    return [
        PendingMatchOut(
            match_id=m.id,
            transaction_id=t.id,
            tx_hash=t.tx_hash,
            amount=str(m.allocated_amount),
            counterparty_id=m.counterparty_id,
        )
        for m, t in rows
    ]


class ResolveIn(BaseModel):
    counterparty_id: int
    contract_id: int | None = None
    invoice_id: int | None = None
    allocated_amount: Decimal | None = None  # None — вся сумма pending-строки
    remember_address: bool = True  # запомнить адрес как правило автоматчинга
    actor: str = "operator"


@router.post("/matching/{match_id}/resolve")
async def resolve_match(
    match_id: int, data: ResolveIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """Ручная привязка из очереди разбора; система запоминает правило."""
    match = await session.get(Match, match_id)
    if match is None or match.state != MatchState.PENDING:
        raise HTTPException(status_code=404, detail="Pending-строка не найдена")
    tx = await session.get(Transaction, match.transaction_id)

    match.counterparty_id = data.counterparty_id
    match.contract_id = data.contract_id
    match.invoice_id = data.invoice_id
    if data.allocated_amount is not None:
        match.allocated_amount = data.allocated_amount
    match.state = MatchState.MANUAL
    match.matched_by = data.actor

    if data.remember_address and tx is not None:
        counterparty_address = (
            tx.from_address if tx.direction.value == "in" else tx.to_address
        )
        exists = await session.scalar(
            select(CounterpartyAddress.id).where(
                CounterpartyAddress.network_id == tx.network_id,
                CounterpartyAddress.address == counterparty_address,
            )
        )
        if exists is None:
            session.add(
                CounterpartyAddress(
                    counterparty_id=data.counterparty_id,
                    network_id=tx.network_id,
                    address=counterparty_address,
                    origin="learned",
                )
            )

    session.add(
        AuditLog(
            actor=data.actor,
            action="match_resolved",
            entity="match",
            entity_id=str(match_id),
            details={
                "counterparty_id": data.counterparty_id,
                "contract_id": data.contract_id,
                "invoice_id": data.invoice_id,
                "remembered": data.remember_address,
            },
        )
    )
    await session.commit()
    return {"status": "resolved"}
