from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from connector.adapters.stage3.adapters import RegistryResult
from connector.models import (
    RegistryMonitoringSnapshot,
    RegistryMonitoringTarget,
)
from connector.services.registry_monitoring import RegistryMonitoringService


class SequenceRegistryAdapter:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def check(self, ref):
        self.calls.append(ref)
        if not self.results:
            raise AssertionError("no registry result configured")
        return self.results.pop(0)


def make_result(
    *,
    status="active",
    checked_at=None,
    checksum=None,
    raw_payload=None,
    subject_ref="7707083893",
):
    checked_at = checked_at or datetime(
        2026,
        10,
        6,
        9,
        0,
        tzinfo=timezone.utc,
    )

    return RegistryResult(
        subject_ref=subject_ref,
        status=status,
        registry_type="ois",
        record_number=None,
        checked_at=checked_at,
        raw_payload=raw_payload or {"status": status},
        checksum=checksum or ("a" * 64),
        source="cbr-ois",
        source_url=(
            "https://www.cbr.ru/vfs/finmarkets/files/"
            "supervision/list_ois.xlsx"
        ),
        registry_entry_id=None,
        admission_mode=None,
        valid_from=None,
        valid_to=None,
        application_id=None,
        admission_date=None,
        legal_basis=None,
    )


@pytest.mark.asyncio
async def test_first_refresh_persists_snapshot_and_updates_schedule(
    async_session,
    sample_operation,
):
    checked_at = datetime(
        2026,
        10,
        6,
        9,
        0,
        tzinfo=timezone.utc,
    )

    adapter = SequenceRegistryAdapter(
        [
            make_result(
                status="active",
                checked_at=checked_at,
            )
        ]
    )

    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
        refresh_interval_seconds=3600,
    )
    async_session.add(target)
    await async_session.flush()

    service = RegistryMonitoringService(
        async_session,
        adapter_factory=lambda source_id: adapter,
    )

    result = await service.refresh_target(target)

    assert result.target_id == target.id
    assert result.diff is None

    assert result.snapshot.id is not None
    assert result.snapshot.target_id == target.id
    assert result.snapshot.status_result == "active"
    assert result.snapshot.subject_reference == "7707083893"

    assert target.last_checked_at == checked_at
    assert target.next_check_at == checked_at + timedelta(hours=1)

    assert adapter.calls == ["7707083893"]

    count = await async_session.scalar(
        select(func.count())
        .select_from(RegistryMonitoringSnapshot)
        .where(RegistryMonitoringSnapshot.target_id == target.id)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_second_refresh_computes_diff_against_previous_snapshot(
    async_session,
    sample_operation,
):
    first_at = datetime(
        2026,
        10,
        6,
        9,
        0,
        tzinfo=timezone.utc,
    )
    second_at = datetime(
        2026,
        10,
        6,
        10,
        0,
        tzinfo=timezone.utc,
    )

    adapter = SequenceRegistryAdapter(
        [
            make_result(
                status="active",
                checked_at=first_at,
                checksum="a" * 64,
            ),
            make_result(
                status="excluded",
                checked_at=second_at,
                checksum="b" * 64,
            ),
        ]
    )

    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
        refresh_interval_seconds=3600,
    )
    async_session.add(target)
    await async_session.flush()

    service = RegistryMonitoringService(
        async_session,
        adapter_factory=lambda source_id: adapter,
    )

    first = await service.refresh_target(target)
    second = await service.refresh_target(target)

    assert first.diff is None

    assert second.diff is not None
    assert second.diff.is_deterioration is True
    assert second.diff.status_transition == "active->excluded"

    assert second.diff.previous_snapshot_id == first.snapshot.id
    assert second.diff.current_snapshot_id == second.snapshot.id

    count = await async_session.scalar(
        select(func.count())
        .select_from(RegistryMonitoringSnapshot)
        .where(RegistryMonitoringSnapshot.target_id == target.id)
    )
    assert count == 2

    assert target.last_checked_at == second_at
    assert target.next_check_at == second_at + timedelta(hours=1)


