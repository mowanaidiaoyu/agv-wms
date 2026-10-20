from __future__ import annotations

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..core.config import API_PREFIX
from ..schemas import (
    AlgorithmEvaluationRequest,
    AlgorithmEvaluationResponse,
    ScenarioPayload,
    SimulationControlResponse,
    TopologyPayload,
    TopologyValidationResult,
)
from ..services.runtime import repo, simulation_service
from ..services.scenario_sync import sync_scenario_with_topology_payload
from ..services.simulation import validate_runtime_inputs
from ..services.topology_validation import validate_topology_business_rules
from ..services.inventory import InventoryService
from .path_demo import router as path_demo_router
from .sku_algorithm import router as sku_algorithm_router
from .inventory import router as inventory_router
from .shuttle import router as shuttle_router

router = APIRouter(prefix=API_PREFIX)
ws_router = APIRouter()
router.include_router(path_demo_router)
router.include_router(sku_algorithm_router)
router.include_router(inventory_router)
router.include_router(shuttle_router)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/topologies")
def list_topologies():
    return repo.list_topologies()


@router.post("/topologies")
def create_topology(payload: TopologyPayload):
    return repo.upsert_topology(payload.id, payload.name, payload.model_dump(by_alias=True))


@router.post("/topologies/validate", response_model=TopologyValidationResult)
def validate_topology(payload: TopologyPayload):
    inventory = InventoryService(repo)
    return validate_topology_business_rules(
        payload.model_dump(by_alias=True),
        access_mappings=inventory.list_access_mappings(payload.id) if repo.get_topology(payload.id) else [],
    )


@router.get("/topologies/{topology_id}")
def get_topology(topology_id: str):
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    return topology


@router.put("/topologies/{topology_id}")
def update_topology(topology_id: str, payload: TopologyPayload):
    if topology_id != payload.id:
        raise HTTPException(status_code=400, detail="Topology id mismatch")
    return repo.upsert_topology(payload.id, payload.name, payload.model_dump(by_alias=True))


@router.delete("/topologies/{topology_id}")
def delete_topology(topology_id: str):
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    referencing = [item["name"] for item in repo.list_scenarios() if item["payload"].get("topologyId") == topology_id]
    if referencing:
        raise HTTPException(
            status_code=409,
            detail=f"该拓扑被 {len(referencing)} 个场景引用（{'、'.join(referencing)}），请先删除或改绑这些场景后再删除。",
        )
    return repo.delete_topology(topology_id)


@router.get("/scenarios")
def list_scenarios():
    scenarios = repo.list_scenarios()
    topologies = {item["id"]: item["payload"] for item in repo.list_topologies()}
    for scenario in scenarios:
        topology = topologies.get(scenario["payload"].get("topologyId"))
        if topology:
            scenario["payload"] = sync_scenario_with_topology_payload(topology, scenario["payload"])
    return scenarios


@router.post("/scenarios")
def create_scenario(payload: ScenarioPayload):
    topology = repo.get_topology(payload.topologyId)
    if not topology:
        raise HTTPException(status_code=400, detail="Referenced topology not found")
    synced_payload = sync_scenario_with_topology_payload(topology["payload"], payload.model_dump())
    return repo.upsert_scenario(payload.id, payload.name, synced_payload)


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str):
    scenario = repo.get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    topology = repo.get_topology(scenario["payload"].get("topologyId"))
    if topology:
        scenario["payload"] = sync_scenario_with_topology_payload(topology["payload"], scenario["payload"])
    return scenario


@router.post("/simulations/start", response_model=SimulationControlResponse)
async def start_simulation(payload: dict[str, str]):
    topology = repo.get_topology(payload["topologyId"])
    scenario = repo.get_scenario(payload["scenarioId"])
    if not topology or not scenario:
        raise HTTPException(status_code=400, detail="Topology or scenario missing")
    synced_scenario_payload = sync_scenario_with_topology_payload(topology["payload"], scenario["payload"])
    repo.upsert_scenario(scenario["id"], scenario["name"], synced_scenario_payload)
    runtime_errors = validate_runtime_inputs(topology["payload"], synced_scenario_payload)
    if runtime_errors:
        raise HTTPException(status_code=400, detail=runtime_errors)
    result = await simulation_service.start(topology["payload"], synced_scenario_payload)
    return result


@router.post("/simulations/{run_id}/pause", response_model=SimulationControlResponse)
def pause_simulation(run_id: str):
    try:
        return simulation_service.pause(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")


@router.post("/simulations/{run_id}/resume", response_model=SimulationControlResponse)
def resume_simulation(run_id: str):
    try:
        return simulation_service.resume(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")


@router.post("/simulations/{run_id}/stop", response_model=SimulationControlResponse)
def stop_simulation(run_id: str):
    try:
        return simulation_service.stop(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")


@router.get("/simulations/{run_id}/state")
def get_simulation_state(run_id: str):
    try:
        return simulation_service.get_state(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")


@router.get("/simulations/{run_id}/metrics")
def get_simulation_metrics(run_id: str):
    try:
        return simulation_service.get_metrics(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")


@router.get("/simulations/{run_id}/events")
def get_simulation_events(run_id: str):
    return simulation_service.get_events(run_id)


@router.get("/simulations")
def list_runs():
    return repo.list_runs()


@router.post("/algorithms/evaluate", response_model=AlgorithmEvaluationResponse)
def evaluate_algorithms(payload: AlgorithmEvaluationRequest):
    topology = repo.get_topology(payload.topologyId)
    scenario = repo.get_scenario(payload.scenarioId)
    if not topology or not scenario:
        raise HTTPException(status_code=400, detail="Topology or scenario missing")
    synced_scenario_payload = sync_scenario_with_topology_payload(topology["payload"], scenario["payload"])
    runtime_errors = validate_runtime_inputs(topology["payload"], synced_scenario_payload)
    if runtime_errors:
        raise HTTPException(status_code=400, detail=runtime_errors)
    results = simulation_service.evaluate_algorithms(topology["payload"], synced_scenario_payload)
    return {"topologyId": payload.topologyId, "scenarioId": payload.scenarioId, "results": results}


@ws_router.websocket("/ws/simulation/{run_id}")
async def simulation_ws(websocket: WebSocket, run_id: str):
    await simulation_service.hub.connect(run_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        simulation_service.hub.disconnect(run_id, websocket)
