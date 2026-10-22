from __future__ import annotations

import math
import hashlib
import json
from typing import Any

from .shuttle_topology import (
    DEFAULT_FLOOR,
    SHUTTLE_LEVELS,
    ShuttleConfigStore,
    build_shuttle_track_graph,
    shuttle_slot_world_position,
    shuttle_slot_mask,
    topology_revision,
    validate_shuttle_configuration,
)


CONTRACT_VERSION = "shuttle-storage-contract/v1"
DATA_SOURCE = "topology_shuttle_slot_candidate"
RACK_HALF_EXTENT = 0.96
SHUTTLE_HALF_WIDTH = 0.46
SHUTTLE_HALF_DEPTH = 0.41
_CANDIDATE_CACHE: dict[str, list[dict[str, Any]]] = {}


def _candidate_id(zone_id: str, level: int, row: int, col: int) -> str:
    return f"shuttle-location:{zone_id}:L{level}:r{row}:c{col}"


def _adjacent_track_nodes(
    graph: dict[str, dict[str, Any]], level: int, row: int, col: int
) -> list[str]:
    candidates = [
        f"L{level}-r{row + delta_row}-c{col + delta_col}"
        for delta_row, delta_col in ((-2, 0), (2, 0), (0, -2), (0, 2))
    ]
    return sorted(node_id for node_id in candidates if node_id in graph)


def _pick_face(rack_row: int, rack_col: int, track_row: int, track_col: int) -> str:
    if track_row < rack_row:
        return "north"
    if track_row > rack_row:
        return "south"
    if track_col < rack_col:
        return "west"
    return "east"


def _access_options(
    topology: dict[str, Any],
    graph: dict[str, dict[str, Any]],
    lift_reachable_nodes: set[str],
    level: int,
    row: int,
    col: int,
) -> list[dict[str, Any]]:
    rack_x, _, rack_z = shuttle_slot_world_position(topology, level, row, col)
    options: list[dict[str, Any]] = []
    for node_id in _adjacent_track_nodes(graph, level, row, col):
        node = graph[node_id]
        face = _pick_face(row, col, int(node["row"]), int(node["col"]))
        along_x = int(node["row"]) == row
        vehicle_half_extent = SHUTTLE_HALF_WIDTH if along_x else SHUTTLE_HALF_DEPTH
        clearance = 2.0 - RACK_HALF_EXTENT - vehicle_half_extent
        delta_x = rack_x - float(node["world_x"])
        delta_z = rack_z - float(node["world_z"])
        connected = node_id in lift_reachable_nodes
        codes = ["real_shuttle_track", "same_level", "orthogonally_adjacent"]
        if clearance >= 0.35:
            codes.append("vehicle_bbox_clear")
        if connected:
            codes.append("connected_to_verified_lift_network")
        options.append(
            {
                "track_node_id": node_id,
                "shuttle_level": level,
                "row": int(node["row"]),
                "col": int(node["col"]),
                "world_x": float(node["world_x"]),
                "world_y": float(node["world_y"]),
                "world_z": float(node["world_z"]),
                "required_pick_face": face,
                "required_heading_radians": math.atan2(delta_x, delta_z),
                "connected_to_lift_network": connected,
                "vehicle_clearance_cells": round(clearance, 3),
                "geometry_clear": clearance >= 0.35,
                "validation_codes": codes,
            }
        )
    return options


def _reachable_from_lifts(
    graph: dict[str, dict[str, Any]], lift_nodes: set[str]
) -> set[str]:
    pending = [node_id for node_id in lift_nodes if node_id in graph]
    visited = set(pending)
    while pending:
        current = pending.pop()
        for neighbor in graph[current]["neighbors"]:
            if neighbor not in visited:
                visited.add(neighbor)
                pending.append(neighbor)
    return visited


