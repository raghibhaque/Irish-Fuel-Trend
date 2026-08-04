"""EU Weekly Oil Bulletin fetcher.

Source: European Commission DG Energy, "Weekly Oil Bulletin — Prices History".
Public, no auth. Excel file with weekly national-average consumer prices
(inclusive of duties + taxes) going back to ~2005 for every EU member state.

Unit in source: EUR per 1000 litres. We convert to EUR per litre before storing.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

from app.db import connection

logger = logging.getLogger(__name__)

BULLETIN_PAGE_URL = "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en"
HISTORY_FILENAME_HINT = "Weekly_Oil_Bulletin_Prices_History"
CACHE_PATH = Path(__file__).resolve().parents[3] / "data" / "raw" / "eu_oil_bulletin_history.xlsx"

SOURCE_NAME = "EU_WEEKLY_OIL_BULLETIN"
COUNTRY = "IE"
PETROL_COL = "IE_price_with_tax_euro95"
DIESEL_COL = "IE_price_with_tax_diesel"

# Rows 1 (long names) + 2 (units) below the header row must be dropped before parsing.
HEADER_JUNK_ROWS = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (irish-fuel-trend/0.1; +https://github.com/)",
}


def _discover_history_url() -> str:
    """Scrape bulletin landing page and return absolute URL to history xlsx."""
    r = requests.get(BULLETIN_PAGE_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    match = re.search(
        rf'href="(/document/download/[^"]+{re.escape(HISTORY_FILENAME_HINT)}[^"]+\.xlsx)"',
        r.text,
    )
    if not match:
        raise RuntimeError(
            "Could not locate history xlsx link on bulletin page. "
            "The EU site layout may have changed."
        )
    return "https://energy.ec.europa.eu" + match.group(1)


def download_bulletin(force: bool = False) -> Path:
    """Download the history xlsx (cached to data/raw/). Returns path."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists() and not force:
        logger.info("Using cached bulletin at %s", CACHE_PATH)
        return CACHE_PATH

    url = _discover_history_url()
    logger.info("Downloading bulletin: %s", url)
    r = requests.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    CACHE_PATH.write_bytes(r.content)
    logger.info("Saved %d bytes to %s", len(r.content), CACHE_PATH)
    return CACHE_PATH


def parse_ireland_prices(xlsx_path: Path) -> pd.DataFrame:
    """Return DataFrame with columns: date, petrol, diesel (both EUR/L)."""
    raw = pd.read_excel(xlsx_path, sheet_name="Prices with taxes", header=0, engine="openpyxl")
    # Drop the two descriptive rows immediately below the header row.
    df = raw.iloc[HEADER_JUNK_ROWS:].copy()

    date_col = df.columns[0]  # first column is the date column
    if PETROL_COL not in df.columns or DIESEL_COL not in df.columns:
        raise RuntimeError(
            f"Expected columns {PETROL_COL!r} and {DIESEL_COL!r} not found. "
            f"Got: {list(df.columns)[:8]}..."
        )

    out = pd.DataFrame({
        "date": pd.to_datetime(df[date_col], errors="coerce").dt.date,
        "petrol_per_1000l": pd.to_numeric(df[PETROL_COL], errors="coerce"),
        "diesel_per_1000l": pd.to_numeric(df[DIESEL_COL], errors="coerce"),
    })
    out = out.dropna(subset=["date"])
    # Convert EUR / 1000L → EUR / L
    out["petrol"] = out["petrol_per_1000l"] / 1000.0
    out["diesel"] = out["diesel_per_1000l"] / 1000.0
    out = out[["date", "petrol", "diesel"]].sort_values("date").reset_index(drop=True)
    return out


def _iter_rows(df: pd.DataFrame) -> Iterable[tuple[date, str, float]]:
    for _, row in df.iterrows():
        d = row["date"]
        if pd.notna(row["petrol"]):
            yield d, "petrol", float(row["petrol"])
        if pd.notna(row["diesel"]):
            yield d, "diesel", float(row["diesel"])


def upsert_prices(df: pd.DataFrame) -> int:
    """Insert or update fuel_prices rows. Returns count written."""
    sql = """
        INSERT INTO fuel_prices (date, country, fuel_type, price_eur_per_litre, source)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(date, country, fuel_type)
        DO UPDATE SET price_eur_per_litre = excluded.price_eur_per_litre,
                      source              = excluded.source,
                      inserted_at         = CURRENT_TIMESTAMP;
    """
    payload = [
        (d.isoformat(), COUNTRY, fuel, price, SOURCE_NAME)
        for d, fuel, price in _iter_rows(df)
    ]
    with connection() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def ingest(force_download: bool = False) -> dict:
    """End-to-end: download, parse, store. Returns summary dict."""
    xlsx_path = download_bulletin(force=force_download)
    df = parse_ireland_prices(xlsx_path)
    rows_written = upsert_prices(df)
    return {
        "rows_written": rows_written,
        "weeks_parsed": len(df),
        "date_range": (df["date"].min().isoformat(), df["date"].max().isoformat()),
        "latest_petrol_eur_per_l": round(float(df.iloc[-1]["petrol"]), 4),
        "latest_diesel_eur_per_l": round(float(df.iloc[-1]["diesel"]), 4),
    }
