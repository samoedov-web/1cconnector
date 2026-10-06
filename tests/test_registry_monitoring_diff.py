from datetime import datetime, timezone

import pytest

from connector.models import RegistryMonitoringSnapshot
from connector.services.registry_monitoring import diff_registry_snapshots


def make_snapshot(
    *,
    snapshot_id: int,
    target_id: int = 1,
    subject_reference: str = "7707083893",
    status: str = "active",
    checksum: str = "a" * 64,
    payload: dict | None = None,
    source_url: str | None = "https://www.cbr.ru/registry.xlsx",
    registry_type: str | None = "ois",
    registry_entry_id: str | None = None,
    valid_from=None,
    valid_to=None,
    admission_mode=None,
    application_id=None,
    admission_date=None,
    legal_basis=None,
):
    return RegistryMonitoringSnapshot(
        id=snapshot_id,
        target_id=target_id,
        subject_reference=subject_reference,
        source="cbr-ois",
        status_result=status,
        snapshot_payload=payload or {"status": status},
        checksum=checksum,
        observed_at=datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc),
        registry_type=registry_type,
        registry_entry_id=registry_entry_id,
        valid_from=valid_from,
        valid_to=valid_to,
        admission_mode=admission_mode,
        application_id=application_id,
        admission_date=admission_date,
        legal_basis=legal_basis,
        source_url=source_url,
    )


@pytest.mark.parametrize(
    "new_status",
    [
        "excluded",
        "suspended",
        "exclusion_decided",
    ],
)
def test_active_to_adverse_status_is_deterioration(new_status):
    previous = make_snapshot(snapshot_id=1, status="active")
    current = make_snapshot(snapshot_id=2, status=new_status)

    diff = diff_registry_snapshots(previous, current)

    assert diff.changed is True
    assert diff.is_deterioration is True
    assert diff.classification == "deterioration"
    assert diff.status_transition == f"active->{new_status}"
    assert [change.field for change in diff.changes] == ["status_result"]


@pytest.mark.parametrize("old_status", ["unknown", "not_found"])
def test_unknown_or_not_found_to_active_is_improvement(old_status):
    previous = make_snapshot(snapshot_id=1, status=old_status)
    current = make_snapshot(snapshot_id=2, status="active")

    diff = diff_registry_snapshots(previous, current)

    assert diff.changed is True
    assert diff.is_improvement is True
    assert diff.classification == "improvement"
    assert diff.status_transition == f"{old_status}->active"


def test_operational_registry_fields_create_semantic_diff():
    previous = make_snapshot(
        snapshot_id=1,
        registry_entry_id="OLD",
        admission_mode=None,
        valid_from=None,
    )
    current = make_snapshot(
        snapshot_id=2,
        registry_entry_id="NEW",
        admission_mode="license",
        valid_from=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )

    diff = diff_registry_snapshots(previous, current)

    assert diff.changed is True
    assert diff.classification == "change"
    assert diff.status_transition is None

    changed_fields = {change.field for change in diff.changes}

    assert changed_fields == {
        "registry_entry_id",
        "valid_from",
        "admission_mode",
    }


def test_raw_payload_checksum_and_source_url_do_not_create_semantic_diff():
    previous = make_snapshot(
        snapshot_id=1,
        checksum="a" * 64,
        payload={"row": 1},
        source_url="https://www.cbr.ru/old.xlsx",
    )
    current = make_snapshot(
        snapshot_id=2,
        checksum="b" * 64,
        payload={"row": 1, "republished": True},
        source_url="https://www.cbr.ru/new.xlsx",
    )

    diff = diff_registry_snapshots(previous, current)

    assert diff.changed is False
    assert diff.classification == "unchanged"
    assert diff.status_transition is None
    assert diff.changes == ()


def test_status_normalization_is_deterministic():
    previous = make_snapshot(snapshot_id=1, status=" ACTIVE ")
    current = make_snapshot(snapshot_id=2, status="active")

    diff = diff_registry_snapshots(previous, current)

    assert diff.changed is False
    assert diff.classification == "unchanged"


def test_different_targets_are_rejected():
    previous = make_snapshot(snapshot_id=1, target_id=1)
    current = make_snapshot(snapshot_id=2, target_id=2)

    with pytest.raises(
        ValueError,
        match="different monitoring targets",
    ):
        diff_registry_snapshots(previous, current)


def test_different_subjects_are_rejected():
    previous = make_snapshot(
        snapshot_id=1,
        subject_reference="7707083893",
    )
    current = make_snapshot(
        snapshot_id=2,
        subject_reference="1234567890",
    )

    with pytest.raises(
        ValueError,
        match="different subject references",
    ):
        diff_registry_snapshots(previous, current)
