"""Переоценка цифровой валюты на отчётную дату (v1, п. 6 ТЗ).

По каждому активу с ненулевым остатком партий:
  1. снимок курса на дату переоценки (purpose="revaluation") — история
     курсов неизменяема, промежуточные значения и источник сохраняются;
  2. рыночная оценка остатка и разница с ФИФО-себестоимостью;
  3. дельта к предыдущей переоценке (1С сторнирует прошлую и начисляет новую);
  4. проект документа «Переоценка цифровой валюты» в очередь обмена.

Идемпотентность: одна переоценка на (актив, дата) — повторный запуск
на ту же дату не создаёт ни дублей реестра, ни документов.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.models import (
    Asset,
    Lot,
    OnecDocType,
    OnecDocument,
    Revaluation,
)
from connector.rates.service import RateService

# Валюта промежуточной оценки актива (стейблкоины ВЭД); при появлении
# контрактов в других валютах — вынести в настройку актива.
VALUATION_CURRENCY = "USD"


def revaluation_key(asset: Asset, as_of: datetime) -> str:
    return f"revaluation:{asset.network_id}:{asset.symbol}:{as_of.date().isoformat()}"


async def run_revaluation(
    session: AsyncSession, rates: RateService, as_of: datetime
) -> list[Revaluation]:
    """Провести переоценку всех активов на дату; вернуть созданные записи."""
    created: list[Revaluation] = []
    assets = (await session.execute(select(Asset))).scalars().all()
    for asset in assets:
        existing = await session.scalar(
            select(Revaluation.id).where(
                Revaluation.asset_id == asset.id, Revaluation.as_of == as_of
            )
        )
        if existing is not None:
            continue

        quantity, book_cost = (
            await session.execute(
                select(
                    func.coalesce(func.sum(Lot.remaining), 0),
                    func.coalesce(func.sum(Lot.remaining * Lot.unit_cost_rub), 0),
                ).where(Lot.asset_id == asset.id, Lot.remaining > 0)
            )
        ).one()
        quantity = Decimal(str(quantity))
        book_cost = Decimal(str(book_cost))
        if quantity <= 0:
            continue

        snapshot = await rates.snapshot(
            asset_symbol=asset.symbol,
            contract_currency=VALUATION_CURRENCY,
            as_of=as_of,
            purpose="revaluation",
        )
        session.add(snapshot)
        await session.flush()

        market = quantity * snapshot.asset_to_rub
        difference = market - book_cost
        previous = await session.scalar(
            select(Revaluation)
            .where(Revaluation.asset_id == asset.id, Revaluation.as_of < as_of)
            .order_by(Revaluation.as_of.desc())
            .limit(1)
        )
        delta = difference - (previous.difference_rub if previous else Decimal(0))

        revaluation = Revaluation(
            asset_id=asset.id,
            as_of=as_of,
            quantity=quantity,
            book_cost_rub=book_cost,
            market_rub=market,
            difference_rub=difference,
            delta_rub=delta,
            rate_snapshot_id=snapshot.id,
        )
        session.add(revaluation)
        session.add(
            OnecDocument(
                idempotency_key=revaluation_key(asset, as_of),
                doc_type=OnecDocType.REVALUATION,
                payload={
                    "doc_type": OnecDocType.REVALUATION.value,
                    "asset": asset.symbol,
                    "as_of": as_of.isoformat(),
                    "quantity": str(quantity),
                    "book_cost_rub": str(book_cost),
                    "market_rub": str(market),
                    "difference_rub": str(difference),
                    "delta_rub": str(delta),
                    "previous_difference_rub": str(
                        previous.difference_rub if previous else Decimal(0)
                    ),
                    "rate": {
                        "asset_to_rub": str(snapshot.asset_to_rub),
                        "source": snapshot.source_primary,
                        "as_of": snapshot.as_of.isoformat(),
                    },
                },
            )
        )
        created.append(revaluation)
    await session.flush()
    return created