@pytest.mark.asyncio
async def test_republished_same_facts_produces_unchanged_diff(
    async_session,
):
    first_at = datetime(
        2026,
        10,
        6,
        9,
        0,
        tzinfo=timezone.utc,
    )
    second_at = datetime(
        2026,
        10,
        6,
        10,
        0,
        tzinfo=timezone.utc,
    )

    adapter = SequenceRegistryAdapter(
        [
            make_result(
                status="active",
                checked_at=first_at,
                checksum="a" * 64,
                raw_payload={"version": 1, "status": "active"},
            ),
            make_result(
                status="active",
                checked_at=second_at,
                checksum="b" * 64,
                raw_payload={"version": 2, "status": "active"},
            ),
        ]
    )

    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )
    async_session.add(target)
    await async_session.flush()

    service = RegistryMonitoringService(
        async_session,
        adapter_factory=lambda source_id: adapter,
    )

    await service.refresh_target(target)
    refreshed = await service.refresh_target(target)

    assert refreshed.diff is not None
    assert refreshed.diff.changed is False
    assert refreshed.diff.classification == "unchanged"


@pytest.mark.asyncio
async def test_provider_subject_mismatch_is_rejected_without_snapshot(
    async_session,
):
    adapter = SequenceRegistryAdapter(
        [
            make_result(
                status="active",
                subject_ref="1234567890",
            )
        ]
    )

    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )
    async_session.add(target)
    await async_session.flush()

    service = RegistryMonitoringService(
        async_session,
        adapter_factory=lambda source_id: adapter,
    )

    with pytest.raises(
        ValueError,
        match="different subject reference",
    ):
        await service.refresh_target(target)

    count = await async_session.scalar(
        select(func.count())
        .select_from(RegistryMonitoringSnapshot)
        .where(RegistryMonitoringSnapshot.target_id == target.id)
    )

    assert count == 0
    assert target.last_checked_at is None
    assert target.next_check_at is None


@pytest.mark.asyncio
async def test_provider_failure_does_not_advance_target_schedule(
    async_session,
):
    class FailingAdapter:
        async def check(self, ref):
            raise RuntimeError("registry provider unavailable")

    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )
    async_session.add(target)
    await async_session.flush()

    service = RegistryMonitoringService(
        async_session,
        adapter_factory=lambda source_id: FailingAdapter(),
    )

    with pytest.raises(
        RuntimeError,
        match="registry provider unavailable",
    ):
        await service.refresh_target(target)

    count = await async_session.scalar(
        select(func.count())
        .select_from(RegistryMonitoringSnapshot)
        .where(RegistryMonitoringSnapshot.target_id == target.id)
    )

    assert count == 0
    assert target.last_checked_at is None
    assert target.next_check_at is None


@pytest.mark.asyncio
async def test_refresh_does_not_commit_session(
    async_session,
):
    adapter = SequenceRegistryAdapter(
        [make_result(status="active")]
    )

    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )
    async_session.add(target)
    await async_session.flush()

    service = RegistryMonitoringService(
        async_session,
        adapter_factory=lambda source_id: adapter,
    )

    await service.refresh_target(target)

    # The caller remains responsible for the transaction boundary.
    assert async_session.in_transaction() is True


@pytest.mark.asyncio
async def test_refresh_uses_canonical_registry_factory_for_mock(
    async_session,
):
    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="7700000000",
        registry_type="DIGITAL_CURRENCY_EXCHANGE",
        source_id="mock",
        refresh_interval_seconds=3600,
    )
    async_session.add(target)
    await async_session.flush()

    # No adapter_factory injection here:
    # RegistryMonitoringService must use canonical get_registry_adapter().
    service = RegistryMonitoringService(async_session)

    refreshed = await service.refresh_target(target)

    assert refreshed.snapshot.id is not None
    assert refreshed.snapshot.subject_reference == "7700000000"
    assert refreshed.snapshot.status_result == "active"
    assert refreshed.snapshot.registry_type == "DIGITAL_CURRENCY_EXCHANGE"
    assert refreshed.snapshot.registry_entry_id == "MOCK-12345"
    assert refreshed.snapshot.source == "mock_cbr"

    assert refreshed.diff is None

    assert target.last_checked_at is not None
    assert target.next_check_at is not None
    assert target.next_check_at > target.last_checked_at
