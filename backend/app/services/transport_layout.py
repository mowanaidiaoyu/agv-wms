from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any

from ..core.config import DATA_DIR
from ..schemas import (
    LayoutPosition,
    PickingArmConveyorStep,
    PickingArmEndpoint,
    PickingArmKinematics,
    PickingArmLayout,
    RightWarehouseMergePoint,
    TransportLayoutPlanResponse,
)
from .shuttle_topology import topology_revision


PLAN_ROOT = DATA_DIR / "transport_layout_plans"
PLAN_SUFFIX = ".arm-preview.json"
_DIRECTION_DELTA = {
    "right": (0, 1),
    "left": (0, -1),
    "up": (-1, 0),
    "down": (1, 0),
}
_DIRECTION_SIDE = {
    "right": "east",
    "left": "west",
    "up": "north",
    "down": "south",
}
_SIDE_DIRECTION = {side: direction for direction, side in _DIRECTION_SIDE.items()}
_OPPOSITE_SIDE = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
}


class LayoutRevisionMismatch(ValueError):
    pass


class LayoutValidationError(ValueError):
    pass


def _load_raw_plan(topology_id: str) -> dict[str, Any]:
    path = PLAN_ROOT / f"{topology_id}{PLAN_SUFFIX}"
    return json.loads(path.read_text(encoding="utf-8"))


def _position(raw: dict[str, Any]) -> LayoutPosition:
    return LayoutPosition(
        floor=str(raw.get("floor") or "1F"),
        row=int(raw["row"]),
        col=int(raw["col"]),
        level=int(raw.get("level") or 1),
    )


def _belt_height(cell: dict[str, Any], level: int) -> float | None:
    if bool(cell.get("conveyorGround")):
        return 0.6 if level == 1 else None
    if int(cell.get("levels") or 1) < level:
        return None
    return 1.5 + (level - 1) * 1.5


def _anchor_position(cell: dict[str, Any]) -> tuple[int, int]:
    anchor = cell.get("conveyorAnchor") or {}
    row = int(anchor.get("row", int(cell["row"]) - int(cell["row"]) % 2))
    col = int(anchor.get("col", int(cell["col"]) - int(cell["col"]) % 2))
    return row, col


def _deck_direction(
    cell: dict[str, Any],
    level: int,
    cells_by_position: dict[tuple[str, int, int], dict[str, Any]],
) -> str:
    floor = str(cell.get("floor") or "1F")
    anchor_row, anchor_col = _anchor_position(cell)
    anchor = cells_by_position.get((floor, anchor_row, anchor_col), cell)
    stored = anchor.get("conveyorDecks")
    deck_name = "lower" if level == 1 else "upper"
    if isinstance(stored, dict):
        deck = stored.get(deck_name)
        if not isinstance(deck, dict) or not deck.get("enabled", True):
            raise LayoutValidationError(f"{deck_name} deck is unavailable at {anchor['id']}")
        output_side = str(deck.get("outputSide") or "")
        direction = _SIDE_DIRECTION.get(output_side)
        if direction is None:
            raise LayoutValidationError(
                f"Unsupported {deck_name} outputSide at {anchor['id']}: {output_side}"
            )
        return direction

    legacy_direction = str(anchor.get("conveyorDirection") or "right")
    if legacy_direction not in _DIRECTION_DELTA:
        raise LayoutValidationError(f"Unsupported conveyor direction at {anchor['id']}")
    if deck_name == "lower" or bool(anchor.get("conveyorGround")):
        return legacy_direction
    return {
        "right": "left",
        "left": "right",
        "up": "down",
        "down": "up",
    }[legacy_direction]


def _configured_turn(
    nodes: list[dict[str, Any]],
    level: int,
    incoming_direction: str,
    outgoing_direction: str,
) -> dict[str, Any] | None:
    deck_name = "lower" if level == 1 else "upper"
    expected_input = _OPPOSITE_SIDE[_DIRECTION_SIDE[incoming_direction]]
    expected_output = _DIRECTION_SIDE[outgoing_direction]
    return next(
        (
            node
            for node in nodes
            if node.get("type") == "turn_transfer"
            and str(node.get("transferDeck") or deck_name) == deck_name
            and node.get("transferInputSide") == expected_input
            and node.get("transferOutputSide") == expected_output
        ),
        None,
    )


