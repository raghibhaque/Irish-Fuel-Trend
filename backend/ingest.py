"""CLI to run data ingestion.

Usage:
    python ingest.py                # ingest all sources
    python ingest.py bulletin       # only EU Oil Bulletin
    python ingest.py --force        # force re-download of cached files
"""
from __future__ import annotations

import argparse
import logging
import sys

from app.db import init_db
from app.data_sources import (
    aa_ireland,
    brand_stations,
    brent_crude,
    brent_curve,
    cso_fuel_index,
    eu_oil_bulletin,
    fuelwatch_counties,
    fuelwatch_ie,
    fx_rates,
    news_monitor,
    nwe_gasoil,
    pumps_ie,
    refined_products,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", default="all",
                        choices=["all", "bulletin", "fx", "brent", "brent_curve",
                                 "news", "fuelwatch", "counties", "refined",
                                 "brands", "nwe", "aa", "cso", "pumps"])
    parser.add_argument("--force", action="store_true",
                        help="force re-download of cached raw files")
    args = parser.parse_args()

    init_db()

    if args.source in ("all", "bulletin"):
        summary = eu_oil_bulletin.ingest(force_download=args.force)
        print("EU Oil Bulletin:", summary)

    if args.source in ("all", "fx"):
        summary = fx_rates.ingest(force_download=args.force)
        print("ECB EUR/USD:", summary)

    if args.source in ("all", "brent"):
        summary = brent_crude.ingest(force_download=args.force)
        label = "MOCK" if summary.get("mock") else "REAL"
        print(f"Brent ({label}):", summary)

    if args.source in ("all", "brent_curve"):
        bno = brent_curve.ingest(force_download=args.force)
        print(f"Brent curve BNO ({'MOCK' if bno.get('mock') else 'REAL'}):", bno)
        usl = brent_curve.ingest_usl(force_download=args.force)
        print(f"Brent curve USL ({'MOCK' if usl.get('mock') else 'REAL'}):", usl)

    if args.source in ("all", "news"):
        summary = news_monitor.ingest()
        print("News (RSS):", summary)

    if args.source in ("all", "fuelwatch"):
        summary = fuelwatch_ie.ingest()
        print("FuelWatch.ie (daily crowd-sourced):", summary)

    if args.source in ("all", "counties"):
        summary = fuelwatch_counties.ingest()
        print("FuelWatch.ie (county breakdown):", summary)

    if args.source in ("all", "refined"):
        summary = refined_products.ingest(force_download=args.force)
        print("Refined products (RBOB/ULSD):", summary)

    if args.source in ("all", "nwe"):
        summary = nwe_gasoil.ingest(force_download=args.force)
        # `nwe_gasoil` runs both symbols in one pass and reports per-symbol
        # mock/real status inside the payload — keep the label plain.
        print("Refined products (NWE gasoil / EBOB):", summary)

    if args.source in ("all", "brands"):
        summary = brand_stations.ingest()
        print("Brand station catalogues (Applegreen, Maxol):", summary)

    if args.source in ("all", "aa"):
        summary = aa_ireland.ingest()
        label = "MOCK" if summary.get("mock") else "REAL"
        print(f"AA Ireland monthly survey ({label}):", summary)

    if args.source in ("all", "cso"):
        summary = cso_fuel_index.ingest()
        label = "MOCK" if summary.get("mock") else "REAL"
        print(f"CSO retail fuel index ({label}):", summary)

    if args.source in ("all", "pumps"):
        summary = pumps_ie.ingest()
        label = "MOCK" if summary.get("mock") else "REAL"
        print(f"Pumps.ie crowd feed ({label}):", summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
