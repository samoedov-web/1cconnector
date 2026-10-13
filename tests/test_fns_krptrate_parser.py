import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from connector.rates.fns import (
    EXPECTED_FIELDS,
    FnsKrptrateParser,
    FrozenFnsKrptrateSource,
)


FIXTURE = Path(
    "tests/fixtures/fns/krptrate_2026-10-06_sample.json"
)

META = json.loads(
    Path(
        "tests/fixtures/fns/krptrate_2026-10-06_meta.json"
    ).read_text(encoding="utf-8")
)


def load_rows():
    return json.loads(
        FIXTURE.read_text(encoding="utf-8")
    )


def parser():
    return FnsKrptrateParser(
        source_url=META["source_url"],
        evidence_hash=META["official_zip_sha256"],
    )


def parse_dt(value):
    return datetime.strptime(
        value,
        "%d.%m.%Y %H:%M:%S",
    ).replace(tzinfo=timezone.utc)


def test_frozen_fixture_preserves_exact_native_schema():
    rows = load_rows()

    assert rows

    for row in rows:
        assert set(row) == EXPECTED_FIELDS


def test_fixture_meta_points_to_official_release():
    assert META["dataset"] == "7707329152-krptrate"
    assert META["release_date"] == "2026-10-06"
    assert META["structure_version"] == "20250519"
    assert META["methodology_version"] == "4.0"

    assert META["official_zip_sha256"] == (
        "3f68c04b22c0563c43cb6e72bd864ab"
        "51c950cc701417036540c3841a17d6edd"
    )


def test_parser_returns_exact_published_rub_quote():
    rows = load_rows()

    row = next(
        item
        for item in rows
        if item["kodVal"] == "BTC"
        and item["isAnnulled"] == 0
    )

    result = parser().parse(
        json.dumps(
            [row],
            ensure_ascii=False,
        ).encode("utf-8"),
        base_asset="BTC",
        as_of=datetime(
            2026,
            10,
            6,
            tzinfo=timezone.utc,
        ),
    )

    expected_rate = Decimal(
        row["krptKurs"].replace(",", ".")
    )

    assert result.base_asset == "BTC"
    assert result.quote_currency == "RUB"
    assert result.rate == expected_rate
    assert result.valuation_context == "tax"
    assert result.source == "fns-krptrate"
    assert result.methodology_version == "fns-krptrate-v4.0"

    # krptKurs is used as published by FNS.
    assert result.raw_payload["krptKurs"] == row["krptKurs"]

    # kursValuta remains evidence, not a second calculation performed here.
    assert result.raw_payload["kursValuta"] == row["kursValuta"]


def test_parser_normalizes_decimal_without_float():
    row = {
        "kursData": "01.10.2026 0:00:00",
        "kodVal": "TEST",
        "naimIst": "TEST",
        "krptKurs": "123,4567890123",
        "torgPara": "TESTRUB",
        "kursValuta": "84,1234567890",
        "isAnnulled": 0,
    }

    result = parser().parse(
        json.dumps([row]).encode("utf-8"),
        base_asset="TEST",
        as_of=datetime(
            2026,
            10,
            1,
            tzinfo=timezone.utc,
        ),
    )

    assert result.rate == Decimal("123.4567890123")
    assert isinstance(result.rate, Decimal)

    assert (
        result.raw_payload["_normalized"]["kursValuta"]
        == "84.1234567890"
    )


def test_annulled_record_is_ignored():
    row = next(
        item
        for item in load_rows()
        if item["isAnnulled"] == 1
    )

    with pytest.raises(
        LookupError,
        match="no active exact asset record",
    ):
        parser().parse(
            json.dumps(
                [row],
                ensure_ascii=False,
            ).encode("utf-8"),
            base_asset=row["kodVal"],
            as_of=datetime(
                2026,
                10,
                6,
                tzinfo=timezone.utc,
            ),
        )


def test_usdt_is_not_inferred_from_trading_pair():
    rows = load_rows()

    assert not any(
        row["kodVal"].upper() == "USDT"
        for row in rows
    )

    assert any(
        "USDT" in row["torgPara"].upper()
        for row in rows
    )

    with pytest.raises(
        LookupError,
        match="USDT",
    ):
        parser().parse(
            FIXTURE.read_bytes(),
            base_asset="USDT",
            as_of=datetime(
                2026,
                10,
                6,
                tzinfo=timezone.utc,
            ),
        )


