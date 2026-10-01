"""Фаза 3 depository-спеки: DepositoryAdapter, реестр, мок, генератор фикстур."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from connector.custody.base import Capabilities, DepositoryAdapter, EntryGranularity
from connector.custody.fixtures import DistortionOptions, distort, write_fixture
from connector.custody.mock_adapter import MockDepositoryAdapter
from connector.custody.store import EntryPayload
from connector.models import CustodyOperationType
from connector.sources.base import DataSource
from connector.sources.registry import create_custody_source, custody_source_codes


def dt(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc)


def entry(entry_id: str, amount: str, day: int = 6, asset: str = "USDT",
          counterparty: str | None = "TPartner",
          tx_hash: str | None = None) -> EntryPayload:
    value = Decimal(amount)
    return EntryPayload(
        entry_id=entry_id,
        occurred_at=dt(day),
        asset=asset,
        amount=value,
        operation_type=(
            CustodyOperationType.DEPOSIT if value >= 0 else CustodyOperationType.WITHDRAWAL
        ),
        network="tron",
        counterparty_ref=counterparty,
        external_tx_hash=tx_hash or f"hash-{entry_id}",
    )


@pytest.fixture
def fixtures_dir(tmp_path):
    write_fixture(
        tmp_path / "july.json",
        statement_id="ST-JULY",
        period_from=dt(1),
        period_to=dt(31),
        entries=[entry("1", "48500"), entry("2", "-31200", day=15)],
    )
    write_fixture(
        tmp_path / "june.csv",
        statement_id="ST-JUNE",
        period_from=dt(1) - timedelta(days=30),
        period_to=dt(1) - timedelta(days=1),
        entries=[entry("1", "1000", day=1)],
    )
    return str(tmp_path)


# --- Реестр и контракт ---------------------------------------------------------


def test_mock_registered_in_custody_family(fixtures_dir):
    assert "mock-depo" in custody_source_codes()
    adapter = create_custody_source("mock-depo", fixtures_dir=fixtures_dir)
    assert isinstance(adapter, DepositoryAdapter)
    assert isinstance(adapter, DataSource)
    meta = adapter.meta()
    assert meta.family == "custody"
    assert meta.name == "Мок-депозитарий (фикстуры)"


def test_unknown_custody_source_is_explicit_error():
    with pytest.raises(LookupError, match="real-depo"):
        create_custody_source("real-depo")


# --- Чтение фикстур (JSON и CSV, оба формата через roundtrip генератора) --------


async def test_fetch_statements_filters_by_period(fixtures_dir):
    adapter = MockDepositoryAdapter(fixtures_dir)
    july = await adapter.fetch_statements(dt(1), dt(31))
    assert [s.statement_id for s in july] == ["ST-JULY"]
    both = await adapter.fetch_statements(dt(1) - timedelta(days=30), dt(31))
    assert {s.statement_id for s in both} == {"ST-JULY", "ST-JUNE"}


async def test_fetch_entries_roundtrip_json_and_csv(fixtures_dir):
    adapter = MockDepositoryAdapter(fixtures_dir)
    july = await adapter.fetch_entries("ST-JULY")
    assert [e.amount for e in july] == [Decimal("48500"), Decimal("-31200")]
    assert july[1].operation_type == CustodyOperationType.WITHDRAWAL

    june = await adapter.fetch_entries("ST-JUNE")  # из CSV
    assert june[0].amount == Decimal("1000")
    assert june[0].external_tx_hash == "hash-1"

    with pytest.raises(LookupError, match="ST-NOPE"):
        await adapter.fetch_entries("ST-NOPE")


async def test_capabilities_derived_from_data(tmp_path):
    # Бедная выписка: без хэшей и сети, агрегированная.
    poor = distort(
        [entry("1", "100"), entry("2", "200"), entry("3", "-50", day=7)],
        DistortionOptions(aggregate="day", strip=("hashes", "network")),
    )
    write_fixture(tmp_path / "poor.json", "ST-POOR", dt(1), dt(31), poor,
                  granularity="aggregated")
    adapter = MockDepositoryAdapter(str(tmp_path))
    caps = adapter.capabilities()
    assert caps.has_tx_hash is False
    assert caps.has_network is False
    assert caps.entry_granularity == EntryGranularity.AGGREGATED


async def test_explicit_capabilities_override(fixtures_dir):
    declared = Capabilities(False, False, False, EntryGranularity.MIXED)
    adapter = MockDepositoryAdapter(fixtures_dir, capabilities=declared)
    assert adapter.capabilities() == declared


# --- Эмуляция сбоев ---------------------------------------------------------------


async def test_health_ok_degraded_and_down(fixtures_dir):
    ok = await MockDepositoryAdapter(fixtures_dir).health()
    assert ok.is_ok and "statements=2" in ok.detail

    degraded = MockDepositoryAdapter(fixtures_dir, health_override="degraded")
    assert (await degraded.health()).status == "degraded"
    # degraded: данные ещё отдаются
    assert await degraded.fetch_statements(dt(1), dt(31))

    down = MockDepositoryAdapter(fixtures_dir, health_override="down")
    assert (await down.health()).status == "down"
    with pytest.raises(ConnectionError):
        await down.fetch_statements(dt(1), dt(31))


async def test_duplicate_statement_id_across_files_is_error(tmp_path):
    write_fixture(tmp_path / "a.json", "ST-DUP", dt(1), dt(31), [entry("1", "10")])
    write_fixture(tmp_path / "b.csv", "ST-DUP", dt(1), dt(31), [entry("1", "20")])
    adapter = MockDepositoryAdapter(str(tmp_path))
    with pytest.raises(ValueError, match="ST-DUP"):
        await adapter.fetch_statements(dt(1), dt(31))


# --- Генератор искажений ------------------------------------------------------------


BASE = [entry(str(i), amount) for i, amount in enumerate(
    ["100", "200", "-50", "300", "-120"], start=1)]


def test_drop_and_extra():
    result = distort(BASE, DistortionOptions(drop=2, extra=1), seed=1)
    assert len(result) == len(BASE) - 2 + 1
    extras = [e for e in result if e.entry_id.startswith("EXTRA-")]
    assert len(extras) == 1
    assert extras[0].external_tx_hash.startswith("extra-")


def test_amount_noise_keeps_direction():
    result = distort(BASE, DistortionOptions(amount_noise=True), seed=2)
    for original, noisy in zip(BASE, result):
        assert (noisy.amount >= 0) == (original.amount >= 0)
        assert abs(noisy.amount) <= abs(original.amount)  # комиссия внутри суммы
    assert any(n.amount != o.amount for n, o in zip(BASE, result))


def test_date_shift_moves_some_entries():
    result = distort(BASE * 4, DistortionOptions(date_shift_days=30), seed=3)
    shifted = [e for e in result if e.occurred_at.month != 7]
    assert shifted  # часть строк уехала за границу периода


def test_aggregate_by_day_sums_and_strips_hashes():
    result = distort(BASE, DistortionOptions(aggregate="day"), seed=0)
    assert len(result) == 2  # (USDT, in, день) и (USDT, out, день)
    total_in = next(e for e in result if e.amount > 0)
    assert total_in.amount == Decimal("600")
    assert total_in.external_tx_hash is None
    assert total_in.raw_line["aggregated_of"] == 3


def test_determinism_same_seed_same_fixture():
    options = DistortionOptions(drop=1, extra=2, amount_noise=True, date_shift_days=5)
    a = distort(BASE, options, seed=42)
    b = distort(BASE, options, seed=42)
    assert a == b
    c = distort(BASE, options, seed=43)
    assert a != c
