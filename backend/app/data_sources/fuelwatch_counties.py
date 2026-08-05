"""County-level FuelWatch.ie fetcher.

Two RPCs on the same Supabase backend `fuelwatch_ie` already uses:

    county_price_rankings(window_days)
        -> fuel_type, county, median_price, station_count
    cheapest_reported_stations(window_days, per_fuel)
        -> fuel_type, station_id, name, brand, county, price, reported_at

Both return a *trailing-window* aggregate with no date of their own, and there
is no archive: the raw `price_reports` table is RLS-locked and returns nothing
to the anon key. So no county history is retrievable from upstream. This
module snapshots the current answer once a day, which is the only way county
history comes to exist at all.

Prices arrive as integers in tenths of a cent — 1794 means EUR 1.794/L.

Two windows are pulled. 30 days is the freshest useful median but only covers
19 counties; 180 days covers 24 but is heavily smoothed. Both are stored with
their `window_days`, and the read path prefers 30 and falls back to 180 per
county/fuel (Roscommon, for instance, has diesel but no petrol at 30 days).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.data_sources import fuelwatch_client
from app.db import connection

logger = logging.getLogger(__name__)

SOURCE_NAME = "FUELWATCH_IE_COUNTY"

# Upstream integer prices are tenths of a cent.
PRICE_SCALE = 1000.0

PRIMARY_WINDOW_DAYS = 30
WIDE_WINDOW_DAYS = 180
WINDOWS = (PRIMARY_WINDOW_DAYS, WIDE_WINDOW_DAYS)

# per_fuel caps rows *per fuel*, not per county, so it has to be large enough
# to reach down into the smaller counties. 200 yields ~400 rows / 23 counties.
STATIONS_PER_FUEL = 200
STATIONS_WINDOW_DAYS = 30

# Canonical list, used to report which counties the crowd-sourced feed never
# covers. Leitrim and Monaghan have stations but never enough reports to clear
# the ranking RPC's internal threshold.
IE_COUNTIES = (
    "Carlow", "Cavan", "Clare", "Cork", "Donegal", "Dublin", "Galway", "Kerry",
    "Kildare", "Kilkenny", "Laois", "Leitrim", "Limerick", "Longford", "Louth",
    "Mayo", "Meath", "Monaghan", "Offaly", "Roscommon", "Sligo", "Tipperary",
    "Waterford", "Westmeath", "Wexford", "Wicklow",
)


def _today() -> str:
    """Snapshot date in UTC — the scheduler and CI cron both run on UTC."""
    return datetime.now(timezone.utc).date().isoformat()


def fetch_rankings(window_days: int) -> list[dict]:
    return fuelwatch_client.rpc("county_price_rankings", {"window_days": window_days})


def fetch_stations(window_days: int = STATIONS_WINDOW_DAYS,
                   per_fuel: int = STATIONS_PER_FUEL) -> list[dict]:
    return fuelwatch_client.rpc(
        "cheapest_reported_stations",
        {"window_days": window_days, "per_fuel": per_fuel},
    )


def upsert_rankings(rows: list[dict], window_days: int, snapshot_date: str) -> int:
    sql = """
        INSERT INTO county_prices (
            snapshot_date, county, fuel_type, median_eur_per_litre,
            station_count, window_days, source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date, county, fuel_type, window_days)
        DO UPDATE SET median_eur_per_litre = excluded.median_eur_per_litre,
                      station_count        = excluded.station_count,
                      source               = excluded.source,
                      inserted_at          = CURRENT_TIMESTAMP;
    """
    payload = [
        (
            snapshot_date,
            r["county"],
            r["fuel_type"],
            float(r["median_price"]) / PRICE_SCALE,
            int(r.get("station_count") or 0),
            window_days,
            SOURCE_NAME,
        )
        for r in rows
        if r.get("county") and r.get("fuel_type") and r.get("median_price") is not None
    ]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def upsert_stations(rows: list[dict], snapshot_date: str) -> int:
    sql = """
        INSERT INTO county_stations (
            snapshot_date, station_id, fuel_type, name, brand, county,
            price_eur_per_litre, reported_at, source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date, station_id, fuel_type)
        DO UPDATE SET name                = excluded.name,
                      brand               = excluded.brand,
                      county              = excluded.county,
                      price_eur_per_litre = excluded.price_eur_per_litre,
                      reported_at         = excluded.reported_at,
                      source              = excluded.source,
                      inserted_at         = CURRENT_TIMESTAMP;
    """
    payload = [
        (
            snapshot_date,
            r["station_id"],
            r["fuel_type"],
            r.get("name") or "Unnamed station",
            r.get("brand"),
            r.get("county"),
            float(r["price"]) / PRICE_SCALE,
            r.get("reported_at"),
            SOURCE_NAME,
        )
        for r in rows
        if r.get("station_id") and r.get("fuel_type") and r.get("price") is not None
    ]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def ingest() -> dict:
    """Snapshot today's county medians and station prices. Returns a summary."""
    snapshot_date = _today()
    written_by_window: dict[int, int] = {}
    counties_seen: set[str] = set()

    for window in WINDOWS:
        rows = fetch_rankings(window)
        written_by_window[window] = upsert_rankings(rows, window, snapshot_date)
        counties_seen.update(r["county"] for r in rows if r.get("county"))

    stations = fetch_stations()
    stations_written = upsert_stations(stations, snapshot_date)

    missing = [c for c in IE_COUNTIES if c not in counties_seen]
    return {
        "snapshot_date": snapshot_date,
        "county_rows_by_window": written_by_window,
        "counties_with_median": len(counties_seen),
        "counties_without_median": missing,
        "station_rows": stations_written,
    }
