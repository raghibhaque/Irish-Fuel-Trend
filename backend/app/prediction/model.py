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

Regressor: equal-weight ensemble of Ridge (alpha=1.0) + shallow RandomForest
+ shallow GradientBoosting. Ridge captures the linear pass-through from
crude/product returns; the two tree learners pick up small non-linear
interactions (e.g. large-Brent-move weeks) that Ridge cannot represent. The
three are averaged rather than stacked to keep the blend robust on the ~500
weekly rows available — a stacker would overfit.

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
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import TimeSeriesSplit

from app.db import connection

FLAT_BAND = 0.005  # ±0.5% weekly wholesale return = "flat"

FEATURE_COLS = [
    "brent_eur_ret_2w",
    "brent_eur_ret_6w",
    "product_eur_ret_1w",
    "product_eur_ret_4w",
    "crack_spread_ret_4w",    # 4-week change in refining margin (RBOB/ULSD − Brent, EUR/bbl)
    "prev_return",
]

GAL_PER_BBL = 42.0  # US barrel conversion for aligning refined-product prices with Brent

# Irish VAT on road fuels. Duties + carbon + NORA levy are fixed per litre, so
# a Δ in wholesale flows through to pump as Δwholesale * (1 + VAT).
VAT_RATE_IE = 0.23

# Interquartile band width for the price prediction interval (~50% likelihood).
# 0.6745 = Φ⁻¹(0.75) under the Normal residual assumption.
BAND_Z_50PCT = 0.6745

# fuel -> refined product symbol used as its downstream proxy
FUEL_PRODUCT = {"petrol": "RBOB", "diesel": "ULSD"}

CV_SPLITS = 5

# Embargo between train and test folds in walk-forward CV. The longest raw
# window inside the feature stack is the 6-week Brent return (built from
# `brent[t-1]` and `brent[t-7]`), so successive train/test rows share
# overlapping underlying data. `gap=6` shifts each test fold six weeks
# beyond the last training row, guaranteeing no shared source data between
# the newest training feature window and the first test target.
CV_GAP = 6

# Backtest split. The 52-week expanding-window backtest is used for both the
# residual std (widens/narrows the price band) and the empirical calibration
# ratio (scales confidence to match observed hit-rate). Feeding the same rows
# into both couples the two knobs; splitting the backtest in half decouples
# them — earlier weeks size the band, later weeks calibrate confidence.
CALIBRATION_SPLIT = 26

# Random seed for the tree ensemble components — fixed so training is
# deterministic across the daily refresh (a shifting forecast for the same
# input would be indistinguishable from real signal by the user).
ENSEMBLE_SEED = 0


def _new_ensemble(alpha: float = 1.0) -> list:
    """Factory for the three-model ensemble used everywhere in this module.

    Ridge:   linear baseline; regularises the correlated multi-lag features.
    RForest: shallow trees; picks up threshold effects (e.g. crack-spread
             flips) that a linear model cannot see. Depth capped to prevent
             the ~500-row training set from being memorised.
    GBoost:  additive, similarly shallow. Slower to overfit than a single
             deep tree; complements RForest by learning residuals.
    """
    return [
        Ridge(alpha=alpha),
        RandomForestRegressor(
            n_estimators=150,
            max_depth=4,
            min_samples_leaf=3,
            random_state=ENSEMBLE_SEED,
            n_jobs=1,
        ),
        GradientBoostingRegressor(
            n_estimators=120,
            max_depth=3,
            learning_rate=0.05,
            random_state=ENSEMBLE_SEED,
        ),
    ]


def _ensemble_fit_predict(X_tr: np.ndarray, y_tr: np.ndarray, X_te: np.ndarray) -> np.ndarray:
    """Fit each ensemble member on `(X_tr, y_tr)` and return the mean prediction on `X_te`.

    Equal weighting keeps the blend robust when one component's fold R² is
    unlucky — inverse-error weighting was tried and was worse in walk-forward
    testing because the per-fold error is itself high-variance on this dataset.
    """
    preds = np.zeros(len(X_te))
    for m in _new_ensemble():
        m.fit(X_tr, y_tr)
        preds = preds + m.predict(X_te)
    return preds / 3.0

