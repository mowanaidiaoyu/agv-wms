"""Directed empty-pallet route from Arm 02 to the staging zone."""

from collections import deque
from copy import deepcopy
from typing import Any

from .conveyor import compile_conveyor_graph, conveyor_decks
from .pathfinding import cell_id, shortest_path_shuttle, shuttle_state_is_walkable

PALLET_ARM_ID = "picking-arm-02"
PALLET_BELT_START = "1F:78:50:lower"
PALLET_DROP_ZONE_ID = "zone-drop_zone"


def _find_route(graph: dict, zone: dict) -> list[dict]:
    nodes = graph["nodes"]
    if PALLET_BELT_START not in nodes:
        return []
    parents = {PALLET_BELT_START: None}
    pending = deque([PALLET_BELT_START])
    while pending:
        key = pending.popleft()
        node = nodes[key]
        if (
            int(zone["y"]) <= node["row"] < int(zone["y"]) + int(zone["height"])
            and int(zone["x"]) <= node["col"] < int(zone["x"]) + int(zone["width"])
        ):
            path = []
            while key is not None:
                step = nodes[key]
                path.append({
                    "blockId": key,
                    **{field: step[field] for field in ("row", "col", "deck", "ground")},
                })
                key = parents[key]
            return list(reversed(path))
        for successor in node["nexts"].values():
            if successor and successor not in parents:
                parents[successor] = key
                pending.append(successor)
    return []


def _stock_layout_connections(topology: dict, graph: dict, zone: dict) -> list[dict]:
    """Extend the stock layout across its two known gaps, preserving the main outlet."""
    nodes = graph["nodes"]
    cell_by_position = {
        (cell["row"], cell["col"]): cell
        for cell in topology["cells"] if (cell.get("floor") or "1F") == "1F"
    }
    if (
        (zone["x"], zone["y"], zone["width"], zone["height"]) != (66, 101, 5, 18)
        or nodes.get("1F:78:58:lower", {}).get("next") != "1F:78:60:lower"
        or nodes.get("1F:80:60:lower", {}).get("next") != "1F:82:60:lower"
        or nodes.get("1F:102:60:lower", {}).get("next") != "1F:104:60:lower"
        or not nodes.get("1F:102:62:lower")
    ):
        return []
    fillers = [(row, col) for row in (102, 103) for col in range(64, 68)]
    if any(cell_by_position.get(pos, {}).get("type") not in {None, "road"} for pos in fillers):
        return []

    patches = []
    for row in (78, 102):
        anchor = dict(cell_by_position[row, 60])
        decks = deepcopy(conveyor_decks(anchor))
        decks["lower"].update(outputSide="south", outputSides=["south"] if row == 78 else ["south", "west"])
        anchor["conveyorDecks"] = decks
        patches.append(anchor)
    for row, col in fillers:
        patches.append({
            "id": cell_id(row, col), "floor": "1F", "row": row, "col": col,
            "type": "conveyor", "walkable": True, "cost": 0.6, "levels": 1,
            "conveyorGround": False, "conveyorDirection": "right",
            "conveyorAnchor": {"row": 102, "col": col - col % 2},
        })
    return patches


def configure_pallet_transport(topology: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any] | None:
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
    if (
        not shuttle_state_is_walkable(topology, 76, 35, 1)
        or not shuttle_state_is_walkable(topology, 74, 47, 1)
        or not shortest_path_shuttle(topology, cell_id(76, 35), 1, cell_id(74, 47), 1)
    ):
        return None
    route = _find_route(graph, zone)
    patches: list[dict] = []
    if not route:
        patches = _stock_layout_connections(topology, graph, zone)
        if not patches:
            return None
        changed = {cell["id"] for cell in patches}
        overlay = {**topology, "cells": [cell for cell in topology["cells"] if cell["id"] not in changed] + patches}
        augmented = compile_conveyor_graph(overlay)
        if augmented["issues"]:
            return None
        route = _find_route(augmented, zone)
    if not route:
        return None
    return {
        "armId": PALLET_ARM_ID,
        "pickupCellId": cell_id(74, 47),
        "dropoffCellId": cell_id(78, 50),
        "dropZoneId": PALLET_DROP_ZONE_ID,
        "route": route,
        "conveyorCells": patches,
    }
