"""Экспорт отчетов в файлы (XML, CSV, PDF-ready HTML)."""
from __future__ import annotations
import csv
import io
from typing import Any, List
from connector.reports.regulatory import FnsNotification, CentralBankReport, CurrencyControlDoc

class ReportExporter:
    @staticmethod
    def to_csv_fns(notifications: List[FnsNotification]) -> str:
        """Экспорт уведомлений ФНС в CSV."""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=FnsNotification.__dataclass_fields__.keys())
        writer.writeheader()
        for note in notifications:
            writer.writerow({k: str(v) for k, v in note.__dict__.items()})
        return output.getvalue()

    @staticmethod
    def to_xml_cb(report: CentralBankReport) -> str:
        """Экспорт отчета ЦБ в XML (упрощенно)."""
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<CentralBankReport>
    <Organization>{report.organization_name}</Organization>
    <PeriodFrom>{report.period_from}</PeriodFrom>
    <PeriodTo>{report.period_to}</PeriodTo>
    <TotalInflow>{report.total_inflow_rub}</TotalInflow>
    <TotalOutflow>{report.total_outflow_rub}</TotalOutflow>
    <OperationsCount>{report.operations_count}</OperationsCount>
</CentralBankReport>"""
        return xml

    @staticmethod
    def to_html_currency_control(doc: CurrencyControlDoc) -> str:
        """Экспорт документа валютного контроля в HTML (для печати/PDF)."""
        return f"""
        <html>
        <body>
            <h1>Документ валютного контроля</h1>
            <p>Контракт: {doc.contract_number}</p>
            <p>УНК: {doc.registration_number}</p>
            <p>Контрагент: {doc.counterparty_name}</p>
            <p>Сумма: {doc.total_amount} {doc.currency_contract}</p>
            <p>КВО: {doc.kvvo_code}</p>
            <p>Статус: {doc.status}</p>
        </body>
        </html>
        """
