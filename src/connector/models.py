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
    Назначение оценки хранится для разделения: бухучёт / порог 115-ФЗ / регуляторный отчёт (ACC-18-053).
    """

    __tablename__ = "rate_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purpose: Mapped[str] = mapped_column(String(32))  # finality | revaluation
    valuation_purpose: Mapped[str] = mapped_column(String(64), default="accounting")  # accounting | aml_threshold | regulatory_report

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


class PaymentRoute(enum.StrEnum):
    """Маршрут проведения платежа (п. 1.4 ТЗ).

    direct               — прямой платёж нерезиденту (P2P)
    bank                 — через уполномоченный банк
    agent                — через агента-посредника
    exchange_organization — через лицензированную обменную организацию
    digital_depository   — через цифрового депозитария
    """

    DIRECT = "direct"
    BANK = "bank"
    AGENT = "agent"
    EXCHANGE_ORGANIZATION = "exchange_organization"
    DIGITAL_DEPOSITORY = "digital_depository"


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
    route: Mapped[PaymentRoute] = mapped_column(
        Enum(PaymentRoute, native_enum=False),
        default=PaymentRoute.DIRECT,
    )
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    invoice: Mapped[Invoice] = relationship()
    aml_screening: Mapped["AmlScreening | None"] = relationship()
    transaction: Mapped["Transaction | None"] = relationship()


# --- Обмен с 1С ------------------------------------------------------------


class BankComplianceProfileStatus(enum.StrEnum):
    """Статусная машина профиля банка (G5).
    
    active — профиль действует, может быть обновлён новой версией
    superseded — заменён новой версией (valid_to установлен)
    """
    
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class BankComplianceProfile(Base):
    """Профиль уполномоченного банка для обмена с 1С (G5, ACC-18-040..042).
    
    Определяет требования к документам:
    - requires_unc — требуется уникальный номер контракта (УНК)
    - requires_kvvo — требуется код вида валютной операции (КВВО)
    
    История версий: новая версия закрывает старшую датой valid_to;
    исторические операции сохраняют прежний профиль (ACC-18-041).
    """
    
    __tablename__ = "bank_compliance_profiles"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    # Наименование и версия профиля
    name: Mapped[str] = mapped_column(String(256))
    version: Mapped[str] = mapped_column(String(64), default="1.0")
    
    # Срок действия версии
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL для текущей версии
    
    # Статус
    status: Mapped[BankComplianceProfileStatus] = mapped_column(
        Enum(BankComplianceProfileStatus, native_enum=False),
        default=BankComplianceProfileStatus.ACTIVE,
    )
    
    # Требования к документам (ACC-18-040)
    requires_unc: Mapped[bool] = mapped_column(Boolean, default=False)  # УНК
    requires_kvvo: Mapped[bool] = mapped_column(Boolean, default=True)  # КВВО
    
    # Состав пакета шага 14 по маршруту (ACC-18-042)
    route_package_config: Mapped[dict] = mapped_column(JSON, default=dict)  # {route_type: [doc_types]}
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(128), default="system")


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
    
    # Ссылка на профиль банка, действовавший на момент создания (ACC-18-041)
    bank_profile_id: Mapped[int | None] = mapped_column(ForeignKey("bank_compliance_profiles.id"))
    
    bank_profile: Mapped["BankComplianceProfile | None"] = relationship()


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


class RegulatoryRuleStatus(enum.StrEnum):
    """Статусная машина регуляторного правила (ACC-18-023).
    
    draft → adopted → officially_published → effective → superseded
    Только effective меняет производственное поведение по умолчанию.
    """

    DRAFT = "draft"
    ADOPTED = "adopted"
    OFFICIALLY_PUBLISHED = "officially_published"
    EFFECTIVE = "effective"
    SUPERSEDED = "superseded"


class RegulatoryRule(Base):
    """Регуляторное правило отчётности (G3, ACC-18-020..025).
    
    Хранит порядок отчётности по операциям с ЦВ. Статус определяет
    применимость: только effective влияет на конвейер по умолчанию.
    effective_at — дата начала обязательного применения (ст. 12.1 173-ФЗ).
    """

    __tablename__ = "regulatory_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)  # идентификатор правила
    name: Mapped[str] = mapped_column(String(512))  # наименование
    description: Mapped[str] = mapped_column(Text, default="")
    
    status: Mapped[RegulatoryRuleStatus] = mapped_column(
        Enum(RegulatoryRuleStatus, native_enum=False),
        default=RegulatoryRuleStatus.DRAFT,
    )
    
    # Даты перехода статусов
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    officially_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Версия документа (порядка ЦБ/ФНС)
    version: Mapped[str] = mapped_column(String(64), default="")
    document_ref: Mapped[str] = mapped_column(String(256), default="")  # ссылка на документ
    
    # Формат отчётности
    xml_official: Mapped[bool] = mapped_column(Boolean, default=False)  # ACC-18-022
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ReportStatus(enum.StrEnum):
    """Статус регуляторного отчёта по операции (ACC-18-020, 025, 026).
    
    not_applicable — нет действующего применимого правила
    pending — правило effective и применимо, ждёт подтверждения передачи
    reported — подтверждена передача через официальный канал вне комплекса
    """

    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    REPORTED = "reported"


class AssetLegalCategory(enum.StrEnum):
    """Категория цифрового актива для целей учёта и отчётности (G4).
    
    digital_currency — цифровая валюта (ст. 282.3 НК РФ, без налоговой переоценки)
    foreign_digital_instrument — иностранный цифровой инструмент (отдельная стратегия)
    """
    
    DIGITAL_CURRENCY = "digital_currency"
    FOREIGN_DIGITAL_INSTRUMENT = "foreign_digital_instrument"


class AssetLegalProfileStatus(enum.StrEnum):
    """Статусная машина профиля актива (ACC-18-030).
    
    hypothesis — новый профиль, требует подтверждения
    approved_by_counsel — утверждён заключением консультанта
    superseded — заменён новой версией (valid_to установлен)
    """
    
    HYPOTHESIS = "hypothesis"
    APPROVED_BY_COUNSEL = "approved_by_counsel"
    SUPERSEDED = "superseded"


class AssetLegalProfile(Base):
    """Юридический профиль актива (G4, ACC-18-030..035).
    
    Определяет стратегию учёта для актива в конкретной сети:
    - категория (цифровая валюта vs иностранный цифровой инструмент)
    - налоговый режим
    - стратегия выбытия (ФИФО или иная)
    - юрисдикция и источник классификации
    
    История версий: новая версия закрывает старшую датой valid_to;
    история не переписывается (ACC-18-031).
    """
    
    __tablename__ = "asset_legal_profiles"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    # Ключевые поля состава профиля (ACC-18-035)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"))
    
    # Категория актива (ACC-18-034)
    category: Mapped[AssetLegalCategory] = mapped_column(
        Enum(AssetLegalCategory, native_enum=False),
    )
    
    # Юрисдикция и источник классификации
    jurisdiction: Mapped[str] = mapped_column(String(128), default="")
    source: Mapped[str] = mapped_column(String(256), default="")  # источник классификации
    
    # Срок действия версии (ACC-18-031)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL для текущей версии
    
    # Статус и заключение (ACC-18-030)
    status: Mapped[AssetLegalProfileStatus] = mapped_column(
        Enum(AssetLegalProfileStatus, native_enum=False),
        default=AssetLegalProfileStatus.HYPOTHESIS,
    )
    counsel_opinion_ref: Mapped[str] = mapped_column(String(256), default="")  # ссылка на заключение
    
    # Стратегии учёта (ACC-18-033, 034)
    fifo_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # ФИФО включён
    tax_regime: Mapped[str] = mapped_column(String(128), default="")  # налоговый режим
    revaluation_strategy: Mapped[str] = mapped_column(String(128), default="")  # стратегия переоценки
    
    # Ссылка на регуляторное правило (опционально)
    regulatory_rule_id: Mapped[int | None] = mapped_column(ForeignKey("regulatory_rules.id"))
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    
    asset: Mapped[Asset] = relationship()
    network: Mapped[Network] = relationship()
    regulatory_rule: Mapped["RegulatoryRule | None"] = relationship()


class Operation(Base):
    """Операция с цифровой валютой для регуляторной отчётности (G3).
    
    Связывает транзакцию с применённым регуляторным правилом.
    Сохраняет версию правила и его статус на момент обработки (ACC-18-024).
    Также хранит ссылку на профиль актива, действовавший на момент операции (G4, ACC-18-032).
    """

    __tablename__ = "operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))
    
    # Ссылка на применённое правило (может быть NULL если not_applicable)
    regulatory_rule_id: Mapped[int | None] = mapped_column(ForeignKey("regulatory_rules.id"))
    
    # Снимок состояния правила на момент обработки (ACC-18-024)
    regulatory_rule_version: Mapped[str] = mapped_column(String(64), default="")
    rule_status_at_processing: Mapped[str] = mapped_column(String(64), default="")
    
    # Ссылка на профиль актива, действовавший на момент операции (ACC-18-032)
    asset_legal_profile_id: Mapped[int | None] = mapped_column(ForeignKey("asset_legal_profiles.id"))
    
    # Статус отчётности
    report_status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, native_enum=False),
        default=ReportStatus.NOT_APPLICABLE,
    )
    
    # Дата операции (для проверки effective_at, ACC-18-025)
    operation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    
    # Подтверждение передачи (ACC-18-026)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_channel: Mapped[str] = mapped_column(String(128), default="")  # внешний канал
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    
    transaction: Mapped[Transaction] = relationship()
    regulatory_rule: Mapped["RegulatoryRule | None"] = relationship()
    asset_legal_profile: Mapped["AssetLegalProfile | None"] = relationship()


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
