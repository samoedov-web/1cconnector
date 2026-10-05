from datetime import datetime, timezone

import pytest

from connector.adapters.stage3.adapters import (
    MockRegistryAdapter,
    RegistryResult,
)
from connector.services.compliance_check_service import (
    ComplianceCheckService,
)


@pytest.mark.asyncio
async def test_mock_registry_exposes_normalized_operational_metadata():
    result = await MockRegistryAdapter().check("7700000000")

    assert result.status == "active"
    assert result.registry_type == "DIGITAL_CURRENCY_EXCHANGE"
    assert result.registry_entry_id == "MOCK-12345"
    assert result.record_number == "MOCK-12345"
    assert result.admission_mode == "GENERAL"

    assert result.source == "mock_cbr"
    assert result.source_url is not None

    assert result.checked_at.tzinfo is not None
    assert result.raw_payload["registry_entry_id"] == "MOCK-12345"


@pytest.mark.asyncio
async def test_registry_check_persists_normalized_provider_result(
    async_session,
    sample_operation,
):
    service = ComplianceCheckService(async_session)

    snapshot = await service.run_registry_check(
        sample_operation,
        "7700000000",
    )
    await async_session.flush()

    assert snapshot.id is not None
    assert snapshot.subject_reference == "7700000000"
    assert snapshot.source == "mock_cbr"
    assert snapshot.status_result == "active"

    assert snapshot.registry_type == "DIGITAL_CURRENCY_EXCHANGE"
    assert snapshot.registry_entry_id == "MOCK-12345"
    assert snapshot.admission_mode == "GENERAL"

    assert snapshot.source_url is not None
    assert snapshot.legal_basis == "mock-registry-fixture"

    assert isinstance(snapshot.snapshot_payload, dict)
    assert snapshot.snapshot_payload["status"] == "active"

    assert (
        snapshot.regulatory_version_id
        == sample_operation.regulatory_version_id
    )


@pytest.mark.asyncio
async def test_registry_entry_id_falls_back_to_legacy_record_number(
    async_session,
    sample_operation,
):
    service = ComplianceCheckService(async_session)

    checked_at = datetime.now(timezone.utc)

    class LegacyCompatibleAdapter:
        async def check(self, ref: str) -> RegistryResult:
            return RegistryResult(
                subject_ref=ref,
                status="active",
                registry_type="DIGITAL_DEPOSITORY",
                record_number="LEGACY-ENTRY-42",
                registry_entry_id=None,
                checked_at=checked_at,
                raw_payload={
                    "subject": ref,
                    "status": "active",
                },
                checksum="a" * 64,
                source="legacy-compatible-provider",
            )

    service.registry_adapter = LegacyCompatibleAdapter()

    snapshot = await service.run_registry_check(
        sample_operation,
        "subject-42",
    )
    await async_session.flush()

    assert snapshot.registry_entry_id == "LEGACY-ENTRY-42"
    assert snapshot.registry_type == "DIGITAL_DEPOSITORY"
    assert snapshot.source == "legacy-compatible-provider"
