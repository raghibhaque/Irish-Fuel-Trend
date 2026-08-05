"""Dump API responses to static JSON so the frontend can run without FastAPI.

Used both:
    - locally, after `ingest.py`, to refresh the dev dashboard
    - by GitHub Actions daily to publish updates to GitHub Pages

Output tree (default):
    frontend/data/prices.json      full history for both fuels
    frontend/data/prediction.json  current model output
    frontend/data/news.json        recent oil-relevant items

The script boots the FastAPI app through Starlette's TestClient so we
reuse the exact response schemas the frontend already consumes — no
hand-maintained JSON shapes.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "frontend" / "data"

# Full history request. EU Oil Bulletin goes back to 2005 ≈ 1100 weeks; give
# some headroom so future runs never truncate.
PRICES_WEEKS = 1200
NEWS_LIMIT = 50


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("wrote %s (%d bytes)", path, path.stat().st_size)


def export(out_dir: Path) -> dict:
    init_db()
    with TestClient(app) as client:
        prices = client.get(f"/api/prices?weeks={PRICES_WEEKS}")
        prices.raise_for_status()
        _write(out_dir / "prices.json", prices.json())

        # Prediction may 503 if there isn't enough history yet — degrade gracefully.
        pred = client.get("/api/prediction")
        if pred.status_code == 200:
            _write(out_dir / "prediction.json", pred.json())
        else:
            logger.warning("prediction endpoint returned %s: %s", pred.status_code, pred.text[:200])
            _write(out_dir / "prediction.json", {
                "petrol": {"trend": "unknown", "predicted_weekly_return": None,
                           "confidence": 0, "r2": 0,
                           "explanation": "Prediction unavailable: " + pred.text[:200]},
                "diesel": {"trend": "unknown", "predicted_weekly_return": None,
                           "confidence": 0, "r2": 0,
                           "explanation": "Prediction unavailable."},
                "notes": ["Prediction endpoint returned HTTP " + str(pred.status_code)],
            })

        news = client.get(f"/api/news?limit={NEWS_LIMIT}")
        news.raise_for_status()
        _write(out_dir / "news.json", news.json())

        # 503s until the first county ingest has run — degrade like prediction.
        counties = client.get("/api/counties")
        if counties.status_code == 200:
            _write(out_dir / "counties.json", counties.json())
        else:
            logger.warning("counties endpoint returned %s: %s",
                           counties.status_code, counties.text[:200])
            _write(out_dir / "counties.json", {
                "counties": [],
                "national_reference": [],
                "counties_without_data": [],
                "notes": ["County data unavailable: HTTP " + str(counties.status_code)],
            })

    return {"out_dir": str(out_dir)}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="Directory to write JSON snapshots into.")
    args = parser.parse_args()
    summary = export(args.out.resolve())
    print("Static export:", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
