"""County price localisation — basis decomposition.

No county model is trained here, because there is nothing to train one on:
FuelWatch exposes a current county median but no county history at all (see
`data_sources/fuelwatch_counties`). Instead the national forecast is
translated per county by a measured offset:

    county_price(c, f) = national_price(f) + basis(c, f)

**The basis must be same-pool.** The national series is weekly EU Oil Bulletin
data; the county medians are 30-day trailing crowd-sourced retail. Subtracting
one from the other would fold a crowd-vs-bulletin method bias into every
county offset — a county would look expensive partly because of how it was
measured. So the national reference is derived from the *same RPC rows* the
county medians come from, as a station-weighted mean. The weighted basis then
sums to zero by construction and the method bias cancels.

Sanity check against upstream on 2026-08-05: the derived 30-day petrol
reference was EUR 1.820 against FuelWatch's own `get_average_prices` value of
EUR 1.819.

Everything in this module is a pure function over plain dicts — no database,
no network — so the arithmetic is testable in isolation.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

# Shrinkage half-point, in stations. A county's raw offset is scaled by
# n/(n+K), so a county with K stations keeps half of it. K=10 is roughly the
# median county's sample size. This is a prior, not a fitted value — revisit
# once enough daily snapshots exist to estimate between/within-county variance.
SHRINK_K = 10.0

# Prior for station-to-station price dispersion within a county, in EUR/L.
# Chosen to be comparable to the ~10c spread observed *across* county medians.
# Also a prior, for the same reason.
SIGMA_STATION_EUR = 0.045

# Below this many stations the median is too thin to present without a caveat.
LOW_SAMPLE_THRESHOLD = 8

# Standard window — anything at or below is presented as non-stale. 30d is the
# level users grew up on and roughly matches upstream FuelWatch's default
# aggregate; falling back to 180d earns the "stale" flag.
PRIMARY_WINDOW_DAYS = 30

# Windows tried in order, freshest first. 14d is preferred wherever it clears
# the sample threshold — densely-reported counties (Dublin, Cork, Galway) get a
# median that tracks pump moves twice as fast. Sparser counties silently step
# down to 30d, then 180d, without the user having to choose.
WINDOW_PREFERENCE: tuple[int, ...] = (14, 30, 180)


class UnknownCountyError(ValueError):
    """Raised when a county has no median at any window."""


def national_reference(rows: Iterable[dict]) -> float:
    """Station-weighted mean of county medians — the same-pool national price.

    `rows` must all share one fuel and one window, otherwise the reference
    mixes populations and the basis stops summing to zero.
    """
    total_weight = 0.0
    total = 0.0
    for r in rows:
        n = float(r.get("station_count") or 0)
        if n <= 0:
            continue
        total += float(r["median_eur_per_litre"]) * n
        total_weight += n
    if total_weight <= 0:
        raise ValueError("No rows with a positive station count to average.")
    return total / total_weight


def raw_basis(median_eur_per_litre: float, national_ref: float) -> float:
    """Unshrunk county offset in EUR/L."""
    return float(median_eur_per_litre) - float(national_ref)


def shrink(basis: float, station_count: int, k: float = SHRINK_K) -> float:
    """Pull a raw basis toward zero in proportion to sample size.

    A median over three stations carries little information about the county,
    so most of its apparent offset is discarded. Returns 0.0 at n=0.
    """
    n = float(station_count or 0)
    if n <= 0:
        return 0.0
    return basis * n / (n + k)


def widen_band(band_half_national: float, station_count: int,
               sigma_station: float = SIGMA_STATION_EUR) -> float:
    """Combine national forecast error with county sampling error.

    The two are independent, so they add in quadrature. Sampling error falls
    as 1/sqrt(n); with no stations at all it degrades to the full
    station-level dispersion rather than pretending to certainty.
    """
    n = float(station_count or 0)
    sampling = sigma_station if n <= 0 else sigma_station / math.sqrt(n)
    return math.sqrt(float(band_half_national) ** 2 + sampling ** 2)


def select_row(rows: Sequence[dict],
               window_preference: Sequence[int] = WINDOW_PREFERENCE,
               min_stations: int = LOW_SAMPLE_THRESHOLD,
               standard_window: int = PRIMARY_WINDOW_DAYS) -> dict:
    """Pick the best available row for one county/fuel.

    Iterates `window_preference` in freshness order (default 14 → 30 → 180)
    and returns the first row whose sample size clears `min_stations`. If
    none of the preferred windows qualify, falls back to the densest row
    available so the county still renders a number.

    `stale` is True iff the chosen window is wider than `standard_window`
    (30d). That preserves the pre-14d meaning of stale — "trading freshness
    for coverage beyond the historical default" — so upgrading to 14d does
    not itself flag the number as stale; it's a strict bonus when it exists.

    Rows are the same county+fuel at different windows.
    """
    if not rows:
        raise UnknownCountyError("No county median at any window.")

    by_window = {int(r["window_days"]): r for r in rows}
    for w in window_preference:
        r = by_window.get(w)
        if r and int(r.get("station_count") or 0) >= min_stations:
            return {**r, "stale": w > standard_window}

    # Nothing met the threshold — take the densest row available, and flag
    # stale by the same rule (wider than standard = stale). Ties break to the
    # first row of that station count, which is insertion order — the
    # caller's row order determines the tiebreak.
    densest = max(rows, key=lambda r: int(r.get("station_count") or 0))
    return {**densest, "stale": int(densest["window_days"]) > standard_window}


def build_county_prediction(national: dict, county_row: dict, national_ref: float) -> dict:
    """Translate one national fuel prediction into a county-localised one.

    `national` is a `/api/prediction` fuel payload; `county_row` is a
    `county_prices` row for the same fuel, already passed through `select_row`.

    The current price reported for the county is the national price plus the
    basis, *not* the raw crowd median. Those are two different measurement
    scales, and the card has to agree with the chart, which is also drawn on
    the national scale. The raw median travels alongside as its own field so
    both numbers stay visible.

    The national payload is the single source of truth for the price level —
    `prediction/model._latest_observed_pump` decides which observation that
    is, and every page on the site inherits it. This module only ever shifts
    that level sideways.

    Trend and weekly return are inherited unchanged: a constant additive
    offset cannot change the sign of a movement, and the UI says so rather
    than implying a county-specific directional call.
    """
    median = float(county_row["median_eur_per_litre"])
    n = int(county_row.get("station_count") or 0)

    basis_raw = raw_basis(median, national_ref)
    basis = shrink(basis_raw, n)

    band_half_national = abs(
        float(national["predicted_pump_high_eur_per_l"])
        - float(national["predicted_pump_low_eur_per_l"])
    ) / 2.0
    band_half = widen_band(band_half_national, n)

    current = float(national["current_pump_eur_per_l"]) + basis
    predicted = float(national["predicted_pump_eur_per_l"]) + basis

    return {
        "county": county_row["county"],
        "fuel_type": county_row["fuel_type"],
        "crowd_median_eur_per_litre": median,
        "station_count": n,
        "window_days": int(county_row["window_days"]),
        "stale": bool(county_row.get("stale", False)),
        "low_sample": n < LOW_SAMPLE_THRESHOLD,
        "basis_eur_per_litre": basis,
        "basis_raw_eur_per_litre": basis_raw,
        "trend": national["trend"],
        "predicted_weekly_return": float(national["predicted_weekly_return"]),
        "confidence": float(national["confidence"]),
        "r2": float(national["r2"]),
        "current_pump_eur_per_l": current,
        "predicted_pump_eur_per_l": predicted,
        "predicted_pump_low_eur_per_l": predicted - band_half,
        "predicted_pump_high_eur_per_l": predicted + band_half,
        "predicted_pump_3w_eur_per_l": float(national["predicted_pump_3w_eur_per_l"]) + basis,
    }
