"""API веб-панели администратора: кошельки, очередь ручного разбора, мониторинг.

Матрица доступа (RBAC, п. 9 ТЗ):
- кошельки (изменение)          — admin;
- очередь разбора (просмотр)    — admin / operator / auditor;
- разбор (привязка)             — admin / operator.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.db import get_session
from connector.models import (
    AuditLog,
    CounterpartyAddress,
    Direction,
    Invoice,
    Match,
    MatchState,
    Network,
    Transaction,
    Wallet,
)
from connector.pipeline import apply_payment_to_invoice
from connector.security import CurrentUser, require_admin, require_operator, require_reader

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
    last_scanned_at: datetime | None = None


@router.get("/wallets", response_model=list[WalletOut])
async def list_wallets(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> list[WalletOut]:
    rows = (
        await session.execute(
            select(Wallet, Network.code).join(Network, Wallet.network_id == Network.id)
        )
    ).all()
    return [
        WalletOut(
            id=w.id,
            network_code=code,
            address=w.address,
            label=w.label,
            enabled=w.enabled,
            last_scanned_at=w.last_scanned_at,
        )
        for w, code in rows
    ]


@router.post("/wallets", response_model=WalletOut)
async def add_wallet(
    data: WalletIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_admin),
) -> WalletOut:
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
            actor=user.username,
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
    network: str
    asset: str
    direction: str
    block_time: datetime
    amount: str  # нераспределённая часть (allocated_amount pending-строки)
    tx_amount: str  # полная сумма транзакции
    counterparty_address: str  # адрес второй стороны — ключ для привязки
    counterparty_id: int | None


@router.get("/matching/pending", response_model=list[PendingMatchOut])
async def pending_queue(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> list[PendingMatchOut]:
    """Очередь ручного разбора: всё, что не сматчилось автоматически."""
    rows = (
        await session.execute(
            select(Match, Transaction)
            .join(Transaction, Match.transaction_id == Transaction.id)
            .options(
                selectinload(Transaction.network),
                selectinload(Transaction.asset),
            )
            .where(Match.state == MatchState.PENDING)
            .order_by(Match.id)
        )
    ).all()
    return [
        PendingMatchOut(
            match_id=m.id,
            transaction_id=t.id,
            tx_hash=t.tx_hash,
            network=t.network.code,
            asset=t.asset.symbol,
            direction=t.direction.value,
            block_time=t.block_time,
            amount=str(m.allocated_amount),
            tx_amount=str(t.amount),
            counterparty_address=(
                t.from_address if t.direction == Direction.IN else t.to_address
            ),
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


@router.post("/matching/{match_id}/resolve")
async def resolve_match(
    match_id: int,
    data: ResolveIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_operator),
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
    match.matched_by = user.username

    if data.invoice_id is not None:
        invoice = await session.get(Invoice, data.invoice_id)
        if invoice is None:
            raise HTTPException(status_code=404, detail="Инвойс не найден")
        apply_payment_to_invoice(invoice, match.allocated_amount)

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
            actor=user.username,
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
