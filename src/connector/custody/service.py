"""Сервис депозитарной сверки."""
from __future__ import annotations
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from connector.models import CustodyStatement, CustodyEntry, Transaction, TxStatus, Direction, ReconciliationRun, ReconciliationResult
from connector.custody.engine import reconcile, ReconConfig, LedgerView, CustodyView, ReconStatus
from datetime import datetime

class CustodyReconciliationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def run_reconciliation(self, source_id: str, period_from: datetime, period_to: datetime, config: ReconConfig) -> ReconciliationRun:
        # Загрузка данных из БД
        # (Упрощенно: берем финальные транзакции и выписки)
        txs = await self.session.execute(select(Transaction).where(Transaction.status == TxStatus.FINAL, Transaction.block_time.between(period_from, period_to)))
        entries = await self.session.execute(select(CustodyEntry).join(CustodyStatement).where(CustodyStatement.source_id == source_id, CustodyStatement.period_from <= period_to, CustodyStatement.period_to >= period_from))
        
        ledger_views = [LedgerView(id=t.id, tx_hash=t.tx_hash, asset=t.asset.symbol, amount=t.amount if t.direction == Direction.IN else -t.amount, occurred_at=t.block_time) for t in txs.scalars().all()]
        custody_views = [CustodyView(id=e.id, entry_id=e.entry_id, asset=e.asset, amount=e.amount, occurred_at=e.occurred_at, external_tx_hash=e.external_tx_hash) for e in entries.scalars().all()]

        results = reconcile(ledger_views, custody_views, config)
        
        run = ReconciliationRun(source_id=source_id, period_from=period_from, period_to=period_to, config=config.as_dict())
        self.session.add(run)
        await self.session.flush()
        
        for res in results:
            self.session.add(ReconciliationResult(run_id=run.id, status=res.status.value, rule=res.rule, ledger_tx_ids=list(res.ledger_ids), custody_entry_ids=list(res.custody_ids), detail=res.detail))
        
        return run

    async def get_runs(self, source_id: Optional[str] = None) -> List[ReconciliationRun]:
        stmt = select(ReconciliationRun)
        if source_id: stmt = stmt.where(ReconciliationRun.source_id == source_id)
        res = await self.session.execute(stmt.order_by(ReconciliationRun.started_at.desc()))
        return list(res.scalars().all())
