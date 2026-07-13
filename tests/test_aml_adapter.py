"""Фаза 1 aml-спеки: AmlAdapter, реестр, мок, журнал неизменяемости."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.aml.base import AmlAdapter
from connector.aml.mock_adapter import MockAmlAdapter
from connector.aml.store import last_screening, store_screening
from connector.hashing import canonical_sha256
from connector.models import AmlScreening, Base
from connector.sources.base import DataSource
from connector.sources.registry import aml_source_codes, create_aml_source

DIRTY = "TDirtyAddress11111111111111111111"
GREY = "TGreyAddress222222222222222222222"


@pytest.fixture
def fixtures_path(tmp_path):
    path = tmp_path / "aml.json"
    path.write_text(json.dumps({
        "default_score": 5,
        "addresses": {
            DIRTY: {"risk_score": 85, "categories": ["sanctions", "darknet"]},
            GREY: {"risk_score": 50, "categories": ["mixer"]},
        },
    }, ensure_ascii=False), encoding="utf-8")
    return str(path)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# --- Реестр и контракт ---------------------------------------------------------


def test_mock_registered_in_aml_family(fixtures_path):
    assert "mock-aml" in aml_source_codes()
    adapter = create_aml_source("mock-aml", fixtures_path=fixtures_path)
    assert isinstance(adapter, AmlAdapter)
    assert isinstance(adapter, DataSource)
    meta = adapter.meta()
    assert meta.family == "aml"
    assert meta.name == "Мок AML-провайдер (фикстуры)"


def test_unknown_provider_is_explicit_error():
    with pytest.raises(LookupError, match="crystal"):
        create_aml_source("crystal")


# --- Скрининг --------------------------------------------------------------------


async def test_screening_scores_by_fixture(fixtures_path):
    adapter = MockAmlAdapter(fixtures_path)

    dirty = await adapter.screen_address("tron", DIRTY)
    assert dirty.risk_score == 85
    assert dirty.categories == ("sanctions", "darknet")
    assert dirty.raw["risk_score"] == 85  # сырой ответ — в первичку

    unknown = await adapter.screen_address("tron", "TCleanUnknownAddress")
    assert unknown.risk_score == 5  # default_score
    assert unknown.categories == ()


async def test_health_and_down_emulation(fixtures_path):
    ok = await MockAmlAdapter(fixtures_path).health()
    assert ok.is_ok and "known_addresses=2" in ok.detail

    down = MockAmlAdapter(fixtures_path, health_override="down")
    assert (await down.health()).status == "down"
    with pytest.raises(ConnectionError):
        await down.screen_address("tron", DIRTY)


async def test_without_fixtures_everything_is_clean():
    adapter = MockAmlAdapter()  # без файла — скор 0 (для дев-контура)
    result = await adapter.screen_address("tron", DIRTY)
    assert result.risk_score == 0


# --- Журнал неизменяемости ---------------------------------------------------------


async def test_store_appends_history_with_checksum(session, fixtures_path):
    adapter = MockAmlAdapter(fixtures_path)

    first = await store_screening(session, "mock-aml", await adapter.screen_address("tron", GREY))
    assert first.checksum == canonical_sha256(first.raw)
    assert first.risk_score == 50

    # Повторная проверка — новая запись (история append-only), не апдейт.
    second = await store_screening(
        session, "mock-aml", await adapter.screen_address("tron", GREY)
    )
    assert second.id != first.id
    rows = (await session.execute(select(AmlScreening))).scalars().all()
    assert len(rows) == 2

    latest = await last_screening(session, "tron", GREY)
    assert latest.id == second.id
    assert latest.categories == ["mixer"]


async def test_last_screening_none_for_unchecked(session):
    assert await last_screening(session, "tron", "TNeverChecked") is None
