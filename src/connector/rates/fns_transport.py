"""Read-only, bounded HTTP Range transport for FNS archives."""
from __future__ import annotations

import hashlib
import io
import re
import time
import zipfile
from dataclasses import dataclass
from typing import Callable
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from connector.rates.fns_discovery import (
    DATASET,
    ARCHIVE_PATTERN,
)

MAX_BYTES = 20 * 1024 * 1024
MAX_UNPACKED = 100 * 1024 * 1024
CHUNK_SIZE = 256 * 1024
MAX_MEMBERS = 100


@dataclass(frozen=True)
class FnsArchive:
    source_url: str
    raw_zip: bytes
    sha256: str
    member_name: str
    raw_json: bytes
    json_sha256: str


def validate_archive_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {
            "data.nalog.ru", "file.nalog.ru"
        }
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or ARCHIVE_PATTERN.fullmatch(parsed.path) is None
    ):
        raise ValueError("Untrusted FNS archive URL")


def _network_range(url: str, start: int, end: int):
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Range": f"bytes={start}-{end}",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=40) as response:
        final_url = response.geturl()
        if final_url != url:
            raise ValueError("Unexpected FNS HTTP redirect")
        status = response.status
        content_range = response.headers.get("Content-Range")
        data = response.read(CHUNK_SIZE + 1)
    return status, content_range, data


def fetch_fns_archive(
    url: str,
    *,
    fetch_range: Callable | None = None,
    chunk_size: int = CHUNK_SIZE,
    retries: int = 3,
    retry_delay: float = 0.0,
) -> FnsArchive:
    """Download exact byte ranges; reject gaps and changed objects."""
    validate_archive_url(url)

    if not 1 <= chunk_size <= CHUNK_SIZE:
        raise ValueError("Invalid chunk size")
    if not 1 <= retries <= 5:
        raise ValueError("Invalid retry count")

    fetch = fetch_range or _network_range
    pieces = []
    position = 0
    total = None

    while total is None or position < total:
        requested_end = position + chunk_size - 1
        last_error = None

        for attempt in range(retries):
            try:
                status, header, data = fetch(
                    url, position, requested_end
                )
                if status != 206:
                    raise ValueError("FNS requires HTTP 206")

                match = re.fullmatch(
                    r"bytes (\d+)-(\d+)/(\d+)",
                    header or "",
                )
                if match is None:
                    raise ValueError("Invalid Content-Range")

                start, end, reported_total = map(
                    int, match.groups()
                )
                if not 0 < reported_total <= MAX_BYTES:
                    raise ValueError("Unsafe archive size")
                if total is not None and reported_total != total:
                    raise ValueError("Archive changed during download")
                if (
                    start != position
                    or end != min(
                        requested_end, reported_total - 1
                    )
                    or len(data) != end - start + 1
                ):
                    raise ValueError("Invalid range response")

                total = reported_total
                pieces.append(data)
                position += len(data)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 < retries and retry_delay:
                    time.sleep(retry_delay)

        if last_error is not None:
            raise RuntimeError(
                "FNS archive range download failed"
            ) from last_error

    payload = b"".join(pieces)
    if len(payload) != total:
        raise ValueError("Incomplete FNS archive")

    # Re-read the first range after all other ranges.
    # This detects a common case of source replacement during download.
    first_chunk = pieces[0]
    first_end = len(first_chunk) - 1
    last_error = None

    for attempt in range(retries):
        try:
            status, header, check_data = fetch(
                url, 0, first_end
            )
            expected_header = (
                f"bytes 0-{first_end}/{total}"
            )
            if (
                status != 206
                or header != expected_header
                or check_data != first_chunk
            ):
                raise ValueError(
                    "FNS archive changed during download"
                )
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries and retry_delay:
                time.sleep(retry_delay)

    if last_error is not None:
        raise RuntimeError(
            "FNS archive stability verification failed"
        ) from last_error

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if len(members) != 1 or len(members) > MAX_MEMBERS:
            raise ValueError("Unexpected FNS ZIP members")

        member = members[0]
        if (
            member.is_dir()
            or "/" in member.filename
            or "\\" in member.filename
            or not member.filename.endswith(".json")
            or member.file_size > MAX_UNPACKED
            or member.flag_bits & 1
        ):
            raise ValueError("Unsafe FNS ZIP member")

        with archive.open(member) as stream:
            raw_json = stream.read(MAX_UNPACKED + 1)

        if len(raw_json) > MAX_UNPACKED:
            raise ValueError("FNS JSON exceeds decompression limit")
        if len(raw_json) != member.file_size:
            raise ValueError("Incomplete FNS JSON")

        if archive.testzip() is not None:
            raise ValueError("Invalid FNS ZIP CRC")

    return FnsArchive(
        source_url=url,
        raw_zip=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        member_name=member.filename,
        raw_json=raw_json,
        json_sha256=hashlib.sha256(raw_json).hexdigest(),
    )
