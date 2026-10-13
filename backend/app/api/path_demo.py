from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..core.repository import Repository
from ..path_demo_schemas import (
    MultiPathDemoScenariosResponse,
    MultiPathEvaluationRequest,
    MultiPathEvaluationResponse,
    MultiPathPlanRequest,
    MultiPathPlanResponse,
    PathDemoPlanRequest,
    PathDemoPlanResponse,
    PathDemoTopologyOptions,
)
from ..services.multi_path_evaluation import evaluate_multi_path_algorithms
from ..services.multi_path_demo import analyze_multi_road_topology, build_multi_demo_scenarios, plan_multi_paths
from ..services.path_demo import PathDemoError, analyze_path_demo_topology, plan_path_demo


router = APIRouter(prefix="/path-demo", tags=["path-demo"])
repo = Repository()


def _topology_or_404(topology_id: str) -> dict:
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    return topology


def _resolve_multi_vehicles(record: dict, request: MultiPathPlanRequest | MultiPathEvaluationRequest) -> list[dict]:
    vehicles = [vehicle.model_dump() for vehicle in request.vehicles]
    if request.mode == "custom" and request.scenario_id is not None:
        raise PathDemoError("Custom mode cannot include scenario_id")
    if request.mode == "preset" or request.scenario_id is not None:
        if request.scenario_id is None:
            raise PathDemoError("Preset mode requires scenario_id")
        scenarios = build_multi_demo_scenarios(record["payload"])
        selected = next((item for item in scenarios if item["id"] == request.scenario_id), None)
        if selected is None:
            raise PathDemoError(f"Unknown preset scenario: {request.scenario_id}")
        vehicles = selected["vehicles"]
    return vehicles


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


@router.get("/topologies/{topology_id}/multi-scenarios", response_model=MultiPathDemoScenariosResponse)
def get_multi_path_demo_scenarios(topology_id: str):
    record = _topology_or_404(topology_id)
    try:
        scenarios = build_multi_demo_scenarios(record["payload"])
        road_analysis = analyze_multi_road_topology(record["payload"])
    except PathDemoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "topology_id": topology_id,
        "topology_name": record["name"],
        "topology_version": str(record["payload"].get("meta", {}).get("version", "unknown")),
        **road_analysis,
        "scenarios": scenarios,
    }


@router.post("/plan-multi", response_model=MultiPathPlanResponse)
def create_multi_path_demo_plan(request: MultiPathPlanRequest):
    record = _topology_or_404(request.topology_id)
    try:
        vehicles = _resolve_multi_vehicles(record, request)
        result = plan_multi_paths(
            record["payload"],
            vehicles,
            request.algorithm,
            request.planning_config.model_dump(),
        )
    except PathDemoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "topology_id": request.topology_id,
        "topology_name": record["name"],
        "topology_version": str(record["payload"].get("meta", {}).get("version", "unknown")),
        **result,
    }


@router.post("/evaluate-multi", response_model=MultiPathEvaluationResponse)
def evaluate_multi_path_demo(request: MultiPathEvaluationRequest):
    record = _topology_or_404(request.topology_id)
    try:
        vehicles = _resolve_multi_vehicles(record, request)
        return evaluate_multi_path_algorithms(
            topology=record["payload"],
            topology_id=request.topology_id,
            topology_name=record["name"],
            topology_version=str(record["payload"].get("meta", {}).get("version", "unknown")),
            mode=request.mode,
            scenario_id=request.scenario_id,
            vehicles=vehicles,
            planning_config=request.planning_config.model_dump(),
        )
    except PathDemoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
