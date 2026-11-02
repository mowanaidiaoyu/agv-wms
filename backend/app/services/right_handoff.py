from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
from typing import Any

from .conveyor import compile_conveyor_graph, route_from_block
from .pathfinding import parse_cell_id, shortest_path
from .shuttle_topology import (
    SHUTTLE_LEVEL_HEIGHT,
    SHUTTLE_RACK_BASE_Y,
    SHUTTLE_TRACK_SURFACE_OFFSET,
    build_shuttle_track_graph,
    topology_revision,
)
from .unified_transfer import build_playback_route, plan_transfer


class RightHandoffValidationError(ValueError):
    pass


class RightHandoffConflict(RuntimeError):
    pass


SIMULATION_TEST_LEFT_SOURCE_CELL = "cell-r0-c6"
SIMULATION_TEST_LEFT_SOURCE_STOP = "cell-r0-c7"
SIMULATION_TEST_BREAK_PACK_CELL = "cell-r72-c66"
SIMULATION_TEST_BREAK_PACK_STOP = "cell-r72-c65"
SIMULATION_TEST_BREAK_PACK_ID = "simtest-left-break-pack-r72-c66"
SIMULATION_TEST_LEFT_CARGO_ID = "SIMTEST-LEFT-CASE-001"
SIMULATION_TEST_LEFT_PARTIAL_CARGO_ID = "SIMTEST-LEFT-CASE-001-PARTIAL-10"
SIMULATION_TEST_LEFT_PHASE_TICKS = {
    "picking": 3,
    "station_receiving": 2,
    "opening": 2,
    "picking_units": 3,
    "confirming": 2,
    "loading_return": 2,
    "returning_to_storage": 3,
}


REQUIRED_FACILITY_FIELDS = frozenset({
    "deviceId", "deviceType", "inputNodeId", "outputNodeId", "sourceLevel",
    "targetLevel", "capacity", "transferDuration", "dataSource", "validationStatus",
})


def _cell_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(cell["id"]): cell for cell in topology.get("cells", [])}


def _node_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(node["id"]): node for node in topology.get("nodes", [])}


def _validate_geometry(facility: dict[str, Any], topology: dict[str, Any]) -> None:
    missing = REQUIRED_FACILITY_FIELDS - facility.keys()
    if missing:
        raise RightHandoffValidationError(
            f"Handoff facility {facility.get('deviceId')} is missing {sorted(missing)}"
        )
    if facility.get("dataSource") != "simulation_test":
        raise RightHandoffValidationError("Handoff overlays must be simulation_test data")
    if facility.get("validationStatus") != "simulation_only":
        raise RightHandoffValidationError("Unvalidated handoff overlays must remain simulation_only")
    if int(facility.get("capacity") or 0) != 1:
        raise RightHandoffValidationError("PYC-F3-A handoff facilities must have capacity 1")
    if float(facility.get("transferDuration") or 0) <= 0:
        raise RightHandoffValidationError("Handoff transferDuration must be positive")
    if int(facility.get("sourceLevel") or 0) != int(facility.get("targetLevel") or 0):
        raise RightHandoffValidationError("The ground handoff may not invent a cross-level edge")

    path = (facility.get("geometry") or {}).get("pathCells") or []
    if len(path) < 2:
        raise RightHandoffValidationError("Handoff geometry needs at least two path cells")
    for left, right in zip(path, path[1:]):
        distance = abs(int(left["row"]) - int(right["row"])) + abs(
            int(left["col"]) - int(right["col"])
        )
        if distance != 1:
            raise RightHandoffValidationError("Handoff geometry must be Manhattan-contiguous")

    world_points = (facility.get("geometry") or {}).get("worldPoints") or []
    if len(world_points) < 2:
        raise RightHandoffValidationError("Handoff geometry needs an explicit world-space centerline")
    belt_height = float((facility.get("geometry") or {}).get("beltHeight") or 0)
    if any(abs(float(point["y"]) - belt_height) > 1e-9 for point in world_points):
        raise RightHandoffValidationError("Handoff world-space centerline must stay on the belt surface")

    cells = _cell_lookup(topology)
    for point in path:
        cell = cells.get(f"cell-r{int(point['row'])}-c{int(point['col'])}")
        if cell is not None and (
            cell.get("type") in {"rack", "storage", "blocked"}
            or cell.get("walkable") is False
        ):
            raise RightHandoffValidationError(
                f"Handoff geometry crosses blocked topology cell {cell['id']}"
            )


def build_right_cargo_contracts(
    plan: dict[str, Any],
    scenario: dict[str, Any],
) -> list[dict[str, Any]]:
    right_inventory = {
        str(item.get("sourceLocationId")): item
        for item in scenario.get("inventory", [])
        if item.get("sourceWarehouse") == "right"
    }
    destination = str((scenario.get("rightHandoff") or {})["armReceiveNodeId"])
    right_tasks = [
        task for task in plan.get("tasks", [])
        if task.get("sourceWarehouse") == "right"
    ]
    contracts: list[dict[str, Any]] = []
    for task in right_tasks:
        inventory = right_inventory.get(str(task.get("sourceLocationId")))
        if inventory is None:
            raise RightHandoffValidationError(
                f"Right task source has no validated inventory mapping: {task.get('sourceLocationId')}"
            )
        source_track = str((inventory.get("topologyMapping") or {}).get("trackNodeId") or "")
        if not source_track:
            raise RightHandoffValidationError("Right task source has no ShuttleGraph node")
        contracts.append({
            "cargoId": task["cargoId"],
            "orderId": task["orderId"],
            "taskId": task["taskId"],
            "skuId": task["skuId"],
            "quantityCases": task["quantityCases"],
            "quantityBase": task["quantityBase"],
            "sourceLocationId": task["sourceLocationId"],
            "currentNodeId": source_track,
            "currentDeviceId": None,
            "destinationNodeId": destination,
            "status": "waiting_pick",
            "dataSource": str(scenario.get("dataSource") or "simulation_test"),
            "authoritative": False,
            "preview": False,
        })
    cargo_ids = [item["cargoId"] for item in contracts]
    if len(set(cargo_ids)) != len(cargo_ids):
        raise RightHandoffValidationError("Right cargo IDs must be unique")
    planned_quantity = sum(int(task.get("quantityBase") or 0) for task in right_tasks)
    if sum(int(item["quantityBase"]) for item in contracts) != planned_quantity:
        raise RightHandoffValidationError("Right cargo quantity does not conserve the fulfillment plan")
    return contracts


