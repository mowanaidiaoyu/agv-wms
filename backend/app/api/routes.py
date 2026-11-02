from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect

from ..core.config import API_PREFIX
from ..schemas import (
    GeneralSimulationControlResponse,
    GeneralSimulationPlanRequest,
    ShuttleDockSettingsRequest,
    ShuttlePlanRequest,
    ShuttleTransferFailRequest,
    SimulationControlResponse,
    SimulationOrderCancelRequest,
    SimulationOrderInput,
    OrderFulfillmentPlanResponse,
    SimulationSpeedRequest,
    SimulationSpeedResponse,
    SimulationStartRequest,
    SimulationTestControlResponse,
    SimulationTestStartRequest,
    TransportLayoutPlanResponse,
    TopologyPayload,
)
from ..services.goods import build_inventory, load_catalog
from ..services.fulfillment_data import (
    FulfillmentDataValidationError,
    catalog_with_packaging,
    load_packaging_registry,
    load_simulation_inventory_profile,
    validate_general_simulation_orders,
    validate_simulation_test_scenario,
)
from ..services.order_fulfillment import (
    PackagingSpecificationError,
    plan_order_fulfillment,
    topology_inventory_balances,
)
from ..services.runtime import repo, simulation_service
from ..services.shuttle_topology import topology_revision
from ..services.transport_layout import (
    LayoutRevisionMismatch,
    LayoutValidationError,
    load_transport_layout_plan,
)
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


def _utf8_json_response(payload) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        media_type="application/json; charset=utf-8",
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/topologies")
def list_topologies():
    return _utf8_json_response(repo.list_topologies())


@router.post("/topologies")
def create_topology(payload: TopologyPayload):
    saved = repo.upsert_topology(payload.id, payload.name, payload.model_dump(by_alias=True))
    return _utf8_json_response(saved)


@router.get("/topologies/{topology_id}")
def get_topology(topology_id: str):
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    return _utf8_json_response(topology)


