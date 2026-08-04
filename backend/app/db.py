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
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    date                DATE    NOT NULL,
    country             TEXT    NOT NULL DEFAULT 'IE',
    fuel_type           TEXT    NOT NULL,       -- 'petrol' | 'diesel'
    price_eur_per_litre REAL    NOT NULL,
    source              TEXT    NOT NULL,
    inserted_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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


def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA)


if __name__ == "__main__":
    init_db()
    print(f"Initialized DB at {DB_PATH}")
