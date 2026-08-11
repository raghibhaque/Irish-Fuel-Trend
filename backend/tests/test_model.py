"""Regression tests for the ensemble prediction model.

Pure-function tests + synthetic-data invariants for the primitives in
`app.prediction.model`. The two DB-backed smoke tests at the bottom are
skipped when `data/fuel_trend.db` is missing, so the suite still passes
on a fresh checkout with no ingested data.

These tests guard the properties that make the user-facing prediction
trustworthy — R² behaves like sklearn's, confidence stays in the
documented [0.5, 0.9] band, the ensemble is deterministic across
refreshes, and the empirical calibration corrects in the right direction
when the model's observed hit-rate diverges from its formula-implied one.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.prediction import model as m


# ------------------------------ _r2 ------------------------------

def test_r2_is_one_for_perfect_prediction():
    y = np.array([0.01, -0.02, 0.03, -0.01, 0.04])
    assert m._r2(y, y) == pytest.approx(1.0)


def test_r2_is_zero_for_mean_prediction():
    y = np.array([0.01, -0.02, 0.03, -0.01, 0.04])
    yhat = np.full_like(y, y.mean())
    assert m._r2(y, yhat) == pytest.approx(0.0)


def test_r2_can_go_negative_for_worse_than_mean_prediction():
    y = np.array([0.01, -0.02, 0.03, -0.01, 0.04])
    assert m._r2(y, -y) < 0


def test_r2_returns_zero_when_target_is_constant():
    y = np.zeros(5)
    yhat = np.arange(5, dtype=float)
    assert m._r2(y, yhat) == 0.0


# ---------------------------- ensemble ----------------------------

def test_ensemble_has_all_three_member_types():
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import Ridge

    types = {type(x) for x in m._new_ensemble()}
    assert {Ridge, RandomForestRegressor, GradientBoostingRegressor} <= types


def test_ensemble_prediction_is_deterministic_across_refreshes():
    """RF + GB carry a fixed random_state; the daily refresh must not drift."""
    rng = np.random.default_rng(42)
    X = rng.normal(size=(80, 6))
    y = X[:, 0] * 0.5 + rng.normal(scale=0.01, size=80)
    p1 = m._ensemble_fit_predict(X[:60], y[:60], X[60:])
    p2 = m._ensemble_fit_predict(X[:60], y[:60], X[60:])
    np.testing.assert_array_equal(p1, p2)


def test_ensemble_output_length_matches_test_set():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(50, 6))
    y = rng.normal(size=50)
    preds = m._ensemble_fit_predict(X[:40], y[:40], X[40:])
    assert preds.shape == (10,)


def test_ensemble_learns_a_linear_signal_on_synthetic_data():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(300, 6))
    y = 0.4 * X[:, 0] - 0.3 * X[:, 1] + rng.normal(scale=0.05, size=300)
    preds = m._ensemble_fit_predict(X[:240], y[:240], X[240:])
    assert m._r2(y[240:], preds) > 0.5


# ------------------------- walk-forward R² ------------------------

def test_walk_forward_r2_returns_zero_for_a_tiny_series():
    X = np.zeros((10, 6))
    y = np.zeros(10)
    assert m._walk_forward_r2(X, y) == 0.0


def test_walk_forward_r2_positive_on_a_learnable_signal():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(400, 6))
    y = 0.5 * X[:, 0] - 0.2 * X[:, 2] + rng.normal(scale=0.05, size=400)
    assert m._walk_forward_r2(X, y) > 0.3


# ------------------------ direction probability -------------------

def test_direction_probability_at_zero_or_negative_skill_shrinks_to_coin_flip():
    assert m._direction_probability(0.05, 0.02, r2_cv=0.0) == pytest.approx(0.5)
    assert m._direction_probability(0.05, 0.02, r2_cv=-0.3) == pytest.approx(0.5)


def test_direction_probability_grows_monotonically_with_absolute_prediction():
    a = m._direction_probability(0.005, 0.01, r2_cv=0.2)
    b = m._direction_probability(0.020, 0.01, r2_cv=0.2)
    assert b > a


def test_direction_probability_stays_within_bounded_range():
    """Never below coin-flip, never above the 0.999 cap in the source."""
    for pred in (0.001, 0.05, 0.5, 5.0):
        for r2 in (0.0, 0.1, 0.5, 0.99):
            p = m._direction_probability(pred, 0.01, r2_cv=r2)
            assert 0.5 <= p <= 0.999


# ----------------------- empirical calibration --------------------

def test_empirical_calibration_is_neutral_with_no_backtest_rows():
    assert m._empirical_calibration([], resid_std=0.01, r2_cv=0.1) == 1.0


def test_empirical_calibration_scales_up_when_model_beats_its_formula():
    """Predicted=actual every week means hit-rate 100% while the r2=0 formula
    only claims 50% — calibration should widen the confidence spread."""
    bt = [{"predicted_return": 0.01, "actual_return": 0.01} for _ in range(52)]
    assert m._empirical_calibration(bt, resid_std=0.02, r2_cv=0.0) > 1.0


def test_empirical_calibration_shrinks_when_model_underperforms_formula():
    """Model predicts nonzero and is always wrong — the formula was too
    confident, calibration should compress confidence toward coin-flip."""
    bt = [{"predicted_return": 0.01, "actual_return": -0.01} for _ in range(52)]
    assert m._empirical_calibration(bt, resid_std=0.02, r2_cv=0.2) < 1.0


# ------------------------ DB-backed smoke tests -------------------

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "fuel_trend.db"


@pytest.mark.skipif(not DB_PATH.exists(), reason="fuel_trend.db not present")
def test_train_and_predict_smoke_produces_sane_petrol_output():
    p = m.train_and_predict("petrol")
    assert p.trend in {"up", "down", "flat"}
    assert 0.5 <= p.confidence <= 0.9
    assert p.n_train >= 30
    assert p.current_pump_eur_per_l > 0
    # 50% band must actually straddle the point forecast.
    assert p.predicted_pump_low_eur_per_l <= p.predicted_pump_eur_per_l <= p.predicted_pump_high_eur_per_l
    assert len(p.backtest) > 0
    required = {"date", "actual_return", "predicted_return",
                "actual_pump_eur_per_l", "predicted_pump_eur_per_l",
                "direction_correct"}
    for row in p.backtest:
        assert required <= set(row.keys())


@pytest.mark.skipif(not DB_PATH.exists(), reason="fuel_trend.db not present")
def test_train_and_predict_smoke_produces_sane_diesel_output():
    p = m.train_and_predict("diesel")
    assert p.trend in {"up", "down", "flat"}
    assert p.product_symbol == "ULSD"
    assert p.predicted_pump_3w_eur_per_l > 0
    assert set(p.coefficients.keys()) >= set(m.FEATURE_COLS) | {"_intercept"}
