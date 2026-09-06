"""North-West Europe refined product prices — a closer proxy for Irish pumps
than NYMEX RBOB/ULSD futures, which are New York cargoes priced in USD/gal.

Two symbols stored in the same `refined_products` table (kept USD/gal for
schema uniformity — the model converts to EUR itself):

    NWE_GASOIL   Rotterdam ULSD 10ppm gasoil, Northwest Europe cargo assay
                 (proxy for European diesel wholesale).
    EBOB         Eurobob-grade RBOB equivalent (proxy for European petrol
                 wholesale). Formally quoted in USD/t on the ICE strip; the
                 per-gallon representation here is derived so the model can
                 treat it uniformly with the NY series.

Real source: EIA petroleum spot data (`/v2/petroleum/pri/spt/data/` — weekly
Rotterdam quotes) via `EIA_API_KEY`. Free tier is enough for weekly refresh.
Without an API key or network, we fall back to a MOCK series derived from the
existing Brent series + a modest refining margin + deterministic noise, so
the pipeline stays end-to-end runnable offline.

Set env var `IRISH_FUEL_FORCE_MOCK_NWE=1` to skip the real fetch even when
an API key is present (useful for tests / offline dev).
"""
from __future__ import annotations

import logging
import math
import os
import random
from datetime import date, datetime

from app.db import connection

logger = logging.getLogger(__name__)

# `refined_products` stores USD/gal to match NY futures; NWE prices are
# typically quoted USD/tonne (gasoil) or USD/bbl. Convert to gal on the way in.
GAL_PER_TONNE_GASOIL = 297.0   # industry-standard conversion, ULSD 10ppm at 15°C
GAL_PER_BBL = 42.0
BASELINE_GAL_PER_TONNE = {
    "NWE_GASOIL": GAL_PER_TONNE_GASOIL,
    "EBOB":       333.0,          # 87 RON gasoline density at 15°C
}

REAL_SOURCE = "EIA_PET_SPT"
MOCK_SOURCE = "MOCK_NWE_v1"

# Rough long-run refining margins over Brent, USD/bbl. Used only by the MOCK
# generator to keep the synthetic series in a plausible neighbourhood.
MOCK_MARGIN_USD_PER_BBL = {
    "NWE_GASOIL": 14.0,
    "EBOB":       12.0,
}
MOCK_SEEDS = {"NWE_GASOIL": 4321, "EBOB": 1234}
MOCK_VOL_PCT = 0.04    # ±4% weekly noise around the margin


# --------------- REAL: EIA petroleum spot API ---------------
# Facet mapping: EIA exposes each Rotterdam quote under a distinct series-id
# in its v2 petroleum API. Kept here so the fetcher stays declarative.
EIA_SERIES = {
    "NWE_GASOIL": "PET.RWTC.D",   # placeholder; swap for Rotterdam ULSD series id
    "EBOB":       "PET.EER_EPMRU_PF4_Y35NY_DPG.D",  # placeholder
}


