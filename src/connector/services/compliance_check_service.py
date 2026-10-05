"""Сервис запуска проверок Stage 3."""
from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from connector.models import Operation, RegistrySnapshot, IssuerRiskCheck, Asset
from connector.config import settings
from connector.adapters.stage3.adapters import (
    get_registry_adapter, 
    get_issuer_risk_adapter, 
    RegistryResult, 
    IssuerRiskResult
)
import json

class ComplianceCheckService:
    def __init__(
        self,
        session: AsyncSession,
        registry_adapter=None,
        issuer_adapter=None,
    ):
        self.session = session
        self.registry_adapter = (
            registry_adapter
            if registry_adapter is not None
            else get_registry_adapter(settings.registry_source_id)
        )
        self.issuer_adapter = (
            issuer_adapter
            if issuer_adapter is not None
            else get_issuer_risk_adapter("mock")
        )

    async def run_registry_check(
        self,
        operation: Operation,
        subject_ref: str,
    ) -> RegistrySnapshot:
        result: RegistryResult = await self.registry_adapter.check(subject_ref)

        snapshot = RegistrySnapshot(
            operation_id=operation.id,
            subject_reference=result.subject_ref,
            source=result.source,
            status_result=result.status,
            snapshot_payload=result.raw_payload,
            checksum=result.checksum,
            observed_at=result.checked_at,
            regulatory_version_id=operation.regulatory_version_id,

            registry_type=result.registry_type,
            registry_entry_id=(
                result.registry_entry_id or result.record_number
            ),
            valid_from=result.valid_from,
            valid_to=result.valid_to,
            admission_mode=result.admission_mode,
            application_id=result.application_id,
            admission_date=result.admission_date,
            legal_basis=result.legal_basis,
            source_url=result.source_url,
        )

        self.session.add(snapshot)
        return snapshot

    async def run_issuer_check(self, operation: Operation, asset: Asset) -> IssuerRiskCheck:
        result: IssuerRiskResult = await self.issuer_adapter.check(asset.symbol)
        check = IssuerRiskCheck(
            operation_id=operation.id,
            asset_id=asset.id,
            contract_address=result.contract_address,
            network_id=asset.network_id,
            risk_status=result.risk_status.value,
            provider_health=result.provider_health.value,
            source="mock_issuer",
            payload=json.dumps(result.raw_payload),
            checksum=result.checksum,
            checked_at=result.checked_at
        )
        self.session.add(check)
        return check
