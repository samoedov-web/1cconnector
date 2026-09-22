"""API печатных форм. Доступ — любой аутентифицированный пользователь панели
(аудитору достаточно чтения)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from connector.db import get_session
from connector.reports.reconciliation import reconciliation_data
from connector.reports.render import (
    journal_xlsx,
    payment_act_xlsx,
    reconciliation_xlsx,
    render_html,
    tax_register_xlsx,
)
from connector.reports.service import journal_data, payment_act_data
from connector.reports.tax import tax_register_data
from connector.security import CurrentUser, require_reader

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ReportFormat(StrEnum):
    HTML = "html"
    XLSX = "xlsx"
    JSON = "json"


class FnsFormat(StrEnum):
    HTML = "html"
    XML = "xml"  # машиночитаемая выгрузка под ТКС
    JSON = "json"


def _respond(data: dict, fmt: ReportFormat, template: str, xlsx_builder, filename: str):
    if fmt == ReportFormat.JSON:
        return JSONResponse(data)
    if fmt == ReportFormat.XLSX:
        return Response(
            content=xlsx_builder(data),
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'},
        )
    return HTMLResponse(render_html(template, data))


@router.get("/payment-act/{tx_id}")
async def payment_act(
    tx_id: int,
    format: ReportFormat = Query(default=ReportFormat.HTML),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
):
    """Справка/акт по крипто-платежу для банка/депозитария."""
    data = await payment_act_data(session, tx_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Транзакция не найдена")
    return _respond(
        data, format, "payment_act.html", payment_act_xlsx, f"payment-act-{tx_id}"
    )


@router.get("/fns-notification/{tx_id}")
async def fns_notification(
    tx_id: int,
    format: FnsFormat = Query(default=FnsFormat.HTML),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
):
    """Уведомление ФНС о расчёте в ЦВ (шаги 11–12 регламента оплаты)."""
    from connector.reports.fns import fns_notification_data, fns_notification_xml

    data = await fns_notification_data(session, tx_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Транзакция не найдена")
    if format == FnsFormat.JSON:
        return JSONResponse(data)
    if format == FnsFormat.XML:
        return Response(
            content=fns_notification_xml(data),
            media_type="application/xml; charset=utf-8",
            headers={
                "Content-Disposition":
                    f'attachment; filename="fns-notification-{tx_id}.xml"'
            },
        )
    return HTMLResponse(render_html("fns_notification.html", data))


@router.get("/tax-register")
async def tax_register(
    date_from: datetime,
    date_to: datetime,
    format: ReportFormat = Query(default=ReportFormat.HTML),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
):
    """Налоговый регистр операций с ЦВ за период (v1)."""
    data = await tax_register_data(session, date_from, date_to)
    return _respond(data, format, "tax_register.html", tax_register_xlsx, "tax-register")


@router.get("/reconciliation")
async def reconciliation_act(
    counterparty_id: int,
    date_from: datetime,
    date_to: datetime,
    format: ReportFormat = Query(default=ReportFormat.HTML),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
):
    """Акт сверки с контрагентом RU/EN за период (v1)."""
    data = await reconciliation_data(session, counterparty_id, date_from, date_to)
    if data is None:
        raise HTTPException(status_code=404, detail="Контрагент не найден")
    return _respond(
        data, format, "reconciliation_act.html", reconciliation_xlsx,
        f"reconciliation-{counterparty_id}",
    )


@router.get("/journal")
async def operations_journal(
    wallet_id: int,
    date_from: datetime,
    date_to: datetime,
    format: ReportFormat = Query(default=ReportFormat.HTML),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_reader),
):
    """Журнал операций по адресу за период (для отчёта в ФНС)."""
    data = await journal_data(session, wallet_id, date_from, date_to)
    if data is None:
        raise HTTPException(status_code=404, detail="Кошелёк не найден")
    return _respond(
        data, format, "operations_journal.html", journal_xlsx, f"journal-{wallet_id}"
    )