# How many most-recent weeks to backtest with expanding-window one-step-ahead
# predictions. 52 gives the frontend a full year to compute direction hit-rate
# over any window (8w / 26w / 52w) client-side; the existing "recent calls"
# strip still slices the last 8 for its LED grid.
BACKTEST_WEEKS = 52


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
    current_pump_eur_per_l: float
    predicted_pump_eur_per_l: float
    predicted_pump_low_eur_per_l: float   # 50% interquartile band lower bound
    predicted_pump_high_eur_per_l: float  # 50% interquartile band upper bound
    predicted_pump_3w_eur_per_l: float    # ~3-week horizon, compounded weekly return
    backtest: list                        # list[dict] of recent one-step-ahead calls vs actual


# Reject the freshest daily row when it deviates from the trailing week's
# median by more than this. Real Irish pump moves rarely exceed ~2%/week —
# 5% is well outside plausible signal and characteristic of a bad crowd
# report or a source glitch anchoring the whole site's headline price.
PUMP_SPIKE_THRESHOLD = 0.05


def _spike_guarded_price(
    latest: float,
    trailing: list[float],
    threshold: float = PUMP_SPIKE_THRESHOLD,
) -> float:
    """Return `latest` unless it deviates from the trailing median by more than
    `threshold`, in which case return the trailing median.

    Split out so it can be tested without touching the database.
    """
    # Fewer than 4 prior points is not enough context to call the latest an
    # outlier — accept it and move on rather than pretending certainty.
    if len(trailing) < 4:
        return latest
    prior_median = float(np.median(trailing))
    if prior_median <= 0:
        return latest
    if abs(latest - prior_median) / prior_median > threshold:
        return prior_median
    return latest


def _latest_observed_pump(fuel_type: str) -> float | None:
    """Freshest observed pump price, whatever source produced it.

    The model can only *train* on rows carrying a wholesale price, i.e. EU
    Bulletin weeks. But the newest row in `fuel_prices` is usually a FuelWatch
    daily, up to a week fresher, and that is the number the dashboard
    headlines. Anchoring the forecast here keeps a single current price across
    the whole site; the predicted movement is unaffected because every delta
    below is derived from wholesale, not from the anchor.

    Spike guard: the freshest row is discarded in favour of the trailing
    median when it deviates by more than PUMP_SPIKE_THRESHOLD. A single
    outlier crowd report otherwise anchors every card and county for a day.
    """
    with connection() as conn:
        rows = conn.execute(
            "SELECT price_eur_per_litre FROM fuel_prices "
            "WHERE country='IE' AND fuel_type=? ORDER BY date DESC LIMIT 8",
            (fuel_type,),
        ).fetchall()
    if not rows:
        return None
    latest = float(rows[0]["price_eur_per_litre"])
    trailing = [float(r["price_eur_per_litre"]) for r in rows[1:]]
    return _spike_guarded_price(latest, trailing)


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

    fuel_rows = (
        prices[prices["fuel_type"] == fuel_type]
        .dropna(subset=["price_wo_tax_eur_per_litre"])
        .set_index("date")
        .sort_index()
    )
    price_series = fuel_rows["price_wo_tax_eur_per_litre"]
    pump_series  = fuel_rows["price_eur_per_litre"]

    df = pd.DataFrame({"wholesale": price_series, "pump": pump_series})
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
        df["crack_spread_eur"] = np.nan
        df["crack_spread_ret_4w"] = np.nan
    else:
        prod_eur_weekly = prod_eur_daily.reindex(df.index, method="ffill")
        df["product_eur"]        = prod_eur_weekly
        df["product_eur_lag1"]   = prod_eur_weekly.shift(1)
        df["product_eur_ret_1w"] = df["product_eur_lag1"] / prod_eur_weekly.shift(2) - 1
        df["product_eur_ret_4w"] = df["product_eur_lag1"] / prod_eur_weekly.shift(5) - 1

        # Crack spread: refined product minus crude on a common bbl basis.
        # Positive spread = refiners earning margin. Feature uses lagged
        # values so nothing leaks from the current week's data.
        product_eur_per_bbl = prod_eur_weekly * GAL_PER_BBL
        crack_daily = (product_eur_per_bbl - brent_eur_weekly).dropna()
        df["crack_spread_eur"]      = crack_daily.shift(1)
        df["crack_spread_ret_4w"]   = crack_daily.shift(1) - crack_daily.shift(5)

    keep = ["wholesale", "pump", "target_ret", "brent_eur", "brent_eur_lag1",
            "product_eur", "product_eur_lag1", "crack_spread_eur"] + FEATURE_COLS
    return df[keep].dropna()


