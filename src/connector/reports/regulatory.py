"""Регуляторная отчетность: ФНС, ЦБ, Валютный контроль (Stage 8).

Документы:
- Уведомление о движении средств (ФНС, ст. 12 173-ФЗ).
- Справка о движении активов (ЦБ, проект подзаконных актов).
- Паспорт сделки / Справка о подтверждающих документах (Валютный контроль, 181-И).
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from connector.models import (
    Operation, Contract, Invoice, Counterparty, Transaction, 
    ComplianceDecision, RegistrySnapshot, IssuerRiskCheck
)

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

@dataclass
class FnsNotification:
    """Уведомление об открытии/закрытии счета или движении средств."""
    inn: str
    kpp: str
    account_number: str  # Адрес кошелька или ID счета
    operation_type: str  # open | close | transaction
    amount: Optional[Decimal]
    currency: Optional[str]
    counterparty_inn: Optional[str]
    contract_ref: Optional[str]
    generated_at: str

@dataclass
class CentralBankReport:
    """Сводная справка для ЦБ о движении цифровых активов."""
    organization_name: str
    period_from: str
    period_to: str
    total_inflow_rub: Decimal
    total_outflow_rub: Decimal
    operations_count: int
    top_assets: List[Dict[str, Any]]  # [{symbol, volume_rub}]

@dataclass
class CurrencyControlDoc:
    """Документ валютного контроля (Паспорт сделки или СПД)."""
    contract_number: str
    registration_number: str  # УНК
    counterparty_name: str
    country_code: str
    total_amount: Decimal
    currency_contract: str
    kvvo_code: str  # Код вида валютной операции
    status: str  # active | closed | suspended

class RegulatoryReportingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def generate_fns_notification(self, operation: Operation) -> FnsNotification:
        """Генерирует уведомление для ФНС на основе операции."""
        contract = operation.contract
        counterparty = contract.counterparty if contract else None
        
        # Получаем ИНН организации (заглушка, в реальности из Organization)
        org_inn = "0000000000" 
        org_kpp = "000000000"
        
        return FnsNotification(
            inn=org_inn,
            kpp=org_kpp,
            account_number=operation.wallet_address,
            operation_type="transaction",
            amount=None,  # Заполняется при агрегации
            currency=None,
            counterparty_inn=None,
            contract_ref=contract.number if contract else None,
            generated_at=utcnow().isoformat()
        )

    async def generate_cb_report(self, period_from: datetime, period_to: datetime) -> CentralBankReport:
        """Генерирует сводный отчет для ЦБ за период."""
        # Здесь должна быть сложная выборка по всем операциям периода
        # Для примера возвращаем заглушку
        return CentralBankReport(
            organization_name="Demo Org",
            period_from=period_from.isoformat(),
            period_to=period_to.isoformat(),
            total_inflow_rub=Decimal(0),
            total_outflow_rub=Decimal(0),
            operations_count=0,
            top_assets=[]
        )

    async def generate_currency_control_doc(self, contract: Contract) -> CurrencyControlDoc:
        """Формирует документ валютного контроля."""
        return CurrencyControlDoc(
            contract_number=contract.number,
            registration_number=contract.registration_number,
            counterparty_name=contract.counterparty.name,
            country_code="CN",  # Заглушка
            total_amount=Decimal(0),
            currency_contract=contract.currency,
            kvvo_code=contract.kvvo,
            status="active"
        )

    def export_to_json(self, report_obj: Any) -> str:
        """Экспорт отчета в JSON для отправки в регулятор."""
        if hasattr(report_obj, '__dataclass_fields__'):
            data = asdict(report_obj)
        else:
            data = report_obj
        return json.dumps(data, ensure_ascii=False, default=str)
