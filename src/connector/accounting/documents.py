"""Генерация проектов документов 1С по событиям (п. 6 ТЗ).

События → документы:
- финальность входящей транзакции  → «Поступление цифровой валюты» + партия ФИФО;
- финальность исходящей транзакции → «Выбытие цифровой валюты» (расшифровка ФИФО);
- комиссия сети                    → отдельная статья расходов;
- отчётная дата                    → «Переоценка цифровой валюты» (v1).

Мэппинг на план счетов конфигурируем на стороне 1С; коннектор передаёт
данные и аналитику, счета подставляет расширение по настройкам клиента.
"""

from __future__ import annotations

from decimal import Decimal

from connector.accounting.fifo import DisposalResult
from connector.models import (
    OnecDocType,
    RateSnapshot,
    Transaction,
)

# Привязка платежа для 1С: *_ref — GUID объектов 1С (заполняются
# синхронизацией справочников, onec/sync.py), *_id — внутренние id
# коннектора (диагностика), имена/номера — для показа бухгалтеру,
# если GUID ещё не привязан.
#   {
#     "amount": "...",
#     "counterparty_ref": "<GUID|''>", "counterparty_id": 1, "counterparty_name": "...",
#     "contract_ref": "<GUID|''>", "contract_id": 2, "contract_number": "...",
#     "invoice_ref": "<GUID|''>", "invoice_id": 3, "invoice_number": "...",
#   }
Allocation1C = dict


def idempotency_key(tx: Transaction, doc_type: OnecDocType, network_code: str) -> str:
    """Ключ идемпотентности обмена: повторная выгрузка не создаёт дублей."""
    return f"{network_code}:{tx.tx_hash}:{tx.log_index}:{doc_type.value}"


def _base_payload(tx: Transaction, rate: RateSnapshot) -> dict:
    return {
        "tx_hash": tx.tx_hash,
        "log_index": tx.log_index,
        "network": tx.network.code,
        "asset": tx.asset.symbol,
        "block_time": tx.block_time.isoformat(),
        "finalized_at": tx.finalized_at.isoformat() if tx.finalized_at else None,
        "amount": str(tx.amount),
        "rate": {
            "asset_to_contract": str(rate.asset_to_contract),
            "contract_currency": rate.contract_currency,
            "contract_to_rub": str(rate.contract_to_rub),
            "asset_to_rub": str(rate.asset_to_rub),
            "source": rate.source_primary,
            "as_of": rate.as_of.isoformat(),
        },
    }


def build_receipt(
    tx: Transaction, rate: RateSnapshot, allocations: list[Allocation1C]
) -> dict:
    """«Поступление цифровой валюты»: входящий платёж по контракту."""
    amount_rub = tx.amount * rate.asset_to_rub
    return _base_payload(tx, rate) | {
        "doc_type": OnecDocType.RECEIPT.value,
        "from_address": tx.from_address,
        "to_wallet": tx.wallet.address,
        "amount_rub": str(amount_rub),
        "allocations": allocations,
    }


def build_disposal(
    tx: Transaction,
    rate: RateSnapshot,
    allocations: list[Allocation1C],
    fifo: DisposalResult,
) -> dict:
    """«Выбытие цифровой валюты»: оплата поставщику / продажа за рубли."""
    return _base_payload(tx, rate) | {
        "doc_type": OnecDocType.DISPOSAL.value,
        "from_wallet": tx.wallet.address,
        "to_address": tx.to_address,
        "proceeds_rub": str(fifo.proceeds_rub),
        "cost_rub": str(fifo.total_cost_rub),
        "gain_rub": str(fifo.gain_rub),
        "fifo_lines": [
            {"lot_id": p.lot_id, "quantity": str(p.quantity), "cost_rub": str(p.cost_rub)}
            for p in fifo.parts
        ],
        "allocations": allocations,
    }


def build_fee(tx: Transaction, fee_rub: Decimal) -> dict:
    """Комиссия сети — отдельная статья расходов."""
    return {
        "doc_type": OnecDocType.FEE.value,
        "tx_hash": tx.tx_hash,
        "network": tx.network.code,
        "fee_asset": tx.fee_asset,
        "fee_amount": str(tx.fee_amount),
        "fee_rub": str(fee_rub),
        "block_time": tx.block_time.isoformat(),
    }
