# Irish Fuel Trend

A full-stack forecasting dashboard for Irish petrol and diesel pump prices.
Ingests authoritative and crowd-sourced Irish price feeds, joins them with
Brent crude and EUR/USD, trains a lightweight regression model, and serves
a human-readable directional forecast alongside recent oil-relevant news.

The dashboard runs **two ways from the same codebase**:

- Locally as a FastAPI application (`uvicorn`) with a live API.
- Statically on **GitHub Pages** — a scheduled GitHub Actions workflow refreshes
  the data daily, dumps it to JSON, and redeploys the front-end.

---

## Highlights

- **Multi-source ingestion** — reconciles the EU Weekly Oil Bulletin (weekly,
  authoritative), FuelWatch.ie (daily, crowd-sourced), the ECB EUR/USD reference
  feed, and Brent futures from Yahoo Finance.
- **Automatic reverse-engineered scrape** — the FuelWatch source auto-discovers
  the Supabase URL and anon JWT from the SPA's JavaScript bundle each run, so a
  rotated key or a new bundle hash doesn't break ingestion.
- **Layered data model** — daily crowd-sourced rows only backfill dates newer
  than the latest authoritative EU bulletin, preserving the wholesale price
  history the model trains on.
- **Regression with explanation** — a small `scikit-learn` model produces a
  weekly return forecast and confidence, plus a plain-English "why" that names
  the biggest driver (Brent, FX, or momentum).
- **Two deployment shapes, one codebase** — the same `frontend/` bundle reads
  `data/*.json` snapshots. Under `uvicorn` those snapshots are refreshed by an
  APScheduler job; under GitHub Pages they're refreshed by an Actions cron.
- **Idempotent upserts** — every fetcher uses `ON CONFLICT DO UPDATE`, so a
  re-run is safe and only writes changed rows.
- **County localisation without a county model** — FuelWatch publishes a
  current county median but no county history, so nothing can be trained per
  county. Instead the national forecast is shifted by a measured *basis*,
  computed against a station-weighted reference drawn from the same RPC rows
  so crowd-vs-bulletin method bias cancels, then shrunk by `n/(n+10)` so a
  three-station county can't advertise a six-cent gap.

---

## Stack

| Layer         | Choice                                              |
| ------------- | --------------------------------------------------- |
| Backend       | FastAPI, pandas, scikit-learn, statsmodels          |
| Storage       | SQLite (single file at `data/fuel_trend.db`)        |
| Scheduling    | APScheduler in-process + GitHub Actions cron        |
| HTTP client   | `requests` (fetchers), `httpx`/TestClient (export)  |
| Front-end     | Plain HTML/CSS + vanilla JS, Chart.js from CDN      |
| Static export | Starlette `TestClient` → JSON snapshots             |
| Deploy        | GitHub Pages via `actions/deploy-pages`             |

Deliberately no framework on the front-end — the site loads three small JSON
files and renders in one script, which keeps the Pages artifact tiny and the
learning surface small.

---

## Data sources

| Source               | Cadence       | Role                                                              |
| -------------------- | ------------- | ----------------------------------------------------------------- |
| EU Weekly Oil Bulletin | Weekly (Mon) | Authoritative Irish retail petrol/diesel prices, with and without duties + VAT. Since 2005. |
| FuelWatch.ie         | Daily         | Crowd-sourced pump reports aggregated to a national daily average. Fills the gap between weekly bulletins. |
| FuelWatch.ie (county) | Daily        | Per-county median via `county_price_rankings`, plus station-level prices via `cheapest_reported_stations`. Trailing-window aggregates with **no history upstream** — we snapshot them daily to build our own. |
| ECB                  | Daily         | EUR/USD reference rate.                                           |
| Yahoo Finance        | Daily         | Brent crude futures (`BZ=F`). Falls back to a deterministic mock when the feed is unreachable. |
| Curated RSS feeds    | Every 10 min  | Oil-relevant news, keyword-filtered.                              |
| Static tax calendar  | Manual        | Irish excise / carbon / MOT changes for prediction adjustment.    |

All fetchers live in `backend/app/data_sources/` and share the same shape:
each exposes an `ingest()` function that downloads, parses, and upserts.

---

## Setup

