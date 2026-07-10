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
    registration_number: Mapped[str] = mapped_column(String(128), default="")  # учётный номер
    currency: Mapped[str] = mapped_column(String(8))  # валюта контракта: USD, EUR, CNY
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
