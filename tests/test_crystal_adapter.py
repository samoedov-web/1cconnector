"""Фаза 5 aml-спеки: CrystalAdapter — маппинг ответа, конфигурация, ошибки.

Транспорт подменный (httpx.MockTransport): тесты фиксируют предполагаемую
схему API (см. вопрос в разделе 7 спеки) — при сверке с API-докой тарифа
клиента расхождения всплывут здесь.
"""

from urllib.parse import parse_qsl

import httpx
import pytest

from connector.aml.crystal_adapter import CrystalAdapter
from connector.sources.registry import create_aml_source

RESPONSE = {
    "data": {
        "riskscore": 0.42,
        "signals": {"mixer": 0.4, "exchange": 0.1, "miner": 0},
        "fingerprint": 987654,
    }
}


def transport(seen: list, response: dict = RESPONSE, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=response)

    return httpx.MockTransport(handler)


async def test_maps_riskscore_signals_and_keeps_raw():
    seen: list[httpx.Request] = []
    adapter = CrystalAdapter(api_key="k-123", transport=transport(seen))

    result = await adapter.screen_address("tron", "TAddr")

    assert result.risk_score == 42  # 0.42 → шкала 0–100 порогов регламента
    assert result.categories == ("mixer", "exchange")  # вес 0 отброшен
    assert result.provider_ref == "987654"
    assert result.raw == RESPONSE  # как получен — журнал неизменяемости
    assert result.network == "tron"
    assert result.address == "TAddr"

    [request] = seen
    assert request.url.path == "/monitor/one-time-check"
    assert request.headers["X-Auth-Apikey"] == "k-123"
    body = dict(parse_qsl(request.content.decode()))
    assert body == {"address": "TAddr", "currency": "trx"}


async def test_ethereum_maps_to_eth_currency():
    seen: list[httpx.Request] = []
    adapter = CrystalAdapter(api_key="k", transport=transport(seen))
    await adapter.screen_address("ethereum", "0xAbc")
    body = dict(parse_qsl(seen[0].content.decode()))
    assert body["currency"] == "eth"


async def test_unknown_network_rejected():
    adapter = CrystalAdapter(api_key="k", transport=transport([]))
    with pytest.raises(ValueError, match="сеть solana не поддержана"):
        await adapter.screen_address("solana", "addr")


async def test_http_error_propagates_to_flow():
    """5xx — исключение наружу: слой потока оставит pending_aml + алерт."""
    adapter = CrystalAdapter(api_key="k", transport=transport([], status=503))
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.screen_address("tron", "TAddr")


async def test_missing_riskscore_is_loud():
    adapter = CrystalAdapter(
        api_key="k", transport=transport([], response={"data": {}})
    )
    with pytest.raises(ValueError, match="нет riskscore"):
        await adapter.screen_address("tron", "TAddr")


def test_requires_api_key():
    with pytest.raises(ValueError, match="API-ключ"):
        CrystalAdapter(api_key="")


def test_registry_builds_from_shared_config():
    """Смена провайдера — конфигурацией: общие ключи других провайдеров
    (fixtures_path мока) фабрика игнорирует."""
    adapter = create_aml_source(
        "crystal",
        api_key="k",
        base_url="",
        fixtures_path="fixtures/aml.json",
        transport=transport([]),
    )
    assert isinstance(adapter, CrystalAdapter)
    assert adapter.meta().family == "aml"


async def test_score_clamped_to_scale():
    adapter = CrystalAdapter(
        api_key="k",
        transport=transport([], response={"data": {"riskscore": 1.7}}),
    )
    result = await adapter.screen_address("tron", "TAddr")
    assert result.risk_score == 100