def test_future_records_are_not_used_for_as_of():
    rows = [
        {
            "kursData": "30.09.2026 0:00:00",
            "kodVal": "TEST",
            "naimIst": "A",
            "krptKurs": "10,0",
            "torgPara": "TESTUSD",
            "kursValuta": "80,0",
            "isAnnulled": 0,
        },
        {
            "kursData": "01.10.2026 0:00:00",
            "kodVal": "TEST",
            "naimIst": "A",
            "krptKurs": "20,0",
            "torgPara": "TESTUSD",
            "kursValuta": "81,0",
            "isAnnulled": 0,
        },
    ]

    result = parser().parse(
        json.dumps(rows).encode("utf-8"),
        base_asset="TEST",
        as_of=datetime(
            2026,
            9,
            30,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert result.rate == Decimal("10.0")
    assert result.quote_as_of == datetime(
        2026,
        9,
        30,
        tzinfo=timezone.utc,
    )


def test_latest_available_date_is_selected():
    rows = [
        {
            "kursData": "29.09.2026 0:00:00",
            "kodVal": "TEST",
            "naimIst": "A",
            "krptKurs": "9,0",
            "torgPara": "TESTUSD",
            "kursValuta": "79,0",
            "isAnnulled": 0,
        },
        {
            "kursData": "30.09.2026 0:00:00",
            "kodVal": "TEST",
            "naimIst": "A",
            "krptKurs": "10,0",
            "torgPara": "TESTUSD",
            "kursValuta": "80,0",
            "isAnnulled": 0,
        },
    ]

    result = parser().parse(
        json.dumps(rows).encode("utf-8"),
        base_asset="TEST",
        as_of=datetime(
            2026,
            10,
            6,
            tzinfo=timezone.utc,
        ),
    )

    assert result.rate == Decimal("10.0")
    assert result.quote_as_of == datetime(
        2026,
        9,
        30,
        tzinfo=timezone.utc,
    )


def test_multiple_latest_records_fail_closed_without_selector():
    rows = load_rows()

    btc_latest = max(
        parse_dt(row["kursData"])
        for row in rows
        if row["kodVal"] == "BTC"
        and row["isAnnulled"] == 0
    )

    group = [
        row
        for row in rows
        if row["kodVal"] == "BTC"
        and row["isAnnulled"] == 0
        and parse_dt(row["kursData"]) == btc_latest
    ]

    assert len(group) > 1

    with pytest.raises(
        LookupError,
        match="organizer or trading_pair must be specified",
    ):
        parser().parse(
            json.dumps(
                group,
                ensure_ascii=False,
            ).encode("utf-8"),
            base_asset="BTC",
            as_of=datetime(
                2026,
                10,
                6,
                tzinfo=timezone.utc,
            ),
        )


def test_organizer_filter_resolves_ambiguity_when_unique():
    rows = load_rows()

    row = next(
        item
        for item in rows
        if item["kodVal"] == "BTC"
        and item["isAnnulled"] == 0
    )

    same_date_asset = [
        item
        for item in rows
        if item["kodVal"] == row["kodVal"]
        and item["kursData"] == row["kursData"]
        and item["isAnnulled"] == 0
    ]

    organizer_rows = [
        item
        for item in same_date_asset
        if item["naimIst"] == row["naimIst"]
    ]

    # Fixture may still contain several trading pairs for same organizer.
    if len(organizer_rows) == 1:
        result = parser().parse(
            FIXTURE.read_bytes(),
            base_asset="BTC",
            as_of=datetime(
                2026,
                10,
                6,
                tzinfo=timezone.utc,
            ),
            organizer=row["naimIst"],
        )

        assert result.raw_payload["naimIst"] == row["naimIst"]
    else:
        with pytest.raises(
            LookupError,
            match="organizer or trading_pair must be specified",
        ):
            parser().parse(
                FIXTURE.read_bytes(),
                base_asset="BTC",
                as_of=datetime(
                    2026,
                    10,
                    6,
                    tzinfo=timezone.utc,
                ),
                organizer=row["naimIst"],
            )


def test_trading_pair_filter_selects_exact_row():
    row = next(
        item
        for item in load_rows()
        if item["kodVal"] == "BTC"
        and item["isAnnulled"] == 0
    )

    result = parser().parse(
        FIXTURE.read_bytes(),
        base_asset="BTC",
        as_of=datetime(
            2026,
            10,
            6,
            tzinfo=timezone.utc,
        ),
        trading_pair=row["torgPara"],
    )

    assert result.raw_payload["torgPara"] == row["torgPara"]
    assert result.rate == Decimal(
        row["krptKurs"].replace(",", ".")
    )


def test_schema_change_fails_closed():
    row = next(
        item.copy()
        for item in load_rows()
        if item["isAnnulled"] == 0
    )

    row["unexpected"] = "value"

    with pytest.raises(
        ValueError,
        match="schema mismatch",
    ):
        parser().parse(
            json.dumps(
                [row],
                ensure_ascii=False,
            ).encode("utf-8"),
            base_asset=row["kodVal"],
            as_of=datetime(
                2026,
                10,
                6,
                tzinfo=timezone.utc,
            ),
        )


def test_missing_schema_field_fails_closed():
    row = next(
        item.copy()
        for item in load_rows()
        if item["isAnnulled"] == 0
    )

    del row["kursValuta"]

    with pytest.raises(
        ValueError,
        match="schema mismatch",
    ):
        parser().parse(
            json.dumps(
                [row],
                ensure_ascii=False,
            ).encode("utf-8"),
            base_asset=row["kodVal"],
            as_of=datetime(
                2026,
                10,
                6,
                tzinfo=timezone.utc,
            ),
        )


def test_invalid_decimal_fails_closed():
    row = next(
        item.copy()
        for item in load_rows()
        if item["isAnnulled"] == 0
    )

    row["krptKurs"] = "not-a-number"

    with pytest.raises(
        ValueError,
        match="invalid decimal",
    ):
        parser().parse(
            json.dumps(
                [row],
                ensure_ascii=False,
            ).encode("utf-8"),
            base_asset=row["kodVal"],
            as_of=datetime(
                2026,
                10,
                6,
                tzinfo=timezone.utc,
            ),
            trading_pair=row["torgPara"],
        )


@pytest.mark.asyncio
async def test_frozen_source_is_tax_only_and_rub_only():
    row = next(
        item
        for item in load_rows()
        if item["isAnnulled"] == 0
    )

    source = FrozenFnsKrptrateSource(
        FIXTURE,
        source_url=META["source_url"],
        evidence_hash=META["official_zip_sha256"],
        trading_pair=row["torgPara"],
    )

    result = await source.get_market_quote(
        row["kodVal"],
        "RUB",
        datetime(
            2026,
            10,
            6,
            tzinfo=timezone.utc,
        ),
    )

    assert result.valuation_context == "tax"
    assert result.quote_currency == "RUB"

    with pytest.raises(
        LookupError,
        match="RUB quotations only",
    ):
        await source.get_market_quote(
            row["kodVal"],
            "USD",
            datetime(
                2026,
                10,
                6,
                tzinfo=timezone.utc,
            ),
        )


@pytest.mark.parametrize(
    "invalid_rate",
    ["NaN", "Infinity", "-Infinity", "0", "-1"],
)
def test_invalid_nonpositive_or_nonfinite_rate_fails_closed(
    invalid_rate,
):
    row = next(
        item.copy()
        for item in load_rows()
        if item["isAnnulled"] == 0
    )
    row["krptKurs"] = invalid_rate

    with pytest.raises(ValueError):
        parser().parse(
            json.dumps([row]).encode("utf-8"),
            base_asset=row["kodVal"],
            as_of=datetime(2026, 10, 6, tzinfo=timezone.utc),
        )


def test_naive_as_of_fails_closed():
    with pytest.raises(ValueError, match="timezone-aware"):
        parser().parse(
            FIXTURE.read_bytes(),
            base_asset="BTC",
            as_of=datetime(2026, 10, 6),
        )


@pytest.mark.parametrize(
    "invalid_currency_rate",
    ["NaN", "Infinity", "0", "-1"],
)
def test_invalid_kurs_valuta_fails_closed(
    invalid_currency_rate,
):
    row = next(
        item.copy()
        for item in load_rows()
        if item["isAnnulled"] == 0
    )
    row["kursValuta"] = invalid_currency_rate

    with pytest.raises(ValueError):
        parser().parse(
            json.dumps([row]).encode("utf-8"),
            base_asset=row["kodVal"],
            as_of=datetime(2026, 10, 6, tzinfo=timezone.utc),
        )



def test_evidence_hash_matches_actual_processed_bytes():
    import hashlib

    payload = FIXTURE.read_bytes()
    rows = load_rows()
    row = next(
        item for item in rows
        if item["kodVal"] == "BTC"
        and item["isAnnulled"] == 0
    )

    result = parser().parse(
        payload,
        base_asset="BTC",
        trading_pair=row["torgPara"],
        organizer=row["naimIst"],
        as_of=datetime(2026, 10, 6, tzinfo=timezone.utc),
    )

    assert result.evidence_hash == hashlib.sha256(
        payload
    ).hexdigest()

    evidence = result.raw_payload["_evidence"]

    assert evidence["processed_payload_sha256"] == (
        result.evidence_hash
    )
    assert evidence["processed_payload_kind"] == "json_rows"
    assert evidence["upstream_archive_sha256"] == (
        META["official_zip_sha256"]
    )
    assert evidence["upstream_archive_verified"] is False
    assert result.evidence_hash != META["official_zip_sha256"]


def test_evidence_hash_changes_with_processed_payload():
    import hashlib

    row = next(
        item.copy() for item in load_rows()
        if item["kodVal"] == "BTC"
        and item["isAnnulled"] == 0
    )

    payload_a = json.dumps(
        [row], ensure_ascii=False
    ).encode("utf-8")

    row["krptKurs"] = "999999,123456"
    payload_b = json.dumps(
        [row], ensure_ascii=False
    ).encode("utf-8")

    kwargs = {
        "base_asset": "BTC",
        "as_of": datetime(
            2026, 10, 6, tzinfo=timezone.utc
        ),
    }

    result_a = parser().parse(payload_a, **kwargs)
    result_b = parser().parse(payload_b, **kwargs)

    assert result_a.evidence_hash == hashlib.sha256(
        payload_a
    ).hexdigest()
    assert result_b.evidence_hash == hashlib.sha256(
        payload_b
    ).hexdigest()
    assert result_a.evidence_hash != result_b.evidence_hash
    assert result_a.rate != result_b.rate
