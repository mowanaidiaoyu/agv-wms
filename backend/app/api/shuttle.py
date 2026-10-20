from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..services.runtime import repo
from ..services.shuttle_pathfinding import plan_shuttle_path
from ..services.shuttle_topology import (
    ShuttleConfigStore,
    build_shuttle_track_graph,
    level_summaries,
    validate_shuttle_configuration,
)
from ..shuttle_lift_schemas import (
    ShuttleLiftsResponse,
    ShuttlePlanRequest,
    ShuttlePlanResponse,
    ShuttleTopologyLevelsResponse,
    ShuttleTracksResponse,
)


router = APIRouter(prefix="/shuttle", tags=["shuttle-offline-demo"])


def _context(topology_id: str):
    record = repo.get_topology(topology_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Topology not found")
    graph = build_shuttle_track_graph(record["payload"])
    validation = validate_shuttle_configuration(
        record["payload"], ShuttleConfigStore().load(topology_id), graph
    )
    return record, graph, validation


@router.get("/topologies/{topology_id}/levels", response_model=ShuttleTopologyLevelsResponse)
def get_shuttle_levels(topology_id: str):
    record, graph, validation = _context(topology_id)
    return {
        "topology_id": topology_id,
        "topology_name": record["name"],
        "topology_revision": validation["topology_revision"],
        "topology_version": str(record["payload"].get("meta", {}).get("version", "unknown")),
        "config_revision": validation["config_revision"],
        "verification_status": validation["verification_status"],
        "planning_enabled": validation["planning_enabled"],
        "lift_candidate_count": validation["lift_candidate_count"],
        "lift_selection_basis": validation["lift_selection_basis"],
        "levels": level_summaries(graph),
        "warnings": validation["warnings"],
    }


@router.get("/topologies/{topology_id}/tracks", response_model=ShuttleTracksResponse)
def get_shuttle_tracks(topology_id: str, level: int | None = Query(default=None, ge=1, le=3)):
    _, graph, validation = _context(topology_id)
    nodes = [
        node for node in graph.values() if level is None or int(node["shuttle_level"]) == level
    ]
    return {
        "topology_id": topology_id,
        "topology_revision": validation["topology_revision"],
        "config_revision": validation["config_revision"],
        "nodes": sorted(nodes, key=lambda item: item["track_node_id"]),
    }


@router.get("/topologies/{topology_id}/lifts", response_model=ShuttleLiftsResponse)
def get_shuttle_lifts(topology_id: str):
    _, _, validation = _context(topology_id)
    return {
        "topology_id": topology_id,
        "topology_revision": validation["topology_revision"],
        "config_revision": validation["config_revision"],
        "planning_enabled": validation["planning_enabled"],
        "lifts": validation["lifts"],
        "warnings": validation["warnings"],
    }


@router.post("/plan", response_model=ShuttlePlanResponse)
def create_shuttle_plan(request: ShuttlePlanRequest):
    _, graph, validation = _context(request.topology_id)
    if not validation["planning_enabled"]:
        reason = "; ".join(validation["warnings"]) or "Shuttle topology is not verified for planning."
        return {
            "topology_id": request.topology_id,
            "topology_revision": validation["topology_revision"],
            "config_revision": validation["config_revision"],
            "algorithm": request.algorithm,
            "result_status": "failed",
            "failure_reason": reason,
        }
    result = plan_shuttle_path(
        graph,
        validation["lifts"],
        request.start_track_node,
        request.end_track_node,
        request.algorithm,
    )
    return {
        "topology_id": request.topology_id,
        "topology_revision": validation["topology_revision"],
        "config_revision": validation["config_revision"],
        **result,
    }
