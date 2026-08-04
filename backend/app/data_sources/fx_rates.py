"""ECB EUR/USD daily reference rate fetcher.

Source: European Central Bank, "Euro foreign exchange reference rates".
Public, no auth. Full history from 1999-01-04 available as ~2MB XML.

Endpoint: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml

Rate semantics: 1 EUR = <rate> USD (i.e. USD per EUR).
Rates published on TARGET business days only — weekends/holidays gap.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from app.db import connection

logger = logging.getLogger(__name__)

HIST_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
CACHE_PATH = Path(__file__).resolve().parents[3] / "data" / "raw" / "ecb_eurofxref_hist.xml"

SOURCE_NAME = "ECB_EUROFXREF"
CURRENCY = "USD"

# ECB XML uses gesmes + eurofxref namespaces
NS = {
    "gesmes": "http://www.gesmes.org/xml/2002-08-01",
    "ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (irish-fuel-trend/0.1; +https://github.com/)",
}


def download(force: bool = False) -> Path:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists() and not force:
        logger.info("Using cached ECB history at %s", CACHE_PATH)
        return CACHE_PATH
    logger.info("Downloading ECB history: %s", HIST_URL)
    r = requests.get(HIST_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    CACHE_PATH.write_bytes(r.content)
    logger.info("Saved %d bytes to %s", len(r.content), CACHE_PATH)
    return CACHE_PATH


def parse_eur_usd(xml_path: Path) -> list[tuple[date, float]]:
    """Extract [(date, eur_usd_rate), ...] for USD only."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    out: list[tuple[date, float]] = []
    # Structure: gesmes:Envelope / ecb:Cube / ecb:Cube[time=...] / ecb:Cube[currency=USD, rate=...]
    for day_cube in root.iterfind(".//ecb:Cube[@time]", NS):
        time_str = day_cube.attrib.get("time")
        if not time_str:
            continue
        try:
            d = datetime.strptime(time_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        for ccy_cube in day_cube.findall("ecb:Cube", NS):
            if ccy_cube.attrib.get("currency") == CURRENCY:
                rate_str = ccy_cube.attrib.get("rate")
                if rate_str:
                    try:
                        out.append((d, float(rate_str)))
                    except ValueError:
                        pass
                break
    out.sort(key=lambda t: t[0])
    return out


def upsert_rates(rows: list[tuple[date, float]]) -> int:
    sql = """
        INSERT INTO fx_rates (date, eur_usd, source)
        VALUES (?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            eur_usd     = excluded.eur_usd,
            source      = excluded.source,
            inserted_at = CURRENT_TIMESTAMP;
    """
    payload = [(d.isoformat(), rate, SOURCE_NAME) for d, rate in rows]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def ingest(force_download: bool = False) -> dict:
    xml_path = download(force=force_download)
    rows = parse_eur_usd(xml_path)
    written = upsert_rates(rows)
    return {
        "rows_written": written,
        "date_range": (rows[0][0].isoformat(), rows[-1][0].isoformat()) if rows else None,
        "latest_eur_usd": rows[-1][1] if rows else None,
    }
