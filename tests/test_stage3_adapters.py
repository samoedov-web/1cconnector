import pytest
import asyncio
from connector.adapters.stage3.adapters import MockRegistryAdapter, MockIssuerRiskAdapter

@pytest.mark.asyncio
async def test_registry_mock():
    adapter = MockRegistryAdapter()
    res = await adapter.check("test_ref")
    assert res.status == "active"
    assert res.checksum is not None

@pytest.mark.asyncio
async def test_issuer_mock():
    adapter = MockIssuerRiskAdapter()
    res = await adapter.check("USDT")
    assert res.risk_status == "normal"
    assert res.asset_symbol == "USDT"
