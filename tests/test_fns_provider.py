import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from connector.rates.fns_provider import FnsKrptrateProvider
from connector.rates.fns_transport import FnsArchive

URL = (
    "https://data.nalog.ru/opendata/"
    "7707329152-krptrate/"
    "data-20261008-structure-20250519.zip"
)

ROW = {
    "kursData": "08.10.2026 0:00:00",
    "kodVal": "BTC",
    "naimIst": "Binance",
    "krptKurs": "7100000,25",
    "torgPara": "BTCUSDT",
    "kursValuta": "80,00",
    "isAnnulled": 0,
}


def make_archive(row=None):
    raw = json.dumps(
        [row or ROW], ensure_ascii=False
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "data-20261008-structure-20250519.json", raw
        )
    payload = buf.getvalue()
    return FnsArchive(
        source_url=URL,
        raw_zip=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        member_name="data-20261008-structure-20250519.json",
        raw_json=raw,
        json_sha256=hashlib.sha256(raw).hexdigest(),
    )


def provider(archive=None, age=timedelta(days=1)):
    return FnsKrptrateProvider(
        max_quote_age=age,
        organizer="Binance",
        trading_pair="BTCUSDT",
        passport_loader=lambda: f'<a href="{URL}">ZIP</a>',
        archive_loader=lambda _: archive or make_archive(),
    )


@pytest.mark.asyncio
async def test_provider_complete_chain():
    result = await provider().get_market_quote(
        "BTC", "RUB",
        datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )
    assert str(result.rate) == "7100000.25"
    assert result.valuation_context == "tax"
    assert result.raw_payload["_official_archive"]["zip_sha256"]
    assert result.evidence_hash == (
        result.raw_payload["_evidence"]["processed_payload_sha256"]
    )


@pytest.mark.asyncio
async def test_provider_rejects_unsupported_usdt():
    with pytest.raises(LookupError):
        await provider().get_market_quote(
            "USDT", "RUB",
            datetime(2026, 10, 8, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_provider_rejects_stale_quote():
    with pytest.raises(LookupError, match="age limit"):
        await provider(age=timedelta(hours=1)).get_market_quote(
            "BTC", "RUB",
            datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_provider_rejects_corrupt_evidence():
    from dataclasses import replace
    archive = replace(
        make_archive(), sha256="0" * 64
    )
    with pytest.raises(ValueError, match="ZIP evidence"):
        await provider(archive).get_market_quote(
            "BTC", "RUB",
            datetime(2026, 10, 8, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_provider_rejects_foreign_archive_source():
    from dataclasses import replace
    archive = replace(
        make_archive(), source_url="https://evil.example/data.zip"
    )
    with pytest.raises(ValueError, match="source mismatch"):
        await provider(archive).get_market_quote(
            "BTC", "RUB",
            datetime(2026, 10, 8, tzinfo=timezone.utc),
        )



@pytest.mark.asyncio
async def test_provider_persists_original_evidence(tmp_path):
    from connector.rates.fns_evidence_store import FnsEvidenceStore

    archive = make_archive()
    store = FnsEvidenceStore(tmp_path / "vault")

    p = FnsKrptrateProvider(
        max_quote_age=timedelta(days=1),
        organizer="Binance",
        trading_pair="BTCUSDT",
        passport_loader=lambda: f'<a href="{URL}">ZIP</a>',
        archive_loader=lambda _: archive,
        evidence_store=store,
        require_persistence=True,
    )

    result = await p.get_market_quote(
        "BTC", "RUB",
        datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )

    metadata = result.raw_payload["_official_archive"]
    assert metadata["persistence_status"] == "verified"
    assert metadata["storage_scheme"] == "sha256"
    assert store.read_verified(archive.sha256) == archive.raw_zip
    assert store.read_verified(
        archive.json_sha256
    ) == archive.raw_json


@pytest.mark.asyncio
async def test_provider_persistent_repeat_is_idempotent(tmp_path):
    from connector.rates.fns_evidence_store import FnsEvidenceStore

    archive = make_archive()
    store = FnsEvidenceStore(tmp_path / "vault")

    p = FnsKrptrateProvider(
        max_quote_age=timedelta(days=1),
        organizer="Binance",
        trading_pair="BTCUSDT",
        passport_loader=lambda: f'<a href="{URL}">ZIP</a>',
        archive_loader=lambda _: archive,
        evidence_store=store,
        require_persistence=True,
    )

    as_of = datetime(
        2026, 10, 8, 12, tzinfo=timezone.utc
    )

    first = await p.get_market_quote("BTC", "RUB", as_of)
    second = await p.get_market_quote("BTC", "RUB", as_of)

    assert first.evidence_hash == second.evidence_hash
    assert first.rate == second.rate

    objects = [
        x for x in (tmp_path / "vault").rglob("*")
        if x.is_file()
    ]
    assert len(objects) == 2


@pytest.mark.asyncio
async def test_provider_storage_failure_prevents_quote():
    class FailedStore:
        def save_archive(self, archive):
            raise OSError("Evidence storage unavailable")

    p = FnsKrptrateProvider(
        max_quote_age=timedelta(days=1),
        organizer="Binance",
        trading_pair="BTCUSDT",
        passport_loader=lambda: f'<a href="{URL}">ZIP</a>',
        archive_loader=lambda _: make_archive(),
        evidence_store=FailedStore(),
        require_persistence=True,
    )

    with pytest.raises(
        OSError, match="Evidence storage unavailable"
    ):
        await p.get_market_quote(
            "BTC", "RUB",
            datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
        )


def test_provider_requires_store_when_configured():
    with pytest.raises(
        ValueError, match="Persistent evidence store"
    ):
        FnsKrptrateProvider(
            max_quote_age=timedelta(days=1),
            require_persistence=True,
        )