def _merge_point(
    topology: dict[str, Any], raw: dict[str, Any]
) -> RightWarehouseMergePoint:
    node_id = str(raw["topologyNodeId"])
    node = next(
        (item for item in topology.get("nodes", []) if str(item.get("id")) == node_id),
        None,
    )
    if node is None or node.get("type") != "lift_transfer":
        raise LayoutValidationError(f"Merge point device is not a lift_transfer: {node_id}")
    row = int(raw["row"])
    col = int(raw["col"])
    if int(node["row"]) != row or int(node["col"]) != col:
        raise LayoutValidationError(f"Merge point coordinates do not match {node_id}")
    capacity = int(raw["capacity"])
    if capacity != int(node.get("capacity") or 1):
        raise LayoutValidationError(f"Merge point capacity does not match {node_id}")
    return RightWarehouseMergePoint(
        mergePointId=str(raw["mergePointId"]),
        topologyNodeId=node_id,
        row=row,
        col=col,
        level=int(raw["level"]),
        capacity=capacity,
        occupiedCargoIds=[],
        acceptedCargoIds=[],
        status="available",
        acceptedWarehouse="right",
        cargoOrderIds={},
    )


def _strict_conveyor_path(
    topology: dict[str, Any],
    start_cell_id: str,
    level: int,
    expected_endpoint_id: str,
    merge_point: RightWarehouseMergePoint,
    required_waypoints: list[dict[str, Any]],
) -> tuple[list[PickingArmConveyorStep], str]:
    all_cells = {str(cell["id"]): cell for cell in topology.get("cells", [])}
    conveyors = {
        cell_id: cell
        for cell_id, cell in all_cells.items()
        if cell.get("type") == "conveyor"
    }
    cells_by_position = {
        (str(cell.get("floor") or "1F"), int(cell["row"]), int(cell["col"])): cell
        for cell in all_cells.values()
    }
    nodes_by_position: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for node in topology.get("nodes", []):
        key = (str(node.get("floor") or "1F"), int(node["row"]), int(node["col"]))
        nodes_by_position.setdefault(key, []).append(node)

    endpoint_cell = all_cells.get(expected_endpoint_id)
    if endpoint_cell is None:
        raise LayoutValidationError(f"Merge-point cell is missing: {expected_endpoint_id}")
    endpoint_position = (
        str(endpoint_cell.get("floor") or "1F"),
        int(endpoint_cell["row"]),
        int(endpoint_cell["col"]),
    )
    if endpoint_position[1:] != (merge_point.row, merge_point.col):
        raise LayoutValidationError("Merge-point cell coordinates do not match the contract")

    current = conveyors.get(start_cell_id)
    if current is None or _belt_height(current, level) is None:
        raise LayoutValidationError(
            f"{start_cell_id} is not a physical conveyor at level {level}"
        )

    steps: list[PickingArmConveyorStep] = []
    visited: set[str] = set()
    incoming_direction: str | None = None
    for _ in range(500):
        cell_id = str(current["id"])
        if cell_id in visited:
            raise LayoutValidationError(f"Conveyor cycle detected at {cell_id}")
        visited.add(cell_id)
        floor = str(current.get("floor") or "1F")
        row = int(current["row"])
        col = int(current["col"])
        height = _belt_height(current, level)
        if height is None:
            raise LayoutValidationError(f"Conveyor level {level} is missing at {cell_id}")
        direction = _deck_direction(current, level, cells_by_position)
        delta = _DIRECTION_DELTA[direction]
        key = (floor, row, col)
        devices = nodes_by_position.get(key, [])
        transfer = None
        if incoming_direction is not None and incoming_direction != direction:
            transfer = _configured_turn(devices, level, incoming_direction, direction)
            if transfer is None:
                raise LayoutValidationError(
                    f"Direction changes {incoming_direction}->{direction} at {cell_id} "
                    "without a matching configured turn_transfer"
                )
        step = PickingArmConveyorStep(
            cellId=cell_id,
            position=LayoutPosition(floor=floor, row=row, col=col, level=level),
            direction=direction,
            beltHeight=height,
            deviceIds=[str(node["id"]) for node in devices],
            transitionToNext="device_transfer" if transfer else "conveyor",
            transferDeviceId=str(transfer["id"]) if transfer else None,
        )
        steps.append(step)
        next_key = (floor, row + delta[0], col + delta[1])
        if next_key == endpoint_position:
            endpoint_devices = nodes_by_position.get(next_key, [])
            if merge_point.topologyNodeId not in {
                str(node.get("id")) for node in endpoint_devices
            }:
                raise LayoutValidationError(
                    f"Merge point {merge_point.topologyNodeId} is missing at r{merge_point.row},c{merge_point.col}"
                )
            steps.append(PickingArmConveyorStep(
                cellId=expected_endpoint_id,
                position=LayoutPosition(
                    floor=floor,
                    row=merge_point.row,
                    col=merge_point.col,
                    level=level,
                ),
                direction=direction,
                beltHeight=height,
                deviceIds=[str(node["id"]) for node in endpoint_devices],
                transitionToNext="endpoint",
            ))
            break
        next_cell = cells_by_position.get(next_key)
        if next_cell is None or next_cell.get("type") != "conveyor":
            raise LayoutValidationError(
                f"Conveyor path is disconnected before r{next_key[1]},c{next_key[2]}"
            )
        if _belt_height(next_cell, level) != height:
            raise LayoutValidationError(
                f"Conveyor level {level} is discontinuous from {cell_id} to {next_cell['id']}"
            )
        incoming_direction = direction
        current = next_cell
    else:
        raise LayoutValidationError("Conveyor path guard reached")

    positions = [(step.position.row, step.position.col) for step in steps]
    cursor = 0
    for waypoint in required_waypoints:
        target = (int(waypoint["row"]), int(waypoint["col"]))
        try:
            waypoint_index = positions.index(target, cursor)
        except ValueError as error:
            raise LayoutValidationError(f"Required route waypoint r{target[0]},c{target[1]} was not reached") from error
        step = steps[waypoint_index]
        expected_transfer = waypoint.get("transferDeviceId")
        if expected_transfer and step.transferDeviceId != str(expected_transfer):
            raise LayoutValidationError(
                f"Route waypoint r{target[0]},c{target[1]} does not use {expected_transfer}"
            )
        expected_node = waypoint.get("topologyNodeId")
        if expected_node and str(expected_node) not in step.deviceIds:
            raise LayoutValidationError(
                f"Route waypoint r{target[0]},c{target[1]} does not contain {expected_node}"
            )
        cursor = waypoint_index + 1
    return steps, (
        f"Topology-directed upper-deck route reached merge point "
        f"{merge_point.mergePointId} at r{merge_point.row},c{merge_point.col}"
    )


