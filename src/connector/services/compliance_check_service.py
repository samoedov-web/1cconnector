"""Сервис запуска проверок Stage 3."""
from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from connector.models import Operation, RegistrySnapshot, IssuerRiskCheck, Asset
from connector.adapters.stage3.adapters import (
    get_registry_adapter, 
    get_issuer_risk_adapter, 
    RegistryResult, 
    IssuerRiskResult
)
import json

class ComplianceCheckService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.registry_adapter = get_registry_adapter("mock")
        self.issuer_adapter = get_issuer_risk_adapter("mock")

    async def run_registry_check(self, operation: Operation, subject_ref: str) -> RegistrySnapshot:
        result: RegistryResult = await self.registry_adapter.check(subject_ref)
        snapshot = RegistrySnapshot(
            operation_id=operation.id,
            subject_reference=subject_ref,
            source="mock_cbr",
            status_result=result.status,
            snapshot_payload=json.dumps(result.raw_payload),
            checksum=result.checksum,
            observed_at=result.checked_at,
            regulatory_version_id=operation.regulatory_version_id
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
            risk_status=result.risk_status,
            source="mock_issuer",
            payload=json.dumps(result.raw_payload),
            checksum=result.checksum,
            checked_at=result.checked_at
        )
        self.session.add(check)
        return check
