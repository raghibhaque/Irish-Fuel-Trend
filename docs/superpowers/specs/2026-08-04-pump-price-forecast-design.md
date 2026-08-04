# Pump-price level forecast

**Date:** 2026-08-04
**Status:** Approved (design), ready for implementation plan

## Problem

Current model (`backend/app/prediction/model.py`) outputs a weekly wholesale
return (%) and a `up/down/flat` trend label. Users want to see the actual
future pump price in EUR/L (e.g. "1.500 today, ~1.520 next week, ~1.560 in
4 weeks"), with an uncertainty band that widens with horizon.

## Scope

- Extend the existing prediction pipeline with an N-week-ahead **level**
  forecast (EUR/L pump price).
- Emit an uncertainty band (~80% confidence) that grows with horizon.
- Expose via a new HTTP endpoint.
- Render as a dashed forecast line + shaded band tacked onto the existing
  price chart, with a horizon slider.

Out of scope:
- Retraining the underlying model or changing its features.
- Multi-horizon direct models (one model per week offset).
- Forecasting Brent or FX forward. We hold current-week feature values.

## Approach

**Method:** compound the single-step weekly return.

```
r        = TrendPrediction.predicted_weekly_return
wholesale_k = wholesale_now * (1 + r)^k                # k = 1..N
pump_k      = wholesale_k * (1 + VAT) + tax_per_L * (1 + VAT)
```

where `VAT = 0.23` (IE standard rate) and `tax_per_L` is backed out from the
latest observed row so we don't hard-code excise/carbon/NORA:

```
tax_per_L = pump_now / (1 + VAT) - wholesale_now
```

**Uncertainty band:**

```
sigma          = sqrt(mean((y_hat - y)^2))    # residual std on training set
band_multiplier= z * sigma * sqrt(k)          # random-walk-style widening
pump_lower_k   = pump_k * (1 - band_multiplier)
pump_upper_k   = pump_k * (1 + band_multiplier)
```

`z = 1.28` for a ~80% two-sided band. Configurable via constant.

**Horizon:** user-selectable in the UI. Server clamps to `[1, 12]` weeks.

## Data model (`backend/app/models.py`)

```python
class ForecastPoint(BaseModel):
    week_offset: int            # 1..N
    date: date                  # as_of + 7*k
    pump_eur_per_l: float
    pump_lower: float
    pump_upper: float
    wholesale_eur_per_l: float

class ForecastResponse(BaseModel):
    fuel_type: str              # "petrol" | "diesel"
    as_of: date                 # last observed price date
    horizon_weeks: int
    current_pump_eur_per_l: float
    current_wholesale_eur_per_l: float
    weekly_return: float        # r used for compounding
    points: list[ForecastPoint]
    band_z: float
    band_confidence: float      # e.g. 0.80
    notes: list[str] = []       # e.g. mock-Brent warning
```

## New module: `backend/app/prediction/forecast.py`

Public API:

```python
def forecast_levels(fuel_type: str, horizon_weeks: int) -> ForecastResponse
```

Implementation:
1. Call `model.train_and_predict(fuel_type)` — reuse trained model.
2. Read `latest_wholesale_eur_per_l` from `pred.features`, and current pump
   price from the latest `fuel_prices` row (same query pattern as
   `_load_frames` — add a helper if cleaner).
3. Compute residual std by re-loading the training frame via
   `model.build_dataset(fuel_type)`, refitting or reusing coefficients from
   `pred.coefficients` to get `y_hat`, then `sqrt(mean((y - y_hat)^2))`.
   To avoid duplicating training, extend `TrendPrediction` with
   `residual_std: float` set inside `train_and_predict`. Cleaner.
4. Build N points via the formulas above.
5. Attach the mock-Brent note if `brent_source` looks synthetic (mirror
   logic in `routes/prediction.py::get_prediction`; extract shared helper).

## API route

Add to existing `backend/app/routes/prediction.py`:

```
GET /api/forecast?fuel=petrol&weeks=4  -> ForecastResponse
```

- `fuel` required, one of `petrol|diesel`.
- `weeks` optional, default 4, clamped `[1, 12]`.
- 503 if `train_and_predict` raises `RuntimeError` (insufficient history).
- 422 if `fuel` invalid (FastAPI validation via `Literal`).

Frontend calls this endpoint twice per horizon change (petrol + diesel).

## Frontend (`frontend/`)

**`index.html`:**
- Add a range input near the existing `#chart-range`:
  ```html
  <label>Forecast horizon:
    <input type="range" id="forecast-weeks" min="1" max="12" value="4">
    <span id="forecast-weeks-label">4 weeks</span>
  </label>
  ```

**`app.js`:**
- New `loadForecast(weeks)` that fetches both fuels in parallel.
- Extend `renderChart` to accept optional forecast objects. For each fuel,
  add two datasets:
  - Forecast line: same color, `borderDash: [6, 4]`, `pointRadius: 0`.
  - Band: two datasets (upper + lower) with `fill: '-1'` OR a single
    dataset with `fill: {target: 'other-dataset-index'}`. Use a very light
    alpha of the fuel color.
- Historical arrays padded with `null` for forecast dates so lines align.
- Slider event triggers `loadForecast(newN)` and re-renders (no full
  reload of history).
- Legend shows band label: `"Petrol forecast (±${confidence*100}%)"`.

## Error handling

- Not enough history → 503; UI hides forecast layer + shows one-line note
  under the chart.
- Slider `weeks` outside `[1,12]` → server clamps; UI also clamps.
- Missing `wholesale` price for latest row → skip that fuel's forecast,
  show note.

## Testing

`backend/tests/test_forecast_math.py`
- Given a fake `TrendPrediction(predicted_weekly_return=0.02,
  residual_std=0.01)`, latest pump=1.500, wholesale=0.600, VAT=0.23:
  - `pump_1w`, `pump_4w` match closed-form computation exactly.
  - Bands widen: `band_2w > band_1w` and monotonic through horizon.
  - `pump_lower < pump < pump_upper` for every k.

`backend/tests/test_forecast_route.py`
- `GET /api/forecast?fuel=petrol&weeks=4` returns 200 with N=4 points,
  strictly monotonic dates 7 days apart.
- `weeks=0` and `weeks=99` both clamped and return valid response.
- `fuel=nonsense` returns 422.

Use existing test conftest / fixtures if present. Otherwise seed a small
in-memory SQLite with a synthetic price + Brent + FX history.

## Files touched

1. `backend/app/prediction/model.py` — add `residual_std` to
   `TrendPrediction`, compute in `train_and_predict`.
2. `backend/app/prediction/forecast.py` — new module.
3. `backend/app/models.py` — add `ForecastPoint`, `ForecastResponse`.
4. `backend/app/routes/prediction.py` — add `/api/forecast` handler.
5. `frontend/index.html` — horizon slider markup.
6. `frontend/app.js` — fetch + render forecast overlay.
7. `frontend/style.css` — minor: slider styling if needed.
8. `backend/tests/test_forecast_math.py` — new.
9. `backend/tests/test_forecast_route.py` — new.

## Non-goals / follow-ups

- Backtesting the level forecast against held-out weeks (would strengthen
  band calibration; separate task).
- Tax-calendar-aware forecast (if a known excise change lands inside the
  horizon, adjust `tax_per_L` for weeks past that date). Tempting but out
  of scope for this iteration.