def _r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """R² with the sklearn convention (can go negative for poor fits)."""
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - float(np.mean(y_true))) ** 2))
    if ss_tot <= 0:
        return 0.0
    return 1.0 - ss_res / ss_tot


def _walk_forward_r2(X: np.ndarray, y: np.ndarray) -> float:
    """Mean out-of-sample R² of the ensemble across TimeSeriesSplit folds.

    Residual std is *not* returned here — it is derived downstream from the
    52-week expanding-window backtest instead, which reflects recent forecast
    error rather than pooling all-time (calm periods deflate σ; crisis periods
    inflate it, and neither is representative of today).
    """
    n = len(y)
    n_splits = min(CV_SPLITS, max(2, n // 20))
    if n_splits < 2 or n < 20:
        return 0.0
    tscv = TimeSeriesSplit(n_splits=n_splits, gap=CV_GAP)
    scores: list[float] = []
    for train_idx, test_idx in tscv.split(X):
        if len(train_idx) < 10 or len(test_idx) < 2:
            continue
        preds = _ensemble_fit_predict(X[train_idx], y[train_idx], X[test_idx])
        # Do not clip a negative score — that IS the honest signal.
        scores.append(_r2(y[test_idx], preds))
    return float(np.mean(scores)) if scores else 0.0


def _direction_probability(predicted_ret: float, resid_std: float, r2_cv: float) -> float:
    """P(actual return has same sign as prediction) under a Normal residual model.

    Formula: Φ(|ŷ| / σ) where σ is the recent-backtest residual std. If the
    model has zero OOS skill (r2_cv ≤ 0) the |ŷ| signal is untrustworthy, so
    we shrink toward 0.5. For positive r2 we ramp toward full strength
    exponentially — a model with r2_cv=0.2 has demonstrable directional edge
    and the old linear ramp (0.5 + 0.5·r2) was throwing away real skill.
    Empirical calibration in the caller can further correct any residual bias.
    """
    z = abs(predicted_ret) / resid_std
    p_raw = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))  # Φ(z)
    skill = max(0.0, r2_cv)
    # Exponential ramp: skill=0 → 0 multiplier, skill=0.2 → 0.865, skill=0.5 → 0.993.
    skill_weight = 1.0 - math.exp(-10.0 * skill)
    p = 0.5 + (p_raw - 0.5) * skill_weight
    return max(0.5, min(0.999, p))


def _empirical_calibration(
    backtest: list[dict],
    resid_std: float,
    r2_cv: float,
) -> float:
    """Ratio to multiply (confidence − 0.5) by, based on the 52-week backtest.

    We compute the formula's implied confidence for every backtest week and
    compare the average to the observed direction hit rate. If the model was
    62% correct but the formula only claimed 55%, we scale the distance from
    coin-flip by 12/5 = 2.4× so today's confidence reflects the model's
    demonstrated calibration. Bidirectional (also shrinks over-confidence).
    Shrunk toward 1.0 (no adjustment) by evidence weight sqrt(n)/(sqrt(n)+sqrt(20))
    to guard against the 52 samples being unrepresentative.
    """
    if not backtest:
        return 1.0
    formula_confs: list[float] = []
    hits = 0
    for b in backtest:
        pred = float(b.get("predicted_return", 0.0))
        actual = float(b.get("actual_return", 0.0))
        formula_confs.append(_direction_probability(pred, resid_std, r2_cv))
        if (pred >= 0) == (actual >= 0):
            hits += 1
    n = len(backtest)
    empirical = hits / n
    formula_avg = sum(formula_confs) / n
    formula_delta = max(formula_avg - 0.5, 1e-4)
    empirical_delta = max(empirical - 0.5, 0.0)
    raw_ratio = empirical_delta / formula_delta
    # Cap the ratio at 2.0: a single week where |ŷ| is unusually large plus
    # a favourable backtest can otherwise combine to produce implausible
    # >95% confidence for a weekly retail-fuel forecast.
    raw_ratio = min(raw_ratio, 2.0)
    # Weight by evidence: 52 backtest weeks gives w ≈ 0.62 toward empirical.
    w = math.sqrt(n) / (math.sqrt(n) + math.sqrt(20))
    return 1.0 + (raw_ratio - 1.0) * w


