"""RSS news monitor.

Polls a configurable set of RSS feeds (news_keywords.json → feeds), filters
each entry by case-insensitive substring match against a keyword list, and
upserts matches into the `news_events` table.

Idempotent: primary key on URL means re-runs are safe.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from time import mktime
from typing import Any

import feedparser

from app.db import connection

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "news_keywords.json"


def _load_config() -> tuple[list[str], list[dict[str, str]]]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg.get("keywords", []), cfg.get("feeds", [])


def _match_keywords(text: str, keywords: list[str]) -> list[str]:
    lower = text.lower()
    return [kw for kw in keywords if kw.lower() in lower]


def _entry_published(entry: Any) -> datetime:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None) or entry.get(attr) if isinstance(entry, dict) else getattr(entry, attr, None)
        if t:
            return datetime.fromtimestamp(mktime(t), tz=timezone.utc)
    return datetime.now(timezone.utc)


def _upsert(rows: list[tuple[datetime, str, str, str, str, str]]) -> int:
    sql = """
        INSERT INTO news_events (published_at, source, title, url, summary, matched_keywords)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title            = excluded.title,
            summary          = excluded.summary,
            matched_keywords = excluded.matched_keywords,
            inserted_at      = CURRENT_TIMESTAMP;
    """
    # SQLite auto-converter expects "YYYY-MM-DD HH:MM:SS" (no T, no tz suffix).
    def _fmt(dt):
        return dt.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    payload = [(_fmt(pub), src, title, url, summary, matched) for pub, src, title, url, summary, matched in rows]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def poll_once() -> dict:
    keywords, feeds = _load_config()
    if not keywords or not feeds:
        return {"feeds_polled": 0, "entries_scanned": 0, "matches_written": 0}

    rows: list[tuple[datetime, str, str, str, str, str]] = []
    entries_scanned = 0
    feed_errors: list[str] = []

    for feed in feeds:
        name = feed.get("name") or feed.get("url", "?")
        url  = feed.get("url")
        if not url:
            continue
        try:
            parsed = feedparser.parse(url)
        except Exception as e:
            feed_errors.append(f"{name}: {e}")
            continue
        if parsed.bozo and not parsed.entries:
            feed_errors.append(f"{name}: parse failed ({parsed.bozo_exception!r})")
            continue

        for entry in parsed.entries:
            entries_scanned += 1
            title = getattr(entry, "title", "") or ""
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
            link = getattr(entry, "link", "") or ""
            if not title or not link:
                continue
            hits = _match_keywords(f"{title}  {summary}", keywords)
            if not hits:
                continue
            pub = _entry_published(entry)
            rows.append((pub, name, title, link, summary[:800], ", ".join(hits)))

    written = _upsert(rows) if rows else 0
    return {
        "feeds_polled": len(feeds),
        "entries_scanned": entries_scanned,
        "matches_written": written,
        "feed_errors": feed_errors,
    }


def ingest(force_download: bool = False) -> dict:
    _ = force_download
    logger.info("Polling RSS feeds for oil-relevant news…")
    result = poll_once()
    logger.info("News poll: %s", result)
    return result
