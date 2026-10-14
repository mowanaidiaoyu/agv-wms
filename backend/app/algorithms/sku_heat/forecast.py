from __future__ import annotations

from datetime import date, timedelta

from .dataset import aggregate_daily_demand
from .schemas import (
    DailySkuDemand,
    HeatDataset,
    HeatForecastConfig,
    HeatForecastItem,
    HeatForecastMethod,
    HeatForecastResult,
)


FORECAST_ALGORITHM_VERSION = "heat-baseline-v1.0.0"


def forecast_heat(
    dataset: HeatDataset,
    as_of_date: date,
    config: HeatForecastConfig | None = None,
) -> HeatForecastResult:
    active_config = config or HeatForecastConfig()
    effective_start, effective_end = resolve_effective_window(dataset, as_of_date, active_config.lookback_days)
    daily_demand = aggregate_daily_demand(dataset.order_lines)
    sku_ids = sorted({sku.sku_id for sku in dataset.sku_master})
    items = [
        HeatForecastItem(
            sku_id=sku_id,
            predicted_heat=_forecast_series(
                build_daily_demand_series(daily_demand, sku_id, effective_start, effective_end),
                active_config.method,
            ),
        )
        for sku_id in sku_ids
    ]
    return HeatForecastResult(
        algorithm_version=FORECAST_ALGORITHM_VERSION,
        method=active_config.method,
        as_of_date=as_of_date,
        forecast_date=as_of_date + timedelta(days=1),
        lookback_start_date=effective_start,
        lookback_end_date=effective_end,
        config=active_config,
        items=items,
    )


def resolve_effective_window(
    dataset: HeatDataset,
    as_of_date: date,
    lookback_days: int,
) -> tuple[date | None, date | None]:
    requested_start = as_of_date - timedelta(days=lookback_days - 1)
    effective_start = max(requested_start, dataset.start_date)
    effective_end = min(as_of_date, dataset.end_date)
    if effective_start > effective_end:
        return None, None
    return effective_start, effective_end


def build_daily_demand_series(
    daily_demand: list[DailySkuDemand],
    sku_id: str,
    start_date: date | None,
    end_date: date | None,
) -> list[float]:
    if start_date is None or end_date is None or start_date > end_date:
        return []
    demand_by_date = {item.date: item.outbound_quantity for item in daily_demand if item.sku_id == sku_id}
    day_count = (end_date - start_date).days + 1
    return [float(demand_by_date.get(start_date + timedelta(days=offset), 0)) for offset in range(day_count)]


def simple_moving_average(series: list[float]) -> float:
    if not series:
        return 0.0
    return sum(series) / len(series)


def weighted_moving_average(series: list[float]) -> float:
    if not series:
        return 0.0
    weights = list(range(1, len(series) + 1))
    return sum(value * weight for value, weight in zip(series, weights, strict=True)) / sum(weights)


def _forecast_series(series: list[float], method: HeatForecastMethod) -> float:
    if method == HeatForecastMethod.moving_average:
        return simple_moving_average(series)
    if method == HeatForecastMethod.weighted_moving_average:
        return weighted_moving_average(series)
    raise ValueError(f"Unsupported heat forecast method: {method}")
