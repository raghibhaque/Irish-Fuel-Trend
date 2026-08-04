"""GET /api/prices — historical + current Irish avg petrol & diesel."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query

from app.db import connection
from app.models import FuelPriceSeries, PricePoint, PricesResponse

router = APIRouter(prefix="/api", tags=["prices"])


def _load_series(fuel_type: str, since: date) -> FuelPriceSeries:
    with connection() as conn:
        rows = conn.execute(
            "SELECT date, price_eur_per_litre FROM fuel_prices "
            "WHERE country='IE' AND fuel_type=? AND date >= ? "
            "ORDER BY date",
            (fuel_type, since.isoformat()),
        ).fetchall()
    points = [
        PricePoint(
            date=r["date"] if isinstance(r["date"], date) else date.fromisoformat(r["date"]),
            price_eur_per_litre=r["price_eur_per_litre"],
        )
        for r in rows
    ]
    return FuelPriceSeries(
        fuel_type=fuel_type,
        points=points,
        latest=points[-1] if points else None,
    )


@router.get("/prices", response_model=PricesResponse)
def get_prices(
    weeks: int = Query(26, ge=1, le=1200, description="How many weeks of history to return."),
) -> PricesResponse:
    since = date.today() - timedelta(weeks=weeks)
    return PricesResponse(
        petrol=_load_series("petrol", since),
        diesel=_load_series("diesel", since),
    )
