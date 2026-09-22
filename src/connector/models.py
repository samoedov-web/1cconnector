"""Доменная модель.

Ключевые инварианты (п. 1 ТЗ):
- первичка неизменяема: у транзакции хранятся сырой ответ ноды, хэш и метка
  времени получения; строки Transaction/RateSnapshot после финальности не
  редактируются, все действия пользователей — в AuditLog;
- дедупликация транзакций по (network, tx_hash, log_index);
- приватные ключи не хранятся нигде и никогда.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

AMOUNT = Numeric(38, 18)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --- Справочники -----------------------------------------------------------


class Network(Base):
    """Блокчейн-сеть. finality_depth — порог N для confirmed(N) → final."""

    __tablename__ = "networks"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)  # "tron", "ethereum"
    name: Mapped[str] = mapped_column(String(128))
    finality_depth: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Asset(Base):
    """Криптоактив (токен) в конкретной сети."""

    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("network_id", "contract_address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"))
    symbol: Mapped[str] = mapped_column(String(32))  # USDT, USDC
    contract_address: Mapped[str] = mapped_column(String(128))
    decimals: Mapped[int] = mapped_column(Integer)

    network: Mapped[Network] = relationship()


class Organization(Base):
    """Юридическое лицо клиента (лицензионный лимит тарифа).

    Кошельки привязываются к юрлицу; для баз, созданных до появления
    сущности, organization_id у кошелька пуст — считается основным юрлицом.
    """

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    inn: Mapped[str] = mapped_column(String(12), default="")
    kpp: Mapped[str] = mapped_column(String(9), default="")  # для уведомления ФНС
    onec_ref: Mapped[str] = mapped_column(String(64), default="")


class Wallet(Base):
    """Отслеживаемый адрес компании (свой кошелёк). Только публичный адрес."""

    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("network_id", "address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"))
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"))
    address: Mapped[str] = mapped_column(String(128))
    label: Mapped[str] = mapped_column(String(256), default="")
    backfill_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Курсор синхронизации: досюда история уже прочитана. Следующий опрос
    # начинается с (курсор − запас на реорг), а не с backfill_from заново.
    last_scanned_block: Mapped[int | None] = mapped_column(Integer)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    network: Mapped[Network] = relationship()


class Counterparty(Base):
    """Контрагент ВЭД. onec_ref — GUID справочника «Контрагенты» в 1С."""

    __tablename__ = "counterparties"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    onec_ref: Mapped[str] = mapped_column(String(64), default="")

    addresses: Mapped[list[CounterpartyAddress]] = relationship(back_populates="counterparty")


class CounterpartyAddress(Base):
    """Справочник адресов контрагента — правило автоматчинга №1."""

    __tablename__ = "counterparty_addresses"
    __table_args__ = (UniqueConstraint("network_id", "address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"))
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"))
    address: Mapped[str] = mapped_column(String(128))
    # Источник привязки: manual — оператор, learned — запомнено при ручном разборе.
    origin: Mapped[str] = mapped_column(String(16), default="manual")

    counterparty: Mapped[Counterparty] = relationship(back_populates="addresses")


class Contract(Base):
    """Контракт ВЭД: учётный номер для справок валютного контроля."""

    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"))
    number: Mapped[str] = mapped_column(String(128))
    registration_number: Mapped[str] = mapped_column(String(128), default="")  # УНК
    currency: Mapped[str] = mapped_column(String(8))  # валюта контракта: USD, EUR, CNY
    # Код вида валютной операции (справочник ЦБ, 181-И) — уходит в документ
    # 1С и уведомление ФНС (шаги 2, 9, 11 регламента оплаты).
    kvvo: Mapped[str] = mapped_column(String(8), default="")
    onec_ref: Mapped[str] = mapped_column(String(64), default="")

    counterparty: Mapped[Counterparty] = relationship()


class InvoiceStatus(enum.StrEnum):
    OPEN = "open"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    CANCELLED = "cancelled"


class Invoice(Base):
    """Инвойс по контракту — цель правил автоматчинга №2 и №3."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"))
    number: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(AMOUNT)
    currency: Mapped[str] = mapped_column(String(8))
    due_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, native_enum=False), default=InvoiceStatus.OPEN
    )
    paid_amount: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))
    onec_ref: Mapped[str] = mapped_column(String(64), default="")
    # Адрес криптокошелька нерезидента из инвойса (шаг 1 регламента) —
    # при синхронизации попадает в справочник адресов контрагента, и
    # автоматчинг знает адрес ДО платежа.
    crypto_address: Mapped[str] = mapped_column(String(128), default="")

    contract: Mapped[Contract] = relationship()


