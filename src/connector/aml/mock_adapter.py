"""MockAmlAdapter — провайдер скрининга на фикстурах (фаза 1 aml-спеки).

Единственная реализация до подключения реального провайдера (Crystal —
фаза 5). Фикстура — JSON:

    {
      "default_score": 5,
      "addresses": {
        "TDirtyAddress...": {"risk_score": 85, "categories": ["sanctions"]},
        "TGreyAddress...":  {"risk_score": 50, "categories": ["mixer"]}
      }
    }

Неизвестный адрес получает default_score (чистый). Эмуляция задержки и
деградации — как у мок-депозитария, для тестов устойчивости потока.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from connector.aml.base import AmlAdapter, AmlResult
from connector.sources.base import HealthStatus
from connector.sources.registry import register_aml_source


@register_aml_source("mock-aml")
class MockAmlAdapter(AmlAdapter):
    display_name = "Мок AML-провайдер (фикстуры)"
    source_name = "mock-aml"

    def __init__(
        self,
        fixtures_path: str = "",
        latency_seconds: float = 0.0,
        health_override: str = "",  # "" | degraded | down
    ) -> None:
        self._path = Path(fixtures_path) if fixtures_path else None
        self._latency = latency_seconds
        self._health_override = health_override
        self._data: dict | None = None
        self._counter = 0

    @classmethod
    def from_config(cls, **config) -> "MockAmlAdapter":
        return cls(
            fixtures_path=config.get("fixtures_path", ""),
            latency_seconds=float(config.get("latency_seconds", 0)),
            health_override=config.get("health_override", ""),
        )

    def _load(self) -> dict:
        if self._data is None:
            if self._path is not None and self._path.is_file():
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            else:
                self._data = {"default_score": 0, "addresses": {}}
        return self._data

    async def screen_address(self, network: str, address: str) -> AmlResult:
        if self._health_override == "down":
            raise ConnectionError("AML-провайдер недоступен (эмуляция)")
        if self._latency:
            await asyncio.sleep(self._latency)
        data = self._load()
        entry = data.get("addresses", {}).get(address)
        self._counter += 1
        if entry is None:
            score, categories = int(data.get("default_score", 0)), ()
        else:
            score = int(entry["risk_score"])
            categories = tuple(entry.get("categories", []))
        return AmlResult(
            network=network,
            address=address,
            risk_score=score,
            categories=categories,
            provider_ref=f"mock-{self._counter}",
            raw={"provider": "mock", "address": address, "risk_score": score,
                 "categories": list(categories)},
        )

    async def health(self) -> HealthStatus:
        if self._health_override:
            return HealthStatus(self._health_override, "эмуляция сбоя (конфигурация мока)")
        known = len(self._load().get("addresses", {}))
        return HealthStatus("ok", f"known_addresses={known}")
