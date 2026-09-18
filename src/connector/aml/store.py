"""Сохранение результатов AML-скрининга (фаза 1 aml-спеки).

История append-only: каждая проверка — новая запись (адрес мог сменить
скор между проверками, обе записи — доказательная база). Последний
результат по адресу — last_screening().
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.aml.base import AmlResult
from connector.hashing import canonical_sha256
from connector.models import AmlScreening


async def store_screening(
    session: AsyncSession, source_id: str, result: AmlResult
) -> AmlScreening:
    row = AmlScreening(
        source_id=source_id,
        network=result.network,
        address=result.address,
        risk_score=result.risk_score,
        categories=list(result.categories),
        provider_ref=result.provider_ref,
        raw=result.raw,
        checksum=canonical_sha256(result.raw),
        screened_at=result.screened_at,
    )
    session.add(row)
    await session.flush()
    return row


async def last_screening(
    session: AsyncSession, network: str, address: str
) -> AmlScreening | None:
    return await session.scalar(
        select(AmlScreening)
        .where(AmlScreening.network == network, AmlScreening.address == address)
        .order_by(AmlScreening.screened_at.desc(), AmlScreening.id.desc())
        .limit(1)
    )