# --- Первичка --------------------------------------------------------------


class TxStatus(enum.StrEnum):
    SEEN = "seen"
    CONFIRMED = "confirmed"
    FINAL = "final"
    ORPHANED = "orphaned"  # выпала из цепочки после реорга


class Direction(enum.StrEnum):
    IN = "in"
    OUT = "out"


class Transaction(Base):
    """Импортированная транзакция — неизменяемая первичка.

    raw_response — сырой ответ ноды/провайдера на момент получения,
    received_at — метка времени получения (доказательная база, п. 1.3 ТЗ).
    """

    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("network_id", "tx_hash", "log_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"))

    tx_hash: Mapped[str] = mapped_column(String(128))
    log_index: Mapped[int] = mapped_column(Integer, default=0)
    block_number: Mapped[int] = mapped_column(Integer)
    block_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    direction: Mapped[Direction] = mapped_column(Enum(Direction, native_enum=False))
    from_address: Mapped[str] = mapped_column(String(128))
    to_address: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(AMOUNT)
    fee_amount: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))
    fee_asset: Mapped[str] = mapped_column(String(32), default="")  # TRX, ETH

    status: Mapped[TxStatus] = mapped_column(Enum(TxStatus, native_enum=False))
    confirmations: Mapped[int] = mapped_column(Integer, default=0)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    raw_response: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(64))  # провайдер, отдавший данные
    cross_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    network: Mapped[Network] = relationship()
    asset: Mapped[Asset] = relationship()
    wallet: Mapped[Wallet] = relationship()


class RateSnapshot(Base):
    """Снимок курса на момент финальности транзакции или дату переоценки.

    Пересчёт актив → валюта контракта → рубль с сохранением промежуточных
    значений и источника (п. 4 ТЗ). История неизменяема.
    """

    __tablename__ = "rate_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purpose: Mapped[str] = mapped_column(String(32))  # finality | revaluation

    asset_symbol: Mapped[str] = mapped_column(String(32))
    contract_currency: Mapped[str] = mapped_column(String(8))
    asset_to_contract: Mapped[Decimal] = mapped_column(AMOUNT)
    contract_to_rub: Mapped[Decimal] = mapped_column(AMOUNT)
    asset_to_rub: Mapped[Decimal] = mapped_column(AMOUNT)

    source_primary: Mapped[str] = mapped_column(String(128))
    source_details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- Матчинг ---------------------------------------------------------------


class MatchState(enum.StrEnum):
    AUTO = "auto"
    MANUAL = "manual"
    PENDING = "pending"  # очередь ручного разбора


class Match(Base):
    """Привязка транзакции к контрагенту/контракту/инвойсу.

    Одна транзакция может разноситься на несколько инвойсов (частичные
    оплаты/переплаты) — поэтому allocation-строки, а не поле в Transaction.
    """

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"))
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    allocated_amount: Mapped[Decimal] = mapped_column(AMOUNT)

    state: Mapped[MatchState] = mapped_column(Enum(MatchState, native_enum=False))
    rule: Mapped[str] = mapped_column(String(64), default="")  # какое правило сработало
    matched_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- Учёт (ФИФО) -----------------------------------------------------------


class Lot(Base):
    """Партия поступления ЦВ — основа ФИФО (п. 6 ТЗ)."""

    __tablename__ = "lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quantity: Mapped[Decimal] = mapped_column(AMOUNT)
    remaining: Mapped[Decimal] = mapped_column(AMOUNT)
    unit_cost_rub: Mapped[Decimal] = mapped_column(AMOUNT)  # себестоимость единицы, руб.


class DisposalLine(Base):
    """Списание из партии при выбытии (строка расшифровки ФИФО)."""

    __tablename__ = "disposal_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))  # выбытие
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"))
    quantity: Mapped[Decimal] = mapped_column(AMOUNT)
    cost_rub: Mapped[Decimal] = mapped_column(AMOUNT)


