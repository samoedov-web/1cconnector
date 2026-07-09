"""Интеграционные тесты связки: финальность → матчинг → курс → ФИФО → документ 1С."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.models import (
    Asset,
    Base,
    Contract,
    Counterparty,
    CounterpartyAddress,
    Direction,
    DisposalLine,
    Invoice,
    InvoiceStatus,
    Lot,
    Match,
    MatchState,
    Network,
    OnecDocType,
    OnecDocument,
    RateSnapshot,
    Transaction,
    TxStatus,
    Wallet,
)
from connector.pipeline import TransactionPipeline
from connector.rates.base import Quote, RateSource
from connector.rates.service import RateService

USDT_RUB = Decimal(80)


class FakeRateSource(RateSource):
    source_name = "fake"

    async def get_quote(self, base: str, quote: str, as_of: datetime) -> Quote:
        rates = {("USDT", "USD"): Decimal(1), ("USD", "RUB"): Decimal(80)}
        try:
            rate = rates[(base.upper(), quote.upper())]
        except KeyError:
            raise LookupError(f"нет котировки {base}/{quote}") from None
        return Quote(base, quote, rate, as_of, self.source_name, {})


def dt(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 6, day, hour, tzinfo=timezone.utc)


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
    """Сеть TRON + USDT, кошелёк, контрагент с адресом, контракт, инвойс."""
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
    contract = Contract(
        counterparty_id=counterparty.id, number="ВЭД-1", currency="USD"
    )
    session.add(contract)
    await session.flush()
    invoice = Invoice(
        contract_id=contract.id,
        number="INV-1",
        amount=Decimal(1000),
        currency="USD",
        due_from=dt(1),
        due_to=dt(28),
    )
    session.add(invoice)
    await session.flush()
    return {
        "network": network,
        "asset": asset,
        "wallet": wallet,
        "counterparty": counterparty,
        "contract": contract,
        "invoice": invoice,
    }


def make_tx(world, *, direction: Direction, amount: str, tx_hash: str, day: int = 10):
    incoming = direction == Direction.IN
    return Transaction(
        network_id=world["network"].id,
        asset_id=world["asset"].id,
        wallet_id=world["wallet"].id,
        tx_hash=tx_hash,
        log_index=0,
        block_number=1000 + day,  # порядок обработки следует порядку в цепочке
        block_time=dt(day),
        direction=direction,
        from_address="TPartner" if incoming else "TMyWallet",
        to_address="TMyWallet" if incoming else "TPartner",
        amount=Decimal(amount),
        status=TxStatus.FINAL,
        confirmations=25,
        finalized_at=dt(day, 13),
        raw_response={},
        source="test",
    )


def pipeline_for(session) -> TransactionPipeline:
    return TransactionPipeline(session, RateService(primary=FakeRateSource()))


async def test_receipt_creates_lot_document_and_pays_invoice(session, world):
    tx = make_tx(world, direction=Direction.IN, amount="1000", tx_hash="aa")
    session.add(tx)
    await session.flush()

    docs = await pipeline_for(session).process_network(world["network"])

    [doc] = docs
    assert doc.doc_type == OnecDocType.RECEIPT
    assert doc.idempotency_key == "tron:aa:0:receipt"
    assert doc.payload["amount_rub"] == str(Decimal(1000) * USDT_RUB)
    assert doc.payload["allocations"][0]["invoice_ref"] == world["invoice"].id

    lot = await session.scalar(select(Lot).where(Lot.transaction_id == tx.id))
    assert lot.remaining == Decimal(1000)
    assert lot.unit_cost_rub == USDT_RUB

    snapshot = await session.scalar(
        select(RateSnapshot).where(RateSnapshot.transaction_id == tx.id)
    )
    assert snapshot.asset_to_rub == USDT_RUB
    assert snapshot.contract_currency == "USD"

    assert world["invoice"].status == InvoiceStatus.PAID
    match = await session.scalar(select(Match).where(Match.transaction_id == tx.id))
    assert match.state == MatchState.AUTO


async def test_disposal_consumes_lots_fifo_and_builds_document(session, world):
    # Исходящий платёж закрывает собственный инвойс поставщика.
    session.add(
        Invoice(
            contract_id=world["contract"].id,
            number="INV-OUT",
            amount=Decimal(400),
            currency="USD",
            due_from=dt(1),
            due_to=dt(28),
        )
    )
    incoming = make_tx(world, direction=Direction.IN, amount="1000", tx_hash="aa", day=5)
    session.add(incoming)
    await session.flush()
    pipeline = pipeline_for(session)
    await pipeline.process_network(world["network"])

    outgoing = make_tx(world, direction=Direction.OUT, amount="400", tx_hash="bb", day=15)
    session.add(outgoing)
    await session.flush()
    docs = await pipeline.process_network(world["network"])

    [doc] = docs
    assert doc.doc_type == OnecDocType.DISPOSAL
    assert Decimal(doc.payload["cost_rub"]) == Decimal(400) * USDT_RUB
    assert len(doc.payload["fifo_lines"]) == 1

    lot = await session.scalar(select(Lot).where(Lot.transaction_id == incoming.id))
    assert lot.remaining == Decimal(600)
    line = await session.scalar(
        select(DisposalLine).where(DisposalLine.transaction_id == outgoing.id)
    )
    assert line.quantity == Decimal(400)


async def test_reprocessing_is_idempotent(session, world):
    session.add(make_tx(world, direction=Direction.IN, amount="1000", tx_hash="aa"))
    await session.flush()
    pipeline = pipeline_for(session)

    first = await pipeline.process_network(world["network"])
    second = await pipeline.process_network(world["network"])

    assert len(first) == 1
    assert second == []
    docs = (await session.execute(select(OnecDocument))).scalars().all()
    lots = (await session.execute(select(Lot))).scalars().all()
    assert len(docs) == 1
    assert len(lots) == 1


async def test_unmatched_tx_waits_for_manual_review(session, world):
    tx = make_tx(world, direction=Direction.IN, amount="777", tx_hash="cc")
    tx.from_address = "TUnknownSender"
    session.add(tx)
    await session.flush()
    pipeline = pipeline_for(session)

    docs = await pipeline.process_network(world["network"])
    assert docs == []

    match = await session.scalar(select(Match).where(Match.transaction_id == tx.id))
    assert match.state == MatchState.PENDING

    # Оператор привязал транзакцию в панели — следующий цикл создаёт документ.
    match.state = MatchState.MANUAL
    match.counterparty_id = world["counterparty"].id
    match.contract_id = world["contract"].id
    await session.flush()

    docs = await pipeline.process_network(world["network"])
    [doc] = docs
    assert doc.doc_type == OnecDocType.RECEIPT
    assert doc.payload["allocations"][0]["counterparty_ref"] == world["counterparty"].id


async def test_disposal_deferred_when_lots_insufficient(session, world):
    session.add(
        Invoice(
            contract_id=world["contract"].id,
            number="INV-OUT",
            amount=Decimal(500),
            currency="USD",
            due_from=dt(1),
            due_to=dt(28),
        )
    )
    outgoing = make_tx(world, direction=Direction.OUT, amount="500", tx_hash="dd", day=10)
    session.add(outgoing)
    await session.flush()
    pipeline = pipeline_for(session)

    docs = await pipeline.process_network(world["network"])
    assert docs == []  # нет партий — выбытие отложено, не потеряно

    # Бэкфилл догрузил более раннее поступление — оба документа создаются,
    # причём поступление обрабатывается раньше по номеру блока.
    incoming = make_tx(world, direction=Direction.IN, amount="1000", tx_hash="ee", day=2)
    session.add(incoming)
    await session.flush()
    docs = await pipeline.process_network(world["network"])
    assert {d.doc_type for d in docs} == {OnecDocType.RECEIPT, OnecDocType.DISPOSAL}
