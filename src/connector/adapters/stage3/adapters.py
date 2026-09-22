"""Адаптеры Stage 3: RegistrySnapshot & IssuerRiskCheck.

Read-only адаптеры для внешних проверок. Не хранят секретов, не подписывают.
"""
from __future__ import annotations
import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

@dataclass
class RegistryResult:
    subject_ref: str
    status: str  # active, suspended, liquidated, not_found
    registry_type: str  # cbr_finorg, licensed_operator
    record_number: str | None
    checked_at: datetime
    raw_payload: dict
    checksum: str

@dataclass
class IssuerRiskResult:
    asset_symbol: str
    contract_address: str
    network: str
    risk_status: str  # normal, freeze_warning, frozen
    reason: str | None
    checked_at: datetime
    raw_payload: dict
    checksum: str

class BaseAdapter(ABC):
    @abstractmethod
    async def check(self, ref: str) -> Any:
        pass

class MockRegistryAdapter(BaseAdapter):
    """Mock для реестров ЦБ/операторов."""
    async def check(self, ref: str) -> RegistryResult:
        raw = {"subject": ref, "status": "active", "source": "mock_cbr"}
        checksum = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        return RegistryResult(
            subject_ref=ref,
            status="active",
            registry_type="cbr_finorg",
            record_number="MOCK-12345",
            checked_at=datetime.utcnow(),
            raw_payload=raw,
            checksum=checksum
        )

class MockIssuerRiskAdapter(BaseAdapter):
    """Mock для рисков эмитента (USDT/USDC)."""
    async def check(self, asset_symbol: str) -> IssuerRiskResult:
        raw = {"asset": asset_symbol, "risk": "normal"}
        checksum = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        return IssuerRiskResult(
            asset_symbol=asset_symbol,
            contract_address="0x...",
            network="ethereum",
            risk_status="normal",
            reason=None,
            checked_at=datetime.utcnow(),
            raw_payload=raw,
            checksum=checksum
        )

# Фабрика для получения адаптеров
def get_registry_adapter(provider: str = "mock") -> BaseAdapter:
    if provider == "mock":
        return MockRegistryAdapter()
    raise ValueError(f"Unknown provider: {provider}")

def get_issuer_risk_adapter(provider: str = "mock") -> BaseAdapter:
    if provider == "mock":
        return MockIssuerRiskAdapter()
    raise ValueError(f"Unknown provider: {provider}")
