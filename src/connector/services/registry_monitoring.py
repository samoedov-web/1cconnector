"""Participant-level registry monitoring domain logic.

This module intentionally starts with pure deterministic diff logic.
Persistence, refresh scheduling, impact analysis and alerts are added
separately so registry history remains easy to reason about and test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from connector.models import RegistryMonitoringSnapshot


SEMANTIC_FIELDS = (
    "status_result",
    "registry_type",
    "registry_entry_id",
    "valid_from",
    "valid_to",
    "admission_mode",
    "application_id",
    "admission_date",
    "legal_basis",
)

_DETERIORATED_FROM_ACTIVE = {
    "excluded",
    "suspended",
    "exclusion_decided",
    "not_found",
    "unknown",
}

_IMPROVED_TO_ACTIVE = {
    "excluded",
    "suspended",
    "exclusion_decided",
    "not_found",
    "unknown",
}


@dataclass(frozen=True)
class RegistryFieldChange:
    field: str
    before: Any
    after: Any


@dataclass(frozen=True)
class RegistryDiff:
    """Deterministic semantic difference between two registry observations."""

    previous_snapshot_id: int | None
    current_snapshot_id: int | None
    subject_reference: str

    previous_status: str
    current_status: str

    classification: str
    changes: tuple[RegistryFieldChange, ...]

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    @property
    def status_changed(self) -> bool:
        return self.previous_status != self.current_status

    @property
    def status_transition(self) -> str | None:
        if not self.status_changed:
            return None
        return f"{self.previous_status}->{self.current_status}"

    @property
    def is_deterioration(self) -> bool:
        return self.classification == "deterioration"

    @property
    def is_improvement(self) -> bool:
        return self.classification == "improvement"


def _normalize_status(value: str | None) -> str:
    if value is None:
        return "unknown"
    normalized = value.strip().lower()
    return normalized or "unknown"


def _classify_status_transition(
    previous_status: str,
    current_status: str,
) -> str:
    if previous_status == current_status:
        return "unchanged"

    if (
        previous_status == "active"
        and current_status in _DETERIORATED_FROM_ACTIVE
    ):
        return "deterioration"

    if (
        current_status == "active"
        and previous_status in _IMPROVED_TO_ACTIVE
    ):
        return "improvement"

    return "change"


def diff_registry_snapshots(
    previous: RegistryMonitoringSnapshot,
    current: RegistryMonitoringSnapshot,
) -> RegistryDiff:
    """Compare two monitoring snapshots using normalized registry facts only.

    Raw payload, checksum, source URL and timestamps are deliberately excluded:
    a provider may republish the same registry facts with different bytes or
    transport metadata without creating a regulatory status change.
    """

    if previous.target_id != current.target_id:
        raise ValueError("registry snapshots belong to different monitoring targets")

    if previous.subject_reference != current.subject_reference:
        raise ValueError("registry snapshots have different subject references")

    changes: list[RegistryFieldChange] = []

    for field in SEMANTIC_FIELDS:
        before = getattr(previous, field)
        after = getattr(current, field)

        if field == "status_result":
            before = _normalize_status(before)
            after = _normalize_status(after)

        if before != after:
            changes.append(
                RegistryFieldChange(
                    field=field,
                    before=before,
                    after=after,
                )
            )

    previous_status = _normalize_status(previous.status_result)
    current_status = _normalize_status(current.status_result)

    classification = _classify_status_transition(
        previous_status,
        current_status,
    )

    # Non-status semantic changes are still meaningful even if the status
    # itself stayed unchanged.
    if classification == "unchanged" and changes:
        classification = "change"

    return RegistryDiff(
        previous_snapshot_id=previous.id,
        current_snapshot_id=current.id,
        subject_reference=current.subject_reference,
        previous_status=previous_status,
        current_status=current_status,
        classification=classification,
        changes=tuple(changes),
    )
