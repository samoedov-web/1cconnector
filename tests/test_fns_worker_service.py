"""FNS worker service configuration tests."""

from datetime import timedelta
from pathlib import Path

import pytest

from connector.models import FnsMonitoringTarget
from connector.rates.fns_evidence_store import FnsEvidenceStore
from connector.rates.fns_worker_service import build_fns_target_service


def make_target(*, organizer="TEST", pair="BTCRUB"):
    return FnsMonitoringTarget(
        base_asset="BTC",
        source_id="fns-krptrate",
        organizer=organizer,
        trading_pair=pair,
        enabled=True,
        refresh_interval_seconds=3600,
    )


def test_service_binds_exact_target_selectors(tmp_path: Path):
    target = make_target()
    store = FnsEvidenceStore(tmp_path / "fns-evidence")

    service = build_fns_target_service(
        target,
        evidence_store=store,
        max_quote_age=timedelta(hours=48),
    )

    assert service.provider.organizer == "TEST"
    assert service.provider.trading_pair == "BTCRUB"
    assert service.provider.require_persistence is True
    assert service.provider.evidence_store is store
    assert service.evidence_store is store
    assert service.provider.valuation_context == "tax"


@pytest.mark.parametrize(
    "field",
    ["organizer", "trading_pair"],
)
def test_missing_selector_rejected_before_service(tmp_path, field):
    target = make_target()
    setattr(target, field, None)

    store = FnsEvidenceStore(tmp_path / "fns-evidence")

    with pytest.raises(ValueError):
        build_fns_target_service(
            target,
            evidence_store=store,
            max_quote_age=timedelta(hours=48),
        )


def test_invalid_quote_age_rejected(tmp_path):
    store = FnsEvidenceStore(tmp_path / "fns-evidence")

    with pytest.raises(ValueError, match="max_quote_age"):
        build_fns_target_service(
            make_target(),
            evidence_store=store,
            max_quote_age=timedelta(0),
        )