def audit_right_handoff(
    topology: dict[str, Any],
    scenario: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    contract = deepcopy(scenario.get("rightHandoff") or {})
    if contract.get("formalRouteStatus") != "BLOCKED":
        raise RightHandoffValidationError("The unconnected formal route must remain BLOCKED")
    if contract.get("simulationRouteStatus") != "PASS_SIMULATION_ONLY":
        raise RightHandoffValidationError("The test route must be labelled PASS_SIMULATION_ONLY")

    graph = build_shuttle_track_graph(topology)
    source = str(contract.get("sourceTrackNodeId") or "")
    proven_exit = str(contract.get("provenLiftExitTrackNodeId") or "")
    handoff_track = str(contract.get("handoffTrackNodeId") or "")
    for node_id in (source, proven_exit, handoff_track):
        if node_id not in graph:
            raise RightHandoffValidationError(f"ShuttleGraph node is missing: {node_id}")

    lift_route = plan_transfer(topology, source, proven_exit, "astar")
    if lift_route["result_status"] != "success" or lift_route.get("chosen_lift") != "shuttle-lift-a":
        raise RightHandoffValidationError("The source-to-L1 route must use the validated Lift-A graph path")
    egress_route = plan_transfer(topology, proven_exit, handoff_track, "astar")
    if egress_route["result_status"] != "success" or egress_route.get("chosen_lift") is not None:
        raise RightHandoffValidationError("The L1 exit-to-handoff route must stay on ShuttleGraph level 1")

    cells = _cell_lookup(topology)
    arm_cell_id = str(contract.get("armReceiveNodeId") or "")
    arm_cell = cells.get(arm_cell_id)
    if arm_cell is None or arm_cell.get("type") != "conveyor" or not arm_cell.get("conveyorGround"):
        raise RightHandoffValidationError("The arm receive node must remain a real ground Conveyor cell")

    nodes = _node_lookup(topology)
    dock = nodes.get("dock-01")
    if dock is None or dock.get("type") != "shuttle_dock":
        raise RightHandoffValidationError("dock-01 audit evidence is missing")
    if (int(dock.get("row", -1)), int(dock.get("col", -1))) != (74, 17):
        raise RightHandoffValidationError("dock-01 geometry changed and needs a new audit")
    turn = nodes.get("turn_transfer-25")
    if turn is None or (int(turn.get("row", -1)), int(turn.get("col", -1))) != (92, 27):
        raise RightHandoffValidationError("turn_transfer-25 is missing from the interface")
    formal_turn_configured = (
        turn.get("transferDeck") == "lower"
        and turn.get("transferInputSide") == "south"
        and turn.get("transferOutputSide") == "east"
    )
    if formal_turn_configured:
        raise RightHandoffValidationError(
            "The formal transfer is now configured; the simulation-only overlay must be re-audited"
        )

    facilities = contract.get("facilities") or []
    if len(facilities) != 3 or len({item.get("deviceId") for item in facilities}) != 3:
        raise RightHandoffValidationError("The test interface requires exactly three unique facilities")
    for facility in facilities:
        _validate_geometry(facility, topology)

    conveyor_graph = compile_conveyor_graph(topology)
    conveyor_route = route_from_block(conveyor_graph, "1F:92:29", "lower")
    expected_route = [f"1F:92:{col}:lower" for col in range(29, 48, 2)]
    if conveyor_route != expected_route:
        raise RightHandoffValidationError("The real ground Conveyor route to the arm receive cell is broken")
    if str(contract.get("armReceiveConveyorBlockId")) != expected_route[-1]:
        raise RightHandoffValidationError("Arm receive Conveyor block does not match cell-r92-c48")

    authority = contract.get("authority") or {}
    if authority != {
        "stateOwner": "SimulationService",
        "requiresUnifiedTick": True,
        "previewSignalsAccepted": False,
        "animationCanCompleteBusinessTask": False,
    }:
        raise RightHandoffValidationError("Handoff authority must remain on the unified SimulationService tick")

    cargo = build_right_cargo_contracts(plan, scenario)
    if any(item["currentNodeId"] != source for item in cargo):
        raise RightHandoffValidationError(
            "Configured right inventory is not connected to the supported Shuttle handoff route"
        )
    return {
        "formalRouteStatus": "BLOCKED",
        "simulationRouteStatus": "PASS_SIMULATION_ONLY",
        "formalBlockers": [
            "No authoritative ShuttleGraph-to-Conveyor handoff device is configured.",
            "turn_transfer-25 has no formal lower-deck south-to-east direction contract.",
            "Lift-A timings are synthetic_demo and program_verified_pending_human.",
        ],
        "existingDockAssessment": contract["existingDockAssessment"],
        "sourceTrackNodeId": source,
        "provenLiftExitTrackNodeId": proven_exit,
        "handoffTrackNodeId": handoff_track,
        "armReceiveNodeId": arm_cell_id,
        "shuttlePath": [item["track_node_id"] for item in lift_route["path_nodes"]],
        "egressPath": [item["track_node_id"] for item in egress_route["path_nodes"]],
        "chosenLift": lift_route["chosen_lift"],
        "conveyorPath": ["1F:92:27:lower", *expected_route],
        "facilities": facilities,
        "authority": authority,
        "cargo": cargo,
        "segments": [
            {"segmentId": "source-to-l1-exit", "validationStatus": "simulation_only", "reason": "Real ShuttleGraph geometry; Lift-A timing remains synthetic and pending human validation."},
            {"segmentId": "l1-exit-to-handoff-track", "validationStatus": "simulation_only", "reason": "Real ShuttleGraph geometry; no physical commissioning evidence."},
            {"segmentId": "shuttle-to-conveyor-interface", "validationStatus": "simulation_only", "reason": "Three run-local facilities provide the missing explicit interface."},
            {"segmentId": "ground-conveyor-to-arm", "validationStatus": "physical_validated", "reason": "Existing topology ground Conveyor cells are contiguous and directed east to cell-r92-c48; this status applies only to the topology segment, not the simulation-only interface."},
            {"segmentId": "formal-end-to-end", "validationStatus": "blocked", "reason": "Formal handoff and turn direction contracts are absent."},
        ],
    }


def can_authoritative_arm_grip(
    cargo: dict[str, Any] | None,
    signal: dict[str, Any] | None,
) -> bool:
    return bool(
        cargo
        and cargo.get("status") == "waiting_arm"
        and cargo.get("preview") is False
        and signal
        and signal.get("authoritative") is True
        and signal.get("cargoId") == cargo.get("cargoId")
    )


@dataclass
class RunLocalHandoffState:
    """A non-persistent scenario snapshot; SimulationService remains the future tick owner."""

    run_scope_id: str
    templates: list[dict[str, Any]]
    facilities: list[dict[str, Any]]
    cargo: list[dict[str, Any]] = field(init=False)
    active_by_device: dict[str, list[str]] = field(default_factory=dict, init=False)
    completed_pairs: set[tuple[str, str]] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.cargo = deepcopy(self.templates)
        for item in self.cargo:
            item["runScopeId"] = self.run_scope_id
        self.active_by_device = {}
        self.completed_pairs = set()

    def begin_transfer(self, cargo_id: str, device_id: str) -> dict[str, Any]:
        pair = (cargo_id, device_id)
        cargo = next((item for item in self.cargo if item["cargoId"] == cargo_id), None)
        facility = next((item for item in self.facilities if item["deviceId"] == device_id), None)
        if cargo is None or facility is None:
            raise RightHandoffConflict("Unknown cargo or handoff facility")
        if pair in self.completed_pairs or cargo.get("currentDeviceId") == device_id:
            raise RightHandoffConflict("Duplicate cargo handoff rejected")
        active = self.active_by_device.setdefault(device_id, [])
        if len(active) >= int(facility["capacity"]):
            raise RightHandoffConflict("Handoff facility capacity exceeded")
        active.append(cargo_id)
        cargo["currentDeviceId"] = device_id
        cargo["status"] = "dock_transferring"
        return deepcopy(cargo)

    def complete_transfer(self, cargo_id: str, device_id: str) -> dict[str, Any]:
        active = self.active_by_device.get(device_id, [])
        if cargo_id not in active:
            raise RightHandoffConflict("Cargo does not own the handoff facility")
        active.remove(cargo_id)
        cargo = next(item for item in self.cargo if item["cargoId"] == cargo_id)
        facility = next(item for item in self.facilities if item["deviceId"] == device_id)
        cargo["currentNodeId"] = facility["outputNodeId"]
        cargo["currentDeviceId"] = None
        cargo["status"] = "conveyor_transporting"
        self.completed_pairs.add((cargo_id, device_id))
        return deepcopy(cargo)


SIMULATION_TEST_RUN_MODE = "simulation_test"
GENERAL_SIMULATION_RUN_MODE = "simulation"
FULFILLMENT_SIMULATION_RUN_MODES = frozenset({SIMULATION_TEST_RUN_MODE, GENERAL_SIMULATION_RUN_MODE})
SIMULATION_TEST_SHUTTLE_ID = "shuttle-robot-01"
SIMULATION_TEST_TICK_SECONDS = 0.6
SIMULATION_TEST_ARM_PHASES = (
    "approach", "grip", "lift", "rotate", "place", "release", "return_home",
)
SIMULATION_TEST_ARM_PHASE_TICKS = {
    "approach": 2,
    "grip": 1,
    "lift": 2,
    "rotate": 3,
    "place": 2,
    "release": 1,
    "return_home": 2,
}


def _track_position(
    topology: dict[str, Any],
    cell_id: str,
    level: float,
    *,
    node_id: str,
) -> dict[str, Any]:
    _, row, col = parse_cell_id(cell_id)
    return {
        "nodeId": node_id,
        "row": row,
        "col": col,
        "level": level,
        "worldX": col + 1.0 - int(topology["grid"]["cols"]) / 2.0,
        "worldY": SHUTTLE_RACK_BASE_Y + SHUTTLE_TRACK_SURFACE_OFFSET + (level - 1.0) * SHUTTLE_LEVEL_HEIGHT,
        "worldZ": int(topology["grid"]["rows"]) / 2.0 - (row + 1.0),
    }


def _world_position(node_id: str, point: dict[str, Any], *, level: float = 1.0) -> dict[str, Any]:
    return {
        "nodeId": node_id,
        "row": point.get("row"),
        "col": point.get("col"),
        "level": level,
        "worldX": float(point["x"]),
        "worldY": float(point["y"]),
        "worldZ": float(point["z"]),
    }


def _lerp_position(
    node_id: str,
    points: list[dict[str, Any]],
    progress: float,
) -> dict[str, Any]:
    progress = max(0.0, min(1.0, progress))
    if len(points) == 1:
        return _world_position(node_id, points[0])
    scaled = progress * (len(points) - 1)
    index = min(len(points) - 2, int(math.floor(scaled)))
    local = scaled - index
    left, right = points[index], points[index + 1]
    point = {
        axis: float(left[axis]) + (float(right[axis]) - float(left[axis])) * local
        for axis in ("x", "y", "z")
    }
    return _world_position(node_id, point)


def _route_frames(topology: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    paths, layers, stages = build_playback_route(plan, SIMULATION_TEST_TICK_SECONDS)
    frames: list[dict[str, Any]] = []
    for index, (cell, level, stage) in enumerate(zip(paths, layers, stages, strict=True)):
        _, row, col = parse_cell_id(cell)
        rounded_level = max(1, min(4, round(level)))
        frames.append({
            "cellId": cell,
            "level": level,
            "stage": stage,
            "nodeId": f"L{rounded_level}-r{row}-c{col}",
            "position": _track_position(
                topology,
                cell,
                level,
                node_id=f"L{rounded_level}-r{row}-c{col}",
            ),
            "index": index,
        })
    validate_shuttle_route_frames(frames)
    return frames


def validate_shuttle_route_frames(frames: list[dict[str, Any]]) -> None:
    """Reject visual playback shortcuts that are not cardinal ShuttleGraph edges."""
    for index in range(1, len(frames)):
        previous = frames[index - 1]["position"]
        current = frames[index]["position"]
        row_changed = int(previous["row"]) != int(current["row"])
        col_changed = int(previous["col"]) != int(current["col"])
        level_changed = abs(float(previous["level"]) - float(current["level"])) > 1e-9
        if level_changed and (row_changed or col_changed):
            raise RightHandoffValidationError(
                f"Shuttle lift frame drifts horizontally: {frames[index - 1]['nodeId']} -> {frames[index]['nodeId']}"
            )
        if not level_changed and row_changed and col_changed:
            raise RightHandoffValidationError(
                f"Shuttle track frame is diagonal: {frames[index - 1]['nodeId']} -> {frames[index]['nodeId']}"
            )


def _ctu_position(topology: dict[str, Any], cell: dict[str, Any]) -> dict[str, Any]:
    row, col = int(cell["row"]), int(cell["col"])
    floor = str(cell.get("floor") or "1F")
    return {
        "nodeId": str(cell.get("id") or f"cell-r{row}-c{col}"),
        "row": row,
        "col": col,
        "level": 1,
        "worldX": col + 0.5 - int(topology["grid"]["cols"]) / 2.0,
        "worldY": 0.0,
        "worldZ": int(topology["grid"]["rows"]) / 2.0 - (row + 0.5),
        "floor": floor,
    }


def _ctu_route_frames(
    topology: dict[str, Any],
    start_cell_id: str,
    goal_cell_id: str,
) -> list[dict[str, Any]]:
    route = shortest_path(
        topology,
        start_cell_id,
        goal_cell_id,
        "astar",
        avoid_storage=True,
    )
    if not route or str(route[0]["id"]) != start_cell_id or str(route[-1]["id"]) != goal_cell_id:
        raise RightHandoffValidationError(
            f"CTU road route is unavailable: {start_cell_id} -> {goal_cell_id}"
        )
    frames: list[dict[str, Any]] = []
    for index, cell in enumerate(route):
        position = _ctu_position(topology, cell)
        frames.append({
            "cellId": position["nodeId"],
            "level": 1,
            "stage": "track",
            "nodeId": position["nodeId"],
            "position": position,
            "index": index,
        })
    for left, right in zip(frames, frames[1:]):
        row_delta = abs(int(left["position"]["row"]) - int(right["position"]["row"]))
        col_delta = abs(int(left["position"]["col"]) - int(right["position"]["col"]))
        if row_delta + col_delta != 1:
            raise RightHandoffValidationError(
                f"CTU route contains a non-cardinal edge: {left['nodeId']} -> {right['nodeId']}"
            )
        if left["position"]["floor"] != right["position"]["floor"]:
            raise RightHandoffValidationError("The F5 CTU route may not invent a cross-floor edge")
    return frames


def _build_left_break_pack_contract(
    topology: dict[str, Any],
    left_task: dict[str, Any],
) -> dict[str, Any]:
    source = next(
        (node for node in topology.get("nodes", []) if node.get("id") == "storage-01"),
        None,
    )
    if source is None or (int(source["row"]), int(source["col"])) != (0, 6):
        raise RightHandoffValidationError("storage-01 must remain at r0,c6 for the F5 test contract")
    if str(left_task.get("sourceLocationId")) != "storage-01:L1":
        raise RightHandoffValidationError("The F5 left task must use storage-01:L1")

    station_zone = next(
        (zone for zone in topology.get("zones", []) if zone.get("id") == "zone-port-4"),
        None,
    )
    if station_zone is None or station_zone.get("type") != "port":
        raise RightHandoffValidationError("zone-port-4 is unavailable for the simulation-only break-pack overlay")
    if not (
        int(station_zone["x"]) <= 66 < int(station_zone["x"]) + int(station_zone["width"])
        and int(station_zone["y"]) <= 72 < int(station_zone["y"]) + int(station_zone["height"])
    ):
        raise RightHandoffValidationError("The break-pack overlay no longer fits inside zone-port-4")

    ctu_candidates: list[tuple[int, str, list[dict[str, Any]]]] = []
    for node in topology.get("nodes", []):
        if node.get("type") != "ctu_robot":
            continue
        start = f"cell-r{int(node['row'])}-c{int(node['col'])}"
        route = _ctu_route_frames(topology, start, SIMULATION_TEST_LEFT_SOURCE_STOP)
        ctu_candidates.append((len(route), str(node["id"]), route))
    if not ctu_candidates:
        raise RightHandoffValidationError("No topology CTU can reach storage-01")
    _, ctu_id, to_source = min(ctu_candidates, key=lambda item: (item[0], item[1]))
    to_station = _ctu_route_frames(
        topology,
        SIMULATION_TEST_LEFT_SOURCE_STOP,
        SIMULATION_TEST_BREAK_PACK_STOP,
    )
    to_storage = _ctu_route_frames(
        topology,
        SIMULATION_TEST_BREAK_PACK_STOP,
        SIMULATION_TEST_LEFT_SOURCE_STOP,
    )
    source_position = _ctu_position(topology, {
        "id": SIMULATION_TEST_LEFT_SOURCE_CELL, "floor": "1F", "row": 0, "col": 6,
    })
    station_position = _ctu_position(topology, {
        "id": SIMULATION_TEST_BREAK_PACK_CELL, "floor": "1F", "row": 72, "col": 66,
    })
    initial_ctu_position = deepcopy(to_source[0]["position"])
    source_quantity = int(left_task["sourceQuantityBase"])
    pick_quantity = int(left_task["pickQuantityBase"])
    return_quantity = int(left_task["returnQuantityBase"])
    cargo_id = str(left_task.get("cargoId") or f"cargo-{left_task['taskId']}-source")
    partial_cargo_id = f"{cargo_id}-partial-{return_quantity}"
    return {
        "status": "waiting_pick",
        "queueActivated": False,
        "taskId": left_task["taskId"],
        "sourceLocationId": left_task["sourceLocationId"],
        "sourceCellId": SIMULATION_TEST_LEFT_SOURCE_CELL,
        "sourceStopCellId": SIMULATION_TEST_LEFT_SOURCE_STOP,
        "sourcePosition": source_position,
        "cargo": {
            "cargoId": cargo_id,
            "orderId": left_task["orderId"],
            "taskId": left_task["taskId"],
            "skuId": left_task["skuId"],
            "quantityCases": 1,
            "quantityBase": source_quantity,
            "originalQuantityBase": source_quantity,
            "pickedQuantityBase": 0,
            "remainingQuantityBase": source_quantity,
            "sourceLocationId": left_task["sourceLocationId"],
            "currentNodeId": SIMULATION_TEST_LEFT_SOURCE_CELL,
            "currentDeviceId": None,
            "status": "waiting_pick",
            "caseType": "full_case",
            "dataSource": "simulation_test",
            "authoritative": False,
            "preview": False,
            "simulationStateAuthoritative": True,
            "currentPosition": source_position,
            "previousPosition": deepcopy(source_position),
        },
        "partialCase": None,
        "partialCargoId": partial_cargo_id,
        "pickQuantityBase": pick_quantity,
        "returnQuantityBase": return_quantity,
        "ctu": {
            "deviceId": ctu_id,
            "capacity": 1,
            "phase": "to_source",
            "phaseTicks": 0,
            "activeCargoId": None,
            "routeName": "to_source",
            "routeIndex": 0,
            "toSourceFrames": to_source,
            "toStationFrames": to_station,
            "toStorageFrames": to_storage,
            "currentPosition": initial_ctu_position,
            "dataSource": "simulation_test",
        },
        "station": {
            "deviceId": SIMULATION_TEST_BREAK_PACK_ID,
            "deviceType": "break_pack_station",
            "positionCellId": SIMULATION_TEST_BREAK_PACK_CELL,
            "inputNodeId": SIMULATION_TEST_BREAK_PACK_STOP,
            "outputNodeId": SIMULATION_TEST_BREAK_PACK_STOP,
            "capacity": 1,
            "reservedCargoIds": [],
            "occupiedCargoIds": [],
            "phase": "idle",
            "phaseTicks": 0,
            "position": station_position,
            "dataSource": "simulation_test",
            "validationStatus": "simulation_only",
        },
        "ledger": {
            "initialSourceQuantityBase": source_quantity,
            "sourceQuantityBase": source_quantity,
            "ctuQuantityBase": 0,
            "stationQuantityBase": 0,
            "pickedQuantityBase": 0,
            "returnedQuantityBase": 0,
            "returnApplied": False,
        },
        "quantityAudit": {
            "sourceConserved": False,
            "orderQuantityAccounted": False,
        },
    }


def build_simulation_test_state(
    topology: dict[str, Any],
    scenario_audit: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    """Build one isolated, deterministic server-authoritative test snapshot."""
    if scenario_audit.get("topologyRevision") != topology_revision(topology):
        raise RightHandoffValidationError("Simulation-test topology revision mismatch")
    handoff = scenario_audit["rightHandoff"]
    outbound_plan = plan_transfer(
        topology,
        handoff["sourceTrackNodeId"],
        handoff["handoffTrackNodeId"],
        "astar",
    )
    return_plan = plan_transfer(
        topology,
        handoff["handoffTrackNodeId"],
        handoff["sourceTrackNodeId"],
        "astar",
    )
    if outbound_plan["result_status"] != "success" or outbound_plan.get("chosen_lift") != "shuttle-lift-a":
        raise RightHandoffValidationError("Outbound simulation route must use Lift-A")
    if return_plan["result_status"] != "success":
        raise RightHandoffValidationError("Shuttle return route is not traversable")
    outbound = _route_frames(topology, outbound_plan)
    returning = _route_frames(topology, return_plan)
    if not outbound or not returning:
        raise RightHandoffValidationError("Simulation shuttle playback route is empty")

    # This is the already validated F1 transport-layout contract. It remains
    # simulation_test authority here; loading it does not mutate topology.
    from .transport_layout import load_transport_layout_plan

    arm_layout = load_transport_layout_plan(topology).pickingArm.model_dump(by_alias=True)
    merge_path = []
    for step in arm_layout["previewConveyorPath"]:
        position = step["position"]
        merge_path.append({
            "nodeId": step["cellId"],
            "row": int(position["row"]),
            "col": int(position["col"]),
            "level": int(position["level"]),
            "worldX": int(position["col"]) + 1.0 - int(topology["grid"]["cols"]) / 2.0,
            "worldY": float(step["beltHeight"]),
            "worldZ": int(topology["grid"]["rows"]) / 2.0 - (int(position["row"]) + 1.0),
            "deviceIds": list(step.get("deviceIds") or []),
            "transferDeviceId": step.get("transferDeviceId"),
        })
    if not merge_path or merge_path[0]["nodeId"] != "cell-r86-c50" or merge_path[-1]["nodeId"] != "cell-r108-c68":
        raise RightHandoffValidationError("Picking-arm merge Conveyor contract changed")

    cargo_templates = deepcopy(handoff["cargo"])
    cargo: list[dict[str, Any]] = []
    for case_index, item in enumerate(cargo_templates):
        waiting_position = deepcopy(outbound[0]["position"])
        waiting_position["worldY"] += case_index * 0.22
        item.update({
            "caseIndex": case_index,
            "dataSource": str(scenario_audit.get("dataSource") or "simulation_test"),
            "simulationStateAuthoritative": True,
            "status": "waiting_pick",
            "previousPosition": deepcopy(waiting_position),
            "currentPosition": deepcopy(waiting_position),
            "positionTick": 0,
            "routeIndex": 0,
            "facilityIndex": None,
            "facilityElapsedTicks": 0,
            "conveyorIndex": None,
            "waitingTicks": 0,
            "blockedReason": None,
            "movementState": {
                "fromNodeId": waiting_position["nodeId"],
                "toNodeId": waiting_position["nodeId"],
                "segmentProgress": 1.0,
                "movementDurationSeconds": SIMULATION_TEST_TICK_SECONDS,
                "transportState": "waiting_pick",
                "tick": 0,
            },
        })
        cargo.append(item)

    facilities = []
    for item in handoff["facilities"]:
        duration_ticks = max(1, math.ceil(float(item["transferDuration"]) / SIMULATION_TEST_TICK_SECONDS))
        facilities.append({
            **deepcopy(item),
            "durationTicks": duration_ticks,
            "occupiedCargoIds": [],
            "activeCargoId": None,
            "queueCargoIds": [],
        })

    source_position = deepcopy(outbound[0]["position"])
    conveyor_occupancy = {block_id: None for block_id in handoff["conveyorPath"]}
    allocations = scenario_audit["plan"]["allocations"]
    right_tasks = [
        task for task in scenario_audit["plan"]["tasks"]
        if task.get("sourceWarehouse") == "right"
    ]
    left_tasks = [
        task for task in scenario_audit["plan"]["tasks"]
        if task.get("sourceWarehouse") == "left"
    ]
    left_flows = [_build_left_break_pack_contract(topology, task) for task in left_tasks]
    left_flow = left_flows[0] if left_flows else None
    requested_quantity = sum(int(item.get("quantityBase") or 0) for item in allocations)
    right_quantity = sum(int(item.get("rightQuantityBase") or 0) for item in allocations)
    left_source_quantity = sum(int(item.get("leftSourceQuantityBase") or 0) for item in allocations)
    left_pick_quantity = sum(int(item.get("leftPickQuantityBase") or 0) for item in allocations)
    left_return_quantity = sum(int(item.get("leftReturnQuantityBase") or 0) for item in allocations)
    initial_right_cases = sum(
        int(item.get("quantityCases") or 0)
        for item in scenario_audit.get("inventory", [])
        if item.get("sourceWarehouse") == "right"
    )
    run_mode = str(scenario_audit.get("runMode") or SIMULATION_TEST_RUN_MODE)
    data_source = str(scenario_audit.get("dataSource") or "simulation_test")
    inventory_records = []
    for record in scenario_audit.get("inventory", []):
        matching_tasks = [
            task for task in scenario_audit["plan"]["tasks"]
            if task.get("sourceWarehouse") == record.get("sourceWarehouse")
            and task.get("sourceLocationId") == record.get("sourceLocationId")
            and task.get("skuId") == record.get("skuId")
        ]
        inventory_records.append({
            **deepcopy(record),
            "initialCases": int(record.get("quantityCases") or 0),
            "availableCases": int(record.get("quantityCases") or 0),
            "reservedCases": len(matching_tasks),
            "pickedCases": 0,
            "returnedQuantityBase": 0,
        })
    return {
        "scenarioId": scenario_audit["scenarioId"],
        "runMode": run_mode,
        "dataSource": data_source,
        "topologyRevision": scenario_audit["topologyRevision"],
        "simulationStateAuthoritative": True,
        "formalRouteStatus": "BLOCKED",
        "simulationRouteStatus": "PASS_SIMULATION_ONLY",
        "handoffContract": deepcopy(handoff),
        "syntheticTimingDisclosure": "Lift-A and transfer durations are synthetic_demo / simulation_test and are not physical commissioning evidence.",
        "order": {
            "orderId": scenario_audit["order"]["id"] if len(scenario_audit.get("orders") or [scenario_audit["order"]]) == 1 else "multiple",
            "quantityBase": requested_quantity,
            "rightQuantityBase": right_quantity,
            "leftSourceQuantityBase": left_source_quantity,
            "leftPickQuantityBase": left_pick_quantity,
            "leftReturnQuantityBase": left_return_quantity,
            "status": "both_sides_transporting",
            "canDeclareCompleted": False,
            "formalOrderCompleted": False,
            "physicalDeliveryConfirmed": False,
            "rightMergeAcceptedQuantityBase": 0,
            "leftPickedQuantityBase": 0,
            "leftReturnedQuantityBase": 0,
            "quantityAccounted": 0,
        },
        "inventory": {
            "isolationMode": "run_local_snapshot",
            "initializedOnce": True,
            "records": inventory_records,
            "right": {
                "initialCases": initial_right_cases,
                "availableCases": initial_right_cases,
                "inTransitCases": 0,
                "waitingArmCases": 0,
                "mergeArrivedCases": 0,
                **({
                    "plannedCases": len(right_tasks),
                    "reservedCases": len(right_tasks),
                } if run_mode == GENERAL_SIMULATION_RUN_MODE else {}),
            },
            "left": {
                "sourceLocationId": left_tasks[0]["sourceLocationId"] if left_tasks else None,
                "sourceQuantityBase": left_source_quantity,
                "pickQuantityBase": left_pick_quantity,
                "returnQuantityBase": left_return_quantity,
                "status": "waiting_split" if left_tasks else "not_required",
                "dataSource": data_source,
            },
        },
        "leftFlow": left_flow,
        "leftFlows": left_flows,
        "cargo": cargo,
        "shuttle": {
            "deviceId": SIMULATION_TEST_SHUTTLE_ID,
            "capacity": 1,
            "occupiedCargoIds": [],
            "phase": "idle",
            "routeIndex": 0,
            "outboundFrames": outbound,
            "returnFrames": returning,
            "currentPosition": source_position,
        },
        "lift": {
            "deviceId": "shuttle-lift-a",
            "capacity": 1,
            "occupiedCargoIds": [],
            "dataSource": "synthetic_demo",
            "validationStatus": "simulation_only",
        },
        "facilities": facilities,
        "conveyor": {
            "path": deepcopy(handoff["conveyorPath"]),
            "occupiedByBlock": conveyor_occupancy,
            "capacityPerBlock": 1,
            "beltHeight": float(facilities[-1]["geometry"]["beltHeight"]),
            "gridRows": int(topology["grid"]["rows"]),
            "gridCols": int(topology["grid"]["cols"]),
        },
        "armInfeed": {
            "nodeId": handoff["armReceiveNodeId"],
            "capacity": 1,
            "occupiedCargoIds": [],
            "status": "simulation_only_waiting_buffer",
        },
        "arm": {
            "armId": arm_layout["armId"],
            "pickupNodeId": arm_layout["pickup"]["topologyCellId"],
            "dropoffNodeId": arm_layout["dropoff"]["topologyCellId"],
            "capacity": 1,
            "phase": "idle",
            "phaseElapsedTicks": 0,
            "phaseDurationTicks": 0,
            "phaseProgress": 0.0,
            "activeCargoId": None,
            "cargoPlacement": "none",
            "completedCargoIds": [],
            "dataSource": "simulation_test",
            "simulationStateAuthoritative": True,
        },
        "mergeConveyor": {
            "path": merge_path,
            "occupiedByNode": {item["nodeId"]: None for item in merge_path},
            "capacityPerBlock": 1,
        },
        "mergePoint": {
            "mergePointId": arm_layout["mergePoint"]["mergePointId"],
            "topologyNodeId": arm_layout["mergePoint"]["topologyNodeId"],
            "nodeId": merge_path[-1]["nodeId"],
            "capacity": int(arm_layout["mergePoint"]["capacity"]),
            "acceptedCargoIds": [],
            "acceptedCount": 0,
            "dataSource": "simulation_test",
        },
        "events": [],
        "appliedEventIds": [],
        "resetGeneration": 0,
        "lastProgressTick": 0,
        "blockedTicks": 0,
        "diagnostics": [],
    }


def _event(
    sim: dict[str, Any],
    tick: int,
    event_type: str,
    cargo: dict[str, Any],
    from_node: str | None,
    to_node: str | None,
    device_id: str | None,
    quantity_base: int | None = None,
) -> dict[str, Any]:
    event_id = ":".join((
        str(sim["resetGeneration"]),
        str(cargo["cargoId"]),
        event_type,
        str(device_id or "none"),
        str(to_node or "none"),
    ))
    return {
        "eventId": event_id,
        "tick": tick,
        "eventType": event_type,
        "orderId": cargo["orderId"],
        "taskId": cargo["taskId"],
        "cargoId": cargo["cargoId"],
        "fromNodeId": from_node,
        "toNodeId": to_node,
        "deviceId": device_id,
        "quantityBase": int(cargo["quantityBase"] if quantity_base is None else quantity_base),
        "dataSource": str(sim.get("dataSource") or "simulation_test"),
    }


def _set_position(cargo: dict[str, Any], position: dict[str, Any], tick: int) -> None:
    previous = deepcopy(cargo["currentPosition"])
    cargo["previousPosition"] = previous
    cargo["currentPosition"] = deepcopy(position)
    cargo["currentNodeId"] = position["nodeId"]
    cargo["positionTick"] = tick
    cargo["movementState"] = {
        "fromNodeId": previous["nodeId"],
        "toNodeId": position["nodeId"],
        "segmentProgress": 1.0,
        "movementDurationSeconds": SIMULATION_TEST_TICK_SECONDS,
        "transportState": cargo["status"],
        "tick": tick,
    }


def _try_acquire_facility(
    sim: dict[str, Any],
    cargo: dict[str, Any],
    facility_index: int,
    tick: int,
    events: list[dict[str, Any]],
) -> bool:
    facility = sim["facilities"][facility_index]
    occupied = facility["occupiedCargoIds"]
    if cargo["cargoId"] in occupied:
        return True
    if len(occupied) >= int(facility["capacity"]):
        if cargo["cargoId"] not in facility["queueCargoIds"]:
            facility["queueCargoIds"].append(cargo["cargoId"])
        cargo["waitingTicks"] += 1
        cargo["blockedReason"] = f"waiting for {facility['deviceId']} capacity"
        return False
    facility["queueCargoIds"] = [item for item in facility["queueCargoIds"] if item != cargo["cargoId"]]
    occupied.append(cargo["cargoId"])
    from_node = cargo["currentNodeId"]
    cargo["facilityIndex"] = facility_index
    cargo["facilityElapsedTicks"] = 0
    cargo["currentDeviceId"] = facility["deviceId"]
    cargo["status"] = "dock_transferring"
    cargo["blockedReason"] = None
    events.append(_event(
        sim, tick, "handoff_accepted", cargo, from_node,
        facility["inputNodeId"], facility["deviceId"],
    ))
    return True


def _advance_facilities(sim: dict[str, Any], cargo: dict[str, Any], tick: int, events: list[dict[str, Any]]) -> bool:
    facility_index = cargo.get("facilityIndex")
    if facility_index is None:
        return False
    facility = sim["facilities"][int(facility_index)]
    cargo["facilityElapsedTicks"] += 1
    progress = cargo["facilityElapsedTicks"] / int(facility["durationTicks"])
    _set_position(
        cargo,
        _lerp_position(facility["outputNodeId"], facility["geometry"]["worldPoints"], progress),
        tick,
    )
    if cargo["facilityElapsedTicks"] < int(facility["durationTicks"]):
        return True

    next_index = int(facility_index) + 1
    if next_index < len(sim["facilities"]):
        next_facility = sim["facilities"][next_index]
        if len(next_facility["occupiedCargoIds"]) >= int(next_facility["capacity"]):
            cargo["waitingTicks"] += 1
            cargo["blockedReason"] = f"waiting for {next_facility['deviceId']} capacity"
            return False
        facility["occupiedCargoIds"].remove(cargo["cargoId"])
        events.append(_event(
            sim, tick, "handoff_completed", cargo,
            facility["inputNodeId"], facility["outputNodeId"], facility["deviceId"],
        ))
        cargo["facilityIndex"] = None
        return _try_acquire_facility(sim, cargo, next_index, tick, events)

    conveyor_path = sim["conveyor"]["path"]
    entry_index = 1 if len(conveyor_path) > 1 else 0
    entry_block = conveyor_path[entry_index]
    if sim["conveyor"]["occupiedByBlock"][entry_block] is not None:
        cargo["waitingTicks"] += 1
        cargo["blockedReason"] = f"waiting for Conveyor block {entry_block}"
        return False
    facility["occupiedCargoIds"].remove(cargo["cargoId"])
    events.append(_event(
        sim, tick, "handoff_completed", cargo,
        facility["inputNodeId"], facility["outputNodeId"], facility["deviceId"],
    ))
    sim["conveyor"]["occupiedByBlock"][entry_block] = cargo["cargoId"]
    cargo["facilityIndex"] = None
    cargo["currentDeviceId"] = entry_block
    cargo["conveyorIndex"] = entry_index
    cargo["status"] = "conveyor_transporting"
    cargo["blockedReason"] = None
    events.append(_event(
        sim, tick, "conveyor_entered", cargo,
        facility["outputNodeId"], entry_block, entry_block,
    ))
    return True


def _conveyor_position(sim: dict[str, Any], block_id: str) -> dict[str, Any]:
    floor, row_text, col_text, _ = block_id.split(":", 3)
    row, col = int(row_text), int(col_text)
    return {
        "nodeId": block_id,
        "row": row,
        "col": col,
        "level": 1,
        "worldX": col + 1.0 - float(sim["conveyor"]["gridCols"]) / 2.0,
        "worldY": float(sim["conveyor"]["beltHeight"]),
        "worldZ": float(sim["conveyor"]["gridRows"]) / 2.0 - (row + 1.0),
        "floor": floor,
    }


def _advance_conveyor(sim: dict[str, Any], cargo: dict[str, Any], tick: int, events: list[dict[str, Any]]) -> bool:
    index = cargo.get("conveyorIndex")
    if index is None:
        return False
    path = sim["conveyor"]["path"]
    current = path[int(index)]
    if int(index) == len(path) - 1:
        infeed = sim["armInfeed"]
        if len(infeed["occupiedCargoIds"]) >= int(infeed["capacity"]):
            cargo["waitingTicks"] += 1
            cargo["blockedReason"] = "waiting for arm infeed buffer capacity"
            return False
        sim["conveyor"]["occupiedByBlock"][current] = None
        infeed["occupiedCargoIds"].append(cargo["cargoId"])
        cargo["conveyorIndex"] = None
        cargo["currentDeviceId"] = None
        cargo["status"] = "waiting_arm"
        cargo["currentNodeId"] = infeed["nodeId"]
        cargo["blockedReason"] = None
        base = _conveyor_position(sim, current)
        base["nodeId"] = infeed["nodeId"]
        _set_position(cargo, base, tick)
        events.append(_event(
            sim, tick, "arm_infeed_arrived", cargo,
            current, infeed["nodeId"], infeed["nodeId"],
        ))
        return True
    next_block = path[int(index) + 1]
    if sim["conveyor"]["occupiedByBlock"][next_block] is not None:
        cargo["waitingTicks"] += 1
        cargo["blockedReason"] = f"waiting for Conveyor block {next_block}"
        return False
    sim["conveyor"]["occupiedByBlock"][current] = None
    sim["conveyor"]["occupiedByBlock"][next_block] = cargo["cargoId"]
    cargo["conveyorIndex"] = int(index) + 1
    cargo["currentDeviceId"] = next_block
    cargo["blockedReason"] = None
    _set_position(cargo, _conveyor_position(sim, next_block), tick)
    events.append(_event(
        sim, tick, "conveyor_advanced", cargo,
        current, next_block, next_block,
    ))
    return True


def _advance_merge_conveyor(
    sim: dict[str, Any],
    cargo: dict[str, Any],
    tick: int,
    events: list[dict[str, Any]],
) -> bool:
    index = cargo.get("mergeConveyorIndex")
    if index is None:
        return False
    route = sim["mergeConveyor"]["path"]
    current = route[int(index)]
    occupied = sim["mergeConveyor"]["occupiedByNode"]
    if int(index) == len(route) - 1:
        occupied[current["nodeId"]] = None
        cargo["mergeConveyorIndex"] = None
        cargo["currentDeviceId"] = sim["mergePoint"]["topologyNodeId"]
        cargo["status"] = "merge_arrived"
        cargo["blockedReason"] = None
        position = deepcopy(current)
        position["worldX"] += (int(cargo["caseIndex"]) - 2) * 0.18
        _set_position(cargo, position, tick)
        accepted = sim["mergePoint"]["acceptedCargoIds"]
        if cargo["cargoId"] not in accepted:
            accepted.append(cargo["cargoId"])
        sim["mergePoint"]["acceptedCount"] = len(accepted)
        events.append(_event(
            sim, tick, "merge_point_arrived", cargo,
            current["nodeId"], sim["mergePoint"]["nodeId"],
            sim["mergePoint"]["topologyNodeId"],
        ))
        return True
    next_step = route[int(index) + 1]
    if occupied[next_step["nodeId"]] is not None:
        cargo["waitingTicks"] += 1
        cargo["blockedReason"] = f"waiting for merge Conveyor block {next_step['nodeId']}"
        return False
    occupied[current["nodeId"]] = None
    occupied[next_step["nodeId"]] = cargo["cargoId"]
    cargo["mergeConveyorIndex"] = int(index) + 1
    cargo["currentDeviceId"] = next_step["nodeId"]
    cargo["blockedReason"] = None
    _set_position(cargo, next_step, tick)
    events.append(_event(
        sim, tick, "merge_conveyor_advanced", cargo,
        current["nodeId"], next_step["nodeId"], next_step["nodeId"],
    ))
    return True


def _start_arm_if_available(
    sim: dict[str, Any], tick: int, events: list[dict[str, Any]]
) -> bool:
    arm = sim["arm"]
    infeed = sim["armInfeed"]
    if arm["phase"] != "idle" or arm["activeCargoId"] is not None:
        return False
    if not infeed["occupiedCargoIds"]:
        return False
    cargo_id = infeed["occupiedCargoIds"][0]
    cargo = next(item for item in sim["cargo"] if item["cargoId"] == cargo_id)
    arm.update({
        "phase": "approach",
        "phaseElapsedTicks": 0,
        "phaseDurationTicks": SIMULATION_TEST_ARM_PHASE_TICKS["approach"],
        "phaseProgress": 0.0,
        "activeCargoId": cargo_id,
        "cargoPlacement": "pickup",
    })
    events.append(_event(
        sim, tick, "arm_pick_started", cargo,
        infeed["nodeId"], arm["pickupNodeId"], arm["armId"],
    ))
    return True


def _advance_arm(
    sim: dict[str, Any], tick: int, events: list[dict[str, Any]]
) -> bool:
    arm = sim["arm"]
    phase = str(arm["phase"])
    cargo_id = arm.get("activeCargoId")
    if phase == "idle" or not cargo_id:
        return False
    cargo = next(item for item in sim["cargo"] if item["cargoId"] == cargo_id)
    duration = int(SIMULATION_TEST_ARM_PHASE_TICKS[phase])
    was_complete = int(arm["phaseElapsedTicks"]) >= duration
    if not was_complete:
        arm["phaseElapsedTicks"] = min(duration, int(arm["phaseElapsedTicks"]) + 1)
        arm["phaseDurationTicks"] = duration
        arm["phaseProgress"] = arm["phaseElapsedTicks"] / duration
        # Publish the exact phase endpoint for one authoritative snapshot.
        # The next Tick transfers ownership/enters the next phase. This gives
        # the renderer a continuous 0..1 interval instead of jumping from the
        # final partial sample directly into the next pose.
        return True

    if phase == "release":
        route = sim["mergeConveyor"]["path"]
        first = route[0]
        occupied = sim["mergeConveyor"]["occupiedByNode"]
        if occupied[first["nodeId"]] is not None:
            cargo["waitingTicks"] += 1
            cargo["blockedReason"] = f"waiting for merge Conveyor block {first['nodeId']}"
            return False
        occupied[first["nodeId"]] = cargo_id
        cargo["status"] = "merge_conveyor_transporting"
        cargo["mergeConveyorIndex"] = 0
        cargo["currentDeviceId"] = first["nodeId"]
        cargo["blockedReason"] = None
        _set_position(cargo, first, tick)
        arm.update({
            "phase": "return_home",
            "phaseElapsedTicks": 0,
            "phaseDurationTicks": SIMULATION_TEST_ARM_PHASE_TICKS["return_home"],
            "phaseProgress": 0.0,
            "cargoPlacement": "none",
        })
        events.append(_event(
            sim, tick, "arm_release_completed", cargo,
            arm["dropoffNodeId"], first["nodeId"], arm["armId"],
        ))
        return True

    if phase == "return_home":
        if cargo_id not in arm["completedCargoIds"]:
            arm["completedCargoIds"].append(cargo_id)
        arm.update({
            "phase": "idle",
            "phaseElapsedTicks": 0,
            "phaseDurationTicks": 0,
            "phaseProgress": 0.0,
            "activeCargoId": None,
            "cargoPlacement": "none",
        })
        events.append(_event(
            sim, tick, "arm_cycle_completed", cargo,
            arm["dropoffNodeId"], arm["armId"], arm["armId"],
        ))
        return True

    next_phase = SIMULATION_TEST_ARM_PHASES[SIMULATION_TEST_ARM_PHASES.index(phase) + 1]
    if phase == "grip":
        infeed = sim["armInfeed"]
        if cargo_id not in infeed["occupiedCargoIds"]:
            raise RightHandoffConflict("Arm cargo no longer owns the infeed position")
        infeed["occupiedCargoIds"].remove(cargo_id)
        cargo["status"] = "arm_processing"
        cargo["currentDeviceId"] = arm["armId"]
        arm["cargoPlacement"] = "gripper"
    arm.update({
        "phase": next_phase,
        "phaseElapsedTicks": 0,
        "phaseDurationTicks": SIMULATION_TEST_ARM_PHASE_TICKS[next_phase],
        "phaseProgress": 0.0,
    })
    events.append(_event(
        sim, tick, f"arm_phase_{next_phase}", cargo,
        cargo["currentNodeId"], arm["armId"], arm["armId"],
    ))
    return True


def _set_left_cargo_position(cargo: dict[str, Any], position: dict[str, Any]) -> None:
    cargo["previousPosition"] = deepcopy(cargo["currentPosition"])
    cargo["currentPosition"] = deepcopy(position)
    cargo["currentNodeId"] = position["nodeId"]


def _advance_left_ctu_route(left: dict[str, Any]) -> bool:
    ctu = left["ctu"]
    frames = ctu[
        {
            "to_source": "toSourceFrames",
            "to_station": "toStationFrames",
            "returning": "toStorageFrames",
        }[ctu["phase"]]
    ]
    if int(ctu["routeIndex"]) >= len(frames) - 1:
        return False
    ctu["routeIndex"] = int(ctu["routeIndex"]) + 1
    ctu["currentPosition"] = deepcopy(frames[ctu["routeIndex"]]["position"])
    cargo = left["cargo"]
    if ctu.get("activeCargoId") == cargo["cargoId"]:
        _set_left_cargo_position(cargo, ctu["currentPosition"])
    partial = left.get("partialCase")
    if partial and ctu.get("activeCargoId") == partial["cargoId"]:
        partial["previousPosition"] = deepcopy(partial["currentPosition"])
        partial["currentPosition"] = deepcopy(ctu["currentPosition"])
        partial["currentNodeId"] = ctu["currentPosition"]["nodeId"]
    return True


def _advance_left_break_pack(
    state: dict[str, Any],
    sim: dict[str, Any],
    tick: int,
    events: list[dict[str, Any]],
) -> bool:
    left = sim["leftFlow"]
    ctu = left["ctu"]
    station = left["station"]
    ledger = left["ledger"]
    cargo = left["cargo"]
    source_expected = int(left["cargo"]["originalQuantityBase"])
    pick_expected = int(left["pickQuantityBase"])
    return_expected = int(left["returnQuantityBase"])
    phase = str(ctu["phase"])
    progressed = False

    if phase in {"to_source", "to_station", "returning"}:
        progressed = _advance_left_ctu_route(left)
        frames = ctu[
            {"to_source": "toSourceFrames", "to_station": "toStationFrames", "returning": "toStorageFrames"}[phase]
        ]
        if int(ctu["routeIndex"]) >= len(frames) - 1:
            if phase == "to_source":
                ctu.update({"phase": "picking", "phaseTicks": 0})
                cargo.update({
                    "status": "ctu_picking",
                    "currentDeviceId": ctu["deviceId"],
                })
                left["status"] = "ctu_picking"
                events.append(_event(
                    sim, tick, "left_ctu_pick_started", cargo,
                    left["sourceCellId"], left["sourceStopCellId"], ctu["deviceId"],
                ))
                progressed = True
            elif phase == "to_station":
                if station["occupiedCargoIds"] or station["reservedCargoIds"]:
                    left["status"] = "waiting_break_pack_capacity"
                    return progressed
                station["reservedCargoIds"] = [cargo["cargoId"]]
                station.update({"phase": "receiving", "phaseTicks": 0})
                ctu.update({"phase": "station_receiving", "phaseTicks": 0})
                cargo["status"] = "station_receiving"
                left["status"] = "station_receiving"
                events.append(_event(
                    sim, tick, "break_pack_handoff_started", cargo,
                    left["station"]["inputNodeId"], station["positionCellId"], station["deviceId"],
                ))
                progressed = True
            else:
                ctu.update({"phase": "returning_to_storage", "phaseTicks": 0})
                left["status"] = "returning_to_storage"
                partial = left["partialCase"]
                partial["status"] = "returning_to_storage"
                events.append(_event(
                    sim, tick, "partial_case_return_started", partial,
                    left["sourceStopCellId"], left["sourceCellId"], ctu["deviceId"], return_expected,
                ))
                progressed = True
    elif phase == "picking":
        ctu["phaseTicks"] = int(ctu["phaseTicks"]) + 1
        progressed = True
        if ctu["phaseTicks"] >= SIMULATION_TEST_LEFT_PHASE_TICKS["picking"]:
            if int(ledger["sourceQuantityBase"]) != source_expected or int(ledger["ctuQuantityBase"]) != 0:
                raise RightHandoffConflict("Left run-local source inventory cannot be acquired exactly once")
            record = next((
                item for item in sim["inventory"].get("records", [])
                if item.get("sourceWarehouse") == "left"
                and item.get("sourceLocationId") == cargo.get("sourceLocationId")
                and item.get("skuId") == cargo.get("skuId")
            ), None)
            if record is not None:
                if int(record["availableCases"]) <= 0 or int(record["reservedCases"]) <= 0:
                    raise RightHandoffConflict("Run-local left inventory reservation is unavailable")
                record["availableCases"] = int(record["availableCases"]) - 1
                record["reservedCases"] = int(record["reservedCases"]) - 1
                record["pickedCases"] = int(record["pickedCases"]) + 1
            ledger.update({"sourceQuantityBase": 0, "ctuQuantityBase": source_expected})
            ctu.update({
                "phase": "to_station", "phaseTicks": 0, "routeIndex": 0,
                "routeName": "to_station", "activeCargoId": cargo["cargoId"],
            })
            cargo.update({"status": "ctu_transporting", "currentDeviceId": ctu["deviceId"]})
            _set_left_cargo_position(cargo, ctu["currentPosition"])
            left["status"] = "ctu_transporting"
            sim["inventory"]["left"]["status"] = "ctu_transporting"
            events.append(_event(
                sim, tick, "left_ctu_pick_completed", cargo,
                left["sourceCellId"], left["sourceStopCellId"], ctu["deviceId"],
            ))
    elif phase == "station_receiving":
        ctu["phaseTicks"] = int(ctu["phaseTicks"]) + 1
        progressed = True
        if ctu["phaseTicks"] >= SIMULATION_TEST_LEFT_PHASE_TICKS["station_receiving"]:
            if int(ledger["ctuQuantityBase"]) != source_expected:
                raise RightHandoffConflict("Break-pack station received an invalid CTU quantity")
            ledger.update({"ctuQuantityBase": 0, "stationQuantityBase": source_expected})
            station["reservedCargoIds"] = []
            station["occupiedCargoIds"] = [cargo["cargoId"]]
            ctu.update({"phase": "waiting_break_pack", "phaseTicks": 0, "activeCargoId": None})
            station.update({"phase": "opening", "phaseTicks": 0})
            cargo.update({
                "status": "break_pack_opening",
                "currentDeviceId": station["deviceId"],
            })
            _set_left_cargo_position(cargo, station["position"])
            left["status"] = "break_pack_opening"
            events.append(_event(
                sim, tick, "break_pack_case_received", cargo,
                station["inputNodeId"], station["positionCellId"], station["deviceId"],
            ))
    elif phase == "waiting_break_pack":
        station["phaseTicks"] = int(station["phaseTicks"]) + 1
        progressed = True
        station_phase = str(station["phase"])
        duration_key = {
            "opening": "opening",
            "picking": "picking_units",
            "confirming": "confirming",
        }.get(station_phase)
        if duration_key is None:
            raise RightHandoffConflict(f"Unknown break-pack phase: {station_phase}")
        if station["phaseTicks"] >= SIMULATION_TEST_LEFT_PHASE_TICKS[duration_key]:
            station["phaseTicks"] = 0
            if station_phase == "opening":
                station["phase"] = "picking"
                cargo["status"] = "break_pack_picking"
                left["status"] = "break_pack_picking"
                events.append(_event(
                    sim, tick, "break_pack_case_opened", cargo,
                    station["positionCellId"], station["positionCellId"], station["deviceId"],
                ))
            elif station_phase == "picking":
                if int(ledger["stationQuantityBase"]) != source_expected or int(ledger["pickedQuantityBase"]) != 0:
                    raise RightHandoffConflict("Break-pack pick must conserve the original source case")
                ledger.update({"stationQuantityBase": return_expected, "pickedQuantityBase": pick_expected})
                cargo.update({
                    "pickedQuantityBase": pick_expected,
                    "remainingQuantityBase": return_expected,
                    "status": "break_pack_confirming",
                })
                station["phase"] = "confirming"
                left["status"] = "break_pack_confirming"
                events.append(_event(
                    sim, tick, "break_pack_units_picked", cargo,
                    station["positionCellId"], station["positionCellId"], station["deviceId"], pick_expected,
                ))
            else:
                partial = {
                    "cargoId": left["partialCargoId"],
                    "parentCargoId": cargo["cargoId"],
                    "orderId": cargo["orderId"],
                    "taskId": cargo["taskId"],
                    "skuId": cargo["skuId"],
                    "quantityCases": 0,
                    "quantityBase": return_expected,
                    "caseType": "partial_case" if return_expected else "empty_case",
                    "sourceLocationId": cargo["sourceLocationId"],
                    "currentNodeId": station["positionCellId"],
                    "currentDeviceId": station["deviceId"],
                    "status": "waiting_return",
                    "dataSource": "simulation_test",
                    "authoritative": False,
                    "preview": False,
                    "simulationStateAuthoritative": True,
                    "currentPosition": deepcopy(station["position"]),
                    "previousPosition": deepcopy(station["position"]),
                }
                left["partialCase"] = partial
                cargo["status"] = "split_accounted"
                station["phase"] = "loading_return"
                ctu.update({"phase": "loading_return", "phaseTicks": 0})
                left["status"] = "waiting_return"
                events.append(_event(
                    sim, tick, "partial_case_created", partial,
                    station["positionCellId"], station["outputNodeId"], station["deviceId"], return_expected,
                ))
    elif phase == "loading_return":
        ctu["phaseTicks"] = int(ctu["phaseTicks"]) + 1
        progressed = True
        if ctu["phaseTicks"] >= SIMULATION_TEST_LEFT_PHASE_TICKS["loading_return"]:
            partial = left["partialCase"]
            if int(ledger["stationQuantityBase"]) != return_expected or int(ledger["ctuQuantityBase"]) != 0:
                raise RightHandoffConflict("Return case quantity does not match the break-pack ledger")
            ledger.update({"stationQuantityBase": 0, "ctuQuantityBase": return_expected})
            station.update({"occupiedCargoIds": [], "phase": "idle", "phaseTicks": 0})
            ctu.update({
                "phase": "returning", "phaseTicks": 0, "routeIndex": 0,
                "routeName": "to_storage", "activeCargoId": partial["cargoId"],
            })
            partial.update({"status": "ctu_returning", "currentDeviceId": ctu["deviceId"]})
            partial["currentPosition"] = deepcopy(ctu["currentPosition"])
            partial["currentNodeId"] = ctu["currentPosition"]["nodeId"]
            left["status"] = "ctu_returning"
            events.append(_event(
                sim, tick, "partial_case_loaded", partial,
                station["positionCellId"], station["outputNodeId"], ctu["deviceId"], return_expected,
            ))
    elif phase == "returning_to_storage":
        ctu["phaseTicks"] = int(ctu["phaseTicks"]) + 1
        progressed = True
        if ctu["phaseTicks"] >= SIMULATION_TEST_LEFT_PHASE_TICKS["returning_to_storage"]:
            partial = left["partialCase"]
            if ledger["returnApplied"]:
                raise RightHandoffConflict("Partial-case inventory return was already applied")
            if int(ledger["ctuQuantityBase"]) != return_expected:
                raise RightHandoffConflict("Partial-case return quantity is not on the CTU")
            ledger.update({
                "sourceQuantityBase": return_expected,
                "ctuQuantityBase": 0,
                "returnedQuantityBase": return_expected,
                "returnApplied": True,
            })
            record = next((
                item for item in sim["inventory"].get("records", [])
                if item.get("sourceWarehouse") == "left"
                and item.get("sourceLocationId") == cargo.get("sourceLocationId")
                and item.get("skuId") == cargo.get("skuId")
            ), None)
            if record is not None:
                record["returnedQuantityBase"] = int(record.get("returnedQuantityBase") or 0) + return_expected
            ctu.update({"phase": "idle", "phaseTicks": 0, "activeCargoId": None})
            partial.update({
                "status": "stored_partial_case",
                "currentNodeId": left["sourceCellId"],
                "currentDeviceId": None,
            })
            partial["previousPosition"] = deepcopy(partial["currentPosition"])
            partial["currentPosition"] = deepcopy(left["sourcePosition"])
            left["status"] = "returned"
            left["quantityAudit"] = {
                "sourceConserved": int(ledger["pickedQuantityBase"]) + int(ledger["returnedQuantityBase"]) == source_expected,
                "orderQuantityAccounted": False,
            }
            sim["inventory"]["left"]["status"] = "returned_partial_case"
            events.append(_event(
                sim, tick, "partial_case_returned", partial,
                left["sourceStopCellId"], left["sourceCellId"], ctu["deviceId"], return_expected,
            ))

    robot = next((
        item for item in state.get("robots", []) if item.get("id") == ctu["deviceId"]
    ), None)
    if robot is not None:
        position = ctu["currentPosition"]
        active = ctu.get("activeCargoId")
        carried = cargo if active == cargo["cargoId"] else left.get("partialCase") if active else None
        action = None
        action_phase = str(ctu["phase"])
        if action_phase in {"picking", "station_receiving", "loading_return", "returning_to_storage"}:
            action_type = "deliver" if action_phase in {"station_receiving", "returning_to_storage"} else "pickup"
            elapsed = int(ctu.get("phaseTicks") or 0)
            action = {
                "type": action_type,
                "stage": ("lifting", "extending", "stowing")[min(2, elapsed)],
                "targetLayer": 1,
                "slotIndex": 0,
                "nodeId": cargo["sourceLocationId"] if action_phase in {"picking", "returning_to_storage"} else station["deviceId"],
                "cellId": left["sourceCellId"] if action_phase in {"picking", "returning_to_storage"} else station["positionCellId"],
                "skuId": cargo["skuId"],
                "verified": True,
            }
        robot.update({
            "currentNode": position["nodeId"],
            "currentCellId": position["nodeId"],
            "currentFloor": position.get("floor", "1F"),
            "currentRow": position["row"],
            "currentCol": position["col"],
            "currentLayer": 1,
            "status": "idle" if ctu["phase"] == "idle" else "moving",
            "phaseLabel": f"simulation_test left {ctu['phase']}",
            "load": 1 if carried else 0,
            "carrying": [{
                "cargoId": carried["cargoId"],
                "nodeId": carried["currentNodeId"],
                "cellId": carried["currentNodeId"],
                "layer": 1,
                "skuId": carried["skuId"],
                "qty": carried["quantityBase"],
                "dataSource": "simulation_test",
                "authoritative": False,
            }] if carried else [],
            "currentTaskId": left["taskId"] if ctu["phase"] != "idle" else None,
            "handlingAction": action,
            "simulationOnly": True,
            "dataSource": "simulation_test",
        })
    return progressed


def step_simulation_test_state(
    state: dict[str, Any],
    tick: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Advance the F3-B flow once. The caller owns the one and only clock."""
    sim = state["simulationTest"]
    events: list[dict[str, Any]] = []
    progressed = False

    # F5 left-side work shares this exact authoritative Tick with the right
    # flow. There is intentionally no timer, callback-driven state, or Run.
    # Old F4 snapshots intentionally have no ``leftFlow`` key.  Preserve their
    # right-only completion semantics even if a newer initial snapshot also
    # carried the queue field before it was downgraded for a recovery test.
    left_flows = [] if "leftFlow" not in sim else sim.get("leftFlows")
    if left_flows is None:
        left_flows = [sim["leftFlow"]] if sim.get("leftFlow") is not None else []
        sim["leftFlows"] = left_flows
    active_left_index = next(
        (index for index, item in enumerate(left_flows) if item.get("status") != "returned"),
        None,
    )
    if active_left_index is not None:
        active_left = left_flows[active_left_index]
        # A single CTU serves the queue.  After a preceding return it is already
        # at the source stop, so the next case starts there instead of replaying
        # an unrelated home-to-source path (which would be a visual jump).
        if active_left_index > 0 and not active_left.get("queueActivated"):
            frames = active_left["ctu"]["toSourceFrames"]
            active_left["ctu"].update({
                "phase": "picking",
                "phaseTicks": 0,
                "routeIndex": len(frames) - 1,
                "routeName": "to_source",
                "currentPosition": deepcopy(frames[-1]["position"]),
            })
            active_left["queueActivated"] = True
        sim["leftFlow"] = active_left
        progressed = _advance_left_break_pack(state, sim, tick, events) or progressed
        left_flows[active_left_index] = sim["leftFlow"]
    elif left_flows:
        sim["leftFlow"] = left_flows[-1]

    # Advance downstream first so every ownership transfer observes free
    # capacity before an upstream cargo attempts to enter it.
    for cargo in reversed(sim["cargo"]):
        if cargo["status"] == "merge_conveyor_transporting":
            progressed = _advance_merge_conveyor(sim, cargo, tick, events) or progressed
    progressed = _advance_arm(sim, tick, events) or progressed
    for cargo in reversed(sim["cargo"]):
        if cargo["status"] == "dock_transferring":
            progressed = _advance_facilities(sim, cargo, tick, events) or progressed
        elif cargo["status"] == "conveyor_transporting":
            progressed = _advance_conveyor(sim, cargo, tick, events) or progressed

    shuttle = sim["shuttle"]
    cargo_by_id = {item["cargoId"]: item for item in sim["cargo"]}
    occupied_id = shuttle.get("activeCargoId")

    if shuttle["phase"] == "idle":
        cargo = next((item for item in sim["cargo"] if item["status"] == "waiting_pick"), None)
        if cargo is not None:
            shuttle["occupiedCargoIds"] = [cargo["cargoId"]]
            shuttle["activeCargoId"] = cargo["cargoId"]
            shuttle["phase"] = "picking"
            shuttle["phaseTicks"] = 1
            cargo["status"] = "shuttle_picking"
            cargo["currentDeviceId"] = shuttle["deviceId"]
            events.append(_event(
                sim, tick, "cargo_pick_started", cargo,
                cargo["sourceLocationId"], cargo["currentNodeId"], shuttle["deviceId"],
            ))
            progressed = True
    elif shuttle["phase"] == "picking" and occupied_id:
        cargo = cargo_by_id[occupied_id]
        shuttle["phaseTicks"] -= 1
        if shuttle["phaseTicks"] <= 0:
            shuttle["phase"] = "outbound"
            shuttle["routeIndex"] = 0
            cargo["status"] = "shuttle_transporting"
            sim["inventory"]["right"]["availableCases"] -= 1
            sim["inventory"]["right"]["inTransitCases"] += 1
            record = next((
                item for item in sim["inventory"].get("records", [])
                if item.get("sourceWarehouse") == "right"
                and item.get("sourceLocationId") == cargo.get("sourceLocationId")
                and item.get("skuId") == cargo.get("skuId")
            ), None)
            if record is not None:
                if int(record["availableCases"]) <= 0 or int(record["reservedCases"]) <= 0:
                    raise RightHandoffConflict("Run-local right inventory reservation is unavailable")
                record["availableCases"] = int(record["availableCases"]) - 1
                record["reservedCases"] = int(record["reservedCases"]) - 1
                record["pickedCases"] = int(record["pickedCases"]) + 1
            events.append(_event(
                sim, tick, "cargo_pick_completed", cargo,
                cargo["sourceLocationId"], cargo["currentNodeId"], shuttle["deviceId"],
            ))
            progressed = True
    elif shuttle["phase"] == "outbound" and occupied_id:
        cargo = cargo_by_id[occupied_id]
        frames = shuttle["outboundFrames"]
        if shuttle["routeIndex"] < len(frames) - 1:
            previous_stage = frames[shuttle["routeIndex"]]["stage"]
            next_frame = frames[shuttle["routeIndex"] + 1]
            if (
                next_frame["stage"] == "enter_lift"
                and sim["lift"]["occupiedCargoIds"]
                and cargo["cargoId"] not in sim["lift"]["occupiedCargoIds"]
            ):
                cargo["waitingTicks"] += 1
                cargo["blockedReason"] = "waiting for Lift-A capacity"
                progressed = False
            else:
                shuttle["routeIndex"] += 1
                frame = frames[shuttle["routeIndex"]]
                shuttle["currentPosition"] = deepcopy(frame["position"])
                _set_position(cargo, frame["position"], tick)
                stage = frame["stage"]
                if stage == "enter_lift":
                    cargo["status"] = "lift_waiting"
                    if cargo["cargoId"] not in sim["lift"]["occupiedCargoIds"]:
                        sim["lift"]["occupiedCargoIds"] = [cargo["cargoId"]]
                        shuttle["occupiedCargoIds"] = []
                        cargo["currentDeviceId"] = sim["lift"]["deviceId"]
                        events.append(_event(
                            sim, tick, "lift_entered", cargo,
                            frames[shuttle["routeIndex"] - 1]["nodeId"], frame["nodeId"], sim["lift"]["deviceId"],
                        ))
                elif stage == "lift_moving":
                    cargo["status"] = "lift_moving"
                elif previous_stage in {"lift_moving", "exit_lift"} and stage not in {"lift_moving", "enter_lift"}:
                    cargo["status"] = "shuttle_to_handoff"
                    if sim["lift"]["occupiedCargoIds"]:
                        sim["lift"]["occupiedCargoIds"] = []
                        shuttle["occupiedCargoIds"] = [cargo["cargoId"]]
                        cargo["currentDeviceId"] = shuttle["deviceId"]
                        events.append(_event(
                            sim, tick, "lift_exited", cargo,
                            frames[shuttle["routeIndex"] - 1]["nodeId"], frame["nodeId"], sim["lift"]["deviceId"],
                        ))
                elif float(frame["level"]) <= 1.0:
                    cargo["status"] = "shuttle_to_handoff"
                else:
                    cargo["status"] = "shuttle_transporting"
                cargo["blockedReason"] = None
                progressed = True
        else:
            cargo["status"] = "waiting_dock"
            cargo["blockedReason"] = None
            shuttle["phase"] = "waiting_dock"
            progressed = True
    elif shuttle["phase"] == "waiting_dock" and occupied_id:
        cargo = cargo_by_id[occupied_id]
        if _try_acquire_facility(sim, cargo, 0, tick, events):
                shuttle["occupiedCargoIds"] = []
                shuttle["activeCargoId"] = None
                shuttle["phase"] = "returning"
                shuttle["routeIndex"] = 0
                progressed = True
    elif shuttle["phase"] == "returning":
        frames = shuttle["returnFrames"]
        if shuttle["routeIndex"] < len(frames) - 1:
            shuttle["routeIndex"] += 1
            shuttle["currentPosition"] = deepcopy(frames[shuttle["routeIndex"]]["position"])
            progressed = True
        else:
            shuttle["phase"] = "idle"
            shuttle["routeIndex"] = 0
            progressed = True

    # Mirror the backend-authoritative shuttle snapshot into the existing robot
    # contract so the one 3D scene and its existing interpolation stay reusable.
    robot = next((
        item for item in state.get("robots", [])
        if item.get("id") == shuttle["deviceId"]
    ), None)
    if robot is not None:
        position = shuttle["currentPosition"]
        visual_cargo_id = shuttle.get("activeCargoId")
        robot.update({
            "currentNode": position["nodeId"],
            "currentCellId": f"cell-r{position['row']}-c{position['col']}",
            "currentRow": position["row"],
            "currentCol": position["col"],
            "currentLayer": position["level"],
            "status": "idle" if shuttle["phase"] == "idle" else "moving",
            "phaseLabel": f"simulation_test · {shuttle['phase']}",
            # Visual carrying follows activeCargoId even while Lift-A owns the
            # capacity resource. This keeps the single cargo attached to the
            # Shuttle model throughout the vertical trip without duplicating
            # logical ownership.
            "load": 1 if visual_cargo_id else 0,
            "carrying": [deepcopy(cargo_by_id[visual_cargo_id])] if visual_cargo_id else [],
            "currentTaskId": cargo_by_id[visual_cargo_id]["taskId"] if visual_cargo_id else None,
        })

    # A cargo that entered the capacity-one infeed this tick starts the arm
    # cycle immediately, but arm movement begins on the next authoritative tick.
    progressed = _start_arm_if_available(sim, tick, events) or progressed

    right = sim["inventory"]["right"]
    waiting_arm = sum(item["status"] == "waiting_arm" for item in sim["cargo"])
    merge_arrived = sum(item["status"] == "merge_arrived" for item in sim["cargo"])
    right["waitingArmCases"] = waiting_arm
    right["mergeArrivedCases"] = merge_arrived
    planned_right_cases = len(sim["cargo"])
    picked_right_cases = int(right.get("initialCases") or 0) - int(right["availableCases"])
    right["inTransitCases"] = max(0, picked_right_cases - merge_arrived)
    sim["order"]["status"] = "right_merge_arrived" if merge_arrived == planned_right_cases else "right_transporting"

    legacy_right_only = not left_flows
    left_picked = sum(int(item["ledger"]["pickedQuantityBase"]) for item in left_flows)
    left_returned = sum(int(item["ledger"]["returnedQuantityBase"]) for item in left_flows)
    right_quantity = sum(
        int(item["quantityBase"])
        for item in sim["cargo"] if item["status"] == "merge_arrived"
    )
    quantity_accounted = right_quantity + left_picked
    right_complete = (
        merge_arrived == planned_right_cases
        and sim["arm"]["phase"] == "idle"
        and not sim["armInfeed"]["occupiedCargoIds"]
    )
    left_complete = all(
        item["status"] == "returned" and bool(item["ledger"]["returnApplied"])
        for item in left_flows
    )
    requested_quantity = int(sim["order"]["quantityBase"])
    expected_left_source = sum(int(item["ledger"]["initialSourceQuantityBase"]) for item in left_flows)
    quantity_conserved = (
        right_quantity == int(sim["order"].get("rightQuantityBase") or 0)
        if legacy_right_only
        else quantity_accounted == requested_quantity
        and left_picked + left_returned == expected_left_source
    )
    for left in left_flows:
        source_total = int(left["ledger"]["initialSourceQuantityBase"])
        left["quantityAudit"] = {
            "sourceConserved": int(left["ledger"]["pickedQuantityBase"]) + int(left["ledger"]["returnedQuantityBase"]) == source_total,
            "orderQuantityAccounted": quantity_accounted == requested_quantity,
        }
    sim["order"].update({
        "status": (
            "right_merge_arrived"
            if legacy_right_only and right_complete
            else "right_transporting"
            if legacy_right_only
            else "simulation_test_quantity_accounted"
            if right_complete and left_complete and quantity_conserved
            else "waiting_left_completion"
            if right_complete
            else "waiting_right_completion"
            if left_complete
            else "both_sides_transporting"
        ),
        "rightMergeAcceptedQuantityBase": right_quantity,
        "leftPickedQuantityBase": left_picked,
        "leftReturnedQuantityBase": left_returned,
        "quantityAccounted": quantity_accounted,
        "physicalDeliveryConfirmed": False,
        "formalOrderCompleted": False,
    })
    for state_order in state.get("orders", []):
        if state_order.get("status") == "cancelled":
            continue
        order_id = state_order.get("id")
        order_cargo = [item for item in sim["cargo"] if item.get("orderId") == order_id]
        order_left = [item for item in left_flows if item["cargo"].get("orderId") == order_id]
        order_right_quantity = sum(
            int(item["quantityBase"]) for item in order_cargo if item["status"] == "merge_arrived"
        )
        order_left_picked = sum(int(item["ledger"]["pickedQuantityBase"]) for item in order_left)
        order_left_returned = sum(int(item["ledger"]["returnedQuantityBase"]) for item in order_left)
        order_requested = sum(int(line.get("quantityBase") or 0) for line in state_order.get("lines", []))
        order_accounted = order_right_quantity + order_left_picked
        order_done = (
            all(item["status"] == "merge_arrived" for item in order_cargo)
            and all(item["status"] == "returned" for item in order_left)
            and order_accounted == order_requested
        )
        state_order.update({
            "status": "executing",
            "statusLabel": (
                f"simulation quantity accounted: {order_accounted}/{order_requested}; formal physical delivery remains BLOCKED"
                if order_done
                else f"simulation in progress: {order_accounted}/{order_requested}; formal delivery not confirmed"
            ),
            "progress": min(99, round(99 * order_accounted / max(1, order_requested))),
            "completedTasks": 0,
            "completedTick": None,
            "quantityAccounted": order_accounted,
            "rightMergeAcceptedQuantityBase": order_right_quantity,
            "leftPickedQuantityBase": order_left_picked,
            "leftReturnedQuantityBase": order_left_returned,
            "physicalDeliveryConfirmed": False,
            "formalOrderCompleted": False,
        })
        state_order.setdefault("decomposition", {})["stage"] = (
            "simulation_quantity_accounted" if order_done else "executing"
        )

    if events or progressed:
        sim["lastProgressTick"] = tick
        sim["blockedTicks"] = 0
    else:
        sim["blockedTicks"] += 1
        if sim["blockedTicks"] in {20, 60, 120}:
            sim["diagnostics"].append({
                "tick": tick,
                "reason": "simulation_test transport has no progress",
                "waitingCargoIds": [
                    item["cargoId"] for item in sim["cargo"]
                    if item["status"] != "merge_arrived"
                ],
            })
            sim["diagnostics"] = sim["diagnostics"][-10:]

    complete = right_complete and left_complete and quantity_conserved
    return events, complete
