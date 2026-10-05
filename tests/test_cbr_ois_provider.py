from pathlib import Path

import httpx
import pytest

from connector.adapters.stage3.adapters import (
    get_registry_adapter,
)
from connector.adapters.stage3.cbr_registry import (
    CBROISRegistryAdapter,
    CBR_OIS_SOURCE_PATH,
)
from connector.sources.registry import (
    create_registry_source,
    registry_source_codes,
)


FIXTURE = Path(
    "tests/fixtures/cbr/native/list_ois_2026-10-04.xlsx"
)


def frozen_ois_transport():
    content = FIXTURE.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == CBR_OIS_SOURCE_PATH

        return httpx.Response(
            200,
            content=content,
            headers={
                "content-type": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                )
            },
            request=request,
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_legacy_factory_lazily_registers_cbr_ois():
    adapter = get_registry_adapter("cbr-ois")

    try:
        assert isinstance(adapter, CBROISRegistryAdapter)
        assert "cbr-ois" in registry_source_codes()
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_canonical_factory_creates_cbr_ois_after_registration():
    # Trigger lazy registration through compatibility entry point first.
    adapter = get_registry_adapter("cbr-ois")

    canonical = create_registry_source(
        "cbr-ois",
        transport=frozen_ois_transport(),
        base_url="https://cbr.test",
    )

    try:
        assert isinstance(adapter, CBROISRegistryAdapter)
        assert isinstance(canonical, CBROISRegistryAdapter)
    finally:
        await adapter.aclose()
        await canonical.aclose()


@pytest.mark.asyncio
async def test_cbr_ois_provider_reads_official_frozen_workbook():
    # Ensure registration.
    get_registry_adapter("cbr-ois")

    adapter = create_registry_source(
        "cbr-ois",
        transport=frozen_ois_transport(),
        base_url="https://cbr.test",
    )

    try:
        result = await adapter.check("7707083893")
    finally:
        await adapter.aclose()

    assert result.status == "active"
    assert result.registry_type == "INFORMATION_SYSTEM_OPERATOR"

    assert result.subject_ref == "7707083893"
    assert result.raw_payload["record"]["ogrn"] == "1027700132195"

    assert result.registry_entry_id is None
    assert result.admission_mode is None

    assert result.source == "CBR"
    assert result.source_url == (
        "https://cbr.test"
        "/vfs/finmarkets/files/supervision/list_ois.xlsx"
    )

    assert result.raw_payload["registry_as_of"][:10] == "2026-10-04"


@pytest.mark.asyncio
async def test_cbr_ois_provider_returns_not_found_fail_closed():
    get_registry_adapter("cbr-ois")

    adapter = create_registry_source(
        "cbr-ois",
        transport=frozen_ois_transport(),
        base_url="https://cbr.test",
    )

    try:
        result = await adapter.check("0000000000")
    finally:
        await adapter.aclose()

    assert result.status == "not_found"
    assert result.raw_payload["matched"] is False


@pytest.mark.asyncio
async def test_cbr_ois_is_selectable_by_configured_provider(
    async_session,
    monkeypatch,
):
    from connector.config import settings
    from connector.services.compliance_check_service import (
        ComplianceCheckService,
    )

    monkeypatch.setattr(
        settings,
        "registry_source_id",
        "cbr-ois",
    )

    service = ComplianceCheckService(async_session)

    try:
        assert isinstance(
            service.registry_adapter,
            CBROISRegistryAdapter,
        )
    finally:
        await service.registry_adapter.aclose()
