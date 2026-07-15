"""Тесты индексера: сверка источников, реорг-проверка, окно сканирования,
пагинация диапазонов блоков."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.indexer.base import RawTransfer
from connector.indexer.ethereum import block_ranges
from connector.indexer.service import IndexerService, merge_sources
from connector.models import (
    Asset,
    Base,
    Direction,
    Network,
    Transaction,
    TxStatus,
    Wallet,
)
from connector.worker import scan_window


def dt(day: int) -> datetime:
    return datetime(2026, 6, day, tzinfo=timezone.utc)


def transfer(tx_hash: str, log_index: int = 0, block: int = 100) -> RawTransfer:
    return RawTransfer(
        tx_hash=tx_hash,
        log_index=log_index,
        block_number=block,
        block_time=dt(10),
        token_contract="TUsdt",
        from_address="TPartner",
        to_address="TMyWallet",
        amount=Decimal(100),
        fee_amount=Decimal(0),
        fee_asset="TRX",
        raw={},
        source="src",
    )


# --- merge_sources -----------------------------------------------------------


def test_merge_takes_union_and_flags_cross_checked():
    a, b, c = transfer("aa"), transfer("bb"), transfer("cc")
    merged = merge_sources([[a, b], [b, c]])
    by_hash = {m.transfer.tx_hash: m.cross_checked for m in merged}
    # Объединение: ничего не потеряно; bb видели оба источника.
    assert by_hash == {"aa": False, "bb": True, "cc": False}


def test_merge_single_source_nothing_cross_checked():
    merged = merge_sources([[transfer("aa")]])
    assert [m.cross_checked for m in merged] == [False]


# --- scan_window -------------------------------------------------------------


def test_first_scan_is_full_backfill():
    assert scan_window(dt(1), None, None, 19) == (None, dt(1))


def test_next_scans_start_from_cursor_with_reorg_margin():
    from_block, since = scan_window(dt(1), 1000, dt(15), finality_depth=19)
    assert from_block == 1000 - 38
    assert since == dt(15) - timedelta(hours=1)


def test_cursor_margin_does_not_go_negative():
    from_block, _ = scan_window(None, 10, None, finality_depth=19)
    assert from_block == 0


# --- block_ranges ------------------------------------------------------------


def test_block_ranges_cover_interval_without_gaps():
    ranges = block_ranges(0, 45, chunk=20)
    assert ranges == [(0, 19), (20, 39), (40, 45)]


def test_block_ranges_single_and_empty():
    assert block_ranges(5, 5, chunk=20) == [(5, 5)]
    assert block_ranges(10, 5, chunk=20) == []


# --- check_reorgs ------------------------------------------------------------


class FakeAdapter:
    """Канонические блоки транзакций; None — выпала из цепочки."""

    def __init__(self, blocks: dict[str, int | None]) -> None:
        self.blocks = blocks

    async def get_transaction_block(self, tx_hash: str) -> int | None:
        return self.blocks.get(tx_hash)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def make_tx(session, network, asset, wallet, tx_hash, status, block=100):
    tx = Transaction(
        network_id=network.id,
        asset_id=asset.id,
        wallet_id=wallet.id,
        tx_hash=tx_hash,
        log_index=0,
        block_number=block,
        block_time=dt(10),
        direction=Direction.IN,
        from_address="TPartner",
        to_address="TMyWallet",
        amount=Decimal(100),
        status=status,
        raw_response={},
        source="src",
    )
    session.add(tx)
    await session.flush()
    return tx


@pytest.fixture
async def world(session):
    network = Network(code="tron", name="TRON", finality_depth=19)
    session.add(network)
    await session.flush()
    asset = Asset(network_id=network.id, symbol="USDT", contract_address="TUsdt", decimals=6)
    wallet = Wallet(network_id=network.id, address="TMyWallet")
    session.add_all([asset, wallet])
    await session.flush()
    return {"network": network, "asset": asset, "wallet": wallet}


async def test_reorged_tx_marked_orphaned_and_moved_tx_updated(session, world):
    n, a, w = world["network"], world["asset"], world["wallet"]
    gone = await make_tx(session, n, a, w, "gone", TxStatus.CONFIRMED, block=100)
    moved = await make_tx(session, n, a, w, "moved", TxStatus.SEEN, block=100)
    stable = await make_tx(session, n, a, w, "stable", TxStatus.CONFIRMED, block=100)
    final = await make_tx(session, n, a, w, "final", TxStatus.FINAL, block=50)

    adapter = FakeAdapter({"gone": None, "moved": 105, "stable": 100, "final": None})
    changed = await IndexerService(session).check_reorgs(n, adapter)

    assert {t.tx_hash for t in changed} == {"gone", "moved"}
    assert gone.status == TxStatus.ORPHANED
    assert gone.confirmations == 0
    assert moved.block_number == 105
    assert stable.status == TxStatus.CONFIRMED
    # Финальные не перепроверяются — статус не тронут даже при "исчезновении".
    assert final.status == TxStatus.FINAL


async def test_ingest_marks_cross_checked_and_upgrades_existing(session, world):
    n, w = world["network"], world["wallet"]
    service = IndexerService(session)

    # Первый цикл: транзакцию видел только один источник.
    merged = merge_sources([[transfer("aa")]])
    [created] = await service.ingest_transfers(w, n, merged, latest_block=105)
    assert created.cross_checked is False

    # Второй цикл: подтвердил и второй источник — флаг поднялся, дубля нет.
    merged = merge_sources([[transfer("aa")], [transfer("aa")]])
    created_again = await service.ingest_transfers(w, n, merged, latest_block=106)
    assert created_again == []
    tx = await session.scalar(select(Transaction).where(Transaction.tx_hash == "aa"))
    assert tx.cross_checked is True


def test_single_source_networks_flagged_for_alert():
    """Два источника — требование конфигурации: одиночный не должен быть тихим
    (находка аудита whitepaper — «минимум два источника» не принуждался кодом)."""
    from connector.worker import single_source_networks

    adapters = {"tron": [object()], "ethereum": [object(), object()]}
    assert single_source_networks(adapters) == ["tron"]
    assert single_source_networks({"tron": [object(), object()]}) == []
