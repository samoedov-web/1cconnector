from datetime import datetime, timezone

import pytest

from connector.models import Operation, RegulatoryVersion
from connector.rates.base import Quote, RateSource
from connector.rates.service import RateService
from connector.services.evidence_service import EvidenceService


class TraceRateSource(RateSource):
    source_name = "trace-test"

    async def get_quote(self, base, quote, as_of):
        from decimal import Decimal

        return Quote(
            base=base,
            quote=quote,
            rate=Decimal("1"),
            as_of=as_of,
            source=self.source_name,
            raw={},
        )


@pytest.mark.asyncio
async def test_evidence_inherits_operation_regulatory_version(
    async_session,
    sample_operation,
):
    rv = RegulatoryVersion(
        code="TRACE-1",
        source_document="trace-source",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        checksum="a" * 64,
    )
    async_session.add(rv)
    await async_session.flush()

    sample_operation.regulatory_version_id = rv.id
    await async_session.flush()

    evidence = await EvidenceService(async_session).add_evidence(
        operation=sample_operation,
        evidence_type="registry_check",
        source="trace-test",
        source_reference="subject-1",
        raw_payload={"status": "active"},
    )

    assert evidence.regulatory_version_id == rv.id


@pytest.mark.asyncio
async def test_rate_snapshot_preserves_explicit_regulatory_version():
    service = RateService(primary=TraceRateSource())

    snapshot = await service.snapshot(
        asset_symbol="USDT",
        contract_currency="USD",
        as_of=datetime(2026, 1, 15, tzinfo=timezone.utc),
        regulatory_version_id=42,
    )

    assert snapshot.regulatory_version_id == 42
