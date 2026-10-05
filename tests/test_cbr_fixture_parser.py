from datetime import datetime, timezone
from pathlib import Path

import pytest

from connector.adapters.stage3.cbr_parser import (
    NormalizedCBRFixtureParser,
)
from connector.adapters.stage3.cbr_registry import (
    CBRRawResponse,
    CBRRegistryRequest,
    CBRRegistryType,
)


FIXTURES = Path("tests/fixtures/cbr")


@pytest.mark.parametrize(
    ("fixture_name", "registry_type", "subject_ref", "entry_id"),
    [
        (
            "digital_currency_exchange.json",
            CBRRegistryType.DIGITAL_CURRENCY_EXCHANGE,
            "7700000000",
            "OOOCV-TEST-001",
        ),
        (
            "digital_depository.json",
            CBRRegistryType.DIGITAL_DEPOSITORY,
            "7800000000",
            "CD-TEST-001",
        ),
        (
            "information_system_operator.json",
            CBRRegistryType.INFORMATION_SYSTEM_OPERATOR,
            "7900000000",
            "OIS-TEST-001",
        ),
    ],
)
def test_normalized_fixture_parser(
    fixture_name,
    registry_type,
    subject_ref,
    entry_id,
):
    content = (FIXTURES / fixture_name).read_bytes()

    request = CBRRegistryRequest(
        registry_type=registry_type,
        subject_ref=subject_ref,
    )

    raw = CBRRawResponse(
        registry_type=registry_type,
        source_url="https://cbr.test/registry",
        content=content,
        content_type="application/json",
        checked_at=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
    )

    result = NormalizedCBRFixtureParser().parse(request, raw)

    assert result.subject_ref == subject_ref
    assert result.registry_type == registry_type.value
    assert result.registry_entry_id == entry_id
    assert result.status == "active"
    assert result.source == "CBR"
    assert result.source_url == "https://cbr.test/registry"
    assert len(result.checksum) == 64
    assert result.checked_at.tzinfo is not None


def test_fixture_parser_rejects_subject_mismatch():
    content = (
        FIXTURES / "digital_currency_exchange.json"
    ).read_bytes()

    request = CBRRegistryRequest(
        registry_type=CBRRegistryType.DIGITAL_CURRENCY_EXCHANGE,
        subject_ref="WRONG",
    )

    raw = CBRRawResponse(
        registry_type=CBRRegistryType.DIGITAL_CURRENCY_EXCHANGE,
        source_url="https://cbr.test/registry",
        content=content,
        content_type="application/json",
        checked_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValueError, match="subject_ref"):
        NormalizedCBRFixtureParser().parse(request, raw)
