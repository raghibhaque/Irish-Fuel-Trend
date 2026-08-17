"""Tests for the county basis decomposition.

All pure arithmetic — no network, no database, no fixtures. The numbers in
`RANKING_ROWS` are real 30-day petrol medians pulled from FuelWatch on
2026-08-05, so the reference check below is against live-shaped data.
"""
from __future__ import annotations

import math

import pytest

from app.prediction import county


# county, median EUR/L, station count — real 30-day petrol rows.
RANKING_ROWS = [
    {"county": "Dublin",    "median_eur_per_litre": 1.813, "station_count": 57},
    {"county": "Cork",      "median_eur_per_litre": 1.809, "station_count": 43},
    {"county": "Kerry",     "median_eur_per_litre": 1.859, "station_count": 31},
    {"county": "Waterford", "median_eur_per_litre": 1.800, "station_count": 19},
    {"county": "Wicklow",   "median_eur_per_litre": 1.759, "station_count": 10},
    {"county": "Donegal",   "median_eur_per_litre": 1.859, "station_count": 3},
]

NATIONAL = {
    "trend": "up",
    "predicted_weekly_return": 0.012,
    "confidence": 0.71,
    "r2": 0.18,
    "current_pump_eur_per_l": 1.800,
    "predicted_pump_eur_per_l": 1.820,
    "predicted_pump_low_eur_per_l": 1.800,
    "predicted_pump_high_eur_per_l": 1.840,
    "predicted_pump_3w_eur_per_l": 1.860,
}


def _row(county_name: str, window_days: int = 30) -> dict:
    src = next(r for r in RANKING_ROWS if r["county"] == county_name)
    return {**src, "fuel_type": "petrol", "window_days": window_days}


# ----------------------------- national_reference -----------------------------

def test_national_reference_matches_hand_computed_weighted_mean():
    expected = sum(r["median_eur_per_litre"] * r["station_count"] for r in RANKING_ROWS) \
        / sum(r["station_count"] for r in RANKING_ROWS)
    assert county.national_reference(RANKING_ROWS) == pytest.approx(expected)


def test_national_reference_is_station_weighted_not_a_plain_mean():
    """Dublin's 57 stations must outweigh Donegal's 3."""
    plain = sum(r["median_eur_per_litre"] for r in RANKING_ROWS) / len(RANKING_ROWS)
    assert county.national_reference(RANKING_ROWS) != pytest.approx(plain)


def test_raw_basis_sums_to_zero_when_station_weighted():
    """The point of a same-pool reference: offsets cancel across the pool."""
    ref = county.national_reference(RANKING_ROWS)
    weighted = sum(
        county.raw_basis(r["median_eur_per_litre"], ref) * r["station_count"]
        for r in RANKING_ROWS
    )
    assert weighted == pytest.approx(0.0, abs=1e-9)


def test_national_reference_ignores_zero_station_rows():
    rows = RANKING_ROWS + [{"county": "Ghost", "median_eur_per_litre": 9.99,
                            "station_count": 0}]
    assert county.national_reference(rows) == pytest.approx(
        county.national_reference(RANKING_ROWS)
    )


def test_national_reference_rejects_empty_pool():
    with pytest.raises(ValueError):
        county.national_reference([])


# ---------------------------------- shrink -----------------------------------

def test_shrink_is_monotone_in_station_count():
    values = [county.shrink(0.05, n) for n in (1, 3, 10, 30, 57)]
    assert values == sorted(values)
    assert all(v < 0.05 for v in values)


def test_shrink_half_point_is_at_k_stations():
    assert county.shrink(0.06, 10, k=10.0) == pytest.approx(0.03)


def test_shrink_returns_zero_with_no_stations():
    assert county.shrink(0.05, 0) == 0.0


def test_shrink_preserves_sign():
    assert county.shrink(-0.04, 20) < 0
    assert county.shrink(0.04, 20) > 0


# -------------------------------- widen_band ---------------------------------

def test_widen_band_shrinks_toward_national_as_sample_grows():
    bands = [county.widen_band(0.02, n) for n in (1, 3, 10, 30, 57)]
    assert bands == sorted(bands, reverse=True)


def test_widen_band_always_exceeds_the_national_band():
    for n in (0, 1, 3, 10, 57, 500):
        assert county.widen_band(0.02, n) > 0.02


def test_widen_band_combines_in_quadrature():
    expected = math.sqrt(0.02 ** 2 + (county.SIGMA_STATION_EUR / math.sqrt(9)) ** 2)
    assert county.widen_band(0.02, 9) == pytest.approx(expected)


def test_widen_band_degrades_to_full_dispersion_with_no_stations():
    expected = math.sqrt(0.02 ** 2 + county.SIGMA_STATION_EUR ** 2)
    assert county.widen_band(0.02, 0) == pytest.approx(expected)


# -------------------------------- select_row ---------------------------------

