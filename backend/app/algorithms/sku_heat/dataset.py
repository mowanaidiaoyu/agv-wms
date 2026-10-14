from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from .schemas import DailySkuDemand, HistoricalOrderLine


@dataclass
class DemandAccumulator:
    order_line_count: int = 0
    order_ids: set[str] = field(default_factory=set)
    outbound_quantity: int = 0


def aggregate_daily_demand(order_lines: Iterable[HistoricalOrderLine]) -> list[DailySkuDemand]:
    buckets: dict[tuple[date, str], DemandAccumulator] = {}
    for line in order_lines:
        key = (line.created_at.date(), line.sku_id)
        accumulator = buckets.setdefault(key, DemandAccumulator())
        accumulator.order_line_count += 1
        accumulator.order_ids.add(line.order_id)
        accumulator.outbound_quantity += line.quantity

    return [
        DailySkuDemand(
            date=demand_date,
            sku_id=sku_id,
            order_line_count=accumulator.order_line_count,
            order_count=len(accumulator.order_ids),
            outbound_quantity=accumulator.outbound_quantity,
        )
        for (demand_date, sku_id), accumulator in sorted(buckets.items(), key=lambda item: item[0])
    ]
