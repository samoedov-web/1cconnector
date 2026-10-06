from decimal import Decimal

import pytest
from sqlalchemy import func, select

from connector.models import (
    Alert,
    ExpectedPayment,
    ExpectedPaymentStatus,
    OperationState,
    RegistryMonitoringTarget,
)
from connector.services.registry_monitoring import (
    RegistryDiff,
    RegistryFieldChange,
    RegistryRefreshResult,
    create_registry_deterioration_alert,
)


def make_diff(
    *,
    classification="deterioration",
    previous_status="active",
    current_status="excluded",
):
    changes = ()

    if previous_status != current_status:
        changes = (
            RegistryFieldChange(
                field="status_result",
                before=previous_status,
                after=current_status,
            ),
        )

    return RegistryDiff(
        previous_snapshot_id=10,
        current_snapshot_id=11,
        subject_reference="7707083893",
        previous_status=previous_status,
        current_status=current_status,
        classification=classification,
        changes=changes,
    )


def make_refresh(diff):
    class Snapshot:
        id = 11

    return RegistryRefreshResult(
        target_id=1,
        snapshot=Snapshot(),
        diff=diff,
    )


@pytest.mark.asyncio
async def test_deterioration_with_operational_impact_creates_error_alert(
    async_session,
    sample_operation,
    sample_invoice,
):
    sample_operation.state = OperationState.APPROVED

    payment = ExpectedPayment(
        operation_id=sample_operation.id,
        invoice_id=sample_invoice.id,
        to_address="TRegistryAlertImpact",
        network="tron",
        amount=Decimal("100"),
        currency="USDT",
        tolerance=Decimal("0.005"),
        status=ExpectedPaymentStatus.AML_APPROVED,
    )

    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add_all([payment, target])
    await async_session.flush()

    operation_state_before = sample_operation.state
    payment_status_before = payment.status

    outcome = await create_registry_deterioration_alert(
        async_session,
        target=target,
        refresh=make_refresh(make_diff()),
    )

    assert outcome.alert is not None
    assert outcome.alert.severity == "error"
    assert outcome.alert.title == "Registry Status Deterioration"

    assert outcome.impact is not None
    assert outcome.impact.has_impact is True

    details = outcome.alert.details

    assert details["event_type"] == "registry_status_deterioration"
    assert details["subject_reference"] == "7707083893"
    assert details["status_transition"] == "active->excluded"
    assert details["operation_ids"] == [sample_operation.id]
    assert details["expected_payment_ids"] == [payment.id]

    # Registry monitoring must never mutate operational history/state.
    assert sample_operation.state == operation_state_before
    assert payment.status == payment_status_before


@pytest.mark.asyncio
async def test_deterioration_without_canonical_impact_creates_warning(
    async_session,
):
    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )

    async_session.add(target)
    await async_session.flush()

    outcome = await create_registry_deterioration_alert(
        async_session,
        target=target,
        refresh=make_refresh(make_diff()),
    )

    assert outcome.alert is not None
    assert outcome.alert.severity == "warning"

    assert outcome.impact is not None
    assert outcome.impact.has_impact is False

    assert outcome.alert.details["operation_ids"] == []
    assert outcome.alert.details["expected_payment_ids"] == []


@pytest.mark.asyncio
async def test_first_observation_does_not_create_alert(
    async_session,
):
    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )
    async_session.add(target)
    await async_session.flush()

    outcome = await create_registry_deterioration_alert(
        async_session,
        target=target,
        refresh=make_refresh(None),
    )

    assert outcome.alert is None
    assert outcome.impact is None

    count = await async_session.scalar(
        select(func.count()).select_from(Alert)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_improvement_does_not_create_alert(
    async_session,
):
    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )
    async_session.add(target)
    await async_session.flush()

    diff = make_diff(
        classification="improvement",
        previous_status="not_found",
        current_status="active",
    )

    outcome = await create_registry_deterioration_alert(
        async_session,
        target=target,
        refresh=make_refresh(diff),
    )

    assert outcome.alert is None
    assert outcome.impact is None

    count = await async_session.scalar(
        select(func.count()).select_from(Alert)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_repeated_same_adverse_status_does_not_duplicate_alert(
    async_session,
):
    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )
    async_session.add(target)
    await async_session.flush()

    # First semantic transition creates the alert.
    deterioration = make_diff(
        classification="deterioration",
        previous_status="active",
        current_status="excluded",
    )

    first = await create_registry_deterioration_alert(
        async_session,
        target=target,
        refresh=make_refresh(deterioration),
    )

    assert first.alert is not None

    # Next refresh sees excluded -> excluded.
    unchanged = make_diff(
        classification="unchanged",
        previous_status="excluded",
        current_status="excluded",
    )

    second = await create_registry_deterioration_alert(
        async_session,
        target=target,
        refresh=make_refresh(unchanged),
    )

    assert second.alert is None

    count = await async_session.scalar(
        select(func.count())
        .select_from(Alert)
        .where(Alert.title == "Registry Status Deterioration")
    )

    assert count == 1


