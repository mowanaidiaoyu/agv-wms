from __future__ import annotations

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..core.config import API_PREFIX
from ..schemas import SimulationControlResponse, SimulationStartRequest, TopologyPayload
from ..services.goods import build_inventory, load_catalog
from ..services.runtime import repo, simulation_service

router = APIRouter(prefix=API_PREFIX)
ws_router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/topologies")
def list_topologies():
    return repo.list_topologies()


@router.post("/topologies")
def create_topology(payload: TopologyPayload):
    return repo.upsert_topology(payload.id, payload.name, payload.model_dump(by_alias=True))


@router.get("/topologies/{topology_id}")
def get_topology(topology_id: str):
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    return topology


@router.delete("/topologies/{topology_id}")
def delete_topology(topology_id: str):
    topology = repo.delete_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    return topology


@router.get("/goods/inventory/{topology_id}")
def goods_inventory(topology_id: str):
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    return {"topologyId": topology_id, "slots": build_inventory(topology["payload"])}


@router.get("/goods/catalog")
def goods_catalog():
    return load_catalog()


@router.post("/simulations/start", response_model=SimulationControlResponse)
async def start_simulation(payload: SimulationStartRequest):
    topology_id = payload.topologyId
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=400, detail="Topology missing")
    try:
        return await simulation_service.start(
            topology["payload"],
            [order.model_dump() for order in payload.orders],
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


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


@ws_router.websocket("/ws/simulation/{run_id}")
async def simulation_ws(websocket: WebSocket, run_id: str):
    await simulation_service.hub.connect(run_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        simulation_service.hub.disconnect(run_id, websocket)
