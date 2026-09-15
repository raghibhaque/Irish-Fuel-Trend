"""AA Ireland monthly fuel price survey.

Source: https://www.theaa.ie/aa/motoring-advice/petrol-prices.aspx (public,
no auth). AA Ireland publishes an independent monthly average pump price
for petrol and diesel, based on their own forecourt survey. It is
methodologically distinct from the EU Weekly Oil Bulletin (which uses
Revenue-reported wholesale data + tax stack) and from FuelWatch.ie
(driver-crowd reports).

Because AA data is a monthly survey rather than a weekly print, it is
stored as a supplementary source in `fuel_prices` with `source='AA_IRELAND'`
using the last-of-month as the date key. Model training already averages
across sources when multiple exist for a date, so a monthly AA reading
smooths the weekly bulletin drift.

Real fetch is a best-effort HTML scrape of the AA page; the page rewrites
its markup periodically. When the scrape fails we fall back to a
deterministic mock series so the pipeline is always runnable offline.
Set IRISH_FUEL_FORCE_MOCK_AA=1 to force the mock.
"""
from __future__ import annotations

import calendar
import logging
import math
import os
import re
from datetime import date

import requests

from app.db import connection

logger = logging.getLogger(__name__)

REAL_SOURCE = "AA_IRELAND"
MOCK_SOURCE = "MOCK_AA_IRELAND_v1"
COUNTRY = "IE"

PAGE_URL = "https://www.theaa.ie/aa/motoring-advice/petrol-prices.aspx"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (irish-fuel-trend/0.1; +https://github.com/)",
    "Accept": "text/html,application/xhtml+xml",
}

# AA publishes numbers as "petrol: 175.9c" style. The regex tolerates a
# leading label and trailing "c" / "cent" / "cpl". Values are in cent/litre.
PRICE_RE = re.compile(
    r"(petrol|diesel)[^0-9]{0,40}?(\d{2,3}(?:\.\d{1,2})?)\s*(?:c\b|cent|cpl)",
    re.IGNORECASE,
)
MONTH_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{4})",
    re.IGNORECASE,
)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def fetch_real_latest() -> list[tuple[date, str, float]] | None:
    """Scrape one row per fuel from AA page. Returns None on parse failure."""
    try:
        r = requests.get(PAGE_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        logger.warning("AA Ireland fetch failed: %s", e)
        return None
    html = r.text
    m = MONTH_RE.search(html)
    if not m:
        logger.warning("AA Ireland: could not locate month label on page")
        return None
    month_name, year_str = m.group(1).title(), m.group(2)
    month = list(calendar.month_name).index(month_name)
    d = _month_end(int(year_str), month)
    seen: dict[str, float] = {}
    for match in PRICE_RE.finditer(html):
        fuel = match.group(1).lower()
        if fuel in seen:
            continue
        raw = float(match.group(2))
        # Values above 20 are cent/litre; convert to EUR/litre.
        eur = raw / 100.0 if raw > 20 else raw
        seen[fuel] = eur
    if "petrol" not in seen and "diesel" not in seen:
        return None
    rows: list[tuple[date, str, float]] = []
    for fuel, eur in seen.items():
        rows.append((d, fuel, round(eur, 4)))
    return rows


MOCK_SEED_BASE = 4127


def generate_mock_series() -> list[tuple[date, str, float]]:
    """Deterministic monthly petrol/diesel series anchored on rough IE levels.

    Anchored on 2022-01 through today (or 2027-12 whichever is earlier).
    Base petrol 1.68 EUR/L, base diesel 1.72 EUR/L, seasonal ripple ±0.05
    and small monthly drift so returns match the ballpark of weekly bulletin
    swings. Used offline; the numbers are not authoritative — the source
    label makes the mock provenance explicit.
    """
    today = date.today()
    end_year, end_month = today.year, today.month
    year, month = 2022, 1
    out: list[tuple[date, str, float]] = []
    i = 0
    while (year, month) <= (end_year, end_month):
        d = _month_end(year, month)
        seasonal = 0.05 * math.sin(i / 6.0 * math.pi)
        drift = 0.002 * i
        petrol = round(1.68 + seasonal + drift, 4)
        diesel = round(1.72 + seasonal * 0.9 + drift, 4)
        out.append((d, "petrol", petrol))
        out.append((d, "diesel", diesel))
        month += 1
        if month > 12:
            month = 1
            year += 1
        i += 1
    return out


def upsert_prices(rows: list[tuple[date, str, float]], source: str) -> int:
    """Insert into fuel_prices (no wholesale — AA publishes pump only)."""
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
    payload = [(d.isoformat(), COUNTRY, fuel, price, source) for d, fuel, price in rows]
    with connection() as conn:
        # Only overwrite AA rows or empty slots — don't clobber
        # EU_WEEKLY_OIL_BULLETIN rows sitting at the same date.
        existing_bulletin = {
            (r["date"], r["fuel_type"])
            for r in conn.execute(
                "SELECT date, fuel_type FROM fuel_prices "
                "WHERE country=? AND source=?",
                (COUNTRY, "EU_WEEKLY_OIL_BULLETIN"),
            ).fetchall()
        }
        filtered = [
            row for row in payload
            if (row[0], row[2]) not in existing_bulletin
        ]
        conn.executemany(sql, filtered)
    return len(payload)


def _delete_source(source: str) -> int:
    with connection() as conn:
        cur = conn.execute("DELETE FROM fuel_prices WHERE source=?", (source,))
        return cur.rowcount


def ingest(force_download: bool = False) -> dict:
    _ = force_download
    force_mock = os.environ.get("IRISH_FUEL_FORCE_MOCK_AA") == "1"
    if not force_mock:
        real_rows = fetch_real_latest()
        if real_rows:
            written = upsert_prices(real_rows, REAL_SOURCE)
            return {
                "mock": False,
                "source": REAL_SOURCE,
                "rows_written": written,
                "date": real_rows[0][0].isoformat(),
                "petrol": next((p for _, f, p in real_rows if f == "petrol"), None),
                "diesel": next((p for _, f, p in real_rows if f == "diesel"), None),
            }
        logger.warning("AA Ireland real fetch produced no rows — using mock.")
    series = generate_mock_series()
    _delete_source(MOCK_SOURCE)
    written = upsert_prices(series, MOCK_SOURCE)
    latest_d = series[-1][0]
    return {
        "mock": True,
        "source": MOCK_SOURCE,
        "rows_written": written,
        "date_range": (series[0][0].isoformat(), latest_d.isoformat()),
        "latest_petrol": next((p for d, f, p in reversed(series) if f == "petrol" and d == latest_d), None),
        "latest_diesel": next((p for d, f, p in reversed(series) if f == "diesel" and d == latest_d), None),
    }
