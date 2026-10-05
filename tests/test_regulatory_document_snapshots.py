from datetime import datetime, timezone

import pytest

from connector.models import (
    RegulatoryDocumentSnapshot,
    RegulatoryVersion,
    RegistrySnapshot,
)


@pytest.mark.asyncio
async def test_regulatory_document_snapshot_persists_external_document_state(
    async_session,
):
    rv = RegulatoryVersion(
        code="REG-2026-10-05",
        source_document="CBR operational registry regime",
        effective_from=datetime(2026, 10, 5, tzinfo=timezone.utc),
        checksum="1" * 64,
        status="effective",
    )
    async_session.add(rv)
    await async_session.flush()

    snapshot = RegulatoryDocumentSnapshot(
        document_type="CBR_REGULATION",
        issuer="Bank of Russia",
        document_number="890-P",
        document_date=datetime(2026, 8, 27, tzinfo=timezone.utc),
        registration_number="test-registration",
        registered_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        published_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        effective_from=datetime(2026, 10, 5, tzinfo=timezone.utc),
        effective_to=None,
        status="EFFECTIVE",
        source="CBR",
        source_url="https://example.invalid/cbr/890-p",
        checked_at=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
        raw_payload={"status": "effective"},
        checksum="2" * 64,
        regulatory_version_id=rv.id,
        supersedes_snapshot_id=None,
    )

    async_session.add(snapshot)
    await async_session.flush()

    assert snapshot.id is not None
    assert snapshot.document_number == "890-P"
    assert snapshot.status == "EFFECTIVE"
    assert snapshot.regulatory_version_id == rv.id
    assert snapshot.effective_from == datetime(
        2026, 10, 5, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_registry_snapshot_supports_operational_registry_metadata(
    async_session,
    sample_operation,
):
    snapshot = RegistrySnapshot(
        operation_id=sample_operation.id,
        subject_reference="7700000000",
        source="CBR",
        status_result="active",
        snapshot_payload={"entry": "TEST-001"},
        checksum="3" * 64,
        observed_at=datetime(2026, 10, 5, 12, 30, tzinfo=timezone.utc),
        regulatory_version_id=sample_operation.regulatory_version_id,

        registry_type="DIGITAL_CURRENCY_EXCHANGE",
        registry_entry_id="TEST-001",
        valid_from=datetime(2026, 10, 5, tzinfo=timezone.utc),
        valid_to=None,
        admission_mode="GENERAL",
        application_id="APP-001",
        admission_date=datetime(2026, 10, 5, tzinfo=timezone.utc),
        legal_basis="Test legal basis",
        source_url="https://example.invalid/cbr/registry",
    )

    async_session.add(snapshot)
    await async_session.flush()

    assert snapshot.id is not None
    assert snapshot.registry_type == "DIGITAL_CURRENCY_EXCHANGE"
    assert snapshot.registry_entry_id == "TEST-001"
    assert snapshot.admission_mode == "GENERAL"


@pytest.mark.asyncio
async def test_legacy_registry_snapshot_contract_remains_valid(
    async_session,
    sample_operation,
):
    snapshot = RegistrySnapshot(
        operation_id=sample_operation.id,
        subject_reference="legacy-subject",
        source="legacy-test",
        status_result="active",
        snapshot_payload={},
        checksum="4" * 64,
        observed_at=datetime.now(timezone.utc),
    )

    async_session.add(snapshot)
    await async_session.flush()

    assert snapshot.id is not None
    assert snapshot.registry_type is None
    assert snapshot.registry_entry_id is None
    assert snapshot.admission_mode is None


@pytest.mark.asyncio
async def test_regulatory_document_history_is_extended_with_new_snapshot(
    async_session,
):
    first = RegulatoryDocumentSnapshot(
        document_type="CBR_REGULATION",
        issuer="Bank of Russia",
        document_number="7429-U",
        document_date=datetime(2026, 8, 27, tzinfo=timezone.utc),
        status="PUBLISHED",
        source="CBR",
        checked_at=datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc),
        raw_payload={"status": "published"},
        checksum="5" * 64,
    )
    async_session.add(first)
    await async_session.flush()

    second = RegulatoryDocumentSnapshot(
        document_type="CBR_REGULATION",
        issuer="Bank of Russia",
        document_number="7429-U",
        document_date=datetime(2026, 8, 27, tzinfo=timezone.utc),
        status="EFFECTIVE",
        source="CBR",
        checked_at=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
        raw_payload={"status": "effective"},
        checksum="6" * 64,
        supersedes_snapshot_id=first.id,
    )
    async_session.add(second)
    await async_session.flush()

    assert first.id != second.id
    assert first.status == "PUBLISHED"
    assert second.status == "EFFECTIVE"
    assert second.supersedes_snapshot_id == first.id
