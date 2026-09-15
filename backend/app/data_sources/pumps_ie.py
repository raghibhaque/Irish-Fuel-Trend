"""Pumps.ie / Cheapest Petrol Ireland daily crowd-sourced pump prices.

Source: https://pumps.ie/ (public web app; Cheapest Petrol Ireland re-uses
the same feed). Data model is similar to FuelWatch.ie — drivers report
forecourt prices; the site publishes a rolling daily national average.

This module widens crowd coverage: FuelWatch already contributes daily
averages, but its user-base skews to Leinster commuter routes. Pumps.ie
draws more from Munster and Connacht, so the two sources together
smooth the county-mix bias in the pump anchor.

Because the site has no documented public API, real fetch is a
best-effort scrape of the homepage's inline JSON blob. When the layout
changes we fall back to a deterministic mock series so ingestion never
hard-fails offline. Set IRISH_FUEL_FORCE_MOCK_PUMPS=1 to force the mock.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import date, timedelta

import requests

from app.db import connection

logger = logging.getLogger(__name__)

REAL_SOURCE = "PUMPS_IE"
MOCK_SOURCE = "MOCK_PUMPS_IE_v1"
COUNTRY = "IE"

PAGE_URL = "https://pumps.ie/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (irish-fuel-trend/0.1; +https://github.com/)",
    "Accept": "text/html,application/json;q=0.9",
}

# Modern SPAs typically inline a __NEXT_DATA__ or window.__INITIAL_STATE__
# JSON blob. We match either shape opportunistically.
NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>',
    re.DOTALL,
)
INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\});",
    re.DOTALL,
)

# Fallback regex — hunt for "petrol": 179.9 or "petrol_avg": "1.799" style
# pairs anywhere on the page. Uses word boundaries so it doesn't overreach.
FIELD_RE = re.compile(
    r'"(petrol|diesel)(?:_avg|_average|Average)?"\s*:\s*"?(\d{1,3}(?:\.\d{1,3})?)"?',
    re.IGNORECASE,
)

DEFAULT_LOOKBACK_DAYS = 30


def _walk(node, key: str):
    """Yield every value whose key matches (case-insensitive) inside nested dict/list."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and k.lower() == key.lower():
                yield v
            yield from _walk(v, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key)


def _extract_averages_from_json(blob: dict) -> tuple[float | None, float | None]:
    petrol = next(iter(_walk(blob, "petrol_avg")), None)
    diesel = next(iter(_walk(blob, "diesel_avg")), None)
    if petrol is None:
        petrol = next(iter(_walk(blob, "avgPetrol")), None)
    if diesel is None:
        diesel = next(iter(_walk(blob, "avgDiesel")), None)

    def _coerce(x):
        if x is None:
            return None
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v / 100.0 if v > 20 else v

    return _coerce(petrol), _coerce(diesel)


def fetch_real_latest() -> tuple[date, float | None, float | None] | None:
    """Return today's petrol/diesel national averages, or None on failure."""
    try:
        r = requests.get(PAGE_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        logger.warning("Pumps.ie fetch failed: %s", e)
        return None
    html = r.text
    petrol: float | None = None
    diesel: float | None = None
    for regex in (NEXT_DATA_RE, INITIAL_STATE_RE):
        m = regex.search(html)
        if not m:
            continue
        try:
            blob = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        petrol, diesel = _extract_averages_from_json(blob)
        if petrol or diesel:
            break
    if petrol is None and diesel is None:
        # Last-ditch regex scan of raw HTML.
        found: dict[str, float] = {}
        for m in FIELD_RE.finditer(html):
            fuel = m.group(1).lower()
            if fuel in found:
                continue
            raw = float(m.group(2))
            found[fuel] = raw / 100.0 if raw > 20 else raw
        petrol = found.get("petrol")
        diesel = found.get("diesel")
    if petrol is None and diesel is None:
        return None
    return date.today(), petrol, diesel


def generate_mock_series(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> list[tuple[date, str, float]]:
    today = date.today()
    out: list[tuple[date, str, float]] = []
    for i in range(lookback_days, -1, -1):
        d = today - timedelta(days=i)
        seasonal = 0.02 * math.sin(i / 5.0 * math.pi)
        petrol = round(1.75 + seasonal, 4)
        diesel = round(1.78 + seasonal * 0.9, 4)
        out.append((d, "petrol", petrol))
        out.append((d, "diesel", diesel))
    return out


def _latest_bulletin_date(conn) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) AS d FROM fuel_prices WHERE country=? AND source=?",
        (COUNTRY, "EU_WEEKLY_OIL_BULLETIN"),
    ).fetchone()
    return row["d"] if row and row["d"] else None


def upsert_prices(rows: list[tuple[date, str, float]], source: str) -> int:
    """Insert daily rows for dates newer than the latest EU bulletin.

    Mirrors fuelwatch_ie.upsert_prices — the bulletin is authoritative for
    historical dates; crowd sources only fill the trailing gap.
    """
    sql = """
        INSERT INTO fuel_prices (
            date, country, fuel_type, price_eur_per_litre,
            price_wo_tax_eur_per_litre, source
        )
        VALUES (?, ?, ?, ?, NULL, ?)
        ON CONFLICT(date, country, fuel_type)
        DO UPDATE SET price_eur_per_litre = excluded.price_eur_per_litre,
                      source              = excluded.source,
                      inserted_at         = CURRENT_TIMESTAMP;
    """
    with connection() as conn:
        cutoff = _latest_bulletin_date(conn)
        payload = [
            (d.isoformat(), COUNTRY, fuel, price, source)
            for d, fuel, price in rows
            if (cutoff is None or d.isoformat() > cutoff)
        ]
        conn.executemany(sql, payload)
    return len(payload)


def _delete_source(source: str) -> int:
    with connection() as conn:
        cur = conn.execute("DELETE FROM fuel_prices WHERE source=?", (source,))
        return cur.rowcount


def ingest(force_download: bool = False) -> dict:
    _ = force_download
    force_mock = os.environ.get("IRISH_FUEL_FORCE_MOCK_PUMPS") == "1"
    if not force_mock:
        real = fetch_real_latest()
        if real and (real[1] is not None or real[2] is not None):
            d, petrol, diesel = real
            rows: list[tuple[date, str, float]] = []
            if petrol is not None:
                rows.append((d, "petrol", round(petrol, 4)))
            if diesel is not None:
                rows.append((d, "diesel", round(diesel, 4)))
            written = upsert_prices(rows, REAL_SOURCE)
            return {
                "mock": False,
                "source": REAL_SOURCE,
                "rows_written": written,
                "date": d.isoformat(),
                "petrol": petrol,
                "diesel": diesel,
            }
        logger.warning("Pumps.ie real fetch produced nothing — using mock.")
    series = generate_mock_series()
    _delete_source(MOCK_SOURCE)
    written = upsert_prices(series, MOCK_SOURCE)
    return {
        "mock": True,
        "source": MOCK_SOURCE,
        "rows_written": written,
        "date_range": (series[0][0].isoformat(), series[-1][0].isoformat()),
    }