Windows PowerShell (macOS/Linux equivalents in parentheses):

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1                # source .venv/bin/activate
pip install -r requirements.txt
python ingest.py all                        # first-time data load
python export_static.py                     # writes frontend/data/*.json
uvicorn app.main:app --reload --port 8000
```

Then open:

- Dashboard: <http://localhost:8000/>
- API health: <http://localhost:8000/api/health>
- OpenAPI docs: <http://localhost:8000/docs>

The scheduler starts automatically on app boot and refreshes each source at
its own cadence — you don't need to keep re-running `ingest.py`.

### Ingest CLI

```powershell
python ingest.py                    # all sources
python ingest.py bulletin           # EU Oil Bulletin only
python ingest.py fuelwatch          # FuelWatch daily
python ingest.py counties           # FuelWatch county medians + stations
python ingest.py fx                 # ECB EUR/USD
python ingest.py brent              # Brent futures
python ingest.py news               # RSS feeds
python ingest.py all --force        # force re-download of cached raw files
```

---

## API

| Endpoint                            | Purpose                                                 |
| ----------------------------------- | ------------------------------------------------------- |
| `GET /api/health`                   | Liveness probe                                          |
| `GET /api/prices?weeks=N`           | Historical petrol/diesel series with latest snapshot    |
| `GET /api/prediction`               | Trend, weekly return forecast, confidence, R², explanation |
| `GET /api/news?limit=N`             | Oil-relevant news items, most recent first              |
| `GET /api/counties[?county=Cork]`   | Per-county median, basis, localised forecast, cheapest stations |

Full response schemas live in `backend/app/models.py` and are visible in the
auto-generated OpenAPI docs at `/docs`.

---

## Deploy: GitHub Pages

The dashboard is statically deployable. `.github/workflows/refresh.yml`
runs daily at 06:15 UTC (also on push and on manual dispatch):

1. Sets up Python 3.12 and installs `backend/requirements.txt`.
2. Runs `python ingest.py all --force` to pull every source fresh.
3. Runs `python export_static.py` to dump `frontend/data/*.json`.
4. Uploads `frontend/` as the Pages artifact.
5. Deploys via `actions/deploy-pages`.

One-time setup on the repository:

1. **Settings → Pages → Source: GitHub Actions.**
2. Push to `main`. The workflow runs and publishes to
   `https://<user>.github.io/<repo>/`.

The API is not exposed on Pages — the browser reads `data/*.json` instead,
using the same relative paths that FastAPI serves during local development.
No code path forks between the two deploy shapes.

---

## Project structure

```
petrolpredictor/
├── .github/workflows/refresh.yml   Daily ingest + Pages deploy
├── backend/
│   ├── ingest.py                   CLI for all data sources
│   ├── export_static.py            Dumps API responses to JSON
│   ├── requirements.txt
│   └── app/
│       ├── main.py                 FastAPI entrypoint (lifespan boots scheduler)
│       ├── db.py                   SQLite schema + connection helper
│       ├── models.py               Pydantic response models
│       ├── scheduler.py            APScheduler jobs
│       ├── data_sources/
│       │   ├── eu_oil_bulletin.py  Weekly EU authoritative feed
│       │   ├── fuelwatch_client.py Supabase creds discovery + RPC/REST helpers
│       │   ├── fuelwatch_ie.py     Daily crowd-sourced national average
│       │   ├── fuelwatch_counties.py County medians + station prices
│       │   ├── fx_rates.py         ECB EUR/USD
│       │   ├── brent_crude.py      Yahoo Finance Brent (with mock fallback)
│       │   ├── news_monitor.py     RSS keyword monitor
│       │   └── tax_calendar.py     Static Irish tax event calendar
│       ├── prediction/
│       │   ├── model.py            Feature engineering + regression
│       │   ├── county.py           Basis decomposition (pure functions)
│       │   └── explain.py          Natural-language explanation
│       └── routes/
│           ├── prices.py           /api/prices
│           ├── prediction.py       /api/prediction
│           ├── counties.py         /api/counties
│           └── news.py             /api/news
├── backend/tests/                  pytest — county basis math
├── frontend/
│   ├── index.html                  National dashboard
│   ├── county.html                 County Fuel Predictor
│   ├── style.css                   Dark theme, hand-tuned
│   ├── shared.js                   Formatters, chart theme, sparklines
│   ├── app.js                      National page controller
│   ├── county.js                   County page controller
│   └── data/                       Generated JSON snapshots (gitignored)
├── data/                           SQLite file (gitignored)
└── docs/                           Design specs
```

---

## Design decisions worth calling out

- **SQLite over Postgres.** The full dataset is well under a megabyte and
  read-heavy — Postgres would be pure overhead for a single-node dashboard.
- **Idempotent upserts everywhere.** Re-running any ingest command is safe;
  every source uses `INSERT ... ON CONFLICT DO UPDATE` on natural keys.
- **Auto-discovery for the crowd-sourced source.** The Supabase URL + anon
  JWT are extracted from the FuelWatch bundle each run instead of being
  pinned in code. Robust to key rotation and bundle re-hashing.
- **Weekly source wins on overlap.** The FuelWatch fetcher skips dates ≤ the
  latest EU bulletin date, so the authoritative wholesale history the model
  trains on is never overwritten by crowd-sourced retail.
- **One current price, site-wide.** The model can only train on rows carrying
  a wholesale price (EU Bulletin weeks), but the freshest row in
  `fuel_prices` is usually a FuelWatch daily up to a week newer. That
  observation is the single anchor for every displayed price — national card,
  national forecast, and every county. Predicted *movement* is unaffected,
  since each delta is derived from wholesale rather than from the anchor.
- **Same bundle, two deploys.** The front-end always reads relative
  `data/*.json`. FastAPI's `StaticFiles` serves those files at
  `http://localhost:8000/data/…` locally; GitHub Pages serves them at
  `/<repo>/data/…`. Zero conditional logic in `app.js`.

---

## Roadmap

- [x] EU Weekly Oil Bulletin ingest
- [x] ECB FX rate ingest
- [x] Brent crude (real + mock fallback)
- [x] Regression forecast + explanation
- [x] REST API (`/api/prices`, `/api/prediction`, `/api/news`)
- [x] Dashboard front-end
- [x] RSS news monitor + tax calendar
- [x] APScheduler background refresh
- [x] Daily crowd-sourced Irish pump data (FuelWatch.ie)
- [x] Static export + GitHub Pages CI
- [x] County-level breakdown + County Fuel Predictor page
- [ ] Confidence intervals on the forecast rather than a single point return
- [ ] Retire the reconstructed county line once ~8 weeks of real snapshots exist
- [ ] Per-county model, once there is a county target series to train on (~6 months)
- [ ] Estimate the shrinkage `K` and `σ_station` from data instead of priors
- [ ] Alerting when the model's confidence drops sharply
