"""Trend prediction model — v2.

Modelling target: **wholesale** weekly return (EU Bulletin's pre-tax price).
Rationale: pump price = wholesale + fixed excise + carbon tax + NORA levy,
then VAT applied. The tax stack is largely flat per litre and dilutes the
crude-driven signal in the return series. Modelling wholesale isolates the
part of the price that actually moves with crude and FX.

Direction/trend of wholesale return is the same as pump return, so the
up/down/flat label transfers directly to what the driver at the pump sees
(with a small time lag as retailers pass through the change).

Features:
    brent_eur_ret_1w  Brent (EUR/bbl) return over prior 1 week
    brent_eur_ret_2w  Brent (EUR/bbl) return over prior 2 weeks
    brent_eur_ret_4w  Brent (EUR/bbl) return over prior 4 weeks
    brent_eur_ret_6w  Brent (EUR/bbl) return over prior 6 weeks
    prev_return        wholesale return from the previous week (AR(1) term)

Regressor: Ridge (alpha=1.0). Regularises the correlated multi-lag features
without sacrificing interpretability.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
from sklearn.linear_model import Ridge

from app.db import connection

FLAT_BAND = 0.005  # ±0.5% weekly wholesale return = "flat"

FEATURE_COLS = [
    "brent_eur_ret_1w",
    "brent_eur_ret_2w",
    "brent_eur_ret_4w",
    "brent_eur_ret_6w",
    "prev_return",
]


@dataclass
class TrendPrediction:
    fuel_type: str
    as_of: date
    trend: str
    predicted_weekly_return: float
    confidence: float
    features: dict
    r2: float
    n_train: int
    coefficients: dict


def _load_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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
    return prices, brent, fx


def _brent_eur_series(brent: pd.DataFrame, fx: pd.DataFrame) -> pd.Series:
    """Daily Brent expressed in EUR/bbl (usd / eur_usd_rate)."""
    br = brent.set_index("date")["price_usd_per_barrel"].sort_index()
    fx_s = fx.set_index("date")["eur_usd"].sort_index()
    fx_aligned = fx_s.reindex(br.index, method="ffill")
    return (br / fx_aligned).dropna()


def build_dataset(fuel_type: str) -> pd.DataFrame:
    prices, brent, fx = _load_frames()

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
    # Sample daily Brent-in-EUR onto the weekly fuel index using ffill
    brent_eur_weekly = brent_eur_daily.reindex(df.index, method="ffill")

    df["brent_eur"]        = brent_eur_weekly
    df["brent_eur_lag1"]   = brent_eur_weekly.shift(1)
    df["brent_eur_lag2"]   = brent_eur_weekly.shift(2)
    df["brent_eur_lag4"]   = brent_eur_weekly.shift(4)
    df["brent_eur_lag6"]   = brent_eur_weekly.shift(6)
    df["brent_eur_ret_1w"] = df["brent_eur_lag1"] / df["brent_eur_lag2"] - 1
    df["brent_eur_ret_2w"] = df["brent_eur_lag1"] / brent_eur_weekly.shift(3) - 1
    df["brent_eur_ret_4w"] = df["brent_eur_lag1"] / df["brent_eur_lag4"] - 1
    df["brent_eur_ret_6w"] = df["brent_eur_lag1"] / df["brent_eur_lag6"] - 1

    keep = ["wholesale", "target_ret", "brent_eur", "brent_eur_lag1"] + FEATURE_COLS
    return df[keep].dropna()


def train_and_predict(fuel_type: str) -> TrendPrediction:
    df = build_dataset(fuel_type)
    if len(df) < 30:
        raise RuntimeError(f"Not enough rows to train ({len(df)}). Need 30+.")

    X = df[FEATURE_COLS].values
    y = df["target_ret"].values

    model = Ridge(alpha=1.0)
    model.fit(X, y)
    r2 = float(model.score(X, y))

    latest = df.iloc[-1]
    x_next = latest[FEATURE_COLS].values.reshape(1, -1)
    predicted_ret = float(model.predict(x_next)[0])

    if predicted_ret > FLAT_BAND:
        trend = "up"
    elif predicted_ret < -FLAT_BAND:
        trend = "down"
    else:
        trend = "flat"

    std_ret = float(df["target_ret"].std()) or 1e-6
    confidence = min(1.0, abs(predicted_ret) / (2 * std_ret))

    features = {
        "brent_eur_ret_1w": float(latest["brent_eur_ret_1w"]),
        "brent_eur_ret_2w": float(latest["brent_eur_ret_2w"]),
        "brent_eur_ret_4w": float(latest["brent_eur_ret_4w"]),
        "brent_eur_ret_6w": float(latest["brent_eur_ret_6w"]),
        "prev_wholesale_return": float(latest["prev_return"]),
        "brent_eur_per_bbl_current": float(latest["brent_eur"]),
        "brent_eur_per_bbl_lag1": float(latest["brent_eur_lag1"]),
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
        r2=r2,
        n_train=len(df),
        coefficients=coefficients,
    )
