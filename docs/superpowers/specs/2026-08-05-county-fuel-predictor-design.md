# County Fuel Predictor

**Date:** 2026-08-05
**Status:** Approved (design), ready for implementation plan

## Problem

The dashboard forecasts a single national Irish pump price. Petrol in Wicklow
and petrol in Kerry differ by roughly 10 cents per litre, so a national number
is the wrong number for most drivers. The roadmap item reads "County-level
breakdown (FuelWatch exposes this)".

Deliver a second page, **County Fuel Predictor**, with a county dropdown and
the same class of charts and forecast cards as the national dashboard.

## What the upstream source actually exposes

Verified against the live FuelWatch Supabase backend on 2026-08-05 by
extracting the anon key from the SPA bundle (the mechanism
`fuelwatch_ie.py` already uses).

| Capability | Available |
| --- | --- |
| Current county median, petrol + diesel | Yes — RPC `county_price_rankings(window_days)` |
| Station counts behind each median | Yes — 3 to 57 per county |
| Individual station prices, names, brands | Yes — RPC `cheapest_reported_stations(window_days, per_fuel)` |
| **Any historical county value** | **No** |

`county_price_rankings` returns `fuel_type, county, median_price,
station_count`. There is **no date column and no archive**. It is a trailing
median over the requested window, recomputed on every call. The raw
`price_reports` table is RLS-locked and returns zero rows to the anon key, so
history cannot be reconstructed from source records either.

Prices are integers in tenths of a cent: `1794` means €1.794/L.

Coverage as a function of window:

| `window_days` | Counties with a median | Station-medians in pool |
| --- | --- | --- |
| 7 | 10 | 172 |
| 30 | 19 | 559 |
| 90 | 22 | 692 |
| 180+ | 24 | 931 (saturates) |

Leitrim and Monaghan never produce a median — they have stations in the
station-level RPC but too few to clear the ranking function's threshold. The
whole crowd-sourced pool is only about six months deep.

**Consequence:** no county target series exists, so no per-county model can be
trained. The design works around this rather than pretending otherwise.

## Scope

In scope:

- Daily ingest of county medians and station-level prices into new tables,
  starting the county history that does not currently exist anywhere.
- A basis decomposition that localises the national forecast per county.
- `GET /api/counties` and a `counties.json` static export.
- A new `frontend/county.html` page: county dropdown, price cards with
  sparklines, fill-up calculator, history chart, two prediction cards,
  cheapest-stations panel.
- Shared-helper extraction in both `frontend/` and `backend/app/data_sources/`
  to avoid duplicating code across the two pages and two fetchers.
