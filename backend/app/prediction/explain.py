"""Human-readable explanation for a TrendPrediction.

Templated, deterministic. No LLM. Keeps the "why" easy to audit and change.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.data_sources.tax_calendar import events_between
from app.prediction.model import TrendPrediction

# Window we look ahead + look back for a tax change to flag.
TAX_LOOK_BACK_DAYS = 14
TAX_LOOK_AHEAD_DAYS = 21


TREND_HEAD = {
    "up":   "Prices likely to rise over the next 1–3 weeks.",
    "down": "Prices likely to fall over the next 1–3 weeks.",
    "flat": "Prices likely to hold roughly steady over the next 1–3 weeks.",
}


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def _tax_sentence(fuel_type: str, as_of: date) -> str:
    window_start = as_of - timedelta(days=TAX_LOOK_BACK_DAYS)
    window_end   = as_of + timedelta(days=TAX_LOOK_AHEAD_DAYS)
    events = events_between(window_start, window_end, fuel_type=fuel_type)
    if not events:
        return "No Irish excise/carbon-tax changes flagged in the ±3-week window."
    bits = []
    for e in events:
        delta = e.delta_for(fuel_type)
        sign = "+" if delta >= 0 else ""
        bits.append(f"{e.effective_date.isoformat()} {e.category} {sign}€{delta:.3f}/L ({e.description})")
    return "Tax changes in window: " + "; ".join(bits) + "."


def explain(pred: TrendPrediction, mock_brent: bool = False) -> str:
    f = pred.features
    parts = [TREND_HEAD[pred.trend]]

    parts.append(
        f"Brent-in-EUR {_pct(f['brent_eur_ret_2w'])} over the prior 2 weeks "
        f"and {_pct(f['brent_eur_ret_6w'])} over the prior 6 weeks "
        f"(currently €{f['brent_eur_per_bbl_current']:.2f}/bbl)."
    )
    parts.append(
        f"Last week's wholesale {pred.fuel_type} return was "
        f"{_pct(f['prev_wholesale_return'])}."
    )

    parts.append(_tax_sentence(pred.fuel_type, pred.as_of))

    parts.append(
        f"Model estimate: {_pct(pred.predicted_weekly_return)} weekly change "
        f"in wholesale {pred.fuel_type}, confidence {pred.confidence:.0%}, "
        f"in-sample R² {pred.r2:.2f}, trained on {pred.n_train} weeks. "
        f"Pump price direction follows wholesale with a short lag."
    )

    if mock_brent:
        parts.append(
            "⚠ Brent series is currently MOCK data — swap in a real feed before "
            "acting on the numbers."
        )

    return " ".join(parts)