def _expanding_backtest(df: pd.DataFrame, weeks: int) -> list[dict]:
    """One-step-ahead expanding-window ensemble predictions for the last `weeks` rows.

    For each of the last `weeks` weeks, fit the ensemble on all data up to
    (but excluding) that week, predict, and record predicted vs actual return
    plus the implied pump price shift. Gives an honest "here's what the model
    would have said in real time" strip for the UI, and its residuals feed
    both `resid_std` and the empirical confidence calibration downstream.
    """
    X_all = df[FEATURE_COLS].values
    y_all = df["target_ret"].values
    wholesale_all = df["wholesale"].values
    pump_all = df["pump"].values
    dates = list(df.index)
    n = len(df)
    start = max(30, n - weeks)  # ensure at least 30 training rows for the first call
    out: list[dict] = []
    for i in range(start, n):
        pred = float(_ensemble_fit_predict(X_all[:i], y_all[:i], X_all[i:i+1])[0])
        actual = float(y_all[i])
        # Pump-price movement implied by the prediction for week i, using the
        # wholesale price at week i-1 (the info the model would have had).
        base_wholesale = float(wholesale_all[i-1])
        prev_pump      = float(pump_all[i-1])
        actual_pump    = float(pump_all[i])
        predicted_pump = prev_pump + base_wholesale * pred * (1.0 + VAT_RATE_IE)
        direction_hit  = (pred >= 0) == (actual >= 0)
        d = dates[i]
        out.append({
            "date": d.date().isoformat() if hasattr(d, "date") else str(d),
            "actual_return": actual,
            "predicted_return": pred,
            "actual_pump_eur_per_l": actual_pump,
            "predicted_pump_eur_per_l": predicted_pump,
            "direction_correct": bool(direction_hit),
        })
    return out


