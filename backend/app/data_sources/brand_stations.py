"""Brand-published station catalogues.

Fetches Ireland station lists directly from the operators that publish them:

    Applegreen  — WordPress REST at locations.applegreen.com/wp-json/locations/v1/search
                  Filterable by ?county=Clare. Returns name, address, town, lat,
                  lon and a rich amenity payload (adblue, ev, subway, ...).

    Maxol       — Yext Answers API at cdn.yextapis.com (public key baked into
                  stations.maxol.ie). Filter by address.region == 'Clare'. Small
                  count (~3 in Clare) but authoritative — these are Maxol's own
                  records, not crowd reports.

Neither source publishes *prices*. What we get here is a durable name +
brand + location catalogue that:
  - widens Clare coverage beyond the ~5-10 stations FuelWatch reports on a
    typical day,
  - lets the county page cross-reference crowd-reported prices against the
    known brand universe,
  - decays gracefully — if one brand endpoint breaks the others still ingest.

Snapshotted daily so the read path can trend changes (openings/closures)
and so we can rebuild history from the DB alone.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import requests

from app.db import connection

logger = logging.getLogger(__name__)

# Full IE + NI county list. Applegreen search accepts each verbatim; Maxol
# uses these as address.region filter values.
IE_COUNTIES = (
    "Carlow", "Cavan", "Clare", "Cork", "Donegal", "Dublin", "Galway", "Kerry",
    "Kildare", "Kilkenny", "Laois", "Leitrim", "Limerick", "Longford", "Louth",
    "Mayo", "Meath", "Monaghan", "Offaly", "Roscommon", "Sligo", "Tipperary",
    "Waterford", "Westmeath", "Wexford", "Wicklow",
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (irish-fuel-trend/0.1; +https://github.com/)",
    "Accept": "application/json",
}

# ---------------------------------------------------------- Applegreen

APPLEGREEN_SEARCH_URL = (
    "https://locations.applegreen.com/wp-json/locations/v1/search"
)


def fetch_applegreen_county(county: str, timeout: int = 30) -> list[dict]:
    r = requests.get(
        APPLEGREEN_SEARCH_URL,
        params={"county": county},
        headers=HEADERS,
        timeout=timeout,
    )
    r.raise_for_status()
    payload = r.json()
    if not payload.get("success"):
        return []
    rows: list[dict] = []
    for s in payload.get("data") or []:
        if not s.get("applegreen_location", True):
            # skip non-Applegreen-branded partner sites in the same feed
            continue
        amen = {
            k: s.get(k) for k in (
                "twentyfour_hour", "hgv", "service_area", "fast_fill", "adblue",
                "adblue_can", "gas_oil_pump", "ev_charging_bay", "toilet",
                "chopstix", "burger_king", "braeburn_coffee", "bakewell",
                "subway", "icecream", "car_wash", "new_carwash", "jetwash_diy",
                "brushwash_machine", "airvac", "handwash_attended", "ms",
                "car_mat_cleaner",
            )
        }
        rows.append({
            "external_id": str(s.get("id")),
            "name": s.get("name") or "Applegreen",
            "county": county,
            "town": s.get("town"),
            "address": (
                ", ".join(x for x in (s.get("address_1"), s.get("address_2")) if x)
                or None
            ),
            "latitude": s.get("latitude"),
            "longitude": s.get("longitude"),
            "amenities_json": json.dumps(amen, sort_keys=True),
            "url": (
                "https://applegreenstores.com" + s.get("link")
                if s.get("link") else None
            ),
        })
    return rows


# ---------------------------------------------------------- Maxol (Yext)

YEXT_QUERY_URL = (
    "https://cdn.yextapis.com/v2/accounts/me/answers/vertical/query"
)
YEXT_API_KEY = "3652290fc55da954d9da84ed7947c845"
YEXT_EXPERIENCE_KEY = "maxol-station-finder"
YEXT_VERTICAL_KEY = "locations"
YEXT_API_VERSION = "20240101"


def fetch_maxol_county(county: str, timeout: int = 30) -> list[dict]:
    r = requests.get(
        YEXT_QUERY_URL,
        params={
            "api_key": YEXT_API_KEY,
            "experienceKey": YEXT_EXPERIENCE_KEY,
            "verticalKey": YEXT_VERTICAL_KEY,
            "v": YEXT_API_VERSION,
            "locale": "en",
            "input": "",
            "limit": 50,
            "filters": json.dumps({"address.region": {"$eq": county}}),
        },
        headers=HEADERS,
        timeout=timeout,
    )
    r.raise_for_status()
    payload = r.json()
    results = payload.get("response", {}).get("results") or []
    rows: list[dict] = []
    for res in results:
        data = res.get("data") or {}
        addr = data.get("address") or {}
        pos = data.get("yextDisplayCoordinate") or data.get("geocodedCoordinate") or {}
        rows.append({
            "external_id": str(data.get("meta", {}).get("id") or data.get("id") or data.get("name")),
            "name": data.get("name") or "Maxol",
            "county": county,
            "town": addr.get("city"),
            "address": addr.get("line1"),
            "latitude": pos.get("latitude"),
            "longitude": pos.get("longitude"),
            "amenities_json": json.dumps({
                k: data.get(k) for k in (
                    "hours", "brands", "services", "paymentOptions",
                ) if data.get(k) is not None
            }, sort_keys=True),
            "url": data.get("websiteUrl", {}).get("url") if isinstance(data.get("websiteUrl"), dict) else None,
        })
    return rows


# ---------------------------------------------------------- persist

def upsert_stations(rows: list[dict], source_brand: str, snapshot_date: str) -> int:
    sql = """
        INSERT INTO brand_stations (
            snapshot_date, source_brand, external_id, name, county, town,
            address, latitude, longitude, amenities, url
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date, source_brand, external_id)
        DO UPDATE SET name       = excluded.name,
                      county     = excluded.county,
                      town       = excluded.town,
                      address    = excluded.address,
                      latitude   = excluded.latitude,
                      longitude  = excluded.longitude,
                      amenities  = excluded.amenities,
                      url        = excluded.url,
                      inserted_at = CURRENT_TIMESTAMP;
    """
    payload = [
        (
            snapshot_date,
            source_brand,
            r["external_id"],
            r["name"],
            r.get("county"),
            r.get("town"),
            r.get("address"),
            r.get("latitude"),
            r.get("longitude"),
            r.get("amenities_json"),
            r.get("url"),
        )
        for r in rows
        if r.get("external_id")
    ]
    if not payload:
        return 0
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ---------------------------------------------------------- top-level ingest

_SOURCES = (
    ("APPLEGREEN", fetch_applegreen_county),
    ("MAXOL",      fetch_maxol_county),
)


def ingest(counties: tuple[str, ...] = IE_COUNTIES) -> dict:
    """Pull every brand catalogue for every IE county and upsert one snapshot.

    Failures are per (brand, county) — one broken source or county doesn't
    stop the rest. Returns a summary dict of counts per brand + errors.
    """
    snapshot_date = _today()
    written_by_brand: dict[str, int] = {}
    clare_counts: dict[str, int] = {}
    errors: list[str] = []

    for brand, fetcher in _SOURCES:
        total = 0
        for county in counties:
            try:
                rows = fetcher(county)
            except Exception as exc:  # requests, JSON, HTTP — never fatal
                errors.append(f"{brand}/{county}: {exc}")
                continue
            n = upsert_stations(rows, brand, snapshot_date)
            total += n
            if county == "Clare":
                clare_counts[brand] = n
        written_by_brand[brand] = total

    return {
        "snapshot_date": snapshot_date,
        "rows_by_brand": written_by_brand,
        "clare_rows_by_brand": clare_counts,
        "errors": errors,
    }
