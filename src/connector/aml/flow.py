"""Поток ожидаемых платежей: создание, скрининг, связывание (фазы 2 и 4).

Правила:
- ExpectedPayment создаётся из инвойса с адресом нерезидента при
  синхронизации из 1С; уникальность (invoice_id, to_address) — замена
  адреса создаёт новую запись, прежняя остаётся;
- скрининг — немедленно при создании (шаг 3 регламента: до отправки);
  статус по порогам (0–approved_max → aml_approved, до review_max →
  aml_review, выше → aml_rejected) — пороги в настройках;
- провайдер недоступен → статус остаётся pending_aml + алерт; повторная
  синхронизация того же инвойса повторяет попытку скрининга;
- переходы статусов валидируются: недопустимый переход — ошибка
  (InvalidTransition), а не молчаливая порча статуса;
- фаза 4: индексер связывает исходящую транзакцию с одобренным ожиданием
  (to_address + сумма ± tolerance) → sent; исходящая без approved-ожидания —
  алерт «отправка вне регламента»; ре-скрининг адресов по расписанию.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.alerts import send_alert
from connector.aml.base import AmlAdapter
from connector.aml.store import last_screening, store_screening
from connector.config import settings
from connector.models import (
    AmlScreening,
    AuditLog,
    CounterpartyAddress,
    Direction,
    ExpectedPayment,
    ExpectedPaymentStatus as EPS,
    Invoice,
    Network,
    PaymentRoute,
    Transaction,
    Wallet,
)

log = logging.getLogger("connector.aml")


def default_aml_adapter() -> AmlAdapter:
    """Провайдер скрининга из настроек (реестр aml-источников).

    Импорты регистрируют провайдеров; выбор — конфигурацией
    (aml_source_id: mock-aml | crystal), не кодом. Лишние ключи общего
    конфига каждая фабрика игнорирует.
    """
    import connector.aml.crystal_adapter  # noqa: F401
    import connector.aml.mock_adapter  # noqa: F401

    from connector.sources.registry import create_aml_source

    return create_aml_source(
        settings.aml_source_id,
        fixtures_path=settings.aml_fixtures_path,
        api_key=settings.aml_api_key,
        base_url=settings.aml_base_url,
    )

TRANSITIONS: dict[EPS, set[EPS]] = {
    EPS.PENDING_AML: {EPS.AML_APPROVED, EPS.AML_REVIEW, EPS.AML_REJECTED},
    EPS.AML_REVIEW: {EPS.AML_APPROVED, EPS.AML_REJECTED},
    EPS.AML_APPROVED: {EPS.SENT, EPS.EXPIRED},
    EPS.SENT: {EPS.MATCHED},
    EPS.AML_REJECTED: set(),
    EPS.MATCHED: set(),
    EPS.EXPIRED: {EPS.PENDING_AML},  # повторный скрининг после истечения
}


class InvalidTransition(Exception):
    pass


def transition(payment: ExpectedPayment, new_status: EPS) -> None:
    """Смена статуса с валидацией по статусной машине."""
    if new_status not in TRANSITIONS[payment.status]:
        raise InvalidTransition(
            f"Переход {payment.status.value} → {new_status.value} недопустим "
            f"(ожидаемый платёж {payment.id})"
        )
    payment.status = new_status
    payment.status_changed_at = datetime.now(timezone.utc)


def status_for_score(risk_score: int) -> EPS:
    """Статус по порогам регламента (шаг 3); пороги конфигурируемы."""
    if risk_score <= settings.aml_approved_max_score:
        return EPS.AML_APPROVED
    if risk_score <= settings.aml_review_max_score:
        return EPS.AML_REVIEW
    return EPS.AML_REJECTED


async def ensure_expected_payment(
    session: AsyncSession,
    invoice: Invoice,
    network_code: str,
    adapter: AmlAdapter,
    route: PaymentRoute = PaymentRoute.DIRECT,
) -> ExpectedPayment | None:
    """Создать ожидаемый платёж по инвойсу и проверить адрес (шаги 2–3).

    Идемпотентно: существующая запись (invoice, адрес) не пересоздаётся;
    для застрявшей в pending_aml (провайдер был недоступен) — повторная
    попытка скрининга.
    """
    if not invoice.crypto_address:
        return None
    payment = await session.scalar(
        select(ExpectedPayment).where(
            ExpectedPayment.invoice_id == invoice.id,
            ExpectedPayment.to_address == invoice.crypto_address,
        )
    )
    if payment is None:
        payment = ExpectedPayment(
            invoice_id=invoice.id,
            to_address=invoice.crypto_address,
            network=network_code,
            amount=invoice.amount,
            currency=invoice.currency,
            tolerance=settings.matching_amount_tolerance,
            route=route,
        )
        session.add(payment)
        await session.flush()
    if payment.status == EPS.EXPIRED:
        # Одобрение истекло (48 ч, решение владельца) — синхронизация
        # инвойса запускает проверку заново.
        transition(payment, EPS.PENDING_AML)
    if payment.status != EPS.PENDING_AML:
        return payment  # уже проверен (или решён комплаенсом)

    try:
        result = await adapter.screen_address(network_code, invoice.crypto_address)
    except Exception as exc:  # noqa: BLE001 — провайдер внешний
        log.warning("AML-провайдер недоступен для %s: %s", invoice.crypto_address, exc)
        await send_alert(
            session, "warning",
            "AML-провайдер недоступен — ожидаемый платёж не проверен",
            {"invoice": invoice.number, "address": invoice.crypto_address,
             "error": str(exc)},
        )
        return payment  # остаётся pending_aml, USDT не одобрены к отправке

    screening = await store_screening(session, adapter.source_name, result)
    payment.aml_screening_id = screening.id
    new_status = status_for_score(result.risk_score)
    transition(payment, new_status)
    if new_status != EPS.AML_APPROVED:
        await send_alert(
            session,
            "error" if new_status == EPS.AML_REJECTED else "warning",
            f"AML: адрес получателя по инвойсу {invoice.number} — "
            + ("высокий риск, отправка запрещена"
               if new_status == EPS.AML_REJECTED
               else "средний риск, требуется решение комплаенса"),
            {"address": invoice.crypto_address, "risk_score": result.risk_score,
             "categories": list(result.categories)},
        )
    await session.flush()
    return payment


# --- Фаза 4: связывание исходящих --------------------------------------------


def amount_within_tolerance(expected: ExpectedPayment, amount) -> bool:
    """Сумма транзакции в пределах ожидания ± tolerance (доля от суммы)."""
    delta = expected.amount * expected.tolerance
    return expected.amount - delta <= amount <= expected.amount + delta


async def link_outgoing_payments(
    session: AsyncSession, txs: list[Transaction], network_code: str
) -> list[ExpectedPayment]:
    """Связать исходящие транзакции с одобренными ожидаемыми платежами.

    Шаги 5–6 регламента: казначей отправил USDT → индексер увидел исходящую
    и связывает её с ExpectedPayment по (to_address, сумма ± tolerance) →
    статус sent. Нарушения регламента — алерты:
    - исходящая на адрес без approved-ожидания → error «отправка вне
      регламента» (в т.ч. на адрес с aml_rejected — это грубое нарушение);
    - ожидание есть, но сумма вне tolerance → warning, ожидание не связывается.
    Перевод на собственный кошелёк (внутреннее перемещение) не проверяется.
    """
    outgoing = [t for t in txs if t.direction == Direction.OUT]
    if not outgoing:
        return []
    own_addresses = {
        addr.lower()
        for addr in (
            await session.execute(
                select(Wallet.address)
                .join(Network, Wallet.network_id == Network.id)
                .where(Network.code == network_code)
            )
        ).scalars()
    }
    linked: list[ExpectedPayment] = []
    for tx in outgoing:
        if tx.to_address.lower() in own_addresses:
            continue  # внутреннее перемещение между своими кошельками
        candidates = (
            (
                await session.execute(
                    select(ExpectedPayment)
                    .where(
                        ExpectedPayment.to_address == tx.to_address,
                        ExpectedPayment.network == network_code,
                    )
                    .order_by(ExpectedPayment.created_at, ExpectedPayment.id)
                )
            )
            .scalars()
            .all()
        )
        approved = [p for p in candidates if p.status == EPS.AML_APPROVED]
        match = next(
            (p for p in approved if amount_within_tolerance(p, tx.amount)), None
        )
        if match is not None:
            transition(match, EPS.SENT)
            match.transaction_id = tx.id
            session.add(AuditLog(
                actor="indexer",
                action="aml_payment_sent",
                entity="expected_payment",
                entity_id=str(match.id),
                details={"tx_hash": tx.tx_hash, "amount": str(tx.amount),
                         "to_address": tx.to_address},
            ))
            linked.append(match)
            continue
        if approved:
            await send_alert(
                session, "warning",
                "AML: сумма исходящей не совпадает с одобренным ожиданием",
                {"tx_hash": tx.tx_hash, "amount": str(tx.amount),
                 "expected": [str(p.amount) for p in approved],
                 "to_address": tx.to_address},
            )
        else:
            await send_alert(
                session, "error",
                "Отправка вне регламента: исходящая без одобренного ожидания",
                {"tx_hash": tx.tx_hash, "amount": str(tx.amount),
                 "to_address": tx.to_address,
                 "existing_statuses": [p.status.value for p in candidates]},
            )
    await session.flush()
    return linked


async def mark_matched(session: AsyncSession, tx: Transaction) -> None:
    """sent → matched: конвейер провёл выбытие по связанной транзакции."""
    payment = await session.scalar(
        select(ExpectedPayment).where(
            ExpectedPayment.transaction_id == tx.id,
            ExpectedPayment.status == EPS.SENT,
        )
    )
    if payment is not None:
        transition(payment, EPS.MATCHED)


# --- Решения владельца (2026-07-14): срок одобрения и таймер отправки ----------


async def expire_stale_approvals(
    session: AsyncSession, now: datetime | None = None
) -> list[ExpectedPayment]:
    """Отозвать одобрения старше aml_approval_ttl_hours (48 ч — решение
    владельца, вопрос 7.1 спеки).

    Платёж с отметкой казначея «отправил» не истекает: средства уже в
    пути в пределах срока одобрения, его контролирует таймер сценария 4.
    Повторная синхронизация инвойса переведёт expired → pending_aml
    и перескринит адрес.
    """
    if settings.aml_approval_ttl_hours <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    deadline = now - timedelta(hours=settings.aml_approval_ttl_hours)
    stale = (
        (
            await session.execute(
                select(ExpectedPayment).where(
                    ExpectedPayment.status == EPS.AML_APPROVED,
                    ExpectedPayment.status_changed_at < deadline,
                    ExpectedPayment.sent_marked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for payment in stale:
        transition(payment, EPS.EXPIRED)
        session.add(AuditLog(
            actor="indexer",
            action="aml_approval_expired",
            entity="expected_payment",
            entity_id=str(payment.id),
            details={"to_address": payment.to_address,
                     "ttl_hours": settings.aml_approval_ttl_hours},
        ))
        await send_alert(
            session, "warning",
            "AML: одобрение истекло — требуется повторная проверка адреса",
            {"expected_payment_id": payment.id,
             "to_address": payment.to_address,
             "ttl_hours": settings.aml_approval_ttl_hours},
        )
    await session.flush()
    return list(stale)


async def check_sent_timeouts(
    session: AsyncSession, now: datetime | None = None
) -> list[ExpectedPayment]:
    """Таймер сценария 4 регламента (кнопка «отправил» — решение владельца).

    Казначей отметил отправку, а индексер не обнаружил транзакцию за
    aml_sent_timeout_minutes (30 мин) — алерт казначею: проверить вручную
    (обозреватель сети, статус кошелька). Алерт поднимается один раз;
    обнаружение транзакции закрывает вопрос само (статус станет sent).
    """
    if settings.aml_sent_timeout_minutes <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    deadline = now - timedelta(minutes=settings.aml_sent_timeout_minutes)
    overdue = (
        (
            await session.execute(
                select(ExpectedPayment).where(
                    ExpectedPayment.status == EPS.AML_APPROVED,
                    ExpectedPayment.sent_marked_at.is_not(None),
                    ExpectedPayment.sent_marked_at < deadline,
                    ExpectedPayment.sent_timeout_alerted.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    for payment in overdue:
        payment.sent_timeout_alerted = True
        await send_alert(
            session, "warning",
            "Отправка отмечена, но транзакция не обнаружена — проверьте вручную",
            {"expected_payment_id": payment.id,
             "to_address": payment.to_address,
             "marked_by": payment.sent_marked_by,
             "marked_at": payment.sent_marked_at.isoformat(),
             "timeout_minutes": settings.aml_sent_timeout_minutes},
        )
    await session.flush()
    return list(overdue)


# --- Фаза 5: блок AML для документа 1С, акта и уведомления ФНС -----------------


async def aml_summary(session: AsyncSession, tx: Transaction) -> dict:
    """Результат AML-проверки по транзакции (шаги 9, 11, 13 регламента).

    Единый блок для проекта документа 1С, акта по платежу и уведомления
    ФНС — аудит-цепочка «инвойс → адрес → скор → решение → tx_hash»
    читается из любого из них. Статусы:
    - performed     — исходящая связана с ожидаемым платежом, скрининг был;
    - not_performed — исходящая вне регламента (нет связанного ожидания);
    - not_required  — входящая: регламент охватывает только исходящие
      (скрининг входящих — вопрос 7.2 спеки).
    """
    if tx.direction != Direction.OUT:
        return {"status": "not_required",
                "note": "входящий платёж: регламент охватывает исходящие"}
    payment = await session.scalar(
        select(ExpectedPayment).where(ExpectedPayment.transaction_id == tx.id)
    )
    if payment is None:
        return {"status": "not_performed",
                "note": "исходящая не связана с одобренным ожидаемым платежом"
                        " (отправка вне регламента)"}
    screening = (
        await session.get(AmlScreening, payment.aml_screening_id)
        if payment.aml_screening_id is not None
        else None
    )
    return {
        "status": "performed",
        "note": "",
        "payment_status": payment.status.value,
        "invoice_id": payment.invoice_id,
        "risk_score": screening.risk_score if screening else None,
        "categories": list(screening.categories) if screening else [],
        "provider": screening.source_id if screening else "",
        "screened_at": (
            screening.screened_at.isoformat() if screening else None
        ),
        "decided_by": payment.decided_by,
        "decision_note": payment.decision_note,
    }


# --- Фаза 4: ре-скрининг по расписанию ----------------------------------------


def rescreen_due(
    last_run: datetime | None, now: datetime, hours: int
) -> bool:
    """Пора ли запускать ре-скрининг (0 часов — отключён)."""
    if hours <= 0:
        return False
    return last_run is None or now - last_run >= timedelta(hours=hours)


async def rescreen_known_addresses(
    session: AsyncSession, adapter: AmlAdapter
) -> int:
    """Повторный скрининг справочника адресов контрагентов.

    Каждая проверка — новая запись журнала (append-only). Смена статуса
    по порогам относительно прошлого скрининга → алерт; одобренные
    ожидаемые платежи на подорожавший адрес переводятся в expired —
    одобрение отозвано, повторная синхронизация инвойса запустит
    проверку заново (expired → pending_aml).
    """
    rows = (
        await session.execute(
            select(CounterpartyAddress.address, Network.code)
            .join(Network, CounterpartyAddress.network_id == Network.id)
            .distinct()
        )
    ).all()
    screened = 0
    for address, network_code in rows:
        previous = await last_screening(session, network_code, address)
        try:
            result = await adapter.screen_address(network_code, address)
        except Exception as exc:  # noqa: BLE001 — провайдер внешний
            log.warning("Ре-скрининг: провайдер недоступен (%s)", exc)
            await send_alert(
                session, "warning",
                "AML-провайдер недоступен — ре-скрининг не выполнен",
                {"address": address, "error": str(exc)},
            )
            break  # остальные адреса ждут следующего запуска
        await store_screening(session, adapter.source_name, result)
        screened += 1
        if previous is None:
            continue
        old_status = status_for_score(previous.risk_score)
        new_status = status_for_score(result.risk_score)
        if new_status == old_status:
            continue
        await send_alert(
            session,
            "error" if new_status == EPS.AML_REJECTED else "warning",
            "AML: скор адреса изменился при ре-скрининге",
            {"address": address, "network": network_code,
             "old_score": previous.risk_score, "new_score": result.risk_score,
             "categories": list(result.categories)},
        )
        if new_status != EPS.AML_APPROVED:
            # Отзыв одобрения: адрес больше не проходит по порогам.
            stale = (
                (
                    await session.execute(
                        select(ExpectedPayment).where(
                            ExpectedPayment.to_address == address,
                            ExpectedPayment.network == network_code,
                            ExpectedPayment.status == EPS.AML_APPROVED,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for payment in stale:
                transition(payment, EPS.EXPIRED)
                session.add(AuditLog(
                    actor="indexer",
                    action="aml_approval_revoked",
                    entity="expected_payment",
                    entity_id=str(payment.id),
                    details={"address": address,
                             "new_score": result.risk_score},
                ))
    await session.flush()
    return screened
