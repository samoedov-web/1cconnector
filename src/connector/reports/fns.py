"""Уведомление ФНС о расчёте в цифровой валюте (шаги 11–12 регламента).

Официальный формат уведомления ещё не издан (форма КНД — проект),
поэтому структура — нейтральная, готовая к маппингу в формат ТКС при
его выходе (принцип обновляемых форм, п. 1.5 ТЗ). Состав полей — по
регламенту: организация, операция, адреса, хэш, рублёвая оценка по
курсу ЦБ, основание (инвойс/контракт/УНК/КВВО), результат AML.

Подпись ЭЦП и отправка через ТКС — вне коннектора (бухгалтер, шаг 12).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from xml.dom import minidom

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.models import Organization
from connector.reports.service import payment_act_data


async def fns_notification_data(session: AsyncSession, tx_id: int) -> dict | None:
    """Данные уведомления; основа — те же реквизиты, что в акте по платежу."""
    act = await payment_act_data(session, tx_id)
    if act is None:
        return None

    # Организация-владелец кошелька; для баз до появления юрлиц — первая.
    organization = await session.scalar(select(Organization).order_by(Organization.id))
    payment = act["payment"]
    allocations = act["allocations"]
    primary = allocations[0] if allocations else {}

    return {
        "report": "fns_notification",
        "format_version": "draft-0.1",  # до выхода официального формата КНД
        "generated_at": datetime.now().astimezone().isoformat(),
        "organization": {
            "name": organization.name if organization else "",
            "inn": organization.inn if organization else "",
            "kpp": organization.kpp if organization else "",
        },
        "operation": {
            "date": payment["finalized_at"] or payment["block_time"],
            "direction": payment["direction"],
            "asset": payment["asset"],
            "amount": payment["amount"],
            "network": act["immutability"]["network"],
            "from_address": payment["from_address"],
            "to_address": payment["to_address"],
            "tx_hash": act["immutability"]["tx_hash"],
        },
        "valuation": None if act["rate"] is None else {
            "amount_rub": act["rate"]["amount_rub"],
            "contract_currency": act["rate"]["contract_currency"],
            "rate_to_rub": act["rate"]["asset_to_rub"],
            "rate_source": act["rate"]["source"],
        },
        "basis": {
            "invoice": primary.get("invoice_number") or "",
            "contract": primary.get("contract_number") or "",
            "unk": primary.get("contract_registration_number") or "",
            "kvvo": primary.get("kvvo") or "",
            "counterparty": primary.get("counterparty") or "",
            "purpose": _purpose(primary),
        },
        # Результат AML-проверки адреса получателя (шаг 11 регламента):
        # тот же блок, что в акте по платежу и документе 1С.
        "aml": act["aml"],
        "immutability": act["immutability"],
    }


def _purpose(allocation: dict) -> str:
    invoice = allocation.get("invoice_number")
    contract = allocation.get("contract_number")
    if invoice and contract:
        return f"Оплата по инвойсу {invoice}, контракт {contract}"
    if contract:
        return f"Оплата по контракту {contract}"
    return "Расчёт в цифровой валюте по ВЭД-контракту"


def fns_notification_xml(data: dict) -> str:
    """Машиночитаемая выгрузка под ТКС (структура — до выхода формата ФНС)."""
    root = ET.Element("УведомлениеРасчетЦВ", {"ВерсияФормата": data["format_version"]})
    org = data["organization"]
    ET.SubElement(root, "Организация", {
        "Наименование": org["name"], "ИНН": org["inn"], "КПП": org["kpp"],
    })
    op = data["operation"]
    operation = ET.SubElement(root, "Операция", {
        "Дата": op["date"] or "",
        "Вид": "поступление" if op["direction"] == "in" else "выбытие",
    })
    ET.SubElement(operation, "ЦифроваяВалюта", {
        "Тикер": op["asset"], "Сеть": op["network"], "Сумма": op["amount"],
    })
    ET.SubElement(operation, "Адреса", {
        "Отправитель": op["from_address"], "Получатель": op["to_address"],
    })
    ET.SubElement(operation, "Транзакция", {
        "Хэш": op["tx_hash"],
        "SHA256ОтветаНоды": data["immutability"]["raw_response_sha256"],
    })
    if data["valuation"]:
        val = data["valuation"]
        ET.SubElement(operation, "РублеваяОценка", {
            "Сумма": val["amount_rub"],
            "Курс": val["rate_to_rub"],
            "ИсточникКурса": val["rate_source"],
        })
    basis = data["basis"]
    ET.SubElement(operation, "Основание", {
        "Инвойс": basis["invoice"], "Контракт": basis["contract"],
        "УНК": basis["unk"], "КВВО": basis["kvvo"],
        "Контрагент": basis["counterparty"],
        "НазначениеПлатежа": basis["purpose"],
    })
    aml = data["aml"]
    aml_attrs = {"Статус": aml["status"], "Примечание": aml.get("note", "")}
    if aml["status"] == "performed":
        aml_attrs |= {
            "Скор": str(aml["risk_score"]) if aml["risk_score"] is not None else "",
            "Категории": ", ".join(aml["categories"]),
            "Провайдер": aml["provider"],
            "ДатаПроверки": aml["screened_at"] or "",
            "СтатусПлатежа": aml["payment_status"],
            "РешениеКомплаенса": aml["decided_by"],
        }
    ET.SubElement(operation, "AML", aml_attrs)
    raw = ET.tostring(root, encoding="unicode")
    return minidom.parseString(raw).toprettyxml(indent="  ", encoding=None)