@router.get(
    "/topologies/{topology_id}/transport-layout-plan",
    response_model=TransportLayoutPlanResponse,
)
def get_transport_layout_plan(topology_id: str):
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    try:
        return load_transport_layout_plan(topology["payload"])
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Picking-arm preview plan not found") from error
    except LayoutRevisionMismatch as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LayoutValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


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
            {
                "topologyId": topology_id,
                "dataSource": "simulation_catalog_seed",
                "authoritative": False,
                "slots": build_inventory(payload),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        if len(_goods_inventory_cache) >= 8:
            _goods_inventory_cache.clear()
        _goods_inventory_cache[key] = body
    return Response(content=body, media_type="application/json")


@router.get("/goods/catalog")
def goods_catalog():
    return catalog_with_packaging(load_catalog())


@router.get("/goods/packaging-specs")
def goods_packaging_specs():
    return load_packaging_registry()


@router.post(
    "/topologies/{topology_id}/order-fulfillment-plan",
    response_model=OrderFulfillmentPlanResponse,
)
def create_order_fulfillment_plan(topology_id: str, payload: SimulationOrderInput):
    """Validate a base-unit order and return a non-mutating preparation plan."""
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    try:
        return plan_order_fulfillment(
            payload.model_dump(),
            catalog_with_packaging(load_catalog()),
            topology_inventory_balances(topology["payload"]),
        )
    except PackagingSpecificationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/topologies/{topology_id}/fulfillment-test-scenarios/{scenario_id}")
def get_fulfillment_test_scenario(topology_id: str, scenario_id: str):
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    try:
        return validate_simulation_test_scenario(
            topology["payload"],
            load_catalog(),
            scenario_id,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Fulfillment test scenario not found") from error
    except FulfillmentDataValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/topologies/{topology_id}/simulation-inventory-profiles/{profile_id}")
def get_simulation_inventory_profile(topology_id: str, profile_id: str):
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    try:
        profile = load_simulation_inventory_profile(profile_id)
        actual_revision = topology_revision(topology["payload"])
        if profile.get("topologyId") != topology_id or profile.get("topologyRevision") != actual_revision:
            raise FulfillmentDataValidationError("Simulation inventory profile topology revision mismatch")
        packaging = [
            item for item in load_packaging_registry()["records"]
            if item.get("scope") == "simulation" and item.get("profileId") == profile_id
        ]
        return {**profile, "packaging": packaging, "formalInventoryMutated": False}
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Simulation inventory profile not found") from error
    except FulfillmentDataValidationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/topologies/{topology_id}/general-simulation-plan")
def create_general_simulation_plan(topology_id: str, payload: GeneralSimulationPlanRequest):
    if payload.topologyId != topology_id:
        raise HTTPException(status_code=400, detail="Topology id mismatch")
    topology = repo.get_topology(topology_id)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    if payload.topologyRevision != topology_revision(topology["payload"]):
        raise HTTPException(status_code=409, detail="Topology revision mismatch")
    try:
        return validate_general_simulation_orders(
            topology["payload"],
            load_catalog(),
            payload.profileId,
            [order.model_dump() for order in payload.orders],
            [item.model_dump() for item in payload.inventoryOverrides],
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Simulation inventory profile not found") from error
    except (FulfillmentDataValidationError, PackagingSpecificationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


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


@router.post(
    "/simulations/test-scenarios/{scenario_id}/start",
    response_model=SimulationTestControlResponse,
)
async def start_simulation_test_scenario(
    scenario_id: str,
    payload: SimulationTestStartRequest,
):
    if payload.scenarioId != scenario_id:
        raise HTTPException(status_code=400, detail="Scenario id mismatch")
    topology = repo.get_topology(payload.topologyId)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    actual_revision = topology_revision(topology["payload"])
    if payload.topologyRevision != actual_revision:
        raise HTTPException(
            status_code=409,
            detail=f"Scenario expects {payload.topologyRevision}; current topology is {actual_revision}",
        )
    try:
        audit = validate_simulation_test_scenario(
            topology["payload"],
            load_catalog(),
            scenario_id,
        )
        return await simulation_service.start_simulation_test(topology["payload"], audit)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Fulfillment test scenario not found") from error
    except (FulfillmentDataValidationError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/simulations/general/start",
    response_model=GeneralSimulationControlResponse,
)
async def start_general_simulation(payload: GeneralSimulationPlanRequest):
    topology = repo.get_topology(payload.topologyId)
    if not topology:
        raise HTTPException(status_code=404, detail="Topology not found")
    actual_revision = topology_revision(topology["payload"])
    if payload.topologyRevision != actual_revision:
        raise HTTPException(status_code=409, detail="Topology revision mismatch")
    try:
        audit = validate_general_simulation_orders(
            topology["payload"],
            load_catalog(),
            payload.profileId,
            [order.model_dump() for order in payload.orders],
            [item.model_dump() for item in payload.inventoryOverrides],
            require_ready=True,
        )
        return await simulation_service.start_general_simulation(topology["payload"], audit)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Simulation inventory profile not found") from error
    except (FulfillmentDataValidationError, PackagingSpecificationError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/simulations")
def list_simulations(topology_id: str | None = None):
    return [
        {
            "id": item["id"],
            "status": item["status"],
            "topologyId": item["topology_id"],
            "tick": int(item["payload"].get("tick", 0)),
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }
        for item in repo.list_runs(topology_id)
    ]


@router.post("/simulations/{run_id}/pause", response_model=SimulationControlResponse)
async def pause_simulation(run_id: str):
    try:
        if run_id not in simulation_service.runtimes:
            await simulation_service.recover_simulation_test(run_id)
        return simulation_service.pause(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/simulations/{run_id}/orders")
async def insert_simulation_order(run_id: str, payload: SimulationOrderInput):
    """向已在运行或暂停的 Run 原子插入订单，返回最新权威状态。"""
    try:
        state = await simulation_service.add_order(run_id, payload.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(
        json.dumps(state, ensure_ascii=False, default=str).encode("utf-8"),
        media_type="application/json",
    )


@router.post("/simulations/{run_id}/orders/{order_id}/cancel")
async def cancel_simulation_order(
    run_id: str,
    order_id: str,
    payload: SimulationOrderCancelRequest,
):
    """安全取消订单：未取货任务直接释放，已取货任务先回库。"""
    try:
        state = await simulation_service.cancel_order(run_id, order_id, payload.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run or order not found")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(
        json.dumps(state, ensure_ascii=False, default=str).encode("utf-8"),
        media_type="application/json",
    )


@router.post("/simulations/{run_id}/resume", response_model=SimulationControlResponse)
async def resume_simulation(run_id: str):
    try:
        if run_id not in simulation_service.runtimes:
            await simulation_service.recover_simulation_test(run_id)
        return simulation_service.resume(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/simulations/{run_id}/stop", response_model=SimulationControlResponse)
async def stop_simulation(run_id: str):
    try:
        return await simulation_service.stop(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
        if run_id not in simulation_service.runtimes:
            await simulation_service.recover_simulation_test(run_id)
        return await simulation_service.reset(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")


@router.post("/simulations/{run_id}/speed", response_model=SimulationSpeedResponse)
async def set_simulation_speed(run_id: str, payload: SimulationSpeedRequest):
    try:
        if run_id not in simulation_service.runtimes:
            await simulation_service.recover_simulation_test(run_id)
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
