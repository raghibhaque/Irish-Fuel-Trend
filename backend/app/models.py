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


class BacktestPoint(BaseModel):
    date: date
    actual_return: float
    predicted_return: float
    actual_pump_eur_per_l: float
    predicted_pump_eur_per_l: float
    direction_correct: bool


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
    predicted_pump_3w_eur_per_l: float
    backtest: list[BacktestPoint] = Field(default_factory=list)
    features: PredictionFeatures
    explanation: str


class PredictionResponse(BaseModel):
    petrol: Prediction
    diesel: Prediction
    generated_at: datetime
    brent_source: str
    notes: list[str] = Field(default_factory=list)


class CountyStation(BaseModel):
    name: str
    brand: Optional[str] = None
    price_eur_per_litre: float
    reported_at: Optional[str] = None      # ISO-8601 from upstream, kept verbatim


class CountyObservation(BaseModel):
    """One day's snapshot of a county median — the county history we build ourselves."""
    date: date
    median_eur_per_litre: float
    station_count: int


class CountyFuelSnapshot(BaseModel):
    fuel_type: Literal["petrol", "diesel"]
    crowd_median_eur_per_litre: float      # raw 30d crowd-sourced median
    station_count: int
    window_days: int                       # trailing window the median covers
    stale: bool                            # True when falling back to the wide window
    low_sample: bool                       # True below the station threshold
    basis_eur_per_litre: float             # shrunk offset vs the same-pool national ref
    basis_raw_eur_per_litre: float

    # Prediction fields are absent when the national model can't run — the page
    # still renders prices and history in that case.
    trend: Optional[Literal["up", "down", "flat"]] = None
    predicted_weekly_return: Optional[float] = None
    confidence: Optional[float] = None
    r2: Optional[float] = None
    current_pump_eur_per_l: Optional[float] = None
    predicted_pump_eur_per_l: Optional[float] = None
    predicted_pump_low_eur_per_l: Optional[float] = None
    predicted_pump_high_eur_per_l: Optional[float] = None
    predicted_pump_3w_eur_per_l: Optional[float] = None

    observations: list[CountyObservation] = Field(default_factory=list)
    stations: list[CountyStation] = Field(default_factory=list)


class CountyEntry(BaseModel):
    county: str
    petrol: Optional[CountyFuelSnapshot] = None
    diesel: Optional[CountyFuelSnapshot] = None


class NationalReference(BaseModel):
    """Station-weighted mean of county medians — the basis is measured against this."""
    fuel_type: Literal["petrol", "diesel"]
    window_days: int
    median_eur_per_litre: float
    station_count: int


class CountiesResponse(BaseModel):
    generated_at: datetime
    snapshot_date: date
    counties: list[CountyEntry]
    national_reference: list[NationalReference]
    counties_without_data: list[str] = Field(default_factory=list)
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
