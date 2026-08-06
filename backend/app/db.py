"""SQLite connection + schema.

Single-file DB at ../data/fuel_trend.db (relative to backend/). No ORM — plain
sqlite3 keeps it obvious for learning. Schema is idempotent; safe to call
`init_db()` on every startup.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "fuel_trend.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS fuel_prices (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    date                       DATE    NOT NULL,
    country                    TEXT    NOT NULL DEFAULT 'IE',
    fuel_type                  TEXT    NOT NULL,       -- 'petrol' | 'diesel'
    price_eur_per_litre        REAL    NOT NULL,       -- pump price (with taxes)
    price_wo_tax_eur_per_litre REAL,                   -- wholesale (net of duties+taxes)
    source                     TEXT    NOT NULL,
    inserted_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, country, fuel_type)
);
CREATE INDEX IF NOT EXISTS ix_fuel_prices_date ON fuel_prices(date);

CREATE TABLE IF NOT EXISTS fx_rates (
    date         DATE PRIMARY KEY,
    eur_usd      REAL NOT NULL,
    source       TEXT NOT NULL,
    inserted_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS brent_crude (
    date                    DATE PRIMARY KEY,
    price_usd_per_barrel    REAL NOT NULL,
    source                  TEXT NOT NULL,
    inserted_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS refined_products (
    date                DATE NOT NULL,
    symbol              TEXT NOT NULL,          -- 'RBOB' | 'ULSD'
    price_usd_per_gal   REAL NOT NULL,
    source              TEXT NOT NULL,
    inserted_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, symbol)
);
CREATE INDEX IF NOT EXISTS ix_refined_products_symbol_date ON refined_products(symbol, date);

-- Per-county median pump prices, snapshotted daily from FuelWatch's
-- county_price_rankings RPC. The upstream returns a trailing-window median
-- with no date of its own, so snapshot_date records when *we* pulled it —
-- this table is the only county history that exists anywhere.
CREATE TABLE IF NOT EXISTS county_prices (
    snapshot_date        DATE    NOT NULL,
    county               TEXT    NOT NULL,
    fuel_type            TEXT    NOT NULL,       -- 'petrol' | 'diesel'
    median_eur_per_litre REAL    NOT NULL,
    station_count        INTEGER NOT NULL,       -- stations behind the median
    window_days          INTEGER NOT NULL,       -- trailing window it covers
    source               TEXT    NOT NULL,
    inserted_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, county, fuel_type, window_days)
);
CREATE INDEX IF NOT EXISTS ix_county_prices_lookup
    ON county_prices(county, fuel_type, snapshot_date);

CREATE TABLE IF NOT EXISTS county_stations (
    snapshot_date       DATE    NOT NULL,
    station_id          TEXT    NOT NULL,
    fuel_type           TEXT    NOT NULL,
    name                TEXT    NOT NULL,
    brand               TEXT,
    county              TEXT,
    price_eur_per_litre REAL    NOT NULL,
    -- TEXT, not TIMESTAMP: upstream sends ISO-8601 with a 'T' separator and a
    -- UTC offset, which sqlite3's declared-type TIMESTAMP converter cannot
    -- parse. Stored verbatim and formatted client-side.
    reported_at         TEXT,
    source              TEXT    NOT NULL,
    inserted_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, station_id, fuel_type)
);
CREATE INDEX IF NOT EXISTS ix_county_stations_county
    ON county_stations(county, fuel_type, snapshot_date);

-- Brand-published station catalogues (Applegreen, Maxol, etc). These sources
-- give us name/address/lat-long/amenities but *no prices*. Used to cross-
-- reference the FuelWatch crowd feed and to widen coverage for small
-- counties (Clare especially) where FuelWatch alone reports only a handful
-- of stations. snapshot_date lets us track brand-list churn over time.
CREATE TABLE IF NOT EXISTS brand_stations (
    snapshot_date DATE    NOT NULL,
    source_brand  TEXT    NOT NULL,       -- 'APPLEGREEN' | 'MAXOL' | 'CIRCLE_K' ...
    external_id   TEXT    NOT NULL,       -- brand's own station id
    name          TEXT    NOT NULL,
    county        TEXT,
    town          TEXT,
    address       TEXT,
    latitude      REAL,
    longitude     REAL,
    amenities     TEXT,                   -- JSON blob, brand-specific
    url           TEXT,
    inserted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, source_brand, external_id)
);
CREATE INDEX IF NOT EXISTS ix_brand_stations_county
    ON brand_stations(county, source_brand, snapshot_date);

CREATE TABLE IF NOT EXISTS news_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    published_at TIMESTAMP NOT NULL,
    source       TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL UNIQUE,
    summary      TEXT,
    matched_keywords TEXT,
    inserted_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_news_events_published ON news_events(published_at DESC);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(row["name"] == col for row in conn.execute(f"PRAGMA table_info({table})"))


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent, forward-only migrations for pre-existing DBs."""
    if not _column_exists(conn, "fuel_prices", "price_wo_tax_eur_per_litre"):
        conn.execute("ALTER TABLE fuel_prices ADD COLUMN price_wo_tax_eur_per_litre REAL;")


def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


if __name__ == "__main__":
    init_db()
    print(f"Initialized DB at {DB_PATH}")
