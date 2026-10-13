import pytest

from connector.rates.fns_discovery import (
    discover_fns_archive,
)

ROOT = (
    "https://data.nalog.ru/opendata/"
    "7707329152-krptrate/"
)
NAME = "data-20261008-structure-20250519.zip"


def test_discovers_official_link():
    html = f'<a href="{ROOT}{NAME}">ZIP</a>'
    assert discover_fns_archive(html) == ROOT + NAME


def test_relative_link():
    html = (
        '<a href="//data.nalog.ru/opendata/'
        '7707329152-krptrate/'
        f'{NAME}">ZIP</a>'
    )
    assert discover_fns_archive(html) == ROOT + NAME


def test_duplicate_identical_link_is_acceptable():
    html = f'<a href="{ROOT}{NAME}">A</a>' * 2
    assert discover_fns_archive(html) == ROOT + NAME


@pytest.mark.parametrize("html", [
    "",
    '<a href="https://evil.example/archive.zip">ZIP</a>',
    '<a href="http://data.nalog.ru/opendata/'
    '7707329152-krptrate/data-20261008-'
    'structure-20250519.zip">ZIP</a>',
    '<a href="https://data.nalog.ru/opendata/'
    '7707329152-krptrate/data-20261008-'
    'structure-20250519.zip?token=1">ZIP</a>',
    '<a href="https://data.nalog.ru/opendata/'
    '7707329152-krptrate/data-20261340-'
    'structure-20250519.zip">ZIP</a>',
])
def test_invalid_link_fails_closed(html):
    with pytest.raises(ValueError):
        discover_fns_archive(html)


def test_multiple_releases_fail_closed():
    other = "data-20261007-structure-20250519.zip"
    html = (
        f'<a href="{ROOT}{NAME}">A</a>'
        f'<a href="{ROOT}{other}">B</a>'
    )
    with pytest.raises(ValueError, match="exactly one"):
        discover_fns_archive(html)
