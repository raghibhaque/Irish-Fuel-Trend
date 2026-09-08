"""ECB EUR/USD + EUR/GBP daily reference rate fetcher.

Source: European Central Bank, "Euro foreign exchange reference rates".
Public, no auth. Full history from 1999-01-04 available as ~2MB XML.

Endpoint: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml

Rate semantics: 1 EUR = <rate> {USD | GBP}.
Rates published on TARGET business days only — weekends/holidays gap.

USD anchors the crude-to-euro conversion in the trend model. GBP is included
because a meaningful slice of Irish petrol/diesel supply moves via UK
terminals; sterling weakness against the euro cheapens the UK-anchored bit
of the wholesale stack. The GBP series feeds the `gbp_eur_ret_2w` feature.
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

# Currencies we lift out of the ECB envelope. USD is required (drives every
# crude/product EUR conversion); GBP is nice-to-have (fed to the trend model
# as a UK-supply-route signal, but the row is still inserted if GBP is missing
# for that date — pre-2004 dates only publish USD).
USD_CCY = "USD"
GBP_CCY = "GBP"

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


def parse_rates(xml_path: Path) -> list[tuple[date, float, float | None]]:
    """Extract [(date, eur_usd_rate, eur_gbp_or_None), ...].

    USD absence for a given day skips the row (that day's cube is unusable
    for the rest of the pipeline). GBP absence just stores None — the trend
    model tolerates it via forward-fill and drops rows where the feature
    window can't be computed.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    out: list[tuple[date, float, float | None]] = []
    for day_cube in root.iterfind(".//ecb:Cube[@time]", NS):
        time_str = day_cube.attrib.get("time")
        if not time_str:
            continue
        try:
            d = datetime.strptime(time_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        eur_usd: float | None = None
        eur_gbp: float | None = None
        for ccy_cube in day_cube.findall("ecb:Cube", NS):
            ccy = ccy_cube.attrib.get("currency")
            rate_str = ccy_cube.attrib.get("rate")
            if not rate_str:
                continue
            try:
                rate = float(rate_str)
            except ValueError:
                continue
            if ccy == USD_CCY:
                eur_usd = rate
            elif ccy == GBP_CCY:
                eur_gbp = rate
        if eur_usd is not None:
            out.append((d, eur_usd, eur_gbp))
    out.sort(key=lambda t: t[0])
    return out


# Kept as an alias so any existing importer (dev scripts, notebooks) that reached
# for the old two-column parser continues to compile. New code should call
# `parse_rates`, which also returns GBP.
def parse_eur_usd(xml_path: Path) -> list[tuple[date, float]]:
    return [(d, usd) for d, usd, _ in parse_rates(xml_path)]


def upsert_rates(rows: list[tuple[date, float, float | None]]) -> int:
    sql = """
        INSERT INTO fx_rates (date, eur_usd, eur_gbp, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            eur_usd     = excluded.eur_usd,
            eur_gbp     = excluded.eur_gbp,
            source      = excluded.source,
            inserted_at = CURRENT_TIMESTAMP;
    """
    payload = [(d.isoformat(), usd, gbp, SOURCE_NAME) for d, usd, gbp in rows]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def ingest(force_download: bool = False) -> dict:
    xml_path = download(force=force_download)
    rows = parse_rates(xml_path)
    written = upsert_rates(rows)
    latest = rows[-1] if rows else None
    return {
        "rows_written": written,
        "date_range": (rows[0][0].isoformat(), rows[-1][0].isoformat()) if rows else None,
        "latest_eur_usd": latest[1] if latest else None,
        "latest_eur_gbp": latest[2] if latest else None,
    }
