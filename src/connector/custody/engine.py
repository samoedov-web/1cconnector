"""Движок сверки блокчейн ↔ депозитарий (п. 4.5 спеки, фаза 4).

Чистая логика над снимками данных (тестируется без БД). Каскад матчинга
по убыванию приоритета:

  0. дубликаты строк выписки → duplicate_suspect (не двойной матч);
  1. по external_tx_hash — точное соответствие; расхождение суммы сверх
     допуска при совпавшем хэше → amount_mismatch;
  2. по кортежу (asset, amount ± tolerance, occurred_at ± date_window,
     направление); если пара очевидна, но сумма/дата вне допуска —
     amount_mismatch / date_mismatch;
  3. агрегатный матчинг: одна строка выписки ↔ N транзакций (и наоборот);
     частичное покрытие агрегата → amount_mismatch с дельтой;
  4. остальное — missing_in_custody / missing_on_chain (уходит в очередь
     ручного разбора; связанное оператором получает статус manual).

Свойства: детерминированность (входы сортируются, выбор кандидата по
фиксированному ключу), полный аудит решений в detail каждого результата.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal


class ReconStatus(enum.StrEnum):
    MATCHED = "matched"
    MATCHED_AGGREGATE = "matched_aggregate"
    MISSING_IN_CUSTODY = "missing_in_custody"
    MISSING_ON_CHAIN = "missing_on_chain"
    AMOUNT_MISMATCH = "amount_mismatch"
    DATE_MISMATCH = "date_mismatch"
    DUPLICATE_SUSPECT = "duplicate_suspect"
    MANUAL = "manual"


@dataclass(frozen=True)
class LedgerView:
    """Финальная on-chain транзакция; amount со знаком (＋поступление)."""

    id: int
    tx_hash: str
    asset: str
    amount: Decimal
    occurred_at: datetime


@dataclass(frozen=True)
class CustodyView:
    """Строка выписки после маппинга активов; amount со знаком."""

    id: int
    entry_id: str
    asset: str
    amount: Decimal
    occurred_at: datetime
    external_tx_hash: str | None = None
    maybe_aggregate: bool = False  # granularity выписки aggregated/mixed


@dataclass(frozen=True)
class ReconConfig:
    """Конфигурация per-client (п. 4.5): допуски, окно, приём агрегатов."""

    tolerance_abs: Decimal = Decimal(0)
    tolerance_rel: Decimal = Decimal(0)  # доля от суммы
    date_window: timedelta = timedelta(days=1)
    accept_aggregates: bool = True

    def as_dict(self) -> dict:
        return {
            "tolerance_abs": str(self.tolerance_abs),
            "tolerance_rel": str(self.tolerance_rel),
            "date_window_hours": self.date_window.total_seconds() / 3600,
            "accept_aggregates": self.accept_aggregates,
        }


@dataclass(frozen=True)
class ResultItem:
    status: ReconStatus
    rule: str  # hash | tuple | aggregate | none
    ledger_ids: tuple[int, ...] = ()
    custody_ids: tuple[int, ...] = ()
    detail: dict = field(default_factory=dict)


def _within(a: Decimal, b: Decimal, cfg: ReconConfig) -> bool:
    return abs(a - b) <= cfg.tolerance_abs + abs(b) * cfg.tolerance_rel


def _same_direction(a: Decimal, b: Decimal) -> bool:
    return (a >= 0) == (b >= 0)


def reconcile(
    ledger: list[LedgerView], custody: list[CustodyView], cfg: ReconConfig
) -> list[ResultItem]:
    ledger = sorted(ledger, key=lambda item: (item.occurred_at, item.id))
    custody = sorted(custody, key=lambda item: (item.occurred_at, item.id))
    results: list[ResultItem] = []
    open_ledger: dict[int, LedgerView] = {item.id: item for item in ledger}
    open_custody: dict[int, CustodyView] = {item.id: item for item in custody}

    # --- 0. Дубликаты строк выписки ---------------------------------------
    seen_hash: dict[str, int] = {}
    seen_signature: dict[tuple, int] = {}
    for entry in custody:
        if entry.external_tx_hash:
            key = entry.external_tx_hash
            if key in seen_hash:
                results.append(ResultItem(
                    ReconStatus.DUPLICATE_SUSPECT, "hash", (), (entry.id,),
                    {"reason": "повтор external_tx_hash в выписке",
                     "duplicate_of_entry": seen_hash[key]},
                ))
                open_custody.pop(entry.id)
                continue
            seen_hash[key] = entry.id
        else:
            signature = (entry.asset, str(entry.amount), entry.occurred_at.isoformat())
            if signature in seen_signature:
                results.append(ResultItem(
                    ReconStatus.DUPLICATE_SUSPECT, "tuple", (), (entry.id,),
                    {"reason": "повтор (актив, сумма, дата) в выписке",
                     "duplicate_of_entry": seen_signature[signature]},
                ))
                open_custody.pop(entry.id)
                continue
            seen_signature[signature] = entry.id

    # --- 1. Точный матч по хэшу --------------------------------------------
    ledger_by_hash = {item.tx_hash: item.id for item in ledger}
    for entry in list(open_custody.values()):
        if not entry.external_tx_hash:
            continue
        ledger_id = ledger_by_hash.get(entry.external_tx_hash)
        if ledger_id is None or ledger_id not in open_ledger:
            continue
        tx = open_ledger.pop(ledger_id)
        open_custody.pop(entry.id)
        if _within(entry.amount, tx.amount, cfg):
            results.append(ResultItem(
                ReconStatus.MATCHED, "hash", (tx.id,), (entry.id,),
                {"tx_hash": tx.tx_hash},
            ))
        else:
            results.append(ResultItem(
                ReconStatus.AMOUNT_MISMATCH, "hash", (tx.id,), (entry.id,),
                {"tx_hash": tx.tx_hash,
                 "ledger_amount": str(tx.amount),
                 "custody_amount": str(entry.amount),
                 "delta": str(entry.amount - tx.amount)},
            ))

    # --- 2. Матч по кортежу --------------------------------------------------
    for entry in sorted(open_custody.values(), key=lambda e: (e.occurred_at, e.id)):
        candidates = [
            tx for tx in open_ledger.values()
            if tx.asset == entry.asset
            and _same_direction(tx.amount, entry.amount)
            and abs(tx.occurred_at - entry.occurred_at) <= cfg.date_window
            and _within(entry.amount, tx.amount, cfg)
        ]
        if candidates:
            best = min(candidates, key=lambda tx: (
                abs(entry.amount - tx.amount),
                abs(tx.occurred_at - entry.occurred_at),
                tx.id,
            ))
            open_ledger.pop(best.id)
            open_custody.pop(entry.id)
            results.append(ResultItem(
                ReconStatus.MATCHED, "tuple", (best.id,), (entry.id,),
                {"amount_delta": str(entry.amount - best.amount),
                 "date_delta_hours": abs(
                     best.occurred_at - entry.occurred_at
                 ).total_seconds() / 3600},
            ))

    # --- 2b. Очевидные пары с расхождением суммы или даты ---------------------
    for entry in sorted(open_custody.values(), key=lambda e: (e.occurred_at, e.id)):
        if entry.maybe_aggregate:
            continue  # агрегаты разбирает правило 3
        in_window = [
            tx for tx in open_ledger.values()
            if tx.asset == entry.asset
            and _same_direction(tx.amount, entry.amount)
            and abs(tx.occurred_at - entry.occurred_at) <= cfg.date_window
        ]
        if len(in_window) == 1:
            tx = in_window[0]
            open_ledger.pop(tx.id)
            open_custody.pop(entry.id)
            results.append(ResultItem(
                ReconStatus.AMOUNT_MISMATCH, "tuple", (tx.id,), (entry.id,),
                {"reason": "единственный кандидат в окне, сумма вне допуска",
                 "ledger_amount": str(tx.amount),
                 "custody_amount": str(entry.amount),
                 "delta": str(entry.amount - tx.amount)},
            ))
            continue
        amount_matches = [
            tx for tx in open_ledger.values()
            if tx.asset == entry.asset
            and _same_direction(tx.amount, entry.amount)
            and _within(entry.amount, tx.amount, cfg)
        ]
        if len(amount_matches) == 1:
            tx = amount_matches[0]
            open_ledger.pop(tx.id)
            open_custody.pop(entry.id)
            results.append(ResultItem(
                ReconStatus.DATE_MISMATCH, "tuple", (tx.id,), (entry.id,),
                {"reason": "сумма сходится, дата вне окна",
                 "ledger_at": tx.occurred_at.isoformat(),
                 "custody_at": entry.occurred_at.isoformat(),
                 "date_delta_hours": abs(
                     tx.occurred_at - entry.occurred_at
                 ).total_seconds() / 3600},
            ))

    # --- 3. Агрегатный матчинг ------------------------------------------------
    # Одна строка выписки ↔ N транзакций.
    for entry in sorted(open_custody.values(), key=lambda e: (e.occurred_at, e.id)):
        candidates = sorted(
            (tx for tx in open_ledger.values()
             if tx.asset == entry.asset
             and _same_direction(tx.amount, entry.amount)
             and abs(tx.occurred_at - entry.occurred_at) <= cfg.date_window),
            key=lambda tx: (tx.occurred_at, tx.id),
        )
        if len(candidates) < 2:
            continue
        total = sum((tx.amount for tx in candidates), Decimal(0))
        ledger_ids = tuple(tx.id for tx in candidates)
        if _within(entry.amount, total, cfg):
            for tx_id in ledger_ids:
                open_ledger.pop(tx_id)
            open_custody.pop(entry.id)
            if cfg.accept_aggregates:
                results.append(ResultItem(
                    ReconStatus.MATCHED_AGGREGATE, "aggregate", ledger_ids, (entry.id,),
                    {"transactions": len(ledger_ids), "total": str(total)},
                ))
            else:
                results.append(ResultItem(
                    ReconStatus.MANUAL, "aggregate", ledger_ids, (entry.id,),
                    {"reason": "агрегат найден, авто-принятие отключено",
                     "transactions": len(ledger_ids), "total": str(total)},
                ))
        elif entry.maybe_aggregate:
            # Частичное покрытие агрегата: фиксируем расхождение с дельтой.
            for tx_id in ledger_ids:
                open_ledger.pop(tx_id)
            open_custody.pop(entry.id)
            results.append(ResultItem(
                ReconStatus.AMOUNT_MISMATCH, "aggregate", ledger_ids, (entry.id,),
                {"reason": "агрегат покрыт частично",
                 "custody_amount": str(entry.amount),
                 "chain_total": str(total),
                 "delta": str(entry.amount - total),
                 "transactions": len(ledger_ids)},
            ))

    # N строк выписки ↔ одна транзакция (обратный агрегат).
    for tx in sorted(open_ledger.values(), key=lambda item: (item.occurred_at, item.id)):
        group = sorted(
            (e for e in open_custody.values()
             if e.asset == tx.asset
             and _same_direction(e.amount, tx.amount)
             and abs(e.occurred_at - tx.occurred_at) <= cfg.date_window),
            key=lambda e: (e.occurred_at, e.id),
        )
        if len(group) < 2:
            continue
        total = sum((e.amount for e in group), Decimal(0))
        if _within(total, tx.amount, cfg):
            custody_ids = tuple(e.id for e in group)
            for entry in group:
                open_custody.pop(entry.id)
            open_ledger.pop(tx.id)
            status = (
                ReconStatus.MATCHED_AGGREGATE if cfg.accept_aggregates
                else ReconStatus.MANUAL
            )
            results.append(ResultItem(
                status, "aggregate", (tx.id,), custody_ids,
                {"entries": len(custody_ids), "total": str(total)},
            ))

    # --- 4. Остатки — расхождения (уходят в очередь ручного разбора) -----------
    for entry in sorted(open_custody.values(), key=lambda e: (e.occurred_at, e.id)):
        results.append(ResultItem(
            ReconStatus.MISSING_ON_CHAIN, "none", (), (entry.id,),
            {"reason": "строка выписки не найдена в цепочке",
             "custody_amount": str(entry.amount), "asset": entry.asset},
        ))
    for tx in sorted(open_ledger.values(), key=lambda item: (item.occurred_at, item.id)):
        results.append(ResultItem(
            ReconStatus.MISSING_IN_CUSTODY, "none", (tx.id,), (),
            {"reason": "транзакция цепочки отсутствует в выписке",
             "ledger_amount": str(tx.amount), "asset": tx.asset,
             "tx_hash": tx.tx_hash},
        ))
    return results
