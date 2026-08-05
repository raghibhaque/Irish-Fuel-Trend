"""GET /api/counties — per-county price level, localised forecast, and stations.

The response deliberately carries **no reconstructed price series**. Twenty-four
counties by two fuels by ~1200 national points would be a multi-megabyte
payload of data the browser can derive itself. It ships the basis; the county
page adds it to the national series it already fetches from `prices.json`.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.data_sources.fuelwatch_counties import IE_COUNTIES, PRIMARY_WINDOW_DAYS
from app.db import connection
from app.models import (
    CountiesResponse,
    CountyEntry,
    CountyFuelSnapshot,
    CountyObservation,
    CountyStation,
    NationalReference,
)
from app.prediction import county as county_mod
from app.routes import prediction as prediction_route

router = APIRouter(prefix="/api", tags=["counties"])

FUELS = ("petrol", "diesel")

# How much of our own accumulated county history to ship. Caps payload growth;
# at 24 counties x 2 fuels this is ~8600 points once fully banked.
OBSERVED_DAYS = 180

# Cheapest stations shown per county per fuel.
STATIONS_PER_COUNTY = 5


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _latest_snapshot_date(conn, table: str) -> str | None:
    row = conn.execute(f"SELECT MAX(snapshot_date) AS d FROM {table}").fetchone()
    return str(row["d"])[:10] if row and row["d"] else None


def _national_predictions() -> tuple[dict, list[str]]:
    """National per-fuel predictions, or an empty dict plus a note if unavailable.

    The county page degrades to prices-and-history rather than failing outright
    when the national model can't train.
    """
    try:
        resp = prediction_route.get_prediction()
    except HTTPException as exc:
        return {}, [f"National prediction unavailable ({exc.detail}). "
                    f"County prices and history are still shown."]
    return {f: getattr(resp, f).model_dump() for f in FUELS}, list(resp.notes)


def _latest_national_pump(conn) -> dict[str, float]:
    """Freshest observed national pump price per fuel — the county level's anchor.

    This is the same row the national dashboard headlines. The prediction
    model's own `current_pump_eur_per_l` is the last EU Bulletin week, which
    can sit ~10c away on a different measurement scale; anchoring here keeps
    the county page from contradicting the site's national number. See
    `prediction/county.build_county_prediction`.
    """
    rows = conn.execute(
        "SELECT fuel_type, price_eur_per_litre FROM fuel_prices f "
        "WHERE country='IE' AND date = ("
        "  SELECT MAX(date) FROM fuel_prices WHERE country='IE' AND fuel_type = f.fuel_type"
        ")"
    ).fetchall()
    return {r["fuel_type"]: float(r["price_eur_per_litre"]) for r in rows}


def _load_snapshot_rows(conn, snapshot_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT county, fuel_type, median_eur_per_litre, station_count, window_days "
        "FROM county_prices WHERE snapshot_date = ?",
        (snapshot_date,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_observations(conn, since: str) -> dict[tuple[str, str], list[CountyObservation]]:
    """Our own accumulated county history, primary window only."""
    rows = conn.execute(
        "SELECT snapshot_date, county, fuel_type, median_eur_per_litre, station_count "
        "FROM county_prices WHERE window_days = ? AND snapshot_date >= ? "
        "ORDER BY snapshot_date",
        (PRIMARY_WINDOW_DAYS, since),
    ).fetchall()
    out: dict[tuple[str, str], list[CountyObservation]] = defaultdict(list)
    for r in rows:
        out[(r["county"], r["fuel_type"])].append(
            CountyObservation(
                date=_as_date(r["snapshot_date"]),
                median_eur_per_litre=r["median_eur_per_litre"],
                station_count=r["station_count"],
            )
        )
    return out


def _load_stations(conn, snapshot_date: str | None) -> dict[tuple[str, str], list[CountyStation]]:
    if not snapshot_date:
        return {}
    rows = conn.execute(
        "SELECT county, fuel_type, name, brand, price_eur_per_litre, reported_at "
        "FROM county_stations WHERE snapshot_date = ? AND county IS NOT NULL "
        "ORDER BY price_eur_per_litre",
        (snapshot_date,),
    ).fetchall()
    out: dict[tuple[str, str], list[CountyStation]] = defaultdict(list)
    for r in rows:
        bucket = out[(r["county"], r["fuel_type"])]
        if len(bucket) < STATIONS_PER_COUNTY:
            bucket.append(
                CountyStation(
                    name=r["name"],
                    brand=r["brand"],
                    price_eur_per_litre=r["price_eur_per_litre"],
                    reported_at=r["reported_at"],
                )
            )
    return out


def _national_references(rows: list[dict]) -> dict[tuple[str, int], float]:
    """Same-pool reference per (fuel, window). See prediction/county.py for why."""
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for r in rows:
        grouped[(r["fuel_type"], int(r["window_days"]))].append(r)
    refs: dict[tuple[str, int], float] = {}
    for key, group in grouped.items():
        try:
            refs[key] = county_mod.national_reference(group)
        except ValueError:
            continue
    return refs


def _build_snapshot(fuel: str, rows: list[dict], refs, national: dict,
                    anchors: dict[str, float], observations,
                    stations) -> CountyFuelSnapshot | None:
    try:
        chosen = county_mod.select_row(rows, primary_window=PRIMARY_WINDOW_DAYS)
    except county_mod.UnknownCountyError:
        return None

    ref = refs.get((fuel, int(chosen["window_days"])))
    if ref is None:
        return None

    if national:
        payload = county_mod.build_county_prediction(
            national[fuel], chosen, ref, anchor_pump=anchors.get(fuel)
        )
    else:
        # No national model: still report the measured level and offset.
        basis_raw = county_mod.raw_basis(chosen["median_eur_per_litre"], ref)
        n = int(chosen.get("station_count") or 0)
        payload = {
            "fuel_type": fuel,
            "crowd_median_eur_per_litre": chosen["median_eur_per_litre"],
            "station_count": n,
            "window_days": int(chosen["window_days"]),
            "stale": bool(chosen.get("stale", False)),
            "low_sample": n < county_mod.LOW_SAMPLE_THRESHOLD,
            "basis_eur_per_litre": county_mod.shrink(basis_raw, n),
            "basis_raw_eur_per_litre": basis_raw,
        }

    payload.pop("county", None)   # carried by the parent CountyEntry
    return CountyFuelSnapshot(
        **payload,
        observations=observations.get((chosen["county"], fuel), []),
        stations=stations.get((chosen["county"], fuel), []),
    )


@router.get("/counties", response_model=CountiesResponse)
def get_counties(
    county: str | None = Query(None, description="Filter to a single county, e.g. Cork."),
) -> CountiesResponse:
    with connection() as conn:
        snapshot_date = _latest_snapshot_date(conn, "county_prices")
        if not snapshot_date:
            raise HTTPException(
                status_code=503,
                detail="No county snapshots yet. Run `python ingest.py counties`.",
            )
        rows = _load_snapshot_rows(conn, snapshot_date)
        since = (_as_date(snapshot_date) - timedelta(days=OBSERVED_DAYS)).isoformat()
        observations = _load_observations(conn, since)
        stations = _load_stations(conn, _latest_snapshot_date(conn, "county_stations"))
        anchors = _latest_national_pump(conn)

    national, notes = _national_predictions()
    refs = _national_references(rows)

    by_county: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_county[r["county"]][r["fuel_type"]].append(r)

    wanted = sorted(by_county)
    if county:
        match = next((c for c in wanted if c.lower() == county.strip().lower()), None)
        if match is None:
            raise HTTPException(
                status_code=404,
                detail=f"No county data for {county!r}. Available: {', '.join(wanted)}",
            )
        wanted = [match]

    entries = [
        CountyEntry(
            county=name,
            **{
                fuel: _build_snapshot(fuel, by_county[name].get(fuel, []), refs,
                                      national, anchors, observations, stations)
                for fuel in FUELS
            },
        )
        for name in wanted
    ]

    missing = [c for c in IE_COUNTIES if c not in by_county]
    if missing:
        notes.append(
            f"No crowd-sourced reports clear the ranking threshold in "
            f"{', '.join(missing)} — these counties have no median at any window."
        )

    return CountiesResponse(
        generated_at=datetime.now(timezone.utc),
        snapshot_date=_as_date(snapshot_date),
        counties=entries,
        national_reference=[
            NationalReference(
                fuel_type=fuel,
                window_days=window,
                median_eur_per_litre=value,
                station_count=sum(
                    int(r["station_count"] or 0) for r in rows
                    if r["fuel_type"] == fuel and int(r["window_days"]) == window
                ),
            )
            for (fuel, window), value in sorted(refs.items())
        ],
        counties_without_data=missing,
        notes=notes,
    )
