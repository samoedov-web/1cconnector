from datetime import datetime, timezone

from connector.adapters.stage3.adapters import RegistryResult
from connector.models import RegistryMonitoringSnapshot
from connector.services.registry_monitoring import diff_registry_snapshots


def snapshot_from_result(
    *,
    snapshot_id: int,
    target_id: int,
    result: RegistryResult,
) -> RegistryMonitoringSnapshot:
    return RegistryMonitoringSnapshot(
        id=snapshot_id,
        target_id=target_id,
        subject_reference=result.subject_ref,
        source=result.source,
        status_result=result.status,
        snapshot_payload=result.raw_payload,
        checksum=result.checksum,
        observed_at=result.checked_at,
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


def make_result(
    *,
    status: str,
    checksum: str,
    registry_entry_id=None,
    valid_from=None,
    valid_to=None,
    admission_mode=None,
    raw_payload=None,
) -> RegistryResult:
    return RegistryResult(
        subject_ref="7707083893",
        source="cbr-ois",
        status=status,
        raw_payload=raw_payload or {"status": status},
        checksum=checksum,
        checked_at=datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc),
        registry_type="ois",
        registry_entry_id=registry_entry_id,
        record_number=None,
        valid_from=valid_from,
        valid_to=valid_to,
        admission_mode=admission_mode,
        application_id=None,
        admission_date=None,
        legal_basis=None,
        source_url="https://www.cbr.ru/vfs/finmarkets/files/supervision/list_ois.xlsx",
    )


def test_real_registry_result_fields_map_to_monitoring_snapshot():
    result = make_result(
        status="active",
        checksum="a" * 64,
        registry_entry_id="REG-1",
        valid_from=datetime(2026, 9, 1, tzinfo=timezone.utc),
        admission_mode="registry",
    )

    snapshot = snapshot_from_result(
        snapshot_id=1,
        target_id=10,
        result=result,
    )

    assert snapshot.subject_reference == result.subject_ref
    assert snapshot.source == result.source
    assert snapshot.status_result == result.status
    assert snapshot.snapshot_payload == result.raw_payload
    assert snapshot.checksum == result.checksum
    assert snapshot.registry_type == result.registry_type
    assert snapshot.registry_entry_id == result.registry_entry_id
    assert snapshot.valid_from == result.valid_from
    assert snapshot.admission_mode == result.admission_mode
    assert snapshot.source_url == result.source_url


def test_real_contract_active_to_excluded_is_deterioration():
    previous_result = make_result(
        status="active",
        checksum="a" * 64,
    )
    current_result = make_result(
        status="excluded",
        checksum="b" * 64,
    )

    previous = snapshot_from_result(
        snapshot_id=1,
        target_id=10,
        result=previous_result,
    )
    current = snapshot_from_result(
        snapshot_id=2,
        target_id=10,
        result=current_result,
    )

    diff = diff_registry_snapshots(previous, current)

    assert diff.classification == "deterioration"
    assert diff.status_transition == "active->excluded"
    assert diff.is_deterioration is True


def test_real_contract_republished_same_facts_is_not_semantic_change():
    previous_result = make_result(
        status="active",
        checksum="a" * 64,
        raw_payload={"file_version": 1, "status": "active"},
    )
    current_result = make_result(
        status="active",
        checksum="b" * 64,
        raw_payload={"file_version": 2, "status": "active"},
    )

    previous = snapshot_from_result(
        snapshot_id=1,
        target_id=10,
        result=previous_result,
    )
    current = snapshot_from_result(
        snapshot_id=2,
        target_id=10,
        result=current_result,
    )

    diff = diff_registry_snapshots(previous, current)

    assert diff.changed is False
    assert diff.classification == "unchanged"


def test_real_contract_effective_date_change_is_semantic_change():
    previous_result = make_result(
        status="active",
        checksum="a" * 64,
        valid_to=None,
    )
    current_result = make_result(
        status="active",
        checksum="b" * 64,
        valid_to=datetime(2026, 10, 31, tzinfo=timezone.utc),
    )

    previous = snapshot_from_result(
        snapshot_id=1,
        target_id=10,
        result=previous_result,
    )
    current = snapshot_from_result(
        snapshot_id=2,
        target_id=10,
        result=current_result,
    )

    diff = diff_registry_snapshots(previous, current)

    assert diff.changed is True
    assert diff.classification == "change"
    assert {change.field for change in diff.changes} == {"valid_to"}
