"""Модуль сопоставления — ключевая ценность продукта (п. 5 ТЗ).

Правила автоматчинга в порядке приоритета:
  1. адрес отправителя/получателя ∈ справочника адресов контрагента → контрагент;
  2. сумма ± допуск и валюта совпадают с открытым инвойсом контрагента → инвойс;
  3. период платежа в окне ожидания по графику контракта.

Всё, что не сматчилось, — в очередь ручного разбора (state=pending);
при ручной привязке система запоминает адрес (CounterpartyAddress.origin=learned).

Движок реализован как чистая логика над снимками данных — тестируется без БД.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class TxView:
    """Снимок транзакции, достаточный для матчинга."""

    id: int
    counterparty_address: str  # адрес второй стороны (from для входящей, to для исходящей)
    amount: Decimal
    asset_symbol: str
    block_time: datetime


@dataclass(frozen=True)
class InvoiceView:
    id: int
    contract_id: int
    counterparty_id: int
    currency: str
    open_amount: Decimal  # остаток к оплате
    due_from: datetime | None
    due_to: datetime | None


@dataclass(frozen=True)
class Allocation:
    """Результат: разнесение (части) транзакции."""

    invoice_id: int | None
    contract_id: int | None
    counterparty_id: int | None
    amount: Decimal
    rule: str  # address | address+amount | address+window | none


@dataclass
class MatchOutcome:
    allocations: list[Allocation] = field(default_factory=list)
    needs_manual_review: bool = False


# Валюта инвойса считается совпавшей, если актив — стейблкоин этой валюты.
STABLECOIN_CURRENCY = {"USDT": "USD", "USDC": "USD"}


def _currency_matches(asset_symbol: str, invoice_currency: str) -> bool:
    return STABLECOIN_CURRENCY.get(asset_symbol.upper()) == invoice_currency.upper()


def match_transaction(
    tx: TxView,
    address_book: dict[str, int],  # lower(address) -> counterparty_id
    open_invoices: list[InvoiceView],
    amount_tolerance: Decimal = Decimal("0.005"),
) -> MatchOutcome:
    """Применить правила 1→2→3 к одной транзакции.

    Возвращает разнесение на инвойсы; частичные оплаты и переплаты
    поддерживаются: транзакция жадно (по сроку) закрывает несколько
    инвойсов, остаток уходит в ручной разбор.
    """
    counterparty_id = address_book.get(tx.counterparty_address.lower())
    if counterparty_id is None:
        # Правило 1 не сработало — контрагент неизвестен, весь платёж в разбор.
        return MatchOutcome(
            allocations=[Allocation(None, None, None, tx.amount, "none")],
            needs_manual_review=True,
        )

    candidates = [
        inv
        for inv in open_invoices
        if inv.counterparty_id == counterparty_id and _currency_matches(tx.asset_symbol, inv.currency)
    ]

    # Правило 2: сумма ± допуск с одним открытым инвойсом.
    for inv in candidates:
        tolerance = inv.open_amount * amount_tolerance
        if abs(tx.amount - inv.open_amount) <= tolerance:
            return MatchOutcome(
                allocations=[
                    Allocation(inv.id, inv.contract_id, counterparty_id, tx.amount, "address+amount")
                ]
            )

    # Правило 3: платёж в окне ожидания по графику — разнесение по срокам (ФИФО инвойсов).
    in_window = sorted(
        (
            inv
            for inv in candidates
            if (inv.due_from is None or inv.due_from <= tx.block_time)
            and (inv.due_to is None or tx.block_time <= inv.due_to)
        ),
        key=lambda i: (i.due_to or datetime.max, i.id),
    )
    allocations: list[Allocation] = []
    remaining = tx.amount
    for inv in in_window:
        if remaining <= 0:
            break
        part = min(remaining, inv.open_amount)
        allocations.append(
            Allocation(inv.id, inv.contract_id, counterparty_id, part, "address+window")
        )
        remaining -= part

    if remaining > 0:
        # Переплата или неизвестный платёж от известного контрагента —
        # контрагента фиксируем, остаток в ручной разбор.
        allocations.append(Allocation(None, None, counterparty_id, remaining, "address"))
        return MatchOutcome(allocations=allocations, needs_manual_review=True)

    return MatchOutcome(allocations=allocations)
