"""Сборка данных печатных форм (п. 7 ТЗ).

Журнал неизменяемости: каждая строка каждого отчёта несёт ссылку на исходную
транзакцию — хэш, сеть, блок, источник данных, метку времени получения и
SHA-256 канонизированного сырого ответа ноды. Данные собираются в
машиночитаемую структуру (format=json) — она же контекст HTML/XLSX-шаблонов
и заготовка под будущие электронные форматы ФНС.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.hashing import canonical_sha256
from connector.models import (
    Contract,
    Counterparty,
    Invoice,
    Match,
    RateSnapshot,
    Transaction,
    Wallet,
)


def raw_response_sha256(raw: dict) -> str:
    """Хэш канонизированного сырого ответа ноды — для сверки при проверках."""
    return canonical_sha256(raw)


def _immutability_block(tx: Transaction) -> dict:
    return {
        "tx_hash": tx.tx_hash,
        "log_index": tx.log_index,
        "network": tx.network.code,
        "block_number": tx.block_number,
        "source": tx.source,
        "received_at": tx.received_at.isoformat() if tx.received_at else None,
        "raw_response_sha256": raw_response_sha256(tx.raw_response),
    }


async def _tx_with_refs(session: AsyncSession, tx_id: int) -> Transaction | None:
    return await session.scalar(
        select(Transaction)
        .options(
            selectinload(Transaction.network),
            selectinload(Transaction.asset),
            selectinload(Transaction.wallet),
        )
        .where(Transaction.id == tx_id)
    )


async def _allocations(session: AsyncSession, tx_id: int) -> list[dict]:
    rows = (
        (await session.execute(select(Match).where(Match.transaction_id == tx_id)))
        .scalars()
        .all()
    )
    result = []
    for m in rows:
        counterparty = (
            await session.get(Counterparty, m.counterparty_id) if m.counterparty_id else None
        )
        contract = await session.get(Contract, m.contract_id) if m.contract_id else None
        invoice = await session.get(Invoice, m.invoice_id) if m.invoice_id else None
        result.append(
            {
                "amount": str(m.allocated_amount),
                "state": m.state.value,
                "rule": m.rule,
                "matched_by": m.matched_by,
                "counterparty": counterparty.name if counterparty else None,
                "contract_number": contract.number if contract else None,
                # Учётный номер контракта — основа справки валютного контроля.
                "contract_registration_number": (
                    contract.registration_number if contract else None
                ),
                "kvvo": contract.kvvo if contract else None,
                "invoice_number": invoice.number if invoice else None,
            }
        )
    return result


async def payment_act_data(session: AsyncSession, tx_id: int) -> dict | None:
    """Справка/акт по крипто-платежу для банка и цифрового депозитария."""
    tx = await _tx_with_refs(session, tx_id)
    if tx is None:
        return None
    rate = await session.scalar(
        select(RateSnapshot).where(
            RateSnapshot.transaction_id == tx.id, RateSnapshot.purpose == "finality"
        )
    )
    return {
        "report": "payment_act",
        "generated_at": datetime.now().astimezone().isoformat(),
        "payment": {
            "direction": tx.direction.value,
            "asset": tx.asset.symbol,
            "amount": str(tx.amount),
            "from_address": tx.from_address,
            "to_address": tx.to_address,
            "wallet_label": tx.wallet.label,
            "block_time": tx.block_time.isoformat(),
            "finalized_at": tx.finalized_at.isoformat() if tx.finalized_at else None,
            "status": tx.status.value,
            "confirmations": tx.confirmations,
        },
        "rate": None
        if rate is None
        else {
            "asset_to_contract": str(rate.asset_to_contract),
            "contract_currency": rate.contract_currency,
            "contract_to_rub": str(rate.contract_to_rub),
            "asset_to_rub": str(rate.asset_to_rub),
            "amount_rub": str(tx.amount * rate.asset_to_rub),
            "source": rate.source_primary,
            "as_of": rate.as_of.isoformat(),
        },
        "allocations": await _allocations(session, tx.id),
        "immutability": _immutability_block(tx),
    }


async def journal_data(
    session: AsyncSession, wallet_id: int, date_from: datetime, date_to: datetime
) -> dict | None:
    """Журнал операций по адресу за период (отчёт в ФНС по операциям
    через иностранные кошельки)."""
    wallet = await session.scalar(
        select(Wallet).options(selectinload(Wallet.network)).where(Wallet.id == wallet_id)
    )
    if wallet is None:
        return None
    txs = (
        (
            await session.execute(
                select(Transaction)
                .options(
                    selectinload(Transaction.network),
                    selectinload(Transaction.asset),
                    selectinload(Transaction.wallet),
                )
                .where(
                    Transaction.wallet_id == wallet_id,
                    Transaction.block_time >= date_from,
                    Transaction.block_time <= date_to,
                )
                .order_by(Transaction.block_number, Transaction.log_index)
            )
        )
        .scalars()
        .all()
    )
    rows = []
    for tx in txs:
        rate = await session.scalar(
            select(RateSnapshot).where(
                RateSnapshot.transaction_id == tx.id, RateSnapshot.purpose == "finality"
            )
        )
        rows.append(
            {
                "block_time": tx.block_time.isoformat(),
                "direction": tx.direction.value,
                "asset": tx.asset.symbol,
                "amount": str(tx.amount),
                "amount_rub": str(tx.amount * rate.asset_to_rub) if rate else None,
                "from_address": tx.from_address,
                "to_address": tx.to_address,
                "status": tx.status.value,
                "allocations": await _allocations(session, tx.id),
                "immutability": _immutability_block(tx),
            }
        )
    return {
        "report": "operations_journal",
        "generated_at": datetime.now().astimezone().isoformat(),
        "wallet": {
            "address": wallet.address,
            "label": wallet.label,
            "network": wallet.network.code,
        },
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "rows": rows,
        "totals": {
            "count": len(rows),
            "in": str(sum((tx.amount for tx in txs if tx.direction.value == "in"), start=0)),
            "out": str(sum((tx.amount for tx in txs if tx.direction.value == "out"), start=0)),
        },
    }
