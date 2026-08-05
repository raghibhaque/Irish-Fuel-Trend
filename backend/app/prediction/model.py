"""Trend prediction model — v3.

Modelling target: **wholesale** weekly return (EU Bulletin's pre-tax price).
Rationale: pump price = wholesale + fixed excise + carbon tax + NORA levy,
then VAT applied. The tax stack is largely flat per litre and dilutes the
crude-driven signal in the return series. Modelling wholesale isolates the
part of the price that actually moves with crude, refining margin, and FX.

Features (per fuel):
    brent_eur_ret_2w   Brent (EUR/bbl) return over prior 2 weeks
    brent_eur_ret_6w   Brent (EUR/bbl) return over prior 6 weeks
    product_eur_ret_1w  Refined-product (EUR/gal) return, prior 1 week
    product_eur_ret_4w  Refined-product (EUR/gal) return, prior 4 weeks
    prev_return         wholesale return from the previous week (AR(1) term)

Product = RBOB for petrol, ULSD (NY heating oil) for diesel. Refined-product
futures move with refining margins that pure crude misses.

Regressor: Ridge (alpha=1.0). Regularises correlated multi-lag features.

Accuracy reporting: walk-forward CV via `TimeSeriesSplit` (5 folds) produces
`r2_cv` — the honest out-of-sample number. In-sample `r2_in_sample` is kept
for transparency but should never drive user-facing confidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import math

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.model_selection import TimeSeriesSplit

from app.db import connection

FLAT_BAND = 0.005  # ±0.5% weekly wholesale return = "flat"

FEATURE_COLS = [
    "brent_eur_ret_2w",
    "brent_eur_ret_6w",
    "product_eur_ret_1w",
    "product_eur_ret_4w",
    "prev_return",
]

# fuel -> refined product symbol used as its downstream proxy
FUEL_PRODUCT = {"petrol": "RBOB", "diesel": "ULSD"}

CV_SPLITS = 5


@dataclass
class TrendPrediction:
    fuel_type: str
    as_of: date
    trend: str
    predicted_weekly_return: float
    confidence: float
    features: dict
    r2: float                 # out-of-sample (walk-forward CV) — user-facing
    r2_in_sample: float       # kept for transparency / debugging
    n_train: int
    coefficients: dict
    product_symbol: str


def _load_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with connection() as conn:
        prices = pd.read_sql_query(
            "SELECT date, fuel_type, price_eur_per_litre, price_wo_tax_eur_per_litre "
            "FROM fuel_prices WHERE country='IE' ORDER BY date",
            conn,
            parse_dates=["date"],
        )
        brent = pd.read_sql_query(
            "SELECT date, price_usd_per_barrel FROM brent_crude ORDER BY date",
            conn,
            parse_dates=["date"],
        )
        fx = pd.read_sql_query(
            "SELECT date, eur_usd FROM fx_rates ORDER BY date",
            conn,
            parse_dates=["date"],
        )
        refined = pd.read_sql_query(
            "SELECT date, symbol, price_usd_per_gal FROM refined_products ORDER BY date",
            conn,
            parse_dates=["date"],
        )
    return prices, brent, fx, refined


def _to_eur(usd_series: pd.Series, fx: pd.DataFrame) -> pd.Series:
    fx_s = fx.set_index("date")["eur_usd"].sort_index()
    fx_aligned = fx_s.reindex(usd_series.index, method="ffill")
    return (usd_series / fx_aligned).dropna()


def _brent_eur_series(brent: pd.DataFrame, fx: pd.DataFrame) -> pd.Series:
    br = brent.set_index("date")["price_usd_per_barrel"].sort_index()
    return _to_eur(br, fx)


def _product_eur_series(refined: pd.DataFrame, fx: pd.DataFrame, symbol: str) -> pd.Series:
    sub = refined[refined["symbol"] == symbol]
    if sub.empty:
        return pd.Series(dtype=float)
    s = sub.set_index("date")["price_usd_per_gal"].sort_index()
    return _to_eur(s, fx)


def build_dataset(fuel_type: str) -> pd.DataFrame:
    prices, brent, fx, refined = _load_frames()
    product_symbol = FUEL_PRODUCT[fuel_type]

    price_series = (
        prices[prices["fuel_type"] == fuel_type]
        .dropna(subset=["price_wo_tax_eur_per_litre"])
        .set_index("date")["price_wo_tax_eur_per_litre"]
        .sort_index()
    )

    df = pd.DataFrame({"wholesale": price_series})
    df["wholesale_prev"] = df["wholesale"].shift(1)
    df["target_ret"] = df["wholesale"] / df["wholesale_prev"] - 1
    df["prev_return"] = df["target_ret"].shift(1)

    brent_eur_daily = _brent_eur_series(brent, fx)
    brent_eur_weekly = brent_eur_daily.reindex(df.index, method="ffill")
    df["brent_eur"]        = brent_eur_weekly
    df["brent_eur_lag1"]   = brent_eur_weekly.shift(1)
    df["brent_eur_ret_2w"] = df["brent_eur_lag1"] / brent_eur_weekly.shift(3) - 1
    df["brent_eur_ret_6w"] = df["brent_eur_lag1"] / brent_eur_weekly.shift(7) - 1

    prod_eur_daily = _product_eur_series(refined, fx, product_symbol)
    if prod_eur_daily.empty:
        # Refined product series missing — leave columns as NaN so dropna
        # removes affected rows; if all removed, train_and_predict raises.
        df["product_eur"] = np.nan
        df["product_eur_lag1"] = np.nan
        df["product_eur_ret_1w"] = np.nan
        df["product_eur_ret_4w"] = np.nan
    else:
        prod_eur_weekly = prod_eur_daily.reindex(df.index, method="ffill")
        df["product_eur"]        = prod_eur_weekly
        df["product_eur_lag1"]   = prod_eur_weekly.shift(1)
        df["product_eur_ret_1w"] = df["product_eur_lag1"] / prod_eur_weekly.shift(2) - 1
        df["product_eur_ret_4w"] = df["product_eur_lag1"] / prod_eur_weekly.shift(5) - 1

    keep = ["wholesale", "target_ret", "brent_eur", "brent_eur_lag1",
            "product_eur", "product_eur_lag1"] + FEATURE_COLS
    return df[keep].dropna()


def _walk_forward_stats(X: np.ndarray, y: np.ndarray, alpha: float = 1.0) -> tuple[float, float]:
    """Mean out-of-sample R² and pooled residual std across TimeSeriesSplit folds.

    Residual std is the standard deviation of (y_test - y_pred) pooled across
    every OOS fold — the honest one-step-ahead forecast error the model
    actually makes on unseen weeks. Used to build a calibrated probability
    that the sign of the next return matches the model's prediction.
    """
    n = len(y)
    n_splits = min(CV_SPLITS, max(2, n // 20))
    if n_splits < 2 or n < 20:
        return 0.0, float(np.std(y)) or 1e-6
    tscv = TimeSeriesSplit(n_splits=n_splits)
    scores: list[float] = []
    residuals: list[np.ndarray] = []
    for train_idx, test_idx in tscv.split(X):
        if len(train_idx) < 10 or len(test_idx) < 2:
            continue
        m = Ridge(alpha=alpha)
        m.fit(X[train_idx], y[train_idx])
        # sklearn returns R² which can go negative for poor fits — that IS the
        # honest signal; do not clip.
        scores.append(float(m.score(X[test_idx], y[test_idx])))
        residuals.append(y[test_idx] - m.predict(X[test_idx]))
    r2 = float(np.mean(scores)) if scores else 0.0
    resid_std = float(np.std(np.concatenate(residuals))) if residuals else float(np.std(y))
    return r2, max(resid_std, 1e-6)


def _direction_probability(predicted_ret: float, resid_std: float, r2_cv: float) -> float:
    """P(actual return has same sign as prediction) under a Normal residual model.

    Formula: Φ(|ŷ| / σ) where σ is the OOS residual std. If the model has
    demonstrated no OOS skill (r2_cv <= 0) it is not trustworthy regardless
    of |ŷ|, so we shrink toward 0.5 (a coin flip) proportional to r2 shortfall.
    Never below 0.5 (below-50% means predicting the wrong direction — we would
    flip the trend label instead).
    """
    z = abs(predicted_ret) / resid_std
    p_raw = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))  # Φ(z)
    skill = max(0.0, min(1.0, r2_cv))
    # Shrink toward coin flip when skill is weak, but never below 0.5.
    p = 0.5 + (p_raw - 0.5) * (0.5 + 0.5 * skill)
    return max(0.5, min(0.999, p))


def train_and_predict(fuel_type: str) -> TrendPrediction:
    df = build_dataset(fuel_type)
    if len(df) < 30:
        raise RuntimeError(f"Not enough rows to train ({len(df)}). Need 30+.")

    X = df[FEATURE_COLS].values
    y = df["target_ret"].values

    # Honest accuracy first — walk-forward CV on the same feature matrix.
    r2_cv, resid_std = _walk_forward_stats(X, y)

    # Fit final model on all data for the forward prediction.
    model = Ridge(alpha=1.0)
    model.fit(X, y)
    r2_in_sample = float(model.score(X, y))

    latest = df.iloc[-1]
    x_next = latest[FEATURE_COLS].values.reshape(1, -1)
    predicted_ret = float(model.predict(x_next)[0])

    if predicted_ret > FLAT_BAND:
        trend = "up"
    elif predicted_ret < -FLAT_BAND:
        trend = "down"
    else:
        trend = "flat"

    # Calibrated probability that the actual return has the same sign as the
    # prediction, given the OOS residual std and demonstrated OOS skill.
    # Naturally high when |prediction| is large vs typical error, low when not.
    confidence = _direction_probability(predicted_ret, resid_std, r2_cv)

    features = {
        "brent_eur_ret_2w": float(latest["brent_eur_ret_2w"]),
        "brent_eur_ret_6w": float(latest["brent_eur_ret_6w"]),
        "product_eur_ret_1w": float(latest["product_eur_ret_1w"]),
        "product_eur_ret_4w": float(latest["product_eur_ret_4w"]),
        "prev_wholesale_return": float(latest["prev_return"]),
        "brent_eur_per_bbl_current": float(latest["brent_eur"]),
        "brent_eur_per_bbl_lag1": float(latest["brent_eur_lag1"]),
        "product_eur_per_gal_current": float(latest["product_eur"]),
        "latest_wholesale_eur_per_l": float(latest["wholesale"]),
    }

    coefficients = {name: float(coef) for name, coef in zip(FEATURE_COLS, model.coef_)}
    coefficients["_intercept"] = float(model.intercept_)

    return TrendPrediction(
        fuel_type=fuel_type,
        as_of=latest.name.date() if hasattr(latest.name, "date") else latest.name,
        trend=trend,
        predicted_weekly_return=predicted_ret,
        confidence=confidence,
        features=features,
        r2=r2_cv,
        r2_in_sample=r2_in_sample,
        n_train=len(df),
        coefficients=coefficients,
        product_symbol=FUEL_PRODUCT[fuel_type],
    )
