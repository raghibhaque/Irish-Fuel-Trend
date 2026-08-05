"""GET /api/prediction — trend + explanation for petrol & diesel."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.db import connection
from app.models import Prediction, PredictionFeatures, PredictionResponse
from app.prediction import explain as explain_mod
from app.prediction import model as model_mod

router = APIRouter(prefix="/api", tags=["prediction"])


def _brent_source() -> str:
    with connection() as conn:
        row = conn.execute(
            "SELECT source FROM brent_crude ORDER BY date DESC LIMIT 1"
        ).fetchone()
    return row["source"] if row else "unknown"


def _predict_for(fuel_type: str, mock_brent: bool) -> Prediction:
    pred = model_mod.train_and_predict(fuel_type)
    explanation = explain_mod.explain(pred, mock_brent=mock_brent)
    return Prediction(
        fuel_type=fuel_type,
        as_of=pred.as_of,
        trend=pred.trend,
        predicted_weekly_return=pred.predicted_weekly_return,
        confidence=pred.confidence,
        r2=pred.r2,
        r2_in_sample=pred.r2_in_sample,
        n_train=pred.n_train,
        product_symbol=pred.product_symbol,
        current_pump_eur_per_l=pred.current_pump_eur_per_l,
        predicted_pump_eur_per_l=pred.predicted_pump_eur_per_l,
        predicted_pump_low_eur_per_l=pred.predicted_pump_low_eur_per_l,
        predicted_pump_high_eur_per_l=pred.predicted_pump_high_eur_per_l,
        features=PredictionFeatures(**pred.features),
        explanation=explanation,
    )


@router.get("/prediction", response_model=PredictionResponse)
def get_prediction() -> PredictionResponse:
    brent_source = _brent_source()
    is_mock = brent_source.upper().startswith("MOCK")
    notes: list[str] = []
    if is_mock:
        notes.append(
            "Brent crude values are synthetic (source=" + brent_source + "). "
            "Predictions are structurally correct but not economically meaningful "
            "until a real Brent feed is wired up."
        )
    try:
        petrol = _predict_for("petrol", mock_brent=is_mock)
        diesel = _predict_for("diesel", mock_brent=is_mock)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return PredictionResponse(
        petrol=petrol,
        diesel=diesel,
        generated_at=datetime.now(timezone.utc),
        brent_source=brent_source,
        notes=notes,
    )
