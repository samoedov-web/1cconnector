"""Strict discovery of FNS krptrate archive links."""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import re

DATASET = "7707329152-krptrate"
PASSPORT_URL = (
    "https://www.nalog.gov.ru/opendata/"
    f"{DATASET}/"
)
ARCHIVE_PATTERN = re.compile(
    rf"^/opendata/{DATASET}/"
    r"data-(\d{8})-structure-(\d{8})\.zip$"
)


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


def discover_fns_archive(html: str) -> str:
    """Return one unambiguous published official archive URL.

    No guessed URLs, external redirects, or implicit latest-date
    selection. Transport and ZIP verification are separate stages.
    """
    parser = _LinkParser()
    parser.feed(html)

    candidates = set()

    for href in parser.links:
        url = urljoin(PASSPORT_URL, href)
        parsed = urlparse(url)

        if parsed.scheme != "https":
            continue
        if parsed.hostname not in {
            "data.nalog.ru",
            "file.nalog.ru",
        }:
            continue
        if parsed.username or parsed.password:
            continue
        if parsed.port not in (None, 443):
            continue
        if parsed.query or parsed.fragment:
            continue

        match = ARCHIVE_PATTERN.fullmatch(parsed.path)
        if match is None:
            continue

        from datetime import datetime
        try:
            for value in match.groups():
                datetime.strptime(value, "%Y%m%d")
        except ValueError:
            continue

        candidates.add(url)

    if len(candidates) != 1:
        raise ValueError(
            "FNS archive discovery requires exactly one "
            f"official URL; found {len(candidates)}"
        )

    return candidates.pop()
