"""Адаптеры Stage 3: RegistrySnapshot & IssuerRiskCheck.

Read-only адаптеры для внешних проверок. Не хранят секретов, не подписывают.
"""
from __future__ import annotations
import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from connector.models import IssuerProviderHealth, IssuerRiskStatus
from connector.sources.registry import (
    create_registry_source,
    register_registry_source,
)

@dataclass
class RegistryResult:
    """Normalized result returned by a registry provider.

    `record_number` is retained as a legacy/provider-facing field.
    `registry_entry_id` is the canonical normalized identifier persisted
    into RegistrySnapshot.
    """

    subject_ref: str
    status: str
    registry_type: str
    record_number: str | None
    checked_at: datetime
    raw_payload: dict
    checksum: str

    source: str = "unknown"
    source_url: str | None = None

    registry_entry_id: str | None = None
    admission_mode: str | None = None

    valid_from: datetime | None = None
    valid_to: datetime | None = None

    application_id: str | None = None
    admission_date: datetime | None = None
    legal_basis: str | None = None

@dataclass
class IssuerRiskResult:
    asset_symbol: str
    contract_address: str
    network: str
    risk_status: IssuerRiskStatus
    provider_health: IssuerProviderHealth
    reason: str | None
    checked_at: datetime
    raw_payload: dict
    checksum: str

class BaseAdapter(ABC):
    @abstractmethod
    async def check(self, ref: str) -> Any:
        pass

@register_registry_source("mock")
class MockRegistryAdapter(BaseAdapter):
    """Deterministic registry provider for tests/dev."""

    async def check(self, ref: str) -> RegistryResult:
        raw = {
            "subject": ref,
            "status": "active",
            "source": "mock_cbr",
            "registry_type": "DIGITAL_CURRENCY_EXCHANGE",
            "registry_entry_id": "MOCK-12345",
            "admission_mode": "GENERAL",
        }
        checksum = hashlib.sha256(
            json.dumps(raw, sort_keys=True).encode()
        ).hexdigest()

        return RegistryResult(
            subject_ref=ref,
            status="active",
            registry_type="DIGITAL_CURRENCY_EXCHANGE",
            record_number="MOCK-12345",
            registry_entry_id="MOCK-12345",
            admission_mode="GENERAL",
            checked_at=datetime.now(timezone.utc),
            raw_payload=raw,
            checksum=checksum,
            source="mock_cbr",
            source_url="https://example.invalid/mock-cbr-registry",
            legal_basis="mock-registry-fixture",
        )

class MockIssuerRiskAdapter(BaseAdapter):
    """Mock для рисков эмитента (USDT/USDC)."""
    async def check(self, asset_symbol: str) -> IssuerRiskResult:
        raw = {"asset": asset_symbol, "risk": IssuerRiskStatus.OK.value}
        checksum = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        return IssuerRiskResult(
            asset_symbol=asset_symbol,
            contract_address="0x...",
            network="ethereum",
            risk_status=IssuerRiskStatus.OK,
            provider_health=IssuerProviderHealth.AVAILABLE,
            reason=None,
            checked_at=datetime.utcnow(),
            raw_payload=raw,
            checksum=checksum
        )

# Фабрика для получения адаптеров
def get_registry_adapter(provider: str = "mock") -> BaseAdapter:
    """Backward-compatible wrapper around the canonical source registry."""
    try:
        return create_registry_source(provider)
    except LookupError as exc:
        raise ValueError(f"Unknown provider: {provider}") from exc

def get_issuer_risk_adapter(provider: str = "mock") -> BaseAdapter:
    if provider == "mock":
        return MockIssuerRiskAdapter()
    raise ValueError(f"Unknown provider: {provider}")
