from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect

from ..core.config import API_PREFIX
from ..schemas import (
    ShuttleDockSettingsRequest,
    ShuttlePlanRequest,
    ShuttleTransferFailRequest,
    SimulationControlResponse,
    SimulationSpeedRequest,
    SimulationSpeedResponse,
    SimulationStartRequest,
    TransportLayoutPlanResponse,
    TransportNetworkAuditResponse,
    TopologyPayload,
)
from ..services.goods import build_inventory, load_catalog
from ..services.runtime import repo, simulation_service
from ..services.shuttle_topology import topology_revision
from ..services.transport_layout import LayoutRevisionMismatch, load_transport_layout_plan
from ..services.transport_network import audit_transport_network
from ..services.unified_transfer import plan_transfer, shuttle_contract

router = APIRouter(prefix=API_PREFIX)
ws_router = APIRouter()

# 合同响应缓存：内容只随 (拓扑 revision, 配置 revision) 变化。缓存序列化后的
# JSON 字节，跳过每次请求对 1402 个轨道节点的 jsonable_encoder 逐字段编码
#（实测占热路径 ~230ms）。有界防止拓扑反复编辑时无限增长。
_contract_response_cache: dict[tuple[str, str, str], bytes] = {}
_CONTRACT_RESPONSE_CACHE_LIMIT = 16

# 库存响应缓存：同上，4 万+条记录的逐字段编码实测 ~10 分钟。
_goods_inventory_cache: dict[tuple[str, str], bytes] = {}
_GOODS_INVENTORY_CACHE_LIMIT = 8


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


@router.get(
    "/topologies/{topology_id}/transport-network/audit",
    response_model=TransportNetworkAuditResponse,
)
def get_transport_network_audit(topology_id: str):
    topology = repo.get_topology_read_only(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    return audit_transport_network(topology["payload"])


@router.get(
    "/topologies/{topology_id}/transport-layout-plan",
    response_model=TransportLayoutPlanResponse,
)
def get_transport_layout_plan(topology_id: str):
    topology = repo.get_topology_read_only(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    try:
        return load_transport_layout_plan(topology["payload"])
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Transport layout plan not found") from error
    except LayoutRevisionMismatch as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
    # 大型拓扑的库存是 4 万+ 条记录（~12MB）：jsonable_encoder 逐字段编码实测需要
    # ~10 分钟并阻塞事件循环；这里直接 json.dumps 字节流返回（~0.4s），并按拓扑
    # revision 缓存（库存只随拓扑 / 商品目录变化）。
    payload = topology["payload"]
    key = (topology_id, topology_revision(payload))
    body = _goods_inventory_cache.get(key)
    if body is None:
        body = json.dumps(
            {"topologyId": topology_id, "slots": build_inventory(payload)},
            ensure_ascii=False,
        ).encode("utf-8")
        if len(_goods_inventory_cache) >= 8:
            _goods_inventory_cache.clear()
        _goods_inventory_cache[key] = body
    return Response(content=body, media_type="application/json")


@router.get("/goods/catalog")
def goods_catalog():
    return load_catalog()


@router.get("/shuttle/topologies/{topology_id}/contract")
def get_shuttle_contract(topology_id: str):
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    try:
        result = shuttle_contract(topology["payload"])
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    key = (topology_id, str(result["topologyRevision"]), str(result["configRevision"]))
    body = _contract_response_cache.get(key)
    if body is None:
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        if len(_contract_response_cache) >= _CONTRACT_RESPONSE_CACHE_LIMIT:
            _contract_response_cache.clear()
        _contract_response_cache[key] = body
    return Response(content=body, media_type="application/json")


@router.post("/shuttle/plan")
def create_shuttle_plan(payload: ShuttlePlanRequest):
    topology = repo.get_topology(payload.topologyId)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    try:
        result = plan_transfer(
            topology["payload"],
            payload.startTrackNode,
            payload.endTrackNode,
            payload.algorithm,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if result["result_status"] != "success":
        raise HTTPException(status_code=409, detail=result.get("failure_reason") or "No route")
    return result


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
            [transfer.model_dump() for transfer in payload.transfers],
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/simulations/{run_id}/pause", response_model=SimulationControlResponse)
def pause_simulation(run_id: str):
    try:
        return simulation_service.pause(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/simulations/{run_id}/resume", response_model=SimulationControlResponse)
def resume_simulation(run_id: str):
    try:
        return simulation_service.resume(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/simulations/{run_id}/stop", response_model=SimulationControlResponse)
def stop_simulation(run_id: str):
    try:
        return simulation_service.stop(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")


@router.post("/simulations/{run_id}/tasks/{task_id}/complete-picking")
async def complete_picking_task(run_id: str, task_id: str):
    try:
        await simulation_service.complete_picking(run_id, task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run or task not found")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return simulation_service.get_state(run_id)


@router.post("/simulations/{run_id}/reset", response_model=SimulationControlResponse)
async def reset_simulation(run_id: str):
    try:
        return await simulation_service.reset(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")


@router.post("/simulations/{run_id}/speed", response_model=SimulationSpeedResponse)
def set_simulation_speed(run_id: str, payload: SimulationSpeedRequest):
    try:
        return simulation_service.set_speed(run_id, payload.multiplier)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/simulations/{run_id}/transfers/{task_id}/cancel")
def cancel_shuttle_transfer(run_id: str, task_id: str):
    try:
        return simulation_service.cancel_transfer(run_id, task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Transfer task not found")


@router.post("/simulations/{run_id}/transfers/{task_id}/fail")
def fail_shuttle_transfer(run_id: str, task_id: str, payload: ShuttleTransferFailRequest):
    try:
        return simulation_service.fail_transfer(run_id, task_id, payload.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail="Transfer task not found")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/simulations/{run_id}/transfers/{task_id}/retry")
async def retry_shuttle_transfer(run_id: str, task_id: str):
    try:
        return await simulation_service.retry_transfer(run_id, task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Transfer task not found")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/simulations/{run_id}/state")
def get_simulation_state(run_id: str):
    try:
        state = simulation_service.get_state(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    # 状态可能带全量库存（自动派单模式 4 万+条）：前端在 WebSocket 断开时会以
    # 1.5s 间隔轮询本接口，jsonable_encoder 逐字段编码会拖死事件循环，
    # 直接 json.dumps 字节流返回。
    return Response(
        json.dumps(state, ensure_ascii=False, default=str).encode("utf-8"),
        media_type="application/json",
    )


@router.put("/simulations/{run_id}/shuttle-docks/{dock_id}")
def update_shuttle_dock(run_id: str, dock_id: str, payload: ShuttleDockSettingsRequest):
    try:
        return simulation_service.update_shuttle_dock(
            run_id, dock_id, payload.model_dump(exclude_unset=True)
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Dock not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
        state = simulation_service.get_state(run_id)
        await websocket.send_json({"type": "snapshot", "tick": state.get("tick", 0), "payload": state})
        while True:
            await websocket.receive_text()
    except KeyError:
        simulation_service.hub.disconnect(run_id, websocket)
        await websocket.close(code=1008, reason="Run not found")
    except WebSocketDisconnect:
        simulation_service.hub.disconnect(run_id, websocket)
