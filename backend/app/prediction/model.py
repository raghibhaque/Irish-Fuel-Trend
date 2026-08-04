"""Trend prediction model.

v1 approach: weekly OLS regression of pump-price weekly return on lagged Brent
and lagged EUR/USD returns. The predicted next-week return is bucketed into a
trend label: up / down / flat.

Intentionally simple. The value is the end-to-end pipeline + explanation
layer, not model sophistication.

NOTE: brent_crude is currently MOCK, so predictions are structurally correct
but not economically meaningful yet. Swap in real Brent data (see TODO in
data_sources/brent_crude.py) and the exact same code will produce real
predictions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sklearn.linear_model import LinearRegression

from app.db import connection

# Threshold on predicted weekly return to classify trend
FLAT_BAND = 0.005   # ±0.5% weekly = "flat"

BRENT_LAG_WEEKS = 2   # crude typically flows through to pump price in 1–3 weeks
FX_LAG_WEEKS    = 2


@dataclass
class TrendPrediction:
    fuel_type: str
    as_of: date
    trend: str                     # 'up' | 'down' | 'flat'
    predicted_weekly_return: float # decimal, e.g. 0.012 = +1.2%
    confidence: float              # 0..1, based on |return| / typical stdev
    features: dict                 # snapshot of feature values used
    r2: float                      # in-sample R^2 of training fit
    n_train: int                   # training row count


def _load_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with connection() as conn:
        prices = pd.read_sql_query(
            "SELECT date, fuel_type, price_eur_per_litre FROM fuel_prices "
            "WHERE country='IE' ORDER BY date",
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


def _weekly_fx(fx: pd.DataFrame, sample_dates: pd.DatetimeIndex) -> pd.Series:
    """For each sample date, return the most recent fx ≤ that date (asof join)."""
    fx_sorted = fx.sort_values("date").set_index("date")["eur_usd"]
    return fx_sorted.reindex(sample_dates, method="ffill")


def build_dataset(fuel_type: str) -> pd.DataFrame:
    """Return one row per week with target + feature columns."""
    prices, brent, fx = _load_frames()

    px = (
        prices[prices["fuel_type"] == fuel_type]
        .set_index("date")["price_eur_per_litre"]
        .sort_index()
    )
    br = brent.set_index("date")["price_usd_per_barrel"].sort_index()

    df = pd.DataFrame({"price": px})
    df["price_prev"] = df["price"].shift(1)
    df["target_ret"] = df["price"] / df["price_prev"] - 1  # this-week's return

    # Align Brent to the fuel_prices weekly index using asof (nearest ≤ date)
    br_asof = br.reindex(df.index, method="ffill")
    df["brent"]        = br_asof
    df["brent_lag1"]   = br_asof.shift(1)
    df["brent_lag2"]   = br_asof.shift(2)
    df["brent_ret_2w"] = df["brent_lag1"] / df["brent_lag2"] - 1

    fx_asof = _weekly_fx(fx, df.index)
    df["eur_usd"]        = fx_asof
    df["eur_usd_lag1"]   = fx_asof.shift(1)
    df["eur_usd_lag2"]   = fx_asof.shift(2)
    df["eur_usd_ret_2w"] = df["eur_usd_lag1"] / df["eur_usd_lag2"] - 1

    return df.dropna(subset=["target_ret", "brent_ret_2w", "eur_usd_ret_2w"])


def train_and_predict(fuel_type: str) -> TrendPrediction:
    df = build_dataset(fuel_type)
    if len(df) < 20:
        raise RuntimeError(
            f"Not enough rows to train ({len(df)}). Need at least 20 weeks."
        )

    feature_cols = ["brent_ret_2w", "eur_usd_ret_2w"]
    X = df[feature_cols].values
    y = df["target_ret"].values
    model = LinearRegression()
    model.fit(X, y)
    r2 = float(model.score(X, y))

    # Predict for the most recent row's features
    latest = df.iloc[-1]
    x_next = latest[feature_cols].values.reshape(1, -1)
    predicted_ret = float(model.predict(x_next)[0])

    # Trend bucket
    if predicted_ret > FLAT_BAND:
        trend = "up"
    elif predicted_ret < -FLAT_BAND:
        trend = "down"
    else:
        trend = "flat"

    # Confidence: |predicted return| relative to historical stdev of weekly returns
    std_ret = float(df["target_ret"].std()) or 1e-6
    confidence = min(1.0, abs(predicted_ret) / (2 * std_ret))

    features = {
        "brent_ret_2w": float(latest["brent_ret_2w"]),
        "eur_usd_ret_2w": float(latest["eur_usd_ret_2w"]),
        "brent_lag1_usd_per_bbl": float(latest["brent_lag1"]),
        "brent_lag2_usd_per_bbl": float(latest["brent_lag2"]),
        "eur_usd_lag1": float(latest["eur_usd_lag1"]),
        "eur_usd_lag2": float(latest["eur_usd_lag2"]),
        "latest_price_eur_per_l": float(latest["price"]),
    }

    return TrendPrediction(
        fuel_type=fuel_type,
        as_of=latest.name.date() if hasattr(latest.name, "date") else latest.name,
        trend=trend,
        predicted_weekly_return=predicted_ret,
        confidence=confidence,
        features=features,
        r2=r2,
        n_train=len(df),
    )