class Revaluation(Base):
    """Переоценка остатка ЦВ на отчётную дату (v1, п. 6 ТЗ).

    Себестоимость партий ФИФО не переписывается (историческая оценка нужна
    налоговому учёту при выбытии); переоценка — отдельный реестр и документ
    для бухучёта: 1С сторнирует предыдущую разницу и начисляет новую
    (в payload передаются и полная разница, и дельта к предыдущей).
    """

    __tablename__ = "revaluations"
    __table_args__ = (UniqueConstraint("asset_id", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quantity: Mapped[Decimal] = mapped_column(AMOUNT)  # остаток по партиям
    book_cost_rub: Mapped[Decimal] = mapped_column(AMOUNT)  # себестоимость ФИФО
    market_rub: Mapped[Decimal] = mapped_column(AMOUNT)  # оценка по курсу на дату
    difference_rub: Mapped[Decimal] = mapped_column(AMOUNT)  # market − book
    delta_rub: Mapped[Decimal] = mapped_column(AMOUNT)  # к предыдущей переоценке
    rate_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("rate_snapshots.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    asset: Mapped[Asset] = relationship()


# --- AML (specs/aml-adapter.md, фаза 1) --------------------------------------


class AmlScreening(Base):
    """Результат AML-скрининга адреса — неизменяемая первичка.

    История скринингов append-only (повторная проверка того же адреса —
    новая запись): доказательная база осмотрительности «мы проверяли и вот
    что видели на тот момент». raw + checksum — по схеме журнала
    неизменяемости; на PostgreSQL защищено триггерами.
    """

    __tablename__ = "aml_screenings"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64))  # какой провайдер
    network: Mapped[str] = mapped_column(String(32))
    address: Mapped[str] = mapped_column(String(128), index=True)
    risk_score: Mapped[int] = mapped_column(Integer)  # 0–100
    categories: Mapped[list] = mapped_column(JSON, default=list)
    provider_ref: Mapped[str] = mapped_column(String(128), default="")
    raw: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))  # SHA-256 raw
    screened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExpectedPaymentStatus(enum.StrEnum):
    """Статусная машина ожидаемого платежа (шаги 2–7 регламента).

    pending_aml → aml_approved | aml_review | aml_rejected
    aml_review  → aml_approved | aml_rejected   (решение комплаенса, фаза 3)
    aml_approved → sent → matched                (связывание, фаза 4)
    aml_approved → expired                       (срок одобрения — вопрос 7.1)
    """

    PENDING_AML = "pending_aml"
    AML_APPROVED = "aml_approved"
    AML_REVIEW = "aml_review"
    AML_REJECTED = "aml_rejected"
    SENT = "sent"
    MATCHED = "matched"
    EXPIRED = "expired"


