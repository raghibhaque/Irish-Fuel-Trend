"""CSO Ireland monthly retail fuel price index.

Source: Central Statistics Office (CSO) PxStat / JSON-Stat API. Public,
no auth. The "CPM17" table publishes Consumer Price Index components,
including "Petrol" and "Diesel" per litre indices used to compute the
official inflation basket. Refreshed monthly, one working-day lag.

Endpoint (JSON-Stat 2.0):
    https://ws.cso.ie/public/api.jsonrpc

Method:
    PxStat.Data.Cube_API.ReadDataset
    { "class": "query", "id": [], "dimension": {}, "extension":
      { "pivot": null, "codes": false, "language": { "code": "en" },
        "format": { "type": "JSON-stat", "version": "2.0" },
        "matrix": "CPM17" } }

Index base: 2016 = 100. Not a currency price — a unit-less index. Feed
into the model as a slow-moving trend indicator (month-on-month change)
to catch retail-side stickiness that weekly wholesale data misses.

Real fetch is best-effort; on failure we emit a deterministic mock series
that mirrors typical CPI oscillation so downstream code stays runnable.
Set IRISH_FUEL_FORCE_MOCK_CSO=1 to force the mock.
"""
from __future__ import annotations

import calendar
import json
import logging
import math
import os
from datetime import date

import requests

from app.db import connection

logger = logging.getLogger(__name__)

REAL_SOURCE = "CSO_CPM17"
MOCK_SOURCE = "MOCK_CSO_CPM17_v1"

ENDPOINT = "https://ws.cso.ie/public/api.jsonrpc"
MATRIX = "CPM17"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (irish-fuel-trend/0.1; +https://github.com/)",
    "Content-Type": "application/json",
}

# Product codes inside the CSO CPM17 matrix. These strings are the
# canonical labels the CSO uses; if their layout ever changes we log and
# fall back to the mock.
PRODUCT_PETROL_LABELS = {"Petrol", "Petrol (per litre)"}
PRODUCT_DIESEL_LABELS = {"Diesel", "Auto Diesel", "Diesel (per litre)"}


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _parse_period(code: str) -> date | None:
    """CSO period codes look like '2024M03'. Return month-end date."""
    if "M" not in code:
        return None
    try:
        y_str, m_str = code.split("M", 1)
        return _month_end(int(y_str), int(m_str))
    except (ValueError, IndexError):
        return None


def fetch_real_series() -> list[tuple[date, str, float]] | None:
    """Best-effort call to the CSO JSON-RPC endpoint. Returns None on failure."""
    payload = {
        "jsonrpc": "2.0",
        "method": "PxStat.Data.Cube_API.ReadDataset",
        "params": {
            "class": "query",
            "id": [],
            "dimension": {},
            "extension": {
                "pivot": None,
                "codes": False,
                "language": {"code": "en"},
                "format": {"type": "JSON-stat", "version": "2.0"},
                "matrix": MATRIX,
            },
            "version": "2.0",
        },
        "id": 1,
    }
    try:
        r = requests.post(ENDPOINT, headers=HEADERS,
                          data=json.dumps(payload), timeout=60)
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        logger.warning("CSO real fetch failed: %s", e)
        return None
    result = body.get("result") or {}
    dims = result.get("dimension") or {}
    values = result.get("value") or []
    if not values or not dims:
        logger.warning("CSO response missing 'value' or 'dimension'")
        return None
    # JSON-stat: dimensions have deterministic order in "id"; sizes in "size";
    # value array flattened row-major over that order. We locate the "TLIST(M1)"
    # (time) and product dims and rebuild coordinates.
    dim_ids = result.get("id") or []
    sizes = result.get("size") or []
    if not dim_ids or not sizes or len(dim_ids) != len(sizes):
        logger.warning("CSO response layout unexpected")
        return None
    # Product & time dim indices.
    time_idx = next((i for i, d in enumerate(dim_ids)
                     if dims.get(d, {}).get("class") == "time"
                     or d.upper().startswith("T")), None)
    prod_idx = next((i for i, d in enumerate(dim_ids)
                     if d.lower().startswith("cpm") or "product" in d.lower()),
                    None)
    if time_idx is None or prod_idx is None:
        # Fall back to positional guess: first dim = product, last = time.
        prod_idx, time_idx = 0, len(dim_ids) - 1
    time_cats = list(((dims[dim_ids[time_idx]] or {}).get("category", {}).get("index") or {}).keys())
    prod_cats_map = ((dims[dim_ids[prod_idx]] or {}).get("category") or {})
    prod_labels = prod_cats_map.get("label") or {}
    prod_index_map = prod_cats_map.get("index") or {}
    if not time_cats or not prod_index_map:
        logger.warning("CSO response missing time or product categories")
        return None

    # Strides for row-major flatten.
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    def flat_index(coords: list[int]) -> int:
        return sum(c * s for c, s in zip(coords, strides))

    rows: list[tuple[date, str, float]] = []
    for prod_code, prod_pos in prod_index_map.items():
        label = prod_labels.get(prod_code, "")
        if label in PRODUCT_PETROL_LABELS:
            fuel = "petrol"
        elif label in PRODUCT_DIESEL_LABELS:
            fuel = "diesel"
        else:
            continue
        for t_pos, t_code in enumerate(time_cats):
            d = _parse_period(t_code)
            if d is None:
                continue
            coords = [0] * len(sizes)
            coords[prod_idx] = prod_pos
            coords[time_idx] = t_pos
            try:
                val = values[flat_index(coords)]
            except IndexError:
                continue
            if val is None:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            rows.append((d, fuel, round(fval, 4)))
    return rows or None


