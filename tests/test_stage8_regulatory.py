import pytest
import json
from decimal import Decimal
from datetime import datetime, timezone
from connector.reports.regulatory import (
    RegulatoryReportingService, 
    FnsNotification, 
    CentralBankReport, 
    CurrencyControlDoc
)
from connector.reports.exporter import ReportExporter

@pytest.mark.asyncio
async def test_fns_notification_generation(async_session, sample_operation):
    service = RegulatoryReportingService(async_session)
    note = await service.generate_fns_notification(sample_operation)
    
    assert isinstance(note, FnsNotification)
    assert note.operation_type == "transaction"
    assert note.generated_at is not None

@pytest.mark.asyncio
async def test_cb_report_generation(async_session):
    service = RegulatoryReportingService(async_session)
    now = datetime.now(timezone.utc)
    report = await service.generate_cb_report(now, now)
    
    assert isinstance(report, CentralBankReport)
    assert report.period_from == now.isoformat()

def test_export_fns_to_csv():
    notes = [
        FnsNotification("INN1", "KPP1", "ADDR1", "transaction", Decimal(100), "RUB", None, None, "2026-01-01"),
        FnsNotification("INN2", "KPP2", "ADDR2", "open", None, None, None, None, "2026-01-02")
    ]
    csv_data = ReportExporter.to_csv_fns(notes)
    
    assert "INN1" in csv_data
    assert "transaction" in csv_data
    assert csv_data.startswith("inn,kpp,account_number")

def test_export_cb_to_xml():
    report = CentralBankReport("Org", "2026-01-01", "2026-01-31", Decimal(1000), Decimal(500), 10, [])
    xml = ReportExporter.to_xml_cb(report)
    
    assert "<?xml" in xml
    assert "<TotalInflow>1000</TotalInflow>" in xml
