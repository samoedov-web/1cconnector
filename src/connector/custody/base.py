"""Интерфейс DepositoryAdapter — семейство custody (п. 4.3 спеки, фаза 3).

Контракт под будущие реальные адаптеры депозитариев/лицензированных
обменников: когда появятся API (подзаконные акты + реестр ЦБ), интеграция —
тонкий адаптер с этим интерфейсом, регистрируемый в том же реестре,
что и адаптеры сетей. Интерфейс версионируется через
SourceMeta.adapter_api_version.

capabilities() — ключевой элемент: движок сверки (фаза 4) обязан работать
при ЛЮБОЙ комбинации возможностей, в т.ч. когда выписка не содержит
tx-хэшей и агрегирует операции за день.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from connector.custody.store import EntryPayload, StatementPayload
from connector.sources.base import DataSource


class EntryGranularity(StrEnum):
    PER_TX = "per_tx"  # строка выписки = одна транзакция
    AGGREGATED = "aggregated"  # строка = агрегат (день/контрагент)
    MIXED = "mixed"


@dataclass(frozen=True)
class Capabilities:
    """Что умеет отдавать конкретный депозитарий."""

    has_tx_hash: bool
    has_counterparty: bool
    has_network: bool
    entry_granularity: EntryGranularity


class DepositoryAdapter(DataSource):
    """Read-only доступ к выпискам одного депозитария.

    Наследники обязаны реализовать capabilities/fetch_statements/
    fetch_entries/health и фабрику from_config (для реестра источников);
    meta() унаследован от DataSource.
    """

    family: ClassVar[str] = "custody"

    @classmethod
    @abstractmethod
    def from_config(cls, **config) -> "DepositoryAdapter":
        """Единообразная фабрика для реестра источников."""

    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    async def fetch_statements(
        self, period_from: datetime, period_to: datetime
    ) -> list[StatementPayload]:
        """Выписки, чей период пересекается с запрошенным."""

    @abstractmethod
    async def fetch_entries(self, statement_id: str) -> list[EntryPayload]:
        """Строки выписки; LookupError для неизвестного statement_id."""
