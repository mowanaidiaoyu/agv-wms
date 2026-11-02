from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable


class PackagingSpecificationError(ValueError):
    """The catalog cannot provide a verified case size for an order line."""


FORMAL_PACKAGING_STATUSES = frozenset({"verified", "human_verified"})
FORMAL_INVENTORY_SOURCES = frozenset({"formal_inventory"})


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def catalog_by_sku(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("skuId")): item
        for item in catalog.get("skus", [])
        if isinstance(item, dict) and item.get("skuId")
    }


def verified_units_per_case(
    sku: dict[str, Any],
    allowed_statuses: frozenset[str] = FORMAL_PACKAGING_STATUSES,
) -> int:
    value = sku.get("unitsPerCase")
    source = str(sku.get("packagingSource") or "").strip()
    status = str(sku.get("verificationStatus") or "").strip()
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PackagingSpecificationError(
            f"SKU {sku.get('skuId')} has no valid unitsPerCase packaging specification"
        )
    if not source:
        raise PackagingSpecificationError(
            f"SKU {sku.get('skuId')} unitsPerCase is not accompanied by a verified packagingSource"
        )
    if status not in allowed_statuses:
        raise PackagingSpecificationError(
            f"SKU {sku.get('skuId')} packaging verificationStatus={status or 'missing'} is not allowed"
        )
    return value


def topology_inventory_balances(topology: dict[str, Any]) -> list[dict[str, Any]]:
    """Read only explicit, topology-owned stock; never synthesize SKU inventory.

    A storage node is accepted only when it carries an explicit SKU and quantity.
    A shuttle_dock is a transport/handoff device and is never inventory. Storage
    capacity is also never interpreted as stock. Only a storage node explicitly
    linked to a formal inventory record is eligible. Generated ``build_inventory``
    data is deliberately not consumed.
    """
    balances: list[dict[str, Any]] = []
    for node in topology.get("nodes", []):
        if node.get("type") != "storage":
            continue
        if node.get("inventorySource") != "formal_inventory" or not node.get("inventoryRecordId"):
            continue
        sku_id = str(node.get("skuId") or "").strip()
        quantity = node.get("quantity")
        if not sku_id or not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            continue
        side = str(node.get("sourceWarehouse") or node.get("warehouseSide") or "").lower()
        if side not in {"left", "right"}:
            continue
        balances.append({
            "sourceWarehouse": side,
            "sourceLocationId": str(node.get("id")),
            "skuId": sku_id,
            "quantityCases": quantity,
            "dataSource": "formal_inventory",
            "inventoryRecordId": str(node["inventoryRecordId"]),
            "verificationStatus": "verified",
        })
    return balances


def _available_cases(
    balances: Iterable[dict[str, Any]],
    *,
    warehouse: str,
    sku_id: str,
) -> list[dict[str, Any]]:
    return [
        balance
        for balance in balances
        if balance.get("sourceWarehouse") == warehouse
        and str(balance.get("skuId")) == sku_id
        and int(balance.get("quantityCases") or 0) > 0
    ]


def _source_locations(
    balances: list[dict[str, Any]],
    required_cases: int,
) -> list[str]:
    result: list[str] = []
    for balance in balances:
        take = min(required_cases - len(result), int(balance.get("quantityCases") or 0))
        result.extend([str(balance["sourceLocationId"])] * take)
        if len(result) == required_cases:
            break
    return result


