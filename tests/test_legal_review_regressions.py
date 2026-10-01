"""Regression tests for concrete findings from the legal/methodology review."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.accounting.revaluation import run_revaluation
from connector.aml.flow import ensure_expected_payment, link_outgoing_payments
from connector.aml.mock_adapter import MockAmlAdapter
from connector.config import settings
from connector.models import (
    Asset,
    Base,
    Contract,
    Counterparty,
    CounterpartyAddress,
    Direction,
    ExpectedPaymentStatus as EPS,
    Invoice,
    Lot,
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
from connector.reports.tax import tax_register_data


PARTNER = "TPartnerAddress111111111111111111"
UNK = "24070001/1481/0000/9/1"
KVVO = "12060"


def dt(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc)


class FakeRateSource(RateSource):
    source_name = "fake-cbr"

    async def get_quote(
        self,
        base: str,
        quote: str,
        as_of: datetime,
    ) -> Quote:
        pair = (base.upper(), quote.upper())

        if pair == ("USDT", "USD"):
            rate = Decimal("1")
        elif pair == ("USD", "RUB"):
            rate = Decimal("80") if as_of.day <= 15 else Decimal("100")
        else:
            raise LookupError(f"нет котировки {base}/{quote}")

        return Quote(
            base,
            quote,
            rate,
            as_of,
            self.source_name,
            {},
        )


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:"
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    async with factory() as db:
        yield db
        await db.rollback()

    await engine.dispose()


@pytest_asyncio.fixture
async def world(session):
    network = Network(
        code="tron",
        name="TRON",
        finality_depth=19,
    )
    session.add(network)
    await session.flush()

    asset = Asset(
        network_id=network.id,
        symbol="USDT",
        contract_address="TUsdt",
        decimals=6,
    )

    wallet = Wallet(
        network_id=network.id,
        address="TMyWallet",
    )

    counterparty = Counterparty(
        name="Shanghai Trade Co.",
    )

    session.add_all([
        asset,
        wallet,
        counterparty,
    ])
    await session.flush()

    session.add(
        CounterpartyAddress(
            counterparty_id=counterparty.id,
            network_id=network.id,
            address=PARTNER,
        )
    )

    contract = Contract(
        counterparty_id=counterparty.id,
        number="ВЭД-1",
        registration_number=UNK,
        currency="USD",
        kvvo=KVVO,
    )
    session.add(contract)
    await session.flush()

    return {
        "network": network,
        "asset": asset,
        "wallet": wallet,
        "counterparty": counterparty,
        "contract": contract,
    }


async def add_invoice(
    session,
    world,
    *,
    number="INV-1",
    amount="1000",
):
    invoice = Invoice(
        contract_id=world["contract"].id,
        number=number,
        amount=Decimal(amount),
        currency="USD",
        due_from=dt(1),
        due_to=dt(28),
        crypto_address=PARTNER,
    )
    session.add(invoice)
    await session.flush()
    return invoice


def make_tx(
    world,
    *,
    direction=Direction.OUT,
    amount="1000",
    tx_hash="out1",
    day=10,
):
    incoming = direction == Direction.IN

    return Transaction(
        network_id=world["network"].id,
        asset_id=world["asset"].id,
        wallet_id=world["wallet"].id,
        tx_hash=tx_hash,
        log_index=0,
        block_number=1000 + day,
        block_time=dt(day),
        direction=direction,
        from_address=PARTNER if incoming else "TMyWallet",
        to_address="TMyWallet" if incoming else PARTNER,
        amount=Decimal(amount),
        status=TxStatus.FINAL,
        confirmations=25,
        finalized_at=dt(day, 13),
        raw_response={
            "provider": "node-1",
            "tx": tx_hash,
        },
        source="node-1",
    )


def rate_pipeline(session):
    return TransactionPipeline(
        session,
        RateService(primary=FakeRateSource()),
    )


@pytest.mark.asyncio
async def test_expected_payment_tolerance_is_decimal_immediately(
    session,
    world,
    monkeypatch,
):
    monkeypatch.setattr(
        settings,
        "matching_amount_tolerance",
        "0.005",
    )

    invoice = await add_invoice(
        session,
        world,
        amount="1000",
    )

    payment = await ensure_expected_payment(
        session,
        invoice,
        "tron",
        MockAmlAdapter(""),
    )

    # Regression: до round-trip через БД поле не должно оставаться строкой.
    assert isinstance(payment.tolerance, Decimal)
    assert payment.tolerance == Decimal("0.005")
    assert payment.status == EPS.AML_APPROVED


@pytest.mark.asyncio
async def test_pipeline_is_idempotent_for_same_transaction(
    session,
    world,
):
    await add_invoice(
        session,
        world,
        amount="1000",
    )

    tx = make_tx(
        world,
        direction=Direction.IN,
        tx_hash="in-idem",
        day=5,
    )
    session.add(tx)
    await session.flush()

    pipeline = rate_pipeline(session)

    docs = await pipeline.process(tx)
    assert len(docs) == 1

    # Повторная обработка той же транзакции не создаёт дубль.
    assert await pipeline.process(tx) == []

    total = await session.scalar(
        select(func.count(OnecDocument.id))
    )
    assert total == 1

    doc = await session.scalar(
        select(OnecDocument)
    )
    assert doc.idempotency_key == "tron:in-idem:0:receipt"


@pytest.mark.asyncio
async def test_revaluation_never_enters_tax_register(
    session,
    world,
):
    await add_invoice(
        session,
        world,
        amount="100",
    )

    receipt = make_tx(
        world,
        direction=Direction.IN,
        amount="100",
        tx_hash="in-tax",
        day=5,
    )
    session.add(receipt)
    await session.flush()

    rates = RateService(
        primary=FakeRateSource()
    )

    pipeline = TransactionPipeline(
        session,
        rates,
    )

    await pipeline.process(receipt)

    created = await run_revaluation(
        session,
        rates,
        as_of=dt(20),
    )

    assert len(created) == 1
    assert created[0].difference_rub == Decimal("2000")

    register = await tax_register_data(
        session,
        dt(1),
        dt(28),
    )

    kinds = {
        row["kind"]
        for row in register["rows"]
    }

    assert kinds == {"receipt"}
    assert Decimal(
        register["totals"]["income_rub"]
    ) == Decimal("8000")
    assert Decimal(
        register["totals"]["expense_rub"]
    ) == Decimal("0")
    assert Decimal(
        register["totals"]["result_rub"]
    ) == Decimal("8000")


@pytest.mark.asyncio
async def test_disposal_document_contains_unk_kvvo_and_fifo(
    session,
    world,
):
    invoice = await add_invoice(
        session,
        world,
        amount="1000",
    )

    payment = await ensure_expected_payment(
        session,
        invoice,
        "tron",
        MockAmlAdapter(""),
    )

    assert payment.status == EPS.AML_APPROVED

    receipt = make_tx(
        world,
        direction=Direction.IN,
        amount="2000",
        tx_hash="in-disp",
        day=5,
    )
    session.add(receipt)
    await session.flush()

    session.add(
        Lot(
            transaction_id=receipt.id,
            asset_id=world["asset"].id,
            wallet_id=world["wallet"].id,
            acquired_at=dt(5),
            quantity=Decimal("2000"),
            remaining=Decimal("2000"),
            unit_cost_rub=Decimal("80"),
        )
    )

    outgoing = make_tx(
        world,
        amount="1000",
        tx_hash="out-disp",
        day=10,
    )
    session.add(outgoing)
    await session.flush()

    await link_outgoing_payments(
        session,
        [outgoing],
        "tron",
    )

    docs = await rate_pipeline(session).process(
        outgoing
    )

    disposal = next(
        doc
        for doc in docs
        if doc.doc_type == OnecDocType.DISPOSAL
    )

    payload = disposal.payload

    assert payload["tx_hash"] == "out-disp"
    assert payload["fifo_lines"]

    allocations = payload["allocations"]
    assert allocations

    allocation = allocations[0]

    assert (
        allocation["contract_registration_number"]
        == UNK
    )
    assert allocation["kvvo"] == KVVO
