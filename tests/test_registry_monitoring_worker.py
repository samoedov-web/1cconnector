import pytest

from connector.models import RegistryMonitoringTarget
from connector.services.registry_monitoring import get_due_registry_targets


@pytest.mark.asyncio
async def test_due_target_can_be_reloaded_in_separate_transaction_boundary(
    async_session,
):
    target = RegistryMonitoringTarget(
        counterparty_id=None,
        subject_reference="WORKER-TARGET",
        registry_type="ois",
        source_id="mock",
        enabled=True,
        refresh_interval_seconds=3600,
        next_check_at=None,
    )
    async_session.add(target)
    await async_session.flush()

    due = await get_due_registry_targets(async_session)

    assert len(due) == 1
    assert due[0].id == target.id


def test_worker_contains_registry_monitoring_wiring():
    from pathlib import Path

    text = Path("src/connector/worker.py").read_text()

    assert "get_due_registry_targets" in text
    assert "RegistryMonitoringService" in text
    assert "create_registry_deterioration_alert" in text
    assert "Registry Refresh Failed" in text
    assert "Сбой планировщика Registry Intelligence" in text


def test_worker_registry_failure_path_does_not_raise_by_design():
    from pathlib import Path

    text = Path("src/connector/worker.py").read_text()

    # Structural regression guard: per-target refresh must be isolated.
    assert "for target in due_registry_targets:" in text
    assert "except Exception as exc:" in text
    assert "await session.commit()" in text
    assert "await alert_session.commit()" in text
