"""FuelWatch.ie daily average pump price fetcher.

Source: https://app.fuelwatch.ie/ (Expo web SPA backed by Supabase).
Prices are crowd-sourced from drivers reporting real forecourt prices.
Not an official government feed — used to fill the daily gap between the
authoritative but weekly EU Oil Bulletin releases.

Endpoint discovered by extracting the Supabase URL + anon JWT from the SPA
JavaScript bundle. Both are public (shipped to every browser). The bundle
filename is hash-suffixed and rotates on redeploy, so we auto-discover it
from the app's HTML each run rather than hard-coding.

Data path:
    daily_price_snapshots
        snapshot_date  DATE
        petrol_avg     REAL   -- EUR per litre
        diesel_avg     REAL   -- EUR per litre
        petrol_count   INT    -- reports contributing
        diesel_count   INT
        total_count    INT
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Iterable

import requests

from app.db import connection

logger = logging.getLogger(__name__)

APP_URL = "https://app.fuelwatch.ie/"
SUPABASE_URL_RE = re.compile(r"https://[a-z0-9]+\.supabase\.co")
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}")
BUNDLE_RE = re.compile(r'src="(/_expo/static/js/web/entry-[^"]+\.js)"')

SNAPSHOTS_PATH = "/rest/v1/daily_price_snapshots"

SOURCE_NAME = "FUELWATCH_IE"
COUNTRY = "IE"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (irish-fuel-trend/0.1; +https://github.com/)",
}

# Only backfill this many recent days into fuel_prices. Older dates are
# already covered authoritatively by the EU Oil Bulletin, no point overwriting.
DEFAULT_LOOKBACK_DAYS = 60


def _discover_supabase_creds() -> tuple[str, str]:
    """Scrape the SPA HTML + JS bundle to recover the Supabase URL and anon key."""
    r = requests.get(APP_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    bundle_match = BUNDLE_RE.search(r.text)
    if not bundle_match:
        raise RuntimeError("Could not find Expo JS bundle URL in FuelWatch app HTML.")
    bundle_url = APP_URL.rstrip("/") + bundle_match.group(1)

    b = requests.get(bundle_url, headers=HEADERS, timeout=60)
    b.raise_for_status()
    url_match = SUPABASE_URL_RE.search(b.text)
    key_match = JWT_RE.search(b.text)
    if not url_match or not key_match:
        raise RuntimeError("Could not extract Supabase URL/anon key from bundle.")
    return url_match.group(0), key_match.group(0)


def fetch_daily_snapshots(limit: int = DEFAULT_LOOKBACK_DAYS) -> list[dict]:
    """Return list of daily average rows, newest first."""
    base, key = _discover_supabase_creds()
    r = requests.get(
        base + SNAPSHOTS_PATH,
        params={
            "select": "snapshot_date,petrol_avg,diesel_avg,petrol_count,diesel_count,total_count",
            "order": "snapshot_date.desc",
            "limit": str(limit),
        },
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _iter_rows(snapshots: list[dict]) -> Iterable[tuple[str, str, float]]:
    for row in snapshots:
        d = row.get("snapshot_date")
        p = row.get("petrol_avg")
        di = row.get("diesel_avg")
        if not d:
            continue
        if p is not None:
            yield d, "petrol", float(p)
        if di is not None:
            yield d, "diesel", float(di)


def _latest_bulletin_date(conn) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) AS d FROM fuel_prices WHERE country=? AND source=?",
        (COUNTRY, "EU_WEEKLY_OIL_BULLETIN"),
    ).fetchone()
    return row["d"] if row and row["d"] else None


def upsert_prices(snapshots: list[dict]) -> int:
    """Insert daily crowd-sourced rows for dates newer than the latest EU bulletin.

    Skipping older dates preserves the authoritative EU wholesale + retail
    history that the prediction model trains on. If no bulletin exists yet,
    all rows are written.
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
            (d, COUNTRY, fuel, price, SOURCE_NAME)
            for d, fuel, price in _iter_rows(snapshots)
            if cutoff is None or d > cutoff
        ]
        conn.executemany(sql, payload)
    return len(payload)


def ingest(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """End-to-end: discover creds, fetch snapshots, upsert. Returns summary."""
    snapshots = fetch_daily_snapshots(limit=lookback_days)
    rows_written = upsert_prices(snapshots)
    if not snapshots:
        return {"rows_written": 0, "days": 0}
    latest = snapshots[0]
    oldest = snapshots[-1]
    return {
        "rows_written": rows_written,
        "days": len(snapshots),
        "date_range": (oldest["snapshot_date"], latest["snapshot_date"]),
        "latest_petrol_eur_per_l": round(float(latest["petrol_avg"]), 4)
            if latest.get("petrol_avg") is not None else None,
        "latest_diesel_eur_per_l": round(float(latest["diesel_avg"]), 4)
            if latest.get("diesel_avg") is not None else None,
        "latest_report_count": latest.get("total_count"),
    }
