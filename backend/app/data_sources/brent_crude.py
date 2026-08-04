"""Brent crude oil price fetcher.

Primary source: Yahoo Finance ticker `BZ=F` (Brent crude front-month futures)
via the `yfinance` library. Daily close in USD/bbl. Free, no API key. May
break if Yahoo changes their unofficial endpoints — in that case we fall
back to a MOCK generator so the pipeline keeps working.

Set env var `IRISH_FUEL_FORCE_MOCK_BRENT=1` to force the mock even when
yfinance is reachable (useful for offline dev).
"""
from __future__ import annotations

import logging
import math
import os
import random
from datetime import date, datetime, timedelta

from app.db import connection

logger = logging.getLogger(__name__)

REAL_SOURCE = "YFINANCE_BZF"
MOCK_SOURCE = "MOCK_BRENT_v1"
YF_TICKER = "BZ=F"

# ---- mock generator config (fallback only) ----
BASE_PRICE = 75.0
VOLATILITY = 3.5
FLOOR = 20.0
CEIL = 140.0
SEED = 42


# --------------- REAL: yfinance ---------------
def fetch_real_daily() -> list[tuple[date, float]]:
    """Fetch full-history daily Brent close via yfinance. Raises on failure."""
    import yfinance as yf  # imported lazily so mock path works without dep
    ticker = yf.Ticker(YF_TICKER)
    hist = ticker.history(period="max", interval="1d", auto_adjust=False)
    if hist is None or hist.empty or "Close" not in hist.columns:
        raise RuntimeError(f"yfinance returned empty history for {YF_TICKER}")
    hist = hist[hist["Close"].notna()]
    rows: list[tuple[date, float]] = []
    for ts, close in hist["Close"].items():
        # ts is a tz-aware Timestamp; convert to naive date
        d = ts.date() if hasattr(ts, "date") else datetime.fromisoformat(str(ts)).date()
        rows.append((d, round(float(close), 2)))
    rows.sort(key=lambda t: t[0])
    return rows


# --------------- MOCK fallback ---------------
def _fuel_price_date_range() -> tuple[date, date] | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT MIN(date), MAX(date) FROM fuel_prices WHERE country='IE'"
        ).fetchone()
    if not row or not row[0]:
        return None
    return datetime.fromisoformat(row[0]).date(), datetime.fromisoformat(row[1]).date()


def _weekly_dates(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def generate_mock_series(start: date, end: date) -> list[tuple[date, float]]:
    rng = random.Random(SEED)
    dates = _weekly_dates(start, end)
    price = BASE_PRICE
    out: list[tuple[date, float]] = []
    for i, d in enumerate(dates):
        trend = 15.0 * math.sin(i / 26.0)
        step = rng.gauss(0.0, VOLATILITY)
        price = price + step
        price = 0.85 * price + 0.15 * (BASE_PRICE + trend)
        price = max(FLOOR, min(CEIL, price))
        out.append((d, round(price, 2)))
    return out


# --------------- storage ---------------
def _delete_source(source: str) -> int:
    with connection() as conn:
        cur = conn.execute("DELETE FROM brent_crude WHERE source = ?", (source,))
        return cur.rowcount


def upsert_prices(rows: list[tuple[date, float]], source: str) -> int:
    sql = """
        INSERT INTO brent_crude (date, price_usd_per_barrel, source)
        VALUES (?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            price_usd_per_barrel = excluded.price_usd_per_barrel,
            source               = excluded.source,
            inserted_at          = CURRENT_TIMESTAMP;
    """
    payload = [(d.isoformat(), price, source) for d, price in rows]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def ingest(force_download: bool = False) -> dict:
    _ = force_download
    force_mock = os.environ.get("IRISH_FUEL_FORCE_MOCK_BRENT") == "1"

    if not force_mock:
        try:
            rows = fetch_real_daily()
            # Clear any prior mock rows so mocks don't linger next to real data.
            deleted = _delete_source(MOCK_SOURCE)
            if deleted:
                logger.info("Removed %d prior MOCK Brent rows.", deleted)
            written = upsert_prices(rows, REAL_SOURCE)
            return {
                "mock": False,
                "source": REAL_SOURCE,
                "rows_written": written,
                "date_range": (rows[0][0].isoformat(), rows[-1][0].isoformat()),
                "latest_usd_per_barrel": rows[-1][1],
            }
        except Exception as e:
            logger.warning("Real Brent fetch failed (%s). Falling back to MOCK.", e)

    rng = _fuel_price_date_range()
    if not rng:
        raise RuntimeError("Cannot generate mock Brent: no fuel_prices rows.")
    start, end = rng
    logger.warning("USING MOCK BRENT DATA (source=%s).", MOCK_SOURCE)
    series = generate_mock_series(start, end)
    written = upsert_prices(series, MOCK_SOURCE)
    return {
        "mock": True,
        "source": MOCK_SOURCE,
        "rows_written": written,
        "date_range": (series[0][0].isoformat(), series[-1][0].isoformat()),
        "latest_usd_per_barrel": series[-1][1],
    }
