from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from connector.models import (
    RegistryMonitoringSnapshot,
    RegistryMonitoringTarget,
)


@pytest.mark.asyncio
async def test_registry_monitoring_target_can_be_persisted(async_session):
    target = RegistryMonitoringTarget(
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
        refresh_interval_seconds=3600,
    )
    async_session.add(target)
    await async_session.flush()

    stored = await async_session.scalar(
        select(RegistryMonitoringTarget).where(
            RegistryMonitoringTarget.id == target.id
        )
    )

    assert stored is not None
    assert stored.subject_reference == "7707083893"
    assert stored.registry_type == "ois"
    assert stored.source_id == "cbr-ois"
    assert stored.enabled is True
    assert stored.refresh_interval_seconds == 3600


@pytest.mark.asyncio
async def test_registry_monitoring_snapshot_is_participant_scoped(
    async_session,
):
    target = RegistryMonitoringTarget(
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )
    async_session.add(target)
    await async_session.flush()

    observed_at = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)

    snapshot = RegistryMonitoringSnapshot(
        target_id=target.id,
        subject_reference=target.subject_reference,
        source="cbr-ois",
        status_result="active",
        snapshot_payload={"status": "active"},
        checksum="a" * 64,
        observed_at=observed_at,
        registry_type="ois",
        registry_entry_id=None,
        valid_from=None,
        valid_to=None,
        admission_mode=None,
        application_id=None,
        admission_date=None,
        legal_basis=None,
        source_url="https://www.cbr.ru/",
    )
    async_session.add(snapshot)
    await async_session.flush()

    stored = await async_session.scalar(
        select(RegistryMonitoringSnapshot).where(
            RegistryMonitoringSnapshot.id == snapshot.id
        )
    )

    assert stored is not None
    assert stored.target_id == target.id
    assert stored.subject_reference == "7707083893"
    assert stored.status_result == "active"
    assert stored.snapshot_payload == {"status": "active"}

    # Ключевой архитектурный инвариант R3:
    # participant monitoring не требует operation_id.
    assert not hasattr(RegistryMonitoringSnapshot, "operation_id")


@pytest.mark.asyncio
async def test_registry_monitoring_target_can_link_canonical_counterparty(
    async_session,
    sample_operation,
):
    target = RegistryMonitoringTarget(
        counterparty_id=sample_operation.counterparty_id,
        subject_reference="7707083893",
        registry_type="ois",
        source_id="cbr-ois",
    )
    async_session.add(target)
    await async_session.flush()

    stored = await async_session.scalar(
        select(RegistryMonitoringTarget).where(
            RegistryMonitoringTarget.id == target.id
        )
    )

    assert stored is not None
    assert stored.counterparty_id == sample_operation.counterparty_id


@pytest.mark.asyncio
async def test_registry_monitoring_target_counterparty_link_is_optional(
    async_session,
):
    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="TECHNICAL-SUBJECT",
        registry_type="ois",
        source_id="mock",
    )
    async_session.add(target)
    await async_session.flush()

    assert target.id is not None
    assert target.counterparty_id is None