def train_and_predict(fuel_type: str) -> TrendPrediction:
    df = build_dataset(fuel_type)
    if len(df) < 30:
        raise RuntimeError(f"Not enough rows to train ({len(df)}). Need 30+.")

    X = df[FEATURE_COLS].values
    y = df["target_ret"].values

    backtest = _expanding_backtest(df, BACKTEST_WEEKS)

    # Honest accuracy first — walk-forward CV on the same feature matrix.
    r2_cv = _walk_forward_r2(X, y)

    # Residual std and empirical calibration must come from *disjoint* slices
    # of the backtest — using the same rows for both couples the band width
    # and confidence-scaling knobs and lets one lucky window inflate both.
    # Split at `CALIBRATION_SPLIT`: earlier weeks size the residual std,
    # later weeks feed the empirical calibration below. Both slices still
    # reflect recent (last-year) behaviour, not pooled all-history.
    if backtest and len(backtest) > CALIBRATION_SPLIT:
        std_slice = backtest[:-CALIBRATION_SPLIT]
        calib_slice = backtest[-CALIBRATION_SPLIT:]
    else:
        # Not enough backtest to split — fall back to using the whole window
        # for the std and skipping empirical calibration downstream.
        std_slice = backtest
        calib_slice = []

    if std_slice:
        bt_resids = np.array(
            [b["actual_return"] - b["predicted_return"] for b in std_slice]
        )
        resid_std = float(np.std(bt_resids)) or float(np.std(y)) or 1e-6
    else:
        resid_std = float(np.std(y)) or 1e-6
    resid_std = max(resid_std, 1e-6)

    # Fit final ensemble on all data for the forward prediction. Ridge is kept
    # as a named member so its coefficients (interpretable, unlike RF/GB) can
    # still be surfaced downstream for the explanation string.
    members = _new_ensemble()
    for m in members:
        m.fit(X, y)
    ridge_model = next(m for m in members if isinstance(m, Ridge))
    in_sample_preds = np.mean(np.stack([m.predict(X) for m in members]), axis=0)
    r2_in_sample = _r2(y, in_sample_preds)

    latest = df.iloc[-1]
    x_next = latest[FEATURE_COLS].values.reshape(1, -1)
    predicted_ret = float(np.mean([m.predict(x_next)[0] for m in members]))

    if predicted_ret > FLAT_BAND:
        trend = "up"
    elif predicted_ret < -FLAT_BAND:
        trend = "down"
    else:
        trend = "flat"

    # Calibrated probability that the actual return has the same sign as the
    # prediction, given the OOS residual std and demonstrated OOS skill.
    # Naturally high when |prediction| is large vs typical error, low when not.
    raw_conf = _direction_probability(predicted_ret, resid_std, r2_cv)
    # Empirical recalibration against the held-out calibration slice (the
    # more recent half of the backtest): if the formula was systematically
    # under- or over-confident against actual hit rate, scale the distance
    # from coin-flip so today's number matches demonstrated skill.
    cal_ratio = _empirical_calibration(calib_slice, resid_std, r2_cv)
    # 0.9 is the honest ceiling — even a well-calibrated weekly retail-fuel
    # forecast should not claim near-certainty; unmodelled shocks (Middle East
    # flare-ups, refinery outages, budget-night duty changes) put a hard cap
    # on how confident any purely macro model can be.
    confidence = max(0.5, min(0.9, 0.5 + (raw_conf - 0.5) * cal_ratio))

    # Price prediction: pump Δ = wholesale × Δreturn × (1 + VAT), since Irish
    # duties + carbon + NORA levy are fixed per litre and VAT applies to the
    # whole stack. 50% band uses the OOS residual std scaled by Φ⁻¹(0.75).
    current_wholesale = float(latest["wholesale"])
    # Anchor on the freshest observed pump price rather than the last row the
    # model could train on — see _latest_observed_pump. Falls back to the
    # training row if fuel_prices is somehow empty.
    current_pump      = _latest_observed_pump(fuel_type) or float(latest["pump"])
    pump_multiplier   = 1.0 + VAT_RATE_IE
    delta_pump        = current_wholesale * predicted_ret * pump_multiplier
    band_half         = current_wholesale * resid_std * BAND_Z_50PCT * pump_multiplier
    predicted_pump    = current_pump + delta_pump
    pump_low          = predicted_pump - band_half
    pump_high         = predicted_pump + band_half
    # 3-week horizon assumes the same weekly return compounds. Rough because
    # the model isn't retrained forward — this is signposting, not a forecast.
    delta_pump_3w     = current_wholesale * ((1.0 + predicted_ret) ** 3 - 1.0) * pump_multiplier
    predicted_pump_3w = current_pump + delta_pump_3w

    features = {
        "brent_eur_ret_2w": float(latest["brent_eur_ret_2w"]),
        "brent_eur_ret_6w": float(latest["brent_eur_ret_6w"]),
        "product_eur_ret_1w": float(latest["product_eur_ret_1w"]),
        "product_eur_ret_4w": float(latest["product_eur_ret_4w"]),
        "crack_spread_eur": float(latest["crack_spread_eur"]),
        "crack_spread_ret_4w": float(latest["crack_spread_ret_4w"]),
        "prev_wholesale_return": float(latest["prev_return"]),
        "brent_eur_per_bbl_current": float(latest["brent_eur"]),
        "brent_eur_per_bbl_lag1": float(latest["brent_eur_lag1"]),
        "product_eur_per_gal_current": float(latest["product_eur"]),
        "latest_wholesale_eur_per_l": float(latest["wholesale"]),
    }

    # Coefficients reflect the Ridge member only — surfaced for the
    # explanation string, which reads feature signs to describe direction.
    # RF/GB contribute to the numeric prediction but do not expose signed
    # linear weights, so they are intentionally omitted here.
    coefficients = {name: float(coef) for name, coef in zip(FEATURE_COLS, ridge_model.coef_)}
    coefficients["_intercept"] = float(ridge_model.intercept_)

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
        current_pump_eur_per_l=current_pump,
        predicted_pump_eur_per_l=predicted_pump,
        predicted_pump_low_eur_per_l=pump_low,
        predicted_pump_high_eur_per_l=pump_high,
        predicted_pump_3w_eur_per_l=predicted_pump_3w,
        backtest=backtest,
    )