@pytest.mark.asyncio
async def test_refresh_to_deterioration_creates_alert_end_to_end(
    async_session,
    sample_operation,
    sample_invoice,
):
    from datetime import datetime, timezone

    from connector.adapters.stage3.adapters import RegistryResult
    from connector.services.registry_monitoring import RegistryMonitoringService

    class SequenceAdapter:
        def __init__(self):
            self.results = [
                RegistryResult(
                    subject_ref="7707083893",
                    status="active",
                    registry_type="ois",
                    record_number=None,
                    checked_at=datetime(
                        2026, 10, 6, 9, 0, tzinfo=timezone.utc
                    ),
                    raw_payload={"status": "active"},
                    checksum="a" * 64,
                    source="cbr-ois",
                    source_url="https://www.cbr.ru/registry.xlsx",
                    registry_entry_id=None,
                    admission_mode=None,
                    valid_from=None,
                    valid_to=None,
                    application_id=None,
                    admission_date=None,
                    legal_basis=None,
                ),
                RegistryResult(
                    subject_ref="7707083893",
                    status="excluded",
                    registry_type="ois",
                    record_number=None,
                    checked_at=datetime(
                        2026, 10, 6, 10, 0, tzinfo=timezone.utc
                    ),
                    raw_payload={"status": "excluded"},
                    checksum="b" * 64,
                    source="cbr-ois",
                    source_url="https://www.cbr.ru/registry.xlsx",
                    registry_entry_id=None,
                    admission_mode=None,
                    valid_from=None,
                    valid_to=None,
                    application_id=None,
                    admission_date=None,
                    legal_basis=None,
                ),
            ]

        async def check(self, ref):
            return self.results.pop(0)

    sample_operation.state = OperationState.APPROVED

    payment = ExpectedPayment(
        operation_id=sample_operation.id,
        invoice_id=sample_invoice.id,
        to_address="TRegistryE2E",
        network="tron",
        amount=Decimal("100"),
        currency="USDT",
        tolerance=Decimal("0.005"),
        status=ExpectedPaymentStatus.AML_APPROVED,
    )

    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
        refresh_interval_seconds=3600,
    )

    async_session.add_all([payment, target])
    await async_session.flush()

    adapter = SequenceAdapter()

    service = RegistryMonitoringService(
        async_session,
        adapter_factory=lambda source_id: adapter,
    )

    first_refresh = await service.refresh_target(target)

    first_alert = await create_registry_deterioration_alert(
        async_session,
        target=target,
        refresh=first_refresh,
    )

    assert first_refresh.diff is None
    assert first_alert.alert is None

    second_refresh = await service.refresh_target(target)

    assert second_refresh.diff is not None
    assert second_refresh.diff.is_deterioration is True
    assert second_refresh.diff.status_transition == "active->excluded"

    second_alert = await create_registry_deterioration_alert(
        async_session,
        target=target,
        refresh=second_refresh,
    )

    assert second_alert.alert is not None
    assert second_alert.alert.severity == "error"

    assert second_alert.impact is not None
    assert second_alert.impact.operation_ids == (sample_operation.id,)
    assert second_alert.impact.expected_payment_ids == (payment.id,)

    assert second_alert.alert.details["status_transition"] == (
        "active->excluded"
    )

    count = await async_session.scalar(
        select(func.count())
        .select_from(Alert)
        .where(Alert.title == "Registry Status Deterioration")
    )

    assert count == 1
