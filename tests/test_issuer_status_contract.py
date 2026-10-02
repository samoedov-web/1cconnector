from datetime import datetime, timezone

import pytest

from connector.models import (
    ComplianceDecision,
    IssuerRiskCheck,
    IssuerRiskStatus,
    RegistrySnapshot,
)
from connector.services.operation_service import (
    ComplianceGateError,
    OperationService,
)


async def _base_gate_records(session, operation, risk_status: str):
    session.add(
        ComplianceDecision(
            operation_id=operation.id,
            decision_type="aml",
            decision="approved",
            decided_by="test",
            decided_at=datetime.now(timezone.utc),
        )
    )

    session.add(
        RegistrySnapshot(
            operation_id=operation.id,
            subject_reference="subject",
            source="test",
            status_result="active",
            snapshot_payload={},
            checksum="a" * 64,
            observed_at=datetime.now(timezone.utc),
        )
    )

    session.add(
        IssuerRiskCheck(
            operation_id=operation.id,
            asset_id=operation.asset_id,
            contract_address="0xTEST",
            network_id=operation.network_id,
            risk_status=risk_status,
            provider_health="AVAILABLE",
            source="test",
            payload={},
            checksum="b" * 64,
            checked_at=datetime.now(timezone.utc),
        )
    )

    await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        IssuerRiskStatus.OK.value,
        "normal",  # legacy compatibility
    ],
)
async def test_gate_accepts_canonical_ok_and_legacy_normal(
    async_session,
    sample_operation,
    status,
):
    await _base_gate_records(
        async_session,
        sample_operation,
        status,
    )

    await OperationService(async_session)._verify_compliance_gate(
        sample_operation
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        IssuerRiskStatus.FREEZE.value,
        IssuerRiskStatus.BLACKLIST.value,
        IssuerRiskStatus.UNKNOWN.value,
        "frozen",         # legacy compatibility
        "freeze_warning", # legacy unknown semantics: fail closed
    ],
)
async def test_gate_rejects_non_ok_issuer_statuses(
    async_session,
    sample_operation,
    status,
):
    await _base_gate_records(
        async_session,
        sample_operation,
        status,
    )

    with pytest.raises(ComplianceGateError):
        await OperationService(async_session)._verify_compliance_gate(
            sample_operation
        )
