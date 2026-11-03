from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from .pathfinding import cell_id
from .shuttle_pathfinding import plan_shuttle_path
from .shuttle_topology import (
    SHUTTLE_COORDINATE_CONTRACT_REVISION,
    ShuttleConfigStore,
    build_shuttle_track_graph,
    topology_revision,
    validate_shuttle_configuration,
)


TRANSFER_DATA_SOURCE = "run_isolated_simulation_snapshot"

# Graph + validation only depend on (topology revision, config revision), so the
# expensive rebuild is reused across contract/plan/runtime calls until either
# input changes. Bounded to avoid unbounded growth across topology edits.
_context_cache: dict[tuple[str, str, str], tuple[dict[str, dict[str, Any]], dict[str, Any]]] = {}
_CONTEXT_CACHE_LIMIT = 16


def shuttle_context(topology: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    config = ShuttleConfigStore().load(str(topology["id"]))
    cache_key = (
        str(topology["id"]),
        topology_revision(topology),
        str((config or {}).get("config_revision", "missing")),
    )
    cached = _context_cache.get(cache_key)
    if cached is not None:
        return cached
    graph = build_shuttle_track_graph(topology)
    validation = validate_shuttle_configuration(topology, config, graph)
    if not validation["planning_enabled"]:
        reason = "; ".join(validation["warnings"]) or "ShuttleGraph planning is not enabled"
        raise ValueError(reason)
    if len(_context_cache) >= _CONTEXT_CACHE_LIMIT:
        _context_cache.clear()
    _context_cache[cache_key] = (graph, validation)
    return graph, validation


def shuttle_contract(topology: dict[str, Any]) -> dict[str, Any]:
    graph, validation = shuttle_context(topology)
    return {
        "topologyId": topology["id"],
        "topologyRevision": validation["topology_revision"],
        "configRevision": validation["config_revision"],
        "coordinateContractRevision": SHUTTLE_COORDINATE_CONTRACT_REVISION,
        "verificationStatus": validation["verification_status"],
        "planningEnabled": validation["planning_enabled"],
        "tracks": sorted(graph.values(), key=lambda item: item["track_node_id"]),
        "lifts": validation["lifts"],
        "warnings": validation["warnings"],
    }


def plan_transfer(
    topology: dict[str, Any],
    start_track_node: str,
    end_track_node: str,
    algorithm: str,
) -> dict[str, Any]:
    graph, validation = shuttle_context(topology)
    result = plan_shuttle_path(
        graph,
        validation["lifts"],
        start_track_node,
        end_track_node,
        algorithm,
    )
    return {
        "topologyId": topology["id"],
        "topologyRevision": validation["topology_revision"],
        "configRevision": validation["config_revision"],
        "coordinateContractRevision": SHUTTLE_COORDINATE_CONTRACT_REVISION,
        **result,
    }


def _balance_key(location_id: str, sku_id: str) -> tuple[str, str]:
    return location_id, sku_id


def build_transfer_runtime(
    topology: dict[str, Any],
    requests: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Create run-local tasks, robots, balances and the initial event ledger.

    No global WMS balance is mutated. Resetting a run restores this snapshot, while
    idempotent event ids prevent replay from applying pick/drop twice inside a run.
    """
    if not requests:
        return [], [], [], []
    balances: dict[tuple[str, str], dict[str, Any]] = {}
    tasks: list[dict[str, Any]] = []
    robots_by_id: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []

    for request in requests:
        plan = plan_transfer(
            topology,
            str(request["sourceTrackNode"]),
            str(request["destinationTrackNode"]),
            str(request.get("algorithm") or "astar"),
        )
        if plan["result_status"] != "success":
            raise ValueError(plan.get("failure_reason") or "ShuttleGraph did not return a route")
        quantity = int(request["quantity"])
        sku_id = str(request["skuId"])
        sku_name = str(request.get("skuName") or sku_id)
        source = plan["start"]
        destination = plan["destination"]
        source_key = _balance_key(str(request["sourceLocationId"]), sku_id)
        destination_key = _balance_key(str(request["destinationLocationId"]), sku_id)
        initial_values = (
            (source_key, source, int(request.get("initialSourceQuantity", 10))),
            (destination_key, destination, int(request.get("initialDestinationQuantity", 5))),
        )
        for key, track, initial_quantity in initial_values:
            existing = balances.get(key)
            if existing and existing["quantityOnHand"] != initial_quantity:
                raise ValueError(f"Conflicting initial quantity for {key[0]} / {key[1]}")
            if not existing:
                balances[key] = {
                    "locationId": key[0],
                    "trackNodeId": track["track_node_id"],
                    "level": track["shuttle_level"],
                    "row": track["row"],
                    "col": track["col"],
                    "worldX": track["world_x"],
                    "worldY": track["world_y"],
                    "worldZ": track["world_z"],
                    "skuId": sku_id,
                    "skuName": sku_name,
                    "quantityOnHand": initial_quantity,
                    "quantityReserved": 0,
                    "quantityAvailable": initial_quantity,
                    "dataSource": TRANSFER_DATA_SOURCE,
                }

        source_balance = balances[source_key]
        if source_balance["quantityAvailable"] < quantity:
            raise ValueError(
                f"Insufficient inventory at {source_key[0]}: "
                f"available={source_balance['quantityAvailable']}, requested={quantity}"
            )
        source_balance["quantityReserved"] += quantity
        source_balance["quantityAvailable"] -= quantity

        task_id = str(request["id"])
        vehicle_id = str(request.get("vehicleId") or "SHUTTLE-UNIFIED-01")
        source_cell = cell_id(int(source["row"]), int(source["col"]), "1F")
        destination_cell = cell_id(int(destination["row"]), int(destination["col"]), "1F")
        task = {
            "id": task_id,
            "orderId": None,
            "robotType": "shuttle",
            "mode": "shuttle_transfer",
            "status": "pending",
            "phase": "reserved",
            "phaseLabel": "库存已预留",
            "sourceCellId": source_cell,
            "sourceLayer": int(source["shuttle_level"]),
            "deliveryCellId": destination_cell,
            "targetCellId": destination_cell,
            "targetLayer": int(destination["shuttle_level"]),
            "lines": [{
                "nodeId": str(request["sourceLocationId"]),
                "cellId": source_cell,
                "layer": int(source["shuttle_level"]),
                "skuId": sku_id,
                "skuName": sku_name,
                "quantity": quantity,
            }],
            "currentLineIndex": 0,
            "picked": 0,
            "priority": 1,
            "etaTicks": max(1, len(plan["path_nodes"])),
            "urgent": False,
            "assignedRobotId": None,
            "requestedVehicleId": vehicle_id,
            "remainingTicks": 0,
            "history": [],
            "createdTick": 0,
            "startedTick": None,
            "completedTick": None,
            "skuId": sku_id,
            "skuName": sku_name,
            "quantity": quantity,
            "sourceLocationId": str(request["sourceLocationId"]),
            "destinationLocationId": str(request["destinationLocationId"]),
            "sourceTrackNode": str(request["sourceTrackNode"]),
            "destinationTrackNode": str(request["destinationTrackNode"]),
            "inTransitQuantity": 0,
            "appliedInventoryEventIds": [],
            "plan": plan,
            "pickupReadyTick": None,
            "failureReason": None,
        }
        tasks.append(task)
        events.append({
            "eventId": f"{task_id}:reserve",
            "taskId": task_id,
            "tick": 0,
            "eventType": "reserve",
            "locationId": source_key[0],
            "skuId": sku_id,
            "quantity": quantity,
            "onHandDelta": 0,
            "reservedDelta": quantity,
            "inTransitDelta": 0,
        })
        if vehicle_id not in robots_by_id:
            robots_by_id[vehicle_id] = {
                "id": vehicle_id,
                "type": "shuttle",
                "currentNode": source_cell,
                "currentCellId": source_cell,
                "homeCellId": source_cell,
                "currentFloor": "1F",
                "currentRow": int(source["row"]),
                "currentCol": int(source["col"]),
                "currentLayer": int(source["shuttle_level"]),
                "status": "idle",
                "battery": 100.0,
                "path": [source_cell],
                "pathLayers": [int(source["shuttle_level"])],
                "pathStages": ["reserved"],
                "targetCellId": source_cell,
                "targetLayer": int(source["shuttle_level"]),
                "currentTaskId": None,
                "returningToStandby": False,
                "handlingAction": None,
                "waitTicks": 0,
                "failed": False,
                "phaseLabel": "待命",
                "load": 0,
                "carrying": [],
                "capacity": 100,
            }

    return tasks, list(robots_by_id.values()), list(balances.values()), events


def build_playback_route(plan: dict[str, Any], tick_seconds: float) -> tuple[list[str], list[float], list[str]]:
    nodes = plan.get("path_nodes") or []
    edges = plan.get("path_edges") or []
    if not nodes:
        return [], [], []
    first = nodes[0]
    paths = [cell_id(int(first["row"]), int(first["col"]), "1F")]
    layers: list[float] = [float(first["shuttle_level"])]
    stages = ["track"]
    for index, edge in enumerate(edges):
        source = nodes[index]
        target = nodes[index + 1]
        target_cell = cell_id(int(target["row"]), int(target["col"]), "1F")
        if edge["edge_type"] != "lift":
            paths.append(target_cell)
            layers.append(float(target["shuttle_level"]))
            stages.append("track")
            continue
        source_cell = cell_id(int(source["row"]), int(source["col"]), "1F")
        enter_seconds = max(0.0, float(edge["motion_start_seconds"]) - float(edge["start_seconds"]))
        motion_seconds = max(0.0, float(edge["motion_end_seconds"]) - float(edge["motion_start_seconds"]))
        exit_seconds = max(0.0, float(edge["end_seconds"]) - float(edge["motion_end_seconds"]))
        for _ in range(max(1, math.ceil(enter_seconds / tick_seconds))):
            paths.append(source_cell)
            layers.append(float(source["shuttle_level"]))
            stages.append("enter_lift")
        motion_ticks = max(1, math.ceil(motion_seconds / tick_seconds))
        for step in range(1, motion_ticks + 1):
            progress = step / motion_ticks
            level = float(source["shuttle_level"]) + (
                float(target["shuttle_level"]) - float(source["shuttle_level"])
            ) * progress
            paths.append(target_cell)
            layers.append(level)
            stages.append("lift_moving")
        for _ in range(max(1, math.ceil(exit_seconds / tick_seconds))):
            paths.append(target_cell)
            layers.append(float(target["shuttle_level"]))
            stages.append("exit_lift")
    return paths, layers, stages


def _find_balance(state: dict[str, Any], location_id: str, sku_id: str) -> dict[str, Any]:
    balance = next(
        (
            item for item in state.get("transferInventory", [])
            if item["locationId"] == location_id and item["skuId"] == sku_id
        ),
        None,
    )
    if balance is None:
        raise ValueError(f"Transfer balance not found: {location_id} / {sku_id}")
    return balance


def _append_event(
    state: dict[str, Any],
    task: dict[str, Any],
    tick: int,
    event_type: str,
    *,
    location_id: str | None,
    on_hand_delta: int,
    reserved_delta: int,
    in_transit_delta: int,
) -> bool:
    event_id = f"{task['id']}:{event_type}"
    applied = task.setdefault("appliedInventoryEventIds", [])
    if event_id in applied:
        return False
    applied.append(event_id)
    balance = _find_balance(state, location_id, task["skuId"]) if location_id else None
    state.setdefault("transferEvents", []).append({
        "eventId": event_id,
        "taskId": task["id"],
        "tick": tick,
        "eventType": event_type,
        "locationId": location_id,
        "skuId": task["skuId"],
        "quantity": task["quantity"],
        "onHandDelta": on_hand_delta,
        "reservedDelta": reserved_delta,
        "inTransitDelta": in_transit_delta,
        "quantityOnHandAfter": balance["quantityOnHand"] if balance else None,
        "quantityReservedAfter": balance["quantityReserved"] if balance else None,
        "inTransitAfter": task["inTransitQuantity"],
    })
    return True


def apply_pick(state: dict[str, Any], task: dict[str, Any], tick: int) -> bool:
    event_id = f"{task['id']}:pick"
    if event_id in task.setdefault("appliedInventoryEventIds", []):
        return False
    balance = _find_balance(state, task["sourceLocationId"], task["skuId"])
    quantity = int(task["quantity"])
    if balance["quantityOnHand"] < quantity or balance["quantityReserved"] < quantity:
        raise ValueError("Reserved source inventory is no longer available")
    balance["quantityOnHand"] -= quantity
    balance["quantityReserved"] -= quantity
    balance["quantityAvailable"] = balance["quantityOnHand"] - balance["quantityReserved"]
    task["inTransitQuantity"] = quantity
    task["picked"] = quantity
    return _append_event(
        state, task, tick, "pick",
        location_id=task["sourceLocationId"],
        on_hand_delta=-quantity,
        reserved_delta=-quantity,
        in_transit_delta=quantity,
    )


def apply_drop(state: dict[str, Any], task: dict[str, Any], tick: int) -> bool:
    event_id = f"{task['id']}:drop"
    if event_id in task.setdefault("appliedInventoryEventIds", []):
        return False
    quantity = int(task["quantity"])
    if int(task.get("inTransitQuantity", 0)) != quantity:
        raise ValueError("Drop requires the requested quantity to be in transit")
    balance = _find_balance(state, task["destinationLocationId"], task["skuId"])
    balance["quantityOnHand"] += quantity
    balance["quantityAvailable"] = balance["quantityOnHand"] - balance["quantityReserved"]
    task["inTransitQuantity"] = 0
    return _append_event(
        state, task, tick, "drop",
        location_id=task["destinationLocationId"],
        on_hand_delta=quantity,
        reserved_delta=0,
        in_transit_delta=-quantity,
    )


def cancel_transfer(state: dict[str, Any], task: dict[str, Any], tick: int) -> bool:
    if task.get("status") in {"completed", "cancelled"}:
        return False
    quantity = int(task["quantity"])
    source = _find_balance(state, task["sourceLocationId"], task["skuId"])
    if int(task.get("inTransitQuantity", 0)):
        source["quantityOnHand"] += quantity
        task["inTransitQuantity"] = 0
        event_type, on_hand_delta, reserved_delta, transit_delta = "cancel_return", quantity, 0, -quantity
    else:
        source["quantityReserved"] = max(0, source["quantityReserved"] - quantity)
        event_type, on_hand_delta, reserved_delta, transit_delta = "cancel_release", 0, -quantity, 0
    source["quantityAvailable"] = source["quantityOnHand"] - source["quantityReserved"]
    changed = _append_event(
        state, task, tick, event_type,
        location_id=task["sourceLocationId"],
        on_hand_delta=on_hand_delta,
        reserved_delta=reserved_delta,
        in_transit_delta=transit_delta,
    )
    task["status"] = "cancelled"
    task["phase"] = "cancelled"
    task["phaseLabel"] = "已取消并完成库存补偿"
    return changed


def clone_initial_state(state: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(state)
