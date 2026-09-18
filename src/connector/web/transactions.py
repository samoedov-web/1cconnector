"""API транзакций для веб-панели: список с фильтрами и карточка.

Карточка включает журнал неизменяемости и привязки — то, что оператор
видит перед печатью акта. Доступ — любой аутентифицированный (чтение).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.db import get_session
from connector.models import (
    Direction,
    ExpectedPayment,
    Match,
    Network,
    OnecDocument,
    RateSnapshot,
    Transaction,
    TxStatus,
    Wallet,
)
from connector.reports.service import payment_act_data
from connector.security import CurrentUser, require_reader

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


class TxRow(BaseModel):
    id: int
    network: str
    wallet_address: str
    tx_hash: str
    block_time: datetime
    direction: Direction
    asset: str
    amount: str
    from_address: str
    to_address: str
    status: TxStatus
    confirmations: int
    cross_checked: bool


class TxPage(BaseModel):
    total: int
    items: list[TxRow]


@router.get("", response_model=TxPage)
async def list_transactions(
    network: str | None = None,
    wallet_id: int | None = None,
    status: TxStatus | None = None,
    direction: Direction | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    tx_hash: str | None = Query(default=None, description="точный хэш или его начало"),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> TxPage:
    stmt = select(Transaction).options(
        selectinload(Transaction.network),
        selectinload(Transaction.asset),
        selectinload(Transaction.wallet),
    )
    count_stmt = select(func.count(Transaction.id))
    conditions = []
    if network:
        network_id = await session.scalar(select(Network.id).where(Network.code == network))
        conditions.append(Transaction.network_id == (network_id or -1))
    if wallet_id is not None:
        conditions.append(Transaction.wallet_id == wallet_id)
    if status is not None:
        conditions.append(Transaction.status == status)
    if direction is not None:
        conditions.append(Transaction.direction == direction)
    if date_from is not None:
        conditions.append(Transaction.block_time >= date_from)
    if date_to is not None:
        conditions.append(Transaction.block_time <= date_to)
    if tx_hash:
        conditions.append(Transaction.tx_hash.like(tx_hash + "%"))
    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = await session.scalar(count_stmt)
    rows = (
        (
            await session.execute(
                stmt.order_by(Transaction.block_time.desc(), Transaction.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return TxPage(
        total=total or 0,
        items=[
            TxRow(
                id=t.id,
                network=t.network.code,
                wallet_address=t.wallet.address,
                tx_hash=t.tx_hash,
                block_time=t.block_time,
                direction=t.direction,
                asset=t.asset.symbol,
                amount=str(t.amount),
                from_address=t.from_address,
                to_address=t.to_address,
                status=t.status,
                confirmations=t.confirmations,
                cross_checked=t.cross_checked,
            )
        for t in rows
        ],
    )


@router.get("/{tx_id}")
async def transaction_card(
    tx_id: int,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> dict:
    """Карточка: реквизиты + привязки + курс + журнал неизменяемости + документы 1С.

    Основа — та же структура, что у акта по платежу (переиспользуем сборку),
    дополненная статусами матчей и документов обмена.
    """
    data = await payment_act_data(session, tx_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Транзакция не найдена")

    matches = (
        (await session.execute(select(Match).where(Match.transaction_id == tx_id)))
        .scalars()
        .all()
    )
    docs = (
        (
            await session.execute(
                select(OnecDocument).where(OnecDocument.transaction_id == tx_id)
            )
        )
        .scalars()
        .all()
    )
    has_rate = (
        await session.scalar(
            select(RateSnapshot.id).where(RateSnapshot.transaction_id == tx_id)
        )
        is not None
    )
    # Пометка регламента (фаза 4 aml-спеки): исходящая либо связана с
    # одобренным ожиданием, либо помечается «вне регламента»; переводы на
    # собственные кошельки — внутренние перемещения.
    tx = await session.get(Transaction, tx_id)
    aml = None
    if tx is not None and tx.direction == Direction.OUT:
        payment = await session.scalar(
            select(ExpectedPayment).where(ExpectedPayment.transaction_id == tx_id)
        )
        if payment is not None:
            aml = {
                "linked": True,
                "status": payment.status.value,
                "expected_payment_id": payment.id,
            }
        else:
            internal = await session.scalar(
                select(Wallet.id).where(
                    Wallet.network_id == tx.network_id,
                    Wallet.address == tx.to_address,
                )
            )
            aml = {"linked": False, "internal": internal is not None}
    return data | {
        "id": tx_id,
        "matches": [
            {
                "id": m.id,
                "state": m.state.value,
                "rule": m.rule,
                "matched_by": m.matched_by,
                "amount": str(m.allocated_amount),
            }
            for m in matches
        ],
        "onec_documents": [
            {
                "doc_type": d.doc_type.value,
                "status": d.status.value,
                "onec_ref": d.onec_ref,
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
        "has_rate_snapshot": has_rate,
        "aml": aml,
    }
