"""Brent futures curve proxy via the BNO ETF (United States Brent Oil Fund).

Why this exists: front-month Brent (`BZ=F`, already ingested) tells us the
spot. What it doesn't tell us is the *shape* of the forward curve —
backwardation (spot > forwards) versus contango (spot < forwards). That
shape is a well-known physical-tightness signal and is orthogonal to spot
returns: the two can diverge sharply during inventory squeezes or storage
gluts.

Cleanest way to get the shape would be individual dated ICE Brent contracts
(BZM26.NYM, BZU26.NYM, …) stitched into a rolling M2/M3 with an explicit
expiry calendar. That's a lot of moving parts for a one-signal feature.

Instead, this fetches BNO — a US-listed ETF that holds Brent futures and
rolls them monthly. Its price is not USD/bbl (it's USD/share), so absolute
levels are meaningless here — but its *return* differential against front-
month Brent captures the curve slope for free:

    contango  → BNO tends to underperform BZ=F (roll yield is negative)
    backward. → BNO tends to outperform BZ=F  (roll yield is positive)

The trend model consumes the 4-week return spread (Brent − BNO), lagged one
week, as `brent_curve_slope_4w`.

Source: yfinance ticker `BNO`. Free, no API key. Mock fallback (derived
from the existing Brent series with a small deterministic offset) keeps the
pipeline runnable offline. Set `IRISH_FUEL_FORCE_MOCK_BRENT_CURVE=1` to
force the mock.

Second curve point: `USL` (United States 12 Month Oil Fund). USL holds an
equal-weighted ladder of the next 12 WTI futures — its return vs the
front-month WTI captures the M1→M12 curve slope, a longer-dated view than
BNO's M1→M3-ish exposure. When Brent and WTI curve slopes diverge (Brent
backwardated while WTI is flat, say) that is itself a signal of regional
supply tightness biting European wholesale. Ingested into the same
`brent_curve_etf` table via `usl_price_usd` (added by the db migration).
"""
from __future__ import annotations

import logging
import math
import os
import random
from datetime import date, datetime, timedelta

from app.db import connection

logger = logging.getLogger(__name__)

REAL_SOURCE = "YFINANCE_BNO"
MOCK_SOURCE = "MOCK_BRENT_CURVE_v1"
YF_TICKER = "BNO"
YF_TICKER_USL = "USL"


# --------------- REAL: yfinance ---------------
def _fetch_yf_close(ticker_symbol: str) -> list[tuple[date, float]]:
    import yfinance as yf
    ticker = yf.Ticker(ticker_symbol)
    hist = ticker.history(period="max", interval="1d", auto_adjust=False)
    if hist is None or hist.empty or "Close" not in hist.columns:
        raise RuntimeError(f"yfinance returned empty history for {ticker_symbol}")
    hist = hist[hist["Close"].notna()]
    rows: list[tuple[date, float]] = []
    for ts, close in hist["Close"].items():
        d = ts.date() if hasattr(ts, "date") else datetime.fromisoformat(str(ts)).date()
        rows.append((d, round(float(close), 4)))
    rows.sort(key=lambda t: t[0])
    return rows


def fetch_real_daily() -> list[tuple[date, float]]:
    """Fetch full-history BNO daily close via yfinance. Raises on failure."""
    return _fetch_yf_close(YF_TICKER)


def fetch_real_usl_daily() -> list[tuple[date, float]]:
    """Fetch full-history USL (12-mo WTI ladder ETF) daily close. Raises on failure."""
    return _fetch_yf_close(YF_TICKER_USL)


# --------------- MOCK fallback ---------------
def _brent_daily_rows() -> list[tuple[date, float]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT date, price_usd_per_barrel FROM brent_crude ORDER BY date"
        ).fetchall()
    out: list[tuple[date, float]] = []
    for r in rows:
        raw = r["date"]
        if isinstance(raw, date):
            d = raw
        elif isinstance(raw, datetime):
            d = raw.date()
        else:
            d = datetime.fromisoformat(str(raw)).date()
        out.append((d, float(r["price_usd_per_barrel"])))
    return out


# Mock keeps BNO in the ~$30/share range with a slow-moving roll drag so the
# derived curve slope varies but stays bounded. Fixed seed for deterministic
# ingest replay.
MOCK_SEED = 8642
MOCK_BASE = 30.0
MOCK_VOL_PCT = 0.03
MOCK_ROLL_DRAG_PCT = 0.001   # per-day contango bleed applied to base level


def generate_mock_series() -> list[tuple[date, float]]:
    brent = _brent_daily_rows()
    if not brent:
        # No Brent history to anchor against — synthesise a flat weekly series
        # over the last ~4 years so build_dataset still has enough overlap.
        today = date.today()
        brent = [
            (today - timedelta(days=i), 75.0)
            for i in range(52 * 4)
        ]
        brent.reverse()
    rng = random.Random(MOCK_SEED)
    out: list[tuple[date, float]] = []
    # Move BNO with Brent (correlation ~0.9 in the real series) plus a slow
    # roll-drag term and small idiosyncratic noise.
    brent0 = brent[0][1]
    level = MOCK_BASE
    for i, (d, brent_usd) in enumerate(brent):
        brent_ret = brent_usd / brent0 - 1.0
        noise = rng.gauss(0.0, MOCK_BASE * MOCK_VOL_PCT * 0.1)
        seasonal = 0.4 * math.sin(i / 90.0)
        level = MOCK_BASE * (1.0 + brent_ret) - (i * MOCK_ROLL_DRAG_PCT) + seasonal + noise
        level = max(5.0, level)
        out.append((d, round(level, 4)))
    return out


