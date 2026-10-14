from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from .schemas import (
    ABCClass,
    ABCClassificationResult,
    ABCConfig,
    ABCFeatureVector,
    HeatDataset,
    SkuClassificationItem,
)


ABC_ALGORITHM_VERSION = "abc-v1.0.0"


@dataclass
class RawSkuDemand:
    order_ids: set[str] = field(default_factory=set)
    outbound_quantity: int = 0
    order_line_count: int = 0

    @property
    def outbound_frequency(self) -> int:
        return len(self.order_ids)


def classify_dynamic_abc(
    dataset: HeatDataset,
    as_of_date: date,
    config: ABCConfig | None = None,
) -> ABCClassificationResult:
    active_config = config or ABCConfig()
    lookback_end_date = as_of_date
    lookback_start_date = as_of_date - timedelta(days=active_config.lookback_days - 1)
    sku_ids = sorted({sku.sku_id for sku in dataset.sku_master})
    raw_by_sku = {sku_id: RawSkuDemand() for sku_id in sku_ids}

    for line in dataset.order_lines:
        line_date = line.created_at.date()
        if line.sku_id not in raw_by_sku or line_date < lookback_start_date or line_date > lookback_end_date:
            continue
        raw = raw_by_sku[line.sku_id]
        raw.order_ids.add(line.order_id)
        raw.outbound_quantity += line.quantity
        raw.order_line_count += 1

    frequencies = [raw.outbound_frequency for raw in raw_by_sku.values()]
    quantities = [raw.outbound_quantity for raw in raw_by_sku.values()]
    order_line_counts = [raw.order_line_count for raw in raw_by_sku.values()]
    features = sorted(
        (
            _build_feature_vector(
                sku_id,
                raw_by_sku[sku_id],
                frequencies,
                quantities,
                order_line_counts,
                active_config,
            )
            for sku_id in sku_ids
        ),
        key=lambda item: (-item.score, item.sku_id),
    )
    return ABCClassificationResult(
        algorithm_version=ABC_ALGORITHM_VERSION,
        as_of_date=as_of_date,
        lookback_start_date=lookback_start_date,
        lookback_end_date=lookback_end_date,
        config=active_config,
        features=features,
        classification=_classify_features(features, active_config, as_of_date),
    )


def _build_feature_vector(
    sku_id: str,
    raw: RawSkuDemand,
    frequencies: list[int],
    quantities: list[int],
    order_line_counts: list[int],
    config: ABCConfig,
) -> ABCFeatureVector:
    frequency_normalized = _min_max_normalize(raw.outbound_frequency, frequencies)
    quantity_normalized = _min_max_normalize(raw.outbound_quantity, quantities)
    order_line_normalized = _min_max_normalize(raw.order_line_count, order_line_counts)
    score = 100 * (
        config.frequency_weight * frequency_normalized
        + config.quantity_weight * quantity_normalized
        + config.order_line_weight * order_line_normalized
    )
    return ABCFeatureVector(
        sku_id=sku_id,
        outbound_frequency=raw.outbound_frequency,
        outbound_quantity=raw.outbound_quantity,
        order_line_count=raw.order_line_count,
        frequency_normalized=round(frequency_normalized, 6),
        quantity_normalized=round(quantity_normalized, 6),
        order_line_normalized=round(order_line_normalized, 6),
        score=round(score, 6),
    )


def _min_max_normalize(value: int, values: list[int]) -> float:
    min_value = min(values, default=0)
    max_value = max(values, default=0)
    if max_value == min_value:
        return 0.0
    return (value - min_value) / (max_value - min_value)


def _classify_features(
    features: list[ABCFeatureVector],
    config: ABCConfig,
    as_of_date: date,
) -> list[SkuClassificationItem]:
    total_score = sum(item.score for item in features)
    updated_at = datetime.combine(as_of_date, time.min, tzinfo=timezone.utc)
    if total_score == 0:
        return [
            SkuClassificationItem(sku_id=item.sku_id, abc_class=ABCClass.C, score=item.score, updated_at=updated_at)
            for item in features
        ]

    classification: list[SkuClassificationItem] = []
    cumulative_score = 0.0
    for item in features:
        cumulative_share_before = cumulative_score / total_score
        if cumulative_share_before < config.a_cumulative_share:
            abc_class = ABCClass.A
        elif cumulative_share_before < config.b_cumulative_share:
            abc_class = ABCClass.B
        else:
            abc_class = ABCClass.C
        classification.append(
            SkuClassificationItem(
                sku_id=item.sku_id,
                abc_class=abc_class,
                score=item.score,
                updated_at=updated_at,
            )
        )
        cumulative_score += item.score
    return classification
