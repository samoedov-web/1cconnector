"""Фаза 4 aml-спеки: связывание исходящих, «вне регламента», ре-скрининг."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.aml.flow import (
    link_outgoing_payments,
    rescreen_due,
    rescreen_known_addresses,
)
from connector.aml.mock_adapter import MockAmlAdapter
from connector.models import (
    Alert,
    AmlScreening,
    Asset,
    AuditLog,
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
    Transaction,
    TxStatus,
    Wallet,
)
from connector.pipeline import TransactionPipeline
from connector.rates.base import Quote, RateSource
from connector.rates.service import RateService

PARTNER = "TPartnerAddress111111111111111111"
STRANGER = "TStrangerAddress22222222222222222"
MY_COLD = "TMyColdWallet33333333333333333333"


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
    """TRON + USDT, два своих кошелька, контрагент с адресом, инвойс."""
    network = Network(code="tron", name="TRON", finality_depth=19)
    session.add(network)
    await session.flush()
    asset = Asset(network_id=network.id, symbol="USDT",
                  contract_address="TUsdt", decimals=6)
    wallet = Wallet(network_id=network.id, address="TMyWallet")
    cold = Wallet(network_id=network.id, address=MY_COLD)
    counterparty = Counterparty(name="Shanghai Trade Co.")
    session.add_all([asset, wallet, cold, counterparty])
    await session.flush()
    session.add(CounterpartyAddress(
        counterparty_id=counterparty.id, network_id=network.id, address=PARTNER,
    ))
    contract = Contract(counterparty_id=counterparty.id, number="ВЭД-1",
                        currency="USD")
    session.add(contract)
    await session.flush()
    invoice = Invoice(contract_id=contract.id, number="INV-1",
                      amount=Decimal(1000), currency="USD",
                      due_from=dt(1), due_to=dt(28), crypto_address=PARTNER)
    session.add(invoice)
    await session.flush()
    return {"network": network, "asset": asset, "wallet": wallet,
            "counterparty": counterparty, "contract": contract,
            "invoice": invoice}


def expected_payment(world, *, status=EPS.AML_APPROVED, amount="1000",
                     to_address=PARTNER) -> ExpectedPayment:
    return ExpectedPayment(
        invoice_id=world["invoice"].id, to_address=to_address, network="tron",
        amount=Decimal(amount), currency="USD", tolerance=Decimal("0.005"),
        status=status,
    )


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


# --- Связывание исходящей с одобренным ожиданием (шаги 5–6 регламента) ---------


async def test_approved_payment_linked_to_outgoing(session, world):
    payment = expected_payment(world)
    tx = make_tx(world)
    session.add_all([payment, tx])
    await session.flush()

    linked = await link_outgoing_payments(session, [tx], "tron")

    assert linked == [payment]
    assert payment.status == EPS.SENT
    assert payment.transaction_id == tx.id
    audit = await session.scalar(
        select(AuditLog).where(AuditLog.action == "aml_payment_sent")
    )
    assert audit.entity_id == str(payment.id)
    assert audit.details["tx_hash"] == "out1"
    assert await session.scalar(select(func.count(Alert.id))) == 0


async def test_amount_within_tolerance_still_links(session, world):
    payment = expected_payment(world)  # 1000 ± 0.5%
    tx = make_tx(world, amount="996")
    session.add_all([payment, tx])
    await session.flush()

    await link_outgoing_payments(session, [tx], "tron")
    assert payment.status == EPS.SENT


async def test_outgoing_without_expectation_alerts_out_of_regulation(session, world):
    tx = make_tx(world, to_address=STRANGER)
    session.add(tx)
    await session.flush()

    linked = await link_outgoing_payments(session, [tx], "tron")

    assert linked == []
    alert = await session.scalar(select(Alert))
    assert alert.severity == "error"
    assert "вне регламента" in alert.title
    assert alert.details["to_address"] == STRANGER


async def test_outgoing_to_rejected_address_alerts_with_statuses(session, world):
    """Грубое нарушение: казначей отправил на адрес с запретом комплаенса."""
    payment = expected_payment(world, status=EPS.AML_REJECTED)
    tx = make_tx(world)
    session.add_all([payment, tx])
    await session.flush()

    await link_outgoing_payments(session, [tx], "tron")

    assert payment.status == EPS.AML_REJECTED  # статус не тронут
    alert = await session.scalar(select(Alert))
    assert alert.severity == "error"
    assert alert.details["existing_statuses"] == ["aml_rejected"]


async def test_amount_mismatch_alerts_and_keeps_approval(session, world):
    payment = expected_payment(world)
    tx = make_tx(world, amount="700")  # вне ± 0.5%
    session.add_all([payment, tx])
    await session.flush()

    await link_outgoing_payments(session, [tx], "tron")

    assert payment.status == EPS.AML_APPROVED
    assert payment.transaction_id is None
    alert = await session.scalar(select(Alert))
    assert alert.severity == "warning"
    assert "сумма" in alert.title


async def test_internal_transfer_not_flagged(session, world):
    tx = make_tx(world, to_address=MY_COLD)
    session.add(tx)
    await session.flush()

    linked = await link_outgoing_payments(session, [tx], "tron")

    assert linked == []
    assert await session.scalar(select(func.count(Alert.id))) == 0


async def test_incoming_ignored(session, world):
    tx = make_tx(world, direction=Direction.IN, tx_hash="in1")
    session.add(tx)
    await session.flush()

    assert await link_outgoing_payments(session, [tx], "tron") == []
    assert await session.scalar(select(func.count(Alert.id))) == 0


# --- sent → matched через конвейер (шаг 6 регламента) --------------------------


async def test_pipeline_disposal_marks_payment_matched(session, world):
    # Партия под выбытие (ФИФО) + одобренное ожидание + исходящая.
    receipt = make_tx(world, direction=Direction.IN, amount="1500",
                      tx_hash="in1", day=5)
    session.add(receipt)
    await session.flush()
    session.add(Lot(transaction_id=receipt.id, asset_id=world["asset"].id,
                    wallet_id=world["wallet"].id, acquired_at=dt(5),
                    quantity=Decimal(1500), remaining=Decimal(1500),
                    unit_cost_rub=Decimal(80)))
    payment = expected_payment(world)
    tx = make_tx(world)
    session.add_all([payment, tx])
    await session.flush()

    await link_outgoing_payments(session, [tx], "tron")
    assert payment.status == EPS.SENT

    pipeline = TransactionPipeline(session, RateService(primary=FakeRateSource()))
    docs = await pipeline.process(tx)

    assert any(d.doc_type == OnecDocType.DISPOSAL for d in docs)
    assert payment.status == EPS.MATCHED

    # Повторный прогон идемпотентен и не ломает статусную машину.
    assert await pipeline.process(tx) == []
    assert payment.status == EPS.MATCHED


# --- Ре-скрининг по расписанию --------------------------------------------------


def rescreen_adapter(tmp_path, score: int) -> MockAmlAdapter:
    path = tmp_path / f"aml-{score}.json"
    path.write_text(json.dumps({
        "default_score": 5,
        "addresses": {PARTNER: {"risk_score": score, "categories": ["mixer"]}},
    }), encoding="utf-8")
    return MockAmlAdapter(str(path))


async def test_rescreen_score_change_alerts_and_revokes_approval(
    session, world, tmp_path
):
    payment = expected_payment(world)
    session.add(payment)
    # Прошлый скрининг: адрес был чистым.
    await rescreen_known_addresses(session, rescreen_adapter(tmp_path, 5))
    assert await session.scalar(select(func.count(Alert.id))) == 0

    # Скор вырос до review — алерт + отзыв одобрения.
    screened = await rescreen_known_addresses(session, rescreen_adapter(tmp_path, 50))

    assert screened == 1
    alert = await session.scalar(select(Alert))
    assert "скор адреса изменился" in alert.title
    assert alert.details["old_score"] == 5
    assert alert.details["new_score"] == 50
    assert payment.status == EPS.EXPIRED
    audit = await session.scalar(
        select(AuditLog).where(AuditLog.action == "aml_approval_revoked")
    )
    assert audit.entity_id == str(payment.id)
    # История скринингов append-only: обе записи на месте.
    assert await session.scalar(select(func.count(AmlScreening.id))) == 2


async def test_rescreen_unchanged_score_is_silent(session, world, tmp_path):
    payment = expected_payment(world)
    session.add(payment)
    await rescreen_known_addresses(session, rescreen_adapter(tmp_path, 5))
    await rescreen_known_addresses(session, rescreen_adapter(tmp_path, 5))

    assert await session.scalar(select(func.count(Alert.id))) == 0
    assert payment.status == EPS.AML_APPROVED
    assert await session.scalar(select(func.count(AmlScreening.id))) == 2


async def test_rescreen_provider_down_alerts_and_stops(session, world):
    down = MockAmlAdapter(health_override="down")
    screened = await rescreen_known_addresses(session, down)

    assert screened == 0
    alert = await session.scalar(select(Alert))
    assert "ре-скрининг не выполнен" in alert.title


def test_rescreen_due_schedule():
    now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
    assert rescreen_due(None, now, 24)  # первый запуск — сразу
    assert not rescreen_due(now - timedelta(hours=23), now, 24)
    assert rescreen_due(now - timedelta(hours=24), now, 24)
    assert not rescreen_due(None, now, 0)  # 0 — отключён
