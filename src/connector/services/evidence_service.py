"""Evidence Vault — хранение неизменяемых доказательств (Stage 4).

Принципы:
- Append-only: новые записи только добавляются, старые не меняются.
- SHA-256 checksum для каждого payload.
- Привязка к Operation через EvidenceLink.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from connector.models import EvidenceLink, Operation

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def compute_checksum(payload: Dict[str, Any]) -> str:
    """Вычисляет SHA-256 канонизированного JSON."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

class EvidenceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_evidence(
        self,
        operation: Operation,
        evidence_type: str,
        source: str,
        source_reference: str,
        raw_payload: Dict[str, Any],
        adapter_version: str = "1.0",
        observed_at: Optional[datetime] = None,
    ) -> EvidenceLink:
        """Добавляет запись доказательства (append-only)."""
        checksum = compute_checksum(raw_payload)
        canonical_payload = json.dumps(raw_payload, sort_keys=True, ensure_ascii=False)
        
        evidence = EvidenceLink(
            operation_id=operation.id,
            evidence_type=evidence_type,
            source=source,
            source_reference=source_reference,
            raw_payload=raw_payload,
            canonical_payload=canonical_payload,
            sha256_checksum=checksum,
            observed_at=observed_at or utcnow(),
            adapter_version=adapter_version,
        )
        self.session.add(evidence)
        return evidence

    async def get_evidence(self, evidence_id: int) -> Optional[EvidenceLink]:
        """Получает запись по ID."""
        result = await self.session.execute(
            select(EvidenceLink).where(EvidenceLink.id == evidence_id)
        )
        return result.scalar_one_or_none()

    async def list_by_operation(self, operation: Operation) -> list[EvidenceLink]:
        """Список всех доказательств для операции."""
        result = await self.session.execute(
            select(EvidenceLink)
            .where(EvidenceLink.operation_id == operation.id)
            .order_by(EvidenceLink.created_at)
        )
        return list(result.scalars().all())
