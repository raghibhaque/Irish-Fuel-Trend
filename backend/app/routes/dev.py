"""POST /api/ingest — dev-only manual ingest trigger.

Bound to loopback clients only. FastAPI is not exposed on GitHub Pages, but
this guard keeps the endpoint safe if someone runs uvicorn on a public host.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from app.data_sources import (
    brand_stations,
    brent_crude,
    eu_oil_bulletin,
    fuelwatch_counties,
    fuelwatch_ie,
    fx_rates,
    news_monitor,
    refined_products,
)

router = APIRouter(prefix="/api", tags=["dev"])

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

_SOURCES = {
    "news": news_monitor.ingest,
    "brent": brent_crude.ingest,
    "fx": fx_rates.ingest,
    "fuelwatch": fuelwatch_ie.ingest,
    "counties": fuelwatch_counties.ingest,
    "bulletin": eu_oil_bulletin.ingest,
    "refined": refined_products.ingest,
    "brands": brand_stations.ingest,
}


@router.post("/ingest")
def ingest(
    request: Request,
    sources: str = Query(
        "news,brent,fx,fuelwatch,counties,bulletin",
        description="Comma-separated source names (default: everything that moves prices)",
    ),
) -> dict:
    client_host = request.client.host if request.client else ""
    if client_host not in _LOOPBACK:
        raise HTTPException(status_code=403, detail="ingest is loopback-only")

    names = [s.strip() for s in sources.split(",") if s.strip()]
    unknown = [n for n in names if n not in _SOURCES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown sources: {unknown}")

    results = {}
    for name in names:
        try:
            results[name] = {"ok": True, "summary": _SOURCES[name]()}
        except Exception as exc:
            results[name] = {"ok": False, "error": str(exc)}

    # Re-dump the static JSON snapshots the frontend reads. Under uvicorn the
    # SPA hits data/*.json (served by StaticFiles), not the live API, so a
    # pure DB write would not be visible without this step.
    export_summary = None
    try:
        from export_static import DEFAULT_OUT, export
        export_summary = export(Path(DEFAULT_OUT), use_lifespan=False)
    except Exception as exc:
        export_summary = {"ok": False, "error": str(exc)}

    return {"ran": names, "results": results, "export": export_summary}
