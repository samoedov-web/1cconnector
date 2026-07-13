"""Тесты правок по регламенту оплаты (specs/payment-regulation.md)."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.models import (
    Asset,
    Base,
    Contract,
    CounterpartyAddress,
    Direction,
    Lot,
    Network,
    Organization,
    Transaction,
    TxStatus,
    Wallet,
)
from connector.onec.sync import (
    ContractIn,
    CounterpartyIn,
    InvoiceIn,
    SyncIn,
    apply_sync,
    guess_network_code,
)
from connector.pipeline import TransactionPipeline
from connector.rates.service import RateService
from connector.reports.fns import fns_notification_data, fns_notification_xml
from connector.reports.render import render_html

from tests.test_pipeline import FakeRateSource

NONRESIDENT_ADDRESS = "TXyzNonResidentWallet111222333444"


def dt(day: int) -> datetime:
    return datetime(2026, 7, day, 12, tzinfo=timezone.utc)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def sync_batch() -> SyncIn:
    return SyncIn(
        counterparties=[CounterpartyIn(onec_ref="cp-1", name="Shanghai Trade Co.")],
        contracts=[ContractIn(
            onec_ref="c-1", counterparty_ref="cp-1", number="ВЭД-1",
            registration_number="26030012/1481/0000/9/1", currency="USD",
            kvvo="12060",
        )],
        invoices=[InvoiceIn(
            onec_ref="i-1", contract_ref="c-1", number="INV-1",
            amount=Decimal(1000), currency="USD",
            due_from=dt(1), due_to=dt(28),
            crypto_address=NONRESIDENT_ADDRESS,
        )],
    )


def test_network_guessed_by_address_format():
    assert guess_network_code("TXmVpin5vq5gdZsciyyjdZgKRUju4st1wM") == "tron"
    assert guess_network_code("0x" + "a" * 40) == "ethereum"
    assert guess_network_code("bc1qxyz") is None  # неизвестный формат — не добавляем


async def test_invoice_address_lands_in_counterparty_directory(session):
    """Шаги 1–2 регламента: адрес нерезидента известен ДО платежа."""
    session.add(Network(code="tron", name="TRON", finality_depth=19))
    await session.flush()
    await apply_sync(session, sync_batch())

    contract = await session.scalar(select(Contract))
    assert contract.kvvo == "12060"

    address = await session.scalar(select(CounterpartyAddress))
    assert address.address == NONRESIDENT_ADDRESS
    assert address.origin == "invoice"

    # Повторная синхронизация не плодит дублей адреса.
    await apply_sync(session, sync_batch())
    addresses = (await session.execute(select(CounterpartyAddress))).scalars().all()
    assert len(addresses) == 1


async def test_outgoing_payment_automatches_and_document_carries_kvvo(session):
    """Шаги 6–9: исходящий платёж на адрес из инвойса матчится без разбора,
    КВВО уходит в документ 1С."""
    session.add(Network(code="tron", name="TRON", finality_depth=19))
    await session.flush()
    await apply_sync(session, sync_batch())
    network = await session.scalar(select(Network))
    asset = Asset(network_id=network.id, symbol="USDT", contract_address="TUsdt", decimals=6)
    wallet = Wallet(network_id=network.id, address="TMyCompanyWallet")
    session.add_all([asset, wallet])
    await session.flush()
    # Исходящий платёж нерезиденту; партия ФИФО — напрямую (поступление
    # от неизвестного отправителя ждало бы ручного разбора и увело бы тест
    # от проверяемого сценария).
    incoming = Transaction(
        network_id=network.id, asset_id=asset.id, wallet_id=wallet.id,
        tx_hash="in1", log_index=0, block_number=1002, block_time=dt(2),
        direction=Direction.IN, from_address="TSomeone", to_address="TMyCompanyWallet",
        amount=Decimal(1000), status=TxStatus.ORPHANED, confirmations=0,
        raw_response={"id": "in1"}, source="test",
    )
    session.add(incoming)
    await session.flush()
    session.add(Lot(
        transaction_id=incoming.id, asset_id=asset.id, wallet_id=wallet.id,
        acquired_at=dt(2), quantity=Decimal(1000), remaining=Decimal(1000),
        unit_cost_rub=Decimal(80),
    ))
    session.add(Transaction(
        network_id=network.id, asset_id=asset.id, wallet_id=wallet.id,
        tx_hash="out1", log_index=0, block_number=1006, block_time=dt(6),
        direction=Direction.OUT, from_address="TMyCompanyWallet",
        to_address=NONRESIDENT_ADDRESS, amount=Decimal(1000),
        status=TxStatus.FINAL, confirmations=30, finalized_at=dt(6),
        raw_response={"id": "out1"}, source="test",
    ))
    await session.flush()

    pipeline = TransactionPipeline(session, RateService(primary=FakeRateSource()))
    docs = await pipeline.process_network(network)
    disposal = next(d for d in docs if d.payload["doc_type"] == "disposal")
    [alloc] = disposal.payload["allocations"]
    assert alloc["invoice_number"] == "INV-1"  # матч по адресу из инвойса
    assert alloc["kvvo"] == "12060"  # шаг 9 регламента


async def test_fns_notification_data_xml_and_html(session):
    """Шаг 11: уведомление ФНС со всеми полями регламента."""
    session.add(Network(code="tron", name="TRON", finality_depth=19))
    session.add(Organization(name="ООО «Вектор Трейд»", inn="7701234567", kpp="770101001"))
    await session.flush()
    await apply_sync(session, sync_batch())
    network = await session.scalar(select(Network))
    asset = Asset(network_id=network.id, symbol="USDT", contract_address="TUsdt", decimals=6)
    wallet = Wallet(network_id=network.id, address="TMyCompanyWallet")
    session.add_all([asset, wallet])
    await session.flush()
    tx = Transaction(
        network_id=network.id, asset_id=asset.id, wallet_id=wallet.id,
        tx_hash="payhash", log_index=0, block_number=1006, block_time=dt(6),
        direction=Direction.IN, from_address=NONRESIDENT_ADDRESS,
        to_address="TMyCompanyWallet", amount=Decimal(1000),
        status=TxStatus.FINAL, confirmations=30, finalized_at=dt(6),
        raw_response={"id": "payhash"}, source="test",
    )
    session.add(tx)
    await session.flush()
    await TransactionPipeline(session, RateService(primary=FakeRateSource())).process_network(network)

    data = await fns_notification_data(session, tx.id)
    assert data["organization"]["inn"] == "7701234567"
    assert data["organization"]["kpp"] == "770101001"
    assert data["operation"]["tx_hash"] == "payhash"
    assert data["basis"]["unk"] == "26030012/1481/0000/9/1"
    assert data["basis"]["kvvo"] == "12060"
    assert Decimal(data["valuation"]["amount_rub"]) == Decimal(80000)
    # Входящий платёж: регламент охватывает исходящие (вопрос 7.2 aml-спеки).
    assert data["aml"]["status"] == "not_required"

    xml = fns_notification_xml(data)
    assert "УведомлениеРасчетЦВ" in xml
    assert 'ИНН="7701234567"' in xml
    assert 'УНК="26030012/1481/0000/9/1"' in xml
    import xml.etree.ElementTree as ET
    ET.fromstring(xml)  # валидный XML

    html = render_html("fns_notification.html", data)
    assert "УВЕДОМЛЕНИЕ" in html
    assert "payhash" in html


async def test_fns_notification_unknown_tx(session):
    assert await fns_notification_data(session, 999) is None