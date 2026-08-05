"""Refined petroleum product futures — RBOB gasoline + NY ULSD (heating oil).

Rationale: Brent is upstream crude. What Irish pumps actually track is the
downstream *product* price after refining. RBOB (Reformulated Blendstock for
Oxygenate Blending) is the NYMEX gasoline benchmark; ULSD (traded as heating
oil, `HO=F`) is the diesel benchmark. Both are USD/gallon. Correlate closely
with Rotterdam ARA barge prices which are the true European wholesale
reference. Free data via yfinance; no API key.

Petrol prediction uses RBOB; diesel prediction uses ULSD.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from app.db import connection

logger = logging.getLogger(__name__)

# symbol -> (yfinance ticker, human label)
SYMBOLS = {
    "RBOB": "RB=F",   # NYMEX RBOB gasoline futures, USD/gal
    "ULSD": "HO=F",   # NYMEX ULSD (heating oil) futures, USD/gal
}


def fetch_symbol(symbol: str) -> list[tuple[date, float]]:
    import yfinance as yf
    ticker_code = SYMBOLS[symbol]
    ticker = yf.Ticker(ticker_code)
    hist = ticker.history(period="max", interval="1d", auto_adjust=False)
    if hist is None or hist.empty or "Close" not in hist.columns:
        raise RuntimeError(f"yfinance returned empty history for {ticker_code}")
    hist = hist[hist["Close"].notna()]
    rows: list[tuple[date, float]] = []
    for ts, close in hist["Close"].items():
        d = ts.date() if hasattr(ts, "date") else datetime.fromisoformat(str(ts)).date()
        rows.append((d, round(float(close), 4)))
    rows.sort(key=lambda t: t[0])
    return rows


def upsert(symbol: str, rows: list[tuple[date, float]]) -> int:
    sql = """
        INSERT INTO refined_products (date, symbol, price_usd_per_gal, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date, symbol) DO UPDATE SET
            price_usd_per_gal = excluded.price_usd_per_gal,
            source            = excluded.source,
            inserted_at       = CURRENT_TIMESTAMP;
    """
    source = f"YFINANCE_{SYMBOLS[symbol].replace('=', '_')}"
    payload = [(d.isoformat(), symbol, price, source) for d, price in rows]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def ingest(force_download: bool = False) -> dict:
    _ = force_download
    summary: dict = {"symbols": {}}
    for symbol in SYMBOLS:
        try:
            rows = fetch_symbol(symbol)
            written = upsert(symbol, rows)
            summary["symbols"][symbol] = {
                "rows_written": written,
                "date_range": (rows[0][0].isoformat(), rows[-1][0].isoformat()),
                "latest_usd_per_gal": rows[-1][1],
            }
        except Exception as e:
            logger.warning("refined_products fetch failed for %s: %s", symbol, e)
            summary["symbols"][symbol] = {"error": str(e)}
    return summary
