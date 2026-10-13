import hashlib
import io
import zipfile
from dataclasses import replace

import pytest

from connector.rates.fns_evidence_store import FnsEvidenceStore
from connector.rates.fns_transport import FnsArchive


def archive_fixture():
    raw_json = b'[{"kodVal":"BTC","krptKurs":"123.45"}]'
    member = "data-20261008-structure-20250519.json"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, raw_json)
    raw_zip = buf.getvalue()

    return FnsArchive(
        source_url="https://data.nalog.ru/official.zip",
        raw_zip=raw_zip,
        sha256=hashlib.sha256(raw_zip).hexdigest(),
        member_name=member,
        raw_json=raw_json,
        json_sha256=hashlib.sha256(raw_json).hexdigest(),
    )


def test_store_original_zip_and_json(tmp_path):
    store = FnsEvidenceStore(tmp_path / "vault")
    archive = archive_fixture()
    saved = store.save_archive(archive)

    assert store.read_verified(saved.zip_sha256) == archive.raw_zip
    assert store.read_verified(saved.json_sha256) == archive.raw_json
    assert saved.zip_path.is_file()
    assert saved.json_path.is_file()


def test_repeat_is_idempotent(tmp_path):
    store = FnsEvidenceStore(tmp_path / "vault")
    archive = archive_fixture()

    first = store.save_archive(archive)
    second = store.save_archive(archive)

    assert first == second


def test_wrong_zip_hash_rejected(tmp_path):
    store = FnsEvidenceStore(tmp_path / "vault")
    archive = replace(
        archive_fixture(), sha256="0" * 64
    )
    with pytest.raises(ValueError, match="ZIP checksum"):
        store.save_archive(archive)


def test_wrong_json_hash_rejected(tmp_path):
    store = FnsEvidenceStore(tmp_path / "vault")
    archive = replace(
        archive_fixture(), json_sha256="0" * 64
    )
    with pytest.raises(ValueError, match="JSON checksum"):
        store.save_archive(archive)


def test_modified_json_rejected(tmp_path):
    store = FnsEvidenceStore(tmp_path / "vault")
    archive = archive_fixture()
    altered = b'[{"kodVal":"DOGE"}]'
    archive = replace(
        archive,
        raw_json=altered,
        json_sha256=hashlib.sha256(altered).hexdigest(),
    )
    with pytest.raises(ValueError, match="ZIP/JSON content"):
        store.save_archive(archive)


def test_corrupted_stored_object_rejected(tmp_path):
    store = FnsEvidenceStore(tmp_path / "vault")
    archive = archive_fixture()
    saved = store.save_archive(archive)

    saved.json_path.write_bytes(b"corrupted")

    with pytest.raises(ValueError, match="checksum mismatch"):
        store.read_verified(saved.json_sha256)

    with pytest.raises(ValueError, match="checksum mismatch"):
        store.save_archive(archive)
