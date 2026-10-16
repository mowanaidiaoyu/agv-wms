from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from ..core.repository import (
    EvaluationSnapshotConflictError,
    Repository,
    StoredEvaluationCorruptError,
)
from ..path_demo_schemas import (
    MultiPathDemoScenariosResponse,
    MultiPathEvaluationRequest,
    MultiPathEvaluationExport,
    MultiPathEvaluationListResponse,
    MultiPathEvaluationResponse,
    MultiPathEvaluationStatus,
    MultiPathPlanRequest,
    MultiPathPlanResponse,
    PathDemoPlanRequest,
    PathDemoPlanResponse,
    PathDemoTopologyOptions,
)
from ..services.multi_path_evaluation import evaluate_multi_path_algorithms
from ..services.multi_path_demo import analyze_multi_road_topology, build_multi_demo_scenarios, plan_multi_paths
from ..services.path_demo import PathDemoError, analyze_path_demo_topology, plan_path_demo
from ..services.topology_fingerprint import topology_content_sha256


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
        "topology_content_sha256": topology_content_sha256(record["payload"]),
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
        evaluation = MultiPathEvaluationResponse.model_validate(evaluate_multi_path_algorithms(
            topology=record["payload"],
            topology_id=request.topology_id,
            topology_name=record["name"],
            topology_version=str(record["payload"].get("meta", {}).get("version", "unknown")),
            mode=request.mode,
            scenario_id=request.scenario_id,
            vehicles=vehicles,
            planning_config=request.planning_config.model_dump(),
        )).model_dump(mode="json")
    except PathDemoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    try:
        repo.save_multi_path_evaluation(evaluation)
    except EvaluationSnapshotConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StoredEvaluationCorruptError as exc:
        raise HTTPException(status_code=500, detail="Stored multi-path evaluation is corrupted") from exc
    except (sqlite3.DatabaseError, OSError) as exc:
        raise HTTPException(status_code=500, detail="Multi-path evaluation could not be persisted") from exc
    return evaluation


@router.get("/evaluations", response_model=MultiPathEvaluationListResponse)
def list_multi_path_evaluations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    topology_id: str | None = None,
    status: MultiPathEvaluationStatus | None = None,
):
    try:
        result = repo.list_multi_path_evaluations(
            limit=limit,
            offset=offset,
            topology_id=topology_id,
            status=status,
        )
        return MultiPathEvaluationListResponse.model_validate(result)
    except (StoredEvaluationCorruptError, ValidationError) as exc:
        raise HTTPException(status_code=500, detail="Stored multi-path evaluation summary is corrupted") from exc
    except (sqlite3.DatabaseError, OSError) as exc:
        raise HTTPException(status_code=500, detail="Multi-path evaluation history could not be read") from exc


def _stored_evaluation_or_404(experiment_id: str) -> MultiPathEvaluationResponse:
    try:
        evaluation = repo.get_multi_path_evaluation(experiment_id)
        if not evaluation:
            raise HTTPException(status_code=404, detail="Multi-path evaluation not found")
        return MultiPathEvaluationResponse.model_validate(evaluation)
    except HTTPException:
        raise
    except (StoredEvaluationCorruptError, ValidationError) as exc:
        raise HTTPException(status_code=500, detail="Stored multi-path evaluation is corrupted") from exc
    except (sqlite3.DatabaseError, OSError) as exc:
        raise HTTPException(status_code=500, detail="Multi-path evaluation could not be read") from exc


@router.get("/evaluations/{experiment_id}/export", response_model=MultiPathEvaluationExport)
def export_multi_path_evaluation(experiment_id: str):
    evaluation = _stored_evaluation_or_404(experiment_id)
    return {
        "schema_version": evaluation.schema_version,
        "experiment_type": evaluation.experiment_type,
        "disclaimer": "Offline multi-vehicle path-planning experiment; not a real equipment run or complete warehouse task simulation.",
        "unit_definitions": {
            "planning_time_ms": "Algorithm wall-clock planning time in milliseconds; a single run is not statistical proof of superiority.",
            "total_path_distance": "Traversed grid-cell edges (cells).",
            "makespan_seconds": "Planned elapsed time until all vehicles finish (seconds).",
            "wait_time_seconds": "Planned reservation wait time (seconds).",
            "estimated_duration_seconds": "Planned per-vehicle completion time (seconds).",
            "time_step_seconds": "Duration of one discrete planning time step (seconds).",
            "speed_cells_per_second": "Offline demonstration speed (grid cells per second).",
            "detected_conflicts": "Conflicts detected while constructing the plan (count).",
            "resolved_conflicts": "Detected conflicts resolved by waiting or alternate search (count).",
            "remaining_conflicts": "Conflicts remaining in the returned execution plan (count).",
            "avoidance_count": "Reservation-based avoidance actions (count).",
            "visited_states": "Space-time search states expanded for a vehicle (count).",
            "planning_status": "Algorithm outcome; failed results retain an error reason and no fake metrics.",
        },
        "evaluation": evaluation,
    }


@router.get("/evaluations/{experiment_id}", response_model=MultiPathEvaluationResponse)
def get_multi_path_evaluation(experiment_id: str):
    return _stored_evaluation_or_404(experiment_id)
