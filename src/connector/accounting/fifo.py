"""Учётный движок: метод оценки выбытия ФИФО (п. 6 ТЗ).

Партии поступления (Lot) хранят себестоимость каждой партии; выбытие
списывает партии в порядке поступления и возвращает построчную расшифровку —
она попадает в DisposalLine и в проект документа «Выбытие цифровой валюты».

Реализовано как чистая логика над dataclass-снимками (тестируется без БД);
интерфейс CostBasisMethod оставлен для смены метода настройкой (ЛИФО/средняя).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class LotView:
    """Открытая партия (remaining > 0), в порядке поступления."""

    id: int
    acquired_at: datetime
    remaining: Decimal
    unit_cost_rub: Decimal


@dataclass(frozen=True)
class DisposalPart:
    lot_id: int
    quantity: Decimal
    cost_rub: Decimal  # себестоимость списанной части, руб.


@dataclass(frozen=True)
class DisposalResult:
    parts: list[DisposalPart]
    total_cost_rub: Decimal
    proceeds_rub: Decimal
    gain_rub: Decimal  # финрезультат: выручка − себестоимость


class InsufficientBalanceError(Exception):
    """Выбытие превышает остаток по партиям — расхождение данных, стоп."""


class CostBasisMethod(ABC):
    @abstractmethod
    def dispose(
        self, lots: list[LotView], quantity: Decimal, proceeds_rub: Decimal
    ) -> DisposalResult: ...


class FifoMethod(CostBasisMethod):
    def dispose(
        self, lots: list[LotView], quantity: Decimal, proceeds_rub: Decimal
    ) -> DisposalResult:
        """Списать quantity из партий по ФИФО.

        Мутирует remaining переданных партий (вызывающий код сохраняет их в БД
        одной транзакцией вместе с DisposalLine).
        """
        if quantity <= 0:
            raise ValueError("Количество выбытия должно быть положительным")
        available = sum((lot.remaining for lot in lots), Decimal(0))
        if quantity > available:
            raise InsufficientBalanceError(
                f"Выбытие {quantity} превышает остаток {available} по партиям"
            )

        parts: list[DisposalPart] = []
        remaining = quantity
        for lot in sorted(lots, key=lambda item: (item.acquired_at, item.id)):
            if remaining <= 0:
                break
            if lot.remaining <= 0:
                continue
            take = min(remaining, lot.remaining)
            cost = take * lot.unit_cost_rub
            parts.append(DisposalPart(lot_id=lot.id, quantity=take, cost_rub=cost))
            lot.remaining -= take
            remaining -= take

        total_cost = sum((p.cost_rub for p in parts), Decimal(0))
        return DisposalResult(
            parts=parts,
            total_cost_rub=total_cost,
            proceeds_rub=proceeds_rub,
            gain_rub=proceeds_rub - total_cost,
        )


METHODS: dict[str, type[CostBasisMethod]] = {"fifo": FifoMethod}


def get_method(name: str = "fifo") -> CostBasisMethod:
    try:
        return METHODS[name.lower()]()
    except KeyError:
        raise ValueError(f"Неизвестный метод оценки выбытия: {name}") from None
