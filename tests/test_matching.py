"""Тесты правил автоматчинга (п. 5 ТЗ)."""

from datetime import datetime, timezone
from decimal import Decimal

from connector.matching.engine import InvoiceView, TxView, match_transaction


def dt(day: int) -> datetime:
    return datetime(2026, 3, day, tzinfo=timezone.utc)


ADDR = "TXyzCounterpartyAddress111111111"
BOOK = {ADDR.lower(): 7}


def tx(amount: str, day: int = 10) -> TxView:
    return TxView(
        id=1,
        counterparty_address=ADDR,
        amount=Decimal(amount),
        asset_symbol="USDT",
        block_time=dt(day),
    )


def invoice(inv_id: int, open_amount: str, due_from: int | None = 1, due_to: int | None = 28,
            counterparty_id: int = 7, currency: str = "USD") -> InvoiceView:
    return InvoiceView(
        id=inv_id,
        contract_id=100 + inv_id,
        counterparty_id=counterparty_id,
        currency=currency,
        open_amount=Decimal(open_amount),
        due_from=dt(due_from) if due_from else None,
        due_to=dt(due_to) if due_to else None,
    )


def test_unknown_address_goes_to_manual_queue():
    outcome = match_transaction(tx("1000"), address_book={}, open_invoices=[invoice(1, "1000")])
    assert outcome.needs_manual_review
    assert outcome.allocations[0].counterparty_id is None
    assert outcome.allocations[0].rule == "none"


def test_rule2_amount_within_tolerance_matches_invoice():
    # 1000 против инвойса 1003 — в пределах допуска 0.5%
    outcome = match_transaction(tx("1000"), BOOK, [invoice(1, "1003")])
    assert not outcome.needs_manual_review
    [alloc] = outcome.allocations
    assert alloc.invoice_id == 1
    assert alloc.rule == "address+amount"
    assert alloc.amount == Decimal("1000")


def test_currency_mismatch_blocks_invoice_match():
    outcome = match_transaction(tx("1000"), BOOK, [invoice(1, "1000", currency="EUR")])
    assert outcome.needs_manual_review


def test_rule3_window_partial_payment_across_invoices():
    # 1500 закрывает инвойс 1 (1000) целиком и инвойс 2 (2000) частично.
    outcome = match_transaction(
        tx("1500"), BOOK, [invoice(1, "1000", due_to=15), invoice(2, "2000", due_to=25)]
    )
    assert not outcome.needs_manual_review
    assert [(a.invoice_id, a.amount) for a in outcome.allocations] == [
        (1, Decimal("1000")),
        (2, Decimal("500")),
    ]
    assert all(a.rule == "address+window" for a in outcome.allocations)


def test_overpayment_remainder_goes_to_manual_with_known_counterparty():
    outcome = match_transaction(tx("1300"), BOOK, [invoice(1, "1000", due_to=15)])
    assert outcome.needs_manual_review
    assert outcome.allocations[0].invoice_id == 1
    assert outcome.allocations[0].amount == Decimal("1000")
    tail = outcome.allocations[-1]
    assert tail.invoice_id is None
    assert tail.counterparty_id == 7  # контрагент определён правилом 1
    assert tail.amount == Decimal("300")


def test_payment_outside_window_not_allocated():
    outcome = match_transaction(tx("500", day=20), BOOK, [invoice(1, "500", due_from=1, due_to=15)])
    # Не правило 2 (500 == 500? да — правило 2 сработает раньше окна).
    # Поэтому берём сумму, не совпадающую с инвойсом:
    outcome = match_transaction(tx("400", day=20), BOOK, [invoice(1, "900", due_from=1, due_to=15)])
    assert outcome.needs_manual_review
    assert outcome.allocations[0].invoice_id is None
    assert outcome.allocations[0].counterparty_id == 7


def test_invoices_of_other_counterparty_ignored():
    outcome = match_transaction(tx("1000"), BOOK, [invoice(1, "1000", counterparty_id=99)])
    assert outcome.needs_manual_review