def _endpoint(
    raw: dict[str, Any], cells: dict[str, dict[str, Any]]
) -> PickingArmEndpoint:
    cell_id = str(raw["topologyCellId"])
    cell = cells.get(cell_id)
    if cell is None or cell.get("type") != "conveyor":
        raise LayoutValidationError(f"Picking-arm endpoint is not a conveyor: {cell_id}")
    position = _position(raw)
    if (
        int(cell["row"]) != position.row
        or int(cell["col"]) != position.col
        or str(cell.get("floor") or "1F") != position.floor
    ):
        raise LayoutValidationError(f"Endpoint coordinates do not match {cell_id}")
    height = _belt_height(cell, position.level)
    if height is None or not math.isclose(height, float(raw["beltHeight"])):
        raise LayoutValidationError(f"Endpoint belt height does not match {cell_id}")
    direction = str(cell.get("conveyorDirection") or "right")
    if direction != str(raw["direction"]):
        raise LayoutValidationError(f"Endpoint direction does not match {cell_id}")
    return PickingArmEndpoint(
        nodeId=str(raw["nodeId"]),
        topologyCellId=cell_id,
        position=position,
        beltHeight=height,
        deckIndex=int(raw["deckIndex"]),
        direction=direction,
        provenance="topology",
        validationStatus="PASS",
        evidence=[str(raw.get("note") or "Validated against topology")],
    )