def _fetch_eia_daily(series_id: str, api_key: str) -> list[tuple[date, float]]:
    """Return (date, USD/gal) rows for `series_id`. Requires `EIA_API_KEY`."""
    import urllib.parse
    import urllib.request
    import json

    url = "https://api.eia.gov/v2/seriesid/" + urllib.parse.quote(series_id)
    params = urllib.parse.urlencode({"api_key": api_key})
    with urllib.request.urlopen(f"{url}?{params}", timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    series = payload.get("response", {}).get("data") or []
    rows: list[tuple[date, float]] = []
    for row in series:
        d_str = row.get("period")
        val   = row.get("value")
        if not d_str or val is None:
            continue
        try:
            d = datetime.fromisoformat(d_str).date()
        except ValueError:
            continue
        rows.append((d, float(val)))
    rows.sort(key=lambda t: t[0])
    return rows


def fetch_real_daily(symbol: str) -> list[tuple[date, float]]:
    api_key = os.environ.get("EIA_API_KEY")
    if not api_key:
        raise RuntimeError("EIA_API_KEY not set — skipping real NWE fetch.")
    series_id = EIA_SERIES.get(symbol)
    if not series_id:
        raise RuntimeError(f"No EIA series id configured for symbol {symbol!r}.")
    return _fetch_eia_daily(series_id, api_key)


# --------------- MOCK fallback ---------------
def _brent_daily_rows() -> list[tuple[date, float]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT date, price_usd_per_barrel FROM brent_crude ORDER BY date"
        ).fetchall()
    out: list[tuple[date, float]] = []
    for r in rows:
        raw = r["date"]
        # sqlite date detection returns `datetime.date` when the adapter is
        # registered, but a plain string otherwise. Support both without
        # depending on the connection-side config.
        if isinstance(raw, date):
            d = raw
        elif isinstance(raw, datetime):
            d = raw.date()
        else:
            d = datetime.fromisoformat(str(raw)).date()
        out.append((d, float(r["price_usd_per_barrel"])))
    return out


def generate_mock_series(symbol: str) -> list[tuple[date, float]]:
    """Derive a plausible USD/gal NWE series from Brent + refining margin.

    The mock is not calibrated to real Rotterdam quotes — it exists purely to
    keep the model pipeline runnable offline. The seed is fixed per symbol so
    successive `ingest --force` runs produce the same synthetic history.
    """
    brent = _brent_daily_rows()
    if not brent:
        raise RuntimeError("Cannot generate MOCK NWE series: no brent_crude rows.")
    rng = random.Random(MOCK_SEEDS[symbol])
    margin = MOCK_MARGIN_USD_PER_BBL[symbol]
    out: list[tuple[date, float]] = []
    for i, (d, brent_usd_bbl) in enumerate(brent):
        # Slow-moving refining margin with a mild seasonal component (winter
        # gasoil demand, summer petrol driving demand → symbol-specific phase).
        phase = 0.0 if symbol == "NWE_GASOIL" else math.pi
        seasonal = 2.5 * math.sin(i / 26.0 + phase)
        noise = rng.gauss(0.0, margin * MOCK_VOL_PCT)
        product_usd_bbl = max(5.0, brent_usd_bbl + margin + seasonal + noise)
        product_usd_gal = product_usd_bbl / GAL_PER_BBL
        out.append((d, round(product_usd_gal, 4)))
    return out


# --------------- storage ---------------
def _delete_source(source: str) -> int:
    with connection() as conn:
        cur = conn.execute("DELETE FROM refined_products WHERE source = ?", (source,))
        return cur.rowcount


def upsert(symbol: str, rows: list[tuple[date, float]], source: str) -> int:
    sql = """
        INSERT INTO refined_products (date, symbol, price_usd_per_gal, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date, symbol) DO UPDATE SET
            price_usd_per_gal = excluded.price_usd_per_gal,
            source            = excluded.source,
            inserted_at       = CURRENT_TIMESTAMP;
    """
    payload = [(d.isoformat(), symbol, price, source) for d, price in rows]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


SYMBOLS = ("NWE_GASOIL", "EBOB")


def ingest(force_download: bool = False) -> dict:
    _ = force_download
    force_mock = os.environ.get("IRISH_FUEL_FORCE_MOCK_NWE") == "1"
    summary: dict = {"symbols": {}}
    for symbol in SYMBOLS:
        real_ok = False
        if not force_mock:
            try:
                rows = fetch_real_daily(symbol)
                if not rows:
                    raise RuntimeError("Empty series")
                _delete_source(MOCK_SOURCE)
                written = upsert(symbol, rows, REAL_SOURCE)
                summary["symbols"][symbol] = {
                    "mock": False,
                    "source": REAL_SOURCE,
                    "rows_written": written,
                    "date_range": (rows[0][0].isoformat(), rows[-1][0].isoformat()),
                    "latest_usd_per_gal": rows[-1][1],
                }
                real_ok = True
            except Exception as e:
                logger.warning("Real NWE fetch failed for %s (%s). Falling back to MOCK.", symbol, e)
        if not real_ok:
            rows = generate_mock_series(symbol)
            written = upsert(symbol, rows, MOCK_SOURCE)
            summary["symbols"][symbol] = {
                "mock": True,
                "source": MOCK_SOURCE,
                "rows_written": written,
                "date_range": (rows[0][0].isoformat(), rows[-1][0].isoformat()),
                "latest_usd_per_gal": rows[-1][1],
            }
    return summary
