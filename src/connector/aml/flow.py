"""Поток ожидаемых платежей: создание из инвойса + скрининг (фаза 2).

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
  (InvalidTransition), а не молчаливая порча статуса.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.alerts import send_alert
from connector.aml.base import AmlAdapter
from connector.aml.store import store_screening
from connector.config import settings
from connector.models import (
    ExpectedPayment,
    ExpectedPaymentStatus as EPS,
    Invoice,
)

log = logging.getLogger("connector.aml")

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
        )
        session.add(payment)
        await session.flush()
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
