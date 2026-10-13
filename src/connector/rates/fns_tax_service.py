"""FNS tax quotation orchestration.

This service preserves the separation between tax market evidence
and accounting RateSnapshot generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from connector.rates.fns_evidence_store import FnsEvidenceStore
from connector.rates.fns_persistence import persist_verified_fns_quote
from connector.rates.market import MarketQuoteResult, MarketValuationSource


@dataclass(frozen=True)
class FnsTaxQuoteResult:
    quote: MarketQuoteResult
    evidence_id: int


class FnsTaxIntelligenceService:
    """Retrieve and persist independently verified FNS tax quotes."""

    def __init__(
        self,
        provider: MarketValuationSource,
        evidence_store: FnsEvidenceStore,
    ) -> None:
        if provider.valuation_context != "tax":
            raise ValueError("Tax-only provider required")
        self.provider = provider
        self.evidence_store = evidence_store

    async def collect(
        self,
        session: AsyncSession,
        *,
        base_asset: str,
        as_of: datetime,
        observed_at: datetime,
    ) -> FnsTaxQuoteResult:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")

        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")

        quote = await self.provider.get_market_quote(
            base_asset,
            "RUB",
            as_of,
        )

        if quote.valuation_context != "tax":
            raise ValueError("Unexpected valuation context")

        if quote.source != "fns-krptrate":
            raise ValueError("Unexpected quotation source")

        if quote.base_asset.upper() != base_asset.upper():
            raise ValueError("Unexpected quotation asset")

        if quote.quote_currency.upper() != "RUB":
            raise ValueError("Unexpected quotation currency")

        evidence = await persist_verified_fns_quote(
            session,
            quote,
            self.evidence_store,
            observed_at=observed_at,
        )

        return FnsTaxQuoteResult(
            quote=quote,
            evidence_id=evidence.id,
        )
