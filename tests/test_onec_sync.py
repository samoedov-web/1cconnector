"""Тесты синхронизации справочников из 1С и прохождения GUID до payload."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.models import (
    Asset,
    Base,
    Contract,
    Counterparty,
    CounterpartyAddress,
    Direction,
    Invoice,
    Network,
    Transaction,
    TxStatus,
    Wallet,
)
from connector.onec.sync import ContractIn, CounterpartyIn, InvoiceIn, SyncIn, apply_sync
from connector.pipeline import TransactionPipeline
from connector.rates.service import RateService

from tests.test_pipeline import FakeRateSource

CP_GUID = "a1b2c3d4-0000-0000-0000-000000000001"
CONTRACT_GUID = "a1b2c3d4-0000-0000-0000-000000000002"
INVOICE_GUID = "a1b2c3d4-0000-0000-0000-000000000003"


def dt(day: int) -> datetime:
    return datetime(2026, 6, day, tzinfo=timezone.utc)


def sync_batch(amount: str = "1000") -> SyncIn:
    return SyncIn(
        counterparties=[CounterpartyIn(onec_ref=CP_GUID, name="Shanghai Trade Co.")],
        contracts=[
            ContractIn(
                onec_ref=CONTRACT_GUID,
                counterparty_ref=CP_GUID,
                number="ВЭД-1",
                registration_number="26030012/1481/0000/9/1",
                currency="USD",
            )
        ],
        invoices=[
            InvoiceIn(
                onec_ref=INVOICE_GUID,
                contract_ref=CONTRACT_GUID,
                number="INV-1",
                amount=Decimal(amount),
                currency="USD",
                due_from=dt(1),
                due_to=dt(28),
            )
        ],
    )


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_sync_creates_linked_objects(session):
    counts = await apply_sync(session, sync_batch())
    assert counts == {"counterparties": 1, "contracts": 1, "invoices": 1}

    contract = await session.scalar(select(Contract).where(Contract.onec_ref == CONTRACT_GUID))
    counterparty = await session.get(Counterparty, contract.counterparty_id)
    assert counterparty.onec_ref == CP_GUID
    invoice = await session.scalar(select(Invoice).where(Invoice.onec_ref == INVOICE_GUID))
    assert invoice.contract_id == contract.id
    assert invoice.amount == Decimal(1000)


async def test_sync_is_idempotent_and_updates_fields(session):
    await apply_sync(session, sync_batch())
    invoice = await session.scalar(select(Invoice).where(Invoice.onec_ref == INVOICE_GUID))
    invoice.paid_amount = Decimal(400)  # оплату учёл коннектор

    await apply_sync(session, sync_batch(amount="1200"))  # 1С изменила сумму счёта

    invoices = (await session.execute(select(Invoice))).scalars().all()
    assert len(invoices) == 1  # upsert, не дубль
    assert invoices[0].amount == Decimal(1200)
    assert invoices[0].paid_amount == Decimal(400)  # оплата не затёрта
    counterparties = (await session.execute(select(Counterparty))).scalars().all()
    assert len(counterparties) == 1


async def test_sync_rejects_dangling_references(session):
    batch = SyncIn(
        contracts=[
            ContractIn(
                onec_ref=CONTRACT_GUID,
                counterparty_ref="missing-guid",
                number="ВЭД-1",
                currency="USD",
            )
        ]
    )
    with pytest.raises(HTTPException) as exc:
        await apply_sync(session, batch)
    assert exc.value.status_code == 400


async def test_guids_flow_into_document_payload(session):
    """Справочники из 1С → матчинг → в payload документа уходят GUID."""
    await apply_sync(session, sync_batch())
    network = Network(code="tron", name="TRON", finality_depth=19)
    session.add(network)
    await session.flush()
    asset = Asset(network_id=network.id, symbol="USDT", contract_address="TUsdt", decimals=6)
    wallet = Wallet(network_id=network.id, address="TMyWallet")
    session.add_all([asset, wallet])
    await session.flush()
    counterparty = await session.scalar(
        select(Counterparty).where(Counterparty.onec_ref == CP_GUID)
    )
    session.add(
        CounterpartyAddress(
            counterparty_id=counterparty.id, network_id=network.id, address="TPartner"
        )
    )
    session.add(
        Transaction(
            network_id=network.id,
            asset_id=asset.id,
            wallet_id=wallet.id,
            tx_hash="aa",
            log_index=0,
            block_number=1010,
            block_time=dt(10),
            direction=Direction.IN,
            from_address="TPartner",
            to_address="TMyWallet",
            amount=Decimal(1000),
            status=TxStatus.FINAL,
            confirmations=25,
            finalized_at=dt(10),
            raw_response={},
            source="test",
        )
    )
    await session.flush()

    pipeline = TransactionPipeline(session, RateService(primary=FakeRateSource()))
    [doc] = await pipeline.process_network(network)

    [alloc] = doc.payload["allocations"]
    assert alloc["counterparty_ref"] == CP_GUID
    assert alloc["contract_ref"] == CONTRACT_GUID
    assert alloc["invoice_ref"] == INVOICE_GUID
    assert alloc["counterparty_name"] == "Shanghai Trade Co."
    assert alloc["invoice_number"] == "INV-1"
