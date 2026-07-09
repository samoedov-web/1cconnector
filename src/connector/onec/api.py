"""HTTP API обмена с 1С (п. 8 ТЗ).

Протокол под фоновое задание синхронизации на стороне 1С:
  1. GET  /api/v1/onec/documents?status=draft  — забрать пачку проектов документов;
  2. POST /api/v1/onec/documents/ack           — подтвердить создание в 1С
     (idempotency_key + GUID созданного документа).

Идемпотентность: ключ по хэшу транзакции; повторная выгрузка и повторный
ack не создают дублей и не ломают состояние. Аутентификация — токен в
заголовке X-Exchange-Token.
"""

from __future__ import annotations

import hmac
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.config import settings
from connector.db import get_session
from connector.models import AuditLog, OnecDocStatus, OnecDocument, utcnow

router = APIRouter(prefix="/api/v1/onec", tags=["1c-exchange"])


def require_exchange_token(x_exchange_token: str = Header(default="")) -> None:
    if not settings.onec_exchange_token or not hmac.compare_digest(
        x_exchange_token, settings.onec_exchange_token
    ):
        raise HTTPException(status_code=401, detail="Неверный токен обмена")


class DocumentOut(BaseModel):
    idempotency_key: str
    doc_type: str
    payload: dict
    created_at: datetime


class AckIn(BaseModel):
    idempotency_key: str
    onec_ref: str  # GUID созданного в 1С документа


@router.get("/documents", response_model=list[DocumentOut])
async def pull_documents(
    status: OnecDocStatus = Query(default=OnecDocStatus.DRAFT),
    limit: int = Query(default=100, le=1000),
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_exchange_token),
) -> list[DocumentOut]:
    docs = (
        (
            await session.execute(
                select(OnecDocument)
                .where(OnecDocument.status == status)
                .order_by(OnecDocument.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    now = utcnow()
    for doc in docs:
        if doc.status == OnecDocStatus.DRAFT:
            doc.status = OnecDocStatus.EXPORTED
            doc.exported_at = now
    await session.commit()
    return [
        DocumentOut(
            idempotency_key=d.idempotency_key,
            doc_type=d.doc_type.value,
            payload=d.payload,
            created_at=d.created_at,
        )
        for d in docs
    ]


@router.post("/documents/ack")
async def ack_documents(
    acks: list[AckIn],
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_exchange_token),
) -> dict:
    confirmed = 0
    for ack in acks:
        doc = await session.scalar(
            select(OnecDocument).where(OnecDocument.idempotency_key == ack.idempotency_key)
        )
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Неизвестный ключ {ack.idempotency_key}")
        if doc.status == OnecDocStatus.ACKNOWLEDGED:
            continue  # повторный ack — no-op
        doc.status = OnecDocStatus.ACKNOWLEDGED
        doc.onec_ref = ack.onec_ref
        doc.acked_at = utcnow()
        session.add(
            AuditLog(
                actor="1c-exchange",
                action="document_acknowledged",
                entity="onec_document",
                entity_id=str(doc.id),
                details={"onec_ref": ack.onec_ref},
            )
        )
        confirmed += 1
    await session.commit()
    return {"confirmed": confirmed}
