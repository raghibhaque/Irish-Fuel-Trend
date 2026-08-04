"""Human-readable explanation for a TrendPrediction.

Templated, deterministic. No LLM. Keeps the "why" easy to audit and change.
"""
from __future__ import annotations

from app.prediction.model import TrendPrediction


TREND_HEAD = {
    "up":   "Prices likely to rise over the next 1–3 weeks.",
    "down": "Prices likely to fall over the next 1–3 weeks.",
    "flat": "Prices likely to hold roughly steady over the next 1–3 weeks.",
}


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def explain(pred: TrendPrediction, mock_brent: bool = True) -> str:
    f = pred.features
    parts = [TREND_HEAD[pred.trend]]

    parts.append(
        f"Brent crude {_pct(f['brent_ret_2w'])} over the prior 2 weeks "
        f"(from ${f['brent_lag2_usd_per_bbl']:.2f} to ${f['brent_lag1_usd_per_bbl']:.2f}/bbl)."
    )
    parts.append(
        f"EUR/USD {_pct(f['eur_usd_ret_2w'])} over the same window "
        f"(from {f['eur_usd_lag2']:.4f} to {f['eur_usd_lag1']:.4f})."
    )

    # Placeholder — tax calendar not wired yet
    parts.append("No Irish excise/carbon-tax changes flagged in the window.")

    parts.append(
        f"Model estimate: {_pct(pred.predicted_weekly_return)} weekly change, "
        f"confidence {pred.confidence:.0%}, in-sample R² {pred.r2:.2f}, "
        f"trained on {pred.n_train} weeks."
    )

    if mock_brent:
        parts.append(
            "⚠ Brent series is currently MOCK data — swap in a real feed before "
            "acting on the numbers."
        )

    return " ".join(parts)
