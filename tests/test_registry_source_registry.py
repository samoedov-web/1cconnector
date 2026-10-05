import pytest

# Importing the adapter module performs provider registration, consistent
# with the existing decorator-based source architecture.
from connector.adapters.stage3.adapters import (
    MockRegistryAdapter,
    get_registry_adapter,
)
from connector.sources.registry import (
    create_registry_source,
    registry_source_codes,
)


def test_mock_registry_source_is_registered():
    assert "mock" in registry_source_codes()


def test_create_registry_source_uses_canonical_registry():
    adapter = create_registry_source("mock")

    assert isinstance(adapter, MockRegistryAdapter)


def test_unknown_registry_source_fails_explicitly():
    with pytest.raises(LookupError, match="no-such-registry"):
        create_registry_source("no-such-registry")


def test_legacy_registry_factory_remains_compatible():
    adapter = get_registry_adapter("mock")

    assert isinstance(adapter, MockRegistryAdapter)


def test_legacy_registry_factory_preserves_value_error_contract():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_registry_adapter("no-such-registry")