def test_select_row_prefers_the_primary_window():
    rows = [_row("Cork", window_days=180), _row("Cork", window_days=30)]
    chosen = county.select_row(rows)
    assert chosen["window_days"] == 30
    assert chosen["stale"] is False


def test_select_row_falls_back_to_the_wide_window_and_flags_stale():
    chosen = county.select_row([_row("Cork", window_days=180)])
    assert chosen["window_days"] == 180
    assert chosen["stale"] is True


def test_select_row_raises_for_a_county_with_no_median_anywhere():
    with pytest.raises(county.UnknownCountyError):
        county.select_row([])


def test_thin_primary_window_upgrades_to_wider_window_with_more_stations():
    """Real Clare shape on 2026-08-11: 4 stations on 30d, 8 on 180d.

    A primary median built on 4 stations is thin enough to defensibly upgrade
    to a wider window that carries more of them, at the cost of freshness."""
    rows = [
        {"county": "Clare", "fuel_type": "petrol",
         "median_eur_per_litre": 1.839, "station_count": 4, "window_days": 30},
        {"county": "Clare", "fuel_type": "petrol",
         "median_eur_per_litre": 1.814, "station_count": 8, "window_days": 180},
    ]
    chosen = county.select_row(rows)
    assert chosen["window_days"] == 180
    assert chosen["station_count"] == 8
    assert chosen["stale"] is True


def test_thin_primary_stays_when_wider_window_has_no_extra_stations():
    """No upgrade unless the wider window is strictly denser — otherwise we
    would just be trading freshness for nothing."""
    rows = [
        {"county": "Nowhere", "fuel_type": "petrol",
         "median_eur_per_litre": 1.80, "station_count": 4, "window_days": 30},
        {"county": "Nowhere", "fuel_type": "petrol",
         "median_eur_per_litre": 1.80, "station_count": 4, "window_days": 180},
    ]
    chosen = county.select_row(rows)
    assert chosen["window_days"] == 30
    assert chosen["stale"] is False


def test_well_sampled_primary_is_not_upgraded_even_when_wider_is_denser():
    """A 30d median over 20 stations is already trustworthy; the 180d bloat
    from turnover would drag the price toward a stale level for no benefit."""
    rows = [
        {"county": "Cork", "fuel_type": "petrol",
         "median_eur_per_litre": 1.81, "station_count": 20, "window_days": 30},
        {"county": "Cork", "fuel_type": "petrol",
         "median_eur_per_litre": 1.83, "station_count": 60, "window_days": 180},
    ]
    chosen = county.select_row(rows)
    assert chosen["window_days"] == 30
    assert chosen["stale"] is False


def test_primary_window_default_matches_what_the_ingest_actually_writes():
    """These live in two modules; a silent divergence would break the fallback."""
    from app.data_sources import fuelwatch_counties

    assert county.PRIMARY_WINDOW_DAYS == fuelwatch_counties.PRIMARY_WINDOW_DAYS


def test_window_preference_prefers_freshest_qualifying_window():
    """14d beats 30d when it clears the sample threshold — that's the whole
    point of adding a shorter window. And it's non-stale because 14d is a
    strict freshness bonus over the historical default."""
    rows = [
        {"county": "Dublin", "fuel_type": "petrol",
         "median_eur_per_litre": 1.815, "station_count": 40, "window_days": 14},
        {"county": "Dublin", "fuel_type": "petrol",
         "median_eur_per_litre": 1.820, "station_count": 60, "window_days": 30},
        {"county": "Dublin", "fuel_type": "petrol",
         "median_eur_per_litre": 1.828, "station_count": 90, "window_days": 180},
    ]
    chosen = county.select_row(rows)
    assert chosen["window_days"] == 14
    assert chosen["stale"] is False


def test_thin_14d_falls_back_to_30d_without_flagging_stale():
    """14d too thin for populous counties still gets the 30d default, and 30d
    is by definition not stale (the pre-14d standard)."""
    rows = [
        {"county": "Kildare", "fuel_type": "petrol",
         "median_eur_per_litre": 1.815, "station_count": 5, "window_days": 14},
        {"county": "Kildare", "fuel_type": "petrol",
         "median_eur_per_litre": 1.820, "station_count": 21, "window_days": 30},
    ]
    chosen = county.select_row(rows)
    assert chosen["window_days"] == 30
    assert chosen["stale"] is False


def test_thin_14d_and_thin_30d_step_all_the_way_to_180d():
    rows = [
        {"county": "Clare", "fuel_type": "petrol",
         "median_eur_per_litre": 1.815, "station_count": 2, "window_days": 14},
        {"county": "Clare", "fuel_type": "petrol",
         "median_eur_per_litre": 1.820, "station_count": 4, "window_days": 30},
        {"county": "Clare", "fuel_type": "petrol",
         "median_eur_per_litre": 1.830, "station_count": 12, "window_days": 180},
    ]
    chosen = county.select_row(rows)
    assert chosen["window_days"] == 180
    assert chosen["stale"] is True


# --------------------------- build_county_prediction --------------------------

