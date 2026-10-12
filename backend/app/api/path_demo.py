from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..core.repository import Repository
from ..path_demo_schemas import PathDemoPlanRequest, PathDemoPlanResponse, PathDemoTopologyOptions
from ..services.path_demo import PathDemoError, analyze_path_demo_topology, plan_path_demo


router = APIRouter(prefix="/path-demo", tags=["path-demo"])
repo = Repository()


def _topology_or_404(topology_id: str) -> dict:
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    return topology


@router.get("/topologies/{topology_id}/options", response_model=PathDemoTopologyOptions)
def get_path_demo_options(topology_id: str):
    record = _topology_or_404(topology_id)
    try:
        analysis = analyze_path_demo_topology(record["payload"])
    except PathDemoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "topology_id": topology_id,
        "topology_name": record["name"],
        "topology_version": str(record["payload"].get("meta", {}).get("version", "unknown")),
        **analysis,
    }
@router.post("/plan", response_model=PathDemoPlanResponse)
def create_path_demo_plan(request: PathDemoPlanRequest):
    record = _topology_or_404(request.topology_id)
    try:
        result = plan_path_demo(
            record["payload"],
            request.start_cell_id,
            request.goal_cell_id,
            request.algorithm,
        )
    except PathDemoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "topology_id": request.topology_id,
        "topology_name": record["name"],
        "topology_version": str(record["payload"].get("meta", {}).get("version", "unknown")),
        **result,
    }
