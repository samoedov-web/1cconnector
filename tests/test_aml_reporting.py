"""Фаза 5 aml-спеки: AML-результат в документе 1С, акте и уведомлении ФНС.

Аудит-цепочка шага 13 регламента: invoice → address → score → решение →
tx_hash → документ → уведомление — читается из payload документа, акта
по платежу и уведомления ФНС.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.aml.flow import link_outgoing_payments
from connector.models import (
    AmlScreening,
    Asset,
    Base,
    Contract,
    Counterparty,
    CounterpartyAddress,
    Direction,
    ExpectedPayment,
    ExpectedPaymentStatus as EPS,
    Invoice,
    Lot,
    Network,
    OnecDocType,
    Organization,
    Transaction,
    TxStatus,
    Wallet,
)
from connector.pipeline import TransactionPipeline
from connector.rates.base import Quote, RateSource
from connector.rates.service import RateService
from connector.reports.fns import fns_notification_data, fns_notification_xml
from connector.reports.render import render_html
from connector.reports.service import payment_act_data

PARTNER = "TPartnerAddress111111111111111111"
STRANGER = "TStrangerAddress22222222222222222"


def dt(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc)


class FakeRateSource(RateSource):
    source_name = "fake"

    async def get_quote(self, base: str, quote: str, as_of: datetime) -> Quote:
        rates = {("USDT", "USD"): Decimal(1), ("USD", "RUB"): Decimal(80)}
        try:
            rate = rates[(base.upper(), quote.upper())]
        except KeyError:
            raise LookupError(f"нет котировки {base}/{quote}") from None
        return Quote(base, quote, rate, as_of, self.source_name, {})


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
    """Организация, TRON + USDT, кошелёк, контрагент, инвойс, партия ФИФО."""
    session.add(Organization(name="ООО «Вектор Трейд»",
                             inn="7701234567", kpp="770101001"))
    network = Network(code="tron", name="TRON", finality_depth=19)
    session.add(network)
    await session.flush()
    asset = Asset(network_id=network.id, symbol="USDT",
                  contract_address="TUsdt", decimals=6)
    wallet = Wallet(network_id=network.id, address="TMyWallet")
    counterparty = Counterparty(name="Shanghai Trade Co.")
    session.add_all([asset, wallet, counterparty])
    await session.flush()
    session.add(CounterpartyAddress(
        counterparty_id=counterparty.id, network_id=network.id, address=PARTNER,
    ))
    contract = Contract(counterparty_id=counterparty.id, number="ВЭД-1",
                        currency="USD", kvvo="12060")
    session.add(contract)
    await session.flush()
    invoice = Invoice(contract_id=contract.id, number="INV-1",
                      amount=Decimal(1000), currency="USD",
                      due_from=dt(1), due_to=dt(28), crypto_address=PARTNER)
    session.add(invoice)
    await session.flush()

    receipt = make_tx({"network": network, "asset": asset, "wallet": wallet},
                      direction=Direction.IN, amount="1500",
                      tx_hash="in1", day=5)
    session.add(receipt)
    await session.flush()
    session.add(Lot(transaction_id=receipt.id, asset_id=asset.id,
                    wallet_id=wallet.id, acquired_at=dt(5),
                    quantity=Decimal(1500), remaining=Decimal(1500),
                    unit_cost_rub=Decimal(80)))
    await session.flush()
    return {"network": network, "asset": asset, "wallet": wallet,
            "invoice": invoice}


def make_tx(world, *, direction=Direction.OUT, amount="1000",
            to_address=PARTNER, tx_hash="out1", day: int = 10) -> Transaction:
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
        to_address="TMyWallet" if incoming else to_address,
        amount=Decimal(amount),
        status=TxStatus.FINAL,
        confirmations=25,
        finalized_at=dt(day, 13),
        raw_response={},
        source="test",
    )


async def linked_payment(session, world) -> tuple[ExpectedPayment, Transaction]:
    """Одобренное ожидание со скринингом + связанная исходящая."""
    screening = AmlScreening(
        source_id="mock-aml", network="tron", address=PARTNER,
        risk_score=42, categories=["mixer"], provider_ref="mock-1",
        raw={"provider": "mock"}, checksum="0" * 64, screened_at=dt(9),
    )
    session.add(screening)
    await session.flush()
    payment = ExpectedPayment(
        invoice_id=world["invoice"].id, to_address=PARTNER, network="tron",
        amount=Decimal(1000), currency="USD", tolerance=Decimal("0.005"),
        status=EPS.AML_APPROVED, aml_screening_id=screening.id,
        decided_by="comp", decision_note="контрагент проверен",
    )
    tx = make_tx(world)
    session.add_all([payment, tx])
    await session.flush()
    await link_outgoing_payments(session, [tx], "tron")
    return payment, tx


def pipeline_for(session) -> TransactionPipeline:
    return TransactionPipeline(session, RateService(primary=FakeRateSource()))


# --- Документ 1С (шаг 9 регламента) -------------------------------------------


async def test_disposal_document_carries_aml_block(session, world):
    payment, tx = await linked_payment(session, world)

    docs = await pipeline_for(session).process(tx)

    [doc] = [d for d in docs if d.doc_type == OnecDocType.DISPOSAL]
    aml = doc.payload["aml"]
    assert aml["status"] == "performed"
    assert aml["risk_score"] == 42
    assert aml["categories"] == ["mixer"]
    assert aml["provider"] == "mock-aml"
    assert aml["decided_by"] == "comp"
    # Документ несёт итоговый статус регламента: выбытие проведено → matched.
    assert aml["payment_status"] == "matched"
    assert aml["invoice_id"] == world["invoice"].id


# --- Акт по платежу и карточка (шаги 5, 13 регламента) --------------------------


async def test_payment_act_shows_performed_check(session, world):
    payment, tx = await linked_payment(session, world)
    await pipeline_for(session).process(tx)

    act = await payment_act_data(session, tx.id)
    assert act["aml"]["status"] == "performed"
    assert act["aml"]["risk_score"] == 42
    assert act["aml"]["decision_note"] == "контрагент проверен"

    html = render_html("payment_act.html", act)
    assert "Проверка AML" in html
    assert "проведена до отправки средств" in html
    assert "comp: контрагент проверен" in html
    assert "5. Доказательная база" in html  # нумерация сдвинулась


async def test_payment_act_flags_out_of_regulation(session, world):
    tx = make_tx(world, to_address=STRANGER, tx_hash="rogue")
    session.add(tx)
    await session.flush()

    act = await payment_act_data(session, tx.id)
    assert act["aml"]["status"] == "not_performed"
    assert "вне регламента" in act["aml"]["note"]

    html = render_html("payment_act.html", act)
    assert "не проведена" in html


async def test_incoming_payment_act_not_required(session, world):
    tx = make_tx(world, direction=Direction.IN, amount="200", tx_hash="in2",
                 day=6)
    session.add(tx)
    await session.flush()

    act = await payment_act_data(session, tx.id)
    assert act["aml"]["status"] == "not_required"
    # Раздел AML для входящих не печатается, нумерация прежняя.
    html = render_html("payment_act.html", act)
    assert "Проверка AML" not in html
    assert "4. Доказательная база" in html


# --- Уведомление ФНС (шаг 11 регламента) ---------------------------------------


async def test_fns_notification_carries_full_aml_block(session, world):
    payment, tx = await linked_payment(session, world)
    await pipeline_for(session).process(tx)

    data = await fns_notification_data(session, tx.id)
    assert data["aml"]["status"] == "performed"
    assert data["aml"]["risk_score"] == 42

    xml = fns_notification_xml(data)
    assert 'Статус="performed"' in xml
    assert 'Скор="42"' in xml
    assert 'Категории="mixer"' in xml
    assert 'РешениеКомплаенса="comp"' in xml
    import xml.etree.ElementTree as ET
    ET.fromstring(xml)  # валидный XML

    html = render_html("fns_notification.html", data)
    assert "проведена до отправки средств" in html
    assert "comp: контрагент проверен" in html


async def test_fns_notification_out_of_regulation_is_visible(session, world):
    tx = make_tx(world, to_address=STRANGER, tx_hash="rogue")
    session.add(tx)
    await session.flush()

    data = await fns_notification_data(session, tx.id)
    xml = fns_notification_xml(data)
    assert 'Статус="not_performed"' in xml
    assert "вне регламента" in xml
