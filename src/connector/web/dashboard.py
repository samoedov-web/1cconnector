"""Сводка для экрана мониторинга (п. 10 ТЗ: health индексеров, отставание)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.db import get_session
from connector.models import (
    Alert,
    Match,
    MatchState,
    Network,
    OnecDocument,
    Transaction,
    Wallet,
)
from connector.security import CurrentUser, require_reader

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
) -> dict:
    tx_by_status = dict(
        (
            await session.execute(
                select(Transaction.status, func.count()).group_by(Transaction.status)
            )
        ).all()
    )
    docs_by_status = dict(
        (
            await session.execute(
                select(OnecDocument.status, func.count()).group_by(OnecDocument.status)
            )
        ).all()
    )
    pending_matches = await session.scalar(
        select(func.count(Match.id)).where(Match.state == MatchState.PENDING)
    )

    networks = (await session.execute(select(Network))).scalars().all()
    network_rows = []
    for n in networks:
        wallets = (
            (
                await session.execute(
                    select(Wallet).where(Wallet.network_id == n.id, Wallet.enabled)
                )
            )
            .scalars()
            .all()
        )
        scanned = [w.last_scanned_at for w in wallets if w.last_scanned_at is not None]
        network_rows.append(
            {
                "code": n.code,
                "name": n.name,
                "enabled": n.enabled,
                "finality_depth": n.finality_depth,
                "wallets": len(wallets),
                # Отставание индексера видно по возрасту последнего скана.
                "last_scanned_at": max(scanned).isoformat() if scanned else None,
            }
        )

    alerts = (
        (
            await session.execute(
                select(Alert).order_by(Alert.at.desc(), Alert.id.desc()).limit(10)
            )
        )
        .scalars()
        .all()
    )
    return {
        "networks": network_rows,
        "transactions": {k.value: v for k, v in tx_by_status.items()},
        "pending_matches": pending_matches or 0,
        "onec_documents": {k.value: v for k, v in docs_by_status.items()},
        "alerts": [
            {
                "at": a.at.isoformat() if a.at else None,
                "severity": a.severity,
                "title": a.title,
                "details": a.details,
                "sent": a.sent,
            }
            for a in alerts
        ],
    }
