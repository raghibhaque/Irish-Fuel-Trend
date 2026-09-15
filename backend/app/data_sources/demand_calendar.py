"""Irish demand calendar — bank holidays and school breaks.

Loads a static JSON companion file. No network fetch; the calendar is a
manually-curated list of known dates from gov.ie (bank holidays) and
education.gov.ie (school break windows).

Two demand signals feed the trend model:

* `bank_holiday_in_week(week_end)` — a bank holiday inside next week
  historically pulls forward petrol demand (leisure travel + long
  weekends), which nudges pump prices upward at the margin.
* `school_break_in_week(week_end)` — school mid-terms and summer break
  concentrate family car travel, shifting demand higher than a
  normal working week.

Both return 1/0 flags that the model consumes as leading indicators.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

DATA_PATH = Path(__file__).parent / "demand_calendar_data.json"


@dataclass(frozen=True)
class BankHoliday:
    on: date
    name: str


@dataclass(frozen=True)
class SchoolBreak:
    start: date
    end: date
    name: str

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


_bank_holidays: list[BankHoliday] | None = None
_school_breaks: list[SchoolBreak] | None = None


def _load() -> tuple[list[BankHoliday], list[SchoolBreak]]:
    global _bank_holidays, _school_breaks
    if _bank_holidays is not None and _school_breaks is not None:
        return _bank_holidays, _school_breaks
    with DATA_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    _bank_holidays = sorted(
        (BankHoliday(date.fromisoformat(r["date"]), r["name"])
         for r in raw.get("bank_holidays", [])),
        key=lambda h: h.on,
    )
    _school_breaks = sorted(
        (SchoolBreak(date.fromisoformat(r["start"]), date.fromisoformat(r["end"]), r["name"])
         for r in raw.get("school_breaks", [])),
        key=lambda b: b.start,
    )
    return _bank_holidays, _school_breaks


def bank_holidays() -> list[BankHoliday]:
    return _load()[0]


def school_breaks() -> list[SchoolBreak]:
    return _load()[1]


def bank_holiday_in_week(week_end: date) -> bool:
    """True if any bank holiday falls in the 7-day window ending week_end."""
    start = week_end - timedelta(days=6)
    return any(start <= h.on <= week_end for h in bank_holidays())


def school_break_in_week(week_end: date) -> bool:
    """True if any school-break day falls in the 7-day window ending week_end."""
    start = week_end - timedelta(days=6)
    for b in school_breaks():
        # Overlap between [b.start, b.end] and [start, week_end].
        if b.end < start or b.start > week_end:
            continue
        return True
    return False


def demand_flag(week_end: date) -> int:
    """Combined 0/1 flag: bank holiday OR school break somewhere in the week."""
    return int(bank_holiday_in_week(week_end) or school_break_in_week(week_end))
