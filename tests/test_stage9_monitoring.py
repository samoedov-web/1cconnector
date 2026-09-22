import pytest
from datetime import datetime, timedelta
from connector.monitoring.alerts import MonitoringService
from connector.models import ExpectedPayment, Alert

@pytest.mark.asyncio
async def test_sent_timeout_detection(async_session, sample_invoice):
    service = MonitoringService(async_session)
    
    # Создаем платеж, отправленный 40 минут назад (таймаут 30 мин)
    old_time = datetime.utcnow() - timedelta(minutes=40)
    payment = ExpectedPayment(
        invoice_id=sample_invoice.id,
        to_address="0x...",
        network="ethereum",
        amount=100,
        currency="USDT",
        status="sent",
        sent_marked_at=old_time,
        sent_timeout_alerted=False
    )
    async_session.add(payment)
    await async_session.flush()
    
    alerts = await service.check_sent_timeouts(timeout_minutes=30)
    
    assert len(alerts) == 1
    assert alerts[0]["title"] == "Payment Sent Timeout"
    assert payment.sent_timeout_alerted is True

@pytest.mark.asyncio
async def test_create_alert(async_session):
    service = MonitoringService(async_session)
    alert = await service.create_alert(
        severity="error",
        title="Test Alert",
        details={"test": "data"}
    )
    
    assert alert.id is not None
    assert alert.sent is False
    assert alert.severity == "error"
