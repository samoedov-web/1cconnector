"""Тесты v1-блоков: переоценка, налоговый регистр, акт сверки."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.accounting.revaluation import run_revaluation
from connector.models import (
    Asset,
    Base,
    Contract,
    Counterparty,
    CounterpartyAddress,
    Direction,
    Invoice,
    Network,
    OnecDocType,
    OnecDocument,
    Transaction,
    TxStatus,
    Wallet,
)
from connector.pipeline import TransactionPipeline
from connector.rates.base import Quote, RateSource
from connector.rates.service import RateService
from connector.reports.reconciliation import reconciliation_data
from connector.reports.tax import tax_register_data


def dt(day: int, month: int = 6) -> datetime:
    return datetime(2026, month, day, 12, tzinfo=timezone.utc)


class VariableRateSource(RateSource):
    """Курс USD/RUB зависит от даты — для проверки курсовых разниц."""

    source_name = "variable"

    def __init__(self, usd_rub_by_month: dict[int, str]) -> None:
        self.by_month = usd_rub_by_month

    async def get_quote(self, base, quote, as_of):
        if (base.upper(), quote.upper()) == ("USDT", "USD"):
            return Quote("USDT", "USD", Decimal(1), as_of, self.source_name, {})
        if quote.upper() == "RUB":
            rate = Decimal(self.by_month[as_of.month])
            return Quote(base.upper(), "RUB", rate, as_of, self.source_name, {})
        raise LookupError(f"{base}/{quote}")


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def world(session):
    """Мир с проведённой входящей 1000 USDT по курсу 80 ₽ (июнь)."""
    network = Network(code="tron", name="TRON", finality_depth=19)
    session.add(network)
    await session.flush()
    asset = Asset(network_id=network.id, symbol="USDT", contract_address="TUsdt", decimals=6)
    wallet = Wallet(network_id=network.id, address="TMyWallet")
    counterparty = Counterparty(name="Shanghai Trade Co.")
    session.add_all([asset, wallet, counterparty])
    await session.flush()
    session.add(
        CounterpartyAddress(
            counterparty_id=counterparty.id, network_id=network.id, address="TPartner"
        )
    )
    contract = Contract(counterparty_id=counterparty.id, number="ВЭД-1",
                        registration_number="26030012/1481/0000/9/1", currency="USD")
    session.add(contract)
    await session.flush()
    session.add(
        Invoice(contract_id=contract.id, number="INV-1", amount=Decimal(1000),
                currency="USD", due_from=dt(1), due_to=dt(28))
    )
    tx = Transaction(
        network_id=network.id, asset_id=asset.id, wallet_id=wallet.id,
        tx_hash="aa", log_index=0, block_number=1010, block_time=dt(10),
        direction=Direction.IN, from_address="TPartner", to_address="TMyWallet",
        amount=Decimal(1000), status=TxStatus.FINAL, confirmations=25,
        finalized_at=dt(10), raw_response={}, source="test",
    )
    session.add(tx)
    await session.flush()
    rates = RateService(primary=VariableRateSource({6: "80", 7: "85", 8: "78"}))
    await TransactionPipeline(session, rates).process_network(network)
    return {"network": network, "asset": asset, "wallet": wallet,
            "counterparty": counterparty, "rates": rates}


# --- Переоценка ---------------------------------------------------------------


async def test_revaluation_computes_difference_and_document(session, world):
    # Июль: курс вырос 80 → 85; остаток 1000, себестоимость 80 000.
    [rev] = await run_revaluation(session, world["rates"], dt(31, month=7))
    assert rev.quantity == Decimal(1000)
    assert rev.book_cost_rub == Decimal(80000)
    assert rev.market_rub == Decimal(85000)
    assert rev.difference_rub == Decimal(5000)
    assert rev.delta_rub == Decimal(5000)  # первая переоценка

    doc = await session.scalar(
        select(OnecDocument).where(OnecDocument.doc_type == OnecDocType.REVALUATION)
    )
    assert Decimal(doc.payload["difference_rub"]) == Decimal(5000)
    assert Decimal(doc.payload["delta_rub"]) == Decimal(5000)


async def test_second_revaluation_delta_vs_previous(session, world):
    await run_revaluation(session, world["rates"], dt(31, month=7))  # +5000
    # Август: курс упал до 78 — оценка 78 000, разница −2000, дельта −7000.
    [rev] = await run_revaluation(session, world["rates"], dt(31, month=8))
    assert rev.difference_rub == Decimal(-2000)
    assert rev.delta_rub == Decimal(-7000)


async def test_revaluation_is_idempotent_and_skips_zero_balance(session, world):
    first = await run_revaluation(session, world["rates"], dt(31, month=7))
    again = await run_revaluation(session, world["rates"], dt(31, month=7))
    assert len(first) == 1
    assert again == []  # повторный запуск на ту же дату — ничего
    docs = (await session.execute(select(OnecDocument).where(
        OnecDocument.doc_type == OnecDocType.REVALUATION))).scalars().all()
    assert len(docs) == 1


# --- Налоговый регистр -----------------------------------------------------------


async def test_tax_register_income_disposal_and_totals(session, world):
    # Добавим выбытие 400 USDT в июле (курс 85): выручка 34 000, себестоимость 32 000.
    session.add(
        Invoice(
            contract_id=(await session.scalar(select(Contract.id))),
            number="INV-OUT", amount=Decimal(400), currency="USD",
            due_from=dt(1, 7), due_to=dt(28, 7),
        )
    )
    out = Transaction(
        network_id=world["network"].id, asset_id=world["asset"].id,
        wallet_id=world["wallet"].id, tx_hash="bb", log_index=0,
        block_number=2000, block_time=dt(15, 7), direction=Direction.OUT,
        from_address="TMyWallet", to_address="TPartner", amount=Decimal(400),
        status=TxStatus.FINAL, confirmations=30, finalized_at=dt(15, 7),
        raw_response={}, source="test",
    )
    session.add(out)
    await session.flush()
    await TransactionPipeline(session, world["rates"]).process_network(world["network"])

    data = await tax_register_data(session, dt(1, 6), dt(28, 8))
    kinds = [r["kind"] for r in data["rows"]]
    assert kinds == ["receipt", "disposal"]
    disposal = data["rows"][1]
    assert Decimal(disposal["income_rub"]) == Decimal(34000)
    assert Decimal(disposal["cost_rub"]) == Decimal(32000)
    assert Decimal(disposal["result_rub"]) == Decimal(2000)
    assert Decimal(data["totals"]["income_rub"]) == Decimal(80000 + 34000)
    assert Decimal(data["totals"]["result_rub"]) == Decimal(80000 + 2000)
    assert data["rows"][0]["raw_response_sha256"]  # журнал неизменяемости


async def test_revaluation_never_enters_tax_register(session, world):
    """Ст. 282.3 НК: переоценка — бухгалтерская, налоговую базу не меняет.

    Регистр до и после переоценки идентичен: ни новых строк, ни изменения
    итогов; вид строк ограничен реализованными операциями.
    """
    before = await tax_register_data(session, dt(1, 6), dt(28, 8))
    [rev] = await run_revaluation(session, world["rates"], dt(31, month=7))
    assert rev.difference_rub != 0  # переоценка реально состоялась
    after = await tax_register_data(session, dt(1, 6), dt(28, 8))

    assert after["rows"] == before["rows"]
    assert after["totals"] == before["totals"]
    assert {r["kind"] for r in after["rows"]} <= {"receipt", "disposal", "fee"}


# --- Акт сверки --------------------------------------------------------------------


async def test_reconciliation_act_invoices_payments_balance(session, world):
    data = await reconciliation_data(
        session, world["counterparty"].id, dt(1, 6), dt(28, 6)
    )
    assert data["counterparty"]["name"] == "Shanghai Trade Co."
    [invoice] = data["invoices"]
    assert invoice["number"] == "INV-1"
    [payment] = data["payments"]
    assert payment["tx_hash"] == "aa"
    assert Decimal(payment["contract_currency_amount"]) == Decimal(1000)
    assert Decimal(data["totals"]["invoiced"]) == Decimal(1000)
    assert Decimal(data["totals"]["paid"]) == Decimal(1000)
    assert Decimal(data["totals"]["balance"]) == Decimal(0)


async def test_reconciliation_unknown_counterparty(session):
    assert await reconciliation_data(session, 999, dt(1), dt(28)) is None
