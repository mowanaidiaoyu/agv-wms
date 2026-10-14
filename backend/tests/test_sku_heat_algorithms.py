from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.algorithms.sku_heat.abc import classify_dynamic_abc
from app.algorithms.sku_heat.dataset import aggregate_daily_demand
from app.algorithms.sku_heat.forecast import (
    build_daily_demand_series,
    forecast_heat,
    simple_moving_average,
    weighted_moving_average,
)
from app.algorithms.sku_heat.provider import SyntheticDemoSkuDemandProvider
from app.algorithms.sku_heat.schemas import (
    ABCClass,
    ABCConfig,
    CustomerIndustry,
    HeatDataset,
    HeatForecastConfig,
    HeatForecastMethod,
    HistoricalOrderLine,
    SKUStaticFeature,
)


def order_line(order_id: str, sku_id: str, quantity: int, demand_date: date, suffix: int = 1):
    created_at = datetime.combine(demand_date, datetime.min.time(), tzinfo=timezone.utc)
    return HistoricalOrderLine(
        order_id=order_id,
        order_line_id=f"{order_id}-L{suffix}",
        sku_id=sku_id,
        quantity=quantity,
        created_at=created_at,
        completed_at=created_at + timedelta(minutes=30),
        customer_industry=CustomerIndustry.automotive,
        priority=1,
    )


def dataset(
    sku_ids: list[str],
    lines: list[HistoricalOrderLine],
    start_date: date | None = None,
    end_date: date | None = None,
) -> HeatDataset:
    effective_start = start_date or min((line.created_at.date() for line in lines), default=date(2026, 1, 1))
    effective_end = end_date or max((line.created_at.date() for line in lines), default=effective_start)
    return HeatDataset(
        start_date=effective_start,
        end_date=effective_end,
        order_lines=lines,
        sku_master=[
            SKUStaticFeature(
                sku_id=sku_id,
                is_standard_part=True,
                category="test",
                weight=1,
                volume=0.01,
            )
            for sku_id in sku_ids
        ],
    )


def test_daily_demand_aggregation_counts_orders_lines_and_quantity():
    created = date(2026, 1, 1)
    result = aggregate_daily_demand([
        order_line("ORDER-1", "SKU-A", 2, created, 1),
        order_line("ORDER-1", "SKU-A", 3, created, 2),
        order_line("ORDER-2", "SKU-A", 5, created, 1),
    ])
    assert len(result) == 1
    assert result[0].model_dump() == {
        "date": created,
        "sku_id": "SKU-A",
        "order_line_count": 3,
        "order_count": 2,
        "outbound_quantity": 10,
    }


def test_abc_min_max_normalization_matches_legacy_formula():
    sample = dataset(
        ["SKU-A", "SKU-B", "SKU-C"],
        [
            order_line("ORDER-A", "SKU-A", 10, date(2026, 1, 1)),
            order_line("ORDER-B", "SKU-B", 5, date(2026, 1, 1)),
        ],
    )
    result = classify_dynamic_abc(
        sample,
        date(2026, 1, 1),
        ABCConfig(frequency_weight=0, quantity_weight=1, order_line_weight=0),
    )
    features = {item.sku_id: item for item in result.features}
    assert features["SKU-A"].quantity_normalized == 1
    assert features["SKU-B"].quantity_normalized == 0.5
    assert features["SKU-C"].quantity_normalized == 0


def test_abc_same_feature_values_normalize_to_zero():
    sample = dataset(
        ["SKU-A", "SKU-B"],
        [
            order_line("ORDER-A", "SKU-A", 5, date(2026, 1, 1)),
            order_line("ORDER-B", "SKU-B", 5, date(2026, 1, 1)),
        ],
    )
    result = classify_dynamic_abc(sample, date(2026, 1, 1))
    assert all(item.score == 0 for item in result.features)
    assert {item.abc_class for item in result.classification} == {ABCClass.C}


def test_abc_cumulative_threshold_boundaries_match_legacy_behavior():
    sample = dataset(
        ["SKU-A", "SKU-B", "SKU-C"],
        [
            order_line("ORDER-A", "SKU-A", 100, date(2026, 1, 1)),
            order_line("ORDER-B", "SKU-B", 50, date(2026, 1, 1)),
            order_line("ORDER-C", "SKU-C", 25, date(2026, 1, 1)),
        ],
    )
    config = ABCConfig(
        frequency_weight=0,
        quantity_weight=1,
        order_line_weight=0,
        a_cumulative_share=0.5,
        b_cumulative_share=0.8,
    )
    result = classify_dynamic_abc(sample, date(2026, 1, 1), config)
    assert [(item.sku_id, item.abc_class) for item in result.classification] == [
        ("SKU-A", ABCClass.A),
        ("SKU-B", ABCClass.B),
        ("SKU-C", ABCClass.C),
    ]


