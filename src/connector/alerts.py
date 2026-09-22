"""Алерты мониторинга: журнал в БД + опциональный webhook.

Формат webhook — POST JSON {"severity", "title", "details", "at"};
подходит для любого приёмника (свой обработчик, мост в Telegram/почту).
Недоставка webhook не роняет цикл индексации — алерт остаётся в БД
с sent=false.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from connector.config import settings
from connector.models import Alert

log = logging.getLogger("connector.alerts")


async def send_alert(
    session: AsyncSession,
    severity: str,
    title: str,
    details: dict | None = None,
    webhook_url: str | None = None,
) -> Alert:
    alert = Alert(severity=severity, title=title, details=details or {})
    session.add(alert)
    await session.flush()

    url = webhook_url if webhook_url is not None else settings.alert_webhook_url
    if url:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    url,
                    json={
                        "severity": alert.severity,
                        "title": alert.title,
                        "details": alert.details,
                        "at": alert.at.isoformat() if alert.at else None,
                    },
                )
                resp.raise_for_status()
            alert.sent = True
        except Exception:
            log.exception("Webhook алерта недоступен: %s", title)
    return alert
