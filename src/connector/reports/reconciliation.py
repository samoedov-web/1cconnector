"""Акт сверки с контрагентом, RU/EN (v1, п. 7 ТЗ).

Сверка за период: начисления (инвойсы по контрактам контрагента) против
оплат (финальные транзакции, привязанные матчингом), сальдо в валюте
контракта. Двуязычный шаблон под подпись обеих сторон ВЭД-контракта.

Совместимость с шаблоном Продукта 1: структура данных (начисления/оплаты/
сальдо + реквизиты контракта) повторяет его состав; при получении
оригинального шаблона заменяется только HTML в templates/reports/.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.models import (
    Contract,
    Counterparty,
    Invoice,
    Match,
    RateSnapshot,
    Transaction,
    TxStatus,
)


async def reconciliation_data(
    session: AsyncSession,
    counterparty_id: int,
    date_from: datetime,
    date_to: datetime,
) -> dict | None:
    counterparty = await session.get(Counterparty, counterparty_id)
    if counterparty is None:
        return None

    contracts = (
        (
            await session.execute(
                select(Contract).where(Contract.counterparty_id == counterparty_id)
            )
        )
        .scalars()
        .all()
    )
    contract_by_id = {c.id: c for c in contracts}

    invoices = []
    invoiced_total = Decimal(0)
    if contract_by_id:
        for inv in (
            (
                await session.execute(
                    select(Invoice).where(
                        Invoice.contract_id.in_(contract_by_id),
                        Invoice.due_from >= date_from,
                        Invoice.due_from <= date_to,
                    )
                )
            )
            .scalars()
            .all()
        ):
            contract = contract_by_id[inv.contract_id]
            invoices.append(
                {
                    "date": inv.due_from.isoformat() if inv.due_from else None,
                    "number": inv.number,
                    "contract_number": contract.number,
                    "amount": str(inv.amount),
                    "currency": inv.currency,
                }
            )
            invoiced_total += inv.amount

    payments = []
    paid_total = Decimal(0)
    rows = (
        await session.execute(
            select(Match, Transaction)
            .join(Transaction, Match.transaction_id == Transaction.id)
            .options(selectinload(Transaction.asset), selectinload(Transaction.network))
            .where(
                Match.counterparty_id == counterparty_id,
                Transaction.status == TxStatus.FINAL,
                Transaction.block_time >= date_from,
                Transaction.block_time <= date_to,
            )
            .order_by(Transaction.block_time)
        )
    ).all()
    for match, tx in rows:
        rate = await session.scalar(
            select(RateSnapshot).where(
                RateSnapshot.transaction_id == tx.id, RateSnapshot.purpose == "finality"
            )
        )
        in_contract_currency = (
            match.allocated_amount * rate.asset_to_contract if rate else None
        )
        contract = contract_by_id.get(match.contract_id)
        payments.append(
            {
                "date": tx.block_time.isoformat(),
                "tx_hash": tx.tx_hash,
                "network": tx.network.code,
                "asset": tx.asset.symbol,
                "amount": str(match.allocated_amount),
                "direction": tx.direction.value,
                "contract_number": contract.number if contract else None,
                "contract_currency_amount": (
                    str(in_contract_currency) if in_contract_currency is not None else None
                ),
                "contract_currency": rate.contract_currency if rate else None,
            }
        )
        if in_contract_currency is not None:
            paid_total += in_contract_currency

    currency = contracts[0].currency if contracts else "USD"
    return {
        "report": "reconciliation_act",
        "generated_at": datetime.now().astimezone().isoformat(),
        "counterparty": {"name": counterparty.name},
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "contracts": [
            {"number": c.number, "registration_number": c.registration_number,
             "currency": c.currency}
            for c in contracts
        ],
        "invoices": invoices,
        "payments": payments,
        "totals": {
            "invoiced": str(invoiced_total),
            "paid": str(paid_total),
            "balance": str(invoiced_total - paid_total),
            "currency": currency,
        },
    }
