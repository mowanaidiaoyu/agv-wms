from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from ..algorithms.sku_heat.abc import classify_dynamic_abc
from ..algorithms.sku_heat.forecast import forecast_heat
from ..algorithms.sku_heat.provider import sku_demand_provider
from ..algorithms.sku_heat.schemas import (
    ABCConfig,
    ABCResponse,
    ABCResultItem,
    HeatForecastConfig,
    HeatPredictionRequest,
    HeatPredictionResponse,
)


router = APIRouter(prefix="/algorithm/sku", tags=["sku-algorithm"])


def _resolve_as_of_date(requested: date | None) -> date:
    active_date = requested or sku_demand_provider.coverage_end
    if active_date > sku_demand_provider.coverage_end:
        raise HTTPException(
            status_code=422,
            detail=f"as_of_date exceeds {sku_demand_provider.data_source} coverage end "
            f"{sku_demand_provider.coverage_end.isoformat()}",
        )
    return active_date


@router.get("/abc", response_model=ABCResponse)
def get_dynamic_abc(
    as_of_date: date | None = None,
    lookback_days: int = Query(default=30, gt=0),
) -> ABCResponse:
    active_date = _resolve_as_of_date(as_of_date)
    result = classify_dynamic_abc(
        sku_demand_provider.build_dataset(active_date),
        active_date,
        ABCConfig(lookback_days=lookback_days),
    )
    classes = {item.sku_id: item for item in result.classification}
    return ABCResponse(
        algorithm_version=result.algorithm_version,
        data_source=sku_demand_provider.data_source,
        as_of_date=result.as_of_date,
        lookback_start_date=result.lookback_start_date,
        lookback_end_date=result.lookback_end_date,
        config=result.config,
        items=[
            ABCResultItem(
                **feature.model_dump(),
                abc_class=classes[feature.sku_id].abc_class,
                updated_at=classes[feature.sku_id].updated_at,
            )
            for feature in result.features
        ],
    )


@router.post("/heat/predict", response_model=HeatPredictionResponse)
def predict_sku_heat(payload: HeatPredictionRequest) -> HeatPredictionResponse:
    active_date = _resolve_as_of_date(payload.as_of_date)
    known_skus = {sku.sku_id for sku in sku_demand_provider.get_sku_static_info()}
    unknown_skus = [sku_id for sku_id in payload.sku_ids if sku_id not in known_skus]
    if unknown_skus:
        raise HTTPException(status_code=422, detail=f"unknown sku_ids: {', '.join(unknown_skus)}")
    result = forecast_heat(
        sku_demand_provider.build_dataset(active_date),
        active_date,
        HeatForecastConfig(
            method=payload.method,
            lookback_days=payload.lookback_days,
            horizon_hours=payload.horizon_hours,
        ),
    )
    predictions = {item.sku_id: item for item in result.items}
    return HeatPredictionResponse(
        algorithm_version=result.algorithm_version,
        data_source=sku_demand_provider.data_source,
        as_of_date=result.as_of_date,
        forecast_date=result.forecast_date,
        lookback_start_date=result.lookback_start_date,
        lookback_end_date=result.lookback_end_date,
        method=result.method,
        lookback_days=result.config.lookback_days,
        horizon_hours=result.config.horizon_hours,
        predictions=[predictions[sku_id] for sku_id in payload.sku_ids],
    )
