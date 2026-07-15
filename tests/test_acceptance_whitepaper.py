"""Приёмочные тесты по whitepaper «Расчёты по ВЭД в цифровой валюте» (v1.1).

Независимая приёмка: каждое утверждение взято ИЗ ТЕКСТА whitepaper
(докстринг теста цитирует документ), а не из кода. Расхождение документа
и кода должно проявляться падающим тестом — ассерты под реализацию
не подгоняются.

Инфраструктура (SQLite in-memory, фикстуры, ASGI-клиент) заимствована
из существующих тестов; ожидаемые значения — только из whitepaper.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from connector.accounting.revaluation import run_revaluation
from connector.aml.flow import (
    InvalidTransition,
    aml_summary,
    check_sent_timeouts,
    ensure_expected_payment,
    expire_stale_approvals,
    link_outgoing_payments,
    rescreen_known_addresses,
    status_for_score,
    transition,
)
from connector.aml.mock_adapter import MockAmlAdapter
from connector.config import settings
from connector.db import get_session
from connector.main import app
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
    OnecDocument,
    Organization,
    Role,
    Transaction,
    TxStatus,
    User,
    Wallet,
)
from connector.pipeline import TransactionPipeline
from connector.rates.base import Quote, RateSource
from connector.rates.service import RateService
from connector.reports.fns import fns_notification_data, fns_notification_xml
from connector.reports.tax import tax_register_data
from connector.security import create_token, hash_password

PARTNER = "TPartnerAddress111111111111111111"
STRANGER = "TStrangerAddress22222222222222222"
UNK = "24070001/1481/0000/9/1"  # УНК контракта (шаг 1 регламента)
KVVO = "12060"


def dt(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)


class FakeRateSource(RateSource):
    """Курс для снимков: USDT→USD 1:1, USD→RUB 80 (после 15 июля — 100)."""

    source_name = "fake-cbr"

    async def get_quote(self, base: str, quote: str, as_of: datetime) -> Quote:
        pair = (base.upper(), quote.upper())
        if pair == ("USDT", "USD"):
            rate = Decimal(1)
        elif pair == ("USD", "RUB"):
            rate = Decimal(80) if as_of.day <= 15 else Decimal(100)
        else:
            raise LookupError(f"нет котировки {base}/{quote}")
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
    """TRON + USDT, свой кошелёк, контрагент с адресом, контракт с УНК/КВВО."""
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
                        registration_number=UNK, currency="USD", kvvo=KVVO)
    session.add(contract)
    await session.flush()
    return {"network": network, "asset": asset, "wallet": wallet,
            "counterparty": counterparty, "contract": contract}


async def add_invoice(session, world, *, number="INV-1", amount="1000",
                      address=PARTNER) -> Invoice:
    invoice = Invoice(contract_id=world["contract"].id, number=number,
                      amount=Decimal(amount), currency="USD",
                      due_from=dt(1), due_to=dt(28), crypto_address=address)
    session.add(invoice)
    await session.flush()
    return invoice


def scored_adapter(tmp_path, scores: dict[str, int]) -> MockAmlAdapter:
    """Мок-провайдер: адрес → скор; неизвестный адрес чист (скор 0)."""
    path = tmp_path / f"aml-{'-'.join(map(str, scores.values()))}.json"
    path.write_text(json.dumps({
        "default_score": 0,
        "addresses": {a: {"risk_score": s, "categories": ["mixer"]}
                      for a, s in scores.items()},
    }), encoding="utf-8")
    return MockAmlAdapter(str(path))


def make_tx(world, *, direction=Direction.OUT, amount="1000",
            to_address=PARTNER, tx_hash="out1", day=10,
            raw=None) -> Transaction:
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
        raw_response=raw if raw is not None else {"provider": "node-1",
                                                  "tx": tx_hash},
        source="node-1",
    )


def payment_row(world_invoice_id: int, *, status=EPS.AML_APPROVED,
                amount="1000", to_address=PARTNER) -> ExpectedPayment:
    return ExpectedPayment(
        invoice_id=world_invoice_id, to_address=to_address, network="tron",
        amount=Decimal(amount), currency="USD", tolerance=Decimal("0.005"),
        status=status,
    )


def rate_pipeline(session) -> TransactionPipeline:
    return TransactionPipeline(session, RateService(primary=FakeRateSource()))


# --- Раздел 5: статусная машина ожидаемого платежа ------------------------------


def test_status_machine_documented_transitions():
    """«pending_aml → aml_approved | aml_review | aml_rejected;
    aml_review → aml_approved | aml_rejected; aml_approved → sent → matched;
    aml_approved → expired → pending_aml (истечение 48 ч → повторная проверка)».
    """
    payment = payment_row(1, status=EPS.PENDING_AML)

    # pending_aml → aml_review → aml_approved → sent → matched
    transition(payment, EPS.AML_REVIEW)
    transition(payment, EPS.AML_APPROVED)
    transition(payment, EPS.SENT)
    transition(payment, EPS.MATCHED)
    assert payment.status == EPS.MATCHED

    # pending_aml → aml_rejected (прямой запрет по порогу)
    rejected = payment_row(2, status=EPS.PENDING_AML)
    transition(rejected, EPS.AML_REJECTED)
    assert rejected.status == EPS.AML_REJECTED

    # aml_approved → expired → pending_aml (повторная проверка)
    expired = payment_row(3, status=EPS.AML_APPROVED)
    transition(expired, EPS.EXPIRED)
    transition(expired, EPS.PENDING_AML)
    assert expired.status == EPS.PENDING_AML


def test_status_machine_invalid_transition_is_error():
    """«Каждый шаг фиксируется статусом с валидацией переходов — недопустимый
    переход является ошибкой, а не молчаливой порчей данных»."""
    rejected = payment_row(1, status=EPS.AML_REJECTED)
    with pytest.raises(Exception):
        transition(rejected, EPS.SENT)  # запрещённый адрес нельзя «отправить»
    assert rejected.status == EPS.AML_REJECTED  # статус не испорчен молча

    sent = payment_row(2, status=EPS.SENT)
    with pytest.raises(Exception):
        transition(sent, EPS.EXPIRED)  # sent ведёт только в matched

    matched = payment_row(3, status=EPS.MATCHED)
    with pytest.raises(Exception):
        transition(matched, EPS.PENDING_AML)  # терминальный статус

    # Ошибка — типизированная, пригодная для обработки, а не порча данных.
    assert issubclass(InvalidTransition, Exception)


# --- Раздел 6: пороги AML ---------------------------------------------------------


def test_risk_thresholds_0_30_70_100_and_configurable(monkeypatch):
    """«Оценка риска 0–100 отображается на конфигурируемые пороги:
    0–30 — одобрено к отправке; 31–70 — решение комплаенс-офицера;
    71–100 — отправка запрещена»."""
    # Границы порогов по умолчанию — ровно как в документе.
    assert status_for_score(0) == EPS.AML_APPROVED
    assert status_for_score(30) == EPS.AML_APPROVED
    assert status_for_score(31) == EPS.AML_REVIEW
    assert status_for_score(70) == EPS.AML_REVIEW
    assert status_for_score(71) == EPS.AML_REJECTED
    assert status_for_score(100) == EPS.AML_REJECTED

    # Пороги конфигурируемы: ужесточили политику — решения сместились.
    monkeypatch.setattr(settings, "aml_approved_max_score", 10)
    monkeypatch.setattr(settings, "aml_review_max_score", 50)
    assert status_for_score(30) == EPS.AML_REVIEW
    assert status_for_score(60) == EPS.AML_REJECTED


async def test_provider_unavailable_payment_stays_unapproved(session, world):
    """«Отказоустойчивость: при недоступности провайдера платёж остаётся
    непроверенным (отправка не одобряется) — деградация безопасна»."""
    invoice = await add_invoice(session, world)
    down = MockAmlAdapter(health_override="down")

    payment = await ensure_expected_payment(session, invoice, "tron", down)

    assert payment is not None
    assert payment.status != EPS.AML_APPROVED  # отправка НЕ одобрена
    assert payment.status == EPS.PENDING_AML  # остался непроверенным
    screenings = await session.scalar(select(func.count(AmlScreening.id)))
    assert screenings == 0  # проверка не состоялась и не сфальсифицирована


async def test_outgoing_without_approval_is_out_of_regulation_alert(session, world):
    """«Алерт „вне регламента": исходящая транзакция на адрес без действующего
    одобрения (в том числе на отклонённый комплаенсом адрес) немедленно
    фиксируется с тревожным сигналом и пометкой в карточке операции»."""
    invoice = await add_invoice(session, world)
    # Комплаенс отклонил адрес — а казначей всё равно отправил.
    rejected = payment_row(invoice.id, status=EPS.AML_REJECTED)
    tx_rejected = make_tx(world, tx_hash="rogue1")
    # И вторая исходящая — на адрес вообще без ожидаемого платежа.
    tx_stranger = make_tx(world, tx_hash="rogue2", to_address=STRANGER)
    session.add_all([rejected, tx_rejected, tx_stranger])
    await session.flush()

    linked = await link_outgoing_payments(
        session, [tx_rejected, tx_stranger], "tron")

    assert linked == []  # ни одна не признана регламентной
    alerts = (await session.execute(select(Alert))).scalars().all()
    assert len(alerts) == 2  # обе зафиксированы немедленно
    for alert in alerts:
        assert alert.severity == "error"  # тревожный сигнал
        assert "вне регламента" in alert.title
    # Пометка в карточке операции: AML-блок операции говорит о нарушении.
    card = await aml_summary(session, tx_rejected)
    assert card["status"] != "performed"
    assert "вне регламента" in card["note"]


async def test_approval_ttl_48h_expires_and_resync_rescreens(session, world):
    """«Одобрение действует 48 часов. По истечении срока одобрение отзывается
    (статус expired); очередная синхронизация инвойса автоматически запускает
    повторную проверку. Платёж, по которому казначей уже отметил отправку,
    не истекает»."""
    invoice = await add_invoice(session, world)
    adapter = MockAmlAdapter("")  # чистый адрес: скор 0
    payment = await ensure_expected_payment(session, invoice, "tron", adapter)
    assert payment.status == EPS.AML_APPROVED  # предусловие
    approved_at = payment.status_changed_at

    # Через 47 часов одобрение ещё действует.
    assert await expire_stale_approvals(
        session, now=approved_at + timedelta(hours=47)) == []
    assert payment.status == EPS.AML_APPROVED

    # Через 49 часов — отозвано (expired).
    expired = await expire_stale_approvals(
        session, now=approved_at + timedelta(hours=49))
    assert [p.id for p in expired] == [payment.id]
    assert payment.status == EPS.EXPIRED

    # Очередная синхронизация инвойса запускает повторную проверку:
    # новая запись журнала скринингов, адрес снова одобрен.
    again = await ensure_expected_payment(session, invoice, "tron", adapter)
    assert again.id == payment.id
    assert again.status == EPS.AML_APPROVED
    assert await session.scalar(select(func.count(AmlScreening.id))) == 2

    # Просроченное, но с отметкой казначея «Отправил» — не истекает.
    # (Повторно одобренному платежу даём свежую отметку времени, чтобы
    # он не попал под тот же смоделированный час X — инфраструктура теста.)
    again.status_changed_at = approved_at + timedelta(hours=49)
    invoice2 = await add_invoice(session, world, number="INV-2",
                                 address=STRANGER)
    marked = payment_row(invoice2.id, to_address=STRANGER)
    marked.status_changed_at = approved_at - timedelta(hours=100)
    marked.sent_marked_at = approved_at
    session.add(marked)
    await session.flush()
    assert await expire_stale_approvals(
        session, now=approved_at + timedelta(hours=49)) == []
    assert marked.status == EPS.AML_APPROVED


async def test_sent_timer_30_minutes_signals_treasurer(session, world):
    """«Таймер обнаружения 30 минут. Кнопка „Отправил" фиксирует момент отправки
    (read-only принцип сохраняется: статус платежа меняет только индексер по
    факту обнаружения). Если транзакция не найдена в течение 30 минут —
    казначей получает сигнал проверить операцию вручную»."""
    invoice = await add_invoice(session, world)
    payment = payment_row(invoice.id)
    marked_at = dt(10, 12)
    payment.sent_marked_at = marked_at
    payment.sent_marked_by = "trez"
    session.add(payment)
    await session.flush()

    # Отметка «Отправил» сама по себе статус не меняет (read-only принцип).
    assert payment.status == EPS.AML_APPROVED

    # 29 минут — рано, сигнала нет.
    assert await check_sent_timeouts(session, now=marked_at + timedelta(minutes=29)) == []
    assert await session.scalar(select(func.count(Alert.id))) == 0

    # 31 минута — транзакция не найдена: сигнал проверить вручную.
    overdue = await check_sent_timeouts(session, now=marked_at + timedelta(minutes=31))
    assert [p.id for p in overdue] == [payment.id]
    alert = await session.scalar(select(Alert))
    assert "вручную" in alert.title
    # Статус по-прежнему меняет только индексер — таймер его не трогает.
    assert payment.status == EPS.AML_APPROVED


# --- Раздел 4: конвейер обработки ---------------------------------------------------


async def test_pipeline_idempotent_no_duplicates(session, world):
    """«Конвейер идемпотентен: повторная обработка не создаёт дублей (контроль
    по ключу идемпотентности „сеть : хэш : индекс : тип документа")»."""
    await add_invoice(session, world, amount="1000")
    tx = make_tx(world, direction=Direction.IN, tx_hash="in-idem", day=5)
    session.add(tx)
    await session.flush()

    pipeline = rate_pipeline(session)
    docs = await pipeline.process(tx)
    assert len(docs) == 1

    # Повторная обработка той же транзакции — дублей нет.
    assert await pipeline.process(tx) == []
    total = await session.scalar(select(func.count(OnecDocument.id)))
    assert total == 1

    # Ключ идемпотентности — «сеть : хэш : индекс : тип документа».
    doc = await session.scalar(select(OnecDocument))
    assert doc.idempotency_key == "tron:in-idem:0:receipt"


async def test_fifo_disposal_line_by_line_and_shortage_postpones(session, world):
    """«Поступления цифровой валюты образуют партии; выбытия списываются по ФИФО
    с построчной расшифровкой (какая партия, сколько, по какой себестоимости).
    При нехватке остатка по партиям … выбытие откладывается, а не проводится
    с искажением»."""
    inv150 = await add_invoice(session, world, number="INV-150", amount="150")
    await add_invoice(session, world, number="INV-500", amount="500")
    receipt = make_tx(world, direction=Direction.IN, amount="200",
                      tx_hash="in-lots", day=3)
    session.add(receipt)
    await session.flush()
    lot1 = Lot(transaction_id=receipt.id, asset_id=world["asset"].id,
               wallet_id=world["wallet"].id, acquired_at=dt(3),
               quantity=Decimal(100), remaining=Decimal(100),
               unit_cost_rub=Decimal(70))
    lot2 = Lot(transaction_id=receipt.id, asset_id=world["asset"].id,
               wallet_id=world["wallet"].id, acquired_at=dt(4),
               quantity=Decimal(100), remaining=Decimal(100),
               unit_cost_rub=Decimal(90))
    session.add_all([lot1, lot2])
    payment = payment_row(inv150.id, amount="150")
    tx = make_tx(world, amount="150", tx_hash="out-fifo", day=10)
    session.add_all([payment, tx])
    await session.flush()
    await link_outgoing_payments(session, [tx], "tron")

    pipeline = rate_pipeline(session)
    docs = await pipeline.process(tx)
    disposal = next(d for d in docs if d.doc_type == OnecDocType.DISPOSAL)
    lines = disposal.payload["fifo_lines"]

    # Построчная расшифровка: какая партия, сколько, по какой себестоимости.
    assert len(lines) == 2
    assert lines[0]["lot_id"] == lot1.id  # первым пришёл — первым ушёл
    assert Decimal(lines[0]["quantity"]) == Decimal(100)
    assert Decimal(lines[0]["cost_rub"]) == Decimal(100) * Decimal(70)
    assert lines[1]["lot_id"] == lot2.id
    assert Decimal(lines[1]["quantity"]) == Decimal(50)
    assert Decimal(lines[1]["cost_rub"]) == Decimal(50) * Decimal(90)

    # Нехватка остатка (осталось 50, выбытие 500) — откладывается, не искажается.
    short = make_tx(world, amount="500", tx_hash="out-short", day=11)
    session.add(short)
    await session.flush()
    assert await pipeline.process(short) == []
    short_doc = await session.scalar(
        select(OnecDocument).where(OnecDocument.transaction_id == short.id)
    )
    assert short_doc is None
    assert lot2.remaining == Decimal(50)  # остатки не тронуты


# --- Раздел 7: учётная методология ---------------------------------------------------


async def test_revaluation_never_enters_tax_register(session, world):
    """«Налоговый регистр включает только реализованные операции … переоценка
    в налоговую базу не попадает ни при каких настройках комплекса»."""
    await add_invoice(session, world, amount="100")
    receipt = make_tx(world, direction=Direction.IN, amount="100",
                      tx_hash="in-tax", day=5)
    session.add(receipt)
    await session.flush()

    rates = RateService(primary=FakeRateSource())
    pipeline = TransactionPipeline(session, rates)
    await pipeline.process(receipt)  # партия 100 USDT по 80 ₽

    # Бухгалтерская переоценка на отчётную дату: курс вырос до 100 ₽.
    created = await run_revaluation(session, rates, as_of=dt(20))
    assert len(created) == 1  # переоценка реально состоялась
    assert created[0].difference_rub == Decimal(2000)

    register = await tax_register_data(session, dt(1), dt(28))

    # Только реализованные операции: поступление есть, переоценки нет.
    kinds = {row["kind"] for row in register["rows"]}
    assert kinds == {"receipt"}
    assert Decimal(register["totals"]["income_rub"]) == Decimal(8000)
    assert Decimal(register["totals"]["expense_rub"]) == Decimal(0)
    # Результат не содержит нереализованных +2000 ₽ переоценки.
    assert Decimal(register["totals"]["result_rub"]) == Decimal(8000)


async def test_disposal_document_carries_unk_kvvo_hash_fifo_aml(session, world):
    """Шаг 9 регламента: «Проект документа 1С „Выбытие цифровой валюты":
    рублёвая оценка, расшифровка ФИФО, УНК/КВВО, хэш, блок AML»."""
    invoice = await add_invoice(session, world, amount="1000")
    adapter = MockAmlAdapter("")  # чистый адрес
    payment = await ensure_expected_payment(session, invoice, "tron", adapter)
    assert payment.status == EPS.AML_APPROVED
    await session.refresh(payment)  # tolerance → Decimal, как между сессиями
    receipt = make_tx(world, direction=Direction.IN, amount="2000",
                      tx_hash="in-disp", day=5)
    session.add(receipt)
    await session.flush()
    session.add(Lot(transaction_id=receipt.id, asset_id=world["asset"].id,
                    wallet_id=world["wallet"].id, acquired_at=dt(5),
                    quantity=Decimal(2000), remaining=Decimal(2000),
                    unit_cost_rub=Decimal(80)))
    tx = make_tx(world, amount="1000", tx_hash="out-disp", day=10)
    session.add(tx)
    await session.flush()
    await link_outgoing_payments(session, [tx], "tron")

    docs = await rate_pipeline(session).process(tx)
    disposal = next(d for d in docs if d.doc_type == OnecDocType.DISPOSAL)
    payload = disposal.payload

    # Хэш транзакции.
    assert payload["tx_hash"] == "out-disp"
    # Рублёвая оценка выбытия (1000 USDT × 80 ₽).
    assert Decimal(payload["proceeds_rub"]) == Decimal(80000)
    # Расшифровка ФИФО.
    assert payload["fifo_lines"], "документ без расшифровки ФИФО"
    # Блок AML: проверка выполнялась, скор в документе.
    assert payload["aml"]["status"] == "performed"
    assert payload["aml"]["risk_score"] is not None
    # КВВО и УНК контракта — реквизиты валютного контроля из шага 9.
    serialized = json.dumps(payload, ensure_ascii=False)
    assert KVVO in serialized, "в документе выбытия нет КВВО"
    assert UNK in serialized, "в документе выбытия нет УНК"


async def test_fns_notification_carries_aml_block_and_primary_checksum(session, world):
    """Шаг 11 регламента: «Уведомление ФНС: печатная форма + XML под ТКС:
    операция, оценка, основание, блок AML, контрольная сумма первички»."""
    session.add(Organization(name="ООО «Импортёр»", inn="7701234567",
                             kpp="770101001"))
    invoice = await add_invoice(session, world, amount="1000")
    adapter = MockAmlAdapter("")
    payment = await ensure_expected_payment(session, invoice, "tron", adapter)
    await session.refresh(payment)  # tolerance → Decimal, как между сессиями
    receipt = make_tx(world, direction=Direction.IN, amount="2000",
                      tx_hash="in-fns", day=5)
    session.add(receipt)
    await session.flush()
    session.add(Lot(transaction_id=receipt.id, asset_id=world["asset"].id,
                    wallet_id=world["wallet"].id, acquired_at=dt(5),
                    quantity=Decimal(2000), remaining=Decimal(2000),
                    unit_cost_rub=Decimal(80)))
    raw = {"provider": "node-1", "tx": "out-fns", "block": 1010}
    tx = make_tx(world, amount="1000", tx_hash="out-fns", day=10, raw=raw)
    session.add(tx)
    await session.flush()
    await link_outgoing_payments(session, [tx], "tron")
    await rate_pipeline(session).process(tx)

    data = await fns_notification_data(session, tx.id)

    assert data is not None
    # Операция и оценка.
    assert data["operation"]["tx_hash"] == "out-fns"
    assert data["valuation"] is not None
    assert Decimal(data["valuation"]["amount_rub"]) == Decimal(80000)
    # Основание: инвойс/контракт/УНК/КВВО.
    assert data["basis"]["invoice"] == "INV-1"
    assert data["basis"]["unk"] == UNK
    assert data["basis"]["kvvo"] == KVVO
    # Блок AML — проверка адреса получателя выполнялась.
    assert data["aml"]["status"] == "performed"
    # Контрольная сумма первички: SHA-256 сырого ответа источника.
    checksum = data["immutability"]["raw_response_sha256"]
    assert len(checksum) == 64 and int(checksum, 16) is not None
    import hashlib
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    assert checksum == hashlib.sha256(canonical.encode()).hexdigest()

    # XML под ТКС несёт и AML, и контрольную сумму.
    xml = fns_notification_xml(data)
    assert "AML" in xml
    assert checksum in xml
    assert UNK in xml


# --- Раздел 6: непрерывный контроль и доказуемость -----------------------------------


async def test_rescreen_growth_revokes_approval_and_journal_is_append_only(
    session, world, tmp_path
):
    """«Ре-скрининг по расписанию: … рост риска порождает алерт, действующие
    одобрения на „подорожавший" адрес отзываются автоматически»; «журнал
    append-only: повторная проверка — новая запись, история не переписывается»."""
    invoice = await add_invoice(session, world)
    payment = payment_row(invoice.id)  # действующее одобрение на PARTNER
    session.add(payment)
    await session.flush()

    # Первая проверка: адрес чист.
    await rescreen_known_addresses(session, scored_adapter(tmp_path, {PARTNER: 5}))
    first = await session.scalar(select(AmlScreening).order_by(AmlScreening.id))
    assert first.risk_score == 5

    # Рост риска: 5 → 85.
    await rescreen_known_addresses(session, scored_adapter(tmp_path, {PARTNER: 85}))

    # Алерт о росте риска.
    alerts = (await session.execute(select(Alert))).scalars().all()
    assert alerts, "рост риска не породил алерт"
    # Действующее одобрение отозвано автоматически.
    assert payment.status != EPS.AML_APPROVED
    # Журнал append-only: обе записи на месте, история не переписана.
    rows = (
        (await session.execute(select(AmlScreening).order_by(AmlScreening.id)))
        .scalars().all()
    )
    assert len(rows) == 2
    assert rows[0].id == first.id and rows[0].risk_score == 5  # не тронута
    assert rows[1].risk_score == 85


async def test_compliance_decision_recorded_in_audit_log(monkeypatch):
    """«Решение комплаенса фиксируется с комментарием в журнале аудита»;
    шаг 4 регламента: «По среднему риску — одобрить/отклонить с комментарием;
    решение в журнале аудита»."""
    secret = "acceptance-secret"
    monkeypatch.setattr(settings, "secret_key", secret)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        async with factory() as session:
            officer = User(username="comp", password_hash=hash_password("x" * 8),
                           role=Role.COMPLIANCE)
            session.add(officer)
            payment = ExpectedPayment(
                invoice_id=1, to_address=PARTNER, network="tron",
                amount=Decimal(1000), currency="USD",
                tolerance=Decimal("0.005"), status=EPS.AML_REVIEW,
            )
            session.add(payment)
            await session.commit()
            officer_id, payment_id = officer.id, payment.id

        note = "Проверено по спискам вручную, контрагент известен с 2024 года"
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            r = await client.post(
                f"/api/v1/aml/payments/{payment_id}/decide",
                json={"action": "approve", "note": note},
                headers={"Authorization": "Bearer " + create_token(
                    officer_id, Role.COMPLIANCE, secret, 3600)},
            )
        assert r.status_code == 200

        async with factory() as session:
            payment = await session.get(ExpectedPayment, payment_id)
            assert payment.status == EPS.AML_APPROVED  # решение применено
            audit = await session.scalar(
                select(AuditLog).where(AuditLog.entity_id == str(payment_id))
            )
            assert audit is not None, "решение комплаенса не попало в аудит"
            assert audit.actor == "comp"  # кто принял решение
            # Комментарий решения — в журнале аудита.
            assert note in json.dumps(audit.details, ensure_ascii=False) + audit.note
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
