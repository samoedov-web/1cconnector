"""Абстракция источников данных (specs/depository-adapter.md, п. 4.1, фаза 1).

Общий предок двух семейств:

    DataSource (abstract)
    ├── chain   — адаптеры сетей (существующий контракт ChainAdapter,
    │             connector.indexer.base) — поведение не изменено;
    └── custody — адаптеры депозитариев (DepositoryAdapter, фаза 3).

Интерфейс версионируется (ADAPTER_API_VERSION): будущие реальные адаптеры
депозитариев объявляют совместимость через meta().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

ADAPTER_API_VERSION = 1


@dataclass(frozen=True)
class SourceMeta:
    """Идентификация адаптера: id экземпляра, класс, семейство, версия API."""

    id: str
    name: str
    family: str  # chain | custody
    adapter_api_version: int = ADAPTER_API_VERSION


@dataclass(frozen=True)
class HealthStatus:
    """Состояние источника для мониторинга (п. 10 ТЗ, health-check)."""

    status: str  # ok | degraded | down
    detail: str = ""

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


class DataSource(ABC):
    """Источник внешних данных. Общая поверхность семейств — идентификация
    и health; остальной контракт определяют семейства."""

    family: ClassVar[str]
    source_name: str = ""

    def meta(self) -> SourceMeta:
        return SourceMeta(
            id=self.source_name or type(self).__name__,
            name=type(self).__name__,
            family=self.family,
        )

    @abstractmethod
    async def health(self) -> HealthStatus:
        """Доступность источника; не бросает исключений."""
