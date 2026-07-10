"""Налоговый регистр операций с цифровой валютой (v1, п. 6 ТЗ).

Регистр для расчёта налоговой базы: доходы (рублёвая оценка поступлений
и выручка выбытий на дату признания — финальности), расходы (себестоимость
выбытий по ФИФО, комиссии сети). Каждая строка несёт журнал неизменяемости.

Рублёвая оценка — по снимкам курсов, зафиксированным на момент финальности
(правила НК с учётом поправок 2024–2026 конкретизируются подзаконными
актами — структура строк готова к маппингу в декларационные регистры 1С).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.models import (
    Direction,
    DisposalLine,
    RateSnapshot,
    Transaction,
    TxStatus,
)
from connector.reports.service import raw_response_sha256


async def tax_register_data(
    session: AsyncSession, date_from: datetime, date_to: datetime
) -> dict:
    txs = (
        (
            await session.execute(
                select(Transaction)
                .options(
                    selectinload(Transaction.network),
                    selectinload(Transaction.asset),
                )
                .where(
                    Transaction.status == TxStatus.FINAL,
                    Transaction.block_time >= date_from,
                    Transaction.block_time <= date_to,
                )
                .order_by(Transaction.block_time, Transaction.id)
            )
        )
        .scalars()
        .all()
    )

    rows: list[dict] = []
    totals = {"income": Decimal(0), "expense": Decimal(0), "fees": Decimal(0)}
    for tx in txs:
        finality_rate = await session.scalar(
            select(RateSnapshot).where(
                RateSnapshot.transaction_id == tx.id, RateSnapshot.purpose == "finality"
            )
        )
        if finality_rate is None:
            continue  # транзакция ещё не прошла конвейер
        amount_rub = tx.amount * finality_rate.asset_to_rub

        if tx.direction == Direction.IN:
            rows.append(_row(tx, "receipt", amount_rub, None, None))
            totals["income"] += amount_rub
        else:
            cost = await session.scalar(
                select(func.coalesce(func.sum(DisposalLine.cost_rub), 0)).where(
                    DisposalLine.transaction_id == tx.id
                )
            )
            cost = Decimal(str(cost))
            rows.append(_row(tx, "disposal", amount_rub, cost, amount_rub - cost))
            totals["income"] += amount_rub
            totals["expense"] += cost

        if tx.fee_amount and tx.fee_amount > 0:
            fee_rate = await session.scalar(
                select(RateSnapshot).where(
                    RateSnapshot.transaction_id == tx.id, RateSnapshot.purpose == "fee"
                )
            )
            if fee_rate is not None:
                fee_rub = tx.fee_amount * fee_rate.asset_to_rub
                rows.append(_fee_row(tx, fee_rub))
                totals["fees"] += fee_rub

    return {
        "report": "tax_register",
        "generated_at": datetime.now().astimezone().isoformat(),
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "rows": rows,
        "totals": {
            "income_rub": str(totals["income"]),
            "expense_rub": str(totals["expense"] + totals["fees"]),
            "fees_rub": str(totals["fees"]),
            "result_rub": str(totals["income"] - totals["expense"] - totals["fees"]),
        },
    }


def _row(tx: Transaction, kind: str, income: Decimal, cost: Decimal | None,
         result: Decimal | None) -> dict:
    return {
        "date": tx.block_time.isoformat(),
        "kind": kind,  # receipt | disposal
        "asset": tx.asset.symbol,
        "quantity": str(tx.amount),
        "income_rub": str(income),
        "cost_rub": str(cost) if cost is not None else None,
        "result_rub": str(result) if result is not None else None,
        "tx_hash": tx.tx_hash,
        "network": tx.network.code,
        "raw_response_sha256": raw_response_sha256(tx.raw_response),
    }


def _fee_row(tx: Transaction, fee_rub: Decimal) -> dict:
    return {
        "date": tx.block_time.isoformat(),
        "kind": "fee",
        "asset": tx.fee_asset,
        "quantity": str(tx.fee_amount),
        "income_rub": None,
        "cost_rub": str(fee_rub),
        "result_rub": str(-fee_rub),
        "tx_hash": tx.tx_hash,
        "network": tx.network.code,
        "raw_response_sha256": raw_response_sha256(tx.raw_response),
    }