def test_prediction_inherits_national_direction_unchanged():
    ref = county.national_reference(RANKING_ROWS)
    out = county.build_county_prediction(NATIONAL, county.select_row([_row("Cork")]), ref)
    assert out["trend"] == NATIONAL["trend"]
    assert out["predicted_weekly_return"] == NATIONAL["predicted_weekly_return"]


def test_cheap_county_lands_below_the_national_price():
    ref = county.national_reference(RANKING_ROWS)
    out = county.build_county_prediction(NATIONAL, county.select_row([_row("Wicklow")]), ref)
    assert out["basis_eur_per_litre"] < 0
    assert out["current_pump_eur_per_l"] < NATIONAL["current_pump_eur_per_l"]
    assert out["predicted_pump_eur_per_l"] < NATIONAL["predicted_pump_eur_per_l"]


def test_every_price_field_moves_by_exactly_the_basis():
    ref = county.national_reference(RANKING_ROWS)
    out = county.build_county_prediction(NATIONAL, county.select_row([_row("Kerry")]), ref)
    basis = out["basis_eur_per_litre"]
    for field in ("current_pump_eur_per_l", "predicted_pump_eur_per_l",
                  "predicted_pump_3w_eur_per_l"):
        assert out[field] == pytest.approx(NATIONAL[field] + basis)


def test_thin_county_is_flagged_and_its_offset_is_heavily_shrunk():
    ref = county.national_reference(RANKING_ROWS)
    out = county.build_county_prediction(NATIONAL, county.select_row([_row("Donegal")]), ref)
    assert out["low_sample"] is True
    assert out["station_count"] == 3
    # n=3, K=10 keeps 3/13 of the raw offset.
    assert abs(out["basis_eur_per_litre"]) < abs(out["basis_raw_eur_per_litre"]) / 4


def test_well_sampled_county_is_not_flagged_and_keeps_most_of_its_offset():
    ref = county.national_reference(RANKING_ROWS)
    out = county.build_county_prediction(NATIONAL, county.select_row([_row("Dublin")]), ref)
    assert out["low_sample"] is False
    assert abs(out["basis_eur_per_litre"]) > abs(out["basis_raw_eur_per_litre"]) * 0.8


def test_thin_county_gets_a_wider_band_than_a_well_sampled_one():
    ref = county.national_reference(RANKING_ROWS)
    thin = county.build_county_prediction(NATIONAL, county.select_row([_row("Donegal")]), ref)
    thick = county.build_county_prediction(NATIONAL, county.select_row([_row("Dublin")]), ref)
    width = lambda o: o["predicted_pump_high_eur_per_l"] - o["predicted_pump_low_eur_per_l"]
    assert width(thin) > width(thick)


def test_crowd_median_is_reported_separately_from_the_translated_price():
    """The card shows both; they are on different measurement scales."""
    ref = county.national_reference(RANKING_ROWS)
    out = county.build_county_prediction(NATIONAL, county.select_row([_row("Cork")]), ref)
    assert out["crowd_median_eur_per_litre"] == 1.809
    assert out["current_pump_eur_per_l"] != out["crowd_median_eur_per_litre"]


def test_county_level_tracks_the_national_payload_exactly():
    """The national payload is the only source of truth for the price level.

    `model._latest_observed_pump` decides which observation that is; this
    module must not second-guess it, or the county page drifts away from the
    site's own national headline.
    """
    ref = county.national_reference(RANKING_ROWS)
    for name in ("Dublin", "Wicklow", "Donegal"):
        out = county.build_county_prediction(NATIONAL, county.select_row([_row(name)]), ref)
        basis = out["basis_eur_per_litre"]
        assert out["current_pump_eur_per_l"] == pytest.approx(
            NATIONAL["current_pump_eur_per_l"] + basis
        )


def test_county_carries_the_national_models_movement_unchanged():
    ref = county.national_reference(RANKING_ROWS)
    out = county.build_county_prediction(NATIONAL, county.select_row([_row("Cork")]), ref)
    national_step = NATIONAL["predicted_pump_eur_per_l"] - NATIONAL["current_pump_eur_per_l"]
    national_step_3w = NATIONAL["predicted_pump_3w_eur_per_l"] - NATIONAL["current_pump_eur_per_l"]
    assert out["predicted_pump_eur_per_l"] - out["current_pump_eur_per_l"] \
        == pytest.approx(national_step)
    assert out["predicted_pump_3w_eur_per_l"] - out["current_pump_eur_per_l"] \
        == pytest.approx(national_step_3w)


def test_band_stays_centred_on_the_prediction():
    ref = county.national_reference(RANKING_ROWS)
    out = county.build_county_prediction(NATIONAL, county.select_row([_row("Cork")]), ref)
    midpoint = (out["predicted_pump_low_eur_per_l"] + out["predicted_pump_high_eur_per_l"]) / 2
    assert midpoint == pytest.approx(out["predicted_pump_eur_per_l"])
