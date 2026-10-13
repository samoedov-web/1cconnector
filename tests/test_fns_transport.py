import hashlib
import io
import zipfile

import pytest

from connector.rates.fns_transport import (
    fetch_fns_archive,
    validate_archive_url,
)

URL = (
    "https://data.nalog.ru/opendata/"
    "7707329152-krptrate/"
    "data-20261008-structure-20250519.zip"
)


def zip_payload(content=b'[{"asset":"BTC"}]'):
    buf = io.BytesIO()
    with zipfile.ZipFile(
        buf, "w", zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr(
            "data-20261008-structure-20250519.json",
            content,
        )
    return buf.getvalue()


def mock_transport(payload, *, fail_first=False):
    calls = []

    def fetch(url, start, end):
        calls.append((start, end))
        if fail_first and len(calls) == 1:
            raise OSError("temporary HTTP failure")
        actual_end = min(end, len(payload) - 1)
        return (
            206,
            f"bytes {start}-{actual_end}/{len(payload)}",
            payload[start:actual_end + 1],
        )

    return fetch, calls


def test_download_and_evidence_hashes():
    payload = zip_payload()
    fetch, calls = mock_transport(payload)

    result = fetch_fns_archive(
        URL, fetch_range=fetch, chunk_size=30
    )

    assert result.raw_zip == payload
    assert result.raw_json == b'[{"asset":"BTC"}]'
    assert result.sha256 == hashlib.sha256(
        payload
    ).hexdigest()
    assert result.json_sha256 == hashlib.sha256(
        result.raw_json
    ).hexdigest()
    assert len(calls) > 1


def test_transient_failure_retries():
    payload = zip_payload()
    fetch, calls = mock_transport(
        payload, fail_first=True
    )
    result = fetch_fns_archive(
        URL, fetch_range=fetch, retries=2
    )
    assert result.raw_zip == payload
    assert len(calls) >= 2


def test_changed_archive_fails_closed():
    payload = zip_payload()
    def fetch(url, start, end):
        length = len(payload) + (1 if start else 0)
        actual_end = min(end, len(payload) - 1)
        return (
            206,
            f"bytes {start}-{actual_end}/{length}",
            payload[start:actual_end + 1],
        )

    with pytest.raises(RuntimeError):
        fetch_fns_archive(
            URL, fetch_range=fetch, chunk_size=10,
            retries=1,
        )


def test_http_200_fails_closed():
    with pytest.raises(RuntimeError):
        fetch_fns_archive(
            URL,
            fetch_range=lambda *args: (200, None, b"data"),
            retries=1,
        )


def test_corrupt_zip_fails_closed():
    fetch, _ = mock_transport(b"not a ZIP file")
    with pytest.raises(zipfile.BadZipFile):
        fetch_fns_archive(
            URL, fetch_range=fetch
        )


@pytest.mark.parametrize("url", [
    "https://evil.example/a.zip",
    "http://data.nalog.ru/opendata/"
    "7707329152-krptrate/data-20261008-structure-20250519.zip",
    "https://data.nalog.ru/opendata/"
    "7707329152-krptrate/data-20261008-structure-20250519.zip?x=1",
])
def test_untrusted_url_rejected(url):
    with pytest.raises(ValueError):
        validate_archive_url(url)



def test_archive_replacement_detected():
    payload = zip_payload()
    first_requests = 0

    def fetch(url, start, end):
        nonlocal first_requests
        actual_end = min(end, len(payload) - 1)
        data = payload[start:actual_end + 1]

        if start == 0:
            first_requests += 1
            if first_requests >= 2:
                data = bytes([data[0] ^ 1]) + data[1:]

        return (
            206,
            f"bytes {start}-{actual_end}/{len(payload)}",
            data,
        )

    with pytest.raises(
        RuntimeError,
        match="stability verification failed",
    ):
        fetch_fns_archive(
            URL,
            fetch_range=fetch,
            chunk_size=30,
            retries=1,
        )


def test_decompression_limit_fails_closed(monkeypatch):
    import connector.rates.fns_transport as transport

    monkeypatch.setattr(
        transport, "MAX_UNPACKED", 10
    )

    payload = zip_payload(b"x" * 100)
    fetch, _ = mock_transport(payload)

    with pytest.raises(
        ValueError,
        match="Unsafe FNS ZIP member",
    ):
        fetch_fns_archive(
            URL, fetch_range=fetch
        )
