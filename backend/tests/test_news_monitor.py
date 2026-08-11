"""Unit tests for the RSS URL normaliser.

These are pure-function tests — no feedparser, no network, no database. The
DB `UNIQUE(url)` constraint only dedupes on the exact stored string, so this
normaliser is the actual dedup contract for the news table.
"""
from __future__ import annotations

import pytest

from app.data_sources.news_monitor import _normalise_url


def test_tracking_params_stripped():
    assert _normalise_url("https://example.com/a?utm_source=twitter") \
        == "https://example.com/a"


def test_multiple_tracking_params_stripped_content_preserved():
    got = _normalise_url("https://example.com/a?utm_source=x&id=42&fbclid=y")
    assert got == "https://example.com/a?id=42"


def test_scheme_and_host_lowercased():
    got = _normalise_url("HTTPS://Example.COM/Path?ID=42")
    # Path preserves case (some sites are path-case-sensitive); scheme + host don't.
    assert got == "https://example.com/Path?ID=42"


def test_trailing_slash_stripped_but_not_bare_root():
    assert _normalise_url("https://example.com/foo/") == "https://example.com/foo"
    assert _normalise_url("https://example.com/") == "https://example.com/"


def test_fragment_dropped():
    assert _normalise_url("https://example.com/a#section") == "https://example.com/a"


def test_two_variants_of_the_same_article_normalise_to_the_same_url():
    a = "https://news.example/foo?utm_source=email&utm_medium=newsletter"
    b = "https://news.example/foo?utm_source=twitter&utm_campaign=daily"
    assert _normalise_url(a) == _normalise_url(b)


def test_empty_input_is_returned_unchanged():
    assert _normalise_url("") == ""


def test_url_without_query_is_untouched():
    assert _normalise_url("https://example.com/article-1") \
        == "https://example.com/article-1"


def test_non_tracking_utm_lookalike_is_kept():
    # "utmZone" is not in the tracker list; preserve unknown params.
    got = _normalise_url("https://example.com/a?utmZone=42")
    assert "utmZone=42" in got
