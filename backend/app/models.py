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
    brent_eur_ret_2w: float
    brent_eur_ret_6w: float
    product_eur_ret_1w: float
    product_eur_ret_4w: float
    crack_spread_eur: float          # kept for display only, not a model feature
    crack_spread_ret_4w: float
    prev_wholesale_return: float
    brent_eur_per_bbl_current: float
    brent_eur_per_bbl_lag1: float
    product_eur_per_gal_current: float
    latest_wholesale_eur_per_l: float


class Prediction(BaseModel):
    fuel_type: Literal["petrol", "diesel"]
    as_of: date
    trend: Literal["up", "down", "flat"]
    predicted_weekly_return: float
    confidence: float = Field(ge=0.0, le=1.0)
    r2: float                    # walk-forward out-of-sample R² (can be negative)
    r2_in_sample: float
    n_train: int
    product_symbol: str          # 'RBOB' | 'ULSD'
    current_pump_eur_per_l: float
    predicted_pump_eur_per_l: float
    predicted_pump_low_eur_per_l: float
    predicted_pump_high_eur_per_l: float
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
