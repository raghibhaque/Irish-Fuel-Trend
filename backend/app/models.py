"""Pydantic response models for the API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PricePoint(BaseModel):
    date: date
    price_eur_per_litre: float


class FuelPriceSeries(BaseModel):
    country: str = "IE"
    fuel_type: Literal["petrol", "diesel"]
    unit: str = "EUR/L"
    points: list[PricePoint]
    latest: Optional[PricePoint] = None


class PricesResponse(BaseModel):
    petrol: FuelPriceSeries
    diesel: FuelPriceSeries


class PredictionFeatures(BaseModel):
    brent_ret_2w: float
    eur_usd_ret_2w: float
    brent_lag1_usd_per_bbl: float
    brent_lag2_usd_per_bbl: float
    eur_usd_lag1: float
    eur_usd_lag2: float
    latest_price_eur_per_l: float


class Prediction(BaseModel):
    fuel_type: Literal["petrol", "diesel"]
    as_of: date
    trend: Literal["up", "down", "flat"]
    predicted_weekly_return: float
    confidence: float = Field(ge=0.0, le=1.0)
    r2: float
    n_train: int
    features: PredictionFeatures
    explanation: str


class PredictionResponse(BaseModel):
    petrol: Prediction
    diesel: Prediction
    generated_at: datetime
    brent_source: str
    notes: list[str] = Field(default_factory=list)


class NewsItem(BaseModel):
    published_at: datetime
    source: str
    title: str
    url: str
    summary: Optional[str] = None
    matched_keywords: Optional[str] = None


class NewsResponse(BaseModel):
    items: list[NewsItem]
