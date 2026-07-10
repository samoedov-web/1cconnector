#!/usr/bin/env python3
"""Контрольные примеры сверки для методолога (исполняемая спецификация).

Каждый кейс: две стороны (цепочка и выписка) → ожидаемый результат движка.
Скрипт прогоняет движок и сверяет с ожиданием: расхождение — ненулевой код
выхода. БД и сеть не нужны.

Запуск:  python scripts/reconciliation_demo.py
Кейсы описаны человекочитаемо в docs/methodologist-review.md — этот файл
их исполняет. Добавление кейса методолога = новая запись в CASES.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from connector.custody.reconciliation import (  # noqa: E402
    CustodyView,
    LedgerView,
    ReconConfig,
    reconcile,
)


def dt(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc)


def tx(id: int, amount: str, day: int, tx_hash: str) -> LedgerView:
    return LedgerView(id=id, tx_hash=tx_hash, asset="USDT",
                      amount=Decimal(amount), occurred_at=dt(day))


def line(id: int, amount: str, day: int, tx_hash: str | None = None,
         aggregate: bool = False) -> CustodyView:
    return CustodyView(id=id, entry_id=str(id), asset="USDT",
                       amount=Decimal(amount), occurred_at=dt(day),
                       external_tx_hash=tx_hash, maybe_aggregate=aggregate)


CASES = [
    {
        "name": "К1. Идеальный месяц: все платежи найдены по хэшам",
        "ledger": [tx(1, "48500", 6, "a"), tx(2, "-31200", 15, "b"),
                   tx(3, "12750", 19, "c")],
        "custody": [line(11, "48500", 6, "a"), line(12, "-31200", 15, "b"),
                    line(13, "12750", 19, "c")],
        "config": ReconConfig(),
        "expected": {"matched": 3},
    },
    {
        "name": "К2. Депозитарий потерял платёж (недостача в выписке)",
        "ledger": [tx(1, "48500", 6, "a"), tx(2, "12750", 19, "c")],
        "custody": [line(11, "48500", 6, "a")],
        "config": ReconConfig(),
        "expected": {"matched": 1, "missing_in_custody": 1},
    },
    {
        "name": "К3. В выписке операция, которой нет в цепочке",
        "ledger": [tx(1, "48500", 6, "a")],
        "custody": [line(11, "48500", 6, "a"), line(12, "9999", 20, "phantom")],
        "config": ReconConfig(),
        "expected": {"matched": 1, "missing_on_chain": 1},
    },
    {
        "name": "К4а. Комиссия 15 USDT удержана внутри суммы, допуск 0",
        "ledger": [tx(1, "31200", 15, "b")],
        "custody": [line(11, "31185", 15, "b")],
        "config": ReconConfig(),
        "expected": {"amount_mismatch": 1},
    },
    {
        "name": "К4б. Та же комиссия, допуск 20 USDT (учётная политика)",
        "ledger": [tx(1, "31200", 15, "b")],
        "custody": [line(11, "31185", 15, "b")],
        "config": ReconConfig(tolerance_abs=Decimal(20)),
        "expected": {"matched": 1},
    },
    {
        "name": "К5а. Выписка датирует платёж следующим днём (окно 48 ч)",
        "ledger": [tx(1, "500", 31, "x")],
        "custody": [CustodyView(11, "11", "USDT", Decimal("500"),
                                datetime(2026, 8, 1, 12, tzinfo=timezone.utc))],
        "config": ReconConfig(date_window=timedelta(hours=48)),
        "expected": {"matched": 1},
    },
    {
        "name": "К5б. То же, но окно 6 ч — расхождение даты",
        "ledger": [tx(1, "500", 31, "x")],
        "custody": [CustodyView(11, "11", "USDT", Decimal("500"),
                                datetime(2026, 8, 1, 12, tzinfo=timezone.utc))],
        "config": ReconConfig(date_window=timedelta(hours=6)),
        "expected": {"date_mismatch": 1},
    },
    {
        "name": "К6. Выписка агрегирует день: одна строка = 5 платежей",
        "ledger": [tx(i, "100", 6, f"h{i}") for i in range(1, 6)],
        "custody": [line(11, "500", 6, aggregate=True)],
        "config": ReconConfig(),
        "expected": {"matched_aggregate": 1},
    },
    {
        "name": "К7. Агрегат покрыл 4 платежа из 5 — расхождение с дельтой",
        "ledger": [tx(i, "100", 6, f"h{i}") for i in range(1, 6)],
        "custody": [line(11, "400", 6, aggregate=True)],
        "config": ReconConfig(),
        "expected": {"amount_mismatch": 1},
    },
    {
        "name": "К8. Строка выписки задвоена — не двойной матч",
        "ledger": [tx(1, "48500", 6, "a")],
        "custody": [line(11, "48500", 6, "a"), line(12, "48500", 6, "a")],
        "config": ReconConfig(),
        "expected": {"matched": 1, "duplicate_suspect": 1},
    },
    {
        "name": "К9. Агрегаты требуют ручного подтверждения (настройка)",
        "ledger": [tx(i, "100", 6, f"h{i}") for i in range(1, 4)],
        "custody": [line(11, "300", 6, aggregate=True)],
        "config": ReconConfig(accept_aggregates=False),
        "expected": {"manual": 1},
    },
    {
        "name": "К10. Относительный допуск 0.1% на крупном платеже",
        "ledger": [tx(1, "1000000", 6, "big")],
        "custody": [line(11, "999500", 6, "big")],  # −0.05%
        "config": ReconConfig(tolerance_rel=Decimal("0.001")),
        "expected": {"matched": 1},
    },
]


def main() -> int:
    failures = 0
    print("Контрольные примеры сверки блокчейн ↔ депозитарий")
    print("=" * 72)
    for case in CASES:
        results = reconcile(case["ledger"], case["custody"], case["config"])
        actual = Counter(item.status.value for item in results)
        expected = Counter(case["expected"])
        ok = actual == expected
        mark = "✅" if ok else "❌"
        print(f"{mark} {case['name']}")
        print(f"   ожидание: {dict(expected)}")
        if not ok:
            print(f"   ФАКТ:     {dict(actual)}")
            failures += 1
        for item in results:
            if item.status.value not in ("matched", "matched_aggregate"):
                reason = item.detail.get("reason") or item.detail.get("delta", "")
                print(f"   · {item.status.value} [{item.rule}] {reason}")
    print("=" * 72)
    print(f"Кейсов: {len(CASES)}, расхождений с ожиданием: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