class ExpectedPayment(Base):
    """Ожидаемый исходящий платёж по инвойсу (шаг 2 регламента).

    Создаётся автоматически из инвойса с адресом кошелька нерезидента;
    AML-проверка адреса — до отправки средств. Замена адреса нерезидентом
    (сценарий 1 регламента) — новая запись: прежняя с aml_rejected остаётся
    доказательной базой, уникальность — (invoice_id, to_address).
    """

    __tablename__ = "expected_payments"
    __table_args__ = (UniqueConstraint("invoice_id", "to_address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    to_address: Mapped[str] = mapped_column(String(128))
    network: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(AMOUNT)  # сумма инвойса
    currency: Mapped[str] = mapped_column(String(8))
    tolerance: Mapped[Decimal] = mapped_column(AMOUNT)  # доля, из настроек
    status: Mapped[ExpectedPaymentStatus] = mapped_column(
        Enum(ExpectedPaymentStatus, native_enum=False),
        default=ExpectedPaymentStatus.PENDING_AML,
    )
    aml_screening_id: Mapped[int | None] = mapped_column(ForeignKey("aml_screenings.id"))
    # Исходящая транзакция, закрывшая ожидание (шаги 5–6 регламента):
    # индексер связывает по адресу и сумме ± tolerance → статус sent.
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    # Решение комплаенс-офицера по aml_review (шаг 4 регламента);
    # полная запись решения — в аудит-логе.
    decided_by: Mapped[str] = mapped_column(String(128), default="")
    decision_note: Mapped[str] = mapped_column(Text, default="")
    # Отметка казначея «отправил» (решение владельца по сценарию 4):
    # точка отсчёта таймера «транзакция не обнаружена за N минут»;
    # сам статус меняет только индексер, обнаружив транзакцию.
    sent_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_marked_by: Mapped[str] = mapped_column(String(128), default="")
    # Алерт по таймеру уже поднят (чтобы не дублировать каждый цикл).
    sent_timeout_alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ожидаемый хэш внешней транзакции для детерминированного сопоставления
    expected_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    invoice: Mapped[Invoice] = relationship()
    aml_screening: Mapped["AmlScreening | None"] = relationship()
    transaction: Mapped["Transaction | None"] = relationship()


# --- Обмен с 1С ------------------------------------------------------------


class OnecDocType(enum.StrEnum):
    RECEIPT = "receipt"  # Поступление цифровой валюты
    DISPOSAL = "disposal"  # Выбытие цифровой валюты
    REVALUATION = "revaluation"  # Переоценка цифровой валюты
    FEE = "fee"  # Комиссия сети (отдельная статья расходов)


class OnecDocStatus(enum.StrEnum):
    DRAFT = "draft"  # сформирован, ждёт выгрузки
    EXPORTED = "exported"  # забран 1С
    ACKNOWLEDGED = "acked"  # 1С подтвердила создание документа


class OnecDocument(Base):
    """Проект документа 1С.

    idempotency_key = network:tx_hash:log_index:doc_type — повторная выгрузка
    не создаёт дублей (п. 8 ТЗ).
    """

    __tablename__ = "onec_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True)
    doc_type: Mapped[OnecDocType] = mapped_column(Enum(OnecDocType, native_enum=False))
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[OnecDocStatus] = mapped_column(
        Enum(OnecDocStatus, native_enum=False), default=OnecDocStatus.DRAFT
    )
    onec_ref: Mapped[str] = mapped_column(String(64), default="")  # GUID документа в 1С
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Безопасность и аудит ---------------------------------------------------


class Role(enum.StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"  # разбор нераспознанных транзакций
    AUDITOR = "auditor"  # только чтение
    COMPLIANCE = "compliance"  # решения по aml_review (шаг 4 регламента)
    TREASURER = "treasurer"  # видит «одобрено к отправке» (шаг 5 регламента)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Alert(Base):
    """Алерт мониторинга (п. 10 ТЗ): отставание, расхождение источников, сбои.

    Хранится всегда (виден в панели); при настроенном webhook дополнительно
    отправляется наружу (sent = доставлен).
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    severity: Mapped[str] = mapped_column(String(16))  # warning | error
    title: Mapped[str] = mapped_column(String(256))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    """Append-only журнал действий: кто привязал транзакцию, кто изменил правило."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128))
    entity: Mapped[str] = mapped_column(String(128))
    entity_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")


# =============================================================================
# STAGE 1 — CANONICAL v2.1 DATA FOUNDATION
# =============================================================================
# Канонические сущности для CryptoVED v2.1 (Whitepaper/TZ v2.1)
# READ-ONLY: никаких private keys, signing, broadcast


class RegulatoryVersionStatus(enum.StrEnum):
    """Статус регуляторной версии."""

    DRAFT = "draft"
    PUBLISHED = "published"
    EFFECTIVE = "effective"
    SUPERSEDED = "superseded"


class RegulatoryVersion(Base):
    """Версионирование применяемых регуляторных правил.

    Юридические/регуляторные значения не должны зашиваться в бизнес-код
    без версии. Все правила привязываются к конкретной версии.
    """

    __tablename__ = "regulatory_versions"
    __table_args__ = (UniqueConstraint("code", "effective_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64))  # например "KVVO-2024-09"
    source_document: Mapped[str] = mapped_column(String(512))  # название документа
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64))  # SHA-256 содержимого
    status: Mapped[RegulatoryVersionStatus] = mapped_column(
        Enum(RegulatoryVersionStatus, native_enum=False),
        default=RegulatoryVersionStatus.DRAFT,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # CHECK constraint: effective_to > effective_from если задан
    # Реализуется на уровне БД в миграции


class Operation(Base):
    """Бизнес-операция ВЭД — будущий владелец жизненного цикла.

    ONE OPERATION = ONE INVOICE.
    На Stage 1 только data model, lifecycle НЕ реализуется.
    """

    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("invoice_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    contract_ref: Mapped[str | None] = mapped_column(String(256))
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"))
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    network_id: Mapped[int | None] = mapped_column(ForeignKey("networks.id"))
    wallet_address: Mapped[str | None] = mapped_column(String(128))
    regulatory_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("regulatory_versions.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    invoice: Mapped[Invoice] = relationship()
    counterparty: Mapped[Counterparty | None] = relationship()
    asset: Mapped[Asset | None] = relationship()
    network: Mapped[Network | None] = relationship()
    regulatory_version: Mapped[RegulatoryVersion | None] = relationship()


class EvidenceLink(Base):
    """Связь Operation с доказательными материалами.

    Evidence history должна быть append-only по смыслу.
    На Stage 1 только data foundation, не full Evidence Vault.
    """

    __tablename__ = "evidence_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    evidence_type: Mapped[str] = mapped_column(String(64))  # blockchain_tx, registry_extract, etc.
    source: Mapped[str] = mapped_column(String(128))  # provider name
    source_reference: Mapped[str | None] = mapped_column(String(256))  # external ID
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    canonical_payload: Mapped[dict | None] = mapped_column(JSON)
    sha256_checksum: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    adapter_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    operation: Mapped[Operation] = relationship()

    __table_args__ = ()  # Индексы создаются в миграции


class ComplianceDecisionType(enum.StrEnum):
    """Тип compliance решения."""

    AML = "aml"
    REGISTRY = "registry"
    ISSUER = "issuer"


class ComplianceDecisionResult(enum.StrEnum):
    """Результат compliance решения."""

    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING = "pending"


class ComplianceDecision(Base):
    """Отдельная сущность решения compliance.

    НЕ смешивать AML, Registry, Issuer Risk в одну модель —
    это разные контрольные контуры.
    """

    __tablename__ = "compliance_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    decision_type: Mapped[ComplianceDecisionType] = mapped_column(
        Enum(ComplianceDecisionType, native_enum=False)
    )
    decision: Mapped[ComplianceDecisionResult] = mapped_column(
        Enum(ComplianceDecisionResult, native_enum=False)
    )
    decided_by: Mapped[str | None] = mapped_column(String(128))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason_note: Mapped[str | None] = mapped_column(Text)
    regulatory_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("regulatory_versions.id")
    )
    evidence_link_id: Mapped[int | None] = mapped_column(ForeignKey("evidence_links.id"))

    operation: Mapped[Operation] = relationship()
    regulatory_version: Mapped[RegulatoryVersion | None] = relationship()
    evidence_link: Mapped[EvidenceLink | None] = relationship()


class RegistrySnapshot(Base):
    """Снимок состояния участника/посредника/реестра."""

    __tablename__ = "registry_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    subject_reference: Mapped[str] = mapped_column(String(256))  # кто проверялся
    source: Mapped[str] = mapped_column(String(128))  # источник данных
    status_result: Mapped[str] = mapped_column(String(64))  # результат проверки
    snapshot_payload: Mapped[dict | None] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))  # SHA-256
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    regulatory_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("regulatory_versions.id")
    )

    operation: Mapped[Operation] = relationship()
    regulatory_version: Mapped[RegulatoryVersion | None] = relationship()


class IssuerRiskCheck(Base):
    """Отдельная проверка риска эмитента/актива."""

    __tablename__ = "issuer_risk_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    contract_address: Mapped[str | None] = mapped_column(String(128))
    network_id: Mapped[int | None] = mapped_column(ForeignKey("networks.id"))
    risk_status: Mapped[str] = mapped_column(String(64))  # low/medium/high/blocked
    source: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict | None] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    operation: Mapped[Operation] = relationship()
    asset: Mapped[Asset] = relationship()
    network: Mapped[Network | None] = relationship()


