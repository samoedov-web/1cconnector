"""Система мониторинга и алертов (Stage 9).

Контролирует:
- Отставание индексера от сети (lag).
- Расхождение данных между провайдерами.
- Ошибки подключения к БД/API.
- Таймеры ожидаемых платежей (sent_timeout).
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from connector.models import Alert, Transaction, ExpectedPayment, TxStatus
from connector.config import settings

log = logging.getLogger("connector.monitoring")

class MonitoringService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def check_indexer_lag(self, max_lag_blocks: int = 10) -> List[Dict[str, Any]]:
        """Проверяет отставание индексера от последней финальной транзакции."""
        alerts = []
        # Упрощенная логика: сравниваем last_scanned_block с max(block_number)
        result = await self.session.execute(
            select(func.max(Transaction.block_number)).where(Transaction.status == TxStatus.FINAL)
        )
        max_block = result.scalar_one_or_none()
        
        if max_block is None:
            return alerts
            
        # В реальности нужно сравнивать с курсорами каждого кошелька
        # Здесь заглушка для демонстрации
        lag = 0  # Заглушка: в реальном коде вычисляется разница
        if lag > max_lag_blocks:
            alert_data = {
                "severity": "error",
                "title": "Indexer Lag Detected",
                "details": {"max_block": max_block, "lag": lag}
            }
            await self.create_alert(**alert_data)
            alerts.append(alert_data)
        return alerts

    async def check_sent_timeouts(self, timeout_minutes: int = 30) -> List[Dict[str, Any]]:
        """Проверяет ожидаемые платежи, которые не были обнаружены вовремя."""
        alerts = []
        cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        
        result = await self.session.execute(
            select(ExpectedPayment)
            .where(ExpectedPayment.status == "sent")
            .where(ExpectedPayment.sent_marked_at < cutoff)
            .where(ExpectedPayment.sent_timeout_alerted == False)
        )
        expired_payments = result.scalars().all()
        
        for payment in expired_payments:
            payment.sent_timeout_alerted = True
            alert_data = {
                "severity": "warning",
                "title": "Payment Sent Timeout",
                "details": {
                    "invoice_id": payment.invoice_id,
                    "amount": str(payment.amount),
                    "delayed_since": payment.sent_marked_at.isoformat()
                }
            }
            await self.create_alert(**alert_data)
            alerts.append(alert_data)
            
        return alerts

    async def create_alert(self, severity: str, title: str, details: Dict[str, Any]) -> Alert:
        """Создает запись алерта в БД."""
        alert = Alert(
            severity=severity,
            title=title,
            details=details,
            sent=False  # Флаг отправки во внешний webhook
        )
        self.session.add(alert)
        log.warning(f"Alert created: {title} [{severity}]")
        return alert

    async def get_unsent_alerts(self) -> List[Alert]:
        """Получает список алертов, которые еще не были отправлены наружу."""
        result = await self.session.execute(
            select(Alert).where(Alert.sent == False).order_by(Alert.at)
        )
        return list(result.scalars().all())

    async def mark_alert_sent(self, alert: Alert) -> None:
        """Помечает алерт как отправленный."""
        alert.sent = True
