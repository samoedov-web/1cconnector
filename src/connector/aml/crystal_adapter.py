"""CrystalAdapter — скрининг адресов через Crystal Blockchain (фаза 5 aml-спеки).

Read-only: один POST на проверку адреса, ответ провайдера сохраняется
как получен (журнал неизменяемости — на слое store_screening). API-ключ —
в конфигурации клиента (CONNECTOR_AML_API_KEY), в коде не хранится.

ВНИМАНИЕ: эндпоинт и маппинг ответа — по публичной документации
Crystal Expert API; перед боевым подключением сверить с API-докой
тарифа клиента (вопрос в разделе 7 спеки):
- POST {base_url}/monitor/one-time-check, заголовок X-Auth-Apikey;
- параметры address + currency (наши коды сетей → валюты Crystal);
- ответ data.riskscore ∈ [0..1] → risk_score 0–100 (шкала порогов
  регламента), data.signals {категория: вес} → categories по убыванию
  веса, data.fingerprint → provider_ref.

Смена провайдера — конфигурацией (aml_source_id), не кодом.
"""

from __future__ import annotations

import httpx

from connector.aml.base import AmlAdapter, AmlResult
from connector.sources.base import HealthStatus
from connector.sources.registry import register_aml_source

# Коды сетей коннектора → идентификаторы валют Crystal.
NETWORK_CURRENCY = {"tron": "trx", "ethereum": "eth"}


@register_aml_source("crystal")
class CrystalAdapter(AmlAdapter):
    display_name = "Crystal Blockchain (Expert API)"
    source_name = "crystal"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://apiexpert.crystalblockchain.com",
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,  # для тестов
    ) -> None:
        if not api_key:
            raise ValueError(
                "Не задан API-ключ Crystal (CONNECTOR_AML_API_KEY) — "
                "укажите ключ из кабинета провайдера"
            )
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-Auth-Apikey": api_key},
            timeout=timeout_seconds,
            transport=transport,
        )

    @classmethod
    def from_config(cls, **config) -> "CrystalAdapter":
        # Лишние ключи (fixtures_path и т.п. от других провайдеров)
        # игнорируются — фабрика реестра передаёт общий конфиг.
        return cls(
            api_key=config.get("api_key", ""),
            base_url=config.get("base_url")
            or "https://apiexpert.crystalblockchain.com",
            timeout_seconds=float(config.get("timeout_seconds", 15)),
            transport=config.get("transport"),
        )

    async def screen_address(self, network: str, address: str) -> AmlResult:
        currency = NETWORK_CURRENCY.get(network)
        if currency is None:
            raise ValueError(f"Crystal: сеть {network} не поддержана адаптером")
        response = await self._client.post(
            "/monitor/one-time-check",
            data={"address": address, "currency": currency},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        riskscore = data.get("riskscore")
        if riskscore is None:
            raise ValueError(
                f"Crystal: в ответе нет riskscore для {address} — "
                "сверьте маппинг с API-докой тарифа"
            )
        signals = data.get("signals") or {}
        categories = tuple(
            name for name, weight in
            sorted(signals.items(), key=lambda item: item[1], reverse=True)
            if weight
        )
        return AmlResult(
            network=network,
            address=address,
            risk_score=max(0, min(100, round(float(riskscore) * 100))),
            categories=categories,
            provider_ref=str(data.get("fingerprint", "")),
            raw=payload,  # как получен — журнал неизменяемости
        )

    async def health(self) -> HealthStatus:
        try:
            response = await self._client.get("/", timeout=5)
        except Exception as exc:  # noqa: BLE001 — сеть внешняя
            return HealthStatus("down", str(exc))
        return HealthStatus("ok", f"http={response.status_code}")
