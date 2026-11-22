"""Upper conveyor route from Arm 02 to the existing Arm 01 outlet."""

from collections import deque
from copy import deepcopy
from typing import Any

from .conveyor import compile_conveyor_graph, conveyor_decks
from .pathfinding import cell_id, shortest_path_shuttle, shuttle_state_is_walkable

PALLET_ARM_ID = "picking-arm-02"
PALLET_BELT_START = "1F:78:50:upper"
PALLET_OUTLET_TRUNK = "1F:102:60:upper"
PALLET_OUTLET = "1F:102:62:upper"
PALLET_DELIVERY_ARM_ID = "arm-01"
PALLET_DROP_ZONE_ID = "zone-drop_zone"
PALLET_DROP_ROW = 104
PALLET_DROP_COL = 68


def _find_route(graph: dict) -> list[dict]:
    nodes = graph["nodes"]
    if (
        PALLET_BELT_START not in nodes
        or nodes.get(PALLET_OUTLET, {}).get("next") != PALLET_OUTLET_TRUNK
    ):
        return []
    parents = {PALLET_BELT_START: None}
    pending = deque([PALLET_BELT_START])
    while pending:
        key = pending.popleft()
        node = nodes[key]
        if key == PALLET_OUTLET_TRUNK:
            # Like outbound cartons, leave the trunk at the existing side outlet.
            path = [PALLET_OUTLET]
            while key is not None:
                path.append(key)
                key = parents[key]
            return [
                {"blockId": block, **{field: nodes[block][field] for field in ("row", "col", "deck", "ground")}}
                for block in reversed(path)
            ]
        for successor in node["nexts"].values():
            if successor in nodes and nodes[successor]["deck"] == "upper" and successor not in parents:
                parents[successor] = key
                pending.append(successor)
    return []


def _stock_layout_connections(topology: dict, graph: dict) -> list[dict]:
    """Connect Arm 02 to the upper trunk through existing blocks for pallet routing."""
    nodes = graph["nodes"]
    cell_by_position = {
        (cell["row"], cell["col"]): cell
        for cell in topology["cells"] if (cell.get("floor") or "1F") == "1F"
    }
    if (
        nodes.get("1F:78:60:upper", {}).get("next") != "1F:78:58:upper"
        or nodes.get("1F:84:60:upper", {}).get("next") != "1F:86:60:upper"
    ):
        return []

    connections = {
        **{(78, col): (["east"], ["west"]) for col in range(50, 60, 2)},
        (78, 60): (["east"], ["south"]),
        (80, 60): (["north"], ["south"]),
        (82, 60): (["west", "north"], ["east", "south"]),
        (84, 60): (["east", "west", "north"], ["south"]),
    }
    patches = []
    for position, (inputs, outputs) in connections.items():
        source = cell_by_position.get(position)
        if not source or source.get("type") != "conveyor":
            return []
        anchor = dict(source)
        decks = deepcopy(conveyor_decks(anchor))
        if not decks.get("upper", {}).get("enabled"):
            return []
        decks["upper"].update(
            inputSide=inputs[0], inputSides=inputs,
            outputSide=outputs[0], outputSides=outputs,
        )
        anchor["conveyorDecks"] = decks
        patches.append(anchor)
    return patches


def configure_pallet_transport(
    topology: dict[str, Any], graph: dict[str, Any], secondary_arms: list[dict[str, Any]]
) -> dict[str, Any] | None:
    zone = next((
        item for item in topology.get("zones", [])
        if item.get("id") == PALLET_DROP_ZONE_ID and item.get("type") == "drop_zone"
        and (item.get("floor") or "1F") == "1F"
    ), None)
    if zone is None or not any(
        node.get("id") == PALLET_ARM_ID and node.get("type") == "picking_arm"
        for node in topology.get("nodes", [])
    ):
        return None
    port_cell_id = cell_id(102, 62)
    if (
        not any(
            arm["armId"] == PALLET_DELIVERY_ARM_ID
            and any(port["cellId"] == port_cell_id for port in arm["ports"])
            for arm in secondary_arms
        )
        or not (zone["y"] <= PALLET_DROP_ROW < zone["y"] + zone["height"])
        or not (zone["x"] <= PALLET_DROP_COL < zone["x"] + zone["width"])
        or not shuttle_state_is_walkable(topology, 76, 35, 1)
        or not shuttle_state_is_walkable(topology, 74, 47, 1)
        or not shortest_path_shuttle(topology, cell_id(76, 35), 1, cell_id(74, 47), 1)
    ):
        return None
    route = _find_route(graph)
    if not route:
        patches = _stock_layout_connections(topology, graph)
        if not patches:
            return None
        changed = {cell["id"] for cell in patches}
        overlay = {**topology, "cells": [cell for cell in topology["cells"] if cell["id"] not in changed] + patches}
        augmented = compile_conveyor_graph(overlay)
        # Declaring the upper deck makes the inherited lower deck explicit
        # too. It may report a preexisting direction gap; make sure neither
        # the lower graph nor its capacity changed and validate the new deck.
        lower_nodes = {key: node for key, node in graph["nodes"].items() if node["deck"] == "lower"}
        augmented_lower = {
            key: node for key, node in augmented["nodes"].items() if node["deck"] == "lower"
        }
        if lower_nodes != augmented_lower or any(issue.get("deck") != "lower" for issue in augmented["issues"]):
            return None
        route = _find_route(augmented)
    if not route:
        return None
    return {
        "armId": PALLET_ARM_ID,
        "pickupCellId": cell_id(74, 47),
        "dropoffCellId": cell_id(78, 50),
        "deliveryArmId": PALLET_DELIVERY_ARM_ID,
        "portCellId": port_cell_id,
        "dropZoneId": PALLET_DROP_ZONE_ID,
        "dropRow": PALLET_DROP_ROW,
        "dropCol": PALLET_DROP_COL,
        "route": route,
    }