def plan_order_fulfillment(
    order: dict[str, Any],
    catalog: dict[str, Any],
    inventory_balances: list[dict[str, Any]],
    *,
    allowed_packaging_statuses: frozenset[str] = FORMAL_PACKAGING_STATUSES,
    allowed_inventory_sources: frozenset[str] = FORMAL_INVENTORY_SOURCES,
) -> dict[str, Any]:
    """Build a non-mutating case/break-pack preparation plan for one order.

    The returned tasks are preparation contracts, not runnable SimulationService
    tasks. No stock is reserved or deducted and no authoritative handoff event is
    emitted here.
    """
    sku_catalog = catalog_by_sku(catalog)
    working_balances = deepcopy(inventory_balances)
    for balance in working_balances:
        source = str(balance.get("dataSource") or "")
        if source not in allowed_inventory_sources:
            raise ValueError(f"Inventory source {source or 'missing'} is not allowed for fulfillment planning")
        if balance.get("verificationStatus") not in {"verified", "scenario_verified"}:
            raise ValueError("Inventory balance is not verified")
    allocations: list[dict[str, Any]] = []
    all_tasks: list[dict[str, Any]] = []

    for line_index, line in enumerate(order.get("lines", []), start=1):
        if line.get("quantityBase") is None:
            continue
        sku_id = str(line.get("skuId") or "").strip()
        quantity_base = int(line.get("quantityBase") or 0)
        if quantity_base <= 0:
            raise ValueError(f"SKU {sku_id} quantityBase must be greater than zero")
        sku = sku_catalog.get(sku_id)
        if sku is None:
            raise PackagingSpecificationError(f"SKU {sku_id} does not exist in the product catalog")
        units_per_case = verified_units_per_case(sku, allowed_packaging_statuses)
        supplied_units = line.get("unitsPerCase")
        if supplied_units is not None and int(supplied_units) != units_per_case:
            raise PackagingSpecificationError(
                f"SKU {sku_id} unitsPerCase does not match the verified catalog value"
            )

        right_stock = _available_cases(working_balances, warehouse="right", sku_id=sku_id)
        left_stock = _available_cases(working_balances, warehouse="left", sku_id=sku_id)
        requested_full_cases, remainder_base = divmod(quantity_base, units_per_case)
        # The current physical contract assigns complete cases to the right
        # Shuttle warehouse and the final break-pack case to the left CTU
        # warehouse.  Explicit stock is still mandatory on each side; a missing
        # right case is never silently borrowed from another warehouse.
        right_cases = requested_full_cases
        left_source_cases = 1 if remainder_base else 0
        right_quantity_base = right_cases * units_per_case
        left_pick_base = remainder_base
        left_source_base = left_source_cases * units_per_case
        left_return_base = left_source_base - left_pick_base

        right_locations = _source_locations(right_stock, right_cases)
        left_locations = _source_locations(left_stock, left_source_cases)
        missing_right_cases = right_cases - len(right_locations)
        required_left_picks = [remainder_base] if remainder_base else []
        # No executable-looking partial plan is emitted: if one required source
        # case is missing, the unfulfilled quantity is explicit and the whole
        # allocation remains shortage.
        shortage_base = missing_right_cases * units_per_case + sum(required_left_picks[len(left_locations):])
        status = "ready" if shortage_base == 0 else "shortage"
        line_key = str(line.get("id") or f"line-{line_index}")
        right_tasks: list[dict[str, Any]] = []
        left_tasks: list[dict[str, Any]] = []

        # Partial stock never creates an executable-looking task. The shortage
        # remains an explicit plan that requires manual stock correction.
        if status == "ready":
            for case_index, location_id in enumerate(right_locations, start=1):
                task_id = f"fulfillment-{order['id']}-{line_key}-right-{case_index}"
                right_tasks.append({
                    "orderId": order["id"],
                    "taskId": task_id,
                    "cargoId": f"cargo-{order['id']}-{line_key}-{case_index}",
                    "skuId": sku_id,
                    "sourceWarehouse": "right",
                    "sourceLocationId": location_id,
                    "destinationHandoffId": "cell-r92-c48",
                    "quantityCases": 1,
                    "quantityBase": units_per_case,
                    "status": "waiting_pick",
                    "authoritative": False,
                })
            for left_index, location_id in enumerate(left_locations, start=1):
                is_partial = remainder_base > 0 and left_index == len(left_locations)
                pick_base = remainder_base if is_partial else units_per_case
                task_id = f"fulfillment-{order['id']}-{line_key}-left-{left_index}"
                left_tasks.append({
                    "orderId": order["id"],
                    "taskId": task_id,
                    "skuId": sku_id,
                    "sourceWarehouse": "left",
                    "sourceLocationId": location_id,
                    "sourceQuantityBase": units_per_case,
                    "pickQuantityBase": pick_base,
                    "returnQuantityBase": units_per_case - pick_base,
                    "status": "waiting_split",
                    "authoritative": False,
                })

            # Consume only the planner's private snapshot so several order lines
            # and orders cannot allocate the same case. No caller data is changed.
            for warehouse, locations in (("right", right_locations), ("left", left_locations)):
                for location_id in locations:
                    balance = next(
                        item for item in working_balances
                        if item["sourceWarehouse"] == warehouse
                        and item["sourceLocationId"] == location_id
                        and item["skuId"] == sku_id
                        and int(item.get("quantityCases") or 0) > 0
                    )
                    balance["quantityCases"] = int(balance["quantityCases"]) - 1

        allocation = {
            "orderId": order["id"],
            "lineId": line_key,
            "skuId": sku_id,
            "quantityBase": quantity_base,
            "unitsPerCase": units_per_case,
            "rightQuantityCases": right_cases,
            "rightQuantityBase": right_quantity_base,
            "leftSourceCases": left_source_cases,
            "leftSourceQuantityBase": left_source_base,
            "leftPickQuantityBase": left_pick_base,
            "leftReturnQuantityBase": left_return_base,
            "allocationStatus": status,
            "shortageQuantityBase": shortage_base,
            "inventorySufficient": status == "ready",
            "packagingSource": sku["packagingSource"],
            "packagingVerificationStatus": sku["verificationStatus"],
            "rightTasks": right_tasks,
            "leftTasks": left_tasks,
        }
        allocations.append(allocation)
        all_tasks.extend(right_tasks)
        all_tasks.extend(left_tasks)

    if not allocations:
        raise ValueError("Order has no quantityBase lines to plan")
    overall = "ready" if all(item["allocationStatus"] == "ready" for item in allocations) else "shortage"
    result = {
        "orderId": order["id"],
        "allocationStatus": overall,
        "allocations": allocations,
        "tasks": all_tasks,
        "inventoryMutated": False,
        "inventoryReserved": False,
        "previewCargoAccepted": False,
        "canDeclareCompleted": False,
        "requestFingerprint": _fingerprint({
            "orderId": order["id"],
            "lines": [
                {"id": line.get("id"), "skuId": line.get("skuId"), "quantityBase": line.get("quantityBase")}
                for line in order.get("lines", [])
                if line.get("quantityBase") is not None
            ],
        }),
    }
    return result


def plan_orders_fulfillment(
    orders: list[dict[str, Any]],
    catalog: dict[str, Any],
    inventory_balances: list[dict[str, Any]],
    *,
    allowed_packaging_statuses: frozenset[str] = FORMAL_PACKAGING_STATUSES,
    allowed_inventory_sources: frozenset[str] = FORMAL_INVENTORY_SOURCES,
) -> list[dict[str, Any]]:
    """Plan several orders against one isolated inventory ledger."""
    remaining = deepcopy(inventory_balances)
    plans: list[dict[str, Any]] = []
    for order in orders:
        plan = plan_order_fulfillment(
            order,
            catalog,
            remaining,
            allowed_packaging_statuses=allowed_packaging_statuses,
            allowed_inventory_sources=allowed_inventory_sources,
        )
        plans.append(plan)
        for task in plan["tasks"]:
            balance = next(
                item for item in remaining
                if item["sourceWarehouse"] == task["sourceWarehouse"]
                and item["sourceLocationId"] == task["sourceLocationId"]
                and item["skuId"] == task["skuId"]
            )
            balance["quantityCases"] = int(balance["quantityCases"]) - 1
    return plans
