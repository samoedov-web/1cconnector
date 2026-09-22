"""Налоговый регистр по ст. 282.3 НК РФ (Stage 7).

Учет доходов/расходов от операций с цифровыми активами.
Методология: ФИФО (First-In-First-Out) для расчета себестоимости.
Переоценка не влияет на налоговую базу до момента выбытия.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from connector.models import Lot, DisposalLine, Transaction, Asset, Direction, TxStatus

@dataclass
class TaxLot:
    asset_symbol: str
    quantity: Decimal
    unit_cost_rub: Decimal
    total_cost_rub: Decimal
    acquired_at: datetime
    remaining: Decimal

@dataclass
class TaxDisposal:
    asset_symbol: str
    quantity: Decimal
    income_rub: Decimal
    cost_rub: Decimal
    profit_rub: Decimal  # income - cost
    disposal_date: datetime

class TaxRegistryService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_open_lots(self, asset_id: int) -> List[TaxLot]:
        """Получает открытые партии (остаток > 0) для актива."""
        result = await self.session.execute(
            select(Lot)
            .where(Lot.asset_id == asset_id)
            .where(Lot.remaining > 0)
            .order_by(Lot.acquired_at)  # ФИФО: сначала старые
        )
        lots = result.scalars().all()
        return [
            TaxLot(
                asset_symbol=lot.asset.symbol,
                quantity=lot.quantity,
                unit_cost_rub=lot.unit_cost_rub,
                total_cost_rub=lot.quantity * lot.unit_cost_rub,
                acquired_at=lot.acquired_at,
                remaining=lot.remaining
            )
            for lot in lots
        ]

    async def calculate_disposal_tax(self, transaction: Transaction) -> TaxDisposal:
        """Рассчитывает налоговый результат выбытия (ФИФО)."""
        if transaction.direction != Direction.OUT:
            raise ValueError("Транзакция не является выбытием")
        
        asset = transaction.asset
        quantity_to_dispose = transaction.amount
        income_rub = transaction.amount * transaction.asset_to_rub  # Предполагается наличие снимка курса
        
        # Распределяем выбытие по партиям ФИФО
        open_lots = await self.get_open_lots(asset.id)
        total_cost_rub = Decimal(0)
        remaining_qty = quantity_to_dispose
        
        for lot in open_lots:
            if remaining_qty <= 0:
                break
            
            qty_from_lot = min(remaining_qty, lot.remaining)
            cost_from_lot = qty_from_lot * lot.unit_cost_rub
            total_cost_rub += cost_from_lot
            remaining_qty -= qty_from_lot
        
        profit_rub = income_rub - total_cost_rub
        
        return TaxDisposal(
            asset_symbol=asset.symbol,
            quantity=quantity_to_dispose,
            income_rub=income_rub,
            cost_rub=total_cost_rub,
            profit_rub=profit_rub,
            disposal_date=transaction.block_time
        )
