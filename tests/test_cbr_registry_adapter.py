from datetime import timezone

import httpx
import pytest

from connector.adapters.stage3.adapters import RegistryResult
from connector.adapters.stage3.cbr_registry import (
    CBRRawResponse,
    CBRRegistryAdapter,
    CBRRegistryRequest,
    CBRRegistryTransport,
    CBRRegistryType,
)


class FrozenTestParser:
    """Test-only parser; not a claim about the actual CBR data format."""

    def parse(
        self,
        request: CBRRegistryRequest,
        response: CBRRawResponse,
    ) -> RegistryResult:
        return RegistryResult(
            subject_ref=request.subject_ref,
            status="active",
            registry_type=request.registry_type.value,
            record_number="TEST-CBR-001",
            registry_entry_id="TEST-CBR-001",
            admission_mode="GENERAL",
            checked_at=response.checked_at,
            raw_payload={
                "fixture": response.content.decode(),
                "content_type": response.content_type,
            },
            checksum=response.checksum,
            source="CBR",
            source_url=response.source_url,
            legal_basis="test-only parser fixture",
        )


def mock_transport(
    body: bytes = b'{"fixture":"cbr"}',
    content_type: str = "application/json",
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": content_type},
            request=request,
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_cbr_transport_preserves_raw_evidence():
    transport = CBRRegistryTransport(
        base_url="https://cbr.test",
        transport=mock_transport(b"raw-cbr-evidence"),
    )

    try:
        result = await transport.fetch(
            CBRRegistryType.DIGITAL_CURRENCY_EXCHANGE,
            "/registry/export",
        )
    finally:
        await transport.aclose()

    assert result.registry_type == CBRRegistryType.DIGITAL_CURRENCY_EXCHANGE
    assert result.content == b"raw-cbr-evidence"
    assert result.source_url == "https://cbr.test/registry/export"
    assert len(result.checksum) == 64
    assert result.checked_at.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_cbr_adapter_normalizes_only_through_explicit_parser():
    adapter = CBRRegistryAdapter(
        registry_type=CBRRegistryType.DIGITAL_DEPOSITORY,
        source_path="/registry/export",
        base_url="https://cbr.test",
        parser=FrozenTestParser(),
        transport=mock_transport(),
    )

    try:
        result = await adapter.check("7700000000")
    finally:
        await adapter.aclose()

    assert result.subject_ref == "7700000000"
    assert result.registry_type == "DIGITAL_DEPOSITORY"
    assert result.registry_entry_id == "TEST-CBR-001"
    assert result.source == "CBR"
    assert result.source_url == "https://cbr.test/registry/export"
    assert len(result.checksum) == 64


@pytest.mark.asyncio
async def test_cbr_adapter_fails_closed_without_verified_parser():
    adapter = CBRRegistryAdapter(
        registry_type=CBRRegistryType.INFORMATION_SYSTEM_OPERATOR,
        source_path="/registry/export",
        base_url="https://cbr.test",
        transport=mock_transport(),
    )

    try:
        with pytest.raises(
            RuntimeError,
            match="parser is not configured",
        ):
            await adapter.check("subject")
    finally:
        await adapter.aclose()


def test_cbr_adapter_requires_explicit_registry_type():
    with pytest.raises(ValueError, match="registry_type"):
        CBRRegistryAdapter.from_config(
            source_path="/registry/export",
        )


def test_cbr_adapter_requires_explicit_source_path():
    with pytest.raises(ValueError, match="source_path"):
        CBRRegistryAdapter.from_config(
            registry_type="DIGITAL_CURRENCY_EXCHANGE",
        )


@pytest.mark.parametrize(
    "registry_type",
    [
        CBRRegistryType.DIGITAL_CURRENCY_EXCHANGE,
        CBRRegistryType.DIGITAL_DEPOSITORY,
        CBRRegistryType.INFORMATION_SYSTEM_OPERATOR,
    ],
)
def test_all_regulatory_registry_types_are_explicit(registry_type):
    assert registry_type.value
