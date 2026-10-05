import pytest

from connector.adapters.stage3.adapters import RegistryResult
from connector.services.compliance_check_service import ComplianceCheckService


class InjectedRegistryAdapter:
    async def check(self, ref: str) -> RegistryResult:
        from datetime import datetime, timezone

        return RegistryResult(
            subject_ref=ref,
            status="active",
            registry_type="DIGITAL_DEPOSITORY",
            record_number="INJECTED-1",
            registry_entry_id="INJECTED-1",
            checked_at=datetime.now(timezone.utc),
            raw_payload={"provider": "injected"},
            checksum="c" * 64,
            source="injected-registry",
        )


@pytest.mark.asyncio
async def test_registry_provider_defaults_from_settings(
    async_session,
):
    service = ComplianceCheckService(async_session)

    assert service.registry_adapter.__class__.__name__ == "MockRegistryAdapter"


@pytest.mark.asyncio
async def test_registry_provider_can_be_injected(
    async_session,
    sample_operation,
):
    adapter = InjectedRegistryAdapter()
    service = ComplianceCheckService(
        async_session,
        registry_adapter=adapter,
    )

    assert service.registry_adapter is adapter

    snapshot = await service.run_registry_check(
        sample_operation,
        "subject-injected",
    )
    await async_session.flush()

    assert snapshot.source == "injected-registry"
    assert snapshot.registry_entry_id == "INJECTED-1"
    assert snapshot.registry_type == "DIGITAL_DEPOSITORY"