class ReviewTaskStatus(enum.StrEnum):
    """Статус задачи ручной проверки."""

    PENDING = "pending"
    ASSIGNED = "assigned"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class ReviewTask(Base):
    """Очередь человеческого рассмотрения бизнес/комплаенс исключений.

    НЕ превращать в универсальный контейнер технических ошибок.
    Provider outage / retry / adapter error не становятся автоматически
    бизнес-состояниями Operation.
    """

    __tablename__ = "review_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    task_type: Mapped[str] = mapped_column(String(64))  # compliance_exception, reconciliation, etc.
    status: Mapped[ReviewTaskStatus] = mapped_column(
        Enum(ReviewTaskStatus, native_enum=False),
        default=ReviewTaskStatus.PENDING,
    )
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # low/normal/high/urgent
    reason: Mapped[str] = mapped_column(Text)
    assigned_to: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)

    operation: Mapped[Operation] = relationship()

    # Индекс (status, priority) создаётся в миграции


class ReconciliationCase(Base):
    """Отдельная сущность для расхождений.

    Stage 1 создаёт модель. Reconciliation engine НЕ реализуется.
    """

    __tablename__ = "reconciliation_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"))
    case_type: Mapped[str] = mapped_column(String(64))  # amount_mismatch, missing_tx, etc.
    status: Mapped[str] = mapped_column(String(32), default="open")  # open/resolved/closed
    expected_reference: Mapped[str | None] = mapped_column(String(256))
    actual_reference: Mapped[str | None] = mapped_column(String(256))
    difference_details: Mapped[dict | None] = mapped_column(JSON)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(Text)

    operation: Mapped[Operation] = relationship()
