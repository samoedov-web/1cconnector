"""Operation Service — управление жизненным циклом операций (Stage 2 + Stage 5).

Единая точка изменения состояний через transition().
Compliance Gate: AML=OK AND Registry=OK AND IssuerRisk=OK перед APPROVED.
"""
from __future__ import annotations
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from connector.models import (
    Operation, OperationState, ComplianceDecision, RegistrySnapshot, 
    IssuerRiskCheck, EvidenceLink, RegulatoryVersion
)
from datetime import datetime, timezone

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class OperationStateError(Exception):
    """Недопустимый переход состояния."""
    pass

class ComplianceGateError(Exception):
    """Операция не прошла шлюз комплаенса."""
    pass

# Граф допустимых переходов состояния
ALLOWED_TRANSITIONS = {
    OperationState.DRAFT: [OperationState.COMPLIANCE_PENDING, OperationState.REJECTED],
    OperationState.COMPLIANCE_PENDING: [
        OperationState.REGISTRY_CHECKED, 
        OperationState.AML_CHECKED, 
        OperationState.ISSUER_CHECKED,
        OperationState.REJECTED
    ],
    OperationState.REGISTRY_CHECKED: [OperationState.AML_CHECKED, OperationState.ISSUER_CHECKED, OperationState.REJECTED],
    OperationState.AML_CHECKED: [OperationState.REGISTRY_CHECKED, OperationState.ISSUER_CHECKED, OperationState.APPROVED, OperationState.REJECTED],
    OperationState.ISSUER_CHECKED: [OperationState.REGISTRY_CHECKED, OperationState.AML_CHECKED, OperationState.APPROVED, OperationState.REJECTED],
    OperationState.APPROVED: [OperationState.SENT, OperationState.REJECTED, OperationState.EXPIRED],
    OperationState.SENT: [OperationState.DETECTED, OperationState.REJECTED],
    OperationState.DETECTED: [OperationState.FINAL],
    OperationState.FINAL: [OperationState.MATCHED],
    OperationState.MATCHED: [OperationState.ACCOUNTED],
    OperationState.ACCOUNTED: [OperationState.REPORTED],
    OperationState.REPORTED: [OperationState.CLOSED],
    # Исключения
    OperationState.REVIEW: [OperationState.APPROVED, OperationState.REJECTED, OperationState.MANUAL_RECONCILIATION],
    OperationState.REJECTED: [],  # Тупик
    OperationState.EXPIRED: [],   # Тупик
    OperationState.ORPHANED: [],
    OperationState.PROVIDER_DEGRADED: [OperationState.COMPLIANCE_PENDING],
    OperationState.ISSUER_FREEZE: [OperationState.REJECTED, OperationState.REVIEW],
    OperationState.MANUAL_RECONCILIATION: [OperationState.APPROVED, OperationState.REJECTED],
}

class OperationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_operation(self, op_id: int) -> Optional[Operation]:
        result = await self.session.execute(select(Operation).where(Operation.id == op_id))
        return result.scalar_one_or_none()

    async def transition(self, operation: Operation, new_state: OperationState, actor: str = "system") -> Operation:
        """Выполняет переход состояния с проверкой графа и Compliance Gate."""
        current_state = operation.state
        
        # Проверка допустимости перехода
        if new_state not in ALLOWED_TRANSITIONS.get(current_state, []):
            raise OperationStateError(f"Переход {current_state} → {new_state} запрещён.")

        # Compliance Gate перед APPROVED
        if new_state == OperationState.APPROVED:
            await self._verify_compliance_gate(operation)

        operation.state = new_state
        # Здесь можно добавить запись в AuditLog (будущий этап)
        
        return operation

    async def _verify_compliance_gate(self, operation: Operation) -> None:
        """Проверяет: AML=OK AND Registry=OK AND IssuerRisk=OK."""
        # 1. AML Check
        aml_decision = await self._get_latest_decision(operation, "aml")
        if not aml_decision or aml_decision.decision != "approved":
            raise ComplianceGateError(f"AML check failed for operation {operation.id}")

        # 2. Registry Check
        registry_snapshot = await self._get_latest_snapshot(operation, "registry")
        if not registry_snapshot or registry_snapshot.status_result not in ["active", "licensed"]:
            raise ComplianceGateError(f"Registry check failed for operation {operation.id}")

        # 3. Issuer Risk Check
        issuer_check = await self._get_latest_issuer_check(operation)
        if not issuer_check or issuer_check.risk_status == "frozen":
            raise ComplianceGateError(f"Issuer risk check failed for operation {operation.id}")

    async def _get_latest_decision(self, operation: Operation, decision_type: str) -> Optional[ComplianceDecision]:
        result = await self.session.execute(
            select(ComplianceDecision)
            .where(ComplianceDecision.operation_id == operation.id)
            .where(ComplianceDecision.decision_type == decision_type)
            .order_by(ComplianceDecision.decided_at.desc())
        )
        return result.scalar_one_or_none()

    async def _get_latest_snapshot(self, operation: Operation, source_type: str) -> Optional[RegistrySnapshot]:
        # Упрощенно: берем любой снимок для операции. В реальности можно фильтровать по source.
        result = await self.session.execute(
            select(RegistrySnapshot)
            .where(RegistrySnapshot.operation_id == operation.id)
            .order_by(RegistrySnapshot.observed_at.desc())
        )
        return result.scalar_one_or_none()

    async def _get_latest_issuer_check(self, operation: Operation) -> Optional[IssuerRiskCheck]:
        result = await self.session.execute(
            select(IssuerRiskCheck)
            .where(IssuerRiskCheck.operation_id == operation.id)
            .order_by(IssuerRiskCheck.checked_at.desc())
        )
        return result.scalar_one_or_none()

    async def mark_payment_pending(self, operation: Operation, actor: str) -> Operation:
        """Переводит APPROVED → SENT (семантика: ждем внешнего платежа)."""
        return await self.transition(operation, OperationState.SENT, actor=actor)