- Unit tests for the basis math (the repo's first tests).

Out of scope:

- Training a per-county model. There is no data to train on. Revisit after
  roughly six months of accumulated snapshots.
- County-specific news, or a county backtest strip. A county backtest would
  replay national calls with an offset — accurate as a national record,
  misleading presented as a county track record.
- Changing the national model, its features, or the national page's forecast.
- Station-level forecasting or a map view.

## Approach

### Basis decomposition

County price is the national price plus a county-specific offset:

```
county_price(c, f) = national_price(f) + basis(c, f)
```

The basis must be computed within a single measurement process. The national
series is weekly EU Oil Bulletin data; the county medians are 30-day trailing
crowd-sourced retail. Subtracting one from the other would fold a
crowd-vs-bulletin method bias into every county offset — Donegal would look
six cents dear when part of that gap is just a different way of measuring.

So the national reference is derived from **the same RPC rows** the county
medians come from:

```
national_ref(f, w) = Σ(median_c × n_c) / Σ(n_c)        # station-weighted
basis_raw(c, f)    = median(c, f) − national_ref(f, w)
```

Because the reference is a weighted mean of the same rows, the weighted basis
sums to zero by construction, and the method bias cancels when the basis is
added to the Bulletin-derived national price.

Sanity check: the weighted mean of the 30-day petrol rows is €1.820 against
the app's own `get_average_prices` value of €1.819.

### Shrinkage

A median of three stations is noise. Shrink each basis toward zero in
proportion to sample size:

```
basis(c, f) = basis_raw(c, f) × n / (n + K),  K = 10
```

Dublin (n=57) retains 85% of its raw offset; Donegal (n=3) retains 23%. This
stops thin counties advertising a spurious six-cent gap.

`K = 10` is a prior, not a fitted value — it puts the half-shrinkage point at
ten stations, which is roughly the median county's sample size. Revisit once
enough snapshots exist to estimate between- and within-county variance.

### Forecast translation

```
county_pump_now   = national_pump_now   + basis
county_pump_next  = national_pump_next  + basis
county_pump_3w    = national_pump_3w    + basis
band_half_county  = √(band_half_national² + (σ_station / √n)²)
```

`σ_station = €0.045` is a prior for station-to-station dispersion within a
county, chosen to be comparable to the observed 10-cent spread *across*
county medians. Combining in quadrature treats national model error and
county sampling error as independent, which they are.

The trend label and predicted weekly return percentage are inherited from the
national prediction unchanged. A constant additive offset cannot change the
sign of a movement. The page states this rather than implying the county has
its own directional call.

### Window fallback

Ingest runs `county_price_rankings` at both `window_days=30` (primary,
freshest) and `window_days=180` (widest coverage), storing `window_days` on
each row. Read path prefers a 30-day row; where one is missing for a
county/fuel pair — Roscommon has diesel but no petrol at 30 days — it falls
back to the 180-day row and flags the result `stale: true`.

### Reconstructed history

With zero county history on day one, the chart plots the national series
shifted by the county's current basis, drawn dashed and labelled
**reconstructed**. Observed county snapshots overlay as points and accumulate
one per county per fuel per day from the first cron run.

The range selector defaults to 6 months rather than the homepage's full
history. Applying today's offset to 2005 prices is an assumption with no
supporting evidence, so the page does not lead with it, though the longer
ranges remain selectable.

Once roughly eight weeks of observations have banked, the observed line can
become primary and the reconstruction retired. That is a follow-up, not part
of this work.

## Data model

Added to `backend/app/db.py` `SCHEMA`. Both tables follow the existing
idempotent-upsert convention, so a same-day re-run rewrites rather than
duplicates.

```sql
CREATE TABLE IF NOT EXISTS county_prices (
    snapshot_date        DATE    NOT NULL,   -- date the ranking was pulled
    county               TEXT    NOT NULL,
    fuel_type            TEXT    NOT NULL,   -- 'petrol' | 'diesel'
    median_eur_per_litre REAL    NOT NULL,
    station_count        INTEGER NOT NULL,
    window_days          INTEGER NOT NULL,   -- trailing window the median covers
    source               TEXT    NOT NULL,
    inserted_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, county, fuel_type, window_days)
);
CREATE INDEX IF NOT EXISTS ix_county_prices_lookup
    ON county_prices(county, fuel_type, snapshot_date);

CREATE TABLE IF NOT EXISTS county_stations (
    snapshot_date        DATE    NOT NULL,
    station_id           TEXT    NOT NULL,
    fuel_type            TEXT    NOT NULL,
    name                 TEXT    NOT NULL,
    brand                TEXT,
    county               TEXT,
    price_eur_per_litre  REAL    NOT NULL,
    reported_at          TIMESTAMP,
    source               TEXT    NOT NULL,
    inserted_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, station_id, fuel_type)
);
CREATE INDEX IF NOT EXISTS ix_county_stations_county
    ON county_stations(county, fuel_type, snapshot_date);
```

No table stores the national reference. It is a pure function of
`county_prices` rows for a given snapshot date and window, so it is derived at
read time rather than denormalised.

## Components

### `backend/app/data_sources/fuelwatch_client.py` (new)

Extracted from `fuelwatch_ie.py`. Owns Supabase credential discovery plus thin
`rpc(name, args)` and `rest_get(path, params)` helpers.

Credential discovery currently downloads a 2.9 MB JS bundle on every call and
is private to `fuelwatch_ie.py`. With a second consumer that becomes two
downloads per ingest run. The extracted module caches the discovered
`(url, key)` pair at module level for the life of the process.

`fuelwatch_ie.py` is updated to call it; its existing behaviour is unchanged.

### `backend/app/data_sources/fuelwatch_counties.py` (new)

`ingest()` performs three upstream calls and upserts:

- `county_price_rankings(window_days=30)` → `county_prices`
- `county_price_rankings(window_days=180)` → `county_prices`
- `cheapest_reported_stations(window_days=30, per_fuel=200)` → `county_stations`
  (400 rows across 23 counties; `per_fuel` is a per-fuel row cap, not a
  per-county one, so the county filter is applied on our side)

Divides all upstream integer prices by 1000 to reach EUR/L. Returns a summary
dict matching the shape the other fetchers return.

### `backend/app/prediction/county.py` (new)

Pure functions over plain dicts — no database access, no network — so the
basis, shrinkage, and band arithmetic is unit-testable in isolation:

- `national_reference(rows) -> float`
- `raw_basis(median, national_ref) -> float`
- `shrink(basis, n, k=10) -> float`
- `widen_band(band_half_national, n, sigma_station=0.045) -> float`
- `build_county_prediction(national_prediction, county_row) -> dict`

A separate thin loader reads `county_prices` / `county_stations` and assembles
the API payload, keeping I/O out of the math module.

### `backend/app/routes/counties.py` (new)

`GET /api/counties`, optional `?county=Cork` filter. Response models added to
`models.py` alongside the existing ones:

- `CountyFuelSnapshot` — median, basis, station_count, window_days, stale,
  low_sample, plus the translated prediction fields
- `CountyStation` — name, brand, price, reported_at
- `CountyEntry` — county name, per-fuel snapshots, observed history, stations
- `CountiesResponse` — generated_at, national_ref per fuel, list of entries,
  notes

The response deliberately carries **no reconstructed series**. Twenty-four
counties by two fuels by 1200 national points would be a multi-megabyte
payload of data the client can compute itself. It ships the basis; the browser
adds it to the national series it already fetches from `prices.json`.

Observed history is capped at the most recent 180 days and restricted to
30-day-window rows, so the export does not carry both windows of the same
observation. Payload is roughly 15 KB on the first run and grows toward
400 KB once a full 180 days of snapshots have banked (24 counties × 2 fuels ×
180 days). If that becomes a problem the cap tightens; it is not one at the
sizes this page will see in the first few months.

Returns 503 with a clear message when no county rows exist yet, matching how
`/api/prediction` behaves before it has enough history.

### Wiring

- `ingest.py` — new `counties` choice, included in `all`
- `scheduler.py` — daily job at 06:10 UTC, ten minutes after the `fuelwatch`
  job that shares the same upstream
- `export_static.py` — writes `frontend/data/counties.json`, degrading to a
  stub on a non-200 exactly as `prediction.json` already does
- `.github/workflows/refresh.yml` — no change needed; it runs
  `ingest.py all --force`

## Front-end

### `frontend/shared.js` (new)

`fmtEur`, `fmtPct`, `fmtDate`, `fmtDMY`, `jget`, `escapeHtml`,
`renderSparkline`, and the Chart.js axis/legend theme constants, moved out of
`app.js` and loaded by both pages. Without this, `county.js` would open as a
150-line copy of `app.js`. `app.js` shrinks correspondingly and keeps its
current behaviour.

### `frontend/county.html` + `frontend/county.js` (new)

Sections, in order:

1. **Header and nav.** `Ireland | County` links, added to `index.html` too.
2. **County picker.** All 24 counties with medians; Leitrim and Monaghan
   rendered disabled with "no reports". Deep-linkable via
   `county.html?county=Cork`, remembered in `localStorage`, defaulting to
   Dublin as the largest sample.
3. **Price cards** for petrol and diesel with sparklines over the last twelve
   weeks of the reconstructed series.
4. **Fill-up calculator**, identical arithmetic to the homepage but against
   county prices.
5. **History chart.** Reconstructed line dashed and labelled; observed county
   snapshots as points. Range selector defaults to 6 months.
6. **Prediction cards** for both fuels: predicted pump next week, widened 50%
   band, three-week price, confidence, and the county offset shown explicitly
   as its own row. No backtest strip.
7. **Cheapest stations panel.** Top five per fuel for the selected county,
   with brand and report date.
8. **Low-sample banner** when `n < 8`: "Based on 3 stations — the county
   offset is shrunk toward the national average and the forecast band widened
   accordingly."

Switching county re-renders from data already in memory. No refetch.

### `frontend/style.css`

Additive only: `.site-nav`, `.county-picker`, `.station-list`, `.sample-warn`.
Existing rules untouched.

## Error handling

| Condition | Behaviour |
| --- | --- |
| Upstream RPC fails during ingest | Job logs and exits non-fatally, matching `_safe()` in `scheduler.py`; yesterday's rows remain the newest |
| No `county_prices` rows at all | `/api/counties` returns 503; `export_static.py` writes a stub; page shows an explanatory empty state |
| County has 30d data for one fuel only | Falls back to the 180d row, flagged `stale: true` and surfaced in the UI |
| County has no median at either window | Listed in the dropdown as disabled, "no reports" |
| County has a median but no station rows (23 of 24 counties are covered at 30 days) | Stations panel shows "no recent station reports"; everything else renders normally |
| `station_count < 8` | `low_sample: true`, banner shown, band already widened by the `1/√n` term |
| National prediction unavailable (503) | County page renders prices and chart, prediction cards show the national error message |
| `?county=` names an unknown county | Falls back to the default county, no error |

## Testing

The repository has no tests today and no test dependency. This adds `pytest`
to `backend/requirements.txt` and `backend/tests/test_county_basis.py`:

- `national_reference` matches a hand-computed weighted mean
- weighted basis across all counties sums to approximately zero
- `shrink` is monotone in `n` and returns zero at `n = 0`
- `widen_band` decreases as `n` grows and always exceeds the national band
- `build_county_prediction` preserves the national trend label and weekly
  return
- missing-fuel input falls back to the wide window and sets `stale`
- unknown county raises a clean, typed error

All pure functions. No network, no database, no fixtures.

Manual verification: run `python ingest.py counties`, confirm row counts, run
`export_static.py`, load `county.html`, and check that switching counties
moves the price cards and chart by the reported offset and nothing else.

## Limitations, stated in the product

These go in the page copy and the README, not a footnote:

- County medians are 30-day trailing crowd-sourced retail prices. The national
  line is weekly EU Oil Bulletin data. They are different measurements and the
  page localises the **level**, not the movement.
- County direction is national direction. There is no county-specific
  directional signal, and the page does not claim one.
- The historical county line is reconstructed until enough real snapshots
  accumulate.
- Two counties, Leitrim and Monaghan, have no median at any window.

## README changes

- Data sources table: add the two county RPCs.
- Project structure: `fuelwatch_client.py`, `fuelwatch_counties.py`,
  `prediction/county.py`, `routes/counties.py`, `county.html`, `county.js`,
  `shared.js`.
- API table: `GET /api/counties`.
- Ingest CLI: `python ingest.py counties`.
- Roadmap: tick "County-level breakdown"; add "per-county model once six
  months of snapshots have accumulated" as a new unticked item.

## Follow-ups, explicitly deferred

- Retire the reconstructed line once around eight weeks of observations exist.
- Estimate `K` and `σ_station` from accumulated data instead of using priors.
- A genuine per-county model once there is a county target series.
- County choropleth map.
- Alerting when a county's basis shifts sharply, which would be the first real
  county-specific signal.
