import pytest

from connector.services.compliance_check_service import ComplianceCheckService


@pytest.mark.asyncio
async def test_issuer_check_persists_provider_health(
    async_session,
    sample_operation,
    sample_asset,
):
    check = await ComplianceCheckService(
        async_session
    ).run_issuer_check(
        sample_operation,
        sample_asset,
    )

    await async_session.flush()

    assert check.risk_status == "normal"
    assert check.provider_health == "AVAILABLE"
