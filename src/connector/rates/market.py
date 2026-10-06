"""Market valuation evidence boundary.

This module is intentionally separate from the accounting RateService.

Two regulatory/business contexts may use this boundary differently:

1. tax market intelligence:
   official FNS datasets such as krptrate/krpttorg, where an official
   published RUB quotation may already be available;

2. VED valuation:
   an approved market source for the digital asset leg, for example
   USDT/USD, followed separately by the existing official CBR FX leg.

The contract below does not assume that these methodologies are identical.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from connector.models import MarketQuoteEvidence


ValuationContext = Literal["tax", "ved"]


@dataclass(frozen=True)
class MarketQuoteResult:
    """Normalized externally observed market quotation.

    Important:
    - rate is always Decimal;
    - raw_payload preserves the provider facts used for normalization;
    - evidence_hash is the SHA-256 of the authoritative evidence material;
    - methodology_version identifies the valuation/normalization rules;
    - valuation_context prevents tax-only evidence from being silently
      treated as a VED valuation source.
    """

    base_asset: str
    quote_currency: str
    rate: Decimal
    quote_as_of: datetime

    source: str
    source_url: str | None

    methodology_version: str
    valuation_context: ValuationContext

    raw_payload: dict
    evidence_hash: str


class MarketValuationSource(ABC):
    """Read-only source of normalized market quotations."""

    source_name: str
    valuation_context: ValuationContext

    @abstractmethod
    async def get_market_quote(
        self,
        base_asset: str,
        quote_currency: str,
        as_of: datetime,
    ) -> MarketQuoteResult:
        """Return a normalized market quote or fail closed.

        Implementations must not invent values for unsupported assets,
        trading pairs or dates.
        """


async def persist_market_quote_evidence(
    session: AsyncSession,
    result: MarketQuoteResult,
    *,
    observed_at: datetime,
) -> MarketQuoteEvidence:
    """Persist immutable external quotation evidence.

    Transaction ownership remains with the caller. This function neither
    commits nor creates an accounting RateSnapshot.
    """

    evidence = MarketQuoteEvidence(
        base_asset=result.base_asset.upper(),
        quote_currency=result.quote_currency.upper(),
        rate=result.rate,
        quote_as_of=result.quote_as_of,
        source=result.source,
        source_url=result.source_url,
        methodology_version=result.methodology_version,
        raw_payload={
            **result.raw_payload,
            "_valuation_context": result.valuation_context,
        },
        evidence_hash=result.evidence_hash,
        observed_at=observed_at,
    )

    session.add(evidence)
    await session.flush()

    return evidence
