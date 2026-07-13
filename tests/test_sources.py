"""Фаза 1 depository-спеки: DataSource/ChainSource, реестр источников.

Регрессионное требование фазы — весь существующий набор тестов проходит
без изменений; здесь — только новая поверхность.
"""

import pytest

from connector.indexer.base import ChainAdapter
from connector.indexer.ethereum import EthereumAdapter
from connector.indexer.tron import TronAdapter
from connector.sources.base import ADAPTER_API_VERSION, DataSource, HealthStatus
from connector.sources.registry import chain_source_codes, create_chain_source


def test_registry_contains_both_networks():
    assert chain_source_codes() == ["ethereum", "tron"]


def test_factory_builds_configured_adapters():
    tron = create_chain_source("tron", "https://api.trongrid.io", api_key="k",
                               source_name="tron-1")
    assert isinstance(tron, TronAdapter)
    assert tron.source_name == "tron-1"

    eth = create_chain_source("ethereum", "https://rpc.example")
    assert isinstance(eth, EthereumAdapter)
    assert eth.source_name == "eth-rpc"  # дефолт как у прямого конструктора


def test_unknown_network_is_explicit_error():
    with pytest.raises(LookupError, match="polygon"):
        create_chain_source("polygon", "https://x")


def test_chain_adapter_is_data_source_with_meta():
    adapter = TronAdapter("https://api.trongrid.io", source_name="tron-1")
    assert isinstance(adapter, DataSource)
    meta = adapter.meta()
    assert meta.family == "chain"
    assert meta.id == "tron-1"
    assert meta.adapter_api_version == ADAPTER_API_VERSION


class HealthyStub(ChainAdapter):
    network_code = "stub"
    source_name = "stub"

    @classmethod
    def from_config(cls, url, api_key="", source_name=""):
        return cls()

    async def latest_block(self):
        return 12345

    async def fetch_transfers(self, address, token_contracts, since=None, from_block=None):
        return []

    async def get_transaction_block(self, tx_hash):
        return None

    async def get_transaction_fee(self, tx_hash):
        raise NotImplementedError


class BrokenStub(HealthyStub):
    async def latest_block(self):
        raise ConnectionError("нода недоступна")


async def test_health_ok_and_down_without_raising():
    ok = await HealthyStub().health()
    assert ok == HealthStatus("ok", "height=12345")
    assert ok.is_ok

    down = await BrokenStub().health()
    assert down.status == "down"
    assert "нода недоступна" in down.detail
