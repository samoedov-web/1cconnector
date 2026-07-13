"""Фаза 2 aml-спеки: ExpectedPayment — автосоздание, скрининг, статусы."""

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from connector.aml.flow import InvalidTransition, transition
from connector.aml.mock_adapter import MockAmlAdapter
from connector.config import settings
from connector.models import (
    Alert,
    AmlScreening,
    Base,
    ExpectedPayment,
    ExpectedPaymentStatus as EPS,
    Network,
)
from connector.onec.sync import ContractIn, CounterpartyIn, InvoiceIn, SyncIn, apply_sync

CLEAN = "TCleanAddress11111111111111111111"
GREY = "TGreyAddress222222222222222222222"
DIRTY = "TDirtyAddress33333333333333333333"


def dt(day: int) -> datetime:
    return datetime(2026, 7, day, 12, tzinfo=timezone.utc)


@pytest.fixture
def adapter(tmp_path):
    path = tmp_path / "aml.json"
    path.write_text(json.dumps({
        "default_score": 5,
        "addresses": {
            GREY: {"risk_score": 50, "categories": ["mixer"]},
            DIRTY: {"risk_score": 85, "categories": ["sanctions"]},
        },
    }), encoding="utf-8")
    return MockAmlAdapter(str(path))


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Network(code="tron", name="TRON", finality_depth=19))
        await session.flush()
        yield session
    await engine.dispose()


def batch(address: str, invoice_ref: str = "i-1") -> SyncIn:
    return SyncIn(
        counterparties=[CounterpartyIn(onec_ref="cp-1", name="Shanghai Trade Co.")],
        contracts=[ContractIn(onec_ref="c-1", counterparty_ref="cp-1",
                              number="ВЭД-1", currency="USD", kvvo="12060")],
        invoices=[InvoiceIn(
            onec_ref=invoice_ref, contract_ref="c-1", number="INV-1",
            amount=Decimal(50000), currency="USD",
            due_from=dt(1), due_to=dt(28), crypto_address=address,
        )],
    )


# --- Автосоздание и скрининг при синхронизации (шаги 2–3 регламента) ------------


async def test_clean_address_approved_for_treasurer(session, adapter):
    await apply_sync(session, batch(CLEAN), aml_adapter=adapter)
    payment = await session.scalar(select(ExpectedPayment))
    assert payment.status == EPS.AML_APPROVED
    assert payment.amount == Decimal(50000)
    assert payment.network == "tron"
    assert payment.aml_screening_id is not None
    screening = await session.get(AmlScreening, payment.aml_screening_id)
    assert screening.risk_score == 5  # default_score мока


async def test_grey_address_needs_compliance_decision(session, adapter):
    await apply_sync(session, batch(GREY), aml_adapter=adapter)
    payment = await session.scalar(select(ExpectedPayment))
    assert payment.status == EPS.AML_REVIEW
    alert = await session.scalar(select(Alert))
    assert alert.severity == "warning"
    assert "средний риск" in alert.title


async def test_dirty_address_rejected_with_alert(session, adapter):
    await apply_sync(session, batch(DIRTY), aml_adapter=adapter)
    payment = await session.scalar(select(ExpectedPayment))
    assert payment.status == EPS.AML_REJECTED
    alert = await session.scalar(select(Alert))
    assert alert.severity == "error"
    assert alert.details["categories"] == ["sanctions"]


async def test_resync_is_idempotent_and_does_not_rescreen_decided(session, adapter):
    await apply_sync(session, batch(GREY), aml_adapter=adapter)
    await apply_sync(session, batch(GREY), aml_adapter=adapter)
    payments = (await session.execute(select(ExpectedPayment))).scalars().all()
    screenings = await session.scalar(select(func.count(AmlScreening.id)))
    assert len(payments) == 1  # не задвоился
    assert screenings == 1  # решённый статус не перескринивается синхронизацией


async def test_address_change_creates_new_payment_keeps_old(session, adapter):
    """Сценарий 1 регламента: высокий риск → нерезидент меняет адрес."""
    await apply_sync(session, batch(DIRTY), aml_adapter=adapter)
    await apply_sync(session, batch(CLEAN), aml_adapter=adapter)  # новый адрес
    payments = (
        (await session.execute(
            select(ExpectedPayment).order_by(ExpectedPayment.id))).scalars().all()
    )
    assert len(payments) == 2
    assert payments[0].status == EPS.AML_REJECTED  # доказательная база осталась
    assert payments[1].status == EPS.AML_APPROVED
    assert payments[1].to_address == CLEAN


async def test_provider_down_leaves_pending_and_alerts(session, tmp_path):
    down = MockAmlAdapter(health_override="down")
    await apply_sync(session, batch(CLEAN), aml_adapter=down)
    payment = await session.scalar(select(ExpectedPayment))
    assert payment.status == EPS.PENDING_AML  # отправка НЕ одобрена
    alert = await session.scalar(select(Alert))
    assert "недоступен" in alert.title

    # Провайдер ожил — повторная синхронизация дожимает скрининг.
    path = tmp_path / "aml.json"
    path.write_text(json.dumps({"default_score": 5, "addresses": {}}), encoding="utf-8")
    await apply_sync(session, batch(CLEAN), aml_adapter=MockAmlAdapter(str(path)))
    await session.refresh(payment)
    assert payment.status == EPS.AML_APPROVED


# --- Пороги и статусная машина -----------------------------------------------------


async def test_thresholds_configurable(session, adapter, monkeypatch):
    # Ужесточили политику: 5 больше не «чисто», а на разбор.
    monkeypatch.setattr(settings, "aml_approved_max_score", 0)
    await apply_sync(session, batch(CLEAN), aml_adapter=adapter)
    payment = await session.scalar(select(ExpectedPayment))
    assert payment.status == EPS.AML_REVIEW


def test_transition_validation():
    payment = ExpectedPayment(
        invoice_id=1, to_address=CLEAN, network="tron",
        amount=Decimal(1), currency="USD", tolerance=Decimal("0.005"),
        status=EPS.AML_REJECTED,
    )
    with pytest.raises(InvalidTransition, match="aml_rejected → sent"):
        transition(payment, EPS.SENT)

    payment.status = EPS.AML_APPROVED
    transition(payment, EPS.SENT)  # допустимый путь
    transition(payment, EPS.MATCHED)
    assert payment.status == EPS.MATCHED
