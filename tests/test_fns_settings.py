"""Configuration tests for FNS Tax Intelligence."""

import pytest
from pydantic import ValidationError

from connector.config import Settings


def test_fns_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CONNECTOR_FNS_RATE_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.fns_rate_enabled is False


def test_fns_defaults(monkeypatch):
    for name in (
        "CONNECTOR_FNS_RATE_ENABLED",
        "CONNECTOR_FNS_RATE_REFRESH_HOURS",
        "CONNECTOR_FNS_RATE_MAX_QUOTE_AGE_HOURS",
        "CONNECTOR_FNS_EVIDENCE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.fns_rate_refresh_hours == 24
    assert settings.fns_rate_max_quote_age_hours == 48
    assert settings.fns_evidence_dir == "/var/lib/connector/fns-evidence"


def test_fns_environment_override(monkeypatch):
    monkeypatch.setenv("CONNECTOR_FNS_RATE_ENABLED", "true")
    monkeypatch.setenv("CONNECTOR_FNS_RATE_REFRESH_HOURS", "12")
    monkeypatch.setenv(
        "CONNECTOR_FNS_RATE_MAX_QUOTE_AGE_HOURS", "24"
    )
    monkeypatch.setenv(
        "CONNECTOR_FNS_EVIDENCE_DIR", "/var/lib/connector/fns"
    )

    settings = Settings(_env_file=None)

    assert settings.fns_rate_enabled is True
    assert settings.fns_rate_refresh_hours == 12
    assert settings.fns_rate_max_quote_age_hours == 24
    assert settings.fns_evidence_dir == "/var/lib/connector/fns"


@pytest.mark.parametrize(
    "field,value",
    [
        ("fns_rate_refresh_hours", 0),
        ("fns_rate_refresh_hours", -1),
        ("fns_rate_refresh_hours", 8761),
        ("fns_rate_max_quote_age_hours", 0),
        ("fns_rate_max_quote_age_hours", -1),
        ("fns_rate_max_quote_age_hours", 8761),
    ],
)
def test_fns_invalid_intervals(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_fns_does_not_change_existing_defaults(monkeypatch):
    monkeypatch.delenv("CONNECTOR_CUSTODY_MODE", raising=False)
    monkeypatch.delenv("CONNECTOR_REGISTRY_SOURCE_ID", raising=False)

    settings = Settings(_env_file=None)

    assert settings.custody_mode == "off"
    assert settings.registry_source_id == "mock"
