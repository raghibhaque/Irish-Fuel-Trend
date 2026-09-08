"""Shared pytest setup.

Runs the idempotent schema migration once per test session so that DB-backed
smoke tests see the latest column set (e.g. `fx_rates.eur_gbp`) even on
databases created before those columns existed. Cheap: `init_db()` is a
no-op when everything is already in place.
"""
from __future__ import annotations

from app.db import DB_PATH, init_db


def pytest_configure(config) -> None:  # noqa: ARG001 — pytest hook signature
    if DB_PATH.exists():
        init_db()
