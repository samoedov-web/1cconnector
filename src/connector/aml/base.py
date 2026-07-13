"""Интерфейс AmlAdapter — семейство aml (specs/aml-adapter.md, фаза 1).

Провайдер AML-скрининга адресов (Crystal, Chainalysis, TRM, Шард) —
источник данных в том же реестре, что адаптеры сетей и депозитариев.
Контракт нейтральный: risk_score 0–100 и категории — как отдаёт провайдер,
пороги решений (approved/review/rejected) — конфигурация клиента, к
адаптеру отношения не имеют (фаза 2).
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar

from connector.sources.base import DataSource


@dataclass(frozen=True)
class AmlResult:
    """Результат скрининга одного адреса.

    raw — ответ провайдера как получен (журнал неизменяемости: доказательная
    база осмотрительности для банка и Росфинмониторинга).
    """

    network: str
    address: str
    risk_score: int  # 0–100
    categories: tuple[str, ...] = ()  # sanctions | darknet | mixer | ...
    provider_ref: str = ""  # id проверки у провайдера
    raw: dict = field(default_factory=dict)
    screened_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class AmlAdapter(DataSource):
    """Read-only скрининг адресов у одного провайдера."""

    family: ClassVar[str] = "aml"

    @classmethod
    @abstractmethod
    def from_config(cls, **config) -> "AmlAdapter":
        """Единообразная фабрика для реестра источников."""

    @abstractmethod
    async def screen_address(self, network: str, address: str) -> AmlResult:
        """Проверить адрес; сетевые ошибки пробрасываются вызывающему
        (решение о повторе/деградации — на слое потока, фаза 2)."""
