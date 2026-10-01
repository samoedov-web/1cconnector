"""Отчёт «Акт сверки: блокчейн ↔ депозитарий» (п. 4.6 спеки, фаза 5).

Сводка по статусам + построчная детализация с хэшами и ссылками на
первичку обеих сторон. Экспорт — через существующий модуль обновляемых
шаблонов: HTML (печать/PDF браузером — конвенция продукта), XLSX, JSON.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.models import (
    CustodyEntry,
    ReconciliationRun,
    Transaction,
)
from connector.reports.render import _autofit, _workbook_bytes

STATUS_RU = {
    "matched": "совпало",
    "matched_aggregate": "совпало (агрегат)",
    "missing_in_custody": "нет в выписке",
    "missing_on_chain": "нет в цепочке",
    "amount_mismatch": "расхождение суммы",
    "date_mismatch": "расхождение даты",
    "duplicate_suspect": "подозрение на дубль",
    "manual": "разобрано вручную",
}
DISCREPANCY_STATUSES = (
    "missing_in_custody", "missing_on_chain", "amount_mismatch",
    "date_mismatch", "duplicate_suspect",
)


async def reconciliation_report_data(
    session: AsyncSession, run_id: int
) -> dict | None:
    run = await session.scalar(
        select(ReconciliationRun)
        .options(selectinload(ReconciliationRun.results))
        .where(ReconciliationRun.id == run_id)
    )
    if run is None:
        return None

    ledger_ids = {tx_id for r in run.results for tx_id in r.ledger_tx_ids}
    entry_ids = {e_id for r in run.results for e_id in r.custody_entry_ids}
    txs = {
        tx.id: tx
        for tx in (
            await session.execute(
                select(Transaction)
                .options(selectinload(Transaction.asset))
                .where(Transaction.id.in_(ledger_ids or {0}))
            )
        ).scalars()
    }
    entries = {
        entry.id: entry
        for entry in (
            await session.execute(
                select(CustodyEntry).where(CustodyEntry.id.in_(entry_ids or {0}))
            )
        ).scalars()
    }

    rows = []
    summary: dict[str, dict] = {}
    for result in sorted(run.results, key=lambda r: (r.status, r.id)):
        chain_side = [
            {
                "tx_hash": txs[tx_id].tx_hash,
                "amount": str(txs[tx_id].amount),
                "asset": txs[tx_id].asset.symbol,
                "at": txs[tx_id].block_time.isoformat(),
            }
            for tx_id in result.ledger_tx_ids
            if tx_id in txs
        ]
        custody_side = [
            {
                "entry_id": entries[e_id].entry_id,
                "amount": str(entries[e_id].amount),
                "asset": entries[e_id].asset,
                "at": entries[e_id].occurred_at.isoformat(),
                "external_tx_hash": entries[e_id].external_tx_hash,
            }
            for e_id in result.custody_entry_ids
            if e_id in entries
        ]
        involved = (
            sum((abs(Decimal(item["amount"])) for item in custody_side), Decimal(0))
            or sum((abs(Decimal(item["amount"])) for item in chain_side), Decimal(0))
        )
        bucket = summary.setdefault(
            result.status, {"count": 0, "amount": Decimal(0)}
        )
        bucket["count"] += 1
        bucket["amount"] += involved
        rows.append(
            {
                "result_id": result.id,
                "status": result.status,
                "status_ru": STATUS_RU.get(result.status, result.status),
                "rule": result.rule,
                "chain": chain_side,
                "custody": custody_side,
                "detail": result.detail,
            }
        )

    return {
        "report": "custody_reconciliation",
        "generated_at": datetime.now().astimezone().isoformat(),
        "run": {
            "id": run.id,
            "source_id": run.source_id,
            "period_from": run.period_from.isoformat(),
            "period_to": run.period_to.isoformat(),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "stale": run.stale,
            "config": run.config,
        },
        "summary": [
            {
                "status": status,
                "status_ru": STATUS_RU.get(status, status),
                "count": bucket["count"],
                "amount": str(bucket["amount"]),
            }
            for status, bucket in sorted(summary.items())
        ],
        "rows": rows,
        "discrepancies": sum(
            bucket["count"]
            for status, bucket in summary.items()
            if status in DISCREPANCY_STATUSES
        ),
    }


def reconciliation_xlsx(data: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Сверка"
    run = data["run"]
    ws.append(["Акт сверки: блокчейн ↔ депозитарий"])
    ws.append([f"Источник: {run['source_id']}",
               f"Период: {run['period_from']} — {run['period_to']}",
               "РЕЗУЛЬТАТ УСТАРЕЛ (реорг)" if run["stale"] else ""])
    ws.append([])
    ws.append(["Сводка"])
    ws.append(["Статус", "Строк", "Сумма (по модулю)"])
    for item in data["summary"]:
        ws.append([item["status_ru"], item["count"], item["amount"]])
    ws.append([])
    ws.append(["Детализация"])
    ws.append(["Статус", "Правило", "Цепочка (хэши)", "Сумма цепочки",
               "Выписка (строки)", "Сумма выписки", "Пояснение"])
    for row in data["rows"]:
        ws.append([
            row["status_ru"],
            row["rule"],
            "; ".join(item["tx_hash"] for item in row["chain"]),
            "; ".join(item["amount"] for item in row["chain"]),
            "; ".join(item["entry_id"] for item in row["custody"]),
            "; ".join(item["amount"] for item in row["custody"]),
            row["detail"].get("reason", ""),
        ])
    _autofit(ws)
    return _workbook_bytes(wb)
