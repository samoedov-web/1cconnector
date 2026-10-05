from datetime import datetime, timezone
from pathlib import Path

import pytest

from connector.adapters.stage3.cbr_parser import (
    CBROISXlsxParser,
)
from connector.adapters.stage3.cbr_registry import (
    CBRRawResponse,
    CBRRegistryRequest,
    CBRRegistryType,
)


FIXTURE = Path(
    "tests/fixtures/cbr/native/list_ois_2026-10-04.xlsx"
)


def raw_response() -> CBRRawResponse:
    return CBRRawResponse(
        registry_type=CBRRegistryType.INFORMATION_SYSTEM_OPERATOR,
        source_url=(
            "https://www.cbr.ru/vfs/finmarkets/"
            "files/supervision/list_ois.xlsx"
        ),
        content=FIXTURE.read_bytes(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        checked_at=datetime(
            2026,
            10,
            5,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )


def test_native_ois_parser_finds_sber_by_inn():
    request = CBRRegistryRequest(
        registry_type=CBRRegistryType.INFORMATION_SYSTEM_OPERATOR,
        subject_ref="7707083893",
    )

    result = CBROISXlsxParser().parse(
        request,
        raw_response(),
    )

    assert result.status == "active"
    assert result.registry_type == "INFORMATION_SYSTEM_OPERATOR"
    assert result.registry_entry_id is None
    assert result.admission_mode is None

    assert result.valid_from is not None
    assert result.valid_from.date().isoformat() == "2022-03-17"

    assert result.raw_payload["matched"] is True
    assert (
        result.raw_payload["registry_as_of"][:10]
        == "2026-10-04"
    )
    assert result.raw_payload["record"]["inn"] == "7707083893"
    assert result.raw_payload["record"]["ogrn"] == "1027700132195"


def test_native_ois_parser_finds_atomyze_by_ogrn():
    request = CBRRegistryRequest(
        registry_type=CBRRegistryType.INFORMATION_SYSTEM_OPERATOR,
        subject_ref="1207700427714",
    )

    result = CBROISXlsxParser().parse(
        request,
        raw_response(),
    )

    assert result.status == "active"
    assert result.raw_payload["record"]["inn"] == "9703021466"
    assert result.valid_from.date().isoformat() == "2022-02-03"


def test_native_ois_parser_returns_not_found_with_evidence():
    request = CBRRegistryRequest(
        registry_type=CBRRegistryType.INFORMATION_SYSTEM_OPERATOR,
        subject_ref="0000000000",
    )

    result = CBROISXlsxParser().parse(
        request,
        raw_response(),
    )

    assert result.status == "not_found"
    assert result.registry_entry_id is None
    assert result.raw_payload["matched"] is False
    assert result.raw_payload["registry_as_of"][:10] == "2026-10-04"
    assert len(result.checksum) == 64


def test_native_ois_parser_rejects_wrong_registry_type():
    request = CBRRegistryRequest(
        registry_type=CBRRegistryType.DIGITAL_CURRENCY_EXCHANGE,
        subject_ref="7707083893",
    )

    with pytest.raises(
        ValueError,
        match="INFORMATION_SYSTEM_OPERATOR",
    ):
        CBROISXlsxParser().parse(
            request,
            raw_response(),
        )


def test_native_ois_parser_preserves_source_url_and_check_time():
    request = CBRRegistryRequest(
        registry_type=CBRRegistryType.INFORMATION_SYSTEM_OPERATOR,
        subject_ref="7728168971",
    )

    result = CBROISXlsxParser().parse(
        request,
        raw_response(),
    )

    assert result.source == "CBR"
    assert result.source_url.endswith("list_ois.xlsx")
    assert result.checked_at == datetime(
        2026,
        10,
        5,
        12,
        0,
        tzinfo=timezone.utc,
    )
