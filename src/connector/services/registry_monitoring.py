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


# ---------------------------------------------------------------------------
# R3D — impact analysis
# ---------------------------------------------------------------------------

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connector.models import (
    Contract,
    ExpectedPayment,
    ExpectedPaymentStatus,
    Invoice,
    Operation,
    OperationState,
    RegistryMonitoringTarget,
)


IMPACTED_PAYMENT_STATUSES = (
    ExpectedPaymentStatus.PENDING_AML,
    ExpectedPaymentStatus.AML_REVIEW,
    ExpectedPaymentStatus.AML_APPROVED,
    ExpectedPaymentStatus.SENT,
)

TERMINAL_OPERATION_STATES = (
    OperationState.CLOSED,
    OperationState.REJECTED,
    OperationState.EXPIRED,
    OperationState.ORPHANED,
)


@dataclass(frozen=True)
class RegistryImpact:
    """Read-only operational impact of a registry deterioration."""

    target_id: int | None
    counterparty_id: int | None
    subject_reference: str

    operation_ids: tuple[int, ...]
    expected_payment_ids: tuple[int, ...]

    @property
    def has_impact(self) -> bool:
        return bool(self.operation_ids or self.expected_payment_ids)


async def analyze_registry_impact(
    session: AsyncSession,
    *,
    target: RegistryMonitoringTarget,
    diff: RegistryDiff,
) -> RegistryImpact:
    """Find active operations/payments affected by registry deterioration.

    Important invariants:
    - no state/status is changed;
    - no historical compliance decision is rewritten;
    - subject_reference is not guessed from Counterparty attributes;
    - canonical target.counterparty_id is the only participant link.
    """

    if diff.subject_reference != target.subject_reference:
        raise ValueError(
            "registry diff subject does not match monitoring target"
        )

    # A target may intentionally exist only for evidence/monitoring and have
    # no canonical Counterparty mapping yet.
    if target.counterparty_id is None:
        return RegistryImpact(
            target_id=target.id,
            counterparty_id=None,
            subject_reference=target.subject_reference,
            operation_ids=(),
            expected_payment_ids=(),
        )

    # Improvements and neutral semantic changes do not produce adverse impact.
    if not diff.is_deterioration:
        return RegistryImpact(
            target_id=target.id,
            counterparty_id=target.counterparty_id,
            subject_reference=target.subject_reference,
            operation_ids=(),
            expected_payment_ids=(),
        )

    operation_result = await session.execute(
        select(Operation.id)
        .where(Operation.counterparty_id == target.counterparty_id)
        .where(Operation.state.not_in(TERMINAL_OPERATION_STATES))
        .order_by(Operation.id)
    )

    operation_ids = tuple(operation_result.scalars().all())

    # ExpectedPayment.operation_id is nullable. Therefore impact ownership is
    # resolved through the canonical invoice -> contract -> counterparty path,
    # not only through ExpectedPayment.operation_id.
    payment_result = await session.execute(
        select(ExpectedPayment.id)
        .join(Invoice, Invoice.id == ExpectedPayment.invoice_id)
        .join(Contract, Contract.id == Invoice.contract_id)
        .where(Contract.counterparty_id == target.counterparty_id)
        .where(ExpectedPayment.status.in_(IMPACTED_PAYMENT_STATUSES))
        .order_by(ExpectedPayment.id)
    )

    expected_payment_ids = tuple(payment_result.scalars().all())

    return RegistryImpact(
        target_id=target.id,
        counterparty_id=target.counterparty_id,
        subject_reference=target.subject_reference,
        operation_ids=operation_ids,
        expected_payment_ids=expected_payment_ids,
    )


# ---------------------------------------------------------------------------
# R3E-1 — registry refresh
# ---------------------------------------------------------------------------

from datetime import timedelta

from connector.adapters.stage3.adapters import (
    RegistryResult,
    get_registry_adapter,
)


@dataclass(frozen=True)
class RegistryRefreshResult:
    """Result of one participant-level registry refresh."""

    target_id: int | None
    snapshot: RegistryMonitoringSnapshot
    diff: RegistryDiff | None


