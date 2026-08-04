# Irish Fuel Trend

Petrol/diesel price trend predictor for Ireland. Predicts directional trend (up/down/flat) over a 1–3 week horizon and explains why using crude oil moves, EUR/USD, Irish tax changes, and oil-relevant news.

## Stack

- Backend: FastAPI + pandas + scikit-learn + SQLite
- Frontend: plain HTML/CSS/JS + Chart.js (CDN)
- Scheduler: APScheduler

## Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Then open http://localhost:8000/api/health — should return `{"status":"ok",...}`.

API docs: http://localhost:8000/docs

## Project structure

```
irish-fuel-trend/
├── backend/
│   └── app/
│       ├── main.py               FastAPI entrypoint
│       ├── db.py                 SQLite schema + connection
│       ├── data_sources/         Fetchers: EU Oil Bulletin, ECB FX, Brent, news, tax
│       ├── prediction/           Regression model + explain layer
│       └── routes/               /api/prices, /api/prediction, /api/news
├── frontend/                     Dashboard (HTML/CSS/JS)
└── data/                         SQLite file (gitignored)
```

## Status

- [x] Step 1: FastAPI skeleton + health endpoint
- [ ] Step 2: EU Oil Bulletin fetcher + SQLite storage
- [ ] Step 3: ECB FX rate fetcher
- [ ] Step 4: Brent crude (stub)
- [ ] Step 5: Regression + explain
- [ ] Step 6: API routes
- [ ] Step 7: Frontend dashboard
- [ ] Step 8: Real Brent + RSS news + tax calendar
