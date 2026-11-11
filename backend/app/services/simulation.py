from __future__ import annotations

import asyncio
import logging
import random
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from ..core.repository import Repository
from .assignment import assign_tasks
from .conveyor import (
    compile_conveyor_graph,
    nearest_lift_id,
    normalize_conveyor_anchors,
    route_from_block,
    route_from_block_branch,
    route_from_cell,
)
from .deadlock_episodes import ResourceType
from .deadlock_runtime_adapter import (
    DIAGNOSTICS_NAMESPACE,
    DeadlockRuntimeObserver,
    LifecycleEvent,
    RuntimeBlockEvidence,
)
from .goods import SKU_FIELDS, build_inventory, ctu_storage_nodes, load_catalog
from .motion_distance import MovementType
from .motion_runtime_adapter import (
    DIAGNOSTICS_NAMESPACE as MOTION_DIAGNOSTICS_NAMESPACE,
    EVENT_TYPE as MOTION_EVENT_TYPE,
    MAX_BATCH_SIZE as MOTION_MAX_BATCH_SIZE,
    MotionRuntimeObserver,
    RuntimePosition,
    physics_contract_from_config,
)
from .pathfinding import (
    build_occupied_lookup,
    build_node_lookup,
    build_walkable_lookup,
    cell_id,
    drop_zone_entry_cell,
    node_position,
    node_to_cell_id,
    parse_cell_id,
    pick_station_entry_cell,
    port_entry_cell,
    serviceable_storage_cells,
    shortest_path,
    shortest_path_shuttle,
    shuttle_rack_slots,
    shuttle_servable_slots,
)
from .simulation_config import build_simulation_config
from .transport_layout import _load_raw_plan
from .unified_transfer import (
    apply_drop,
    apply_pick,
    build_playback_route,
    build_transfer_runtime,
    cancel_transfer as cancel_transfer_inventory,
    plan_transfer,
)


logger = logging.getLogger(__name__)


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def find_task(tasks: list[dict[str, Any]], task_id: str | None) -> dict[str, Any] | None:
    if not task_id:
        return None
    return next((task for task in tasks if task["id"] == task_id), None)


ROBOT_CARRY_CAPACITY = {"ctu": 5, "shuttle": 1}
CTU_RACK_LAYERS = 17
# 每 tick 的推进间隔（秒）。调大=仿真更慢、机器人每步更易看清。
# 每个 tick 仍只移动一个格子，缩短调度间隔即可在不改变路径、避障和取货逻辑的前提下提速 2.5 倍。
TICK_INTERVAL_SECONDS = 0.6
DEFAULT_SPEED_MULTIPLIER = 2
SUPPORTED_SPEED_MULTIPLIERS = {1, 2, 4}
TERMINAL_TASK_STATUSES = {"completed", "cancelled", "failed", "exception"}
# CTU 出库口排队成本：每个在途任务在出货口约占用 24 tick（与拣选三阶段节拍同量级）。
PORT_QUEUE_SERVICE_TICKS = 24
# 机械臂交接：四向车在交接处等待后，机械臂抓取（pick）与放箱（place）的阶段时长。
ARM_HANDOFF_PICK_TICKS = 3
ARM_HANDOFF_PLACE_TICKS = 4
# 侧向出货口理货机械臂：抓取时长与交接臂一致；放箱阶段覆盖整段搬运动画
# （前端整周期约 6.6 秒，1× 下 3+8 tick 与之对齐），把货箱放到放置区。
ARM_DROPZONE_PICK_TICKS = 3
ARM_DROPZONE_PLACE_TICKS = 8
# 机械臂放箱后的上层输送必须与前端 CTU 货箱的 DELIVERY_SPEED 一致。
# 相邻 2×2 模块中心相距 2 个世界单位，因此每段需要约 1.11 秒。
ARM_CONVEYOR_VISUAL_SPEED = 1.8
ARM_CONVEYOR_BLOCK_DISTANCE = 2.0
TELEMETRY_EVENT_TYPES = {
    "metrics_snapshot",
    "resource_occupied",
    "robot_position",
    "conveyor_position",
    "arm_handoff",
    MOTION_EVENT_TYPE,
}
# 设备连续等待多少 tick 仍无法前进时，尝试绕开其它设备重新寻路（避免头对头死锁）。
# 排序逐车推进时，后车最多需要等待数个 tick 才能观察到前车离格。
# 过早绕行会让两车同时改道并形成 A-B-A-B 活锁，因此先稳定等待 4 tick。
REROUTE_AFTER_BLOCK_TICKS = 4
YIELD_AFTER_BLOCK_TICKS = 4
MIN_SAFETY_TICKS = 2000
WEBSOCKET_SEND_TIMEOUT_SECONDS = 2.0


@dataclass
class RunRuntime:
    run_id: str
    topology: dict[str, Any]
    config: dict[str, Any]
    state: dict[str, Any]
    metrics: dict[str, Any]
    alerts: list[dict[str, Any]] = field(default_factory=list)
    paused: bool = False
    stopped: bool = False
    task_handle: asyncio.Task | None = None
    alert_memory: dict[str, int] = field(default_factory=dict)
    last_broadcast_inventory_size: int = -1
    inventory_delta: list[dict[str, Any]] = field(default_factory=list)
    speed_multiplier: int = DEFAULT_SPEED_MULTIPLIER
    initial_state: dict[str, Any] = field(default_factory=dict)
    initial_metrics: dict[str, Any] = field(default_factory=dict)
    # 显式分层输送模型编译产物（pyc-new 移植）：空 dict 表示回退 legacy 固定 tick
    conveyor_graph: dict[str, Any] = field(default_factory=dict)
    # 机械臂交接目标（交接处格 + 放货点带面）；None 表示未启用机械臂交接
    arm_handoff: dict[str, Any] | None = None
    # 侧向出货口理货机械臂：把送到出货口的货箱搬运到放置区（drop_zone）
    secondary_arms: list[dict[str, Any]] = field(default_factory=list)
    # P2-2A 只读死锁观测器；不参与路径、调度或车辆状态决策。
    deadlock_observer: DeadlockRuntimeObserver | None = None
    # P3-2 read-only mileage observer; it never participates in movement decisions.
    motion_observer: MotionRuntimeObserver | None = None
    motion_collection_enabled: bool = True
    resetting: bool = False


