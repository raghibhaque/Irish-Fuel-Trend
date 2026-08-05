"""FuelWatch.ie daily average pump price fetcher.

Source: https://app.fuelwatch.ie/ (Expo web SPA backed by Supabase).
Prices are crowd-sourced from drivers reporting real forecourt prices.
Not an official government feed — used to fill the daily gap between the
authoritative but weekly EU Oil Bulletin releases.

Credential discovery and HTTP live in `fuelwatch_client` — see that module for
why the Supabase URL and anon key are scraped rather than pinned.

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
from typing import Iterable

from app.data_sources import fuelwatch_client
from app.db import connection

logger = logging.getLogger(__name__)

SNAPSHOTS_PATH = "/rest/v1/daily_price_snapshots"

SOURCE_NAME = "FUELWATCH_IE"
COUNTRY = "IE"

# Only backfill this many recent days into fuel_prices. Older dates are
# already covered authoritatively by the EU Oil Bulletin, no point overwriting.
DEFAULT_LOOKBACK_DAYS = 60


def fetch_daily_snapshots(limit: int = DEFAULT_LOOKBACK_DAYS) -> list[dict]:
    """Return list of daily average rows, newest first."""
    return fuelwatch_client.rest_get(
        SNAPSHOTS_PATH,
        params={
            "select": "snapshot_date,petrol_avg,diesel_avg,petrol_count,diesel_count,total_count",
            "order": "snapshot_date.desc",
            "limit": str(limit),
        },
    )


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
