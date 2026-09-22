from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from connector.models import Operation, OperationState, ComplianceDecision, RegulatoryVersion, RegistrySnapshot, IssuerRiskCheck

class OperationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def transition(self, operation: Operation, new_state: OperationState) -> None:
        # Простая реализация перехода состояния без сложной валидации графа для Stage 2
        # В продакшене здесь должна быть полная проверка допустимых переходов
        operation.state = new_state
        await self.session.flush()

    async def verify_compliance_gate(self, operation: Operation) -> bool:
        # Проверка наличия одобренных решений по AML, Registry, Issuer
        # Упрощенная логика для Stage 2
        decisions = (await self.session.execute(
            select(ComplianceDecision).where(ComplianceDecision.operation_id == operation.id)
        )).scalars().all()
        
        required_types = {"aml", "registry", "issuer"}
        approved_types = {d.decision_type for d in decisions if d.decision == "approved"}
        
        return required_types.issubset(approved_types)

    async def mark_payment_pending(self, operation: Operation) -> None:
        if operation.state != OperationState.APPROVED:
            raise ValueError("Only APPROVED operations can be marked as SENT")
        await self.transition(operation, OperationState.SENT)
