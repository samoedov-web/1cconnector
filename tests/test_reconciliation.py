"""Фаза 4 depository-спеки: движок сверки — 10 обязательных сценариев
раздела 6 + свойства (детерминированность, идемпотентность, аудит).

Сценарии 1–8 — чистый движок (без БД); 9 (маппинг активов) и 10 (реорг) —
сервисный слой на SQLite.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.custody.reconciliation import (
    CustodyView,
    LedgerView,
    ReconConfig,
    ReconStatus,
    reconcile,
)
from connector.custody.service import mark_stale_runs, run_reconciliation
from connector.custody.store import EntryPayload, StatementPayload, store_statement
from connector.models import (
    Asset,
    Base,
    CustodyAssetMapping,
    CustodyOperationType,
    Direction,
    Network,
    ReconciliationResult,
    Transaction,
    TxStatus,
    Wallet,
)


def dt(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc)


def ledger(id: int, amount: str, day: int = 6, asset: str = "USDT",
           tx_hash: str | None = None) -> LedgerView:
    return LedgerView(
        id=id, tx_hash=tx_hash or f"hash-{id}", asset=asset,
        amount=Decimal(amount), occurred_at=dt(day),
    )


def custody(id: int, amount: str, day: int = 6, asset: str = "USDT",
            tx_hash: str | None = None, aggregate: bool = False) -> CustodyView:
    return CustodyView(
        id=id, entry_id=str(id), asset=asset, amount=Decimal(amount),
        occurred_at=dt(day), external_tx_hash=tx_hash, maybe_aggregate=aggregate,
    )


def by_status(results):
    grouped: dict[ReconStatus, list] = {}
    for item in results:
        grouped.setdefault(item.status, []).append(item)
    return grouped


# --- Сценарий 1: идеальное совпадение 1:1 по хэшам -----------------------------


def test_scenario_1_perfect_hash_match():
    chain = [ledger(1, "100", tx_hash="a"), ledger(2, "-50", day=7, tx_hash="b")]
    depo = [custody(11, "100", tx_hash="a"), custody(12, "-50", day=7, tx_hash="b")]
    results = reconcile(chain, depo, ReconConfig())
    assert all(r.status == ReconStatus.MATCHED for r in results)
    assert all(r.rule == "hash" for r in results)
    assert len(results) == 2


# --- Сценарий 2: недостача в выписке -------------------------------------------


def test_scenario_2_missing_in_custody():
    chain = [ledger(1, "100", tx_hash="a"), ledger(2, "200", tx_hash="b")]
    depo = [custody(11, "100", tx_hash="a")]
    grouped = by_status(reconcile(chain, depo, ReconConfig()))
    assert len(grouped[ReconStatus.MATCHED]) == 1
    [missing] = grouped[ReconStatus.MISSING_IN_CUSTODY]
    assert missing.ledger_ids == (2,)
    assert missing.detail["tx_hash"] == "b"  # аудит: что именно потеряно


# --- Сценарий 3: лишняя запись в выписке -----------------------------------------


def test_scenario_3_missing_on_chain():
    chain = [ledger(1, "100", tx_hash="a")]
    depo = [custody(11, "100", tx_hash="a"), custody(12, "777", tx_hash="phantom")]
    grouped = by_status(reconcile(chain, depo, ReconConfig()))
    [extra] = grouped[ReconStatus.MISSING_ON_CHAIN]
    assert extra.custody_ids == (12,)


# --- Сценарий 4: комиссия внутри суммы и tolerance --------------------------------


def test_scenario_4_amount_mismatch_vs_tolerance():
    chain = [ledger(1, "1000", tx_hash="a")]
    depo = [custody(11, "997", tx_hash="a")]  # комиссия 3 удержана внутри

    strict = by_status(reconcile(chain, depo, ReconConfig()))  # tolerance = 0
    [mismatch] = strict[ReconStatus.AMOUNT_MISMATCH]
    assert mismatch.detail["delta"] == "-3"

    tolerant = ReconConfig(tolerance_abs=Decimal(3))
    grouped = by_status(reconcile(chain, depo, tolerant))
    assert ReconStatus.AMOUNT_MISMATCH not in grouped
    assert len(grouped[ReconStatus.MATCHED]) == 1


# --- Сценарий 5: сдвиг даты через границу периода ----------------------------------


def test_scenario_5_date_window():
    # Выписка датирует платёж 1 августа, цепочка — 31 июля (без хэшей).
    chain = [ledger(1, "500", day=31, tx_hash="x")]
    depo = [CustodyView(11, "11", "USDT", Decimal("500"),
                        datetime(2026, 8, 1, 12, tzinfo=timezone.utc))]

    wide = by_status(reconcile(chain, depo, ReconConfig(date_window=timedelta(days=2))))
    assert len(wide[ReconStatus.MATCHED]) == 1
    assert wide[ReconStatus.MATCHED][0].rule == "tuple"

    narrow = by_status(reconcile(chain, depo, ReconConfig(date_window=timedelta(hours=6))))
    [shifted] = narrow[ReconStatus.DATE_MISMATCH]
    assert shifted.detail["date_delta_hours"] == 24.0


# --- Сценарий 6: агрегированная строка = 5 транзакций -------------------------------


def test_scenario_6_aggregate_of_five():
    chain = [ledger(i, "100", tx_hash=f"h{i}") for i in range(1, 6)]
    depo = [custody(11, "500", aggregate=True)]
    grouped = by_status(reconcile(chain, depo, ReconConfig()))
    [agg] = grouped[ReconStatus.MATCHED_AGGREGATE]
    assert set(agg.ledger_ids) == {1, 2, 3, 4, 5}
    assert agg.detail["transactions"] == 5


def test_scenario_6b_aggregate_manual_when_auto_accept_off():
    chain = [ledger(i, "100") for i in range(1, 6)]
    depo = [custody(11, "500", aggregate=True)]
    grouped = by_status(reconcile(chain, depo, ReconConfig(accept_aggregates=False)))
    [manual] = grouped[ReconStatus.MANUAL]
    assert manual.rule == "aggregate"


# --- Сценарий 7: частичное покрытие агрегата -----------------------------------------


def test_scenario_7_partial_aggregate_with_delta():
    chain = [ledger(i, "100", tx_hash=f"h{i}") for i in range(1, 6)]  # 5 × 100
    depo = [custody(11, "400", aggregate=True)]  # агрегат покрывает 4 из 5
    grouped = by_status(reconcile(chain, depo, ReconConfig()))
    [partial] = grouped[ReconStatus.AMOUNT_MISMATCH]
    assert partial.rule == "aggregate"
    assert partial.detail["delta"] == "-100"  # дельта указана
    assert ReconStatus.MISSING_IN_CUSTODY not in grouped  # кандидаты не задвоены


# --- Сценарий 8: дубликат строки выписки ----------------------------------------------


def test_scenario_8_duplicate_not_double_matched():
    chain = [ledger(1, "100", tx_hash="a")]
    depo = [custody(11, "100", tx_hash="a"), custody(12, "100", tx_hash="a")]
    grouped = by_status(reconcile(chain, depo, ReconConfig()))
    assert len(grouped[ReconStatus.MATCHED]) == 1
    [dup] = grouped[ReconStatus.DUPLICATE_SUSPECT]
    assert dup.custody_ids == (12,)
    assert dup.detail["duplicate_of_entry"] == 11


def test_scenario_8b_duplicate_without_hash():
    chain = [ledger(1, "100", tx_hash="a")]
    depo = [custody(11, "100"), custody(12, "100")]  # одинаковые (актив,сумма,дата)
    grouped = by_status(reconcile(chain, depo, ReconConfig()))
    assert len(grouped[ReconStatus.DUPLICATE_SUSPECT]) == 1


# --- Свойства движка -------------------------------------------------------------------


def test_determinism_same_input_same_output():
    chain = [ledger(i, str(100 + i), day=5 + i % 3) for i in range(1, 8)]
    depo = [custody(10 + i, str(100 + i), day=5 + i % 3) for i in range(1, 8)]
    a = reconcile(chain, depo, ReconConfig())
    b = reconcile(list(reversed(chain)), list(reversed(depo)), ReconConfig())
    assert a == b  # порядок входа не влияет


def test_every_result_carries_audit_detail():
    chain = [ledger(1, "100", tx_hash="a"), ledger(2, "999")]
    depo = [custody(11, "97", tx_hash="a"), custody(12, "555")]
    for item in reconcile(chain, depo, ReconConfig()):
        assert item.rule
        assert item.detail  # каждое решение объяснено


# --- Сценарии 9 и 10: сервисный слой (SQLite) --------------------------------------------


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def make_world(session, tx_hash="chainhash", amount="1000",
                     entry_has_hash=True):
    network = Network(code="tron", name="TRON", finality_depth=19)
    session.add(network)
    await session.flush()
    asset = Asset(network_id=network.id, symbol="USDT", contract_address="T", decimals=6)
    wallet = Wallet(network_id=network.id, address="TMyWallet")
    session.add_all([asset, wallet])
    await session.flush()
    tx = Transaction(
        network_id=network.id, asset_id=asset.id, wallet_id=wallet.id,
        tx_hash=tx_hash, log_index=0, block_number=100, block_time=dt(6),
        direction=Direction.IN, from_address="TP", to_address="TMyWallet",
        amount=Decimal(amount), status=TxStatus.FINAL, confirmations=30,
        finalized_at=dt(6), raw_response={}, source="test",
    )
    session.add(tx)
    await session.flush()
    await store_statement(
        session,
        StatementPayload("ST-1", "mock-depo", dt(1), dt(31), None, {"s": 1}),
        [EntryPayload(
            entry_id="1", occurred_at=dt(6), asset="USDT-TRC20",  # чужой тикер!
            amount=Decimal(amount), operation_type=CustodyOperationType.DEPOSIT,
            external_tx_hash=tx_hash if entry_has_hash else None,
        )],
    )
    return tx


async def test_scenario_9_asset_mapping(session):
    # Бедная выписка без хэшей: исход решает маппинг тикера (при совпавшем
    # хэше движок матчит и без маппинга — хэш идентифицирует транзакцию).
    await make_world(session, entry_has_hash=False)
    # Без маппинга тикер USDT-TRC20 не совпадает с USDT → расхождения.
    run = await run_reconciliation(session, "mock-depo", dt(1), dt(31))
    statuses = {r.status for r in run.results}
    assert statuses == {"missing_on_chain", "missing_in_custody"}

    # С маппингом — matched. Прежний запуск не мешает (другая конфигурация
    # не нужна: помечаем его устаревшим вручную как исправление настройки).
    run.stale = True
    session.add(CustodyAssetMapping(
        source_id="mock-depo", custody_ticker="USDT-TRC20", asset_symbol="USDT",
    ))
    await session.flush()
    rerun = await run_reconciliation(session, "mock-depo", dt(1), dt(31))
    assert [r.status for r in rerun.results] == ["matched"]


async def test_scenario_10_reorg_marks_run_stale_and_rerun_consistent(session):
    tx = await make_world(session)
    run = await run_reconciliation(session, "mock-depo", dt(1), dt(31))
    assert [r.status for r in run.results] == ["matched"]

    # Реорг после выпуска выписки: транзакция потеряла финальность.
    tx.status = TxStatus.ORPHANED
    await session.flush()
    marked = await mark_stale_runs(session)
    assert marked == 1
    assert run.stale is True

    # Повторный запуск даёт консистентный итог: строка выписки без пары.
    rerun = await run_reconciliation(session, "mock-depo", dt(1), dt(31))
    assert rerun.id != run.id
    assert [r.status for r in rerun.results] == ["missing_on_chain"]


async def test_idempotent_rerun_returns_same_run(session):
    await make_world(session)
    first = await run_reconciliation(session, "mock-depo", dt(1), dt(31))
    second = await run_reconciliation(session, "mock-depo", dt(1), dt(31))
    assert second.id == first.id
    total = (await session.execute(select(ReconciliationResult))).scalars().all()
    assert len(total) == len(first.results)  # результаты не задвоены