from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol

from .dataset import aggregate_daily_demand
from .schemas import DailySkuDemand, HeatDataset, HistoricalOrderLine, SKUStaticFeature


DATA_SOURCE_SYNTHETIC_DEMO = "synthetic_demo"
SPECIAL_SKUS = ("SKU-TREND-UP", "SKU-TREND-DOWN", "SKU-STABLE", "SKU-SPARSE")
DEMO_START_DATE = date(2026, 1, 1)


class SkuDemandProvider(Protocol):
    data_source: str
    coverage_start: date
    coverage_end: date

    def get_sku_static_info(self) -> list[SKUStaticFeature]: ...

    def get_historical_order_lines(self, as_of_date: date) -> list[HistoricalOrderLine]: ...

    def get_daily_sku_demand(self, as_of_date: date) -> list[DailySkuDemand]: ...

    def build_dataset(self, as_of_date: date) -> HeatDataset: ...


class SyntheticDemoSkuDemandProvider:
    """Deterministic demo input; it is never presented as WMS production data."""

    data_source = DATA_SOURCE_SYNTHETIC_DEMO

    def __init__(self, days: int = 90, sku_count: int = 100, seed: int = 42) -> None:
        if days <= 0:
            raise ValueError("days must be greater than 0")
        if sku_count < len(SPECIAL_SKUS):
            raise ValueError(f"sku_count must be at least {len(SPECIAL_SKUS)}")
        self.days = days
        self.sku_count = sku_count
        self.seed = seed
        self.coverage_start = DEMO_START_DATE
        self.coverage_end = DEMO_START_DATE + timedelta(days=days - 1)
        self._dataset = _generate_dataset(days, sku_count, seed)

    def get_sku_static_info(self) -> list[SKUStaticFeature]:
        return list(self._dataset.sku_master)

    def get_historical_order_lines(self, as_of_date: date) -> list[HistoricalOrderLine]:
        return [line for line in self._dataset.order_lines if line.created_at.date() <= as_of_date]

    def get_daily_sku_demand(self, as_of_date: date) -> list[DailySkuDemand]:
        return aggregate_daily_demand(self.get_historical_order_lines(as_of_date))

    def build_dataset(self, as_of_date: date) -> HeatDataset:
        return HeatDataset(
            start_date=self.coverage_start,
            end_date=self.coverage_end,
            sku_master=self.get_sku_static_info(),
            order_lines=self.get_historical_order_lines(as_of_date),
        )


def _generate_dataset(days: int, sku_count: int, seed: int) -> HeatDataset:
    rng = random.Random(seed)
    sku_ids = list(SPECIAL_SKUS) + [f"SKU-{index:05d}" for index in range(1, sku_count - len(SPECIAL_SKUS) + 1)]
    categories = ("fastener", "electrical", "hydraulic", "bearing", "panel")
    sku_master = [
        SKUStaticFeature(
            sku_id=sku_id,
            is_standard_part=index % 3 != 0,
            category=categories[index % len(categories)],
            weight=round(0.1 + rng.random() * 49.9, 3),
            volume=round(0.001 + rng.random() * 0.499, 4),
        )
        for index, sku_id in enumerate(sku_ids)
    ]
    order_lines: list[HistoricalOrderLine] = []
    sequence = 1
    for day_index in range(days):
        demand_date = DEMO_START_DATE + timedelta(days=day_index)
        for sku_index, sku_id in enumerate(sku_ids):
            line_count = _daily_line_count(sku_id, sku_index, len(sku_ids), day_index, days, rng)
            for line_index in range(line_count):
                quantity = _quantity_for_sku(sku_id, sku_index, rng)
                created_at = datetime.combine(
                    demand_date,
                    time(
                        hour=8 + sequence % 10,
                        minute=(sequence * 7 + line_index * 11) % 60,
                        tzinfo=timezone.utc,
                    ),
                )
                order_lines.append(
                    HistoricalOrderLine(
                        order_id=f"ORDER-DEMO-{sequence:06d}",
                        order_line_id=f"ORDER-DEMO-{sequence:06d}-L{line_index + 1}",
                        sku_id=sku_id,
                        quantity=quantity,
                        created_at=created_at,
                        completed_at=created_at + timedelta(minutes=25 + rng.randint(0, 180)),
                        customer_industry=rng.choice(list(SKU_INDUSTRIES)),
                        priority=rng.choices([0, 1, 2, 3], weights=[1, 5, 8, 3], k=1)[0],
                    )
                )
                sequence += 1
    return HeatDataset(
        start_date=DEMO_START_DATE,
        end_date=DEMO_START_DATE + timedelta(days=days - 1),
        order_lines=order_lines,
        sku_master=sku_master,
    )


SKU_INDUSTRIES = ("automotive", "construction_machinery", "construction", "electronics", "other")


def _daily_line_count(
    sku_id: str,
    sku_index: int,
    sku_count: int,
    day_index: int,
    days: int,
    rng: random.Random,
) -> int:
    progress = day_index / max(days - 1, 1)
    if sku_id == "SKU-TREND-UP":
        if progress < 0.33:
            return 1 if day_index % 6 == 0 else 0
        if progress < 0.66:
            return 1 if day_index % 2 == 0 else 0
        return 2 + (1 if day_index % 3 == 0 else 0)
    if sku_id == "SKU-TREND-DOWN":
        if progress < 0.33:
            return 2 + (1 if day_index % 3 == 0 else 0)
        if progress < 0.66:
            return 1 if day_index % 2 == 0 else 0
        return 1 if day_index % 7 == 0 else 0
    if sku_id == "SKU-STABLE":
        return 1 + (1 if day_index % 10 == 0 else 0)
    if sku_id == "SKU-SPARSE":
        return 1 if day_index in {0, days // 3, (days * 2) // 3, days - 1} else 0
    generated_position = (sku_index - len(SPECIAL_SKUS)) / max(sku_count - len(SPECIAL_SKUS), 1)
    if generated_position < 0.2:
        return 1 + (1 if rng.random() < 0.35 else 0)
    if generated_position < 0.5:
        return 1 if rng.random() < 0.45 else 0
    return 1 if rng.random() < 0.12 else 0


def _quantity_for_sku(sku_id: str, sku_index: int, rng: random.Random) -> int:
    if sku_id in {"SKU-TREND-UP", "SKU-TREND-DOWN"}:
        return rng.randint(2, 7)
    if sku_id == "SKU-STABLE":
        return rng.randint(1, 4)
    if sku_id == "SKU-SPARSE":
        return rng.randint(4, 12)
    return rng.randint(3, 10) if sku_index % 5 == 0 else rng.randint(1, 6)


sku_demand_provider = SyntheticDemoSkuDemandProvider()
