"""Обмен данными с 1С: Предприятие (Stage 7).

Протокол:
- Идемпотентность по ключу (network:tx_hash:doc_type).
- Выгрузка документов: Receipt, Disposal, Revaluation, Fee.
- Статусы: draft → exported → acked.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from connector.models import OnecDocument, OnecDocType, OnecDocStatus, Transaction

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class OneCExchangeService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_receipt_doc(self, transaction: Transaction, payload: Dict[str, Any]) -> OnecDocument:
        """Создает документ «Поступление цифровой валюты»."""
        key = f"{transaction.network.code}:{transaction.tx_hash}:{OnecDocType.RECEIPT.value}"
        doc = OnecDocument(
            idempotency_key=key,
            doc_type=OnecDocType.RECEIPT,
            transaction_id=transaction.id,
            payload=payload,
            status=OnecDocStatus.DRAFT
        )
        self.session.add(doc)
        return doc

    async def create_disposal_doc(self, transaction: Transaction, payload: Dict[str, Any]) -> OnecDocument:
        """Создает документ «Выбытие цифровой валюты»."""
        key = f"{transaction.network.code}:{transaction.tx_hash}:{OnecDocType.DISPOSAL.value}"
        doc = OnecDocument(
            idempotency_key=key,
            doc_type=OnecDocType.DISPOSAL,
            transaction_id=transaction.id,
            payload=payload,
            status=OnecDocStatus.DRAFT
        )
        self.session.add(doc)
        return doc

    async def create_revaluation_doc(self, asset_id: int, as_of: datetime, payload: Dict[str, Any]) -> OnecDocument:
        """Создает документ «Переоценка цифровой валюты»."""
        key = f"asset:{asset_id}:reval:{as_of.isoformat()}"
        doc = OnecDocument(
            idempotency_key=key,
            doc_type=OnecDocType.REVALUATION,
            payload=payload,
            status=OnecDocStatus.DRAFT
        )
        self.session.add(doc)
        return doc

    async def get_pending_docs(self) -> List[OnecDocument]:
        """Получает список документов, ожидающих выгрузки."""
        result = await self.session.execute(
            select(OnecDocument)
            .where(OnecDocument.status == OnecDocStatus.DRAFT)
            .order_by(OnecDocument.created_at)
        )
        return list(result.scalars().all())

    async def mark_exported(self, doc: OnecDocument) -> None:
        """Помечает документ как выгруженный."""
        doc.status = OnecDocStatus.EXPORTED
        doc.exported_at = utcnow()

    async def mark_acked(self, doc: OnecDocument, onec_ref: str) -> None:
        """Подтверждает создание документа в 1С."""
        doc.status = OnecDocStatus.ACKNOWLEDGED
        doc.acked_at = utcnow()
        doc.onec_ref = onec_ref

    async def sync_from_1c(self, docs_data: List[Dict[str, Any]]) -> int:
        """Обрабатывает ответы от 1С (GUID созданных документов)."""
        count = 0
        for item in docs_data:
            doc_id = item.get("id")
            onec_ref = item.get("onec_ref")
            if doc_id and onec_ref:
                doc = await self.session.get(OnecDocument, doc_id)
                if doc and doc.status == OnecDocStatus.EXPORTED:
                    await self.mark_acked(doc, onec_ref)
                    count += 1
        return count
