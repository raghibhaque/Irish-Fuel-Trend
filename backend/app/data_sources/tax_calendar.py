"""Irish fuel tax calendar.

Loads scheduled/known changes to Irish excise, carbon tax, VAT and NORA levy
from a JSON file next to this module. Populate by hand from Dept of Finance
and Revenue announcements (Budget days, mid-year adjustments, etc.).

The JSON layout is intentionally simple so a non-programmer can edit it.
See tax_calendar_data.json for the schema.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

DATA_PATH = Path(__file__).parent / "tax_calendar_data.json"


@dataclass(frozen=True)
class TaxEvent:
    effective_date: date
    category: str
    description: str
    delta_petrol: float
    delta_diesel: float
    source: str

    def delta_for(self, fuel_type: str) -> float:
        if fuel_type == "petrol":
            return self.delta_petrol
        if fuel_type == "diesel":
            return self.delta_diesel
        return 0.0


def load_events() -> list[TaxEvent]:
    with DATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    events: list[TaxEvent] = []
    for row in data.get("events", []):
        deltas = row.get("delta_eur_per_l", {}) or {}
        events.append(TaxEvent(
            effective_date=date.fromisoformat(row["effective_date"]),
            category=row["category"],
            description=row["description"],
            delta_petrol=float(deltas.get("petrol", 0.0)),
            delta_diesel=float(deltas.get("diesel", 0.0)),
            source=row.get("source", ""),
        ))
    events.sort(key=lambda e: e.effective_date)
    return events


def events_between(start: date, end: date, fuel_type: str | None = None) -> list[TaxEvent]:
    """Events with effective_date in [start, end], optionally filtered to those
    that actually change price for the given fuel_type (non-zero delta)."""
    result: Iterable[TaxEvent] = (e for e in load_events() if start <= e.effective_date <= end)
    if fuel_type:
        result = (e for e in result if e.delta_for(fuel_type) != 0.0)
    return list(result)
