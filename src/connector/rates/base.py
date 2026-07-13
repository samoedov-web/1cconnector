"""Интерфейс источника котировок (п. 4 ТЗ).

Источники — те же, что зафиксированы в учётной политике/договоре клиента:
настройка «основной + резервный». Каждый источник отдаёт котировку с
метаданными для журнала неизменяемости.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Quote:
    base: str  # что котируем: USDT, USD
    quote: str  # в чём: USD, RUB
    rate: Decimal
    as_of: datetime
    source: str
    raw: dict


class RateSource(ABC):
    source_name: str

    @abstractmethod
    async def get_quote(self, base: str, quote: str, as_of: datetime) -> Quote:
        """Котировка base/quote на момент as_of (или ближайшая доступная)."""