# --------------- storage ---------------
def _delete_source(source: str) -> int:
    with connection() as conn:
        cur = conn.execute("DELETE FROM brent_curve_etf WHERE source = ?", (source,))
        return cur.rowcount


def upsert_prices(rows: list[tuple[date, float]], source: str) -> int:
    sql = """
        INSERT INTO brent_curve_etf (date, price_usd, source)
        VALUES (?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            price_usd   = excluded.price_usd,
            source      = excluded.source,
            inserted_at = CURRENT_TIMESTAMP;
    """
    payload = [(d.isoformat(), price, source) for d, price in rows]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def upsert_usl_prices(rows: list[tuple[date, float]]) -> int:
    """Store USL closes into the usl_price_usd column of brent_curve_etf.

    USL rows are keyed on date and update the existing row (created by the
    BNO ingest) so we retain a single row per trading day rather than
    duplicating storage.
    """
    sql = """
        INSERT INTO brent_curve_etf (date, price_usd, usl_price_usd, source)
        VALUES (?, 0.0, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            usl_price_usd = excluded.usl_price_usd,
            inserted_at   = CURRENT_TIMESTAMP;
    """
    payload = [(d.isoformat(), price, "YFINANCE_USL") for d, price in rows]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def ingest(force_download: bool = False) -> dict:
    _ = force_download
    force_mock = os.environ.get("IRISH_FUEL_FORCE_MOCK_BRENT_CURVE") == "1"

    if not force_mock:
        try:
            rows = fetch_real_daily()
            deleted = _delete_source(MOCK_SOURCE)
            if deleted:
                logger.info("Removed %d prior MOCK brent-curve rows.", deleted)
            written = upsert_prices(rows, REAL_SOURCE)
            return {
                "mock": False,
                "source": REAL_SOURCE,
                "rows_written": written,
                "date_range": (rows[0][0].isoformat(), rows[-1][0].isoformat()),
                "latest_usd": rows[-1][1],
            }
        except Exception as e:
            logger.warning("Real BNO fetch failed (%s). Falling back to MOCK.", e)

    series = generate_mock_series()
    logger.warning("USING MOCK BRENT-CURVE DATA (source=%s).", MOCK_SOURCE)
    written = upsert_prices(series, MOCK_SOURCE)
    return {
        "mock": True,
        "source": MOCK_SOURCE,
        "rows_written": written,
        "date_range": (series[0][0].isoformat(), series[-1][0].isoformat()),
        "latest_usd": series[-1][1],
    }


def ingest_usl(force_download: bool = False) -> dict:
    """Fetch USL closes into brent_curve_etf.usl_price_usd. Mock fallback derives
    a level from the existing BNO series shifted slightly, so a curve-slope
    feature is defined even offline."""
    _ = force_download
    force_mock = os.environ.get("IRISH_FUEL_FORCE_MOCK_BRENT_CURVE") == "1"
    if not force_mock:
        try:
            rows = fetch_real_usl_daily()
            written = upsert_usl_prices(rows)
            return {
                "mock": False,
                "source": "YFINANCE_USL",
                "rows_written": written,
                "date_range": (rows[0][0].isoformat(), rows[-1][0].isoformat()),
                "latest_usd": rows[-1][1],
            }
        except Exception as e:
            logger.warning("Real USL fetch failed (%s). Falling back to mock.", e)

    with connection() as conn:
        bno_rows = conn.execute(
            "SELECT date, price_usd FROM brent_curve_etf ORDER BY date"
        ).fetchall()
    if not bno_rows:
        return {"mock": True, "source": "MOCK_USL_v1", "rows_written": 0}
    # Mock USL as BNO scaled up ~50% (matches rough $30 vs $45 real levels)
    # with a small deterministic offset so the two series aren't collinear.
    mock_rows: list[tuple[date, float]] = []
    rng = random.Random(9931)
    for i, r in enumerate(bno_rows):
        raw = r["date"]
        if isinstance(raw, date):
            d = raw
        elif isinstance(raw, datetime):
            d = raw.date()
        else:
            d = datetime.fromisoformat(str(raw)).date()
        base = float(r["price_usd"]) * 1.5
        noise = rng.gauss(0.0, 0.3)
        seasonal = 0.4 * math.sin(i / 120.0)
        mock_rows.append((d, round(base + noise + seasonal, 4)))
    written = upsert_usl_prices(mock_rows)
    return {
        "mock": True,
        "source": "MOCK_USL_v1",
        "rows_written": written,
        "date_range": (mock_rows[0][0].isoformat(), mock_rows[-1][0].isoformat()),
        "latest_usd": mock_rows[-1][1],
    }
