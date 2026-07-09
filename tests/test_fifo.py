"""Тесты учётного движка ФИФО (п. 6 ТЗ)."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from connector.accounting.fifo import (
    FifoMethod,
    InsufficientBalanceError,
    LotView,
    get_method,
)


def dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=timezone.utc)


def lots() -> list[LotView]:
    return [
        LotView(id=1, acquired_at=dt(1), remaining=Decimal(100), unit_cost_rub=Decimal(90)),
        LotView(id=2, acquired_at=dt(5), remaining=Decimal(50), unit_cost_rub=Decimal(95)),
        LotView(id=3, acquired_at=dt(9), remaining=Decimal(200), unit_cost_rub=Decimal(100)),
    ]


def test_disposal_within_first_lot():
    result = FifoMethod().dispose(lots(), Decimal(40), proceeds_rub=Decimal(4000))
    assert len(result.parts) == 1
    assert result.parts[0].lot_id == 1
    assert result.total_cost_rub == Decimal(40) * Decimal(90)
    assert result.gain_rub == Decimal(4000) - Decimal(3600)


def test_disposal_spans_lots_in_acquisition_order():
    result = FifoMethod().dispose(lots(), Decimal(130), proceeds_rub=Decimal(13000))
    assert [(p.lot_id, p.quantity) for p in result.parts] == [
        (1, Decimal(100)),
        (2, Decimal(30)),
    ]
    assert result.total_cost_rub == Decimal(100) * 90 + Decimal(30) * 95


def test_disposal_mutates_remaining():
    open_lots = lots()
    FifoMethod().dispose(open_lots, Decimal(120), proceeds_rub=Decimal(0))
    assert open_lots[0].remaining == Decimal(0)
    assert open_lots[1].remaining == Decimal(30)
    assert open_lots[2].remaining == Decimal(200)


def test_disposal_ignores_exhausted_lots_and_unsorted_input():
    unsorted = list(reversed(lots()))
    unsorted.append(
        LotView(id=0, acquired_at=dt(2), remaining=Decimal(0), unit_cost_rub=Decimal(1))
    )
    result = FifoMethod().dispose(unsorted, Decimal(110), proceeds_rub=Decimal(0))
    assert [p.lot_id for p in result.parts] == [1, 2]


def test_over_disposal_raises():
    with pytest.raises(InsufficientBalanceError):
        FifoMethod().dispose(lots(), Decimal(351), proceeds_rub=Decimal(0))


def test_non_positive_quantity_rejected():
    with pytest.raises(ValueError):
        FifoMethod().dispose(lots(), Decimal(0), proceeds_rub=Decimal(0))


def test_method_registry_is_configurable():
    assert isinstance(get_method("fifo"), FifoMethod)
    with pytest.raises(ValueError):
        get_method("hifo")
