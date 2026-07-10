"""Запуск сверки против БД: сбор данных, маппинг активов, персист результатов.

Идемпотентность (п. 4.5): повторный запуск с тем же (source_id, период,
конфигурация) возвращает существующий незастаревший ReconciliationRun.
После реорга затронутые запуски помечаются stale (сценарий 6.10) —
следующий запуск создаёт новый консистентный результат.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from connector.custody.reconciliation import (
    CustodyView,
    LedgerView,
    ReconConfig,
    reconcile,
)
from connector.hashing import canonical_sha256
from connector.models import (
    CustodyAssetMapping,
    CustodyStatement,
    Direction,
    ReconciliationResult,
    ReconciliationRun,
    Transaction,
    TxStatus,
)


def statement_granularity(raw_payload: dict) -> str:
    """Гранулярность из сырого payload выписки (JSON или CSV-формат мока)."""
    if "granularity" in raw_payload:
        return raw_payload.get("granularity") or "per_tx"
    rows = raw_payload.get("rows") or []
    if rows and rows[0].get("granularity"):
        return rows[0]["granularity"]
    return "per_tx"


async def _ledger_views(
    session: AsyncSession, period_from: datetime, period_to: datetime
) -> list[LedgerView]:
    txs = (
        (
            await session.execute(
                select(Transaction)
                .options(selectinload(Transaction.asset))
                .where(
                    Transaction.status == TxStatus.FINAL,
                    Transaction.block_time >= period_from,
                    Transaction.block_time <= period_to,
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        LedgerView(
            id=tx.id,
            tx_hash=tx.tx_hash,
            asset=tx.asset.symbol,
            amount=tx.amount if tx.direction == Direction.IN else -tx.amount,
            occurred_at=tx.block_time,
        )
        for tx in txs
    ]


async def _custody_views(
    session: AsyncSession, source_id: str, period_from: datetime, period_to: datetime
) -> list[CustodyView]:
    statements = (
        (
            await session.execute(
                select(CustodyStatement)
                .options(selectinload(CustodyStatement.entries))
                .where(
                    CustodyStatement.source_id == source_id,
                    CustodyStatement.period_from <= period_to,
                    CustodyStatement.period_to >= period_from,
                )
            )
        )
        .scalars()
        .all()
    )
    mapping = {
        row.custody_ticker: row.asset_symbol
        for row in (
            await session.execute(
                select(CustodyAssetMapping).where(
                    CustodyAssetMapping.source_id == source_id
                )
            )
        ).scalars()
    }
    views: list[CustodyView] = []
    for statement in statements:
        aggregate_hint = statement_granularity(statement.raw_payload) in (
            "aggregated",
            "mixed",
        )
        for entry in statement.entries:
            views.append(
                CustodyView(
                    id=entry.id,
                    entry_id=entry.entry_id,
                    asset=mapping.get(entry.asset, entry.asset),
                    amount=entry.amount,
                    occurred_at=entry.occurred_at,
                    external_tx_hash=entry.external_tx_hash,
                    maybe_aggregate=aggregate_hint,
                )
            )
    return views


async def run_reconciliation(
    session: AsyncSession,
    source_id: str,
    period_from: datetime,
    period_to: datetime,
    config: ReconConfig | None = None,
) -> ReconciliationRun:
    config = config or ReconConfig()
    config_hash = canonical_sha256(config.as_dict())

    existing = await session.scalar(
        select(ReconciliationRun)
        .options(selectinload(ReconciliationRun.results))
        .where(
            ReconciliationRun.source_id == source_id,
            ReconciliationRun.period_from == period_from,
            ReconciliationRun.period_to == period_to,
            ReconciliationRun.config_hash == config_hash,
            ReconciliationRun.stale.is_(False),
        )
    )
    if existing is not None:
        return existing  # идемпотентность повторного запуска

    ledger = await _ledger_views(session, period_from, period_to)
    custody = await _custody_views(session, source_id, period_from, period_to)
    items = reconcile(ledger, custody, config)

    run = ReconciliationRun(
        source_id=source_id,
        period_from=period_from,
        period_to=period_to,
        config=config.as_dict(),
        config_hash=config_hash,
    )
    session.add(run)
    await session.flush()
    for item in items:
        session.add(
            ReconciliationResult(
                run_id=run.id,
                status=item.status.value,
                rule=item.rule,
                ledger_tx_ids=list(item.ledger_ids),
                custody_entry_ids=list(item.custody_ids),
                detail=item.detail,
            )
        )
    await session.flush()
    await session.refresh(run, ["results"])
    return run


async def mark_stale_runs(session: AsyncSession) -> int:
    """Пометить устаревшими запуски, чьи транзакции потеряли финальность.

    Вызывается индексером после обработки реорга (сценарий 6.10):
    выписка выпущена, но chain-транзакция выпала из цепочки — прежний
    результат сверки недостоверен.
    """
    runs = (
        (
            await session.execute(
                select(ReconciliationRun)
                .options(selectinload(ReconciliationRun.results))
                .where(ReconciliationRun.stale.is_(False))
            )
        )
        .scalars()
        .all()
    )
    marked = 0
    for run in runs:
        tx_ids = {
            tx_id for result in run.results for tx_id in result.ledger_tx_ids
        }
        if not tx_ids:
            continue
        lost = await session.scalar(
            select(Transaction.id)
            .where(Transaction.id.in_(tx_ids), Transaction.status != TxStatus.FINAL)
            .limit(1)
        )
        if lost is not None:
            run.stale = True
            marked += 1
    await session.flush()
    return marked
