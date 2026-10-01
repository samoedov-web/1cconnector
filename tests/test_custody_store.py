"""Фаза 2 depository-спеки: модели выписки и журнал неизменяемости."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.custody.store import (
    EntryPayload,
    StatementConflictError,
    StatementPayload,
    store_statement,
)
from connector.hashing import canonical_sha256
from connector.models import Base, CustodyEntry, CustodyOperationType, CustodyStatement


def dt(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc)


def statement(raw: dict | None = None) -> StatementPayload:
    return StatementPayload(
        statement_id="ST-2026-07",
        source_id="mock-depo",
        period_from=dt(1),
        period_to=dt(31),
        issued_at=dt(31, 18),
        raw_payload=raw or {"statement": "ST-2026-07", "lines": 2},
    )


def entries() -> list[EntryPayload]:
    return [
        EntryPayload(
            entry_id="1",
            occurred_at=dt(6),
            asset="USDT",
            amount=Decimal("48500"),
            operation_type=CustodyOperationType.DEPOSIT,
            network="tron",
            external_tx_hash="7d3f2a9c",
            raw_line={"n": 1},
        ),
        EntryPayload(
            entry_id="2",
            occurred_at=dt(15),
            asset="USDT",
            amount=Decimal("-31200"),  # знак = направление
            operation_type=CustodyOperationType.WITHDRAWAL,
            raw_line={"n": 2},
        ),
    ]


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_statement_stored_with_checksum_and_entries(session):
    row = await store_statement(session, statement(), entries())
    assert row.checksum == canonical_sha256({"statement": "ST-2026-07", "lines": 2})
    stored = (
        (await session.execute(select(CustodyEntry).order_by(CustodyEntry.entry_id)))
        .scalars()
        .all()
    )
    assert len(stored) == 2
    assert stored[0].statement_pk == row.id
    assert stored[0].amount == Decimal("48500")
    assert stored[1].amount == Decimal("-31200")
    assert stored[1].operation_type == CustodyOperationType.WITHDRAWAL
    assert stored[1].network is None  # capabilities: поле может отсутствовать
    assert stored[0].raw_line == {"n": 1}  # сырой слой сохранён как получен


async def test_checksum_is_key_order_independent(session):
    a = await store_statement(
        session,
        StatementPayload("A", "mock", dt(1), dt(31), None, {"x": 1, "y": 2}),
        [],
    )
    b = await store_statement(
        session,
        StatementPayload("B", "mock", dt(1), dt(31), None, {"y": 2, "x": 1}),
        [],
    )
    assert a.checksum == b.checksum


async def test_reload_same_statement_is_idempotent(session):
    first = await store_statement(session, statement(), entries())
    second = await store_statement(session, statement(), entries())
    assert second.id == first.id
    statements = await session.scalar(select(func.count(CustodyStatement.id)))
    entry_count = await session.scalar(select(func.count(CustodyEntry.id)))
    assert statements == 1
    assert entry_count == 2  # строки не задвоились


async def test_same_id_different_content_is_conflict_not_overwrite(session):
    original = await store_statement(session, statement(), entries())
    tampered = statement(raw={"statement": "ST-2026-07", "lines": 999})
    with pytest.raises(StatementConflictError, match="неизменяема"):
        await store_statement(session, tampered, [])
    # Существующая первичка не тронута.
    row = await session.get(CustodyStatement, original.id)
    assert row.raw_payload == {"statement": "ST-2026-07", "lines": 2}


async def test_operation_types_match_spec():
    assert {t.value for t in CustodyOperationType} == {
        "deposit", "withdrawal", "trade", "fee", "transfer_internal", "other",
    }
