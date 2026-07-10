"""Плагинный интерфейс ChainAdapter (п. 3 ТЗ).

Новая сеть (BNB Chain, Polygon, TON) добавляется реализацией этого интерфейса
без изменения ядра. Адаптер только читает публичные данные — никаких ключей.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class RawTransfer:
    """Нормализованный трансфер токена, как его отдал источник данных."""

    tx_hash: str
    log_index: int
    block_number: int
    block_time: datetime
    token_contract: str
    from_address: str
    to_address: str
    amount: Decimal  # уже приведено к единицам токена (с учётом decimals)
    fee_amount: Decimal
    fee_asset: str
    raw: dict = field(default_factory=dict)  # сырой ответ источника — в первичку
    source: str = ""  # имя источника данных


class ChainAdapter(ABC):
    """Read-only доступ к одной сети через один источник данных."""

    network_code: str
    source_name: str

    @abstractmethod
    async def latest_block(self) -> int:
        """Номер последнего блока сети (для расчёта подтверждений)."""

    @abstractmethod
    async def fetch_transfers(
        self,
        address: str,
        token_contracts: list[str],
        since: datetime | None = None,
        from_block: int | None = None,
    ) -> list[RawTransfer]:
        """Все входящие/исходящие трансферы указанных токенов по адресу.

        Для бэкфилла передаётся since (дата добавления адреса),
        для инкрементального опроса — from_block/since от курсора кошелька.
        """

    @abstractmethod
    async def get_transaction_block(self, tx_hash: str) -> int | None:
        """Номер блока транзакции в канонической цепочке.

        None — транзакция в цепочке отсутствует (реорг); номер, отличный от
        сохранённого, — транзакция переехала в другой блок.
        """

    @abstractmethod
    async def get_transaction_fee(self, tx_hash: str) -> tuple[Decimal, str]:
        """Комиссия транзакции в нативной монете сети: (сумма, тикер).

        Комиссию платит отправитель — догружается только для исходящих
        транзакций отслеживаемых кошельков (отдельная статья расходов, п. 6 ТЗ).
        """
