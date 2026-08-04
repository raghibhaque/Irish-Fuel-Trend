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
from app.data_sources import eu_oil_bulletin, fx_rates, brent_crude

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", default="all",
                        choices=["all", "bulletin", "fx", "brent"])
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
        print("Brent (MOCK):", summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
