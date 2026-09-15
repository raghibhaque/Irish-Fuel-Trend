"""CSO Ireland monthly retail fuel Consumer Price sub-indices.

Source: Central Statistics Office (CSO) PxStat / JSON-Stat API. Public,
no auth. Matrix `CPM18` ("Consumer Price Index") publishes monthly
sub-indices for the individual COICOP-classified consumer basket
items, including "Petrol" (code 07221) and "Diesel" (code 07222).
Refreshed monthly, one working-day lag.

CSO retired its monthly national-average petrol price series (matrix
CPM04) in 2011 — CPM18's index is the only monthly CSO series that
tracks Irish forecourt prices to the current month. Unit-less index
(rebased periodically; latest base December 2023 = 100 under statistic
CPM18C01). Stored as `index_value` in cso_fuel_index and fed into the
model as a slow-moving trend anchor whose month-on-month change catches
retail-side stickiness that weekly wholesale data misses.

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

REAL_SOURCE = "CSO_CPM18"
MOCK_SOURCE = "MOCK_CSO_CPM18_v1"

ENDPOINT = "https://ws.cso.ie/public/api.jsonrpc"
MATRIX = "CPM18"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (irish-fuel-trend/0.1; +https://github.com/)",
    "Content-Type": "application/json",
}

# COICOP sub-index codes inside CPM18. Stable across quarterly rebasings.
PRODUCT_CODE_PETROL = "07221"
PRODUCT_CODE_DIESEL = "07222"

# STATISTIC slot to read (CPM18C01 = the raw index; C02/C03 are month-on-month
# and year-on-year percentage-change derivatives which we don't need).
STATISTIC_CODE_INDEX = "CPM18C01"


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _parse_period(code: str) -> date | None:
    """Parse a CSO period code. Two shapes observed:
       * '2024M03'  (older matrices)
       * '202403'   (CPM04 and newer, no separator)
    Both map to the same month-end date.
    """
    if "M" in code:
        try:
            y_str, m_str = code.split("M", 1)
            return _month_end(int(y_str), int(m_str))
        except (ValueError, IndexError):
            return None
    if len(code) == 6 and code.isdigit():
        try:
            return _month_end(int(code[:4]), int(code[4:]))
        except ValueError:
            return None
    return None


def fetch_real_series() -> list[tuple[date, str, float]] | None:
    """Best-effort call to the CSO JSON-RPC endpoint. Returns None on failure.

    JSON-Stat 2.0 allows `category.index` to be either an ordered dict
    ({code: position}) or a plain array (position implied by list index).
    Both shapes handled below. Any parse failure downgrades to mock.
    """
    try:
        return _fetch_real_series_impl()
    except Exception as e:
        logger.warning("CSO parse failed: %s", e)
        return None


def _index_as_map(index_field) -> dict[str, int]:
    """Coerce category.index (dict or list) into {code: position}."""
    if isinstance(index_field, dict):
        return {k: int(v) for k, v in index_field.items()}
    if isinstance(index_field, list):
        return {code: pos for pos, code in enumerate(index_field)}
    return {}


def _fetch_real_series_impl() -> list[tuple[date, str, float]] | None:
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
    # Product & time dim indices. Time dims in PxStat carry "TLIST" in the
    # code and class="time"; product dims carry a "C…V…" code.
    time_idx = next((i for i, d in enumerate(dim_ids)
                     if dims.get(d, {}).get("class") == "time"
                     or "TLIST" in d.upper() or d.upper().startswith("T")), None)
    prod_idx = next((i for i, d in enumerate(dim_ids)
                     if d.upper().startswith("C") and "V" in d.upper()
                     and i != time_idx), None)
    if time_idx is None or prod_idx is None:
        # Fall back to positional guess: last dim = time, first non-STATISTIC = product.
        time_idx = len(dim_ids) - 1
        prod_idx = next((i for i, d in enumerate(dim_ids)
                         if d.upper() != "STATISTIC" and i != time_idx), 0)
    time_index_raw = (dims[dim_ids[time_idx]] or {}).get("category", {}).get("index")
    time_index_map = _index_as_map(time_index_raw)
    # Order codes by their declared position so slice indices align with the flat value array.
    time_cats = [code for code, _ in sorted(time_index_map.items(), key=lambda kv: kv[1])]
    prod_cats_map = ((dims[dim_ids[prod_idx]] or {}).get("category") or {})
    prod_labels = prod_cats_map.get("label") or {}
    prod_index_map = _index_as_map(prod_cats_map.get("index"))
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
        if prod_code == PRODUCT_CODE_PETROL:
            fuel = "petrol"
        elif prod_code == PRODUCT_CODE_DIESEL:
            fuel = "diesel"
        else:
            continue
        _ = prod_labels.get(prod_code, "")  # label carried by CSO — unused after code match
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
    """Mock CPI index series: base 100 in Dec 2023, gently rising with seasonal ripple."""
    today = date.today()
    year, month = 2020, 1
    out: list[tuple[date, str, float]] = []
    i = 0
    while (year, month) <= (today.year, today.month):
        d = _month_end(year, month)
        seasonal = 3.0 * math.sin(i / 6.0 * math.pi)
        drift = 0.6 * i
        petrol = round(90.0 + drift + seasonal, 3)
        diesel = round(88.0 + drift + seasonal * 0.85, 3)
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