def build_transport_layout_plan(
    topology: dict[str, Any], raw: dict[str, Any]
) -> TransportLayoutPlanResponse:
    arm_raw = raw["pickingArmPreview"]
    install_raw = arm_raw["installation"]
    cells = {str(cell["id"]): cell for cell in topology.get("cells", [])}
    occupied = next(
        (
            cell
            for cell in topology.get("cells", [])
            if int(cell["row"]) == int(install_raw["row"])
            and int(cell["col"]) == int(install_raw["col"])
            and str(cell.get("floor") or "1F") == str(install_raw.get("floor") or "1F")
        ),
        None,
    )
    if occupied is not None:
        raise LayoutValidationError(
            f"Picking-arm installation collides with topology cell {occupied['id']}"
        )

    pickup = _endpoint(arm_raw["pickup"], cells)
    dropoff = _endpoint(arm_raw["dropoff"], cells)
    base_row = int(install_raw["row"])
    base_col = int(install_raw["col"])
    work_radius = float(install_raw["workRadius"])
    for endpoint in (pickup, dropoff):
        distance = math.hypot(endpoint.position.row - base_row, endpoint.position.col - base_col)
        if distance > work_radius:
            raise LayoutValidationError(
                f"{endpoint.topologyCellId} is outside the arm work radius"
            )

    merge_point = _merge_point(topology, arm_raw["mergePoint"])
    route_raw = arm_raw["previewRoute"]
    expected_endpoint = str(route_raw["expectedEndpointId"])
    path, stop_reason = _strict_conveyor_path(
        topology,
        dropoff.topologyCellId,
        dropoff.position.level,
        expected_endpoint,
        merge_point,
        list(route_raw.get("requiredWaypoints") or []),
    )
    if path[-1].cellId != expected_endpoint:
        raise LayoutValidationError(
            f"Strict conveyor endpoint changed: expected {expected_endpoint}, got {path[-1].cellId}"
        )
    downstream = arm_raw["downstream"]
    if len(path) < 2 or path[1].cellId != str(downstream["nextRequiredCellId"]):
        raise LayoutValidationError("Picking-arm dropoff has no legal downstream conveyor")

    lift = arm_raw["liftClearance"]
    arm = PickingArmLayout(
        armId=str(arm_raw["armId"]),
        validationStatus="PARTIAL",
        installation=LayoutPosition(
            floor=str(install_raw.get("floor") or "1F"),
            row=base_row,
            col=base_col,
            level=int(install_raw.get("level") or 1),
        ),
        mountHeight=float(install_raw["mountHeight"]),
        workRadius=work_radius,
        kinematics=PickingArmKinematics(**arm_raw["kinematics"]),
        pickup=pickup,
        dropoff=dropoff,
        liftBId=str(lift["liftId"]),
        liftBPosition=LayoutPosition(
            floor="1F", row=int(lift["row"]), col=int(lift["col"]), level=1
        ),
        liftBClearanceCells=float(lift["minimumPlanarDistanceCells"]),
        downstreamCellId=str(downstream["nextRequiredCellId"]),
        downstreamStatus="PASS",
        previewConveyorPath=path,
        previewEndpointId=path[-1].cellId,
        previewPathStatus="PASS",
        previewStopReason=stop_reason,
        mergePoint=merge_point,
        actionSequence=[str(value) for value in arm_raw["actionSequence"]],
        requiredAuthoritativeSignals=[
            str(value) for value in arm_raw["requiredAuthoritativeSignals"]
        ],
        canDeclareDelivery=False,
        evidence=[str(value) for value in arm_raw.get("evidence") or []],
    )
    return TransportLayoutPlanResponse(
        planId=str(raw["planId"]),
        schemaVersion=str(raw["schemaVersion"]),
        topologyId=str(raw["topologyId"]),
        topologyRevision=str(raw["topologyRevision"]),
        overallStatus="PARTIAL",
        pickingArm=arm,
        summary={
            "previewOnly": True,
            "inventoryMutated": False,
            "ordersCompleted": False,
            "formalTopologyMutated": False,
            "conveyorNodeCount": len(path),
        },
    )


def load_transport_layout_plan(topology: dict[str, Any]) -> TransportLayoutPlanResponse:
    raw = _load_raw_plan(str(topology["id"]))
    actual_revision = topology_revision(topology)
    if str(raw.get("topologyId")) != str(topology["id"]):
        raise LayoutRevisionMismatch(
            f"Layout topologyId={raw.get('topologyId')} does not match {topology['id']}"
        )
    if str(raw.get("topologyRevision")) != actual_revision:
        raise LayoutRevisionMismatch(
            f"Layout topologyRevision={raw.get('topologyRevision')} does not match {actual_revision}"
        )
    snapshot = deepcopy(topology)
    plan = build_transport_layout_plan(topology, raw)
    if topology != snapshot:
        raise RuntimeError("Picking-arm preview generation mutated the formal topology")
    return plan