def test_abc_lookback_and_as_of_exclude_old_and_future_lines():
    sample = dataset(
        ["SKU-A", "SKU-B"],
        [
            order_line("OLD", "SKU-A", 1000, date(2026, 1, 1)),
            order_line("CURRENT", "SKU-B", 10, date(2026, 1, 31)),
            order_line("FUTURE", "SKU-A", 1000, date(2026, 2, 1)),
        ],
        date(2026, 1, 1),
        date(2026, 2, 1),
    )
    result = classify_dynamic_abc(sample, date(2026, 1, 31), ABCConfig(lookback_days=7))
    features = {item.sku_id: item for item in result.features}
    assert features["SKU-A"].outbound_quantity == 0
    assert features["SKU-B"].outbound_quantity == 10
    assert result.lookback_start_date == date(2026, 1, 25)


def test_abc_config_rejects_changed_weight_or_threshold_contract():
    with pytest.raises(ValidationError):
        ABCConfig(frequency_weight=0.5, quantity_weight=0.5, order_line_weight=0.5)
    with pytest.raises(ValidationError):
        ABCConfig(a_cumulative_share=0.9, b_cumulative_share=0.8)


def test_sma_and_wma_match_legacy_sparse_series():
    assert simple_moving_average([10, 0, 20]) == pytest.approx(10)
    assert weighted_moving_average([10, 0, 20]) == pytest.approx(70 / 6)


def test_missing_days_inside_coverage_are_zero_filled():
    sample = dataset(
        ["SKU-A"],
        [
            order_line("ORDER-1", "SKU-A", 10, date(2026, 1, 1)),
            order_line("ORDER-2", "SKU-A", 20, date(2026, 1, 3)),
        ],
        date(2026, 1, 1),
        date(2026, 1, 3),
    )
    daily = aggregate_daily_demand(sample.order_lines)
    assert build_daily_demand_series(daily, "SKU-A", sample.start_date, sample.end_date) == [10, 0, 20]
    result = forecast_heat(
        sample,
        date(2026, 1, 3),
        HeatForecastConfig(method=HeatForecastMethod.moving_average, lookback_days=7),
    )
    assert result.items[0].predicted_heat == pytest.approx(10)


def test_cold_start_sku_and_empty_effective_window_return_zero():
    sample = dataset(["SKU-COLD"], [], date(2026, 1, 10), date(2026, 1, 12))
    result = forecast_heat(sample, date(2026, 1, 9))
    assert result.lookback_start_date is None
    assert result.lookback_end_date is None
    assert result.items[0].predicted_heat == 0


@pytest.mark.parametrize("method", list(HeatForecastMethod))
def test_future_data_never_changes_historical_forecast(method: HeatForecastMethod):
    base = dataset(
        ["SKU-A"],
        [order_line("CURRENT", "SKU-A", 5, date(2026, 1, 9))],
        date(2026, 1, 1),
        date(2026, 1, 9),
    )
    with_future = HeatDataset(
        start_date=base.start_date,
        end_date=date(2026, 1, 10),
        sku_master=base.sku_master,
        order_lines=base.order_lines + [order_line("FUTURE", "SKU-A", 100000, date(2026, 1, 10))],
    )
    config = HeatForecastConfig(method=method, lookback_days=7)
    assert forecast_heat(base, date(2026, 1, 9), config).items == forecast_heat(
        with_future,
        date(2026, 1, 9),
        config,
    ).items


def test_forecast_is_next_natural_day_and_deterministic():
    sample = dataset(["SKU-A"], [order_line("ORDER-1", "SKU-A", 5, date(2026, 1, 9))])
    first = forecast_heat(sample, date(2026, 1, 9))
    second = forecast_heat(sample, date(2026, 1, 9))
    assert first == second
    assert first.forecast_date == date(2026, 1, 10)


def test_synthetic_provider_is_explicit_deterministic_and_cutoff_aware():
    first = SyntheticDemoSkuDemandProvider(days=14, sku_count=8, seed=42)
    second = SyntheticDemoSkuDemandProvider(days=14, sku_count=8, seed=42)
    cutoff = first.coverage_start + timedelta(days=5)
    assert first.data_source == "synthetic_demo"
    assert first.build_dataset(cutoff).model_dump(mode="json") == second.build_dataset(cutoff).model_dump(mode="json")
    assert all(line.created_at.date() <= cutoff for line in first.get_historical_order_lines(cutoff))
    assert all(item.date <= cutoff for item in first.get_daily_sku_demand(cutoff))