class RegistryMonitoringService:
    """Refresh participant registry state without mutating operation history."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        adapter_factory=get_registry_adapter,
    ):
        self.session = session
        self.adapter_factory = adapter_factory

    async def refresh_target(
        self,
        target: RegistryMonitoringTarget,
    ) -> RegistryRefreshResult:
        """Fetch and persist one immutable monitoring observation.

        The service:
        - reads the provider configured on target.source_id;
        - preserves every provider observation as a new snapshot;
        - compares against the immediately previous snapshot;
        - updates only mutable scheduler state on the target.

        It does NOT:
        - modify Operation / ExpectedPayment states;
        - rewrite operation-scoped RegistrySnapshot;
        - create alerts;
        - commit the transaction.
        """

        previous_result = await self.session.execute(
            select(RegistryMonitoringSnapshot)
            .where(RegistryMonitoringSnapshot.target_id == target.id)
            .order_by(
                RegistryMonitoringSnapshot.observed_at.desc(),
                RegistryMonitoringSnapshot.id.desc(),
            )
            .limit(1)
        )
        previous = previous_result.scalar_one_or_none()

        adapter = self.adapter_factory(target.source_id)
        result: RegistryResult = await adapter.check(target.subject_reference)

        if result.subject_ref != target.subject_reference:
            raise ValueError(
                "registry provider returned a different subject reference"
            )

        snapshot = RegistryMonitoringSnapshot(
            target_id=target.id,
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

        self.session.add(snapshot)
        await self.session.flush()

        diff = (
            diff_registry_snapshots(previous, snapshot)
            if previous is not None
            else None
        )

        target.last_checked_at = result.checked_at
        target.next_check_at = result.checked_at + timedelta(
            seconds=target.refresh_interval_seconds
        )

        return RegistryRefreshResult(
            target_id=target.id,
            snapshot=snapshot,
            diff=diff,
        )


# ---------------------------------------------------------------------------
# R3E-2 — deterioration alerts
# ---------------------------------------------------------------------------

from connector.alerts import send_alert
from connector.models import Alert


@dataclass(frozen=True)
class RegistryAlertResult:
    """Alert outcome for one completed registry refresh."""

    alert: Alert | None
    impact: RegistryImpact | None


async def create_registry_deterioration_alert(
    session: AsyncSession,
    *,
    target: RegistryMonitoringTarget,
    refresh: RegistryRefreshResult,
) -> RegistryAlertResult:
    """Create one alert for a newly observed adverse registry transition.

    Alert generation is deliberately downstream from refresh/diff:
    repeated provider observations with unchanged normalized facts therefore
    do not create duplicate alerts.

    No Operation, ExpectedPayment or ComplianceDecision state is modified.
    """

    diff = refresh.diff

    if diff is None or not diff.is_deterioration:
        return RegistryAlertResult(
            alert=None,
            impact=None,
        )

    impact = await analyze_registry_impact(
        session,
        target=target,
        diff=diff,
    )

    severity = "error" if impact.has_impact else "warning"

    details = {
        "event_type": "registry_status_deterioration",
        "target_id": target.id,
        "counterparty_id": target.counterparty_id,
        "subject_reference": target.subject_reference,
        "registry_type": target.registry_type,
        "source_id": target.source_id,
        "previous_snapshot_id": diff.previous_snapshot_id,
        "current_snapshot_id": diff.current_snapshot_id,
        "previous_status": diff.previous_status,
        "current_status": diff.current_status,
        "status_transition": diff.status_transition,
        "changed_fields": [
            change.field
            for change in diff.changes
        ],
        "operation_ids": list(impact.operation_ids),
        "expected_payment_ids": list(
            impact.expected_payment_ids
        ),
    }

    alert = await send_alert(
        session,
        severity,
        "Registry Status Deterioration",
        details,
    )

    return RegistryAlertResult(
        alert=alert,
        impact=impact,
    )


# ---------------------------------------------------------------------------
# R3E-3A — due target scheduling
# ---------------------------------------------------------------------------


async def get_due_registry_targets(
    session: AsyncSession,
    *,
    now=None,
) -> tuple[RegistryMonitoringTarget, ...]:
    """Return enabled registry targets whose refresh is due.

    A target is due when:
    - it has never been scheduled/refreshed (next_check_at IS NULL), or
    - next_check_at <= now.

    The function is read-only and deterministic; actual provider calls are
    performed separately by RegistryMonitoringService.
    """

    from connector.models import utcnow

    effective_now = now or utcnow()

    result = await session.execute(
        select(RegistryMonitoringTarget)
        .where(RegistryMonitoringTarget.enabled.is_(True))
        .where(
            (
                RegistryMonitoringTarget.next_check_at.is_(None)
            )
            | (
                RegistryMonitoringTarget.next_check_at
                <= effective_now
            )
        )
        .order_by(RegistryMonitoringTarget.id)
    )

    return tuple(result.scalars().all())