MOCK_SEED_BASE = 5719


def generate_mock_series() -> list[tuple[date, str, float]]:
    """Mock CPI series: base 100 in 2016, gently rising with seasonal ripple."""
    today = date.today()
    year, month = 2020, 1
    out: list[tuple[date, str, float]] = []
    i = 0
    while (year, month) <= (today.year, today.month):
        d = _month_end(year, month)
        seasonal = 3.0 * math.sin(i / 6.0 * math.pi)
        drift = 0.6 * i
        petrol = round(120.0 + drift + seasonal, 3)
        diesel = round(118.0 + drift + seasonal * 0.85, 3)
        out.append((d, "petrol", petrol))
        out.append((d, "diesel", diesel))
        month += 1
        if month > 12:
            month = 1
            year += 1
        i += 1
    return out


def upsert_index(rows: list[tuple[date, str, float]], source: str) -> int:
    sql = """
        INSERT INTO cso_fuel_index (date, fuel_type, index_value, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date, fuel_type)
        DO UPDATE SET index_value = excluded.index_value,
                      source      = excluded.source,
                      inserted_at = CURRENT_TIMESTAMP;
    """
    payload = [(d.isoformat(), fuel, val, source) for d, fuel, val in rows]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def _delete_source(source: str) -> int:
    with connection() as conn:
        cur = conn.execute("DELETE FROM cso_fuel_index WHERE source=?", (source,))
        return cur.rowcount


def ingest(force_download: bool = False) -> dict:
    _ = force_download
    force_mock = os.environ.get("IRISH_FUEL_FORCE_MOCK_CSO") == "1"
    if not force_mock:
        real = fetch_real_series()
        if real:
            _delete_source(MOCK_SOURCE)
            written = upsert_index(real, REAL_SOURCE)
            real.sort(key=lambda t: t[0])
            return {
                "mock": False,
                "source": REAL_SOURCE,
                "rows_written": written,
                "date_range": (real[0][0].isoformat(), real[-1][0].isoformat()),
            }
        logger.warning("CSO real fetch failed — falling back to mock.")
    series = generate_mock_series()
    _delete_source(MOCK_SOURCE)
    written = upsert_index(series, MOCK_SOURCE)
    return {
        "mock": True,
        "source": MOCK_SOURCE,
        "rows_written": written,
        "date_range": (series[0][0].isoformat(), series[-1][0].isoformat()),
    }