def build_shuttle_storage_candidates(topology: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive non-executable rack-level candidates from the same slot geometry as the renderer.

    A candidate is deliberately not a storage location. The topology does not declare a pick
    face, an approved access track node, or a Shuttle inventory identity, so every record stays
    in draft/unverified state until researcher B supplies and reviews those semantics.
    """
    revision = topology_revision(topology)
    shuttle_config = ShuttleConfigStore().load(str(topology["id"]))
    config_digest = hashlib.sha256(
        json.dumps(shuttle_config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    cache_key = f"{revision}:{config_digest}"
    cached = _CANDIDATE_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    # The full 666 catalog contains thousands of nested geometry records. Keep one
    # immutable revision at a time so version-change validation cannot double the
    # process memory while constructing the replacement catalog.
    _CANDIDATE_CACHE.clear()
    graph = build_shuttle_track_graph(topology)
    validation = validate_shuttle_configuration(
        topology, shuttle_config, graph
    )
    lift_nodes = {
        node_id
        for lift in validation.get("lifts", [])
        for node_id in lift.get("level_entrances", {}).values()
    }
    lift_reachable_nodes = _reachable_from_lifts(graph, lift_nodes)
    topology_id = str(topology["id"])
    result: list[dict[str, Any]] = []
    for zone in sorted(
        (item for item in topology.get("zones", []) if item.get("type") == "shuttle"),
        key=lambda item: str(item.get("id", "")),
    ):
        zone_id = str(zone["id"])
        floor = str(zone.get("floor") or DEFAULT_FLOOR)
        removed = set(zone.get("removedSlots") or [])
        for row in range(int(zone["y"]), int(zone["y"]) + int(zone["height"]) - 1, 2):
            for col in range(int(zone["x"]), int(zone["x"]) + int(zone["width"]) - 1, 2):
                if f"{row},{col}" in removed:
                    continue
                mask = shuttle_slot_mask(topology, zone, row, col)
                for level in SHUTTLE_LEVELS:
                    if mask & (1 << (level - 1)):
                        continue
                    potential_access = _adjacent_track_nodes(graph, level, row, col)
                    access_options = _access_options(
                        topology, graph, lift_reachable_nodes, level, row, col
                    )
                    world_x, world_y, world_z = shuttle_slot_world_position(
                        topology, level, row, col
                    )
                    blocker_codes = ["missing_pick_face", "unverified_access_mapping"]
                    if not potential_access:
                        blocker_codes.append("no_adjacent_track_candidate")
                    result.append(
                        {
                            "location_id": _candidate_id(zone_id, level, row, col),
                            "topology_id": topology_id,
                            "topology_revision": revision,
                            "zone_id": zone_id,
                            "floor": floor,
                            "storage_level": level,
                            "row": row,
                            "col": col,
                            "world_x": world_x,
                            "world_y": world_y,
                            "world_z": world_z,
                            "pick_face": "unknown",
                            "access_track_node_id": None,
                            "potential_access_track_node_ids": potential_access,
                            "access_mapping_status": "unverified",
                            "review_status": "draft",
                            "data_source": DATA_SOURCE,
                            "execution_allowed": False,
                            "blocker_codes": blocker_codes,
                            "access_options": access_options,
                            "persisted_status": None,
                        }
                    )
    result = sorted(result, key=lambda item: item["location_id"])
    _CANDIDATE_CACHE[cache_key] = result
    return result


def audit_shuttle_storage(topology: dict[str, Any], preview_limit: int = 12) -> dict[str, Any]:
    zones = {str(zone["id"]): zone for zone in topology.get("zones", [])}
    storage_nodes = [node for node in topology.get("nodes", []) if node.get("type") == "storage"]
    ctu_storage_nodes = [
        node for node in storage_nodes if zones.get(str(node.get("zoneId")), {}).get("type") == "ctu"
    ]
    shuttle_storage_nodes = [
        node for node in storage_nodes if zones.get(str(node.get("zoneId")), {}).get("type") == "shuttle"
    ]
    shuttle_zones = [zone for zone in zones.values() if zone.get("type") == "shuttle"]
    candidates = build_shuttle_storage_candidates(topology)
    formal_dropoffs = [
        resource
        for resource in topology.get("resources", [])
        if resource.get("type") in {"shuttle_dropoff", "shuttle_station", "outbound_station"}
    ]
    return {
        "topology_id": str(topology["id"]),
        "topology_version": str(topology.get("meta", {}).get("version", "unknown")),
        "topology_revision": topology_revision(topology),
        "contract_version": CONTRACT_VERSION,
        "audit_status": "blocked_unverified_mappings",
        "data_source": DATA_SOURCE,
        "execution_allowed": False,
        "ctu_storage_node_count": len(ctu_storage_nodes),
        "shuttle_storage_node_count": len(shuttle_storage_nodes),
        "shuttle_zone_count": len(shuttle_zones),
        "rack_level_candidate_count": len(candidates),
        "candidates_with_adjacent_track_count": sum(
            bool(item["potential_access_track_node_ids"]) for item in candidates
        ),
        "declared_pick_face_count": 0,
        "verified_access_mapping_count": 0,
        "formal_dropoff_count": len(formal_dropoffs),
        "blockers": [
            "666 does not contain formal Shuttle storage nodes with persistent inventory identity.",
            "Visual rack-level candidates do not declare a reviewed pick face.",
            "No storage candidate has a verified ShuttleGraph access-track mapping.",
            "No formal Shuttle outbound handoff resource is declared in the topology.",
        ],
        "required_human_review": [
            "Researcher B must confirm which rack-level candidates are real storage locations.",
            "Researcher B must declare the pick face and approve one real same-level ShuttleGraph node.",
            "Researcher B must confirm a formal handoff station or approve a clearly synthetic demo endpoint.",
        ],
        "candidate_preview": candidates[:preview_limit],
    }


def page_shuttle_storage_candidates(
    topology: dict[str, Any],
    *,
    level: int | None = None,
    zone_id: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    candidates = build_shuttle_storage_candidates(topology)
    if level is not None:
        candidates = [item for item in candidates if item["storage_level"] == level]
    if zone_id is not None:
        candidates = [item for item in candidates if item["zone_id"] == zone_id]
    return {
        "topology_id": str(topology["id"]),
        "topology_revision": topology_revision(topology),
        "contract_version": CONTRACT_VERSION,
        "total": len(candidates),
        "offset": offset,
        "limit": limit,
        "candidates": candidates[offset : offset + limit],
    }
