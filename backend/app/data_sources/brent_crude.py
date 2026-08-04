"""Brent crude oil price fetcher.

============================================================================
 !!! TODO: THIS IS A MOCK. REPLACE WITH REAL DATA SOURCE BEFORE RELYING ON
 !!! ANY PREDICTIONS. Candidate real sources (all free-tier):
 !!!   - EIA API series PET.RBRTE.D (Brent, daily, USD/bbl) — needs API key
 !!!   - FRED series DCOILBRENTEU                          — needs API key
 !!!   - yfinance library, ticker "BZ=F"                    — no key, brittle
 !!! Swap `ingest()` below to fetch from one of the above.
============================================================================

Current implementation: deterministic synthetic weekly series in USD/bbl,
generated across the same date range as fuel_prices. Rows are marked
`source = 'MOCK_BRENT_v1'` so real data can be identified & replaced later.
"""
from __future__ import annotations

import logging
import math
import random
from datetime import date, timedelta

from app.db import connection

logger = logging.getLogger(__name__)

SOURCE_NAME = "MOCK_BRENT_v1"

# Rough historical envelope for Brent (USD/bbl): min $20 (COVID), max $140 (2008/2022 spikes)
BASE_PRICE = 75.0
DRIFT = 0.0
VOLATILITY = 3.5   # USD stddev of weekly step
FLOOR = 20.0
CEIL = 140.0
SEED = 42


def _fuel_price_date_range() -> tuple[date, date] | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT MIN(date), MAX(date) FROM fuel_prices WHERE country='IE'"
        ).fetchone()
    if not row or not row[0]:
        return None
    from datetime import datetime as _dt
    return _dt.fromisoformat(row[0]).date(), _dt.fromisoformat(row[1]).date()


def _weekly_dates(start: date, end: date) -> list[date]:
    """Weekly (7-day) samples from start through end inclusive."""
    out = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def generate_mock_series(start: date, end: date) -> list[tuple[date, float]]:
    """Deterministic smoothed random walk within [FLOOR, CEIL]."""
    rng = random.Random(SEED)
    dates = _weekly_dates(start, end)
    price = BASE_PRICE
    out: list[tuple[date, float]] = []
    for i, d in enumerate(dates):
        # Long slow sinusoid + gaussian step, so the mock has both trend and noise.
        trend = 15.0 * math.sin(i / 26.0)  # ~2yr cycle amplitude ±15
        step = rng.gauss(DRIFT, VOLATILITY)
        price = price + step
        # Anchor gently back toward BASE_PRICE + trend so it doesn't drift off.
        price = 0.85 * price + 0.15 * (BASE_PRICE + trend)
        price = max(FLOOR, min(CEIL, price))
        out.append((d, round(price, 2)))
    return out


def upsert_prices(rows: list[tuple[date, float]]) -> int:
    sql = """
        INSERT INTO brent_crude (date, price_usd_per_barrel, source)
        VALUES (?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            price_usd_per_barrel = excluded.price_usd_per_barrel,
            source               = excluded.source,
            inserted_at          = CURRENT_TIMESTAMP;
    """
    payload = [(d.isoformat(), price, SOURCE_NAME) for d, price in rows]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def ingest(force_download: bool = False) -> dict:
    """MOCK: generate synthetic Brent weekly series over fuel_prices range."""
    _ = force_download  # unused for mock
    rng = _fuel_price_date_range()
    if not rng:
        raise RuntimeError(
            "No fuel_prices rows present. Run bulletin ingest first so we know "
            "which date range to generate mock Brent for."
        )
    start, end = rng
    logger.warning(
        "USING MOCK BRENT DATA (source=%s). Swap for real EIA/FRED/yfinance source.",
        SOURCE_NAME,
    )
    series = generate_mock_series(start, end)
    written = upsert_prices(series)
    return {
        "mock": True,
        "source": SOURCE_NAME,
        "rows_written": written,
        "date_range": (series[0][0].isoformat(), series[-1][0].isoformat()),
        "latest_usd_per_barrel": series[-1][1],
        "min_usd_per_barrel": min(p for _, p in series),
        "max_usd_per_barrel": max(p for _, p in series),
    }
