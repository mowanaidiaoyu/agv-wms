from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ABCClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class CustomerIndustry(StrEnum):
    automotive = "automotive"
    construction_machinery = "construction_machinery"
    construction = "construction"
    electronics = "electronics"
    other = "other"


class HistoricalOrderLine(BaseModel):
    order_id: str = Field(min_length=1)
    order_line_id: str = Field(min_length=1)
    sku_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    created_at: datetime
    completed_at: datetime | None = None
    customer_industry: CustomerIndustry = CustomerIndustry.other
    priority: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def validate_completion_window(self) -> "HistoricalOrderLine":
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("completed_at must be greater than or equal to created_at")
        return self


class SKUStaticFeature(BaseModel):
    sku_id: str = Field(min_length=1)
    is_standard_part: bool
    category: str = Field(min_length=1)
    weight: float | None = Field(default=None, ge=0.0)
    volume: float | None = Field(default=None, ge=0.0)


class DailySkuDemand(BaseModel):
    date: date
    sku_id: str = Field(min_length=1)
    order_line_count: int = Field(ge=0)
    order_count: int = Field(ge=0)
    outbound_quantity: int = Field(ge=0)


class HeatDataset(BaseModel):
    start_date: date
    end_date: date
    order_lines: list[HistoricalOrderLine]
    sku_master: list[SKUStaticFeature]

    @model_validator(mode="after")
    def validate_date_window(self) -> "HeatDataset":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        return self


class ABCConfig(BaseModel):
    lookback_days: int = Field(default=30, gt=0)
    frequency_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    quantity_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    order_line_weight: float = Field(default=0.2, ge=0.0, le=1.0)
    a_cumulative_share: float = Field(default=0.70, gt=0.0, lt=1.0)
    b_cumulative_share: float = Field(default=0.90, gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def validate_weights_and_thresholds(self) -> "ABCConfig":
        if abs(self.frequency_weight + self.quantity_weight + self.order_line_weight - 1.0) > 1e-9:
            raise ValueError("ABC feature weights must sum to 1")
        if not self.a_cumulative_share < self.b_cumulative_share:
            raise ValueError("ABC thresholds must satisfy a_cumulative_share < b_cumulative_share")
        return self


class ABCFeatureVector(BaseModel):
    sku_id: str
    outbound_frequency: int = Field(ge=0)
    outbound_quantity: int = Field(ge=0)
    order_line_count: int = Field(ge=0)
    frequency_normalized: float = Field(ge=0.0, le=1.0)
    quantity_normalized: float = Field(ge=0.0, le=1.0)
    order_line_normalized: float = Field(ge=0.0, le=1.0)
    score: float = Field(ge=0.0, le=100.0)


class SkuClassificationItem(BaseModel):
    sku_id: str
    abc_class: ABCClass
    score: float = Field(ge=0.0, le=100.0)
    updated_at: datetime


class ABCClassificationResult(BaseModel):
    algorithm_version: str
    as_of_date: date
    lookback_start_date: date
    lookback_end_date: date
    config: ABCConfig
    features: list[ABCFeatureVector]
    classification: list[SkuClassificationItem]


class HeatForecastMethod(StrEnum):
    moving_average = "moving_average"
    weighted_moving_average = "weighted_moving_average"


class HeatForecastConfig(BaseModel):
    method: HeatForecastMethod = HeatForecastMethod.weighted_moving_average
    lookback_days: int = Field(default=7, gt=0)
    horizon_hours: Literal[24] = 24


class HeatForecastItem(BaseModel):
    sku_id: str
    predicted_heat: float = Field(ge=0.0)


class HeatForecastResult(BaseModel):
    algorithm_version: str
    method: HeatForecastMethod
    as_of_date: date
    forecast_date: date
    lookback_start_date: date | None
    lookback_end_date: date | None
    config: HeatForecastConfig
    items: list[HeatForecastItem]


class ABCResultItem(ABCFeatureVector):
    abc_class: ABCClass
    updated_at: datetime


class ABCResponse(BaseModel):
    algorithm_version: str
    data_source: Literal["synthetic_demo"]
    as_of_date: date
    lookback_start_date: date
    lookback_end_date: date
    config: ABCConfig
    items: list[ABCResultItem]


class HeatPredictionRequest(BaseModel):
    sku_ids: list[str] = Field(min_length=1)
    as_of_date: date | None = None
    method: HeatForecastMethod = HeatForecastMethod.weighted_moving_average
    lookback_days: int = Field(default=7, gt=0)
    horizon_hours: Literal[24] = 24

    @field_validator("sku_ids")
    @classmethod
    def normalize_sku_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for sku_id in value:
            cleaned = sku_id.strip()
            if not cleaned:
                raise ValueError("sku_ids must not contain blank values")
            if cleaned not in normalized:
                normalized.append(cleaned)
        return normalized


class HeatPredictionResponse(BaseModel):
    algorithm_version: str
    data_source: Literal["synthetic_demo"]
    as_of_date: date
    forecast_date: date
    lookback_start_date: date | None
    lookback_end_date: date | None
    method: HeatForecastMethod
    lookback_days: int
    horizon_hours: Literal[24]
    predictions: list[HeatForecastItem]
