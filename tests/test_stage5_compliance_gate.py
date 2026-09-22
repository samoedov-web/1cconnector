import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from connector.models import Operation, OperationState, ComplianceDecision, RegistrySnapshot, IssuerRiskCheck
from connector.services.operation_service import OperationService, ComplianceGateError, OperationStateError
from datetime import datetime, timezone

@pytest.mark.asyncio
async def test_transition_approved_success(async_session, sample_operation):
    """Успешный проход Compliance Gate."""
    service = OperationService(async_session)
    
    # Создаем положительные решения
    aml = ComplianceDecision(operation_id=sample_operation.id, decision_type="aml", decision="approved", decided_at=datetime.now(timezone.utc))
    registry = RegistrySnapshot(operation_id=sample_operation.id, status_result="active", observed_at=datetime.now(timezone.utc))
    issuer = IssuerRiskCheck(operation_id=sample_operation.id, risk_status="normal", checked_at=datetime.now(timezone.utc))
    
    async_session.add_all([aml, registry, issuer])
    await async_session.flush()
    
    # Пробуем перейти в APPROVED
    op = await service.transition(sample_operation, OperationState.APPROVED)
    assert op.state == OperationState.APPROVED

@pytest.mark.asyncio
async def test_transition_approved_fail_aml(async_session, sample_operation):
    """Провал из-за AML."""
    service = OperationService(async_session)
    
    # AML rejected
    aml = ComplianceDecision(operation_id=sample_operation.id, decision_type="aml", decision="rejected", decided_at=datetime.now(timezone.utc))
    registry = RegistrySnapshot(operation_id=sample_operation.id, status_result="active", observed_at=datetime.now(timezone.utc))
    issuer = IssuerRiskCheck(operation_id=sample_operation.id, risk_status="normal", checked_at=datetime.now(timezone.utc))
    
    async_session.add_all([aml, registry, issuer])
    await async_session.flush()
    
    with pytest.raises(ComplianceGateError):
        await service.transition(sample_operation, OperationState.APPROVED)

@pytest.mark.asyncio
async def test_invalid_transition(async_session, sample_operation):
    """Недопустимый переход состояния."""
    service = OperationService(async_session)
    sample_operation.state = OperationState.DRAFT
    
    with pytest.raises(OperationStateError):
        await service.transition(sample_operation, OperationState.MATCHED)  # Пропуск всех этапов
