from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..core.config import DATA_DIR


SHUTTLE_LEVELS = (1, 2, 3, 4)
SHUTTLE_LEVEL_HEIGHT = 1.4
# A shuttle level is an internal running bay, not the rack roof.  The running
# surface sits one quarter of a bay above that bay's lower boundary; L4
# therefore retains vehicle clearance below the structural top at 4 * 1.4.
SHUTTLE_TRACK_SURFACE_OFFSET = SHUTTLE_LEVEL_HEIGHT / 4
SHUTTLE_RACK_TOP_Y = len(SHUTTLE_LEVELS) * SHUTTLE_LEVEL_HEIGHT
SHUTTLE_COORDINATE_CONTRACT_REVISION = "shuttle-internal-levels-v1"
DEFAULT_FLOOR = "1F"


def shuttle_level_world_y(level: int) -> float:
    if level not in SHUTTLE_LEVELS:
        raise ValueError(f"Unsupported shuttle level: {level}")
    return SHUTTLE_TRACK_SURFACE_OFFSET + (level - 1) * SHUTTLE_LEVEL_HEIGHT


def topology_revision(topology: dict[str, Any]) -> str:
    canonical = json.dumps(topology, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    version = str(topology.get("meta", {}).get("version", "unknown"))
    return f"{version}:{digest[:16]}"


def track_node_id(level: int, row: int, col: int) -> str:
    return f"L{level}-r{row}-c{col}"


def _world_position(topology: dict[str, Any], level: int, row: int, col: int) -> tuple[float, float, float]:
    rows = int(topology["grid"]["rows"])
    cols = int(topology["grid"]["cols"])
    # A slotAisles key is the top-left of a 2x2 visual slot. Its track center is
    # therefore half a cell beyond the normal cell-center transform.
    return (
        col + 1.0 - cols / 2.0,
        shuttle_level_world_y(level),
        rows / 2.0 - (row + 1.0),
    )


def _cell_lookup(topology: dict[str, Any]) -> dict[tuple[str, int, int], dict[str, Any]]:
    return {
        (str(cell.get("floor") or DEFAULT_FLOOR), int(cell["row"]), int(cell["col"])): cell
        for cell in topology.get("cells", [])
    }


def _slot_mask(
    zone: dict[str, Any],
    row: int,
    col: int,
    cells: dict[tuple[str, int, int], dict[str, Any]],
) -> int:
    stored = (zone.get("slotAisles") or {}).get(f"{row},{col}")
    if stored is not None:
        return int(stored)
    floor = str(zone.get("floor") or DEFAULT_FLOOR)
    all_road = all(
        cells.get((floor, row + d_row, col + d_col), {}).get("type") == "road"
        for d_row in (0, 1)
        for d_col in (0, 1)
    )
    return 0b1111 if all_road else 0


def shuttle_slot_mask(
    topology: dict[str, Any],
    zone: dict[str, Any],
    row: int,
    col: int,
    cell_lookup: dict[tuple[str, int, int], dict[str, Any]] | None = None,
) -> int:
    """Expose the renderer-compatible slot mask for read-only contract audits."""
    return _slot_mask(zone, row, col, cell_lookup or _cell_lookup(topology))


def shuttle_slot_world_position(
    topology: dict[str, Any], level: int, row: int, col: int
) -> tuple[float, float, float]:
    """Return the center used by Shuttle rack slots and track nodes."""
    return _world_position(topology, level, row, col)


def _track_type(position: tuple[int, int], positions: set[tuple[int, int]]) -> str:
    row, col = position
    directions = [
        (d_row, d_col)
        for d_row, d_col in ((-2, 0), (2, 0), (0, -2), (0, 2))
        if (row + d_row, col + d_col) in positions
    ]
    if len(directions) > 2:
        return "intersection"
    if len(directions) == 2:
        same_axis = directions[0][0] == directions[1][0] or directions[0][1] == directions[1][1]
        return "straight" if same_axis else "turn"
    return "straight"


def build_shuttle_track_graph(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build L1-L4 nodes from the exact slotAisles bit mask used by the 3D renderer."""
    cells = _cell_lookup(topology)
    positions: dict[int, dict[tuple[int, int], str]] = {level: {} for level in SHUTTLE_LEVELS}
    for zone in topology.get("zones", []):
        if zone.get("type") != "shuttle":
            continue
        removed = set(zone.get("removedSlots") or [])
        for row in range(int(zone["y"]), int(zone["y"]) + int(zone["height"]) - 1, 2):
            for col in range(int(zone["x"]), int(zone["x"]) + int(zone["width"]) - 1, 2):
                if f"{row},{col}" in removed:
                    continue
                mask = _slot_mask(zone, row, col, cells)
                for level in SHUTTLE_LEVELS:
                    if mask & (1 << (level - 1)):
                        positions[level][(row, col)] = str(zone["id"])

    graph: dict[str, dict[str, Any]] = {}
    for level in SHUTTLE_LEVELS:
        level_positions = set(positions[level])
        for (row, col), zone_id in sorted(positions[level].items()):
            node_id = track_node_id(level, row, col)
            neighbors = sorted(
                track_node_id(level, row + d_row, col + d_col)
                for d_row, d_col in ((-2, 0), (2, 0), (0, -2), (0, 2))
                if (row + d_row, col + d_col) in level_positions
            )
            world_x, world_y, world_z = _world_position(topology, level, row, col)
            graph[node_id] = {
                "topology_id": str(topology["id"]),
                "zone_id": zone_id,
                "track_node_id": node_id,
                "shuttle_level": level,
                "row": row,
                "col": col,
                "world_x": world_x,
                "world_y": world_y,
                "world_z": world_z,
                "track_type": _track_type((row, col), level_positions),
                "traversable": True,
                "neighbors": neighbors,
            }
    return graph


def connected_components(graph: dict[str, dict[str, Any]], level: int) -> list[list[str]]:
    allowed = {node_id for node_id, node in graph.items() if int(node["shuttle_level"]) == level}
    remaining = set(allowed)
    components: list[list[str]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        queue = deque([seed])
        component: list[str] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in graph[current]["neighbors"]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))
    return sorted(components, key=lambda item: (-len(item), item[0]))


@dataclass(frozen=True)
class ShuttleConfigStore:
    root: Path = DATA_DIR / "shuttle_configs"

    def path_for(self, topology_id: str) -> Path:
        return self.root / f"{topology_id}.shuttle-v2.json"

    def legacy_path_for(self, topology_id: str) -> Path:
        return self.root / f"{topology_id}.shuttle-v1.json"

    def load(self, topology_id: str) -> dict[str, Any] | None:
        path = self.path_for(topology_id)
        if not path.exists():
            path = self.legacy_path_for(topology_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


def _lift_footprint_is_clear(topology: dict[str, Any], lift: dict[str, Any]) -> tuple[bool, list[str]]:
    evidence: list[str] = []
    zone = next((item for item in topology.get("zones", []) if item.get("id") == lift.get("zone_id")), None)
    if not zone or zone.get("type") != "shuttle":
        return False, ["Lift zone is missing or is not a shuttle zone."]
    row, col = int(lift["row"]), int(lift["col"])
    mask = _slot_mask(zone, row, col, _cell_lookup(topology))
    required_mask = sum(1 << (level - 1) for level in SHUTTLE_LEVELS)
    if mask & required_mask != required_mask:
        return False, ["The configured 2x2 slot is not an aisle on every L1-L4 level."]
    if any(
        node.get("row") in {row, row + 1} and node.get("col") in {col, col + 1}
        for node in topology.get("nodes", [])
    ):
        return False, ["The lift footprint overlaps an existing topology node."]
    cells = _cell_lookup(topology)
    floor = str(zone.get("floor") or DEFAULT_FLOOR)
    footprint = [cells.get((floor, row + d_row, col + d_col)) for d_row in (0, 1) for d_col in (0, 1)]
    if any(not cell or cell.get("type") != "road" or not cell.get("walkable", True) for cell in footprint):
        return False, ["The lift footprint is not backed by four walkable road cells."]
    if any(cell.get("zoneId") != zone["id"] for cell in footprint if cell):
        return False, ["The lift footprint crosses a zone boundary."]
    evidence.extend(
        [
            f"slotAisles[{row},{col}] has L1-L4 aisle bits.",
            "The 2x2 footprint contains four walkable road cells in the same shuttle zone.",
            "No existing topology node occupies the 2x2 footprint.",
        ]
    )
    return True, evidence


def scan_lift_candidates(
    topology: dict[str, Any], graph: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Return only real 2x2 aisle slots that can host an L1-L4 shaft.

    This is an audit result, not an automatic topology mutation. A candidate must
    still be selected in a versioned configuration and reviewed by researcher B.
    """
    graph = graph or build_shuttle_track_graph(topology)
    coordinates = sorted(
        {
            (int(node["row"]), int(node["col"]), str(node["zone_id"]))
            for node in graph.values()
            if int(node["shuttle_level"]) == 1
        }
    )
    candidates: list[dict[str, Any]] = []
    for row, col, zone_id in coordinates:
        node_ids = [track_node_id(level, row, col) for level in SHUTTLE_LEVELS]
        if any(node_id not in graph or len(graph[node_id]["neighbors"]) < 2 for node_id in node_ids):
            continue
        clear, evidence = _lift_footprint_is_clear(
            topology, {"zone_id": zone_id, "row": row, "col": col}
        )
        if not clear:
            continue
        candidates.append(
            {
                "zone_id": zone_id,
                "row": row,
                "col": col,
                "entrances": {f"L{level}": node_ids[level - 1] for level in SHUTTLE_LEVELS},
                "minimum_same_level_degree": min(len(graph[node_id]["neighbors"]) for node_id in node_ids),
                "evidence": evidence,
            }
        )
    return candidates


def validate_shuttle_configuration(
    topology: dict[str, Any],
    config: dict[str, Any] | None,
    graph: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    graph = graph or build_shuttle_track_graph(topology)
    revision = topology_revision(topology)
    warnings: list[str] = []
    candidates = scan_lift_candidates(topology, graph)
    candidate_keys = {(item["row"], item["col"], item["zone_id"]) for item in candidates}
    if config is None:
        return {
            "topology_revision": revision,
            "config_revision": "missing",
            "verification_status": "unverified",
            "planning_enabled": False,
            "lift_candidate_count": len(candidates),
            "lift_selection_basis": [],
            "lifts": [],
            "warnings": ["No versioned shuttle lift configuration exists for this topology."],
        }
    expected_revision = str(config.get("source_topology_revision", ""))
    if expected_revision != revision:
        return {
            "topology_revision": revision,
            "config_revision": str(config.get("config_revision", "unknown")),
            "verification_status": "topology_version_mismatch",
            "planning_enabled": False,
            "lift_candidate_count": len(candidates),
            "lift_selection_basis": [],
            "lifts": [],
            "warnings": [f"Configuration expects {expected_revision}; current topology is {revision}."],
        }
    if config.get("review_status") != "program_verified_pending_human":
        return {
            "topology_revision": revision,
            "config_revision": str(config.get("config_revision", "unknown")),
            "verification_status": "unverified",
            "planning_enabled": False,
            "lift_candidate_count": len(candidates),
            "lift_selection_basis": list(config.get("selection_basis") or []),
            "lifts": [],
            "warnings": ["Draft or rejected lift configurations cannot enter shuttle planning."],
        }

    validated_lifts: list[dict[str, Any]] = []
    occupied: set[tuple[int, int]] = set()
    for raw in config.get("lifts", []):
        lift = dict(raw)
        key = (int(lift["row"]), int(lift["col"]))
        if key in occupied:
            warnings.append(f"{lift['lift_id']} overlaps another lift.")
            continue
        occupied.add(key)
        candidate_key = (key[0], key[1], str(lift.get("zone_id")))
        if candidate_key not in candidate_keys:
            warnings.append(f"{lift['lift_id']} is not present in the deterministic verified candidate scan.")
            continue
        clear, evidence = _lift_footprint_is_clear(topology, lift)
        expected_nodes = [track_node_id(level, *key) for level in SHUTTLE_LEVELS]
        if not clear or any(node_id not in graph for node_id in expected_nodes):
            warnings.extend([f"{lift['lift_id']}: {item}" for item in evidence])
            continue
        if any(len(graph[node_id]["neighbors"]) < 2 for node_id in expected_nodes):
            warnings.append(f"{lift['lift_id']} does not have two same-level track connections on every level.")
            continue
        entrances = {f"L{level}": track_node_id(level, *key) for level in SHUTTLE_LEVELS}
        declared_levels = lift.get("supported_levels")
        if declared_levels is not None and list(declared_levels) != list(SHUTTLE_LEVELS):
            warnings.append(f"{lift['lift_id']} does not declare the complete L1-L4 level contract.")
            continue
        invalid_level_contract = False
        for field_name in ("level_entrances", "level_exits"):
            declared = lift.get(field_name)
            if declared is not None and dict(declared) != entrances:
                warnings.append(f"{lift['lift_id']} has an invalid {field_name} contract.")
                invalid_level_contract = True
                break
        if invalid_level_contract:
            continue
        if lift.get("capacity", 1) != 1:
            warnings.append(f"{lift['lift_id']} capacity must be 1 for the current shuttle contract.")
            continue
        if lift.get("verification_status", "program_verified_pending_human") != "program_verified_pending_human":
            warnings.append(f"{lift['lift_id']} is not program verified for four-level planning.")
            continue
        for node_id in expected_nodes:
            graph[node_id]["track_type"] = "lift_entry"
        lift.update(
            {
                "topology_id": str(topology["id"]),
                "topology_revision": revision,
                "config_revision": str(config["config_revision"]),
                "supported_levels": list(SHUTTLE_LEVELS),
                "level_entrances": entrances,
                "level_exits": dict(entrances),
                "capacity": 1,
                "vehicle_type": "shuttle",
                "state": "idle",
                "verification_status": "program_verified_pending_human",
                "validation_evidence": evidence
                + ["Every L1-L4 entrance has at least two same-level track neighbors."],
            }
        )
        validated_lifts.append(lift)

    planning_enabled = len(validated_lifts) == 2 and {item["lift_id"] for item in validated_lifts} == {
        "shuttle-lift-a",
        "shuttle-lift-b",
    }
    if not planning_enabled:
        warnings.append("Two non-overlapping program-verified lifts are required before cross-level planning.")
    warnings.append("Lift travel timing is synthetic_demo, not a real equipment specification.")
    warnings.append("Program validation is complete; physical placement still requires researcher B's human review.")
    return {
        "topology_revision": revision,
        "config_revision": str(config.get("config_revision", "unknown")),
        "verification_status": "program_verified_pending_human" if planning_enabled else "unverified",
        "planning_enabled": planning_enabled,
        "lift_candidate_count": len(candidates),
        "lift_selection_basis": list(config.get("selection_basis") or []),
        "lifts": validated_lifts,
        "warnings": warnings,
    }


def level_summaries(graph: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for level in SHUTTLE_LEVELS:
        components = connected_components(graph, level)
        result.append(
            {
                "shuttle_level": level,
                "node_count": sum(len(component) for component in components),
                "component_count": len(components),
                "component_sizes": [len(component) for component in components],
            }
        )
    return result


def add_lift_neighbors(
    graph: dict[str, dict[str, Any]], lifts: Iterable[dict[str, Any]]
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {
        node_id: [(neighbor, {"edge_type": "track"}) for neighbor in node["neighbors"]]
        for node_id, node in graph.items()
    }
    for lift in lifts:
        for source_level in SHUTTLE_LEVELS:
            for target_level in SHUTTLE_LEVELS:
                if source_level == target_level:
                    continue
                source = lift["level_entrances"][f"L{source_level}"]
                target = lift["level_exits"][f"L{target_level}"]
                adjacency[source].append((target, {"edge_type": "lift", "lift": lift}))
    for node_id in adjacency:
        adjacency[node_id].sort(key=lambda item: (item[0], item[1].get("lift", {}).get("lift_id", "")))
    return adjacency
