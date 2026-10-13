"""Build a tax-only FNS service from explicit target selectors."""

from __future__ import annotations

from datetime import timedelta

from connector.models import FnsMonitoringTarget
from connector.rates.fns_evidence_store import FnsEvidenceStore
from connector.rates.fns_provider import FnsKrptrateProvider
from connector.rates.fns_scheduler import validate_fns_target_config
from connector.rates.fns_tax_service import FnsTaxIntelligenceService


def build_fns_target_service(
    target: FnsMonitoringTarget,
    *,
    evidence_store: FnsEvidenceStore,
    max_quote_age: timedelta,
) -> FnsTaxIntelligenceService:
    """Bind the FNS provider to one explicitly configured target."""
    validate_fns_target_config(target)

    provider = FnsKrptrateProvider(
        max_quote_age=max_quote_age,
        organizer=target.organizer,
        trading_pair=target.trading_pair,
        evidence_store=evidence_store,
        require_persistence=True,
    )

    if (
        provider.organizer != target.organizer
        or provider.trading_pair != target.trading_pair
    ):
        raise ValueError("FNS provider selector mismatch")

    return FnsTaxIntelligenceService(
        provider,
        evidence_store,
    )