class WebSocketHub:
    def __init__(self) -> None:
        self.connections: dict[str, list[WebSocket]] = {}

    async def connect(self, run_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.setdefault(run_id, []).append(websocket)

    def disconnect(self, run_id: str, websocket: WebSocket) -> None:
        if run_id in self.connections:
            self.connections[run_id] = [conn for conn in self.connections[run_id] if conn is not websocket]

    async def broadcast(self, run_id: str, message: dict[str, Any]) -> None:
        for websocket in list(self.connections.get(run_id, [])):
            try:
                # A disconnected or back-pressured browser must not stop the
                # single authoritative simulation clock.
                await asyncio.wait_for(
                    websocket.send_json(message),
                    timeout=WEBSOCKET_SEND_TIMEOUT_SECONDS,
                )
            except Exception:
                logger.exception(
                    "Simulation WebSocket send failed: run_id=%s type=%s tick=%s",
                    run_id,
                    message.get("type"),
                    message.get("tick"),
                )
                self.disconnect(run_id, websocket)
                # Removing a socket from the hub is not enough: the browser
                # otherwise remains OPEN and disables its HTTP polling fallback.
                try:
                    await websocket.close(code=1011, reason="simulation stream failed")
                except Exception:
                    logger.debug("Simulation WebSocket was already closed: run_id=%s", run_id)


class SimulationService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.hub = WebSocketHub()
        self.runtimes: dict[str, RunRuntime] = {}

    async def start(
        self,
        topology: dict[str, Any],
        orders: list[dict[str, Any]] | None = None,
        transfers: list[dict[str, Any]] | None = None,
        motion_physics_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = build_simulation_config(topology)
        if motion_physics_contract is not None:
            source = motion_physics_contract.get("source", "USER_DEFINED_SIMULATION")
            calibration_status = motion_physics_contract.get("calibrationStatus", "UNVERIFIED")
            if source != "USER_DEFINED_SIMULATION":
                raise ValueError("motionPhysicsContract.source must be USER_DEFINED_SIMULATION")
            if calibration_status != "UNVERIFIED":
                raise ValueError("motionPhysicsContract.calibrationStatus must be UNVERIFIED")
            candidate_config = {**config, "motionPhysicsContract": deepcopy(motion_physics_contract)}
            contract = physics_contract_from_config(candidate_config)
            if contract is None:
                raise ValueError("motionPhysicsContract is required when metric distance is enabled")
            config["motionPhysicsContract"] = {
                **contract.to_dict(),
                "source": source,
                "calibrationStatus": calibration_status,
            }
        if not config["robots"]:
            raise ValueError("当前拓扑没有 CTU 或四向车设备，无法启动仿真")
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        runtime = self._create_runtime(run_id, topology, config, orders, transfers)
        self.runtimes[run_id] = runtime
        self.repo.create_run(run_id, "running", topology["id"], runtime.state, runtime.metrics)
        runtime.task_handle = asyncio.create_task(self._loop(runtime))
        return {"runId": run_id, "status": "running"}

    def pause(self, run_id: str) -> dict[str, Any]:
        runtime = self._require_runtime(run_id)
        if runtime.stopped or runtime.state.get("status") in {"stopped", "completed"}:
            raise ValueError("Stopped or completed Run cannot be paused; reset it first")
        runtime.paused = True
        runtime.state["status"] = "paused"
        self._persist(runtime)
        return {"runId": run_id, "status": "paused"}

    def resume(self, run_id: str) -> dict[str, Any]:
        runtime = self._require_runtime(run_id)
        if runtime.stopped or runtime.state.get("status") in {"stopped", "completed"}:
            raise ValueError("Stopped or completed Run cannot be resumed; reset it first")
        runtime.paused = False
        runtime.state["status"] = "running"
        self._persist(runtime)
        return {"runId": run_id, "status": "running"}

    def stop(self, run_id: str) -> dict[str, Any]:
        runtime = self._require_runtime(run_id)
        runtime.stopped = True
        runtime.state["status"] = "stopped"
        runtime.state["stopReason"] = "user"
        self._finalize_deadlock_observation(runtime, "user")
        self._finalize_motion_observation(runtime, "user")
        self._persist(runtime)
        return {"runId": run_id, "status": "stopped"}

    def set_speed(self, run_id: str, multiplier: int) -> dict[str, Any]:
        runtime = self._require_runtime(run_id)
        if multiplier not in SUPPORTED_SPEED_MULTIPLIERS:
            raise ValueError("Simulation speed must be one of 1, 2, or 4")
        if runtime.stopped or runtime.state.get("status") in {"stopped", "completed"}:
            raise ValueError("Stopped or completed Run speed cannot be changed; reset it first")
        runtime.speed_multiplier = multiplier
        runtime.state["speedMultiplier"] = multiplier
        runtime.state["tickIntervalSeconds"] = self._tick_delay(runtime)
        runtime.state["updatedAt"] = now_dt().isoformat()
        self._record_event(runtime, runtime.state["tick"], "simulation_speed_changed", {"multiplier": multiplier})
        self._persist(runtime)
        return {"runId": run_id, "status": runtime.state["status"], "speedMultiplier": multiplier}

    async def add_order(self, run_id: str, order: dict[str, Any]) -> dict[str, Any]:
        """向当前 Run 插入一张订单，并立即完成库存预留和任务拆解。

        仿真时钟和 HTTP 请求共用同一 asyncio 事件循环；本方法在第一个
        await 之前完成全部状态变更，因此不会与单个 tick 的同步更新交叉。
        """
        runtime = self._require_runtime(run_id)
        if runtime.stopped or runtime.state.get("status") not in {"running", "paused"}:
            raise ValueError("只能向运行中或已暂停的仿真插入订单")

        order_id = str(order.get("id") or "").strip()
        if not order_id:
            raise ValueError("订单编号不能为空")
        if any(item.get("id") == order_id for item in runtime.state.get("orders", [])):
            raise ValueError(f"订单 {order_id} 已存在，请勿重复提交")

        # 运行库存中，已取走的料箱会被删除；尚未取货但已分配给
        # 其他任务的料箱仍在库存里，必须额外排除，避免重复预留。
        # 四向车货道箱按 cellId+layer+depth 唯一；CTU 库位槽按 nodeId+layer 唯一。
        def reservation_key(entry: dict[str, Any]) -> tuple[Any, ...]:
            if entry.get("depth") is not None:
                return (entry.get("cellId"), int(entry.get("layer", 1)), int(entry.get("depth")))
            return (entry.get("nodeId"), int(entry.get("layer", 1)))

        reserved_slots = {
            reservation_key(line)
            for task in runtime.state.get("tasks", [])
            if task.get("status") not in TERMINAL_TASK_STATUSES
            for line in task.get("lines", [])
            if line.get("nodeId") or line.get("depth") is not None
        }
        available_inventory = [
            slot
            for slot in runtime.state.get("inventory", [])
            if reservation_key(slot) not in reserved_slots
        ]
        existing_port_loads: dict[str, int] = {}
        for existing_task in runtime.state.get("tasks", []):
            if (
                existing_task.get("robotType") == "ctu"
                and existing_task.get("status") not in TERMINAL_TASK_STATUSES
            ):
                delivery_cell = str(existing_task.get("deliveryCellId") or "")
                if delivery_cell:
                    existing_port_loads[delivery_cell] = existing_port_loads.get(delivery_cell, 0) + 1
        # 认领状态是本次拆单的临时上下文，不能直接写回 runtime.state（含元组键）。
        shuttle_batch = {"cursor": int((runtime.state.get("shuttleBatch") or {}).get("cursor") or 0)}
        tasks, order_states = self._seed_order_tasks(
            runtime.topology,
            [order],
            available_inventory,
            existing_port_loads,
            shuttle_docks=runtime.state.get("shuttleDocks")
            or self._shuttle_dock_states(runtime.topology),
            shuttle_batch=shuttle_batch,
            existing_tasks=runtime.state.get("tasks", []),
        )
        runtime.state["shuttleBatch"] = {"cursor": int(shuttle_batch.get("cursor") or 0)}
        order_state = order_states[0]
        missing = int(order_state.get("missingQuantity") or 0)
        if missing or not tasks:
            raise ValueError(f"订单 {order_id} 库存不足，缺少 {missing or 1} 箱；未插入当前仿真")

        tick = int(runtime.state.get("tick", 0))
        order_state["insertedTick"] = tick
        runtime.state.setdefault("orders", []).append(order_state)
        runtime.state.setdefault("tasks", []).extend(tasks)
        runtime.state["orderDriven"] = True
        runtime.state["updatedAt"] = now_dt().isoformat()
        self._record_event(runtime, tick, "order_inserted", {"orderId": order_id})
        decomposition = order_state.get("decomposition") or {}
        self._record_event(
            runtime,
            tick,
            "order_inventory_matched",
            {
                "orderId": order_id,
                "requestedQuantity": decomposition.get("requestedQuantity", 0),
                "allocatedQuantity": decomposition.get("allocatedQuantity", 0),
                "missingQuantity": 0,
            },
        )
        self._record_event(
            runtime,
            tick,
            "order_tasks_generated",
            {
                "orderId": order_id,
                "taskIds": order_state.get("taskIds", []),
                "taskCount": decomposition.get("taskCount", 0),
                "strategy": decomposition.get("strategy", "balanced"),
                "maxParallelCtu": decomposition.get("maxParallelCtu", 1),
            },
        )
        self._refresh_orders(runtime)
        self._refresh_metrics(runtime)
        self._persist(runtime)
        await self._broadcast_tick(runtime)
        return runtime.state

    async def cancel_order(self, run_id: str, order_id: str, reason: str = "用户取消") -> dict[str, Any]:
        """安全取消订单，并保持车辆、库存和任务状态一致。

        尚未取货的任务立即取消；已经从库存取走货箱的任务复用回库路径，
        只有全部货箱恢复到原库位后订单才进入 cancelled 终态。
        """
        runtime = self._require_runtime(run_id)
        if runtime.stopped or runtime.state.get("status") not in {"running", "paused"}:
            raise ValueError("只能取消运行中或已暂停仿真里的订单")
        order = next(
            (item for item in runtime.state.get("orders", []) if item.get("id") == order_id),
            None,
        )
        if order is None:
            raise KeyError(order_id)
        if order.get("status") == "cancelled":
            return runtime.state
        if order.get("status") == "completed":
            raise ValueError("订单已经完成，不能删除；可以在历史记录中归档")

        tasks = [
            task for task in runtime.state.get("tasks", [])
            if task.get("orderId") == order_id
        ]
        if not tasks:
            raise ValueError("订单没有可取消的任务")
        if any(task.get("status") == "completed" for task in tasks):
            raise ValueError("订单已有任务完成交付，不能直接取消")
        if any(task.get("status") in {"failed", "exception"} for task in tasks):
            raise ValueError("订单包含异常任务，请先处理告警后再取消")

        # 先完整校验再开始修改，避免多任务订单取消到一半才发现某辆载货车不可用。
        for task in tasks:
            robot = next(
                (
                    item for item in runtime.state.get("robots", [])
                    if item.get("currentTaskId") == task.get("id")
                    or item.get("id") == task.get("assignedRobotId")
                ),
                None,
            )
            picked_count = max(
                int(task.get("picked", 0) or 0),
                len(((robot or {}).get("carrying") or [])),
                len(task.get("transitGoods") or []),
            )
            if picked_count > 0 and (
                robot is None
                or robot.get("failed")
                or robot.get("status") in {"failed", "exception"}
            ):
                raise ValueError(f"任务 {task['id']} 已取货，但车辆不可用，无法自动回库")

        tick = int(runtime.state.get("tick", 0))
        order["cancelRequested"] = True
        order["cancelRequestedTick"] = tick
        order["cancelReason"] = reason
        returning = False

        for task in tasks:
            if task.get("status") == "cancelled":
                continue
            robot = next(
                (
                    item for item in runtime.state.get("robots", [])
                    if item.get("currentTaskId") == task.get("id")
                    or item.get("id") == task.get("assignedRobotId")
                ),
                None,
            )
            carried_goods = [dict(item) for item in ((robot or {}).get("carrying") or [])]
            transit_goods = [dict(item) for item in (task.get("transitGoods") or [])]
            picked_count = max(
                int(task.get("picked", 0) or 0),
                len(carried_goods),
                len(transit_goods),
            )
            if picked_count > 0:
                assert robot is not None
                return_lines = [dict(item) for item in (task.get("lines") or [])[:picked_count]]
                goods = carried_goods or transit_goods or return_lines
                task["cancelAfterReturn"] = True
                task["cancelReason"] = reason
                task["cancelReturnLines"] = return_lines
                task["transitGoods"] = goods
                task["returnLineIndex"] = 0
                task["remainingStageTicks"] = 0
                robot["carrying"] = [dict(item) for item in goods]
                robot["load"] = min(int(robot.get("capacity", 1)), len(goods))
                robot["activePortId"] = None
                robot["handlingAction"] = None
                self._start_return_leg(runtime, robot, task)
                returning = True
            else:
                self._cancel_unpicked_task(runtime, task, robot, reason)

        order["status"] = "returning" if returning else "cancelled"
        order["statusLabel"] = "取消回库中" if returning else "已取消"
        if not returning:
            order["cancelledTick"] = tick
        decomposition = order.setdefault("decomposition", {})
        decomposition["stage"] = "returning" if returning else "cancelled"
        self._record_event(
            runtime,
            tick,
            "order_cancel_requested",
            {"orderId": order_id, "reason": reason, "returning": returning},
        )
        self._refresh_orders(runtime)
        self._refresh_metrics(runtime)
        self._persist(runtime)
        await self._broadcast_tick(runtime)
        return runtime.state

    def _cancel_unpicked_task(
        self,
        runtime: RunRuntime,
        task: dict[str, Any],
        robot: dict[str, Any] | None,
        reason: str,
    ) -> None:
        tick = int(runtime.state.get("tick", 0))
        task["status"] = "cancelled"
        task["phase"] = "cancelled"
        task["phaseLabel"] = "订单已取消"
        task["cancelReason"] = reason
        task["cancelledTick"] = tick
        task["remainingStageTicks"] = 0
        task.setdefault("history", []).append(
            {"tick": tick, "phase": "cancelled", "status": "cancelled"}
        )
        if robot is not None and robot.get("currentTaskId") == task.get("id"):
            robot["currentTaskId"] = None
            robot["status"] = "idle"
            robot["phaseLabel"] = "待命"
            robot["path"] = [robot["currentCellId"]]
            robot["pathLayers"] = [robot.get("currentLayer", 1)]
            robot["handlingAction"] = None
            robot["activePortId"] = None
            robot["blockedByRobotId"] = None
            robot["returningToStandby"] = False
            robot["load"] = 0
            robot["carrying"] = []
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {"taskId": task["id"], "phase": "cancelled", "status": "cancelled", "reason": reason},
        )

    async def reset(self, run_id: str) -> dict[str, Any]:
        runtime = self._require_runtime(run_id)
        runtime.resetting = True
        runtime.stopped = True
        if runtime.task_handle and not runtime.task_handle.done():
            runtime.task_handle.cancel()
            try:
                await runtime.task_handle
            except asyncio.CancelledError:
                pass
        self._finalize_deadlock_observation(runtime, "reset")
        previous_observer = self._ensure_deadlock_observer(runtime)
        self._finalize_motion_observation(runtime, "reset")
        previous_motion_observer = runtime.motion_observer
        runtime.state = deepcopy(runtime.initial_state)
        runtime.metrics = deepcopy(runtime.initial_metrics)
        runtime.state["status"] = "paused"
        runtime.state["stopReason"] = None
        runtime.state["updatedAt"] = now_dt().isoformat()
        runtime.paused = True
        runtime.stopped = False
        runtime.alerts = []
        runtime.alert_memory.clear()
        runtime.last_broadcast_inventory_size = -1
        runtime.inventory_delta = []
        runtime.speed_multiplier = DEFAULT_SPEED_MULTIPLIER
        runtime.state["speedMultiplier"] = DEFAULT_SPEED_MULTIPLIER
        runtime.state["tickIntervalSeconds"] = self._tick_delay(runtime)
        runtime.deadlock_observer = previous_observer.new_generation(runtime.topology)
        runtime.state[DIAGNOSTICS_NAMESPACE] = runtime.deadlock_observer.diagnostics_payload()
        if previous_motion_observer is not None:
            runtime.motion_observer = previous_motion_observer.new_generation()
            runtime.motion_observer.register_robots(runtime.state.get("robots", []))
            runtime.state[MOTION_DIAGNOSTICS_NAMESPACE] = runtime.motion_observer.diagnostics_payload()
        runtime.resetting = False
        runtime.task_handle = asyncio.create_task(self._loop(runtime))
        self._record_event(runtime, 0, "simulation_reset", {"runId": run_id})
        self._persist(runtime)
        return {"runId": run_id, "status": "paused"}

    def cancel_transfer(self, run_id: str, task_id: str) -> dict[str, Any]:
        runtime = self._require_runtime(run_id)
        task = find_task(runtime.state.get("tasks", []), task_id)
        if not task or task.get("mode") != "shuttle_transfer":
            raise KeyError(task_id)
        changed = cancel_transfer_inventory(runtime.state, task, runtime.state["tick"])
        robot = next((item for item in runtime.state["robots"] if item.get("currentTaskId") == task_id), None)
        if robot:
            robot["currentTaskId"] = None
            robot["status"] = "idle"
            robot["phaseLabel"] = "待命"
            robot["load"] = 0
            robot["path"] = [robot["currentCellId"]]
            robot["pathLayers"] = [robot.get("currentLayer", 1)]
            robot["pathStages"] = ["cancelled"]
        if changed:
            self._record_event(runtime, runtime.state["tick"], "transfer_cancelled", {"taskId": task_id})
        self._refresh_metrics(runtime)
        self._persist(runtime)
        return task

    def fail_transfer(self, run_id: str, task_id: str, reason: str) -> dict[str, Any]:
        runtime = self._require_runtime(run_id)
        task = find_task(runtime.state.get("tasks", []), task_id)
        if not task or task.get("mode") != "shuttle_transfer":
            raise KeyError(task_id)
        if task.get("status") in {"completed", "cancelled"}:
            raise ValueError("Terminal transfer cannot be failed")
        task["status"] = "failed"
        task["phase"] = "failed"
        task["phaseLabel"] = "运输失败（库存状态已冻结）"
        task["failureReason"] = reason
        robot = next((item for item in runtime.state["robots"] if item.get("currentTaskId") == task_id), None)
        if robot:
            robot["currentTaskId"] = None
            robot["status"] = "exception"
            robot["phaseLabel"] = task["phaseLabel"]
        self._record_event(runtime, runtime.state["tick"], "transfer_failed", {"taskId": task_id, "reason": reason})
        self._persist(runtime)
        return task

    async def retry_transfer(self, run_id: str, task_id: str) -> dict[str, Any]:
        runtime = self._require_runtime(run_id)
        task = find_task(runtime.state.get("tasks", []), task_id)
        if not task or task.get("mode") != "shuttle_transfer":
            raise KeyError(task_id)
        if task.get("status") != "failed":
            raise ValueError("Only failed transfers can be retried")
        task["failureReason"] = None
        if int(task.get("inTransitQuantity", 0)) > 0:
            robot = next(item for item in runtime.state["robots"] if item["id"] == task["requestedVehicleId"])
            current_node = f"L{round(float(robot.get('currentLayer', 1)))}-r{robot['currentRow']}-c{robot['currentCol']}"
            task["plan"] = plan_transfer(
                runtime.topology,
                current_node,
                task["destinationTrackNode"],
                task["plan"]["algorithm"],
            )
            self._start_transfer_route(runtime, robot, task)
            task["status"] = "assigned"
        else:
            task["status"] = "pending"
            task["phase"] = "reserved"
            task["phaseLabel"] = "等待重试调度"
            robot = next(
                item for item in runtime.state["robots"]
                if item["id"] == task["requestedVehicleId"]
            )
            robot["currentTaskId"] = None
            robot["status"] = "idle"
            robot["phaseLabel"] = "待命"
        runtime.stopped = False
        runtime.paused = False
        runtime.state["status"] = "running"
        runtime.state["stopReason"] = None
        if runtime.task_handle is None or runtime.task_handle.done():
            runtime.task_handle = asyncio.create_task(self._loop(runtime))
        self._record_event(runtime, runtime.state["tick"], "transfer_retried", {"taskId": task_id})
        self._persist(runtime)
        return task

    def get_state(self, run_id: str) -> dict[str, Any]:
        runtime = self.runtimes.get(run_id)
        if runtime:
            return runtime.state
        saved = self.repo.get_run(run_id)
        if not saved:
            raise KeyError(run_id)
        return saved["payload"]

    def get_metrics(self, run_id: str) -> dict[str, Any]:
        runtime = self.runtimes.get(run_id)
        if runtime:
            return runtime.metrics
        saved = self.repo.get_run(run_id)
        if not saved:
            raise KeyError(run_id)
        return saved["metrics"]

    def _require_runtime(self, run_id: str) -> RunRuntime:
        runtime = self.runtimes.get(run_id)
        if not runtime:
            raise KeyError(run_id)
        return runtime

    def _ensure_deadlock_observer(self, runtime: RunRuntime) -> DeadlockRuntimeObserver:
        if runtime.deadlock_observer is not None:
            return runtime.deadlock_observer
        saved = runtime.state.get(DIAGNOSTICS_NAMESPACE) or {}
        generation = max(1, int(saved.get("generation") or 1))
        runtime.deadlock_observer = DeadlockRuntimeObserver(
            runtime.run_id,
            generation,
            runtime.topology,
            initial_tick=int(runtime.state.get("tick") or 0),
        )
        if saved.get("activeEpisodes") and not saved.get("ended"):
            runtime.deadlock_observer.mark_incomplete_recovery(
                saved,
                int(runtime.state.get("tick") or 0),
            )
        runtime.state[DIAGNOSTICS_NAMESPACE] = runtime.deadlock_observer.diagnostics_payload()
        return runtime.deadlock_observer

    def _begin_deadlock_observation(self, runtime: RunRuntime, tick: int) -> None:
        observer = self._ensure_deadlock_observer(runtime)
        observer.begin_tick(tick, runtime.state)

    def _observe_robot_block(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        attempted_next_cell_id: str | None,
        blocker_robot_id: str | None,
        resource_type: ResourceType,
        reason: str,
    ) -> None:
        observer = self._ensure_deadlock_observer(runtime)
        observer.observe_block(
            RuntimeBlockEvidence(
                tick=int(runtime.state.get("tick") or 0),
                robot_id=str(robot.get("id") or ""),
                current_cell_id=robot.get("currentCellId"),
                attempted_next_cell_id=attempted_next_cell_id,
                blocker_robot_id=blocker_robot_id,
                resource_type=resource_type,
                reason=reason,
                task_id=robot.get("currentTaskId"),
                current_layer=int(robot.get("currentLayer") or 1),
                current_floor=robot.get("currentFloor"),
            )
        )

    def _observe_deadlock_mitigation(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        action: str,
        details: str | None = None,
    ) -> None:
        observer = self._ensure_deadlock_observer(runtime)
        observer.observe_mitigation(
            int(runtime.state.get("tick") or 0),
            str(robot.get("id") or ""),
            action,
            details,
        )

    def _flush_deadlock_events(
        self,
        runtime: RunRuntime,
        events: tuple[LifecycleEvent, ...],
    ) -> None:
        observer = self._ensure_deadlock_observer(runtime)
        for event in events:
            try:
                self._record_event(runtime, event.tick, event.event_type, event.payload)
            except Exception:
                logger.exception(
                    "Failed to persist deadlock lifecycle event: run_id=%s transition=%s",
                    runtime.run_id,
                    event.transition_key,
                )
                break
            observer.acknowledge_event(event.transition_key)
        runtime.state[DIAGNOSTICS_NAMESPACE] = observer.diagnostics_payload()

    def _advance_deadlock_observation(self, runtime: RunRuntime, tick: int) -> None:
        observer = self._ensure_deadlock_observer(runtime)
        try:
            events = observer.process_tick(tick, runtime.state)
        except Exception as error:
            logger.exception(
                "Deadlock diagnostics failed without stopping simulation: run_id=%s tick=%s",
                runtime.run_id,
                tick,
            )
            observer.record_failure(tick, error)
            events = observer.pending_events()
        self._flush_deadlock_events(runtime, events)

    def _finalize_deadlock_observation(self, runtime: RunRuntime, reason: str) -> None:
        observer = self._ensure_deadlock_observer(runtime)
        try:
            events = observer.finish(int(runtime.state.get("tick") or 0), reason)
        except Exception as error:
            logger.exception(
                "Deadlock diagnostics finalization failed: run_id=%s reason=%s",
                runtime.run_id,
                reason,
            )
            observer.record_failure(int(runtime.state.get("tick") or 0), error)
            events = observer.pending_events()
        self._flush_deadlock_events(runtime, events)

    def _ensure_motion_observer(self, runtime: RunRuntime) -> MotionRuntimeObserver | None:
        if not runtime.motion_collection_enabled:
            return None
        if runtime.motion_observer is not None:
            return runtime.motion_observer
        saved = runtime.state.get(MOTION_DIAGNOSTICS_NAMESPACE)
        deadlock_diagnostics = runtime.state.get(DIAGNOSTICS_NAMESPACE) or {}
        generation = max(
            1,
            int(
                (saved or {}).get("generation")
                or deadlock_diagnostics.get("generation")
                or 1
            ),
        )
        physics_config = runtime.config
        if physics_config.get("motionPhysicsContract") is None and isinstance(saved, dict):
            frozen = saved.get("physicsContract")
            if isinstance(frozen, dict):
                physics_config = {**runtime.config, "motionPhysicsContract": frozen}
        observer = MotionRuntimeObserver(
            runtime.run_id,
            generation,
            physics_contract_from_config(physics_config),
            initial_tick=int(runtime.state.get("tick") or 0),
            recovered_diagnostics=saved if isinstance(saved, dict) else None,
        )
        observer.register_robots(runtime.state.get("robots", []))
        runtime.motion_observer = observer
        runtime.state[MOTION_DIAGNOSTICS_NAMESPACE] = observer.diagnostics_payload()
        return observer

    def _begin_motion_observation(self, runtime: RunRuntime, tick: int) -> None:
        observer = self._ensure_motion_observer(runtime)
        if observer is None:
            return
        observer.begin_tick(tick)
        runtime.state[MOTION_DIAGNOSTICS_NAMESPACE] = observer.diagnostics_payload()

    def _motion_position(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
    ) -> RuntimePosition | None:
        observer = self._ensure_motion_observer(runtime)
        if observer is None:
            return None
        try:
            return RuntimePosition.from_robot(robot)
        except Exception as error:
            observer.record_observation_failure(
                int(runtime.state.get("tick") or 0),
                str(robot.get("id") or "unknown"),
                str(robot.get("type") or "unknown"),
                error,
            )
            runtime.state[MOTION_DIAGNOSTICS_NAMESPACE] = observer.diagnostics_payload()
            logger.exception(
                "Motion position snapshot failed without changing simulation: run_id=%s robot_id=%s",
                runtime.run_id,
                robot.get("id"),
            )
            return None

    def _record_committed_motion(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        from_position: RuntimePosition | None,
        movement_type: MovementType | None,
        source: str,
    ) -> None:
        observer = self._ensure_motion_observer(runtime)
        if observer is None or from_position is None:
            return
        try:
            to_position = RuntimePosition.from_robot(robot)
            resolved_type = movement_type
            if resolved_type is None:
                resolved_type = (
                    MovementType.LIFT
                    if from_position.layer != to_position.layer
                    or from_position.floor != to_position.floor
                    else MovementType.NORMAL
                )
            outcome = observer.capture_committed_move(
                robot_id=str(robot.get("id") or "unknown"),
                robot_type=str(robot.get("type") or "unknown"),
                from_position=from_position,
                to_position=to_position,
                tick=int(runtime.state.get("tick") or 0),
                movement_type=resolved_type,
                source=source,
                # Current runtime paths have no stable, persisted plan/replan identity.
                planned_path_id=None,
                replan_id=None,
            )
        except Exception as error:
            observer.record_observation_failure(
                int(runtime.state.get("tick") or 0),
                str(robot.get("id") or "unknown"),
                str(robot.get("type") or "unknown"),
                error,
            )
            logger.exception(
                "Motion observation failed without changing simulation: run_id=%s robot_id=%s",
                runtime.run_id,
                robot.get("id"),
            )
        runtime.state[MOTION_DIAGNOSTICS_NAMESPACE] = observer.diagnostics_payload()

    def _flush_motion_events(self, runtime: RunRuntime) -> None:
        observer = self._ensure_motion_observer(runtime)
        if observer is None:
            return
        pending = observer.pending_events()
        offset = 0
        while offset < len(pending):
            tick = pending[offset].tick
            same_tick = []
            while (
                offset < len(pending)
                and pending[offset].tick == tick
                and len(same_tick) < MOTION_MAX_BATCH_SIZE
            ):
                same_tick.append(pending[offset])
                offset += 1
            batch = tuple(same_tick)
            try:
                self._record_event(
                    runtime,
                    tick,
                    MOTION_EVENT_TYPE,
                    observer.batch_payload(batch),
                )
            except Exception as error:
                for event in batch:
                    observer.record_persistence_failure(event, error)
                logger.exception(
                    "Motion event batch persistence failed without stopping simulation: run_id=%s tick=%s count=%s",
                    runtime.run_id,
                    tick,
                    len(batch),
                )
            else:
                for event in batch:
                    observer.acknowledge_persistence(event)
        runtime.state[MOTION_DIAGNOSTICS_NAMESPACE] = observer.diagnostics_payload()

    def _finalize_motion_observation(self, runtime: RunRuntime, reason: str) -> None:
        observer = self._ensure_motion_observer(runtime)
        if observer is None:
            return
        self._flush_motion_events(runtime)
        observer.finish(int(runtime.state.get("tick") or 0), reason)
        runtime.state[MOTION_DIAGNOSTICS_NAMESPACE] = observer.diagnostics_payload()

    def _create_runtime(
        self,
        run_id: str,
        topology: dict[str, Any],
        config: dict[str, Any],
        orders: list[dict[str, Any]] | None = None,
        transfers: list[dict[str, Any]] | None = None,
    ) -> RunRuntime:
        topology = normalize_conveyor_anchors(topology)
        # pyc-new 显式分层输送模型：拓扑声明了 conveyorDecks/conveyorBinding 时编译逐块路由；
        # 未声明（当前 666 拓扑）时 explicit=False，仿真走原固定 tick 行为。
        conveyor_graph = compile_conveyor_graph(topology)
        if conveyor_graph.get("explicit") and conveyor_graph.get("issues"):
            issue = conveyor_graph["issues"][0]
            raise ValueError(
                f"输送线拓扑无效：{issue.get('blockId')} {issue.get('deck')}层，{issue.get('message')}"
            )
        # 机械臂交接：拓扑有 picking_handoff 标记 + 布局计划声明放货点时启用。
        arm_handoff = self._arm_handoff_target(topology)
        # 侧向出货口理货机械臂：把送达出货口的货箱搬运到放置区。
        secondary_arms = self._secondary_arm_configs(topology, conveyor_graph)
        node_lookup = build_node_lookup(topology)
        robots: list[dict[str, Any]] = []
        for robot in config["robots"]:
            if not robot.get("enabled", True):
                continue
            start_node = node_lookup.get(robot["startNode"])
            if start_node is None:
                raise ValueError(
                    f"Robot {robot.get('id', '')} references missing start node {robot.get('startNode')} in topology {topology.get('id')}."
                )
            start_floor = start_node.get("floor", "1F")
            start_cell = node_to_cell_id(topology, robot["startNode"])
            robots.append(
                {
                    "id": robot["id"],
                    "type": robot["type"],
                    "currentNode": start_cell,
                    "currentCellId": start_cell,
                    "homeCellId": start_cell,
                    "currentFloor": start_floor,
                    "currentRow": start_node["row"],
                    "currentCol": start_node["col"],
                    "currentLayer": 1,
                    "lastCellId": None,
                    "status": "idle",
                    "battery": robot["battery"],
                    "path": [start_cell],
                    "pathLayers": [1],
                    "targetCellId": start_cell,
                    "targetLayer": 1,
                    "currentTaskId": None,
                    "returningToStandby": False,
                    "handlingAction": None,
                    "waitTicks": 0,
                    "failed": False,
                    "phaseLabel": "待命",
                    "load": 0,
                    "carrying": [],
                    "capacity": ROBOT_CARRY_CAPACITY.get(robot["type"], 1),
                }
            )

        transfer_tasks, transfer_robots, transfer_inventory, transfer_events = build_transfer_runtime(
            topology, transfers or []
        )
        existing_robot_ids = {robot["id"] for robot in robots}
        robots.extend(robot for robot in transfer_robots if robot["id"] not in existing_robot_ids)

        inventory = build_inventory(topology)
        # 四向车出货位设置（订单任务沿用 dock-02/03 自动分配，不再单独驱动搬运）。
        shuttle_docks = self._shuttle_dock_states(topology)
        # 订单之间共享同一批箱认领状态，避免同一货箱被两条任务重复取走。
        shuttle_batch: dict[str, Any] = {"cursor": 0}
        if orders:
            # 四向车只在订单确实拆出整箱任务时工作，不再额外派发与订单无关的搬运任务。
            tasks, order_states = self._seed_order_tasks(
                topology,
                orders,
                inventory,
                shuttle_docks=shuttle_docks,
                shuttle_batch=shuttle_batch,
            )
        elif transfers:
            tasks, order_states = [], []
        else:
            tasks = self._seed_tasks(topology, config, inventory, shuttle_docks)
            order_states = []
        tasks.extend(transfer_tasks)
        state = {
            "id": run_id,
            "status": "running",
            "stopReason": None,
            "tick": 0,
            "speedMultiplier": DEFAULT_SPEED_MULTIPLIER,
            "tickIntervalSeconds": TICK_INTERVAL_SECONDS / DEFAULT_SPEED_MULTIPLIER,
            "eventCount": len(transfer_events),
            "topologyId": topology["id"],
            "motionPhysicsContract": deepcopy(config.get("motionPhysicsContract")),
            "orderDriven": bool(orders or transfers),
            "robots": robots,
            "tasks": tasks,
            "orders": order_states,
            "inventory": inventory,
            "shuttleDocks": shuttle_docks,
            # 只持久化游标：箱/货道认领是运行时临时状态（键为元组，无法 JSON 序列化），
            # 后续插入订单时会依据在途任务重新推导。
            "shuttleBatch": {"cursor": int(shuttle_batch.get("cursor") or 0)},
            "transferInventory": transfer_inventory,
            "transferEvents": transfer_events,
            "alerts": [],
            "queues": {"conveyor": [], "station": [], "buffer": []},
            "conveyorItems": [],
            "pickingArm": {"status": "idle", "stage": None, "remainingTicks": 0, "taskId": None, "robotId": None} if arm_handoff else None,
            "pickingArms": [
                {
                    "armId": config["armId"],
                    "nodeId": config["nodeId"],
                    "status": "idle",
                    "stage": None,
                    "remainingTicks": 0,
                    "taskId": None,
                    "cargoToken": None,
                    "portCellId": None,
                    "dropCellId": None,
                    "cargo": None,
                }
                for config in secondary_arms
            ],
            "armBeltBoxes": [],
            # 机械臂已搬进放置区的货箱（按 dropZone 内坐标渲染）。
            "armDropBoxes": [],
            "createdAt": now_dt().isoformat(),
            "updatedAt": now_dt().isoformat(),
        }
        metrics = {
            "throughput": 0,
            "completedTasks": 0,
            "pendingTasks": len(tasks),
            "activeRobots": 0,
            "blockedRobots": 0,
            "conveyorUtilization": 0.0,
            "deadlocksDetected": 0,
            "congestionEvents": 0,
            "urgentOrdersHandled": 0,
            "avgEtaTicks": 0.0,
            "avgCompletionTicks": 0.0,
            "stationQueueDepth": 0,
            "conveyorQueueDepth": 0,
            "bufferOccupancy": 0,
        }
        runtime = RunRuntime(
            run_id=run_id,
            topology=deepcopy(topology),
            config=deepcopy(config),
            state=state,
            metrics=metrics,
            speed_multiplier=DEFAULT_SPEED_MULTIPLIER,
            conveyor_graph=conveyor_graph,
            arm_handoff=arm_handoff,
            secondary_arms=secondary_arms,
            motion_collection_enabled=bool(config.get("motionDistanceCollectionEnabled", True)),
        )
        runtime.deadlock_observer = DeadlockRuntimeObserver(
            run_id,
            1,
            runtime.topology,
            initial_tick=0,
        )
        runtime.state[DIAGNOSTICS_NAMESPACE] = runtime.deadlock_observer.diagnostics_payload()
        if runtime.motion_collection_enabled:
            runtime.motion_observer = MotionRuntimeObserver(
                run_id,
                1,
                physics_contract_from_config(config),
                initial_tick=0,
            )
            runtime.motion_observer.register_robots(robots)
            runtime.state[MOTION_DIAGNOSTICS_NAMESPACE] = runtime.motion_observer.diagnostics_payload()
        # Every WebSocket connection receives one complete snapshot. Normal
        # tick frames can therefore omit the potentially very large inventory.
        runtime.last_broadcast_inventory_size = len(inventory)
        runtime.initial_state = deepcopy(state)
        runtime.initial_metrics = deepcopy(metrics)
        return runtime

    def _seed_order_tasks(
        self,
        topology: dict[str, Any],
        orders: list[dict[str, Any]],
        inventory: list[dict[str, Any]],
        existing_port_loads: dict[str, int] | None = None,
        shuttle_docks: list[dict[str, Any]] | None = None,
        shuttle_batch: dict[str, Any] | None = None,
        existing_tasks: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """按 SKU 箱规把订单拆成「四向车整箱 + CTU 尾箱」两库区子任务。

        数量语义：订单行 qtyMode=boxes 时用 caseCnt 折算件数（默认按件数），
        内部统一按件数计算：
        - 整箱数 floor(件数 / caseCnt) 交给四向车（dock-02/03 自动分配）；
        - 余下不足一箱的件数由 CTU 取 1 整箱送到拣货台，拣货时只拣所需件数。
        降级：四向车整箱不足时缺口整箱转 CTU；CTU 无可用箱时缺口转四向车；
        两侧都不足记 exception / missingQuantity。两侧子任务都挂同一个 orderId，
        订单完成由 _refresh_orders 按 orderId 统一汇总。
        """
        delivery_ports = self._ctu_delivery_ports(topology)
        if not delivery_ports:
            raise ValueError("当前拓扑没有输送线出入口，无法为订单生成交付任务")
        ctu_count = max(1, sum(1 for node in topology.get("nodes", []) if node.get("type") == "ctu_robot"))
        port_loads = dict(existing_port_loads or {})
        case_counts = self._sku_case_counts()

        # CTU 槽（无 depth）与四向车货道箱（有 depth）分池：两类库存不能互相顶替。
        ctu_by_sku: dict[str, list[dict[str, Any]]] = {}
        for slot in inventory:
            if slot.get("depth") is not None:
                continue
            sku_id = str(slot.get("skuId") or "")
            if sku_id:
                ctu_by_sku.setdefault(sku_id, []).append(slot)
        shuttle_sources = shuttle_servable_slots(topology)
        docks = [
            dock
            for dock in (shuttle_docks if shuttle_docks is not None else self._shuttle_dock_states(topology))
            if dock.get("enabled", True)
        ]
        # 四向车出货位：dock-02 / dock-03 自动分配，不按订单固定；缺省时退回全部出货位。
        order_docks = [dock for dock in docks if dock.get("dockId") in {"dock-02", "dock-03"}] or docks
        batch = shuttle_batch if shuttle_batch is not None else {"cursor": 0}
        pool_tasks = list(existing_tasks or [])
        shuttle_disabled = not (order_docks and shuttle_sources)

        tasks: list[dict[str, Any]] = []
        order_states: list[dict[str, Any]] = []
        used_slots: set[tuple[str, int]] = set()

        def allocate_shuttle_box(
            order: dict[str, Any],
            sku_id: str,
            line_id: str,
            case_cnt: int,
            demand_pieces: int,
        ) -> list[dict[str, Any]]:
            """生成一趟四向车整箱取货（含前置倒库），并把取货任务挂到订单上。"""
            if shuttle_disabled:
                return []
            dock = order_docks[int(batch.get("order_dock_cursor") or 0) % len(order_docks)]
            batch["order_dock_cursor"] = int(batch.get("order_dock_cursor") or 0) + 1
            urgent = order.get("priority") == "urgent"
            dock_cfg = {
                **dock,
                "skuId": sku_id,
                "quantity": 1,
                "priority": "urgent" if urgent else "normal",
                "cursor": int(dock.get("cursor") or 0),
                "requireSku": True,
            }
            dock_cfg.pop("vehicleId", None)
            new_tasks = self._build_dock_tasks(
                topology,
                inventory,
                dock_cfg,
                shuttle_sources,
                0,
                batch,
                pool_tasks,
            )
            dock["cursor"] = int(dock_cfg.get("cursor") or 0)
            for task in new_tasks:
                task["orderId"] = order["id"]
                task["orderLineId"] = line_id
                task["caseCnt"] = case_cnt
                if task.get("taskKind") != "relocate":
                    task["taskKind"] = "shuttle_order_pick"
                    task["demandPieces"] = int(demand_pieces)
                    task["phaseLabel"] = f"四向车整箱取货 · {demand_pieces} 件"
            return new_tasks

        for order in orders:
            allocated_lines: list[dict[str, Any]] = []
            runtime_lines: list[dict[str, Any]] = []
            order_task_ids: list[str] = []
            missing_boxes = 0
            missing_pieces = 0
            shuttle_boxes_total = 0
            ctu_boxes_total = 0

            for requested in order.get("lines", []):
                sku_id = str(requested.get("skuId") or "")
                line_id = str(requested.get("id") or "")
                try:
                    case_cnt = max(1, int(case_counts.get(sku_id) or requested.get("caseCnt") or 1))
                except (TypeError, ValueError):
                    case_cnt = 1
                try:
                    quantity = max(1, int(requested.get("quantity", 1)))
                except (TypeError, ValueError):
                    quantity = 1
                qty_mode = "pieces" if str(requested.get("qtyMode") or "").lower() == "pieces" else "boxes"
                # 内部统一按件数：按箱输入时用箱规折算。
                pieces = quantity if qty_mode == "pieces" else quantity * case_cnt
                full_boxes, tail_pieces = divmod(pieces, case_cnt)

                # 1) 整箱优先给四向车。
                shuttle_boxes = 0
                attempts = 0
                max_attempts = full_boxes * 3 + 3
                while shuttle_boxes < full_boxes and attempts < max_attempts:
                    attempts += 1
                    created = allocate_shuttle_box(order, sku_id, line_id, case_cnt, case_cnt)
                    if not created:
                        break
                    tasks.extend(created)
                    pool_tasks.extend(created)
                    picked = [task for task in created if task.get("taskKind") == "shuttle_order_pick"]
                    shuttle_boxes += len(picked)
                    order_task_ids.extend(task["id"] for task in picked)
                shuttle_boxes_total += shuttle_boxes
                line_shuttle_boxes = shuttle_boxes

                # 2) 尾箱与四向车整箱缺口都由 CTU 取整箱；尾箱只拣所需件数。
                ctu_requirements = [case_cnt] * max(0, full_boxes - shuttle_boxes)
                if tail_pieces:
                    ctu_requirements.append(tail_pieces)
                line_ctu_boxes = 0
                line_missing_boxes = 0
                line_missing_pieces = 0
                line_shuttle_degraded = 0
                for need_pieces in ctu_requirements:
                    slot = next(
                        (
                            item
                            for item in ctu_by_sku.get(sku_id, [])
                            if (item["nodeId"], int(item.get("layer", 1))) not in used_slots
                        ),
                        None,
                    )
                    if slot is not None:
                        used_slots.add((slot["nodeId"], int(slot.get("layer", 1))))
                        allocated_lines.append({**dict(slot), "qty": case_cnt, "pickPieces": need_pieces})
                        line_ctu_boxes += 1
                        continue
                    # CTU 无可用整箱：缺口转四向车（整箱出库，只按需求件数记账）。
                    created = allocate_shuttle_box(order, sku_id, line_id, case_cnt, need_pieces)
                    tasks.extend(created)
                    pool_tasks.extend(created)
                    picked = [task for task in created if task.get("taskKind") == "shuttle_order_pick"]
                    if picked:
                        shuttle_boxes += len(picked)
                        line_shuttle_degraded += len(picked)
                        order_task_ids.extend(task["id"] for task in picked)
                        continue
                    # 两侧都不足：记异常缺口。
                    line_missing_boxes += 1
                    line_missing_pieces += need_pieces

                missing_boxes += line_missing_boxes
                missing_pieces += line_missing_pieces
                shuttle_boxes_total += line_shuttle_degraded
                ctu_boxes_total += line_ctu_boxes
                requested_boxes = full_boxes + (1 if tail_pieces else 0)
                allocated_boxes = line_shuttle_boxes + line_shuttle_degraded + line_ctu_boxes
                runtime_lines.append(
                    {
                        **requested,
                        "qtyMode": qty_mode,
                        "caseCnt": case_cnt,
                        "requestedPieces": pieces,
                        "requestedBoxes": requested_boxes,
                        "allocatedPieces": pieces - line_missing_pieces,
                        "allocated": allocated_boxes,
                        "shuttleBoxes": line_shuttle_boxes + line_shuttle_degraded,
                        "ctuBoxes": line_ctu_boxes,
                        "missingPieces": line_missing_pieces,
                        "pickedPieces": 0,
                        "picked": 0,
                    }
                )

            dispatch_mode = str(order.get("dispatchMode") or "balanced")
            max_parallel_ctu = max(1, min(7, int(order.get("maxParallelCtu") or 7), ctu_count))
            task_groups = self._split_ctu_order_lines(allocated_lines, dispatch_mode, max_parallel_ctu)
            for task_index, task_lines in enumerate(task_groups, start=1):
                selected_port, port_cost = self._select_ctu_delivery_port(
                    topology,
                    task_lines,
                    delivery_ports,
                    port_loads,
                )
                delivery = selected_port["cellId"]
                task_id = f"task-{order['id']}-{task_index}"
                order_task_ids.append(task_id)
                task = self._build_task(
                    topology,
                    task_id,
                    "ctu",
                    "direct",
                    task_lines,
                    delivery,
                    order.get("priority") == "urgent",
                    1 if order.get("priority") == "urgent" else 2,
                    order_id=order["id"],
                )
                task["taskKind"] = "ctu_order_pick"
                task["taskIndex"] = task_index
                task["splitStrategy"] = dispatch_mode
                task["sourceGroupLabel"] = self._source_group_label(task_lines)
                task["assignedPortId"] = selected_port["id"]
                task["assignedPortLabel"] = selected_port["label"]
                task["portQueuePosition"] = port_loads.get(delivery, 0) + 1
                task["portSelectionCost"] = port_cost
                task["portSelectionReason"] = "综合取货终点距离与出库口排队时间"
                task["pickPiecesList"] = [int(line.get("pickPieces") or 1) for line in task_lines]
                task["demandPieces"] = sum(task["pickPiecesList"])
                port_loads[delivery] = port_loads.get(delivery, 0) + 1
                tasks.append(task)

            requested_pieces = sum(int(line.get("requestedPieces") or 0) for line in runtime_lines)
            requested_boxes = sum(int(line.get("requestedBoxes") or 0) for line in runtime_lines)
            allocated_pieces = sum(int(line.get("allocatedPieces") or 0) for line in runtime_lines)
            allocated_boxes = shuttle_boxes_total + ctu_boxes_total
            has_missing = missing_boxes > 0
            order_states.append(
                {
                    "id": order["id"],
                    "priority": order.get("priority", "normal"),
                    "dispatchMode": dispatch_mode,
                    "maxParallelCtu": max_parallel_ctu,
                    "targetStation": order.get("targetStation", "出库口"),
                    "createdAt": order.get("createdAt", now_dt().isoformat()),
                    "lines": runtime_lines,
                    "taskIds": order_task_ids,
                    "totalTasks": len(order_task_ids),
                    "completedTasks": 0,
                    "progress": 0,
                    "status": "exception" if has_missing or not order_task_ids else "waiting_dispatch",
                    "statusLabel": "库存不足" if has_missing else "等待调度",
                    "missingQuantity": missing_boxes,
                    "missingPieces": missing_pieces,
                    "startedTick": None,
                    "completedTick": None,
                    "decomposition": {
                        "stage": "exception" if has_missing or not order_task_ids else "tasks_generated",
                        "requestedQuantity": requested_boxes,
                        "allocatedQuantity": allocated_boxes,
                        "missingQuantity": missing_boxes,
                        "requestedPieces": requested_pieces,
                        "allocatedPieces": allocated_pieces,
                        "missingPieces": missing_pieces,
                        "pickedPieces": 0,
                        "shuttleBoxes": shuttle_boxes_total,
                        "ctuBoxes": ctu_boxes_total,
                        "shuttleTaskCount": len(
                            [task_id for task_id in order_task_ids if task_id.startswith("task-dock-")]
                        ),
                        "ctuTaskCount": len(task_groups),
                        "taskCount": len(order_task_ids),
                        "assignedTaskCount": 0,
                        "activeVehicleIds": [],
                        "vehicleCapacity": ROBOT_CARRY_CAPACITY["ctu"],
                        "strategy": dispatch_mode,
                        "maxParallelCtu": max_parallel_ctu,
                    },
                }
            )
        return tasks, order_states

    def _sku_case_counts(self) -> dict[str, int]:
        """商品目录里的 SKU → 每箱件数（caseCnt）。目录缺失时回退 1 件/箱。"""
        catalog = load_catalog()
        counts: dict[str, int] = {}
        for sku in catalog.get("skus", []):
            sku_id = str(sku.get("skuId") or "")
            if not sku_id:
                continue
            try:
                counts[sku_id] = max(1, int(sku.get("caseCnt") or 1))
            except (TypeError, ValueError):
                counts[sku_id] = 1
        return counts

    def _first_delivery_cell(self, topology: dict[str, Any]) -> str:
        """输送线出入口（port 区域）入口格；无 port 区域时返回空串。

        订单任务由 CTU 执行（零散件），优先取架空输送线的 port；
        拓扑只画了地面线时回退第一个 port。
        """
        ground_ports, elevated_ports = self._split_ports_by_conveyor_kind(topology)
        preferred = elevated_ports or ground_ports
        return preferred[0] if preferred else ""

    def _secondary_arm_configs(
        self, topology: dict[str, Any], conveyor_graph: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """侧向出货口理货机械臂：除主交接臂外，每个侧向出货口归最近的机械臂。

        拓扑主干竖段（如 col 60）旁 col+2 的死端帽口就是侧向出货口（如 106:62）；
        新增机械臂站在出货口与放置区之间，把送到帽口的货箱抓进放置区。
        """
        arm_nodes = [node for node in topology.get("nodes", []) if node.get("type") == "picking_arm"]
        if len(arm_nodes) < 2:
            return []
        primary_id = self._primary_arm_node_id(topology, arm_nodes)
        ports = self._side_output_ports(conveyor_graph)
        if not ports:
            return []
        secondary_nodes = [
            node for node in arm_nodes if str(node.get("id")) != primary_id
        ]
        if not secondary_nodes:
            return []
        # 只服务机械臂工作半径内的帽口：远端出库口（如 row 82 一线）不在臂的可达范围。
        work_radius = self._arm_work_radius(topology)
        reachable_ports = [
            port
            for port in ports
            if min(
                abs(port["row"] - int(node.get("row", 0)))
                + abs(port["col"] - int(node.get("col", 0)))
                for node in secondary_nodes
            )
            <= work_radius
        ]
        if not reachable_ports:
            return []
        # 出货口按就近归属：109:63 的臂负责 102/106/110，117:63 的臂负责 114/118。
        assignments: dict[str, list[dict[str, Any]]] = {
            str(node.get("id")): [] for node in secondary_nodes
        }
        for port in reachable_ports:
            owner = min(
                secondary_nodes,
                key=lambda node: (
                    abs(port["row"] - int(node.get("row", 0)))
                    + abs(port["col"] - int(node.get("col", 0))),
                    int(node.get("row", 0)),
                ),
            )
            assignments[str(owner.get("id"))].append(port)
        configs: list[dict[str, Any]] = []
        drop_zone = next(
            (zone for zone in topology.get("zones", []) if zone.get("type") == "drop_zone"),
            None,
        )
        drop_col = (
            int(drop_zone["x"]) + int(drop_zone["width"]) // 2 if drop_zone else 67
        )
        for node in secondary_nodes:
            row = int(node.get("row", 0))
            col = int(node.get("col", 0))
            served = assignments[str(node.get("id"))]
            configs.append(
                {
                    "armId": str(node.get("id")),
                    "nodeId": str(node.get("id")),
                    "row": row,
                    "col": col,
                    "dropCol": drop_col,
                    "ports": [dict(port) for port in served],
                }
            )
        return configs

    def _arm_work_radius(self, topology: dict[str, Any]) -> float:
        """机械臂平面工作半径（格）：取布局计划里的安装参数，缺失时用默认 8.9。"""
        try:
            raw = _load_raw_plan(str(topology.get("id") or ""))
            preview = raw.get("pickingArmPreview") or {}
            installation = preview.get("installation") or {}
            radius = installation.get("workRadius")
            if radius is not None:
                return float(radius)
        except (OSError, ValueError, TypeError):
            pass
        return 8.9

    def _primary_arm_node_id(
        self, topology: dict[str, Any], arm_nodes: list[dict[str, Any]]
    ) -> str:
        """主交接臂：布局计划里 pickingArmPreview 声明的臂；读不到时退回第一个臂节点。"""
        try:
            raw = _load_raw_plan(str(topology.get("id") or ""))
            preview = raw.get("pickingArmPreview") or {}
            arm_id = str(preview.get("armId") or "")
            if arm_id and any(str(node.get("id")) == arm_id for node in arm_nodes):
                return arm_id
            installation = preview.get("installation") or {}
            inst_row = int(installation.get("row", -1))
            inst_col = int(installation.get("col", -1))
            if inst_row >= 0 and inst_col >= 0:
                nearest = min(
                    arm_nodes,
                    key=lambda node: abs(int(node.get("row", 0)) - inst_row)
                    + abs(int(node.get("col", 0)) - inst_col),
                )
                return str(nearest.get("id"))
        except (OSError, ValueError, TypeError):
            pass
        return str(arm_nodes[0].get("id"))

    def _side_output_ports(self, conveyor_graph: dict[str, Any]) -> list[dict[str, Any]]:
        """识别主干竖段上的侧向出货口（hat 口）：next 指回竖段、且自身无上游喂入。"""
        nodes = conveyor_graph.get("nodes") or {}
        fed = {
            str(node.get("next"))
            for node in nodes.values()
            if isinstance(node, dict) and node.get("next")
        }
        ports: list[dict[str, Any]] = []
        for block_id, node in nodes.items():
            if not str(block_id).endswith(":upper") or not isinstance(node, dict):
                continue
            downstream = nodes.get(str(node.get("next") or ""))
            if not isinstance(downstream, dict):
                continue
            row = int(node.get("row", 0))
            col = int(node.get("col", 0))
            if int(downstream.get("row", 0)) != row or int(downstream.get("col", 0)) != col - 2:
                continue
            if block_id in fed:
                continue
            ports.append(
                {
                    "blockId": str(block_id),
                    "cellId": cell_id(row, col),
                    "row": row,
                    "col": col,
                    "level": 2,
                    "beltHeight": 3.0,
                }
            )
        ports.sort(key=lambda port: (port["row"], port["col"]))
        return ports

    def _arm_handoff_target(self, topology: dict[str, Any]) -> dict[str, Any] | None:
        """机械臂交接目标：拓扑里有 picking_handoff 标记 + 布局计划声明了放货点时才启用。

        返回 {handoffCellId, handoffNodeId, dropoffCellId, dropoffLevel, dropoffBeltHeight}；
        任一条件缺失返回 None（四向车保持原有交付行为）。
        """
        handoff = next(
            (node for node in topology.get("nodes", []) if node.get("type") == "picking_handoff"),
            None,
        )
        if handoff is None:
            return None
        try:
            raw = _load_raw_plan(str(topology["id"]))
        except (OSError, ValueError):
            return None
        dropoff = (raw.get("pickingArmPreview") or {}).get("dropoff") or {}
        drop_cell = str(dropoff.get("topologyCellId") or "")
        if not drop_cell:
            return None
        cells_by_id = {
            str(cell["id"])
            for cell in topology.get("cells", [])
            if cell.get("type") == "conveyor"
        }
        if drop_cell not in cells_by_id:
            return None
        return {
            "handoffCellId": cell_id(
                int(handoff["row"]), int(handoff["col"]), handoff.get("floor") or "1F"
            ),
            "handoffNodeId": str(handoff["id"]),
            "dropoffCellId": drop_cell,
            "dropoffLevel": int(dropoff.get("level") or 2),
            "dropoffBeltHeight": float(dropoff.get("beltHeight") or 3.0),
        }

    def _advance_picking_arm(self, runtime: RunRuntime) -> None:
        """机械臂交接：从交接处四向车抓取货箱，放到放货点输送带；完成后四向车才释放。

        单臂逐箱服务，两阶段计时：pick（抓取）→ place（放至 L2 放货点带面）。
        等待中的四向车停在交接处（占用该格），后续车辆在地面线上自然排队。
        """
        arm = runtime.state.get("pickingArm")
        target = runtime.arm_handoff
        if not arm or not target:
            return
        tick = runtime.state["tick"]
        stage = arm.get("stage")
        if stage in {"pick", "place"}:
            remaining = int(arm.get("remainingTicks", 0)) - 1
            arm["remainingTicks"] = max(0, remaining)
            if remaining > 0:
                return
            robot = next(
                (item for item in runtime.state["robots"] if item["id"] == arm.get("robotId")),
                None,
            )
            task = find_task(runtime.state["tasks"], arm.get("taskId"))
            if robot is None or task is None or not (robot.get("carrying") or []):
                # 兜底：车或任务丢失（故障/取消）→ 复位机械臂。
                arm.update({"status": "idle", "stage": None, "remainingTicks": 0, "taskId": None, "robotId": None})
                return
            if stage == "pick":
                arm["stage"] = "place"
                arm["remainingTicks"] = ARM_HANDOFF_PLACE_TICKS
                robot["phaseLabel"] = "机械臂放箱至输送线"
                task["phaseLabel"] = robot["phaseLabel"]
                self._record_event(
                    runtime,
                    tick,
                    "arm_handoff",
                    {"stage": "place", "taskId": task["id"], "robotId": robot["id"], "dropoffCellId": target["dropoffCellId"]},
                )
                return
            # place 完成：货箱落到放货点带面，四向车释放；任务转入独立的
            # 上层输送阶段，只有货箱到达输送线终点后才真正完成。
            task["deliveredGoods"] = [dict(slot) for slot in (robot.get("carrying") or [])]
            task["armPlacedCellId"] = target["dropoffCellId"]
            transport_started = self._start_arm_conveyor_delivery(runtime, task, target)
            self._release_arm_handoff_robot(runtime, robot, task)
            arm.update({"status": "idle", "stage": None, "remainingTicks": 0, "taskId": None, "robotId": None})
            self._record_event(
                runtime,
                tick,
                "arm_handoff",
                {
                    "stage": "placed",
                    "taskId": task["id"],
                    "robotId": robot["id"],
                    "cellId": target["dropoffCellId"],
                    "conveyorTransportStarted": transport_started,
                },
            )
            return
        # 空闲：寻找已停在交接处、进入等待阶段的载货四向车。
        for robot in runtime.state["robots"]:
            if robot["currentCellId"] != target["handoffCellId"] or not (robot.get("carrying") or []):
                continue
            task = find_task(runtime.state["tasks"], robot.get("currentTaskId"))
            if task is None or task.get("phase") != "waiting_arm_handoff":
                continue
            arm.update({
                "status": "busy",
                "stage": "pick",
                "remainingTicks": ARM_HANDOFF_PICK_TICKS,
                "taskId": task["id"],
                "robotId": robot["id"],
            })
            robot["phaseLabel"] = "机械臂抓取中"
            task["phaseLabel"] = robot["phaseLabel"]
            self._record_event(
                runtime,
                tick,
                "arm_handoff",
                {"stage": "pick", "taskId": task["id"], "robotId": robot["id"]},
            )
            return

    def _advance_picking_arms(self, runtime: RunRuntime) -> None:
        """侧向出货口理货机械臂：把停在帽口的货箱搬进放置区（drop_zone）。

        每台臂独立逐箱服务：pick 从出货口帽口抓取 → place 放到放置区地面。
        抓取时把货箱从 armBeltBoxes 摘走（输送带面不再显示），放置完成后
        追加到 armDropBoxes，前端据此在放置区渲染真实落位。
        """
        arm_states = runtime.state.get("pickingArms") or []
        if not arm_states or not runtime.secondary_arms:
            return
        configs = {str(config["armId"]): config for config in runtime.secondary_arms}
        tick = int(runtime.state["tick"])
        boxes = runtime.state.setdefault("armBeltBoxes", [])
        for arm in arm_states:
            config = configs.get(str(arm.get("armId")))
            if config is None:
                continue
            stage = arm.get("stage")
            if stage in {"pick", "place"}:
                remaining = int(arm.get("remainingTicks", 0)) - 1
                arm["remainingTicks"] = max(0, remaining)
                if remaining > 0:
                    continue
                if stage == "pick":
                    arm["stage"] = "place"
                    arm["remainingTicks"] = ARM_DROPZONE_PLACE_TICKS
                    self._record_event(
                        runtime,
                        tick,
                        "arm_handoff",
                        {
                            "stage": "dropzone_place",
                            "armId": arm.get("armId"),
                            "taskId": arm.get("taskId"),
                            "dropCellId": arm.get("dropCellId"),
                        },
                    )
                    continue
                cargo = arm.get("cargo") or {}
                drop_boxes = runtime.state.setdefault("armDropBoxes", [])
                drop_boxes.append({
                    **{
                        key: cargo[key]
                        for key in ("orderId", "skuId", "skuName", "largeCategory", "qty")
                        if cargo.get(key) not in (None, "")
                    },
                    "taskId": cargo.get("taskId") or arm.get("taskId"),
                    "armId": arm.get("armId"),
                    "cellId": arm.get("dropCellId"),
                    "row": int(arm.get("dropRow") or 0),
                    "col": int(arm.get("dropCol") or 0),
                    "placedTick": tick,
                })
                # 放置区货箱只用于可视化；保留最近 60 箱，避免长时间 Run 的
                # 状态帧无限膨胀（前端也只渲染最近若干箱）。
                if len(drop_boxes) > 60:
                    del drop_boxes[: len(drop_boxes) - 60]
                self._record_event(
                    runtime,
                    tick,
                    "arm_handoff",
                    {
                        "stage": "dropzone_placed",
                        "armId": arm.get("armId"),
                        "taskId": arm.get("taskId"),
                        "cellId": arm.get("dropCellId"),
                    },
                )
                arm.update({
                    "status": "idle",
                    "stage": None,
                    "remainingTicks": 0,
                    "taskId": None,
                    "cargoToken": None,
                    "portCellId": None,
                    "dropCellId": None,
                    "cargo": None,
                })
                continue
            # 空闲：寻找停在本臂出货口帽口、尚未被搬走的货箱。
            port_cells = {str(port["cellId"]): port for port in config["ports"]}
            claimed_index = next(
                (
                    index
                    for index, box in enumerate(boxes)
                    if str(box.get("cellId") or "") in port_cells
                ),
                None,
            )
            if claimed_index is None:
                continue
            box = boxes.pop(claimed_index)
            port = port_cells[str(box.get("cellId"))]
            drop_row = int(port["row"])
            arm.update({
                "status": "busy",
                "stage": "pick",
                "remainingTicks": ARM_DROPZONE_PICK_TICKS,
                "taskId": box.get("taskId"),
                "cargoToken": f"{arm.get('armId')}:{box.get('taskId') or 'box'}:{tick}",
                "portCellId": str(box.get("cellId")),
                "dropCellId": cell_id(drop_row, int(config.get("dropCol") or 67)),
                "dropRow": drop_row,
                "dropCol": int(config.get("dropCol") or 67),
                "cargo": dict(box),
            })
            self._record_event(
                runtime,
                tick,
                "arm_handoff",
                {
                    "stage": "dropzone_pick",
                    "armId": arm.get("armId"),
                    "taskId": arm.get("taskId"),
                    "portCellId": arm.get("portCellId"),
                },
            )

    def _start_arm_conveyor_delivery(
        self,
        runtime: RunRuntime,
        task: dict[str, Any],
        target: dict[str, Any],
    ) -> bool:
        """机械臂放箱后，从指定上层模块创建逐块输送任务。"""
        tick = int(runtime.state["tick"])
        route = route_from_cell(
            runtime.conveyor_graph,
            str(target.get("dropoffCellId") or ""),
            "upper",
            ground=False,
        )
        if not route:
            # 路径异常时保留货箱而不是让它消失，并明确把任务标为异常。
            box = dict((task.get("transitGoods") or task.get("deliveredGoods") or task.get("lines") or [{}])[0])
            runtime.state.setdefault("armBeltBoxes", []).append({
                **box,
                "taskId": task.get("id"),
                "cellId": target.get("dropoffCellId"),
                "level": target.get("dropoffLevel", 2),
                "beltHeight": target.get("dropoffBeltHeight", 3.0),
                "placedTick": tick,
            })
            task["status"] = "exception"
            task["phase"] = "arm_conveyor_blocked"
            task["phaseLabel"] = "机械臂放箱后输送路径不可用"
            task["failureReason"] = "arm_dropoff_conveyor_route_missing"
            task["completedTick"] = tick
            task.setdefault("history", []).append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._record_event(
                runtime,
                tick,
                "task_state_changed",
                {"taskId": task["id"], "phase": task["phase"], "status": task["status"]},
            )
            return False

        # 同一订单两侧货物送同一个出货口：四向车整箱沿上层线也截断到订单固定的侧向口。
        if task.get("orderId"):
            port_block = self._output_port_for_order(
                runtime,
                task.get("orderId"),
                self._delivery_output_ports(runtime, route),
            )
            if port_block:
                route = self._route_to_output_port(runtime, route, port_block)
                task["outputPortBlockId"] = port_block

        task["status"] = "assigned"
        task["phase"] = "arm_conveyor_delivery"
        task["phaseLabel"] = f"机械臂放箱后上层输送 1/{len(route)}"
        task["assignedRobotId"] = None
        task["remainingStageTicks"] = max(0, len(route) - 1)
        task["lastProgressTick"] = tick
        task["conveyorTransport"] = {
            "phase": "arm_conveyor_delivery",
            "deck": "upper",
            "route": route,
            "index": 0,
            "currentBlockId": route[0],
            "nextBlockId": route[1] if len(route) > 1 else None,
            "waitingTicks": 0,
            "movedTicks": 0,
            "startedTick": tick,
            # 使用真实秒累计，而不是固定 tick 数；切换 1×/2×/4× 后仍与
            # CTU 货箱保持相同的屏幕移动速度。
            "travelProgressSeconds": 0.0,
        }
        task.setdefault("history", []).append({"tick": tick, "phase": task["phase"], "status": task["status"]})
        self._record_event(
            runtime,
            tick,
            "conveyor_position",
            {"taskId": task["id"], "deck": "upper", "blockId": route[0], "index": 0},
        )
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {"taskId": task["id"], "phase": task["phase"], "status": task["status"], "routeLength": len(route)},
        )
        return True

    def _release_arm_handoff_robot(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
    ) -> None:
        """机械臂接管货箱后释放四向车，并立即规划撤离交接位的返程。"""
        robot["currentTaskId"] = None
        robot["handlingAction"] = None
        robot["path"] = [robot["currentCellId"]]
        robot["pathLayers"] = [robot.get("currentLayer", 1)]
        robot["load"] = 0
        robot["carrying"] = []
        robot["waitTicks"] = 0
        task["assignedRobotId"] = None

        # 交接位是单容量资源。若卸箱后直接改成 idle，车辆会永久占住
        # picking-handoff，后续车辆只能停在前一格等待。无论货箱输送任务
        # 是否已经完成，空车都要独立返回自己的待命位。
        home_cell = robot.get("homeCellId")
        can_return_home = bool(
            home_cell
            and home_cell != robot["currentCellId"]
            and self._robot_path_to(
                runtime,
                robot,
                str(home_cell),
                int(robot.get("currentLayer", 1)),
            )
            and len(robot.get("path") or []) > 1
        )
        evacuated_to_last_cell = False
        if not can_return_home:
            # 如果拓扑暂时无法规划到待命位，至少沿刚才实际驶入的路段退出
            # 单容量交接格，不能让异常返程继续堵住所有后续车辆。
            last_cell = robot.get("lastCellId")
            occupied = {
                item.get("currentCellId")
                for item in runtime.state.get("robots", [])
                if item.get("id") != robot.get("id")
            }
            if last_cell and last_cell != robot["currentCellId"] and last_cell not in occupied:
                robot["path"] = [robot["currentCellId"], last_cell]
                robot["pathLayers"] = [int(robot.get("currentLayer", 1))] * 2
                robot["targetCellId"] = last_cell
                robot["targetLayer"] = int(robot.get("currentLayer", 1))
                evacuated_to_last_cell = True

        if can_return_home or evacuated_to_last_cell:
            robot["pathStages"] = ["return_to_standby"] * len(robot["path"])
            robot["returningToStandby"] = True
            robot["status"] = "returning"
            robot["phaseLabel"] = "交接完成 / 返回待命位" if can_return_home else "交接完成 / 撤离交接位"
            self._record_event(
                runtime,
                int(runtime.state["tick"]),
                "arm_handoff_robot_released",
                {
                    "robotId": robot["id"],
                    "handoffCellId": robot["currentCellId"],
                    "standbyCellId": home_cell,
                    "targetCellId": robot.get("targetCellId"),
                    "returnHome": can_return_home,
                },
            )
            return

        # 异常拓扑下保留明确状态，避免伪装成已正常撤离；正常仓库拓扑
        # 的四向车待命位均可达，因此不会走到此分支。
        robot["returningToStandby"] = False
        robot["status"] = "exception"
        robot["phaseLabel"] = "交接完成 / 无法撤离交接位"
        self._emit_alert(
            runtime,
            int(runtime.state["tick"]),
            category=f"arm_handoff_exit_unreachable:{robot['id']}",
            level="error",
            message=f"四向车 {robot['id']} 完成机械臂交接后无法返回待命位。",
            details={
                "robotId": robot["id"],
                "handoffCellId": robot["currentCellId"],
                "standbyCellId": home_cell,
            },
            suggestion="检查机械臂交接位到四向车待命位之间的轨道连通性。",
        )

    def _advance_detached_conveyor_tasks(self, runtime: RunRuntime) -> None:
        """推进已脱离机器人的机械臂放箱输送任务（含拣货分裂的 fwd/回库子任务）。"""
        tick = int(runtime.state["tick"])
        for task in runtime.state.get("tasks", []):
            if task.get("status") in TERMINAL_TASK_STATUSES or task.get("phase") != "arm_conveyor_delivery":
                continue
            transport = task.get("conveyorTransport")
            if not isinstance(transport, dict):
                # 占块门控上线：等路线首块空闲（前箱已前进一格）才出现，避免源头重叠。
                if task.get("pendingTransport") is not None:
                    self._activate_child_transport(runtime, task)
                continue
            if int(transport.get("startedTick", -1)) >= tick:
                continue
            if self._advance_conveyor_transport(runtime, None, task):
                self._complete_detached_conveyor_task(runtime, task)

    def _complete_detached_conveyor_task(self, runtime: RunRuntime, task: dict[str, Any]) -> None:
        """货箱到达机械臂输送路线终点后处理：回库子任务转等待 CTU 取回，其余完成任务。"""
        tick = int(runtime.state["tick"])
        transport = task.get("conveyorTransport") or {}
        final_block = transport.get("currentBlockId")
        # ===== 回库子任务：到达回库口后不停留完成，转为等待 CTU 取回 =====
        if task.get("taskKind") == "picking_return":
            end_block = str(final_block or "").removesuffix(":upper")
            task["returnEndBlockId"] = end_block
            task["conveyorTransport"] = None
            task["phase"] = "pickup_from_port"
            task["phaseLabel"] = self._phase_label("pickup_from_port")
            task["status"] = "assigned"
            task["remainingStageTicks"] = 0
            # 回库口停靠 transport：货箱视觉上停在回库口块等待 CTU 取回。
            self._prepare_conveyor_transport(runtime, task, "pickup_from_port")
            task.setdefault("history", []).append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._record_event(
                runtime,
                tick,
                "task_state_changed",
                {"taskId": task["id"], "phase": task["phase"], "status": task["status"]},
            )
            return
        final_node = runtime.conveyor_graph.get("nodes", {}).get(final_block, {})
        final_cell = cell_id(
            int(final_node.get("row", 0)),
            int(final_node.get("col", 0)),
            str(final_node.get("floor") or "1F"),
        )
        box = dict((task.get("transitGoods") or task.get("deliveredGoods") or task.get("lines") or [{}])[0])
        runtime.state.setdefault("armBeltBoxes", []).append({
            **box,
            "taskId": task.get("id"),
            "cellId": final_cell,
            "level": 2,
            "beltHeight": 3.0,
            "placedTick": tick,
        })
        task["armConveyorCompleted"] = True
        task["armConveyorEndCellId"] = final_cell
        task["conveyorTransport"] = None
        task["status"] = "completed"
        task["phase"] = "completed"
        task["phaseLabel"] = self._phase_label("completed")
        task["completedTick"] = tick
        task["remainingStageTicks"] = 0
        # 子任务（有 parentId）不单独算 completedTasks / throughput
        if not task.get("parentId"):
            runtime.metrics["completedTasks"] += 1
            runtime.metrics["throughput"] += 1
            if task.get("urgent"):
                runtime.metrics["urgentOrdersHandled"] += 1
        task.setdefault("history", []).append({"tick": tick, "phase": task["phase"], "status": task["status"]})
        self._record_event(
            runtime,
            tick,
            "arm_handoff",
            {"stage": "conveyor_completed", "taskId": task["id"], "cellId": final_cell},
        )
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {"taskId": task["id"], "phase": task["phase"], "status": task["status"]},
        )
        self._maybe_loop_dock_task(runtime, task)

    def _split_ports_by_conveyor_kind(self, topology: dict[str, Any]) -> tuple[list[str], list[str]]:
        """按最近输送块的类型把 port 分成（地面线, 架空线）两组。

        地面输送线（conveyorGround）承接四向车出来的整箱货，架空输送线承接 CTU
        的零散件。port 归属取其入口格曼哈顿 ≤5 格内最近输送块；两类都有时各自
        独立派单，缺某一类时由调用方回退全部 port（兼容只画了一种线的拓扑）。
        """
        ground_ports: list[str] = []
        elevated_ports: list[str] = []
        for zone in topology.get("zones", []):
            if zone.get("type") != "port":
                continue
            entry = port_entry_cell(topology, zone)
            try:
                floor, row, col = parse_cell_id(entry)
            except (TypeError, ValueError):
                elevated_ports.append(entry)
                continue
            best: tuple[int, bool] | None = None
            for cell in topology.get("cells", []):
                if cell.get("type") != "conveyor" or (cell.get("floor") or "1F") != floor:
                    continue
                distance = abs(int(cell["row"]) - row) + abs(int(cell["col"]) - col)
                if distance > 5:
                    continue
                if best is None or distance < best[0]:
                    best = (distance, bool(cell.get("conveyorGround")))
            if best and best[1]:
                ground_ports.append(entry)
            else:
                elevated_ports.append(entry)
        return ground_ports, elevated_ports

    def _ctu_delivery_ports(self, topology: dict[str, Any]) -> list[dict[str, str]]:
        """CTU 可用出库口：优先架空输送线，没有时回退到全部 port。"""
        ground_ports, elevated_ports = self._split_ports_by_conveyor_kind(topology)
        preferred = set(elevated_ports or ground_ports)
        ports: list[dict[str, str]] = []
        for zone in topology.get("zones", []):
            if zone.get("type") != "port":
                continue
            entry = port_entry_cell(topology, zone)
            if preferred and entry not in preferred:
                continue
            ports.append(
                {
                    "id": str(zone.get("id") or entry),
                    "label": str(zone.get("label") or zone.get("id") or entry),
                    "cellId": entry,
                }
            )
        return ports

    def _select_ctu_delivery_port(
        self,
        topology: dict[str, Any],
        task_lines: list[dict[str, Any]],
        ports: list[dict[str, str]],
        port_loads: dict[str, int],
    ) -> tuple[dict[str, str], int]:
        """为一个 CTU 工作包选择预计最早完成的出库口（排队长度 + 返程成本）。"""
        source = str(task_lines[-1].get("cellId") or task_lines[0].get("cellId") or "")
        candidates: list[tuple[int, str, dict[str, str]]] = []
        for port in ports:
            route = shortest_path(
                topology,
                source,
                port["cellId"],
                "astar",
                avoid_storage=True,
            )
            # 任务实际取货时停在货位相邻通道；某些旧数据只保留货位格坐标，
            # 在"禁止穿越库位"的估算中可能无法从该格起步，选口阶段回退普通可达距离。
            if not route:
                route = shortest_path(topology, source, port["cellId"], "astar")
            queue_cost = port_loads.get(port["cellId"], 0) * PORT_QUEUE_SERVICE_TICKS
            if route:
                score = max(0, len(route) - 1) + queue_cost
            else:
                # 不可达候选加大惩罚，只有所有口都无法精确寻路时才回退。
                _, source_row, source_col = parse_cell_id(source)
                _, port_row, port_col = parse_cell_id(port["cellId"])
                score = 1000 + abs(source_row - port_row) + abs(source_col - port_col) + queue_cost
            candidates.append((score, port["id"], port))
        if not candidates:
            raise ValueError("订单取货位到所有出库口均不可达")
        score, _, port = min(candidates, key=lambda item: (item[0], item[1]))
        return port, score

    def _split_ctu_order_lines(
        self,
        allocated_lines: list[dict[str, Any]],
        dispatch_mode: str,
        max_parallel_ctu: int,
    ) -> list[list[dict[str, Any]]]:
        """按货位邻近性和订单策略，把库存箱拆成容量不超过 5 的 CTU 工作包。"""
        if not allocated_lines:
            return []
        capacity = ROBOT_CARRY_CAPACITY["ctu"]
        line_count = len(allocated_lines)
        minimum_tasks = (line_count + capacity - 1) // capacity
        parallel_target = min(line_count, max(1, max_parallel_ctu))
        if dispatch_mode == "compact":
            task_count = minimum_tasks
        elif dispatch_mode == "parallel":
            task_count = max(minimum_tasks, parallel_target)
        else:
            balanced_target = (minimum_tasks + parallel_target + 1) // 2
            task_count = max(minimum_tasks, balanced_target)
        task_count = min(line_count, task_count)

        def position_key(line: dict[str, Any]) -> tuple[str, int, int, int]:
            try:
                floor, row, col = parse_cell_id(str(line.get("cellId") or ""))
            except (TypeError, ValueError):
                return ("", 999999, 999999, int(line.get("layer", 1)))
            return (floor, row, col, int(line.get("layer", 1)))

        ordered = sorted(allocated_lines, key=position_key)
        if dispatch_mode == "compact":
            return [ordered[index : index + capacity] for index in range(0, line_count, capacity)]
        base_size, larger_groups = divmod(line_count, task_count)
        groups: list[list[dict[str, Any]]] = []
        cursor = 0
        for index in range(task_count):
            group_size = base_size + (1 if index < larger_groups else 0)
            groups.append(ordered[cursor : cursor + group_size])
            cursor += group_size
        return groups

    def _source_group_label(self, lines: list[dict[str, Any]]) -> str:
        if not lines:
            return "未匹配货位"
        positions: list[tuple[int, int]] = []
        for line in lines:
            try:
                _, row, col = parse_cell_id(str(line.get("cellId") or ""))
                positions.append((row, col))
            except (TypeError, ValueError):
                continue
        if not positions:
            return f"{len(lines)} 箱货物"
        row_min = min(row for row, _ in positions)
        row_max = max(row for row, _ in positions)
        col_min = min(col for _, col in positions)
        col_max = max(col for _, col in positions)
        location = (
            f"R{row_min}C{col_min}"
            if (row_min, col_min) == (row_max, col_max)
            else f"R{row_min}-{row_max} / C{col_min}-{col_max}"
        )
        return f"{location} · {len(lines)} 箱"

    def _seed_tasks(
        self,
        topology: dict[str, Any],
        config: dict[str, Any],
        inventory: list[dict[str, Any]] | None = None,
        shuttle_docks: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if inventory is None:
            inventory = build_inventory(topology)
        order_gen = config.get("orderGenerator") or {}
        rng = random.Random(config.get("seed", 0))

        # 只从「长边有可通行空格」的库位生成取货任务：
        # 2 列宽货架的内侧列没有可进入的长边，生成任务会永远无法完成，导致机器人空等。
        serviceable = serviceable_storage_cells(topology)
        ctu_sources = [
            node["id"]
            for node in ctu_storage_nodes(topology)
            if node_position(node) in serviceable
        ]
        ctu_source_ids = set(ctu_sources)
        shuttle_sources = shuttle_servable_slots(topology)

        # 一个库位槽（nodeId + layer）只有一箱货：同一槽派两单会让两台车抢同一格，
        # 后到的一台永远进不去，形成对头死锁。
        slot_pool = [slot for slot in inventory if slot.get("nodeId") in ctu_source_ids]
        rng.shuffle(slot_pool)
        used_slots: set[tuple[str, int]] = set()

        def take_lines(count: int) -> list[dict[str, Any]]:
            lines: list[dict[str, Any]] = []
            while slot_pool and len(lines) < count:
                slot = slot_pool.pop()
                key = (slot["nodeId"], int(slot["layer"]))
                if key in used_slots:
                    continue
                used_slots.add(key)
                line: dict[str, Any] = {"nodeId": slot["nodeId"], "layer": key[1]}
                for field in SKU_FIELDS:
                    if slot.get(field) not in (None, ""):
                        line[field] = slot[field]
                lines.append(line)
            if lines or not ctu_sources:
                return lines
            # 无库存（商品目录缺失）时按库位×层随机回退，同样排除已派出的槽。
            fallback = [
                (node_id, layer)
                for node_id in ctu_sources
                for layer in range(1, CTU_RACK_LAYERS + 1)
                if (node_id, layer) not in used_slots
            ]
            rng.shuffle(fallback)
            for node_id, layer in fallback[:count]:
                used_slots.add((node_id, layer))
                lines.append({"nodeId": node_id, "layer": layer})
            return lines

        port_zones = [zone for zone in topology["zones"] if zone.get("type") == "port"]
        delivery_cells = [port_entry_cell(topology, zone) for zone in port_zones]
        if not delivery_cells:
            # 没有输送线出入口就无法交付，返回空任务（前端会提示用户先画 port 区域）。
            return []

        # 两类输送线分流：CTU（零散件）走架空线 port；四向车（整箱）直接驶上地面
        # 输送线交付，目标取地面线的 2×2 块锚点——port 是 CTU 定位概念，四向车不需要。
        # 无地面线时回退地面 port / 全部 port，保持旧行为。
        ground_ports, elevated_ports = self._split_ports_by_conveyor_kind(topology)
        ctu_delivery_cells = elevated_ports or delivery_cells
        ground_anchor_cells: set[str] = set()
        for cell in topology.get("cells", []):
            if cell.get("type") != "conveyor" or not cell.get("conveyorGround"):
                continue
            anchor = cell.get("conveyorAnchor") or {"row": cell["row"], "col": cell["col"]}
            ground_anchor_cells.add(
                cell_id(int(anchor["row"]), int(anchor["col"]), cell.get("floor") or "1F")
            )
        if ground_anchor_cells:
            shuttle_delivery_cells = sorted(ground_anchor_cells)
        else:
            shuttle_delivery_cells = ground_ports or delivery_cells

        max_orders = max(1, int(order_gen.get("maxOrders", 8)))
        lines_min = max(1, int(order_gen.get("linesPerOrderMin", 1)))
        lines_max = max(lines_min, int(order_gen.get("linesPerOrderMax", lines_min)))
        # 单趟最多装满一车；订单行数暂不超载箱容量（多趟回程留待后续）。
        lines_max = min(lines_max, ROBOT_CARRY_CAPACITY["ctu"])

        tasks: list[dict[str, Any]] = []
        for index in range(max_orders):
            delivery = ctu_delivery_cells[index % len(ctu_delivery_cells)]
            line_count = rng.randint(lines_min, lines_max)
            lines = take_lines(line_count)
            if not lines:
                break
            tasks.append(self._build_task(topology, f"task-ctu-{index + 1}", "ctu", "direct", lines, delivery, False, 2))

        # 无订单启动时只保留 CTU 演示任务；四向车只在订单拆出整箱任务时工作。
        return tasks

    @staticmethod
    def _shuttle_dock_states(topology: dict[str, Any]) -> list[dict[str, Any]]:
        """从拓扑节点构建出货位运行时设置（含库位轮转游标）。"""
        return [
            {
                "dockId": node["id"],
                "label": node.get("label") or node["id"],
                "row": int(node["row"]),
                "col": int(node["col"]),
                "floor": node.get("floor") or "1F",
                "skuId": node.get("skuId"),
                "skuName": node.get("skuName"),
                "largeCategory": node.get("largeCategory"),
                "quantity": max(1, int(node.get("quantity") or 1)),
                "priority": "normal",
                "vehicleId": None,
                "loop": False,
                "enabled": True,
                "cursor": 0,
            }
            for node in topology.get("nodes", [])
            if node.get("type") == "shuttle_dock"
        ]

    def _seed_shuttle_tasks(
        self,
        topology: dict[str, Any],
        shuttle_docks: list[dict[str, Any]] | None = None,
        inventory: list[dict[str, Any]] | None = None,
        batch: dict[str, Any] | None = None,
        existing_tasks: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """四向车出库搬运任务：库位 → 驶出车库 → 地面输送线交付。

        独立于订单流程（不挂订单），订单驱动 / 自动派单两种模式都会追加这组任务。
        运行时出货位设置（shuttle_docks 状态）优先：按出货位生成搬运任务（每箱一趟，
        目标箱被外层箱挡住时先生成倒库任务）；未传状态时回退读取拓扑上的出货位节点；
        都没有时自动派 6 个任务（随机库位）。
        取货源只使用「可服务货位」（邻接可达巷道），深藏货架内部的货位四向车物理上到不了。
        `batch` / `existing_tasks` 可由订单拆解共享，避免订单任务与自动任务争抢同一箱。
        """
        shuttle_sources = shuttle_servable_slots(topology)
        if not shuttle_sources:
            return []
        if shuttle_docks is None:
            shuttle_docks = self._shuttle_dock_states(topology)
        docks = [
            dock for dock in shuttle_docks
            if dock.get("enabled", True)
        ]
        if docks:
            tasks: list[dict[str, Any]] = []
            shared_batch: dict[str, Any] = batch if batch is not None else {"cursor": 0}
            for dock in docks:
                for _ in range(int(dock.get("quantity") or 1)):
                    tasks.extend(
                        self._build_dock_tasks(
                            topology, inventory or [], dock, shuttle_sources,
                            0, shared_batch, list(existing_tasks or []) + tasks,
                        )
                    )
            return tasks
        delivery_cells = [
            port_entry_cell(topology, zone)
            for zone in topology.get("zones", [])
            if zone.get("type") == "port"
        ]
        if not delivery_cells:
            return []
        ground_ports, elevated_ports = self._split_ports_by_conveyor_kind(topology)
        ground_anchor_cells: set[str] = set()
        for cell in topology.get("cells", []):
            if cell.get("type") != "conveyor" or not cell.get("conveyorGround"):
                continue
            anchor = cell.get("conveyorAnchor") or {"row": cell["row"], "col": cell["col"]}
            ground_anchor_cells.add(
                cell_id(int(anchor["row"]), int(anchor["col"]), cell.get("floor") or "1F")
            )
        # 四向车交付目标：优先地面线 2×2 块锚点（直接驶上带面交付，port 是 CTU 概念）；
        # 无地面线时回退地面 port / 全部 port，保持旧行为。
        shuttle_delivery_cells: list[str] = (
            sorted(ground_anchor_cells) if ground_anchor_cells else (ground_ports or delivery_cells)
        )

        tasks = []
        for index in range(6):
            cell, layer = shuttle_sources[index % len(shuttle_sources)]
            delivery = shuttle_delivery_cells[index % len(shuttle_delivery_cells)]
            tasks.append(
                self._build_task(
                    topology,
                    f"task-shuttle-{index + 1}",
                    "shuttle",
                    "cross_zone",
                    [{"cellId": cell, "layer": layer}],
                    delivery,
                    False,
                    2,
                )
            )
        return tasks

    def _build_dock_tasks(
        self,
        topology: dict[str, Any],
        inventory: list[dict[str, Any]],
        dock: dict[str, Any],
        shuttle_sources: list[tuple[str, int]],
        created_tick: int,
        batch: dict[str, Any],
        existing_tasks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """按出货位生成一趟取货任务；目标箱被外层箱挡住时先生成倒库任务。

        货道模型：每条货道（cellId+layer）纵深最多 SHUTTLE_LANE_DEPTH 箱，depth=1 外层
        靠巷道面；取深处箱前必须先把前方箱移走。
        - dock.skuId 有值：取该 SKU 的最浅箱；前方未认领的箱逐个倒库到空货道
          （blockedBy 依赖），前方箱已被其它任务认领则直接挂依赖等它完成。
        - dock.skuId 为空：轮转取下一货道的最浅未认领箱（同货道更深箱自动串行）。
        - 无匹配 SKU / 无空货道：优雅降级为取可及的最浅未认领箱。
        batch 为跨出货位共享的批处理状态（cursor + 箱/货道认领），调用方持久化游标。
        返回 [倒库任务..., 取货任务]（可能为空）。
        """
        created_tick = int(created_tick)
        existing = existing_tasks or []
        claimed_boxes: dict[tuple[str, int, int], str] = dict(batch.get("claimed_boxes") or {})
        claimed_relocate_lanes: set[str] = set(batch.get("claimed_relocate_lanes") or [])
        # 在途任务占位：取货任务认领其目标箱；倒库任务认领其目标货道
        for task in existing:
            if not task.get("dockId"):
                continue
            if task.get("taskKind") == "relocate":
                if task["status"] in {"pending", "assigned"}:
                    claimed_relocate_lanes.add(str(task.get("deliveryCellId")))
            elif task["status"] in {"pending", "assigned"}:
                line = (task.get("lines") or [{}])[0]
                if line.get("depth") is not None:
                    claimed_boxes[(str(line["cellId"]), int(line.get("layer", 1)), int(line["depth"]))] = task["id"]

        def box_key(box: dict[str, Any]) -> tuple[str, int, int]:
            return (str(box["cellId"]), int(box.get("layer", 1)), int(box.get("depth", 1)))

        # 货道分组：inventory 里带 depth 的四向车库箱
        lane_boxes: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for item in inventory:
            if item.get("depth") is None or not item.get("cellId"):
                continue
            lane_boxes.setdefault((str(item["cellId"]), int(item.get("layer", 1))), []).append(item)
        for boxes in lane_boxes.values():
            boxes.sort(key=lambda box: int(box.get("depth", 1)))

        sku = str(dock.get("skuId") or "").strip()
        pick_box: dict[str, Any] | None = None
        relocate_wanted: list[dict[str, Any]] = []
        depends: list[str] = []
        if sku:
            candidates = [box for boxes in lane_boxes.values() for box in boxes if box_key(box) not in claimed_boxes and box.get("skuId") == sku]
            if candidates:
                pick_box = min(candidates, key=lambda box: int(box.get("depth", 1)))
                siblings = lane_boxes.get((str(pick_box["cellId"]), int(pick_box.get("layer", 1))), [])
                for item in siblings:
                    if box_key(item) == box_key(pick_box):
                        break
                    dep_id = claimed_boxes.get(box_key(item))
                    if dep_id:
                        depends.append(dep_id)  # 前方箱正被其它任务取走：等它完成即可
                    else:
                        relocate_wanted.append(item)
        if pick_box is None:
            # 订单驱动任务要求严格匹配 SKU：没货就返回空，由调用方降级到 CTU，
            # 不能拿其它 SKU 的箱子顶上。
            if sku and dock.get("requireSku"):
                batch["claimed_boxes"] = claimed_boxes
                batch["claimed_relocate_lanes"] = claimed_relocate_lanes
                return []
            # 无 SKU / SKU 无匹配：轮转取第一个有箱且未被占用的货道的最浅箱
            for offset in range(len(shuttle_sources)):
                idx = (int(batch.get("cursor") or 0) + offset) % len(shuttle_sources)
                cell, layer = shuttle_sources[idx]
                unclaimed = [b for b in lane_boxes.get((cell, layer), []) if box_key(b) not in claimed_boxes]
                if unclaimed:
                    pick_box = unclaimed[0]
                    for item in lane_boxes.get((cell, layer), []):
                        if box_key(item) == box_key(pick_box):
                            break
                        dep_id = claimed_boxes.get(box_key(item))
                        if dep_id:
                            depends.append(dep_id)
                    batch["cursor"] = idx + 1
                    break
            if pick_box is None:
                batch["claimed_boxes"] = claimed_boxes
                batch["claimed_relocate_lanes"] = claimed_relocate_lanes
                return []

        tasks: list[dict[str, Any]] = []
        if relocate_wanted:
            for blocker in relocate_wanted:
                dest_cell = next(
                    (
                        cell
                        for cell, layer in shuttle_sources
                        if cell not in claimed_relocate_lanes and not lane_boxes.get((cell, layer))
                    ),
                    None,
                )
                if dest_cell is None:
                    # 没有空货道承接倒库：放弃深处目标，降级取该货道最浅未认领箱
                    pick_box = next(
                        (
                            b
                            for b in lane_boxes.get((str(pick_box["cellId"]), int(pick_box.get("layer", 1))), [])
                            if box_key(b) not in claimed_boxes
                            and (not dock.get("requireSku") or b.get("skuId") == sku)
                        ),
                        None,
                    )
                    relocate_wanted = []
                    tasks = []
                    depends = []
                    break
                claimed_relocate_lanes.add(dest_cell)
                dock["cursor"] = int(dock.get("cursor") or 0) + 1
                line = self._lane_line(blocker)
                task = self._build_task(
                    topology,
                    f"task-dock-{dock['dockId']}-{dock['cursor']}",
                    "shuttle",
                    "cross_zone",
                    [line],
                    dest_cell,
                    False,
                    2,
                    created_tick=created_tick,
                )
                task["dockId"] = dock["dockId"]
                task["taskKind"] = "relocate"
                task["sourceDepth"] = int(blocker.get("depth", 1))
                task["relocateDepth"] = 1
                task["targetLayer"] = int(next(layer for cell, layer in shuttle_sources if cell == dest_cell))
                task["phaseLabel"] = "倒库：为深处货物让位"
                if dock.get("vehicleId"):
                    task["requestedVehicleId"] = dock["vehicleId"]
                tasks.append(task)
                depends.append(task["id"])
            if pick_box is None:
                batch["claimed_boxes"] = claimed_boxes
                batch["claimed_relocate_lanes"] = claimed_relocate_lanes
                return tasks

        if pick_box is not None:
            claimed_boxes[box_key(pick_box)] = f"task-dock-{dock['dockId']}-{int(dock.get('cursor') or 0) + 1}"
            dock["cursor"] = int(dock.get("cursor") or 0) + 1
            line = self._lane_line(pick_box)
            # 机械臂交接：拓扑声明了 picking_handoff + 放货点时，四向车不再直接在
            # 出货位上带，而是沿地面线把货箱运到交接处，等待机械臂抓取放带。
            arm_target = self._arm_handoff_target(topology)
            delivery_cell = (
                arm_target["handoffCellId"]
                if arm_target
                else cell_id(int(dock["row"]), int(dock["col"]), dock.get("floor") or "1F")
            )
            task = self._build_task(
                topology,
                f"task-dock-{dock['dockId']}-{dock['cursor']}",
                "shuttle",
                "cross_zone",
                [line],
                delivery_cell,
                dock.get("priority") == "urgent",
                1 if dock.get("priority") == "urgent" else 2,
                created_tick=created_tick,
            )
            task["dockId"] = dock["dockId"]
            if arm_target:
                task["armHandoff"] = True
            if depends:
                task["blockedBy"] = depends
                task["phaseLabel"] = "等待倒库让位"
            if dock.get("vehicleId"):
                task["requestedVehicleId"] = dock["vehicleId"]
            tasks.append(task)

        batch["claimed_boxes"] = claimed_boxes
        batch["claimed_relocate_lanes"] = claimed_relocate_lanes
        return tasks

    @staticmethod
    def _lane_line(box: dict[str, Any]) -> dict[str, Any]:
        """货道箱 → 任务行（带实际 SKU 与深度）。"""
        line: dict[str, Any] = {"cellId": str(box["cellId"]), "layer": int(box.get("layer", 1))}
        if box.get("depth") is not None:
            line["depth"] = int(box["depth"])
        for field in SKU_FIELDS:
            if box.get(field) not in (None, ""):
                line[field] = box[field]
        return line

    def update_shuttle_dock(self, run_id: str, dock_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """运行时调整出货位设置（取什么货物/取多少/优先级/指定车辆/循环/启停）。

        仅重排该出货位「未分配」的任务；执行中与已完成的任务保持不变。
        """
        runtime = self._require_runtime(run_id)
        state = runtime.state
        dock = next(
            (item for item in state.get("shuttleDocks", []) if item["dockId"] == dock_id),
            None,
        )
        if dock is None:
            # 出货位不在 Run 状态里时自动补建（无需重启 Run）：
            # 先查启动时的拓扑快照，再查最新保存的拓扑（Run 启动后新加的出货位）。
            dock = next(
                (
                    item for item in self._shuttle_dock_states(runtime.topology)
                    if item["dockId"] == dock_id
                ),
                None,
            )
            if dock is None:
                latest = self.repo.get_topology(runtime.topology["id"])
                dock = next(
                    (
                        item for item in self._shuttle_dock_states((latest or {}).get("payload") or {})
                        if item["dockId"] == dock_id
                    ),
                    None,
                )
            if dock is None:
                raise KeyError(dock_id)
            state.setdefault("shuttleDocks", []).append(dock)
        if "priority" in patch and patch["priority"] not in {None, "normal", "urgent"}:
            raise ValueError(f"不支持的出货位优先级: {patch['priority']}")
        vehicle_id = patch.get("vehicleId")
        if vehicle_id:
            robot = next(
                (
                    item for item in state.get("robots", [])
                    if item["id"] == vehicle_id and item.get("type") == "shuttle"
                ),
                None,
            )
            if robot is None:
                raise ValueError(f"指定车辆不存在或不是四向车: {vehicle_id}")

        for field in ("skuId", "skuName", "largeCategory"):
            if field in patch:
                dock[field] = (str(patch[field]).strip() or None) if patch[field] else None
        if "quantity" in patch and patch["quantity"] is not None:
            dock["quantity"] = max(1, int(patch["quantity"]))
        if "priority" in patch:
            dock["priority"] = patch["priority"] or "normal"
        if "vehicleId" in patch:
            dock["vehicleId"] = (str(patch["vehicleId"]).strip() or None) if patch["vehicleId"] else None
        for field in ("loop", "enabled"):
            if field in patch:
                dock[field] = bool(patch[field])

        tick = int(state["tick"])
        tasks = state["tasks"]
        # 只移除「未分配」的任务重新生成；执行中 / 已完成保持不变。
        tasks[:] = [
            task for task in tasks
            if not (task.get("dockId") == dock_id and task["status"] == "pending")
        ]
        if dock.get("enabled", True):
            shuttle_sources = shuttle_servable_slots(runtime.topology)
            if shuttle_sources:
                batch: dict[str, Any] = {"cursor": int(state.get("shuttleSlotCursor") or 0)}
                for _ in range(int(dock.get("quantity") or 1)):
                    tasks.extend(
                        self._build_dock_tasks(
                            runtime.topology, state.get("inventory", []), dock, shuttle_sources,
                            tick, batch, tasks,
                        )
                    )
                state["shuttleSlotCursor"] = int(batch.get("cursor") or 0)
        dock["regeneratedTick"] = tick
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {"dockId": dock_id, "settings": {key: dock.get(key) for key in ("skuId", "skuName", "quantity", "priority", "vehicleId", "loop", "enabled")}},
        )
        self._refresh_metrics(runtime)
        self._persist(runtime)
        return {"runId": run_id, "dockId": dock_id, **{key: dock.get(key) for key in ("skuId", "skuName", "largeCategory", "quantity", "priority", "vehicleId", "loop", "enabled")}}

    def _maybe_loop_dock_task(self, runtime: RunRuntime, task: dict[str, Any]) -> None:
        """循环搬运：出货位开启 loop 时，任务完成后自动补一趟（队列保持 quantity 上限）。"""
        dock_id = task.get("dockId")
        if not dock_id:
            return
        dock = next(
            (item for item in runtime.state.get("shuttleDocks", []) if item["dockId"] == dock_id),
            None,
        )
        if not dock or not dock.get("loop") or not dock.get("enabled", True):
            return
        outstanding = sum(
            1
            for other in runtime.state.get("tasks", [])
            if other.get("dockId") == dock_id and other["status"] in {"pending", "assigned"}
        )
        if outstanding >= int(dock.get("quantity") or 1):
            return
        shuttle_sources = shuttle_servable_slots(runtime.topology)
        if not shuttle_sources:
            return
        batch: dict[str, Any] = {"cursor": int(runtime.state.get("shuttleSlotCursor") or 0)}
        new_tasks = self._build_dock_tasks(
            runtime.topology,
            runtime.state.get("inventory", []),
            dock,
            shuttle_sources,
            int(runtime.state["tick"]),
            batch,
            runtime.state.get("tasks", []),
        )
        runtime.state["shuttleSlotCursor"] = int(batch.get("cursor") or 0)
        runtime.state["tasks"].extend(new_tasks)

    def _build_task(
        self,
        topology: dict[str, Any],
        task_id: str,
        robot_type: str,
        mode: str,
        lines: list[dict[str, Any]],
        delivery_cell_id: str,
        urgent: bool,
        priority: int,
        *,
        created_tick: int = 0,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        if not lines:
            lines = [{"cellId": delivery_cell_id, "layer": 1}]
        line_payload: list[dict[str, Any]] = []
        for line in lines:
            cell = line.get("cellId") or node_to_cell_id(topology, line["nodeId"])
            entry = {"nodeId": line.get("nodeId"), "cellId": cell, "layer": int(line.get("layer", 1))}
            # 货道深度（四向车库倒库/按深度取货用）；普通任务无此字段。
            if line.get("depth") is not None:
                entry["depth"] = int(line["depth"])
            # 箱内件数：CTU 尾箱需要按整箱件数携带，拣货时再按需求件数扣减。
            if line.get("qty") is not None:
                entry["qty"] = int(line["qty"])
            for field in SKU_FIELDS:
                if line.get(field) not in (None, ""):
                    entry[field] = line[field]
            line_payload.append(entry)
        source_cell = line_payload[0]["cellId"]
        source_layer = int(line_payload[0]["layer"])
        return {
            "id": task_id,
            "orderId": order_id,
            "robotType": robot_type,
            "mode": mode,
            "status": "pending",
            "phase": "pending_assignment",
            "phaseLabel": "待分配",
            "sourceCellId": source_cell,
            "sourceLayer": source_layer,
            "sourceNodeId": line_payload[0].get("nodeId"),
            "deliveryCellId": delivery_cell_id,
            "targetCellId": source_cell,
            "targetLayer": source_layer,
            "lines": line_payload,
            "currentLineIndex": 0,
            "picked": 0,
            "priority": priority,
            "etaTicks": 0,
            "urgent": urgent,
            "assignedRobotId": None,
            "remainingTicks": 0,
            # 人工拣货闭环字段（仅在拓扑存在 pick_station 时启用）
            "pickStationZoneId": None,
            "pickStationCellId": None,
            "dropZoneZoneId": None,
            "dropZoneCellId": None,
            "placedGoods": [],
            # 统一阶段计时：conveyor_to_lift / lift_to_upper / conveyor_to_pick /
            # picking / place_goods / conveyor_return 共用。
            "remainingStageTicks": 0,
            "returnLineIndex": 0,
            "transitGoods": [],
            "history": [],
            "createdTick": created_tick,
            "startedTick": None,
            "completedTick": None,
        }

    async def _loop(self, runtime: RunRuntime) -> None:
        nominal_duration = max(1, int(runtime.config.get("durationTicks", 90)))
        safety_limit = max(
            MIN_SAFETY_TICKS,
            nominal_duration * 20,
            int(runtime.config.get("maxDurationTicks") or 0),
        )
        try:
            while runtime.state["tick"] < safety_limit:
                if runtime.stopped:
                    break
                if runtime.paused:
                    await asyncio.sleep(0.25)
                    continue
                # Empty queues and already-terminal restored snapshots complete
                # without manufacturing one extra tick.
                if self._all_tasks_completed(runtime):
                    break
                # State mutation/persistence and transport are deliberately
                # serialized. The previous fire-and-forget broadcast allowed
                # large first-tick inventory frames to overlap later sends.
                self._step_runtime(runtime, persist=True)
                await self._broadcast_tick(runtime)
                if self._all_tasks_completed(runtime):
                    break
                await asyncio.sleep(self._tick_delay(runtime))
        except asyncio.CancelledError:
            # reset() intentionally cancels the old loop before restoring the
            # initial snapshot; it must not be reported as a runtime failure.
            if not runtime.resetting:
                self._finalize_deadlock_observation(runtime, "background_task_cancelled")
                self._finalize_motion_observation(runtime, "background_task_cancelled")
                try:
                    self._persist(runtime)
                except Exception:
                    logger.exception(
                        "Failed to persist cancelled-run deadlock diagnostics: run_id=%s",
                        runtime.run_id,
                    )
            raise
        except Exception as error:
            await self._handle_loop_failure(runtime, error)
            return

        if runtime.stopped:
            runtime.state["status"] = "stopped"
            runtime.state["stopReason"] = runtime.state.get("stopReason") or "user"
        elif self._all_tasks_completed(runtime):
            failed = [
                task for task in runtime.state.get("tasks", [])
                if task.get("status") in {"failed", "exception"}
            ]
            failed_orders = [
                order for order in runtime.state.get("orders", [])
                if order.get("status") == "exception"
            ]
            runtime.state["status"] = "stopped" if failed or failed_orders else "completed"
            if failed or failed_orders:
                runtime.state["stopReason"] = (
                    "transfer_failed"
                    if failed and all(task.get("mode") == "shuttle_transfer" for task in failed) and not failed_orders
                    else "task_failed"
                )
            else:
                runtime.state["stopReason"] = None
        else:
            runtime.state["status"] = "stopped"
            runtime.state["stopReason"] = "safety_limit"
            self._emit_alert(
                runtime,
                runtime.state["tick"],
                category="simulation_timeout",
                level="error",
                message="仿真达到安全上限，但仍有任务未完成。",
                details={
                    "safetyLimit": safety_limit,
                    "unfinishedTaskIds": [
                        task["id"] for task in runtime.state.get("tasks", []) if task.get("status") != "completed"
                    ],
                },
                suggestion="检查不可达任务、设备故障或持续交通阻塞。",
            )
        self._finalize_deadlock_observation(
            runtime,
            str(runtime.state.get("stopReason") or runtime.state.get("status") or "completed"),
        )
        self._finalize_motion_observation(
            runtime,
            str(runtime.state.get("stopReason") or runtime.state.get("status") or "completed"),
        )
        self._persist(runtime)
        await self.hub.broadcast(runtime.run_id, {"type": "simulation_completed", "tick": runtime.state["tick"], "payload": runtime.metrics})

    async def _handle_loop_failure(self, runtime: RunRuntime, error: Exception) -> None:
        tick = int(runtime.state.get("tick", 0))
        error_type = type(error).__name__
        error_message = str(error) or error_type
        logger.exception(
            "Simulation loop failed: run_id=%s tick=%s error=%s",
            runtime.run_id,
            tick,
            error_message,
        )
        runtime.stopped = True
        runtime.paused = False
        runtime.state["status"] = "stopped"
        runtime.state["stopReason"] = "runtime_error"
        runtime.state["runtimeError"] = {
            "type": error_type,
            "message": error_message,
            "tick": tick,
        }
        runtime.state["updatedAt"] = now_dt().isoformat()
        alert = {
            "category": "simulation_runtime_error",
            "level": "error",
            "message": f"仿真后台任务在 Tick {tick} 异常停止：{error_message}",
            "tick": tick,
            "details": {"runId": runtime.run_id, "errorType": error_type},
            "suggestion": "查看后端异常堆栈并修复对应任务阶段后重置 Run。",
        }
        runtime.alerts.append(alert)
        runtime.state["alerts"] = runtime.alerts[-10:]
        try:
            self._record_event(runtime, tick, "simulation_runtime_error", runtime.state["runtimeError"])
        except Exception:
            logger.exception("Failed to record simulation runtime error: run_id=%s", runtime.run_id)
        self._finalize_deadlock_observation(runtime, "runtime_error")
        self._finalize_motion_observation(runtime, "runtime_error")
        try:
            self._persist(runtime)
        except Exception:
            logger.exception("Failed to persist simulation runtime error: run_id=%s", runtime.run_id)
        await self.hub.broadcast(
            runtime.run_id,
            {
                "type": "simulation_error",
                "tick": tick,
                "payload": self._tick_payload(runtime),
            },
        )

    def _all_tasks_completed(self, runtime: RunRuntime) -> bool:
        tasks = runtime.state.get("tasks", [])
        orders = runtime.state.get("orders", [])
        tasks_terminal = all(task.get("status") in TERMINAL_TASK_STATUSES for task in tasks)
        orders_terminal = all(order.get("status") in {"completed", "exception", "cancelled"} for order in orders)
        return tasks_terminal and orders_terminal

    def _tick_delay(self, runtime: RunRuntime) -> float:
        multiplier = runtime.speed_multiplier if runtime.speed_multiplier in SUPPORTED_SPEED_MULTIPLIERS else DEFAULT_SPEED_MULTIPLIER
        return TICK_INTERVAL_SECONDS / multiplier

    def _step_runtime(self, runtime: RunRuntime, *, persist: bool) -> None:
        runtime.state["tick"] += 1
        runtime.state["updatedAt"] = now_dt().isoformat()
        tick = runtime.state["tick"]
        self._begin_deadlock_observation(runtime, tick)
        self._begin_motion_observation(runtime, tick)
        self._inject_faults(runtime, tick)
        self._inject_urgent_order(runtime, tick)
        self._assign_tasks(runtime)
        self._move_robots(runtime)
        self._advance_picking_arm(runtime)
        self._advance_picking_arms(runtime)
        self._advance_detached_conveyor_tasks(runtime)
        self._advance_detached_picking_tasks(runtime)
        self._advance_feeding_parents(runtime)
        self._detect_contention(runtime)
        self._refresh_orders(runtime)
        self._refresh_metrics(runtime)
        self._advance_deadlock_observation(runtime, tick)
        # 全量状态落库很重（任务含完整 plan，状态随历史增长）；常规 tick 降频为
        # 每 10 tick 一次，结束/控制操作仍即时落库（其余 _persist 调用点不变）。
        if persist and tick % 10 == 0:
            self._persist(runtime)

    def _inject_faults(self, runtime: RunRuntime, tick: int) -> None:
        if runtime.state.get("orderDriven"):
            return
        config = runtime.config["faultInjection"]
        if config.get("robotFailureTick") == tick and runtime.state["robots"]:
            target_id = config.get("robotFailureId") or runtime.state["robots"][0]["id"]
            for robot in runtime.state["robots"]:
                if robot["id"] != target_id or robot["failed"]:
                    continue
                task = find_task(runtime.state["tasks"], robot["currentTaskId"])
                if task and task["status"] != "completed":
                    task["status"] = "pending"
                    task["phase"] = "pending_assignment"
                    task["phaseLabel"] = "待分配"
                    task["assignedRobotId"] = None
                    task["etaTicks"] = 0
                    task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
                robot["failed"] = True
                robot["status"] = "failed"
                robot["phaseLabel"] = "设备故障"
                robot["handlingAction"] = None
                robot["currentTaskId"] = None
                robot["path"] = [robot["currentCellId"]]
                # 故障设备停在原地后，它占的格子就是永久障碍：其它设备的剩余路径
                # 如果穿过该格，要么绕开，要么把任务退回重新分配，否则会一直 blocked。
                failed_pos = parse_cell_id(robot["currentCellId"])
                for other in runtime.state["robots"]:
                    if other["id"] == target_id or other["failed"] or not other["currentTaskId"]:
                        continue
                    other_task = find_task(runtime.state["tasks"], other["currentTaskId"])
                    if other_task is None or robot["currentCellId"] not in other["path"][1:]:
                        continue
                    retry = shortest_path(
                        runtime.topology,
                        other["currentCellId"],
                        other["targetCellId"],
                        runtime.config["pathPolicy"],
                        avoid_storage=True,
                        extra_blocked={failed_pos},
                    )
                    if retry and len(retry) > 1:
                        other["path"] = [cell["id"] for cell in retry]
                        other["pathLayers"] = [1] * len(other["path"])
                        continue
                    self._release_task(runtime, other, other_task, reason="通道被故障设备占用")
                self._emit_alert(
                    runtime,
                    tick,
                    category="robot_fault",
                    level="error",
                    message=f"设备 {target_id} 在 tick {tick} 发生故障。",
                    details={"robotId": target_id, "releasedTaskId": task["id"] if task else None},
                    suggestion="将待处理任务重新分配给同类型空闲设备。",
                )
                break

    def _inject_urgent_order(self, runtime: RunRuntime, tick: int) -> None:
        if runtime.state.get("orderDriven"):
            return
        urgent_tick = runtime.config["orderGenerator"].get("urgentInsertionTick")
        if urgent_tick != tick:
            return
        if any(task.get("urgent") and task.get("createdTick") == urgent_tick for task in runtime.state["tasks"]):
            return
        delivery = self._first_delivery_cell(runtime.topology)
        if not delivery:
            return
        inventory = runtime.state.get("inventory") or []
        serviceable = serviceable_storage_cells(runtime.topology)
        source_ids = {
            node["id"]
            for node in ctu_storage_nodes(runtime.topology)
            if node_position(node) in serviceable
        }
        used_slots = {
            (line.get("nodeId"), int(line.get("layer", 1)))
            for task in runtime.state["tasks"]
            for line in task.get("lines", [])
        }
        candidates = [
            slot
            for slot in inventory
            if slot.get("nodeId") in source_ids and (slot["nodeId"], int(slot["layer"])) not in used_slots
        ]
        if not candidates:
            return
        slot = random.choice(candidates)
        line: dict[str, Any] = {"nodeId": slot["nodeId"], "layer": int(slot["layer"])}
        for field in SKU_FIELDS:
            if slot.get(field) not in (None, ""):
                line[field] = slot[field]
        lines = [line]
        task = self._build_task(
            runtime.topology,
            f"task-urgent-{tick}",
            "ctu",
            "direct",
            lines,
            delivery,
            True,
            1,
            created_tick=tick,
        )
        runtime.state["tasks"].insert(0, task)
        self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "status": "pending", "urgent": True})

    def _assign_tasks(self, runtime: RunRuntime) -> None:
        # 回库子任务已在回库口等待：优先派空闲 CTU 取回，再派新任务。
        self._assign_pickup_returns(runtime)
        pending = [task for task in runtime.state["tasks"] if task["status"] == "pending"]
        if not pending:
            return
        pending.sort(key=lambda task: (task["priority"], task["id"]))
        # 故障设备停在原地，占的格子对其它设备不可通行：寻路和代价计算都要排除。
        failed_cells = {
            parse_cell_id(robot["currentCellId"]) for robot in runtime.state["robots"] if robot["failed"]
        }
        transfer_task_ids: set[str] = set()
        for task in pending:
            if task.get("mode") != "shuttle_transfer":
                continue
            robot = next(
                (
                    item for item in runtime.state["robots"]
                    if item["id"] == task.get("requestedVehicleId") and item["status"] == "idle"
                ),
                None,
            )
            if robot is None:
                requested = next(
                    (item for item in runtime.state["robots"] if item["id"] == task.get("requestedVehicleId")),
                    None,
                )
                if requested is None:
                    self._fail_pending_task(runtime, task, "requested_vehicle_missing")
                elif requested.get("failed") or requested.get("status") in {"failed", "exception"}:
                    self._fail_pending_task(runtime, task, "requested_vehicle_unavailable")
                continue
            transfer_task_ids.add(task["id"])
            robot["currentTaskId"] = task["id"]
            robot["status"] = "handling"
            robot["phaseLabel"] = "到达源库位，准备取货"
            robot["load"] = 0
            robot["path"] = [robot["currentCellId"]]
            robot["pathLayers"] = [robot.get("currentLayer", 1)]
            robot["pathStages"] = ["pickup_wait"]
            task["assignedRobotId"] = robot["id"]
            task["status"] = "assigned"
            task["phase"] = "pickup_wait"
            task["phaseLabel"] = "到达源库位，准备取货"
            task["pickupReadyTick"] = runtime.state["tick"] + 1
            task["startedTick"] = runtime.state["tick"]
            task["history"].append({"tick": runtime.state["tick"], "phase": task["phase"], "status": task["status"]})
            self._record_event(
                runtime,
                runtime.state["tick"],
                "transfer_stage",
                {"taskId": task["id"], "stage": "pickup_wait", "robotId": robot["id"]},
            )

        matches = assign_tasks(
            runtime.config.get("dispatchPolicy", "greedy"),
            runtime.state["robots"],
            [task for task in pending if task["id"] not in transfer_task_ids],
            runtime.topology,
            runtime.config.get("pathPolicy", "astar"),
            failed_cells,
        )
        for robot_id, task_id in matches:
            robot = next(item for item in runtime.state["robots"] if item["id"] == robot_id)
            task = find_task(runtime.state["tasks"], task_id)
            if task is None:
                continue
            phase = "pickup_from_ctu" if task["mode"] == "direct" else "pickup_from_shuttle"
            if not self._robot_path_to(
                runtime,
                robot,
                task["sourceCellId"],
                task["sourceLayer"],
                extra_blocked=failed_cells,
                stop_adjacent=True,
            ):
                self._fail_pending_task(runtime, task, "source_unreachable")
                continue
            robot["currentTaskId"] = task_id
            robot["status"] = "moving"
            robot["waitTicks"] = 0
            robot["load"] = 0
            robot["handlingAction"] = None
            robot["phaseLabel"] = self._phase_label(phase)
            task["assignedRobotId"] = robot_id
            task["status"] = "assigned"
            task["phase"] = phase
            task["phaseLabel"] = self._phase_label(phase)
            task["startedTick"] = runtime.state["tick"]
            task["etaTicks"] = max(len(robot["path"]) - 1, 1)
            task["history"].append({"tick": runtime.state["tick"], "phase": task["phase"], "status": task["status"]})
            self._record_event(runtime, runtime.state["tick"], "task_state_changed", {"taskId": task_id, "status": "assigned", "robotId": robot_id, "phase": task["phase"]})

        matched_task_ids = transfer_task_ids | {task_id for _, task_id in matches}
        for task in pending:
            if task["id"] in matched_task_ids or task.get("status") != "pending":
                continue
            compatible = [
                robot for robot in runtime.state["robots"]
                if robot.get("type") == task.get("robotType")
                and not robot.get("failed")
                and robot.get("status") not in {"failed", "exception"}
                and (not task.get("requestedVehicleId") or robot["id"] == task["requestedVehicleId"])
            ]
            if not compatible:
                self._fail_pending_task(runtime, task, "no_compatible_robot")
                continue
            # Busy compatible robots may become available later. Only declare
            # the task unreachable when every compatible robot is idle and the
            # static topology offers no path from any of them to the source.
            if all(robot.get("status") == "idle" for robot in compatible) and not any(
                self._can_reach_task(runtime, robot, task) for robot in compatible
            ):
                self._fail_pending_task(runtime, task, "source_unreachable")

    def _can_reach_task(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any]) -> bool:
        if robot.get("type") == "shuttle":
            return bool(shortest_path_shuttle(
                runtime.topology,
                robot["currentCellId"],
                robot.get("currentLayer", 1),
                task["sourceCellId"],
                task.get("sourceLayer", 1),
                runtime.config["pathPolicy"],
            ))
        return bool(shortest_path(
            runtime.topology,
            robot["currentCellId"],
            task["sourceCellId"],
            runtime.config["pathPolicy"],
            avoid_storage=True,
        ))

    def _fail_pending_task(self, runtime: RunRuntime, task: dict[str, Any], reason: str) -> None:
        if task.get("status") != "pending":
            return
        tick = runtime.state["tick"]
        task["status"] = "failed"
        task["phase"] = "failed"
        task["phaseLabel"] = f"Task cannot be scheduled: {reason}"
        task["failureReason"] = reason
        task["completedTick"] = tick
        task.setdefault("history", []).append({"tick": tick, "phase": "failed", "status": "failed"})
        self._record_event(runtime, tick, "task_failed", {"taskId": task["id"], "reason": reason})
        self._emit_alert(
            runtime,
            tick,
            category=f"task_unassignable:{task['id']}",
            level="error",
            message=f"Task {task['id']} cannot be scheduled ({reason}).",
            details={"taskId": task["id"], "reason": reason},
            suggestion="Check the requested vehicle, device availability, and topology reachability.",
        )

    def _move_robots(self, runtime: RunRuntime) -> None:
        movable = [robot for robot in runtime.state["robots"] if robot["status"] not in {"failed", "idle", "exception"}]
        movable.sort(key=lambda item: item["id"])
        occupied_cells = {robot["currentCellId"] for robot in runtime.state["robots"]}
        reserved_targets: set[str] = set()

        for robot in movable:
            task = find_task(runtime.state["tasks"], robot["currentTaskId"])
            if task is None:
                if robot.get("returningToStandby"):
                    self._move_robot_to_standby(runtime, robot, occupied_cells, reserved_targets)
                    continue
                robot["status"] = "idle"
                robot["phaseLabel"] = "待命"
                robot["path"] = [robot["currentCellId"]]
                robot["pathLayers"] = [robot.get("currentLayer", 1)]
                continue

            if robot.get("yieldTicks", 0) > 0:
                robot["yieldTicks"] -= 1
                robot["status"] = "blocked"
                robot["phaseLabel"] = f"{task['phaseLabel']} / 主动让行"
                yield_path = robot.get("path") or []
                self._observe_robot_block(
                    runtime,
                    robot,
                    yield_path[1] if len(yield_path) > 1 else None,
                    None,
                    ResourceType.OTHER,
                    "ACTIVE_YIELD_WAIT",
                )
                continue

            path = robot["path"]
            if len(path) <= 1:
                self._advance_robot_phase(runtime, robot, task, occupied_cells)
                continue

            path_layers = robot.get("pathLayers") or [robot.get("currentLayer", 1)] * len(path)
            path_stages = robot.get("pathStages") or [task.get("phase", "moving")] * len(path)
            current_cell_id = robot["currentCellId"]
            next_cell_id = path[1]
            next_layer = path_layers[1] if len(path_layers) > 1 else robot.get("currentLayer", 1)
            next_stage = path_stages[1] if len(path_stages) > 1 else task.get("phase", "moving")
            occupied_cells.discard(current_cell_id)

            if next_cell_id in occupied_cells or next_cell_id in reserved_targets:
                robot["status"] = "blocked"
                robot["waitTicks"] += 1
                robot["phaseLabel"] = f"{task['phaseLabel']} / 等待让行"
                occupied_cells.add(current_cell_id)
                blocker = next((item for item in movable if item["currentCellId"] == next_cell_id), None)
                observed_blocker = next(
                    (
                        item for item in runtime.state["robots"]
                        if item.get("id") != robot.get("id")
                        and item.get("currentCellId") == next_cell_id
                    ),
                    None,
                )
                block_resource = (
                    ResourceType.ROBOT
                    if observed_blocker is not None
                    else ResourceType.RESERVATION
                    if next_cell_id in reserved_targets
                    else ResourceType.UNKNOWN_BLOCKER
                )
                self._observe_robot_block(
                    runtime,
                    robot,
                    next_cell_id,
                    observed_blocker.get("id") if observed_blocker else None,
                    block_resource,
                    (
                        "NEXT_CELL_OCCUPIED"
                        if observed_blocker is not None
                        else "TARGET_RESERVED"
                        if next_cell_id in reserved_targets
                        else "UNKNOWN_BLOCKER"
                    ),
                )
                # 连续等待后仍无法前进：CTU 先绕开其它设备重新寻路，绕不开再处理头对头死锁；
                # 四向车同样先绕行（带面/巷道有并行道），头对头时退一格再绕。
                if robot["type"] == "ctu" and robot["waitTicks"] >= REROUTE_AFTER_BLOCK_TICKS:
                    if self._reroute_ctu(runtime, robot, occupied_cells):
                        self._observe_deadlock_mitigation(runtime, robot, "REROUTE")
                        robot["waitTicks"] = 0
                        robot["status"] = "moving"
                        robot["phaseLabel"] = task["phaseLabel"]
                    elif self._clear_head_on(runtime, robot, blocker, occupied_cells):
                        robot["waitTicks"] = 0
                        robot["status"] = "moving"
                        robot["phaseLabel"] = task["phaseLabel"]
                    elif robot["waitTicks"] >= YIELD_AFTER_BLOCK_TICKS:
                        self._yield_ctu(runtime, robot, occupied_cells, reserved_targets)
                elif robot["type"] == "shuttle" and robot["waitTicks"] >= REROUTE_AFTER_BLOCK_TICKS:
                    if self._reroute_shuttle(runtime, robot, occupied_cells):
                        self._observe_deadlock_mitigation(runtime, robot, "REROUTE")
                        robot["waitTicks"] = 0
                        robot["status"] = "moving"
                        robot["phaseLabel"] = task["phaseLabel"]
                    elif self._clear_shuttle_head_on(runtime, robot, blocker, occupied_cells, reserved_targets):
                        robot["waitTicks"] = 0
                        robot["status"] = "moving"
                        robot["phaseLabel"] = task["phaseLabel"]
                continue

            motion_from = self._motion_position(runtime, robot)
            robot["status"] = "moving"
            robot["lastCellId"] = current_cell_id
            robot["currentCellId"] = next_cell_id
            robot["currentNode"] = next_cell_id
            floor, row, col = parse_cell_id(next_cell_id)
            robot["currentFloor"] = floor
            robot["currentRow"] = row
            robot["currentCol"] = col
            robot["currentLayer"] = next_layer
            robot["path"] = path[1:]
            robot["pathLayers"] = path_layers[1:]
            robot["pathStages"] = path_stages[1:]
            robot["battery"] = max(10.0, robot["battery"] - 0.35)
            robot["waitTicks"] = 0
            if task.get("mode") == "shuttle_transfer":
                stage_labels = {
                    "track": "四向车沿规划轨道行驶",
                    "enter_lift": f"进入 {task['plan'].get('chosen_lift') or '升降机'} / 等待",
                    "lift_moving": f"{task['plan'].get('chosen_lift') or '升降机'} 升降中",
                    "exit_lift": f"离开 {task['plan'].get('chosen_lift') or '升降机'}",
                }
                previous_stage = task.get("phase")
                task["phase"] = next_stage
                task["phaseLabel"] = stage_labels.get(next_stage, "四向车运输中")
                robot["phaseLabel"] = task["phaseLabel"]
                if previous_stage != next_stage:
                    self._record_event(
                        runtime,
                        runtime.state["tick"],
                        "transfer_stage",
                        {"taskId": task["id"], "stage": next_stage, "layer": next_layer},
                    )
            else:
                robot["phaseLabel"] = task["phaseLabel"]
            occupied_cells.add(next_cell_id)
            reserved_targets.add(next_cell_id)
            self._record_committed_motion(
                runtime,
                robot,
                motion_from,
                None,
                "_move_robots",
            )
            self._record_event(runtime, runtime.state["tick"], "robot_position", {"robotId": robot["id"], "cellId": robot["currentCellId"], "floor": floor, "row": row, "col": col, "layer": next_layer, "status": robot["status"]})

            if len(robot["path"]) <= 1:
                self._advance_robot_phase(runtime, robot, task, occupied_cells)

        self._flush_motion_events(runtime)

    def _reroute_ctu(self, runtime: RunRuntime, robot: dict[str, Any], occupied_cells: set[str]) -> bool:
        """CTU 被堵时绕行：把其它设备当前占用的格临时视为障碍，重新算一条到当前目标的路径。"""
        target = robot.get("targetCellId")
        if not target or target == robot["currentCellId"]:
            return False
        others = {parse_cell_id(cell) for cell in occupied_cells if cell != robot["currentCellId"]}
        # 普通绕行不得立即返回上一格，否则两辆相邻 CTU 会同步在两条巷道间
        # A-B-A-B 往返。真正需要后退时由 _clear_head_on/_yield_ctu 显式执行。
        last_cell = robot.get("lastCellId")
        if last_cell and last_cell != target:
            others.add(parse_cell_id(last_cell))
        path_cells = shortest_path(
            runtime.topology,
            robot["currentCellId"],
            target,
            runtime.config["pathPolicy"],
            avoid_storage=True,
            extra_blocked=others,
        )
        task = find_task(runtime.state["tasks"], robot.get("currentTaskId"))
        if task and task.get("phase") == "pickup_from_ctu" and len(path_cells) > 1:
            path_cells = path_cells[:-1]
        if not path_cells or len(path_cells) <= 1 or path_cells[1]["id"] in occupied_cells:
            return False
        robot["path"] = [cell["id"] for cell in path_cells]
        robot["pathLayers"] = [1] * len(robot["path"])
        robot["targetCellId"] = target
        return True

    def _reroute_shuttle(self, runtime: RunRuntime, robot: dict[str, Any], occupied_cells: set[str]) -> bool:
        """四向车被堵时绕行：把其它四向车占用的格（含其下一步意图格）视为动态障碍，
        重新规划到原路径终点（path[-1]，即当前阶段目标）。轨道回放任务不重排。"""
        task = find_task(runtime.state["tasks"], robot.get("currentTaskId"))
        if task and task.get("mode") == "shuttle_transfer":
            return False
        path = robot.get("path") or []
        if len(path) <= 1:
            return False
        goal_cell = path[-1]
        goal_layer = int((robot.get("pathLayers") or [robot.get("currentLayer", 1)] * len(path))[-1])
        current_layer = int(robot.get("currentLayer", 1))
        others: set[tuple[int, int, int]] = set()
        for item in runtime.state["robots"]:
            if item["id"] == robot["id"] or item.get("type") != "shuttle":
                continue
            layer = int(item.get("currentLayer", 1))
            others.add((int(item["currentRow"]), int(item["currentCol"]), layer))
            blocker_path = item.get("path") or []
            if len(blocker_path) > 1 and blocker_path[1] != item["currentCellId"]:
                _floor, nr, nc = parse_cell_id(blocker_path[1])
                others.add((nr, nc, layer))
        others.discard((int(robot["currentRow"]), int(robot["currentCol"]), current_layer))
        path3d = shortest_path_shuttle(
            runtime.topology,
            robot["currentCellId"],
            current_layer,
            goal_cell,
            goal_layer,
            runtime.config["pathPolicy"],
            extra_blocked=others,
        )
        if not path3d or len(path3d) <= 1:
            return False
        first = cell_id(path3d[1][0], path3d[1][1], "1F")
        if first in occupied_cells:
            return False
        stage = task.get("phase", "moving") if task else "moving"
        robot["path"] = [cell_id(row, col, "1F") for row, col, _ in path3d]
        robot["pathLayers"] = [layer for _, _, layer in path3d]
        robot["pathStages"] = [stage] * len(robot["path"])
        return True

    def _back_off_shuttle(self, runtime: RunRuntime, robot: dict[str, Any], occupied_cells: set[str], reserved_targets: set[str]) -> bool:
        """四向车退回上一格（仅地面层；layer>1 的竖井场景不适用），破坏头对头对峙。"""
        if int(robot.get("currentLayer", 1)) != 1:
            return False
        last = robot.get("lastCellId")
        if not last or last == robot["currentCellId"] or last in occupied_cells or last in reserved_targets:
            return False
        motion_from = self._motion_position(runtime, robot)
        old_path = robot.get("path") or [robot["currentCellId"]]
        old_layers = robot.get("pathLayers") or [int(robot.get("currentLayer", 1))] * len(old_path)
        floor, row, col = parse_cell_id(last)
        robot["currentCellId"] = last
        robot["currentFloor"] = floor
        robot["currentRow"] = row
        robot["currentCol"] = col
        robot["path"] = [last] + list(old_path)
        robot["pathLayers"] = [int(robot.get("currentLayer", 1))] + [int(layer) for layer in old_layers]
        task = find_task(runtime.state["tasks"], robot.get("currentTaskId"))
        stage = task.get("phase", "moving") if task else "moving"
        robot["pathStages"] = [stage] * len(robot["path"])
        self._record_committed_motion(
            runtime,
            robot,
            motion_from,
            MovementType.BACKOFF,
            "_back_off_shuttle",
        )
        self._observe_deadlock_mitigation(runtime, robot, "BACKTRACK", "shuttle_head_on")
        return True

    def _clear_shuttle_head_on(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        blocker: dict[str, Any] | None,
        occupied_cells: set[str],
        reserved_targets: set[str],
    ) -> bool:
        """两台四向车在带面/巷道头对头：确认对向车正要进入本车格后，退一格再绕行；
        本车退不了则让对向车退。带面与巷道块都是 2 格宽，退让后必有并行道可绕。"""
        if blocker is None or blocker.get("type") != "shuttle" or blocker.get("status") in {"failed", "idle", "exception"}:
            return False
        blocker_path = blocker.get("path") or []
        if len(blocker_path) <= 1 or blocker_path[1] != robot["currentCellId"]:
            return False
        if self._back_off_shuttle(runtime, robot, occupied_cells, reserved_targets):
            if self._reroute_shuttle(runtime, robot, occupied_cells):
                return True
            # 退了但暂时绕不开：至少打破头对头，对向车可以先走
            robot["status"] = "moving"
            return True
        if self._back_off_shuttle(runtime, blocker, occupied_cells, reserved_targets):
            return self._reroute_shuttle(runtime, blocker, occupied_cells)
        return False

    def _clear_head_on(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        blocker: dict[str, Any] | None,
        occupied_cells: set[str],
    ) -> bool:
        """头对头死锁：两台车互相把对方当前格当作下一格时，让其中一台后退一格再绕行。"""
        if blocker is None or blocker["status"] in {"failed", "idle", "exception"}:
            return False
        blocker_path = blocker.get("path") or []
        if len(blocker_path) <= 1 or blocker_path[1] != robot["currentCellId"]:
            return False
        # 固定优先级：ID 较大的车辆让行，避免双方在不同 tick 轮流认为
        # 自己应该后退，形成“你让我、我让你”的对称振荡。
        yielding, priority = sorted((robot, blocker), key=lambda item: str(item.get("id")), reverse=True)
        if self._yield_one_cell(runtime, yielding, occupied_cells):
            return True
        return self._yield_one_cell(runtime, priority, occupied_cells)

    def _yield_one_cell(self, runtime: RunRuntime, robot: dict[str, Any], occupied_cells: set[str]) -> bool:
        """后退到上一格，并从那里重新规划到原目标；退不了或规划失败返回 False。"""
        last = robot.get("lastCellId")
        if not last or last == robot["currentCellId"] or last in occupied_cells:
            return False
        # 不能退进货位/输送带格（上一格是刚取完货的库位时会出现）。
        if parse_cell_id(last) in build_occupied_lookup(runtime.topology):
            return False
        target = robot.get("targetCellId")
        if not target:
            return False
        blocked = {parse_cell_id(cell) for cell in occupied_cells}
        path_cells = shortest_path(
            runtime.topology,
            last,
            target,
            runtime.config["pathPolicy"],
            avoid_storage=True,
            extra_blocked=blocked,
        )
        if not path_cells or len(path_cells) <= 1:
            return False
        motion_from = self._motion_position(runtime, robot)
        occupied_cells.discard(robot["currentCellId"])
        robot["lastCellId"] = robot["currentCellId"]
        robot["currentCellId"] = last
        robot["currentNode"] = last
        floor, row, col = parse_cell_id(last)
        robot["currentFloor"] = floor
        robot["currentRow"] = row
        robot["currentCol"] = col
        robot["path"] = [cell["id"] for cell in path_cells]
        robot["pathLayers"] = [1] * len(robot["path"])
        occupied_cells.add(last)
        self._record_committed_motion(
            runtime,
            robot,
            motion_from,
            MovementType.BACKOFF,
            "_yield_one_cell",
        )
        self._observe_deadlock_mitigation(runtime, robot, "BACKTRACK", "ctu_head_on")
        self._record_event(
            runtime,
            runtime.state["tick"],
            "robot_position",
            {"robotId": robot["id"], "cellId": last, "floor": floor, "row": row, "col": col, "layer": robot.get("currentLayer", 1), "status": "yielding"},
        )
        return True

    def _release_task(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any], *, reason: str) -> None:
        """任务无法继续时退回待分配，并让设备回到空闲，避免永久 blocked。"""
        tick = runtime.state["tick"]
        robot["currentTaskId"] = None
        robot["status"] = "idle"
        robot["phaseLabel"] = f"待命 / {reason}"
        robot["handlingAction"] = None
        robot["path"] = [robot["currentCellId"]]
        robot["pathLayers"] = [robot.get("currentLayer", 1)]
        robot["load"] = 0
        robot["carrying"] = []
        robot["waitTicks"] = 0
        task["status"] = "pending"
        task["phase"] = "pending_assignment"
        task["phaseLabel"] = self._phase_label(task["phase"])
        task["assignedRobotId"] = None
        task["etaTicks"] = 0
        task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {"taskId": task["id"], "status": task["status"], "robotId": robot["id"], "reason": reason},
        )

    def _move_robot_to_standby(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        occupied_cells: set[str],
        reserved_targets: set[str],
    ) -> None:
        """已交付但暂时没有任务的 CTU 返回起始待命位，避免长期占住唯一出库口。"""
        if robot.get("yieldTicks", 0) > 0:
            robot["yieldTicks"] -= 1
            robot["status"] = "blocked"
            robot["phaseLabel"] = "返回待命位 / 主动让行"
            yield_path = robot.get("path") or []
            self._observe_robot_block(
                runtime,
                robot,
                yield_path[1] if len(yield_path) > 1 else None,
                None,
                ResourceType.OTHER,
                "ACTIVE_YIELD_WAIT",
            )
            return

        path = robot.get("path") or [robot["currentCellId"]]
        if len(path) <= 1:
            robot["returningToStandby"] = False
            robot["status"] = "idle"
            robot["phaseLabel"] = "待命"
            return

        current_cell_id = robot["currentCellId"]
        next_cell_id = path[1]
        occupied_cells.discard(current_cell_id)
        if next_cell_id in occupied_cells or next_cell_id in reserved_targets:
            occupied_cells.add(current_cell_id)
            robot["status"] = "blocked"
            robot["waitTicks"] += 1
            robot["phaseLabel"] = "返回待命位 / 等待让行"
            observed_blocker = next(
                (
                    item for item in runtime.state["robots"]
                    if item.get("id") != robot.get("id")
                    and item.get("currentCellId") == next_cell_id
                ),
                None,
            )
            block_resource = (
                ResourceType.ROBOT
                if observed_blocker is not None
                else ResourceType.RESERVATION
                if next_cell_id in reserved_targets
                else ResourceType.UNKNOWN_BLOCKER
            )
            self._observe_robot_block(
                runtime,
                robot,
                next_cell_id,
                observed_blocker.get("id") if observed_blocker else None,
                block_resource,
                (
                    "NEXT_CELL_OCCUPIED"
                    if observed_blocker is not None
                    else "TARGET_RESERVED"
                    if next_cell_id in reserved_targets
                    else "UNKNOWN_BLOCKER"
                ),
            )
            if robot["type"] == "ctu" and robot["waitTicks"] >= REROUTE_AFTER_BLOCK_TICKS:
                if self._reroute_ctu(runtime, robot, occupied_cells):
                    self._observe_deadlock_mitigation(runtime, robot, "REROUTE")
                    robot["waitTicks"] = 0
                    robot["status"] = "returning"
                    robot["phaseLabel"] = "返回待命位"
                elif robot["waitTicks"] >= YIELD_AFTER_BLOCK_TICKS:
                    self._yield_ctu(runtime, robot, occupied_cells, reserved_targets)
            elif robot["type"] == "shuttle" and robot["waitTicks"] >= REROUTE_AFTER_BLOCK_TICKS:
                if self._reroute_shuttle(runtime, robot, occupied_cells):
                    self._observe_deadlock_mitigation(runtime, robot, "REROUTE")
                    robot["waitTicks"] = 0
                    robot["status"] = "returning"
                    robot["phaseLabel"] = "返回待命位"
            return

        motion_from = self._motion_position(runtime, robot)
        path_layers = robot.get("pathLayers") or [1] * len(path)
        next_layer = path_layers[1] if len(path_layers) > 1 else 1
        robot["currentCellId"] = next_cell_id
        robot["currentNode"] = next_cell_id
        floor, row, col = parse_cell_id(next_cell_id)
        robot["currentFloor"] = floor
        robot["currentRow"] = row
        robot["currentCol"] = col
        robot["currentLayer"] = next_layer
        robot["path"] = path[1:]
        robot["pathLayers"] = path_layers[1:]
        robot["battery"] = max(10.0, robot["battery"] - 0.35)
        robot["waitTicks"] = 0
        robot["status"] = "returning"
        robot["phaseLabel"] = "返回待命位"
        occupied_cells.add(next_cell_id)
        reserved_targets.add(next_cell_id)
        self._record_committed_motion(
            runtime,
            robot,
            motion_from,
            MovementType.RETURN_TO_STANDBY,
            "_move_robot_to_standby",
        )
        self._record_event(
            runtime,
            runtime.state["tick"],
            "robot_position",
            {"robotId": robot["id"], "cellId": next_cell_id, "floor": floor, "row": row, "col": col, "layer": next_layer, "status": robot["status"]},
        )
        if len(robot["path"]) <= 1:
            robot["returningToStandby"] = False
            robot["status"] = "idle"
            robot["phaseLabel"] = "待命"

    def _yield_ctu(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        occupied_cells: set[str],
        reserved_targets: set[str],
    ) -> bool:
        """无可用绕行路线时退到相邻空闲格，给对向车辆释放当前通道。"""
        try:
            floor, row, col = parse_cell_id(robot["currentCellId"])
            target_floor, target_row, target_col = parse_cell_id(robot.get("targetCellId") or robot["currentCellId"])
        except (TypeError, ValueError):
            return False

        walkable = build_walkable_lookup(runtime.topology)
        fixed_occupied = build_occupied_lookup(runtime.topology)
        unavailable = set(occupied_cells) | set(reserved_targets)
        candidates: list[tuple[int, str, tuple[str, int, int]]] = []
        for d_row, d_col in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            position = (floor, row + d_row, col + d_col)
            cell = walkable.get(position)
            if not cell or position in fixed_occupied or cell["id"] in unavailable:
                continue
            distance = abs(position[1] - target_row) + abs(position[2] - target_col)
            if position[0] != target_floor:
                distance += 1000
            candidates.append((distance, cell["id"], position))

        if not candidates:
            return False

        # 优先退向离当前目标更远的空格，真正腾出迎面通道，而不是继续争抢目标方向。
        _, yield_cell_id, (_, yield_row, yield_col) = max(candidates, key=lambda item: (item[0], item[1]))
        previous_cell = robot["currentCellId"]
        target_cell = robot.get("targetCellId")
        motion_from = self._motion_position(runtime, robot)
        occupied_cells.discard(previous_cell)
        occupied_cells.add(yield_cell_id)
        reserved_targets.add(yield_cell_id)
        robot["currentCellId"] = yield_cell_id
        robot["currentNode"] = yield_cell_id
        robot["currentFloor"] = floor
        robot["currentRow"] = yield_row
        robot["currentCol"] = yield_col
        robot["currentLayer"] = 1
        robot["waitTicks"] = 0
        robot["yieldTicks"] = 2
        robot["status"] = "blocked"
        if target_cell:
            task = find_task(runtime.state["tasks"], robot.get("currentTaskId"))
            self._robot_path_to(
                runtime,
                robot,
                target_cell,
                robot.get("targetLayer", 1),
                stop_adjacent=bool(task and task.get("phase") == "pickup_from_ctu"),
            )
        self._record_committed_motion(
            runtime,
            robot,
            motion_from,
            MovementType.YIELD,
            "_yield_ctu",
        )
        self._observe_deadlock_mitigation(runtime, robot, "YIELD", "adjacent_free_cell")
        self._record_event(
            runtime,
            runtime.state["tick"],
            "robot_yield",
            {"robotId": robot["id"], "fromCellId": previous_cell, "toCellId": yield_cell_id, "targetCellId": target_cell},
        )
        return True

    def _transfer_goods_to_robot(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        line: dict[str, Any],
    ) -> dict[str, Any] | None:
        """CTU 取到一箱：把该库位槽从库存移除，并把货物挂到机器人 carrying，前端据此把货箱从库位移到车上。"""
        node_id = line.get("nodeId")
        if node_id is None:
            return None
        layer = int(line.get("layer", 1))
        inventory = runtime.state.get("inventory") or []
        for index, slot in enumerate(inventory):
            if slot.get("nodeId") == node_id and int(slot.get("layer", 1)) == layer:
                # 订单尾箱按整箱件数入车（线路上显式声明了 qty=箱规），拣货时再只拣所需件数；
                # 普通任务沿用库存槽自身件数。
                carried = dict(slot)
                if line.get("qty") is not None:
                    carried["qty"] = int(line["qty"])
                robot.setdefault("carrying", []).append(carried)
                del inventory[index]
                runtime.inventory_delta.append({"op": "remove", "nodeId": node_id, "layer": layer})
                return carried
        return None

    def _advance_ctu_pickup_action(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
    ) -> None:
        """用三个可观察阶段完成一次取箱，库存只在货叉真正取到箱时扣减。"""
        tick = runtime.state["tick"]
        capacity = min(ROBOT_CARRY_CAPACITY["ctu"], int(robot.get("capacity", ROBOT_CARRY_CAPACITY["ctu"])))
        lines = task.get("lines", [])
        line_index = int(task.get("currentLineIndex", 0))
        action = robot.get("handlingAction")

        if action is None:
            if robot.get("load", 0) >= capacity or line_index >= len(lines):
                if self._set_robot_leg(runtime, robot, task, "delivery_to_port", task["deliveryCellId"]):
                    task["phaseLabel"] = f"输送线交付（{task['picked']} 箱）"
                return
            line = lines[line_index]
            target_layer = max(1, min(CTU_RACK_LAYERS, int(line.get("layer", 1))))
            target_cell_id = line.get("cellId") or robot["targetCellId"]
            target_floor, target_row, target_col = parse_cell_id(target_cell_id)
            current_floor, current_row, current_col = parse_cell_id(robot["currentCellId"])
            row_delta = target_row - current_row if target_floor == current_floor else 0
            col_delta = target_col - current_col if target_floor == current_floor else 0
            if abs(col_delta) > abs(row_delta):
                approach_direction = "west" if col_delta > 0 else "east"
            else:
                approach_direction = "south" if row_delta >= 0 else "north"
            action = {
                "type": "pickup",
                "stage": "lifting",
                "targetLayer": target_layer,
                "slotIndex": int(robot.get("load", 0)),
                "nodeId": line.get("nodeId"),
                "cellId": target_cell_id,
                "approachDirection": approach_direction,
                "skuId": line.get("skuId"),
                "skuName": line.get("skuName"),
            }
            robot["handlingAction"] = action
            robot["status"] = "handling"
            robot["phaseLabel"] = f"升降至第 {target_layer} 层"
            task["phaseLabel"] = robot["phaseLabel"]
            self._record_event(runtime, tick, "ctu_handling", {"robotId": robot["id"], **action})
            return

        if action.get("stage") == "lifting":
            action["stage"] = "extending"
            robot["status"] = "handling"
            robot["phaseLabel"] = f"第 {action['targetLayer']} 层 · 伸叉取箱"
            task["phaseLabel"] = robot["phaseLabel"]
            self._record_event(runtime, tick, "ctu_handling", {"robotId": robot["id"], **action})
            return

        if action.get("stage") == "extending":
            line = lines[line_index] if 0 <= line_index < len(lines) else {}
            slot = self._transfer_goods_to_robot(runtime, robot, line)
            if slot is None:
                robot["handlingAction"] = None
                robot["status"] = "exception"
                robot["phaseLabel"] = "取货异常：目标库位无货"
                task["status"] = "failed"
                task["phase"] = "failed"
                task["phaseLabel"] = robot["phaseLabel"]
                task["failureReason"] = "pickup_inventory_missing"
                task["completedTick"] = tick
                task.setdefault("history", []).append({"tick": tick, "phase": "failed", "status": "failed"})
                robot["currentTaskId"] = None
                self._record_event(
                    runtime,
                    tick,
                    "task_failed",
                    {"taskId": task["id"], "reason": task["failureReason"], "robotId": robot["id"]},
                )
                self._emit_alert(
                    runtime,
                    tick,
                    category=f"pickup_missing:{task['id']}",
                    level="error",
                    message=f"设备 {robot['id']} 在 {line.get('nodeId', '目标库位')} 未找到待取料箱。",
                    details={"robotId": robot["id"], "taskId": task["id"], "nodeId": line.get("nodeId"), "layer": line.get("layer")},
                    suggestion="核对订单分配库存与拓扑库位数据。",
                )
                return
            robot["load"] = min(capacity, int(robot.get("load", 0)) + 1)
            task["picked"] = int(task.get("picked", 0)) + 1
            task["currentLineIndex"] = line_index + 1
            action["stage"] = "stowing"
            action["verified"] = not line.get("skuId") or slot.get("skuId") == line.get("skuId")
            robot["status"] = "handling"
            robot["phaseLabel"] = f"装入载箱位 {robot['load']}/{capacity}"
            task["phaseLabel"] = robot["phaseLabel"]
            self._record_event(
                runtime,
                tick,
                "ctu_handling",
                {"robotId": robot["id"], **action, "picked": task["picked"], "load": robot["load"]},
            )
            return

        if action.get("stage") == "stowing":
            robot["handlingAction"] = None
            if task["currentLineIndex"] < len(lines) and robot["load"] < capacity:
                next_line = lines[task["currentLineIndex"]]
                if self._set_robot_leg(runtime, robot, task, "pickup_from_ctu", next_line["cellId"], next_line.get("layer", 1)):
                    task["phaseLabel"] = f"CTU 取货 {task['picked']}/{len(lines)}"
                    robot["phaseLabel"] = f"CTU 取货 {robot['load']}/{capacity}"
            else:
                if self._set_robot_leg(runtime, robot, task, "delivery_to_port", task["deliveryCellId"]):
                    task["phaseLabel"] = f"输送线交付（{task['picked']} 箱）"
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            event: dict[str, Any] = {
                "taskId": task["id"],
                "phase": task["phase"],
                "status": task["status"],
                "picked": task["picked"],
                "load": robot["load"],
                "targetLayer": action.get("targetLayer"),
            }
            if action.get("skuId"):
                event["skuId"] = action["skuId"]
                event["skuName"] = action.get("skuName")
            self._record_event(runtime, tick, "task_state_changed", event)

    def _advance_ctu_delivery_action(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
        occupied_cells: set[str] | None = None,
    ) -> None:
        """交付到出入口：按载箱位逐箱升降、伸叉并释放到输送线。"""
        tick = runtime.state["tick"]
        action = robot.get("handlingAction")

        if action is None:
            carrying = robot.get("carrying") or []
            # 只有真正完成一次放箱动作后，货箱才进入 deliveredGoods。前端据此
            # 逐箱创建输送动画，而不是在 CTU 刚到站时一次显示全部货箱。
            task["deliveredGoods"] = []
            if not carrying:
                self._complete_task(runtime, robot, task, occupied_cells)
                return
            robot["handlingAction"] = {
                "type": "deliver",
                "stage": "lifting",
                "targetLayer": 1,
                # 从最上方载箱位开始卸货，下面仍在车上的货箱不会发生跳位。
                "slotIndex": len(carrying) - 1,
                "cellId": robot["currentCellId"],
            }
            robot["status"] = "handling"
            robot["phaseLabel"] = f"逐箱交付 0/{len(carrying)} · 升降对位"
            task["phaseLabel"] = robot["phaseLabel"]
            self._record_event(runtime, tick, "ctu_handling", {"robotId": robot["id"], **robot["handlingAction"]})
            return

        if action.get("stage") == "lifting":
            action["stage"] = "extending"
            robot["status"] = "handling"
            delivered_count = len(task.get("deliveredGoods") or [])
            total_count = delivered_count + len(robot.get("carrying") or [])
            robot["phaseLabel"] = f"逐箱交付 {delivered_count}/{total_count} · 伸叉送箱"
            task["phaseLabel"] = robot["phaseLabel"]
            self._record_event(runtime, tick, "ctu_handling", {"robotId": robot["id"], **action})
            return

        if action.get("stage") == "extending":
            action["stage"] = "stowing"
            robot["status"] = "handling"
            delivered_count = len(task.get("deliveredGoods") or [])
            total_count = delivered_count + len(robot.get("carrying") or [])
            robot["phaseLabel"] = f"逐箱交付 {delivered_count + 1}/{total_count} · 箱入输送线"
            task["phaseLabel"] = robot["phaseLabel"]
            self._record_event(runtime, tick, "ctu_handling", {"robotId": robot["id"], **action})
            return

        # stowing：当前货箱已落到输送线。保留 CTU 上的其余货箱并开始下一箱，
        # 使 deliveredGoods 每次只增加一项，驱动多个有间距的输送动画。
        carrying = robot.setdefault("carrying", [])
        delivered = task.setdefault("deliveredGoods", [])
        if carrying:
            released = dict(carrying.pop())
            released["deliveredTick"] = tick
            delivered.append(released)
        robot["load"] = len(carrying)
        delivered_count = len(delivered)
        total_count = delivered_count + len(carrying)
        self._record_event(
            runtime,
            tick,
            "ctu_box_delivered",
            {
                "robotId": robot["id"],
                "taskId": task["id"],
                "delivered": delivered_count,
                "total": total_count,
                "cellId": robot["currentCellId"],
            },
        )
        if carrying:
            robot["handlingAction"] = {
                "type": "deliver",
                "stage": "lifting",
                "targetLayer": 1,
                "slotIndex": len(carrying) - 1,
                "cellId": robot["currentCellId"],
            }
            robot["status"] = "handling"
            robot["phaseLabel"] = f"逐箱交付 {delivered_count}/{total_count} · 下一箱升降对位"
            task["phaseLabel"] = robot["phaseLabel"]
            self._record_event(runtime, tick, "ctu_handling", {"robotId": robot["id"], **robot["handlingAction"]})
            return

        robot["handlingAction"] = None
        if task.get("taskKind") == "relocate":
            # 倒库交付：货箱放入目标空货道（写回带深度库存），不走上带/拣货链路。
            self._complete_shuttle_relocate(runtime, robot, task)
            return
        zone = self._pick_station_for(runtime, task)
        if zone is not None:
            # 逐箱流水：交付完成后按货箱拆成多个单箱拣货子任务，父任务立即释放机器人。
            # 每箱独立走 升降 → 上层 → 拣货台 → 分裂；升降/拣货台是一次一箱资源，
            # 后续箱靠块容量检查与升降仲裁自然排队。
            self._spawn_picking_box_tasks(runtime, robot, task, zone, occupied_cells)
        else:
            self._complete_task(runtime, robot, task, occupied_cells)

    def _complete_shuttle_relocate(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
    ) -> None:
        """倒库交付：货箱写入目标空货道（depth=1 外层面），任务完成。"""
        carrying = robot.get("carrying") or []
        box = dict(carrying[0]) if carrying else {}
        inventory = runtime.state.setdefault("inventory", [])
        inventory.append({
            **box,
            "cellId": task.get("deliveryCellId"),
            "layer": int(task.get("targetLayer", 1)),
            "depth": int(task.get("relocateDepth", 1)),
        })
        robot["carrying"] = []
        robot["load"] = 0
        tick = runtime.state["tick"]
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {
                "taskId": task["id"],
                "phase": task["phase"],
                "status": task["status"],
                "relocateTo": task.get("deliveryCellId"),
                "relocateDepth": int(task.get("relocateDepth", 1)),
            },
        )
        self._complete_task(runtime, robot, task)

    def _pick_station_for(self, runtime: RunRuntime, task: dict[str, Any]) -> dict[str, Any] | None:
        """为任务选择人工拣货位（取离交付出入口最近的一个）；仅 CTU 任务启用。

        拓扑没有显式 pick_station 区域时，回退用同一 assignedPortId 作为虚拟拣货位——
        让输送线链路（conveyor_to_lift → lift_to_upper → conveyor_to_pick → picking →
        place_goods 分裂 → conveyor_return 回库）能启动，货箱全程由 transport 逐块同步。
        """
        if task.get("robotType") != "ctu":
            return None
        zones = [
            zone
            for zone in runtime.topology.get("zones", [])
            if zone.get("type") == "pick_station"
        ]
        if not zones:
            port_zone_id = task.get("assignedPortId")
            if port_zone_id:
                port_zones = [
                    zone
                    for zone in runtime.topology.get("zones", [])
                    if zone.get("id") == port_zone_id
                ]
                if port_zones:
                    return port_zones[0]
            return None
        try:
            _, row, col = parse_cell_id(task.get("deliveryCellId") or "")
        except (TypeError, ValueError):
            return zones[0]

        def distance(zone: dict[str, Any]) -> int:
            entry_row = int(zone["y"]) + int(zone["height"]) - 1
            entry_col = int(zone["x"]) + int(zone["width"]) // 2
            return abs(entry_row - row) + abs(entry_col - col)

        return min(zones, key=distance)

    def _drop_zone_for(self, runtime: RunRuntime, task: dict[str, Any]) -> dict[str, Any] | None:
        """为任务选择放置区（取离拣货位入口格最近的一个）；没有 drop_zone 区域时返回 None。"""
        zones = [
            zone
            for zone in runtime.topology.get("zones", [])
            if zone.get("type") == "drop_zone"
        ]
        if not zones:
            return None
        try:
            _, row, col = parse_cell_id(task.get("pickStationCellId") or task.get("deliveryCellId") or "")
        except (TypeError, ValueError):
            return zones[0]

        def distance(zone: dict[str, Any]) -> int:
            entry_row = int(zone["y"]) + int(zone["height"]) - 1
            entry_col = int(zone["x"]) + int(zone["width"]) // 2
            return abs(entry_row - row) + abs(entry_col - col)

        return min(zones, key=distance)

    def _prepare_conveyor_transport(
        self,
        runtime: RunRuntime,
        task: dict[str, Any],
        phase: str,
    ) -> int | None:
        """为显式分层输送模型创建逐块运输状态；旧拓扑继续使用原固定 tick。

        除 conveyor_to_lift / conveyor_return 两条滑动路线外，lift_to_upper /
        conveyor_to_pick / picking / place_goods 也挂 transport（静态或短滑行），
        让前端全程用 transportPoint 定位货箱：货箱在下层终点（升降机）平滑抬升、
        沿上层滑到拣货台并停住，避免阶段切换时回退到入口重播动画造成"消失/凭空出现"。
        """
        if not runtime.conveyor_graph.get("explicit"):
            task["conveyorTransport"] = None
            return None
        if phase == "lift_to_upper":
            # 升降机抬升：等待升降权期间货箱停在下层注入块（不下水到上层），
            # 拿到升降权后由驱动器把 deck 翻转为 upper，货箱才平滑抬升。
            lift_block = task.get("liftBlockId")
            if not lift_block:
                task["conveyorTransport"] = None
                return None
            node_id = str(lift_block) if str(lift_block).endswith(":lower") else f"{lift_block}:lower"
            task["conveyorTransport"] = {
                "phase": phase,
                "deck": "lower",
                "route": [node_id],
                "index": 0,
                "currentBlockId": node_id,
                "nextBlockId": None,
                "waitingTicks": 0,
                "movedTicks": 0,
                "startedTick": int(runtime.state["tick"]),
            }
            return None  # 保持默认 liftTicks 计时；transport 仅用于前端定位
        if phase == "conveyor_to_pick":
            # 上层滑行：从升降设备块滑回上层线首，再向前走到拣货台
            # （本布局：94 设备 → 92 → 90 → 88 拣货台，共 4 块）。
            lift_block = task.get("liftBlockId")
            if not lift_block:
                task["conveyorTransport"] = None
                return None
            route = route_from_block(runtime.conveyor_graph, str(lift_block), "upper")[:3]
            if not route:
                task["conveyorTransport"] = None
                return None
            device_block = self._lift_device_block(runtime, task)
            if device_block:
                # 路线起点为升降设备块：货箱从设备上平滑滑入上层线首
                route = [f"{device_block}:upper"] + route
            task["conveyorTransport"] = {
                "phase": phase,
                "deck": "upper",
                "route": route,
                "index": 0,
                "currentBlockId": route[0],
                "nextBlockId": route[1] if len(route) > 1 else None,
                "waitingTicks": 0,
                "movedTicks": 0,
                "startedTick": int(runtime.state["tick"]),
            }
            return max(1, len(route))
        if phase in ("picking", "place_goods"):
            # 拣货/放置：货箱停在拣货台块上（"停几秒钟"），transport 仅用于前端定位。
            station_block = task.get("stationBlockId")
            if not station_block:
                task["conveyorTransport"] = None
                return None
            node_id = str(station_block) if str(station_block).endswith(":upper") else f"{station_block}:upper"
            task["conveyorTransport"] = {
                "phase": phase,
                "deck": "upper",
                "route": [node_id],
                "index": 0,
                "currentBlockId": node_id,
                "nextBlockId": None,
                "waitingTicks": 0,
                "movedTicks": 0,
                "startedTick": int(runtime.state["tick"]),
            }
            return None  # 保持 pickTicks / placeTicks 计时
        if phase == "pickup_from_port":
            # CTU 到回库口取回期间，货箱视觉上停在回库口块（上层），
            # 直到被 CTU 取走（_advance_pickup_from_port 会清掉 transport）。
            end_block = task.get("returnEndBlockId")
            if not end_block:
                task["conveyorTransport"] = None
                return None
            node_id = str(end_block) if str(end_block).endswith(":upper") else f"{end_block}:upper"
            task["conveyorTransport"] = {
                "phase": phase,
                "deck": "upper",
                "route": [node_id],
                "index": 0,
                "currentBlockId": node_id,
                "nextBlockId": None,
                "waitingTicks": 0,
                "movedTicks": 0,
                "startedTick": int(runtime.state["tick"]),
            }
            return None  # 等待 CTU 到达取回，无固定计时
        if phase == "conveyor_to_lift":
            deck = "lower"
            reference_cell = task.get("deliveryCellId")
            zone_id = task.get("assignedPortId")
        elif phase == "conveyor_return":
            deck = "upper"
            reference_cell = task.get("pickStationCellId") or task.get("deliveryCellId")
            zone_id = task.get("pickStationZoneId")
        else:
            task["conveyorTransport"] = None
            return None
        zone = next(
            (item for item in runtime.topology.get("zones", []) if item.get("id") == zone_id),
            None,
        )
        binding = zone.get("conveyorBinding") if isinstance(zone, dict) else None
        route = route_from_block(
            runtime.conveyor_graph,
            binding.get("blockId") if isinstance(binding, dict) else None,
            deck,
        )
        if not route:
            route = route_from_cell(runtime.conveyor_graph, reference_cell, deck, ground=False)
        if not route:
            task["conveyorTransport"] = None
            return None
        # 回库支线：沿图上正式分叉（(84,c) 向北 → (82,c) → 回库口链），
        # 货箱从拣货台 (88,c) 起全程可见地滑行到回库口。
        # 注意不能用 route_from_cell 兜底：分叉加入后回库段起点不再是源头节点，
        # route_from_cell 会找到送货源头 92:c 而返回整条送货路线。
        if phase == "conveyor_return":
            station_block = task.get("stationBlockId")
            if station_block:
                parts = str(station_block).split(":")
                if len(parts) >= 3:
                    s_row, s_col = int(parts[1]), int(parts[2])
                    branch_route = route_from_block_branch(
                        runtime.conveyor_graph,
                        str(station_block),
                        deck,
                        branch_side="north",
                    )
                    if branch_route and branch_route[0] == f"{station_block}:upper":
                        last_parts = branch_route[-1].split(":")
                        # 校验终点：与拣货台同列、位于其北侧（回库口），不是送货主线
                        if int(last_parts[2]) == s_col and int(last_parts[1]) < s_row:
                            route = branch_route
        # index 从 0 起步（currentBlockId=route[0]）：货箱第一帧就出现在线首，
        # 避免 index=-1 时前端 transportPoint 为空回退重播动画造成的闪烁。
        task["conveyorTransport"] = {
            "phase": phase,
            "deck": deck,
            "route": route,
            "index": 0,
            "currentBlockId": route[0],
            "nextBlockId": route[1] if len(route) > 1 else None,
            "waitingTicks": 0,
            "movedTicks": 0,
            "startedTick": int(runtime.state["tick"]),
        }
        return max(1, len(route))

    def _find_pick_station_block(self, runtime: RunRuntime, pick_cell_id: str) -> str | None:
        """从 pickStationCellId（port zone entry cell）找到对应的拣货台上输送块 block_id。

        port zone entry cell 在 row~73，而拣货台上输送块在 row~88（row 值更大）。
        两者 col 接近（±5）。从输送线 upper deck nodes 中选 col 最接近且 row 最大的，
        就能找到拣货台 anchor block。返回不带 deck 后缀的 block_id，如 "1F:88:66"。
        """
        try:
            _, ref_row, ref_col = parse_cell_id(pick_cell_id)
        except (TypeError, ValueError):
            return None
        nodes = runtime.conveyor_graph.get("nodes", {})
        upper_nodes = [
            node for node in nodes.values()
            if node.get("deck") == "upper" and not node.get("ground")
        ]
        if not upper_nodes:
            return None
        # 先按 col 距离排序，选 col 差距 ≤ 5 的（或取 col 最接近的）
        def col_dist(node: dict[str, Any]) -> int:
            return abs(int(node.get("col", 0)) - ref_col)
        sorted_by_col = sorted(upper_nodes, key=col_dist)
        # 取 col 最接近的前几名，在其中找 row 最大的（拣货台 anchor row 通常最大）
        top_col = sorted_by_col[: max(3, len(sorted_by_col) // 4)]
        best = max(top_col, key=lambda n: int(n.get("row", 0)))
        node_id = best.get("id", "")
        # node_id 格式 "1F:88:66:upper" → 去掉最后一个 ":upper" 得到 "1F:88:66"
        if node_id.endswith(":upper"):
            return node_id[: -len(":upper")]
        return node_id

    def _delivery_output_ports(self, runtime: RunRuntime, route: list[str]) -> list[str]:
        """识别主出货线末端竖段上的侧向出货口块。

        本拓扑主干沿 col 60 向南延伸，102/106/110/114/118 行的 col 62 块是
        侧向出货口（帽口）。判定口径：竖段某一行的侧向相邻块 next 指回该行
        主干块。按行号升序返回，保证同一个口在所有线路上的顺序一致。
        """
        nodes = runtime.conveyor_graph.get("nodes", {})
        if not route or not nodes:
            return []
        last = nodes.get(route[-1])
        if not isinstance(last, dict):
            return []
        trunk_col = int(last.get("col", 0))
        floor = str(last.get("floor") or "1F")
        trunk: list[str] = []
        for block_id in reversed(route):
            node = nodes.get(block_id)
            if not isinstance(node, dict) or int(node.get("col", 0)) != trunk_col:
                break
            trunk.append(block_id)
        ports: list[str] = []
        route_set = set(route)
        # 真正的出货口是挂在竖段上的死端支线：没有任何上游块指向它。
        # 多线汇入点（如 84:58 由 84:56 喂入）不是出货口。
        fed_blocks = {
            str(node.get("next"))
            for node in nodes.values()
            if isinstance(node, dict) and node.get("next")
        }
        for block_id in trunk:
            row = int(nodes[block_id].get("row", 0))
            for step in (2, -2):
                neighbor_id = f"{floor}:{row}:{trunk_col + step}:upper"
                neighbor = nodes.get(neighbor_id)
                # 只看侧向口：不能把路线自身的转弯段（如 84:62）误判成出货口。
                if neighbor_id in route_set or not isinstance(neighbor, dict):
                    continue
                if neighbor.get("next") == block_id and neighbor_id not in fed_blocks:
                    ports.append(neighbor_id)
        ports.sort(key=lambda item: int(nodes[item].get("row", 0)))
        return ports

    def _output_port_for_order(self, runtime: RunRuntime, order_id: Any, ports: list[str]) -> str | None:
        """同一订单固定使用同一出货口；不同订单优先均摊到空闲的口。"""
        if not ports:
            return None
        assignments = runtime.state.setdefault("outputPortAssignments", {})
        if not isinstance(assignments, dict):
            assignments = {}
            runtime.state["outputPortAssignments"] = assignments
        order_key = str(order_id or "")
        assigned = assignments.get(order_key) if order_key else None
        if assigned in ports:
            return str(assigned)
        nodes = runtime.conveyor_graph.get("nodes", {})
        usage = {port: 0 for port in ports}
        for value in assignments.values():
            if value in usage:
                usage[value] += 1
        chosen = min(ports, key=lambda port: (usage[port], int(nodes.get(port, {}).get("row", 0))))
        if order_key:
            assignments[order_key] = chosen
        return chosen

    def _route_to_output_port(self, runtime: RunRuntime, route: list[str], port_block: str) -> list[str]:
        """把出货主干路线截断到该出货口所在行，再接入侧向出货口块。"""
        nodes = runtime.conveyor_graph.get("nodes", {})
        port_node = nodes.get(port_block)
        if not isinstance(port_node, dict) or not route:
            return route
        port_row = int(port_node.get("row", 0))
        for index in range(len(route) - 1, -1, -1):
            node = nodes.get(route[index])
            if isinstance(node, dict) and int(node.get("row", 0)) == port_row:
                return route[: index + 1] + [port_block]
        return route

    def _spawn_placed_goods_delivery(self, runtime: RunRuntime, parent_task: dict[str, Any]) -> None:
        """Picking 分裂：已拣部分（placedGoods）创建 forward 输送线子任务。

        从拣货台对应的上层输送块出发，沿 next 方向走到该订单固定的出货口
        （主出货线末端竖段上的侧向口，如同订单走 118:62，另一单走 114:62）。
        这是一个纯输送线任务——没有 robot 参与，只由 conveyorTransport 推进。
        """
        placed = parent_task.get("placedGoods") or []
        if not placed:
            return
        pick_cell = parent_task.get("pickStationCellId")
        if not pick_cell:
            return

        # 找到拣货台对应的上层输送块 block_id。
        # 优先用父任务在 conveyor_to_pick 结束时记录的 stationBlockId（拣货台 anchor，
        # 如 1F:88:66）；没有时回退到按 pickStationCellId 列对齐 + 最大行的启发式。
        station_block = parent_task.get("stationBlockId")
        if station_block:
            route = route_from_block(runtime.conveyor_graph, str(station_block), "upper")
        else:
            block_id = self._find_pick_station_block(runtime, pick_cell)
            route = route_from_block(runtime.conveyor_graph, block_id, "upper") if block_id else []
        if not route:
            return  # 没有可走的 forward 路线 → 放弃（不会发货丢失，任务至少完成了回库）
        # 出货口：同一订单的所有已拣箱固定走同一个侧向出货口。
        port_block = self._output_port_for_order(
            runtime,
            parent_task.get("orderId"),
            self._delivery_output_ports(runtime, route),
        )
        port_cell_id: str | None = None
        if port_block:
            route = self._route_to_output_port(runtime, route, port_block)
            port_node = runtime.conveyor_graph.get("nodes", {}).get(port_block, {})
            port_cell_id = cell_id(
                int(port_node.get("row", 0)),
                int(port_node.get("col", 0)),
                str(port_node.get("floor") or "1F"),
            )
        child_id = f"{parent_task['id']}-placed-fwd"
        # 确保不重复创建
        if any(t.get("id") == child_id for t in runtime.state.get("tasks", [])):
            return
        child_task: dict[str, Any] = {
            "id": child_id,
            "parentId": parent_task["id"],
            "taskKind": "placed_goods_delivery",
            "robotType": "none",  # 纯输送线，无 robot
            "status": "assigned",
            "phase": "arm_conveyor_delivery",
            "phaseLabel": "已拣货物上层输送",
            "lines": [],
            "transitGoods": [dict(slot) for slot in placed],
            "placedGoods": [],
            "deliveredGoods": [],
            "picked": 0,
            "history": [{"tick": runtime.state["tick"], "phase": "arm_conveyor_delivery", "status": "assigned"}],
            "pickStationCellId": pick_cell,
            "assignedPortId": parent_task.get("assignedPortId"),
            # 已拣货物的实际交付点：订单固定的侧向出货口；无法识别时回退原交付点。
            "deliveryCellId": port_cell_id or parent_task.get("deliveryCellId"),
            "outputPortBlockId": port_block,
            "outputPortCellId": port_cell_id,
            "createdTick": runtime.state["tick"],
            "startedTick": runtime.state["tick"],
            "completedTick": None,
            "urgent": bool(parent_task.get("urgent")),
            "orderId": parent_task.get("orderId"),
        }
        child_task["pendingTransport"] = {
            "phase": "arm_conveyor_delivery",
            "deck": "upper",
            "route": route,
            "index": 0,
            "currentBlockId": route[0],
            "nextBlockId": route[1] if len(route) > 1 else None,
            "waitingTicks": 0,
            "movedTicks": 0,
            "startedTick": runtime.state["tick"],
            "travelProgressSeconds": 0.0,
        }
        runtime.state.setdefault("tasks", []).append(child_task)
        self._record_event(
            runtime,
            runtime.state["tick"],
            "task_spawned",
            {"taskId": child_id, "parentId": parent_task["id"], "phase": "arm_conveyor_delivery", "placedGoodsCount": len(placed), "routeLen": len(route)},
        )

    def _spawn_picking_box_tasks(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
        zone: dict[str, Any],
        occupied_cells: set[str] | None = None,
    ) -> None:
        """逐箱流水：把交付的全部货箱拆成单箱拣货子任务，父任务立即完成并释放机器人。

        每个子任务只携带一箱（1 slot），错开 startedTick 依次启动；后续箱在升降机
        前的下层块与拣货台前的上层块靠容量检查自动排队，升降互斥由
        _lift_stage_has_priority 仲裁。
        """
        delivered = task.get("deliveredGoods") or robot.get("carrying") or []
        if not delivered:
            self._complete_task(runtime, robot, task, occupied_cells)
            return
        pick_cell = pick_station_entry_cell(runtime.topology, zone)
        tick = int(runtime.state["tick"])
        pick_list = task.get("pickPiecesList") or []
        for index, slot in enumerate(delivered):
            child_id = f"{task['id']}-pick-{index}"
            if any(t.get("id") == child_id for t in runtime.state.get("tasks", [])):
                continue
            # 该箱需要拣出的件数：订单尾箱只拣尾数，其余整箱按整箱件数拣。
            pick_pieces = (
                max(1, int(pick_list[index]))
                if index < len(pick_list) and pick_list[index]
                else max(1, int(task.get("pickPieces") or 1))
            )
            child: dict[str, Any] = {
                "id": child_id,
                "parentId": task["id"],
                "taskKind": "picking_box",
                "robotType": "none",  # 纯输送线任务，无 robot，由 detached 驱动器推进
                "status": "assigned",
                "phase": "conveyor_to_lift",
                "phaseLabel": self._phase_label("conveyor_to_lift"),
                "lines": [dict(slot)],
                "transitGoods": [dict(slot)],
                "placedGoods": [],
                "deliveredGoods": [],
                "picked": 0,
                "pickPieces": pick_pieces,
                "history": [{"tick": tick, "phase": "conveyor_to_lift", "status": "assigned"}],
                "pickStationZoneId": zone.get("id"),
                "pickStationCellId": pick_cell,
                "assignedPortId": task.get("assignedPortId"),
                "deliveryCellId": task.get("deliveryCellId"),
                "createdTick": tick,
                "startedTick": tick + index,  # 错开启动，逐箱进入输送线
                "completedTick": None,
                "urgent": bool(task.get("urgent")),
                "orderId": task.get("orderId"),
            }
            self._prepare_conveyor_transport(runtime, child, "conveyor_to_lift")
            transport = child.get("conveyorTransport")
            if isinstance(transport, dict):
                # 占块门控上线：先存 pendingTransport，等路线首块空闲才显示/推进，
                # 避免多箱在同一源头块重叠"凭空出现"。
                child["pendingTransport"] = child.pop("conveyorTransport")
                child["pendingTransport"]["startedTick"] = child["startedTick"]
            elif runtime.conveyor_graph.get("explicit"):
                # 显式分层模式下没有可走的下层路线：标记失败，避免任务永久挂起。
                child["status"] = "failed"
                child["phase"] = "failed"
                child["failureReason"] = "conveyor_route_missing"
                child["completedTick"] = tick
            # else: 旧拓扑无显式输送图，纯计时推进（驱动器回退路径）
            runtime.state.setdefault("tasks", []).append(child)
            self._record_event(
                runtime,
                tick,
                "task_spawned",
                {"taskId": child_id, "parentId": task["id"], "taskKind": "picking_box", "slotIndex": index, "startedTick": child["startedTick"]},
            )
        # 父任务进入"喂线"状态：机器人停在出货口旁，直到所有子箱都上线才释放。
        # 这样每箱上线时 CTU 都在场，货箱不会在无车状态下凭空出现在输送线上。
        task["transitGoods"] = []
        task["deliveredGoods"] = [dict(slot) for slot in delivered]
        task["pickingDelegated"] = True  # 前端据此跳过父任务的放置区幽灵渲染
        robot["carrying"] = []
        robot["load"] = 0
        tick = int(runtime.state["tick"])
        task["phase"] = "feeding_conveyor"
        task["phaseLabel"] = self._phase_label("feeding_conveyor")
        robot["status"] = "waiting"
        robot["handlingAction"] = None
        robot["path"] = [robot["currentCellId"]]
        robot["pathLayers"] = [robot.get("currentLayer", 1)]
        robot["phaseLabel"] = task["phaseLabel"]
        task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {"taskId": task["id"], "phase": task["phase"], "status": task["status"]},
        )
        # 不要把车停在出入口堵住后面的车；喂线期间原地等待。
        self._step_off_port(runtime, robot, occupied_cells)

    def _activate_child_transport(self, runtime: RunRuntime, task: dict[str, Any]) -> bool:
        """把子任务的 pendingTransport 升级为正式 transport（"上线"）。

        仅当路线首块空闲时才允许上线：前箱还在首块时，后箱保持 pending（前端不渲染），
        前箱前进一格后后箱出现在腾出的位置——货箱逐个出现，绝不在同一块上重叠。
        """
        pending = task.get("pendingTransport")
        if not isinstance(pending, dict):
            return False
        head_block = str(pending.get("route", [""])[0])
        deck = pending.get("deck")
        for other in runtime.state.get("tasks", []):
            if other.get("id") == task.get("id") or other.get("status") in TERMINAL_TASK_STATUSES:
                continue
            other_transport = other.get("conveyorTransport")
            if (
                isinstance(other_transport, dict)
                and str(other_transport.get("currentBlockId")) == head_block
                and other_transport.get("deck") == deck
            ):
                return False
        activated = dict(pending)
        activated["startedTick"] = int(runtime.state["tick"])
        activated["waitingTicks"] = 0
        activated["movedTicks"] = 0
        task["conveyorTransport"] = activated
        task.pop("pendingTransport", None)
        self._record_event(
            runtime,
            int(runtime.state["tick"]),
            "task_state_changed",
            {"taskId": task["id"], "phase": task.get("phase"), "status": task.get("status"), "conveyorEntered": head_block},
        )
        return True

    def _advance_feeding_parents(self, runtime: RunRuntime) -> None:
        """喂线中的父任务：所有子箱都已上线（或终结）后完成父任务并释放机器人。"""
        for task in runtime.state.get("tasks", []):
            if task.get("phase") != "feeding_conveyor" or task.get("status") in TERMINAL_TASK_STATUSES:
                continue
            children = [
                t for t in runtime.state.get("tasks", [])
                if t.get("parentId") == task["id"] and t.get("taskKind") == "picking_box"
            ]
            if not children:
                continue
            all_entered = all(
                t.get("status") in TERMINAL_TASK_STATUSES
                or isinstance(t.get("conveyorTransport"), dict)
                or t.get("phase") != "conveyor_to_lift"
                for t in children
            )
            if not all_entered:
                continue
            robot = next(
                (item for item in runtime.state.get("robots", []) if item.get("currentTaskId") == task["id"]),
                None,
            )
            if robot is None:
                # 机器人已不挂在此任务（异常兜底）：直接收尾，避免喂线状态卡死。
                tick = int(runtime.state["tick"])
                task["status"] = "completed"
                task["phase"] = "completed"
                task["phaseLabel"] = self._phase_label("completed")
                task["completedTick"] = tick
                task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
                self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "phase": "completed", "status": "completed"})
                continue
            self._complete_task(runtime, robot, task)

    def _advance_detached_picking_tasks(self, runtime: RunRuntime) -> None:
        """推进无机器人（detached）的单箱拣货任务：下层去程 → 升降 → 上层 → 拣货 → 放置分裂。"""
        tick = int(runtime.state["tick"])
        for task in runtime.state.get("tasks", []):
            if task.get("taskKind") != "picking_box" or task.get("status") in TERMINAL_TASK_STATUSES:
                continue
            if int(task.get("startedTick", 0)) > tick:
                continue  # 错开启动：后续箱依次进入输送线
            self._advance_detached_picking_task(runtime, task)

    def _advance_detached_picking_task(self, runtime: RunRuntime, task: dict[str, Any]) -> None:
        """单个 detached 拣货任务的逐 tick 推进（与机器人驱动版阶段机等价，robot=None）。"""
        phase = task.get("phase")
        if phase in {"conveyor_to_lift", "conveyor_to_pick"}:
            transport = task.get("conveyorTransport")
            if transport is None and task.get("pendingTransport") is not None and phase == "conveyor_to_lift":
                # 占块门控上线：路线首块空闲才出现（前箱已前进一格），避免源头重叠。
                self._activate_child_transport(runtime, task)
                return
            if isinstance(transport, dict):
                if phase == "conveyor_to_pick" and not self._station_clear_for_entry(runtime, task, transport):
                    # 站台公平性：前一箱的出货/回库子任务还没放行，本箱留在 (90,c) 排队，
                    # 不抢占拣货台块——避免分裂子任务被饿到最后一箱拣完才集中放出。
                    transport["waitingTicks"] = int(transport.get("waitingTicks", 0)) + 1
                    return
                if not self._advance_conveyor_transport(runtime, None, task):
                    return
                current_block = str(transport.get("currentBlockId") or "")
                if phase == "conveyor_to_lift":
                    # 记录升降注入块，供 lift_to_upper / conveyor_to_pick 定位。
                    task["liftBlockId"] = current_block.removesuffix(":lower")
                else:
                    # 记录拣货台块，供 picking / place_goods 停靠与分裂子任务起点使用。
                    task["stationBlockId"] = current_block.removesuffix(":upper")
                task["conveyorTransport"] = None
                self._advance_detached_stage(runtime, task)
                return
            # 旧拓扑（无显式输送图）：纯计时推进。
            remaining = int(task.get("remainingStageTicks", 0)) - 1
            task["remainingStageTicks"] = max(0, remaining)
            if remaining > 0:
                return
            self._advance_detached_stage(runtime, task)
            return
        if phase == "lift_to_upper":
            if not self._lift_stage_has_priority(runtime, task):
                return  # 升降机被其它箱占用：留在下层等待
            # 升降编排：拿到升降权后货箱先滑上升降机设备块，
            # 计时末段再在设备上抬升到上层（deck 翻转为 upper）。
            lift_transport = task.get("conveyorTransport")
            if isinstance(lift_transport, dict) and lift_transport.get("deck") != "upper":
                device_block = self._lift_device_block(runtime, task)
                if device_block:
                    device_lower = f"{device_block}:lower"
                    if lift_transport.get("currentBlockId") != device_lower:
                        lift_transport["currentBlockId"] = device_lower
                        lift_transport["route"] = [device_lower]
                if int(task.get("remainingStageTicks", 0)) <= 1:
                    # 末段抬升
                    lift_transport["deck"] = "upper"
                    if device_block:
                        device_upper = f"{device_block}:upper"
                        lift_transport["currentBlockId"] = device_upper
                        lift_transport["route"] = [device_upper]
                    else:
                        base = str(lift_transport.get("currentBlockId") or "").removesuffix(":lower")
                        lift_transport["currentBlockId"] = base + ":upper"
                        lift_transport["route"] = [base + ":upper"]
            remaining = int(task.get("remainingStageTicks", 0)) - 1
            task["remainingStageTicks"] = max(0, remaining)
            if remaining > 0:
                return
            self._advance_detached_stage(runtime, task)
            return
        if phase in {"picking", "place_goods"}:
            remaining = int(task.get("remainingStageTicks", 0)) - 1
            task["remainingStageTicks"] = max(0, remaining)
            if remaining > 0:
                return
            if phase == "picking":
                self._finish_picking(runtime, task, None)
            else:
                # 放置结束 → 分裂：已拣部分去出货口，货箱（含空箱）回库。
                self._split_picking_box(runtime, task)
            return

    def _station_clear_for_entry(self, runtime: RunRuntime, task: dict[str, Any], transport: dict[str, Any]) -> bool:
        """拣货台进入公平性：还有更早的分裂子任务（出货/回库）未上线时，本箱不得进站台。

        分裂子任务的路线首块就是拣货台块；它们靠"站台空闲"门控上线。若下一箱
        在前一箱拣完的同一 tick 抢占站台，前箱的出货箱/回库箱会被饿到很久之后
        才集中出现。此门控强制：先放行前箱的分裂子任务，再让下一箱进站台。
        """
        route = transport.get("route") or []
        index = int(transport.get("index", 0))
        if index + 1 >= len(route):
            return True  # 已在站台内，无"进入"动作
        next_block = str(route[index + 1])
        for other in runtime.state.get("tasks", []):
            if other.get("id") == task.get("id") or other.get("status") in TERMINAL_TASK_STATUSES:
                continue
            if other.get("taskKind") not in ("placed_goods_delivery", "picking_return"):
                continue
            if other.get("conveyorTransport") is not None:
                continue  # 已上线（在线上滑行或已到终点），由容量检查接管
            pending = other.get("pendingTransport")
            if isinstance(pending, dict) and str(pending.get("route", [""])[0]) == next_block:
                return False
        return True

    def _advance_detached_stage(self, runtime: RunRuntime, task: dict[str, Any]) -> None:
        """detached 任务的阶段链接续（conveyor_to_lift → lift_to_upper → conveyor_to_pick → picking）。"""
        phase = task.get("phase")
        if phase == "conveyor_to_lift":
            lift_ticks = max(1, int(runtime.config.get("liftTicks", 3) or 3))
            self._enter_timed_stage(runtime, None, task, "lift_to_upper", lift_ticks)
            return
        if phase == "lift_to_upper":
            transfer_ticks = 1 if runtime.conveyor_graph.get("explicit") else max(
                1, int(runtime.config.get("conveyorTransferTicks", 6) or 6)
            )
            self._enter_timed_stage(runtime, None, task, "conveyor_to_pick", transfer_ticks)
            return
        if phase == "conveyor_to_pick":
            self._start_picking(runtime, None, task)
            return

    def _split_picking_box(self, runtime: RunRuntime, task: dict[str, Any]) -> None:
        """单箱拣货任务的放置阶段结束：已拣部分（1 件）创建 forward 出货子任务，
        货箱本体（无论剩余多少、是否已空）创建回库子任务，随后本任务完成。"""
        transit = task.get("transitGoods") or []
        if transit:
            slot = dict(transit[0])
            slot_qty = max(1, int(slot.get("qty", 1) or 1))
            # 订单尾箱只拣所需件数（如整箱 20 件、只拣 10 件），其余留在箱内回库。
            pick_qty = min(slot_qty, max(1, int(task.get("pickPieces") or 1)))
            task["placedGoods"] = [dict(slot, qty=pick_qty)]
            task["transitGoods"] = [dict(slot, qty=slot_qty - pick_qty)]
        else:
            task["placedGoods"] = []
            task["transitGoods"] = []
        if task.get("placedGoods"):
            # 已拣部分：每箱各自一个 fwd 子任务，不与其它箱合并。
            self._spawn_placed_goods_delivery(runtime, task)
        # 回库：拣货完毕后每箱都要有回库货箱（空箱也回库）。
        self._spawn_picking_return(runtime, task)
        self._finish_detached_task(runtime, task)

    def _spawn_picking_return(self, runtime: RunRuntime, task: dict[str, Any]) -> None:
        """创建回库子任务：沿拣货台北侧回库支线滑回回库口，等待 CTU 取回并送回原库位。"""
        station_block = task.get("stationBlockId")
        if not station_block:
            return  # 旧拓扑没有回库支线：跳过回库子任务（任务链仍可完成）
        branch = route_from_block_branch(
            runtime.conveyor_graph,
            str(station_block),
            "upper",
            branch_side="north",
        )
        # 无分叉时 route_from_block_branch 退化为主线（终点是出货口）：
        # 校验终点与拣货台同列且位于其北侧，避免回库箱错开去出货口。
        last_parts = branch[-1].split(":") if branch else []
        parts = str(station_block).split(":")
        if (
            not branch
            or branch[0] != f"{station_block}:upper"
            or len(branch) < 2
            or len(last_parts) < 3
            or len(parts) < 3
            or int(last_parts[2]) != int(parts[2])
            or int(last_parts[1]) >= int(parts[1])
        ):
            return
        child_id = f"{task['id']}-return"
        if any(t.get("id") == child_id for t in runtime.state.get("tasks", [])):
            return
        slot = (task.get("transitGoods") or [{}])[0]
        child: dict[str, Any] = {
            "id": child_id,
            "parentId": task["id"],
            "taskKind": "picking_return",
            "robotType": "none",  # 先纯输送线滑回回库口，再由 CTU 取回
            "status": "assigned",
            "phase": "arm_conveyor_delivery",
            "phaseLabel": "货箱回库输送",
            "lines": [dict(slot)],
            "transitGoods": [dict(slot)],  # 剩余数量可为 0：空箱回库
            "placedGoods": [],
            "deliveredGoods": [],
            "picked": 0,
            "history": [{"tick": int(runtime.state["tick"]), "phase": "arm_conveyor_delivery", "status": "assigned"}],
            "pickStationCellId": task.get("pickStationCellId"),
            "stationBlockId": str(station_block),
            "assignedPortId": task.get("assignedPortId"),
            "deliveryCellId": task.get("deliveryCellId"),
            "createdTick": int(runtime.state["tick"]),
            "startedTick": int(runtime.state["tick"]),
            "completedTick": None,
            "urgent": bool(task.get("urgent")),
            "orderId": task.get("orderId"),
        }
        child["pendingTransport"] = {
            "phase": "arm_conveyor_delivery",
            "deck": "upper",
            "route": branch,
            "index": 0,
            "currentBlockId": branch[0],
            "nextBlockId": branch[1],
            "waitingTicks": 0,
            "movedTicks": 0,
            "startedTick": int(runtime.state["tick"]),
            "travelProgressSeconds": 0.0,
        }
        runtime.state.setdefault("tasks", []).append(child)
        self._record_event(
            runtime,
            int(runtime.state["tick"]),
            "task_spawned",
            {"taskId": child_id, "parentId": task["id"], "taskKind": "picking_return", "routeLen": len(branch)},
        )

    def _finish_detached_task(self, runtime: RunRuntime, task: dict[str, Any]) -> None:
        """detached 任务收尾：无机器人参与，子任务不重复累加指标。"""
        tick = int(runtime.state["tick"])
        task["status"] = "completed"
        task["phase"] = "completed"
        task["phaseLabel"] = self._phase_label("completed")
        task["completedTick"] = tick
        task["remainingStageTicks"] = 0
        task["conveyorTransport"] = None
        if not task.get("parentId"):
            runtime.metrics["completedTasks"] += 1
            runtime.metrics["throughput"] += 1
        task.setdefault("history", []).append({"tick": tick, "phase": task["phase"], "status": task["status"]})
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {"taskId": task["id"], "phase": task["phase"], "status": task["status"]},
        )

    def _assign_pickup_returns(self, runtime: RunRuntime) -> None:
        """给已在回库口等待的回库子任务分配空闲 CTU：取回货箱并按原库位回库。

        批量取货：一台 CTU 认领同一回库口的"已到口箱 + 在途排队箱"（总量不超过
        CTU 容量）。CTU 到口后由输送线逐箱喂到位、逐箱装车，然后一趟按各自
        原库位回送——避免一箱一趟的低效往返。 followers 记录在
        robot.returnPickupQueue，装载逻辑见 _advance_pickup_from_port。
        在 _assign_tasks 开头调用，保证回库取回优先于新任务派单。
        """
        tasks = runtime.state.get("tasks", [])
        capacity = ROBOT_CARRY_CAPACITY.get("ctu", 5)
        waiting = [
            task for task in tasks
            if task.get("taskKind") == "picking_return"
            and task.get("phase") == "pickup_from_port"
            and task.get("status") not in TERMINAL_TASK_STATUSES
            and not task.get("assignedRobotId")
        ]
        if not waiting:
            return
        tick = int(runtime.state["tick"])
        for leader in waiting:
            try:
                _, port_row, port_col = parse_cell_id(str(leader.get("deliveryCellId") or ""))
            except (TypeError, ValueError):
                port_row, port_col = 0, 0
            idle_robots = [
                item for item in runtime.state.get("robots", [])
                if item.get("type") == "ctu"
                and item.get("status") == "idle"
                and not item.get("currentTaskId")
                and not item.get("returnPickupQueue")
            ]
            if not idle_robots:
                return
            # 取离回库口最近的空闲 CTU，减少取箱等待。
            def robot_distance(item: dict[str, Any]) -> tuple[int, str]:
                try:
                    _, row, col = parse_cell_id(str(item.get("currentCellId") or ""))
                except (TypeError, ValueError):
                    row, col = 0, 0
                return (abs(row - port_row) + abs(col - port_col), str(item["id"]))

            robot = min(idle_robots, key=robot_distance)
            robot["currentTaskId"] = leader["id"]
            leader["assignedRobotId"] = robot["id"]
            leader["status"] = "assigned"
            if not self._set_robot_leg(
                runtime,
                robot,
                leader,
                "pickup_from_port",
                leader.get("deliveryCellId"),
                target_layer=2,
            ):
                # 去回库口不可达：释放，避免占住任务
                leader["assignedRobotId"] = None
                robot["currentTaskId"] = None
                continue
            leader["history"].append({"tick": tick, "phase": leader["phase"], "status": leader["status"]})
            self._record_event(
                runtime,
                tick,
                "task_state_changed",
                {"taskId": leader["id"], "phase": leader["phase"], "status": leader["status"], "robotId": robot["id"]},
            )
            # 组队：同一回库口、还在支线上滑行、未被认领的回库箱，按到口先后排队
            end_block = str(leader.get("returnEndBlockId") or "")
            if not end_block:
                continue
            followers = []
            for other in tasks:
                if other.get("id") == leader.get("id"):
                    continue
                if other.get("taskKind") != "picking_return" or other.get("assignedRobotId"):
                    continue
                if other.get("status") in TERMINAL_TASK_STATUSES or other.get("phase") != "arm_conveyor_delivery":
                    continue
                tr = other.get("conveyorTransport")
                if not isinstance(tr, dict):
                    continue
                route = tr.get("route") or []
                if not route or str(route[-1]).removesuffix(":upper") != end_block:
                    continue
                followers.append(other)
            # route index 越大越靠近回库口：先到先装
            followers.sort(key=lambda t: -int((t.get("conveyorTransport") or {}).get("index", 0)))
            for follower in followers[: max(0, capacity - 1)]:
                follower["assignedRobotId"] = robot["id"]
                robot.setdefault("returnPickupQueue", []).append(follower["id"])
            if robot.get("returnPickupQueue"):
                self._record_event(
                    runtime,
                    tick,
                    "task_state_changed",
                    {
                        "taskId": leader["id"],
                        "batchPickup": True,
                        "robotId": robot["id"],
                        "queue": list(robot["returnPickupQueue"]),
                        "batchSize": len(robot["returnPickupQueue"]) + 1,
                    },
                )

    def _advance_conveyor_transport(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any] | None,
        task: dict[str, Any],
    ) -> bool:
        """按块容量和下游占用推进一个货箱；返回 True 表示本段已经走完。"""
        transport = task.get("conveyorTransport")
        if not isinstance(transport, dict) or transport.get("phase") != task.get("phase"):
            return False
        route = list(transport.get("route") or [])
        index = int(transport.get("index", 0))
        if not route or index >= len(route) - 1:
            if task.get("phase") == "conveyor_to_lift":
                task["conveyorResourceId"] = nearest_lift_id(
                    runtime.topology,
                    transport.get("currentBlockId"),
                )
            transport["nextBlockId"] = None
            task["remainingStageTicks"] = 0
            return True

        if task.get("phase") == "arm_conveyor_delivery" and task.get("taskKind") not in (
            "placed_goods_delivery",
            "picking_return",
        ):
            # 拣货分裂的 fwd/回库子任务与拣货链路同线同速（1 块/tick），
            # 不走机械臂带面的固定慢速；dock 机械臂线保持原带速。
            seconds_per_block = ARM_CONVEYOR_BLOCK_DISTANCE / ARM_CONVEYOR_VISUAL_SPEED
            progress_seconds = float(transport.get("travelProgressSeconds", 0.0)) + self._tick_delay(runtime)
            if progress_seconds + 1e-9 < seconds_per_block:
                transport["travelProgressSeconds"] = progress_seconds
                return False
            # 保留不足一个 tick 的余量，使离散模块事件长期平均速度精确
            # 对齐前端连续动画，而不是每段都向上取整造成越走越慢。
            transport["travelProgressSeconds"] = max(0.0, progress_seconds - seconds_per_block)

        next_block = route[index + 1]
        node = runtime.conveyor_graph.get("nodes", {}).get(next_block, {})
        capacity = max(1, int(node.get("capacity", 1) or 1))
        occupying = 0
        for other in runtime.state.get("tasks", []):
            if other.get("id") == task.get("id") or other.get("status") in TERMINAL_TASK_STATUSES:
                continue
            other_transport = other.get("conveyorTransport")
            if (
                isinstance(other_transport, dict)
                and other_transport.get("currentBlockId") == next_block
                and other_transport.get("deck") == transport.get("deck")
            ):
                occupying += 1
        if occupying >= capacity:
            transport["waitingTicks"] = int(transport.get("waitingTicks", 0)) + 1
            transport["nextBlockId"] = next_block
            if robot is not None:
                robot["status"] = "waiting"
                robot["phaseLabel"] = f"{task['phaseLabel']} · 下游阻塞"
            return False

        index += 1
        transport["index"] = index
        transport["currentBlockId"] = next_block
        transport["nextBlockId"] = route[index + 1] if index + 1 < len(route) else None
        transport["waitingTicks"] = 0
        transport["movedTicks"] = int(transport.get("movedTicks", 0)) + 1
        task["remainingStageTicks"] = max(0, len(route) - index - 1)
        task["lastProgressTick"] = int(runtime.state["tick"])
        task_kind = task.get("taskKind")
        if task.get("phase") == "arm_conveyor_delivery":
            transport_label = (
                "回库箱上层输送" if task_kind == "picking_return"
                else "已拣货物上层输送" if task_kind == "placed_goods_delivery"
                else "机械臂放箱后上层输送"
            )
        else:
            transport_label = "下层去程" if transport.get("deck") == "lower" else "上层回程"
        task["phaseLabel"] = f"{transport_label} {index + 1}/{len(route)}"
        if robot is not None:
            robot["status"] = "waiting"
            robot["phaseLabel"] = task["phaseLabel"]
        self._record_event(
            runtime,
            int(runtime.state["tick"]),
            "conveyor_position",
            {
                "taskId": task["id"],
                "deck": transport.get("deck"),
                "blockId": next_block,
                "index": index,
            },
        )
        if index >= len(route) - 1:
            if task.get("phase") == "conveyor_to_lift":
                task["conveyorResourceId"] = nearest_lift_id(runtime.topology, next_block)
            return True
        return False

    def _lift_stage_has_priority(self, runtime: RunRuntime, task: dict[str, Any]) -> bool:
        """升降移载机仲裁：同一升降资源上按进入顺序（liftEnteredTick）先到先得。"""
        resource_id = task.get("conveyorResourceId")
        if not resource_id:
            return True
        contenders = [
            other for other in runtime.state.get("tasks", [])
            if other.get("phase") == "lift_to_upper"
            and other.get("status") not in TERMINAL_TASK_STATUSES
            and other.get("conveyorResourceId") == resource_id
        ]
        if not contenders:
            return True
        winner = min(contenders, key=lambda other: (int(other.get("liftEnteredTick", 0)), str(other.get("id"))))
        return winner.get("id") == task.get("id")

    def _lift_device_block(self, runtime: RunRuntime, task: dict[str, Any]) -> str | None:
        """返回任务所配升降移载机的设备块（如 1F:94:90），用于货箱滑上设备再抬升。"""
        resource_id = task.get("conveyorResourceId")
        if not resource_id:
            return None
        for node in runtime.topology.get("nodes", []):
            if node.get("id") == resource_id:
                floor = str(node.get("floor") or "1F")
                return f"{floor}:{int(node.get('row', 0))}:{int(node.get('col', 0))}"
        return None

    def _enter_timed_stage(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
        phase: str,
        ticks: int,
        event_extra: dict[str, Any] | None = None,
    ) -> None:
        """进入一个按 remainingStageTicks 计时的阶段（输送/升降/拣货/放置等通用）。"""
        tick = runtime.state["tick"]
        task["phase"] = phase
        task["phaseLabel"] = self._phase_label(phase)
        task["status"] = "assigned"
        route_ticks = self._prepare_conveyor_transport(runtime, task, phase)
        task["remainingStageTicks"] = max(1, int(route_ticks if route_ticks is not None else ticks))
        if phase == "lift_to_upper":
            task["liftEnteredTick"] = tick
        if robot is not None:
            robot["status"] = "waiting"
            robot["handlingAction"] = None
            robot["path"] = [robot["currentCellId"]]
            robot["pathLayers"] = [robot.get("currentLayer", 1)]
            robot["phaseLabel"] = f"{task['phaseLabel']} · 剩余 {task['remainingStageTicks']} tick"
        task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
        event: dict[str, Any] = {
            "taskId": task["id"],
            "phase": task["phase"],
            "status": task["status"],
            "stageTicks": task["remainingStageTicks"],
        }
        if event_extra:
            event.update(event_extra)
        self._record_event(runtime, tick, "task_state_changed", event)

    def _start_picking(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any]) -> None:
        """货物已升到上层并沿上层输送线到达人工拣货位，开始自动计时拣货。"""
        pick_ticks = max(1, int(runtime.config.get("pickTicks", 6) or 6))
        self._enter_timed_stage(runtime, robot, task, "picking", pick_ticks)

    def _finish_picking(self, runtime: RunRuntime, task: dict[str, Any], robot: dict[str, Any] | None) -> None:
        """拣货计时结束（或人工跳过）：拣出的货物放到放置区，随后进入放置分裂阶段。

        detached 单箱任务没有放置区时也进入 place_goods：分裂点统一在 place_goods
        结束处（fwd 出货 + 货箱回库）。
        """
        task["remainingStageTicks"] = 0
        task["status"] = "assigned"
        drop_zone = self._drop_zone_for(runtime, task)
        if drop_zone is not None:
            task["dropZoneZoneId"] = drop_zone.get("id")
            task["dropZoneCellId"] = drop_zone_entry_cell(runtime.topology, drop_zone)
        place_ticks = max(1, int(runtime.config.get("placeTicks", 3) or 3))
        event_extra = (
            {
                "dropZoneZoneId": task["dropZoneZoneId"],
                "dropZoneCellId": task["dropZoneCellId"],
            }
            if task.get("dropZoneCellId")
            else None
        )
        self._enter_timed_stage(
            runtime,
            robot,
            task,
            "place_goods",
            place_ticks,
            event_extra=event_extra,
        )


    def _start_conveyor_return(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any]) -> None:
        """空货箱放回上层输送线，沿原路运回上层的输送线出入口。"""
        task["returnLineIndex"] = 0
        return_ticks = max(1, int(runtime.config.get("conveyorTransferTicks", 6) or 6))
        self._enter_timed_stage(runtime, robot, task, "conveyor_return", return_ticks)

    def _advance_pickup_from_port(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any]) -> None:
        """CTU 在回库口取回货箱。支持批量：一次认领最多 CTU 容量个回库箱。

        首次到达装载本任务的箱；若 robot.returnPickupQueue 有排队箱，则等待
        输送线把下一箱喂到回库口块后逐箱装车（每 tick 至多吸收一箱），全部
        装完后按合并后的 lines 逐库位回送（_start_return_leg 原生支持多站）。
        """
        tick = runtime.state["tick"]
        capacity = int(robot.get("capacity", ROBOT_CARRY_CAPACITY.get("ctu", 5)))
        if not task.get("portLoadingStarted"):
            goods = task.get("transitGoods") or []
            robot["carrying"] = [dict(slot) for slot in goods]
            robot["load"] = min(capacity, len(goods))
            # 货箱已被 CTU 取走：清掉回库口停靠 transport，后续由车载渲染接管。
            task["conveyorTransport"] = None
            task["returnLineIndex"] = 0
            task["portLoadingStarted"] = True
            task["history"].append({"tick": tick, "phase": "pickup_from_port", "status": task["status"]})
            self._record_event(
                runtime,
                tick,
                "task_state_changed",
                {"taskId": task["id"], "phase": "pickup_from_port", "status": task["status"], "picked": task.get("picked", 0)},
            )
        queue = list(robot.get("returnPickupQueue") or [])
        if queue:
            next_id = queue[0]
            child = find_task(runtime.state.get("tasks", []), next_id)
            child_transport = (child or {}).get("conveyorTransport")
            if child is None or child.get("status") in TERMINAL_TASK_STATUSES or not isinstance(child_transport, dict):
                # 排队项失效（被取消/失败）：跳过
                queue.pop(0)
                robot["returnPickupQueue"] = queue
                return
            route = child_transport.get("route") or []
            end_block = str(route[-1]) if route else ""
            if str(child_transport.get("currentBlockId") or "") != end_block:
                # 下一箱还在支线上滑行：CTU 原地等待输送线喂箱
                robot["status"] = "waiting"
                robot["handlingAction"] = None
                robot["path"] = [robot["currentCellId"]]
                robot["pathLayers"] = [robot.get("currentLayer", 1)]
                robot["phaseLabel"] = f"回库口批量取货 · 等待下一箱到位（剩 {len(queue)} 箱）"
                task["phaseLabel"] = robot["phaseLabel"]
                return
            # 下一箱已到口：吸收进本任务（箱上车、任务并单完成）
            queue.pop(0)
            robot["returnPickupQueue"] = queue
            slot = (child.get("transitGoods") or [{}])[0]
            line = (child.get("lines") or [{}])[0]
            task.setdefault("transitGoods", []).append(dict(slot))
            task.setdefault("lines", []).append(dict(line))
            if len(robot["carrying"]) < capacity:
                robot["carrying"].append(dict(slot))
            robot["load"] = min(capacity, len(robot["carrying"]))
            child["conveyorTransport"] = None
            child["status"] = "completed"
            child["phase"] = "completed"
            child["phaseLabel"] = "并入批量回库"
            child["completedTick"] = tick
            child.setdefault("history", []).append({"tick": tick, "phase": "completed", "status": "completed"})
            self._record_event(
                runtime,
                tick,
                "task_state_changed",
                {"taskId": child["id"], "phase": "completed", "status": "completed", "absorbedBy": task["id"]},
            )
            return
        # 队列已空：出发，按合并后的 lines 逐库位回送
        robot.pop("returnPickupQueue", None)
        self._start_return_leg(runtime, robot, task)

    def _start_return_leg(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any]) -> None:
        """按订单行顺序把货箱送回各自的原库位、原层。"""
        lines = (task.get("cancelReturnLines") if task.get("cancelAfterReturn") else task.get("lines")) or []
        index = int(task.get("returnLineIndex") or 0)
        if index >= len(lines):
            self._complete_task(runtime, robot, task)
            return
        line = lines[index]
        target_cell = line.get("cellId")
        if not target_cell and line.get("nodeId"):
            try:
                target_cell = node_to_cell_id(runtime.topology, line["nodeId"])
            except KeyError:
                target_cell = None
        if not target_cell:
            task["returnLineIndex"] = index + 1
            self._start_return_leg(runtime, robot, task)
            return
        task["status"] = "assigned"
        self._set_robot_leg(
            runtime,
            robot,
            task,
            "return_to_storage",
            target_cell,
            int(line.get("layer", 1)),
        )
        task["phaseLabel"] = f"回库 {index + 1}/{len(lines)}"
        robot["phaseLabel"] = task["phaseLabel"]
        tick = runtime.state["tick"]
        task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {
                "taskId": task["id"],
                "phase": task["phase"],
                "status": task["status"],
                "nodeId": line.get("nodeId"),
                "layer": line.get("layer"),
                "returnLineIndex": index,
            },
        )

    def _advance_pickup_from_station(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any]) -> None:
        """CTU 到达上层拣货台并取回货箱，开始按原库位回库。"""
        tick = runtime.state["tick"]
        goods = task.get("transitGoods") or []
        capacity = int(robot.get("capacity", ROBOT_CARRY_CAPACITY.get("ctu", 5)))
        robot["carrying"] = [dict(slot) for slot in goods]
        robot["load"] = min(capacity, len(goods))
        task["returnLineIndex"] = 0
        task["history"].append({"tick": tick, "phase": "pickup_from_station", "status": task["status"]})
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {"taskId": task["id"], "phase": "pickup_from_station", "status": task["status"], "picked": task.get("picked", 0)},
        )
        self._start_return_leg(runtime, robot, task)

    def _restore_transit_goods(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
    ) -> None:
        """把当前行的货箱放回原库位；箱内数量保持不变。"""
        lines = (task.get("cancelReturnLines") if task.get("cancelAfterReturn") else task.get("lines")) or []
        goods = task.get("transitGoods") or []
        index = int(task.get("returnLineIndex") or 0)
        if index >= len(lines):
            self._complete_task(runtime, robot, task)
            return
        line = lines[index]
        slot = dict(goods[index]) if index < len(goods) else {
            key: line[key]
            for key in ("nodeId", "layer", *SKU_FIELDS, "qty")
            if line.get(key) not in (None, "")
        }
        inventory = runtime.state.setdefault("inventory", [])
        already_there = any(
            item.get("nodeId") == slot.get("nodeId")
            and int(item.get("layer", 1)) == int(slot.get("layer", 1))
            for item in inventory
        )
        if not already_there:
            inventory.append(slot)
        task["returnLineIndex"] = index + 1
        robot["carrying"] = [dict(item) for item in goods[index + 1 :]]
        robot["load"] = min(int(robot.get("capacity", 1)), len(robot["carrying"]))
        tick = runtime.state["tick"]
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {
                "taskId": task["id"],
                "phase": task["phase"],
                "status": task["status"],
                "nodeId": slot.get("nodeId"),
                "layer": slot.get("layer"),
                "returnLineIndex": task["returnLineIndex"],
            },
        )
        if task["returnLineIndex"] < len(lines):
            self._start_return_leg(runtime, robot, task)
        else:
            robot["carrying"] = []
            robot["load"] = 0
            self._complete_task(runtime, robot, task)

    def _complete_task(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
        occupied_cells: set[str] | None = None,
    ) -> None:
        """任务收尾：清空机器人、回到待命位并累加指标（原交付完成逻辑复用）。"""
        tick = runtime.state["tick"]
        cancelled = bool(task.get("cancelAfterReturn"))
        task["status"] = "cancelled" if cancelled else "completed"
        task["phase"] = "cancelled" if cancelled else "completed"
        task["phaseLabel"] = self._phase_label(task["phase"])
        task["completedTick"] = None if cancelled else tick
        if cancelled:
            task["cancelledTick"] = tick
        task["remainingStageTicks"] = 0
        robot["currentTaskId"] = None
        robot["handlingAction"] = None
        robot["path"] = [robot["currentCellId"]]
        robot["pathLayers"] = [robot.get("currentLayer", 1)]
        robot["load"] = 0
        robot["carrying"] = []
        robot["returnPickupQueue"] = []
        # 交付/回库完成后退回一格，别把车停在出入口或库位旁堵住后面的车。
        self._step_off_port(runtime, robot, occupied_cells)
        home_cell = robot.get("homeCellId")
        has_unfinished_tasks = any(
            other["id"] != task["id"] and other.get("status") not in TERMINAL_TASK_STATUSES
            for other in runtime.state.get("tasks", [])
        )
        if has_unfinished_tasks and home_cell and home_cell != robot["currentCellId"] and self._robot_path_to(runtime, robot, home_cell, 1):
            robot["returningToStandby"] = True
            robot["status"] = "returning"
            robot["phaseLabel"] = "返回待命位"
        else:
            robot["returningToStandby"] = False
            robot["status"] = "idle"
            robot["phaseLabel"] = "待命"
        if not cancelled and not task.get("parentId"):
            # 子任务（拣货分裂）不重复累加指标：父任务完成时已计一次。
            runtime.metrics["completedTasks"] += 1
            runtime.metrics["throughput"] += 1
            if task.get("urgent"):
                runtime.metrics["urgentOrdersHandled"] += 1
        task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
        self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "phase": task["phase"], "status": task["status"]})
        # 出货位循环搬运：开启 loop 的出货位任务完成后自动补一趟。
        self._maybe_loop_dock_task(runtime, task)

    async def complete_picking(self, run_id: str, task_id: str) -> dict[str, Any]:
        """人工拣货手动跳过：立即结束拣货（有放置区时进入放置阶段），并广播当前 tick。"""
        runtime = self._require_runtime(run_id)
        task = find_task(runtime.state.get("tasks", []), task_id)
        if task is None:
            raise KeyError(task_id)
        if task.get("phase") != "picking":
            raise ValueError("任务当前不在人工拣货阶段")
        robot = next(
            (item for item in runtime.state.get("robots", []) if item["id"] == task.get("assignedRobotId")),
            None,
        )
        self._finish_picking(runtime, task, robot)
        self._refresh_orders(runtime)
        self._refresh_metrics(runtime)
        self._persist(runtime)
        await self._broadcast_tick(runtime)
        return task

    def _advance_robot_phase(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
        occupied_cells: set[str] | None = None,
    ) -> None:
        tick = runtime.state["tick"]
        if task.get("mode") == "shuttle_transfer":
            if task["phase"] == "pickup_wait":
                if tick < int(task.get("pickupReadyTick") or tick):
                    return
                try:
                    changed = apply_pick(runtime.state, task, tick)
                    robot["load"] = int(task["quantity"])
                    robot["carrying"] = [{
                        "nodeId": task["sourceLocationId"],
                        "cellId": task["sourceCellId"],
                        "layer": task["sourceLayer"],
                        "skuId": task["skuId"],
                        "skuName": task["skuName"],
                        "qty": task["quantity"],
                    }]
                    self._start_transfer_route(runtime, robot, task)
                except ValueError as error:
                    task["status"] = "failed"
                    task["phase"] = "failed"
                    task["phaseLabel"] = str(error)
                    task["failureReason"] = str(error)
                    robot["status"] = "exception"
                    robot["phaseLabel"] = task["phaseLabel"]
                    self._record_event(runtime, tick, "transfer_failed", {"taskId": task["id"], "reason": str(error)})
                    return
                if changed:
                    self._record_event(runtime, tick, "inventory_pick", {"taskId": task["id"], "quantity": task["quantity"]})
                return
            if task["phase"] in {"track", "enter_lift", "lift_moving", "exit_lift", "moving_to_dropoff"}:
                task["phase"] = "drop_wait"
                task["phaseLabel"] = "到达目标库位，准备放货"
                task["dropReadyTick"] = tick + 1
                robot["status"] = "handling"
                robot["phaseLabel"] = task["phaseLabel"]
                return
            if task["phase"] == "drop_wait":
                if tick < int(task.get("dropReadyTick") or tick):
                    return
                try:
                    changed = apply_drop(runtime.state, task, tick)
                except ValueError as error:
                    task["status"] = "failed"
                    task["phase"] = "failed"
                    task["phaseLabel"] = str(error)
                    robot["status"] = "exception"
                    robot["phaseLabel"] = task["phaseLabel"]
                    self._record_event(runtime, tick, "transfer_failed", {"taskId": task["id"], "reason": str(error)})
                    return
                task["status"] = "completed"
                task["phase"] = "completed"
                task["phaseLabel"] = "放货完成"
                task["completedTick"] = tick
                task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
                robot["currentTaskId"] = None
                robot["status"] = "idle"
                robot["phaseLabel"] = "待命"
                robot["load"] = 0
                robot["carrying"] = []
                runtime.metrics["completedTasks"] += 1
                runtime.metrics["throughput"] += int(task["quantity"])
                if changed:
                    self._record_event(runtime, tick, "inventory_drop", {"taskId": task["id"], "quantity": task["quantity"]})
                return

        if task["phase"] == "pickup_from_ctu":
            self._advance_ctu_pickup_action(runtime, robot, task)
            return

        if task["phase"] == "pickup_from_shuttle":
            # 从货道库存移除被取的箱（带深度货道）；车载记录用任务行的实际箱数据。
            line = (task.get("lines") or [{}])[0]
            if line.get("depth") is not None:
                inventory = runtime.state.setdefault("inventory", [])
                for index, item in enumerate(inventory):
                    if (
                        item.get("cellId") == line.get("cellId")
                        and int(item.get("layer", 1)) == int(line.get("layer", 1))
                        and int(item.get("depth", 1)) == int(line["depth"])
                    ):
                        inventory.pop(index)
                        break
            robot["load"] = min(robot.get("capacity", 1), robot.get("load", 0) + 1)
            task["picked"] = task.get("picked", 0) + 1
            # 记录车载货箱，交付动作才能把实载数据写进 deliveredGoods（前端按它渲染交付货箱）。
            robot["carrying"] = [dict(line_item) for line_item in (task.get("lines") or [])]
            # 倒库任务交付到库内目标货道（其层随目标货道）；出货任务交付到地面线（L1）。
            delivery_layer = int(task.get("targetLayer", 1)) if task.get("taskKind") == "relocate" else 1
            if self._set_robot_leg(runtime, robot, task, "delivery_to_port", task["deliveryCellId"], target_layer=delivery_layer):
                task["phaseLabel"] = "倒库移位" if task.get("taskKind") == "relocate" else "输送线交付"
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "phase": task["phase"], "status": task["status"]})
            return

        if task["phase"] == "delivery_to_port":
            if (
                task.get("robotType") == "shuttle"
                and task.get("armHandoff")
                and robot["currentCellId"] == task.get("deliveryCellId")
            ):
                # 到达机械臂交接处：停车等待机械臂抓取，禁止自行上带。
                task["phase"] = "waiting_arm_handoff"
                task["phaseLabel"] = "等待机械臂抓取"
                robot["status"] = "handling"
                robot["phaseLabel"] = task["phaseLabel"]
                robot["path"] = [robot["currentCellId"]]
                robot["pathLayers"] = [robot.get("currentLayer", 1)]
                task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
                self._record_event(
                    runtime,
                    tick,
                    "task_state_changed",
                    {"taskId": task["id"], "phase": task["phase"], "status": task["status"]},
                )
                return
            self._advance_ctu_delivery_action(runtime, robot, task, occupied_cells)
            return

        if task["phase"] == "waiting_arm_handoff":
            # 机械臂接管（_advance_picking_arm 驱动）；四向车在此阶段原地等待。
            return

        if task["phase"] in {
            "conveyor_to_lift",
            "lift_to_upper",
            "conveyor_to_pick",
            "picking",
            "place_goods",
            "conveyor_return",
        }:
            if task["phase"] in {"conveyor_to_lift", "conveyor_to_pick", "conveyor_return"} and task.get("conveyorTransport"):
                if not self._advance_conveyor_transport(runtime, robot, task):
                    return
                transport = task.get("conveyorTransport") or {}
                current_block = str(transport.get("currentBlockId") or "")
                if task["phase"] == "conveyor_to_lift" and current_block:
                    # 记录升降注入块（下层终点），供 lift_to_upper / conveyor_to_pick 定位。
                    task["liftBlockId"] = current_block.removesuffix(":lower")
                elif task["phase"] == "conveyor_to_pick" and current_block:
                    # 记录拣货台块，供 picking / place_goods 停靠与分裂子任务起点使用。
                    task["stationBlockId"] = current_block.removesuffix(":upper")
                elif task["phase"] == "conveyor_return" and current_block:
                    # 记录回库口块，供 pickup_from_port 阶段停靠定位。
                    task["returnEndBlockId"] = current_block.removesuffix(":upper")
                task["conveyorTransport"] = None
                self._advance_timed_stage(runtime, robot, task)
                return
            if task["phase"] == "lift_to_upper" and not self._lift_stage_has_priority(runtime, task):
                robot["status"] = "waiting"
                robot["phaseLabel"] = "等待升降移载机"
                return
            if task["phase"] == "lift_to_upper":
                # 升降编排：拿到升降权后货箱先滑上升降机设备块，
                # 计时末段再在设备上抬升到上层（deck 翻转为 upper）。
                lift_transport = task.get("conveyorTransport")
                if isinstance(lift_transport, dict) and lift_transport.get("deck") != "upper":
                    device_block = self._lift_device_block(runtime, task)
                    if device_block:
                        # 滑上升降机（输送线末端 → 设备块）
                        device_lower = f"{device_block}:lower"
                        if lift_transport.get("currentBlockId") != device_lower:
                            lift_transport["currentBlockId"] = device_lower
                            lift_transport["route"] = [device_lower]
                    if int(task.get("remainingStageTicks", 0)) <= 1:
                        # 末段抬升
                        lift_transport["deck"] = "upper"
                        if device_block:
                            device_upper = f"{device_block}:upper"
                            lift_transport["currentBlockId"] = device_upper
                            lift_transport["route"] = [device_upper]
                        else:
                            base = str(lift_transport.get("currentBlockId") or "").removesuffix(":lower")
                            lift_transport["currentBlockId"] = base + ":upper"
                            lift_transport["route"] = [base + ":upper"]
            remaining = int(task.get("remainingStageTicks", 0)) - 1
            task["remainingStageTicks"] = max(0, remaining)
            if remaining > 0:
                robot["status"] = "waiting"
                robot["phaseLabel"] = f"{task['phaseLabel']} · 剩余 {remaining} tick"
                return
            self._advance_timed_stage(runtime, robot, task)
            return

        if task["phase"] == "pickup_from_port":
            self._advance_pickup_from_port(runtime, robot, task)
            return

        if task["phase"] == "return_to_storage":
            self._restore_transit_goods(runtime, robot, task)
            return

    def _advance_timed_stage(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any]) -> None:
        """计时阶段（remainingStageTicks 归零）结束后的推进：按人工拣货闭环的阶段链接续。"""
        phase = task["phase"]
        if phase == "conveyor_to_lift":
            # 下层输送线把货箱送到升降移栽机后，升降移栽机把货箱抬到上层。
            lift_ticks = max(1, int(runtime.config.get("liftTicks", 3) or 3))
            self._enter_timed_stage(runtime, robot, task, "lift_to_upper", lift_ticks)
            return
        if phase == "lift_to_upper":
            transfer_ticks = 1 if runtime.conveyor_graph.get("explicit") else max(
                1, int(runtime.config.get("conveyorTransferTicks", 6) or 6)
            )
            self._enter_timed_stage(runtime, robot, task, "conveyor_to_pick", transfer_ticks)
            return
        if phase == "conveyor_to_pick":
            self._start_picking(runtime, robot, task)
            return
        if phase == "picking":
            self._finish_picking(runtime, task, robot)
            return
        if phase == "place_goods":
            # ===== Picking 分裂点 =====
            # 从 transitGoods（输送线送来的货箱）里扣减拣出数量：
            # - 已拣部分合并成 placedGoods（继续 forward 输送线到出货口）
            # - 剩余 qty 留在 transitGoods（走 conveyor_return 回库）
            # - qty 归零的空箱丢弃（无需回库）
            transit = task.get("transitGoods") or []
            if not transit:
                # 没有输送过来的货箱——兜底处理
                task["placedGoods"] = [dict(line) for line in (task.get("lines") or [])]
            else:
                placed_fragments: list[dict[str, Any]] = []
                new_transit: list[dict[str, Any]] = []
                pick_list = task.get("pickPiecesList") or []
                for slot_index, slot in enumerate(transit):
                    sku = slot.get("skuId")
                    slot_qty = max(1, int(slot.get("qty", 1)))
                    # 每箱拣出件数：订单尾箱只拣尾数，其余整箱按整箱件数拣。
                    pick_qty = min(
                        slot_qty,
                        max(1, int(pick_list[slot_index]))
                        if slot_index < len(pick_list) and pick_list[slot_index]
                        else max(1, int(task.get("pickPieces") or 1)),
                    )
                    placed_fragments.append({
                        "skuId": sku,
                        "skuName": slot.get("skuName"),
                        "qty": pick_qty,
                        "nodeId": slot.get("nodeId"),
                        "layer": slot.get("layer"),
                        "cellId": slot.get("cellId"),
                    })
                    leftover = slot_qty - pick_qty
                    if leftover > 0:
                        leftover_slot = dict(slot)
                        leftover_slot["qty"] = leftover
                        new_transit.append(leftover_slot)
                # 汇总 placedGoods（同 SKU 合并 qty）
                placed_map: dict[str, dict[str, Any]] = {}
                for frag in placed_fragments:
                    key = frag["skuId"]
                    if key in placed_map:
                        placed_map[key]["qty"] = int(placed_map[key].get("qty", 1)) + int(frag["qty"])
                    else:
                        placed_map[key] = dict(frag)
                task["placedGoods"] = list(placed_map.values())
                task["transitGoods"] = new_transit

            # ===== 分裂：已拣部分创建 forward 输送线子任务 =====
            if task.get("placedGoods"):
                self._spawn_placed_goods_delivery(runtime, task)

            # 剩余货箱走 conveyor_return 回库；如果 transitGoods 已空则直接 complete
            if task.get("transitGoods"):
                self._start_conveyor_return(runtime, robot, task)
            else:
                # 空箱无剩余：必须走标准任务收尾（释放机器人、退回待命位、累加指标），
                # 否则 CTU 的 currentTaskId 一直挂着已完成任务，滞留在回库口等
                # 一个永远不会出现的回库箱。
                self._complete_task(runtime, robot, task)
                self._record_event(
                    runtime,
                    runtime.state["tick"],
                    "task_completed",
                    {"taskId": task["id"], "reason": "空箱无剩余，直接完成"},
                )
            return
        if phase == "conveyor_return":
            # 货箱经上层输送线回到出入口，CTU 再到出入口取回。
            tick = runtime.state["tick"]
            task["phase"] = "pickup_from_port"
            task["phaseLabel"] = self._phase_label(task["phase"])
            task["status"] = "assigned"
            robot["status"] = "moving"
            robot["handlingAction"] = None
            # 回库口停靠 transport：货箱视觉上停在回库口块等待 CTU 取回。
            # （此阶段不走 _enter_timed_stage，需要显式调用 _prepare 创建。）
            self._prepare_conveyor_transport(runtime, task, "pickup_from_port")
            if task.get("deliveryCellId"):
                self._set_robot_leg(
                    runtime,
                    robot,
                    task,
                    "pickup_from_port",
                    task["deliveryCellId"],
                    target_layer=2,
                )
            robot["phaseLabel"] = task["phaseLabel"]
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._record_event(
                runtime,
                tick,
                "task_state_changed",
                {"taskId": task["id"], "phase": task["phase"], "status": task["status"]},
            )
            return

    def _step_off_port(self, runtime: RunRuntime, robot: dict[str, Any], occupied_cells: set[str] | None) -> None:
        """交付后如果正停在 port 出入口格，退回进入口前的格子。"""
        port_cells = {
            port_entry_cell(runtime.topology, zone)
            for zone in runtime.topology.get("zones", [])
            if zone.get("type") == "port"
        }
        current = robot["currentCellId"]
        back = robot.get("lastCellId")
        if current not in port_cells or not back or back == current:
            return
        if back in {item["currentCellId"] for item in runtime.state["robots"]}:
            return
        if parse_cell_id(back) in build_occupied_lookup(runtime.topology):
            return
        motion_from = self._motion_position(runtime, robot)
        robot["lastCellId"] = current
        robot["currentCellId"] = back
        robot["currentNode"] = back
        floor, row, col = parse_cell_id(back)
        robot["currentFloor"] = floor
        robot["currentRow"] = row
        robot["currentCol"] = col
        robot["path"] = [back]
        robot["pathLayers"] = [robot.get("currentLayer", 1)]
        if occupied_cells is not None:
            occupied_cells.discard(current)
            occupied_cells.add(back)
        self._record_committed_motion(
            runtime,
            robot,
            motion_from,
            MovementType.BACKOFF,
            "_step_off_port",
        )
        self._record_event(
            runtime,
            runtime.state["tick"],
            "robot_position",
            {"robotId": robot["id"], "cellId": back, "floor": floor, "row": row, "col": col, "layer": robot.get("currentLayer", 1), "status": "idle"},
        )

    def _start_transfer_route(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
    ) -> None:
        paths, layers, stages = build_playback_route(task["plan"], TICK_INTERVAL_SECONDS)
        if not paths:
            raise ValueError("Transfer plan contains no playback route")
        robot["path"] = paths
        robot["pathLayers"] = layers
        robot["pathStages"] = stages
        robot["targetCellId"] = task["deliveryCellId"]
        robot["targetLayer"] = task["targetLayer"]
        robot["status"] = "moving"
        robot["phaseLabel"] = "四向车沿规划轨道行驶"
        robot["currentTaskId"] = task["id"]
        task["phase"] = "moving_to_dropoff"
        task["phaseLabel"] = robot["phaseLabel"]
        task["etaTicks"] = max(1, len(paths) - 1)

    def _robot_path_to(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        target_cell_id: str,
        target_layer: int | None = None,
        *,
        extra_blocked: set[tuple[str, int, int]] | None = None,
        stop_adjacent: bool = False,
    ) -> bool:
        """计算并写入 robot 的 path/pathLayers/target；CTU 走自由网格寻路，四向车走巷道+电梯寻路。"""
        if robot["type"] == "shuttle":
            layer = target_layer if target_layer is not None else robot.get("currentLayer", 1)
            path3d = shortest_path_shuttle(
                runtime.topology,
                robot["currentCellId"],
                robot["currentLayer"],
                target_cell_id,
                layer,
                runtime.config["pathPolicy"],
            )
            if not path3d:
                return False
            robot["path"] = [cell_id(row, col, "1F") for row, col, _ in path3d]
            robot["pathLayers"] = [state_layer for _, _, state_layer in path3d]
            robot["targetLayer"] = layer
        else:
            if extra_blocked is None:
                # 故障设备停下的格子对其它设备不可通行，所有后续路段都要绕开。
                extra_blocked = {
                    parse_cell_id(item["currentCellId"]) for item in runtime.state["robots"] if item["failed"]
                }
            path_cells = shortest_path(
                runtime.topology,
                robot["currentCellId"],
                target_cell_id,
                runtime.config["pathPolicy"],
                avoid_storage=True,
                extra_blocked=extra_blocked,
            )
            if not path_cells:
                return False
            if stop_adjacent and len(path_cells) > 1:
                path_cells = path_cells[:-1]
            robot["path"] = [cell["id"] for cell in path_cells]
            robot["pathLayers"] = [1] * len(robot["path"])
            robot["targetLayer"] = max(1, min(CTU_RACK_LAYERS, int(target_layer or 1)))
        robot["targetCellId"] = target_cell_id
        return True

    def _set_robot_leg(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any], phase: str, target_cell_id: str | None, target_layer: int | None = None) -> bool:
        if not target_cell_id:
            self._fail_active_task(runtime, robot, task, "target_missing")
            return False
        if not self._robot_path_to(
            runtime,
            robot,
            target_cell_id,
            target_layer,
            stop_adjacent=phase in {"pickup_from_ctu", "return_to_storage"},
        ):
            self._fail_active_task(runtime, robot, task, f"{phase}_unreachable")
            return False
        robot["handlingAction"] = None
        robot["status"] = "moving"
        robot["phaseLabel"] = self._phase_label(phase)
        task["phase"] = phase
        task["phaseLabel"] = self._phase_label(phase)
        task["etaTicks"] = max(len(robot["path"]) - 1, 1)
        return True

    def _fail_active_task(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
        reason: str,
    ) -> None:
        tick = runtime.state["tick"]
        task["status"] = "failed"
        task["phase"] = "failed"
        task["phaseLabel"] = f"Task failed: {reason}"
        task["failureReason"] = reason
        task["completedTick"] = tick
        task.setdefault("history", []).append({"tick": tick, "phase": "failed", "status": "failed"})
        robot["status"] = "exception"
        robot["phaseLabel"] = task["phaseLabel"]
        robot["currentTaskId"] = None
        robot["path"] = [robot["currentCellId"]]
        robot["pathLayers"] = [robot.get("currentLayer", 1)]
        # 批量回库排队箱释放：让它们可以被重新派车，避免跟着失败任务卡死
        for queued_id in list(robot.get("returnPickupQueue") or []):
            queued = find_task(runtime.state.get("tasks", []), queued_id)
            if queued is not None and queued.get("assignedRobotId") == robot["id"]:
                queued["assignedRobotId"] = None
        robot["returnPickupQueue"] = []
        self._record_event(runtime, tick, "task_failed", {"taskId": task["id"], "reason": reason, "robotId": robot["id"]})

    def _detect_contention(self, runtime: RunRuntime) -> None:
        tick = runtime.state["tick"]
        blocked = [robot for robot in runtime.state["robots"] if robot["status"] == "blocked"]

        if len(blocked) >= 2 and all(robot["waitTicks"] >= 3 for robot in blocked):
            waiting_chain = []
            for robot in blocked:
                task = find_task(runtime.state["tasks"], robot["currentTaskId"])
                waiting_chain.append(
                    {
                        "robotId": robot["id"],
                        "currentCellId": robot["currentCellId"],
                        "nextCellId": robot["path"][1] if len(robot["path"]) > 1 else robot["currentCellId"],
                        "taskId": task["id"] if task else None,
                        "phase": task["phase"] if task else None,
                    }
                )
            if self._emit_alert(
                runtime,
                tick,
                category="deadlock",
                level="warning",
                message="检测到潜在死锁，设备等待链持续增长。",
                details={"waitingChain": waiting_chain},
                suggestion="检查巷道规划与让行策略，必要时回退等待时间最长的设备。",
            ):
                runtime.metrics["deadlocksDetected"] += 1
                self._record_event(runtime, tick, "deadlock_detected", {"waitingChain": waiting_chain, "message": "检测到潜在死锁"})

    def _refresh_metrics(self, runtime: RunRuntime) -> None:
        pending = [
            task for task in runtime.state["tasks"]
            if task["status"] not in TERMINAL_TASK_STATUSES
        ]
        active = [robot for robot in runtime.state["robots"] if robot["status"] in {"moving", "handling", "returning"}]
        blocked = [robot for robot in runtime.state["robots"] if robot["status"] == "blocked"]
        completed = [
            task for task in runtime.state["tasks"]
            if task.get("completedTick") is not None and task.get("startedTick") is not None
        ]
        completed_durations = [
            int(task["completedTick"]) - int(task["startedTick"]) for task in completed
        ]
        conveyor_items: list[dict[str, Any]] = []
        for task in pending:
            transport = task.get("conveyorTransport")
            if not isinstance(transport, dict):
                continue
            conveyor_items.append(
                {
                    "taskId": task.get("id"),
                    "orderId": task.get("orderId"),
                    "deck": transport.get("deck"),
                    "currentBlockId": transport.get("currentBlockId"),
                    "nextBlockId": transport.get("nextBlockId"),
                    "waitingTicks": int(transport.get("waitingTicks", 0)),
                    "phase": task.get("phase"),
                    "goodsCount": len(task.get("transitGoods") or task.get("lines") or []),
                }
            )
        occupied_nodes = {
            (item.get("deck"), item.get("currentBlockId"))
            for item in conveyor_items
            if item.get("currentBlockId")
        }
        graph_nodes = runtime.conveyor_graph.get("nodes", {})
        conveyor_utilization = round(len(occupied_nodes) / len(graph_nodes) * 100, 2) if graph_nodes else 0.0
        conveyor_queue = [
            str(item["taskId"])
            for item in conveyor_items
            if int(item.get("waitingTicks", 0)) > 0
        ]
        station_queue = [
            str(task["id"])
            for task in pending
            if task.get("phase") in {"conveyor_to_pick", "picking", "place_goods"}
        ]
        buffer_queue = [
            str(task["id"])
            for task in pending
            if task.get("phase") in {"waiting_delivery_slot", "pickup_from_port"}
        ]
        runtime.state["conveyorItems"] = conveyor_items
        runtime.state["queues"] = {
            "conveyor": conveyor_queue,
            "station": station_queue,
            "buffer": buffer_queue,
        }

        runtime.metrics.update(
            {
                "pendingTasks": len(pending),
                "activeRobots": len(active),
                "blockedRobots": len(blocked),
                "conveyorUtilization": conveyor_utilization,
                "avgEtaTicks": round(sum(task.get("etaTicks", 0) for task in pending) / len(pending), 2) if pending else 0.0,
                "avgCompletionTicks": round(sum(completed_durations) / len(completed_durations), 2) if completed_durations else 0.0,
                "stationQueueDepth": len(station_queue),
                "conveyorQueueDepth": len(conveyor_queue),
                "bufferOccupancy": len(buffer_queue),
            }
        )

    def _refresh_orders(self, runtime: RunRuntime) -> None:
        tasks = runtime.state.get("tasks", [])
        for order in runtime.state.get("orders", []):
            related = [task for task in tasks if task.get("orderId") == order["id"]]
            if not related:
                continue
            # 订单级任务只统计两侧根任务（四向车整箱 / CTU 尾箱）；拣货、出货、
            # 回库等子任务只参与完成判定，不重复计入任务数。
            root_tasks = [
                task
                for task in related
                if not task.get("parentId")
                and task.get("taskKind") in {"shuttle_order_pick", "ctu_order_pick"}
            ]
            completed = [task for task in related if task.get("status") == "completed"]
            active = [task for task in related if task.get("status") in {"assigned", "running"}]
            exceptions = [task for task in related if task.get("status") in {"exception", "failed"}]

            # 已拣件数：四向车整箱按任务记账；CTU 尾箱按拣货子任务实际拣出件数记账。
            picking_parents = {
                task.get("parentId")
                for task in related
                if task.get("taskKind") == "picking_box"
            }
            picked_by_sku: dict[str, int] = {}
            picked_boxes = 0
            for task in related:
                if task.get("status") != "completed":
                    continue
                kind = task.get("taskKind")
                pieces = 0
                if kind == "picking_box":
                    pieces = sum(int(good.get("qty") or 0) for good in (task.get("placedGoods") or []))
                    picked_boxes += 1
                elif kind == "shuttle_order_pick":
                    pieces = int(task.get("demandPieces") or 0)
                    picked_boxes += 1
                elif kind == "ctu_order_pick" and task.get("id") not in picking_parents:
                    # 无拣货位时 CTU 整箱直接交付，按任务需求件数记账。
                    pieces = int(task.get("demandPieces") or 0)
                    picked_boxes += 1
                if not pieces:
                    continue
                sku_id = str((task.get("lines") or [{}])[0].get("skuId") or "")
                if sku_id:
                    picked_by_sku[sku_id] = picked_by_sku.get(sku_id, 0) + pieces

            total_pieces = 0
            picked_pieces = 0
            for line in order.get("lines", []):
                requested = int(line.get("requestedPieces") or 0)
                if not requested:
                    case_cnt = max(1, int(line.get("caseCnt") or 1))
                    requested = max(1, int(line.get("quantity", 1))) * case_cnt
                    line["requestedPieces"] = requested
                sku_id = str(line.get("skuId") or "")
                available = picked_by_sku.get(sku_id, 0)
                picked = min(requested, available)
                picked_by_sku[sku_id] = max(0, available - picked)
                line["pickedPieces"] = picked
                line["picked"] = picked
                total_pieces += requested
                picked_pieces += picked

            completed_root = [task for task in root_tasks if task.get("status") == "completed"]
            order["completedTasks"] = len(completed_root)
            order["totalTasks"] = len(root_tasks)
            decomposition = order.setdefault("decomposition", {})
            assigned_tasks = [task for task in related if task.get("assignedRobotId")]
            decomposition["taskCount"] = len(root_tasks)
            decomposition["shuttleTaskCount"] = len(
                [task for task in root_tasks if task.get("taskKind") == "shuttle_order_pick"]
            )
            decomposition["ctuTaskCount"] = len(
                [task for task in root_tasks if task.get("taskKind") == "ctu_order_pick"]
            )
            decomposition["assignedTaskCount"] = len(assigned_tasks)
            decomposition["activeVehicleIds"] = list(dict.fromkeys(
                str(task["assignedRobotId"]) for task in assigned_tasks if task.get("assignedRobotId")
            ))
            decomposition["requestedPieces"] = total_pieces
            decomposition["pickedPieces"] = picked_pieces
            decomposition["pickedBoxes"] = picked_boxes
            if order.get("cancelRequested"):
                cancelled_tasks = [task for task in related if task.get("status") == "cancelled"]
                if len(cancelled_tasks) == len(related):
                    order["status"] = "cancelled"
                    order["statusLabel"] = "已取消"
                    order["cancelledTick"] = int(runtime.state.get("tick", 0))
                    decomposition["stage"] = "cancelled"
                else:
                    order["status"] = "returning"
                    order["statusLabel"] = "取消回库中"
                    decomposition["stage"] = "returning"
                continue
            # 取货占 80%，送达占 20%，让订单执行中也能给出真实进度而不是长期停在 0%。
            pickup_progress = picked_pieces / total_pieces if total_pieces else 0
            delivery_progress = (
                len(completed_root) / len(root_tasks) if root_tasks else 0
            )
            order["progress"] = round(pickup_progress * 80 + delivery_progress * 20)
            if len(completed) == len(related) and not order.get("missingQuantity"):
                order["status"] = "completed"
                order["statusLabel"] = "已完成"
                order["progress"] = 100
                order["completedTick"] = max(task.get("completedTick") or 0 for task in completed)
            elif exceptions or order.get("missingQuantity"):
                order["status"] = "exception"
                order["statusLabel"] = (
                    exceptions[0].get("phaseLabel") if exceptions else "库存不足"
                ) or "取货异常"
            elif active:
                order["status"] = "executing"
                order["statusLabel"] = active[0].get("phaseLabel") or "执行中"
                started_ticks = [task.get("startedTick") for task in related if task.get("startedTick") is not None]
                order["startedTick"] = min(started_ticks) if started_ticks else order.get("startedTick")
            else:
                order["status"] = "waiting_dispatch"
                order["statusLabel"] = "等待调度"

    def _record_event(self, runtime: RunRuntime, tick: int, event_type: str, payload: dict[str, Any]) -> None:
        self.repo.add_event(runtime.run_id, tick, event_type, payload)
        if event_type not in TELEMETRY_EVENT_TYPES:
            runtime.state["eventCount"] = int(runtime.state.get("eventCount", 0)) + 1

    def _emit_alert(
        self,
        runtime: RunRuntime,
        tick: int,
        *,
        category: str,
        level: str,
        message: str,
        details: dict[str, Any],
        suggestion: str,
    ) -> bool:
        last_tick = runtime.alert_memory.get(category, -999)
        if tick - last_tick < 4:
            return False
        runtime.alert_memory[category] = tick
        alert = {"category": category, "level": level, "message": message, "tick": tick, "details": details, "suggestion": suggestion}
        runtime.alerts.append(alert)
        runtime.state["alerts"] = runtime.alerts[-10:]
        self._record_event(runtime, tick, "alert", alert)
        return True

    def _phase_label(self, phase: str) -> str:
        mapping = {
            "pending_assignment": "待分配",
            "pickup_from_ctu": "CTU 取货",
            "pickup_from_shuttle": "四向车取货",
            "delivery_to_port": "输送线交付",
            "conveyor_transfer": "输送线转运",
            "conveyor_to_lift": "下层输送转运",
            "lift_to_upper": "升降移栽",
            "conveyor_to_pick": "上层输送至拣货位",
            "picking": "人工拣货",
            "place_goods": "拣货放置",
            "conveyor_return": "输送线回程",
            "arm_conveyor_delivery": "机械臂放箱后上层输送",
            "pickup_from_port": "出入口取回",
            "pickup_from_station": "拣货台取回",
            "return_to_storage": "回库",
            "feeding_conveyor": "货箱逐箱上线",
            "completed": "已完成",
        }
        return mapping.get(phase, phase)

    def _tick_payload(self, runtime: RunRuntime) -> dict[str, Any]:
        payload = {key: value for key, value in runtime.state.items() if key != "inventory"}
        if runtime.inventory_delta:
            payload["inventoryDelta"] = deepcopy(runtime.inventory_delta)
        return payload

    async def _broadcast_tick(self, runtime: RunRuntime) -> None:
        tick = runtime.state["tick"]
        self._record_event(runtime, tick, "metrics_snapshot", runtime.metrics)
        self._record_event(
            runtime,
            tick,
            "resource_occupied",
            {
                # 恢复的快照/单测构造的 runtime 可能没有完整指标，缺省补 0。
                "conveyorUtilization": runtime.metrics.get("conveyorUtilization", 0),
                "conveyorQueueDepth": runtime.metrics.get("conveyorQueueDepth", 0),
                "stationQueueDepth": runtime.metrics.get("stationQueueDepth", 0),
                "bufferOccupancy": runtime.metrics.get("bufferOccupancy", 0),
            },
        )
        # 库存可能包含数万条货箱记录。每个 tick 重复发送会造成大量 JSON 编解码和前端重建。
        # 常规 tick 只发送动态状态；库存仅在数量发生变化时附带，前端与上一帧合并。
        tick_payload = self._tick_payload(runtime)
        await self.hub.broadcast(runtime.run_id, {"type": "tick", "tick": tick, "payload": tick_payload})
        runtime.inventory_delta.clear()
        await self.hub.broadcast(runtime.run_id, {"type": "metrics_snapshot", "tick": tick, "payload": runtime.metrics})

    def _persist(self, runtime: RunRuntime) -> None:
        self.repo.update_run(runtime.run_id, runtime.state["status"], runtime.state, runtime.metrics)
