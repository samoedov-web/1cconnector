"""Движок сверки блокчейн ↔ депозитарий (п. 4.5 спеки)."""
from __future__ import annotations
import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Tuple, Optional

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
    id: int
    tx_hash: str
    asset: str
    amount: Decimal
    occurred_at: datetime

@dataclass(frozen=True)
class CustodyView:
    id: int
    entry_id: str
    asset: str
    amount: Decimal
    occurred_at: datetime
    external_tx_hash: Optional[str] = None
    maybe_aggregate: bool = False

@dataclass(frozen=True)
class ReconConfig:
    tolerance_abs: Decimal = Decimal(0)
    tolerance_rel: Decimal = Decimal(0)
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
    rule: str
    ledger_ids: Tuple[int, ...] = ()
    custody_ids: Tuple[int, ...] = ()
    detail: dict = field(default_factory=dict)

def _within(a: Decimal, b: Decimal, cfg: ReconConfig) -> bool:
    return abs(a - b) <= cfg.tolerance_abs + abs(b) * cfg.tolerance_rel

def _same_direction(a: Decimal, b: Decimal) -> bool:
    return (a >= 0) == (b >= 0)

def reconcile(ledger: List[LedgerView], custody: List[CustodyView], cfg: ReconConfig) -> List[ResultItem]:
    # Сортировка для детерминизма
    ledger = sorted(ledger, key=lambda x: (x.occurred_at, x.id))
    custody = sorted(custody, key=lambda x: (x.occurred_at, x.id))
    
    results: List[ResultItem] = []
    open_ledger: Dict[int, LedgerView] = {x.id: x for x in ledger}
    open_custody: Dict[int, CustodyView] = {x.id: x for x in custody}

    # 0. Дубликаты
    seen_hash: Dict[str, int] = {}
    for entry in list(open_custody.values()):
        if entry.external_tx_hash:
            if entry.external_tx_hash in seen_hash:
                results.append(ResultItem(ReconStatus.DUPLICATE_SUSPECT, "hash", (), (entry.id,), {"dup_of": seen_hash[entry.external_tx_hash]}))
                del open_custody[entry.id]
            else:
                seen_hash[entry.external_tx_hash] = entry.id

    # 1. Exact Hash Match
    ledger_by_hash = {tx.tx_hash: tx.id for tx in open_ledger.values()}
    for entry in list(open_custody.values()):
        if entry.external_tx_hash and entry.external_tx_hash in ledger_by_hash:
            tx_id = ledger_by_hash.pop(entry.external_tx_hash)
            tx = open_ledger.pop(tx_id)
            del open_custody[entry.id]
            
            if _within(entry.amount, tx.amount, cfg):
                results.append(ResultItem(ReconStatus.MATCHED, "hash", (tx.id,), (entry.id,), {"tx_hash": tx.tx_hash}))
            else:
                results.append(ResultItem(ReconStatus.AMOUNT_MISMATCH, "hash", (tx.id,), (entry.id,), {"delta": str(entry.amount - tx.amount)}))

    # 2. Tuple Match (Asset, Amount, Date)
    for entry in list(open_custody.values()):
        candidates = [
            tx for tx in open_ledger.values()
            if tx.asset == entry.asset and _same_direction(tx.amount, entry.amount)
            and abs(tx.occurred_at - entry.occurred_at) <= cfg.date_window
            and _within(entry.amount, tx.amount, cfg)
        ]
        if candidates:
            best = min(candidates, key=lambda t: (abs(t.amount - entry.amount), abs(t.occurred_at - entry.occurred_at), t.id))
            del open_ledger[best.id]
            del open_custody[entry.id]
            results.append(ResultItem(ReconStatus.MATCHED, "tuple", (best.id,), (entry.id,), {}))

    # 3. Aggregates (N txs -> 1 entry)
    if cfg.accept_aggregates:
        for entry in list(open_custody.values()):
            if not entry.maybe_aggregate: continue
            candidates = sorted(
                [tx for tx in open_ledger.values() if tx.asset == entry.asset and _same_direction(tx.amount, entry.amount)],
                key=lambda t: t.occurred_at
            )
            total = sum((t.amount for t in candidates), Decimal(0))
            if _within(total, entry.amount, cfg) and len(candidates) > 1:
                ids = tuple(t.id for t in candidates)
                for tid in ids: del open_ledger[tid]
                del open_custody[entry.id]
                results.append(ResultItem(ReconStatus.MATCHED_AGGREGATE, "aggregate", ids, (entry.id,), {"count": len(ids)}))

    # 4. Остатки -> Manual/Missing
    for entry in open_custody.values():
        results.append(ResultItem(ReconStatus.MISSING_ON_CHAIN, "none", (), (entry.id,), {}))
    for tx in open_ledger.values():
        results.append(ResultItem(ReconStatus.MISSING_IN_CUSTODY, "none", (tx.id,), (), {}))

    return results
