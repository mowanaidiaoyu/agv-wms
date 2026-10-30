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
from .conveyor import compile_conveyor_graph, nearest_lift_id, route_from_block, route_from_cell
from .goods import SKU_FIELDS, build_inventory, ctu_storage_nodes
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
TELEMETRY_EVENT_TYPES = {"metrics_snapshot", "resource_occupied", "robot_position", "conveyor_position", "arm_handoff"}
# 设备连续等待多少 tick 仍无法前进时，尝试绕开其它设备重新寻路（避免头对头死锁）。
REROUTE_AFTER_BLOCK_TICKS = 2
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
    ) -> dict[str, Any]:
        config = build_simulation_config(topology)
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
        reserved_slots = {
            (line.get("nodeId"), int(line.get("layer", 1)))
            for task in runtime.state.get("tasks", [])
            if task.get("status") not in TERMINAL_TASK_STATUSES
            for line in task.get("lines", [])
            if line.get("nodeId")
        }
        available_inventory = [
            slot
            for slot in runtime.state.get("inventory", [])
            if (slot.get("nodeId"), int(slot.get("layer", 1))) not in reserved_slots
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
        tasks, order_states = self._seed_order_tasks(
            runtime.topology,
            [order],
            available_inventory,
            existing_port_loads,
        )
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
        runtime.stopped = True
        if runtime.task_handle and not runtime.task_handle.done():
            runtime.task_handle.cancel()
            try:
                await runtime.task_handle
            except asyncio.CancelledError:
                pass
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

    def _create_runtime(
        self,
        run_id: str,
        topology: dict[str, Any],
        config: dict[str, Any],
        orders: list[dict[str, Any]] | None = None,
        transfers: list[dict[str, Any]] | None = None,
    ) -> RunRuntime:
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
        # 四向车出货位运行时设置（可在「四向车搬运」工作台调整，独立于拓扑编辑）。
        shuttle_docks = self._shuttle_dock_states(topology)
        if orders:
            tasks, order_states = self._seed_order_tasks(topology, orders, inventory)
            # 订单驱动模式同样派发四向车出库搬运任务（独立于订单、不挂订单），
            # 否则 UI 带演示订单启动时四向车搬运永远不会出现。
            tasks.extend(self._seed_shuttle_tasks(topology, shuttle_docks, inventory))
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
            "orderDriven": bool(orders or transfers),
            "robots": robots,
            "tasks": tasks,
            "orders": order_states,
            "inventory": inventory,
            "shuttleDocks": shuttle_docks,
            "transferInventory": transfer_inventory,
            "transferEvents": transfer_events,
            "alerts": [],
            "queues": {"conveyor": [], "station": [], "buffer": []},
            "conveyorItems": [],
            "pickingArm": {"status": "idle", "stage": None, "remainingTicks": 0, "taskId": None, "robotId": None} if arm_handoff else None,
            "armBeltBoxes": [],
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
        )
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
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """将订单匹配到真实库存槽，按货位邻近性拆包并智能选口，拆成 CTU 多箱任务。"""
        delivery_ports = self._ctu_delivery_ports(topology)
        if not delivery_ports:
            raise ValueError("当前拓扑没有输送线出入口，无法为订单生成交付任务")
        ctu_count = max(1, sum(1 for node in topology.get("nodes", []) if node.get("type") == "ctu_robot"))
        port_loads = dict(existing_port_loads or {})

        inventory_by_sku: dict[str, list[dict[str, Any]]] = {}
        for slot in inventory:
            sku_id = str(slot.get("skuId") or "")
            if sku_id:
                inventory_by_sku.setdefault(sku_id, []).append(slot)

        tasks: list[dict[str, Any]] = []
        order_states: list[dict[str, Any]] = []
        used_slots: set[tuple[str, int]] = set()

        for order in orders:
            allocated_lines: list[dict[str, Any]] = []
            runtime_lines: list[dict[str, Any]] = []
            missing = 0
            for requested in order.get("lines", []):
                requested_qty = max(1, int(requested.get("quantity", 1)))
                matches = [
                    slot
                    for slot in inventory_by_sku.get(str(requested.get("skuId") or ""), [])
                    if (slot["nodeId"], int(slot.get("layer", 1))) not in used_slots
                ][:requested_qty]
                for slot in matches:
                    used_slots.add((slot["nodeId"], int(slot.get("layer", 1))))
                    allocated_lines.append(dict(slot))
                missing += requested_qty - len(matches)
                runtime_lines.append(
                    {
                        **requested,
                        "allocated": len(matches),
                        "picked": 0,
                    }
                )

            dispatch_mode = str(order.get("dispatchMode") or "balanced")
            max_parallel_ctu = max(1, min(7, int(order.get("maxParallelCtu") or 7), ctu_count))
            task_groups = self._split_ctu_order_lines(allocated_lines, dispatch_mode, max_parallel_ctu)
            order_task_ids: list[str] = []
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
                task["taskIndex"] = task_index
                task["splitStrategy"] = dispatch_mode
                task["sourceGroupLabel"] = self._source_group_label(task_lines)
                task["assignedPortId"] = selected_port["id"]
                task["assignedPortLabel"] = selected_port["label"]
                task["portQueuePosition"] = port_loads.get(delivery, 0) + 1
                task["portSelectionCost"] = port_cost
                task["portSelectionReason"] = "综合取货终点距离与出库口排队时间"
                port_loads[delivery] = port_loads.get(delivery, 0) + 1
                tasks.append(task)

            requested_quantity = sum(max(1, int(line.get("quantity", 1))) for line in order.get("lines", []))
            allocated_quantity = len(allocated_lines)
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
                    "status": "exception" if missing or not order_task_ids else "waiting_dispatch",
                    "statusLabel": "库存不足" if missing else "等待调度",
                    "missingQuantity": missing,
                    "startedTick": None,
                    "completedTick": None,
                    "decomposition": {
                        "stage": "exception" if missing or not order_task_ids else "tasks_generated",
                        "requestedQuantity": requested_quantity,
                        "allocatedQuantity": allocated_quantity,
                        "missingQuantity": missing,
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

    def _first_delivery_cell(self, topology: dict[str, Any]) -> str:
        """输送线出入口（port 区域）入口格；无 port 区域时返回空串。

        订单任务由 CTU 执行（零散件），优先取架空输送线的 port；
        拓扑只画了地面线时回退第一个 port。
        """
        ground_ports, elevated_ports = self._split_ports_by_conveyor_kind(topology)
        preferred = elevated_ports or ground_ports
        return preferred[0] if preferred else ""

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
            # place 完成：货箱落到放货点带面，四向车释放、任务完成。
            box = dict((robot.get("carrying") or [{}])[0])
            belt = runtime.state.setdefault("armBeltBoxes", [])
            belt.append({
                **box,
                "cellId": target["dropoffCellId"],
                "level": target["dropoffLevel"],
                "beltHeight": target["dropoffBeltHeight"],
                "placedTick": tick,
            })
            task["deliveredGoods"] = [dict(slot) for slot in (robot.get("carrying") or [])]
            task["armPlacedCellId"] = target["dropoffCellId"]
            robot["carrying"] = []
            robot["load"] = 0
            arm.update({"status": "idle", "stage": None, "remainingTicks": 0, "taskId": None, "robotId": None})
            self._record_event(
                runtime,
                tick,
                "arm_handoff",
                {"stage": "placed", "taskId": task["id"], "robotId": robot["id"], "cellId": target["dropoffCellId"]},
            )
            self._complete_task(runtime, robot, task)
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

        tasks.extend(self._seed_shuttle_tasks(topology, shuttle_docks, inventory))
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
    ) -> list[dict[str, Any]]:
        """四向车出库搬运任务：库位 → 驶出车库 → 地面输送线交付。

        独立于订单流程（不挂订单），订单驱动 / 自动派单两种模式都会追加这组任务。
        运行时出货位设置（shuttle_docks 状态）优先：按出货位生成搬运任务（每箱一趟，
        目标箱被外层箱挡住时先生成倒库任务）；未传状态时回退读取拓扑上的出货位节点；
        都没有时自动派 6 个任务（随机库位）。
        取货源只使用「可服务货位」（邻接可达巷道），深藏货架内部的货位四向车物理上到不了。
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
            batch: dict[str, Any] = {"cursor": 0}
            for dock in docks:
                for _ in range(int(dock.get("quantity") or 1)):
                    tasks.extend(
                        self._build_dock_tasks(
                            topology, inventory or [], dock, shuttle_sources,
                            0, batch, tasks,
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
                        (b for b in lane_boxes.get((str(pick_box["cellId"]), int(pick_box.get("layer", 1))), []) if box_key(b) not in claimed_boxes),
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
        self._inject_faults(runtime, tick)
        self._inject_urgent_order(runtime, tick)
        self._assign_tasks(runtime)
        self._move_robots(runtime)
        self._advance_picking_arm(runtime)
        self._detect_contention(runtime)
        self._refresh_orders(runtime)
        self._refresh_metrics(runtime)
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
        if any(task["urgent"] and task["createdTick"] == urgent_tick for task in runtime.state["tasks"]):
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
                # 连续等待后仍无法前进：CTU 先绕开其它设备重新寻路，绕不开再处理头对头死锁；
                # 四向车同样先绕行（带面/巷道有并行道），头对头时退一格再绕。
                if robot["type"] == "ctu" and robot["waitTicks"] >= REROUTE_AFTER_BLOCK_TICKS:
                    if self._reroute_ctu(runtime, robot, occupied_cells):
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
                        robot["waitTicks"] = 0
                        robot["status"] = "moving"
                        robot["phaseLabel"] = task["phaseLabel"]
                    elif self._clear_shuttle_head_on(runtime, robot, blocker, occupied_cells, reserved_targets):
                        robot["waitTicks"] = 0
                        robot["status"] = "moving"
                        robot["phaseLabel"] = task["phaseLabel"]
                continue

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
            self._record_event(runtime, runtime.state["tick"], "robot_position", {"robotId": robot["id"], "cellId": robot["currentCellId"], "floor": floor, "row": row, "col": col, "layer": next_layer, "status": robot["status"]})

            if len(robot["path"]) <= 1:
                self._advance_robot_phase(runtime, robot, task, occupied_cells)

    def _reroute_ctu(self, runtime: RunRuntime, robot: dict[str, Any], occupied_cells: set[str]) -> bool:
        """CTU 被堵时绕行：把其它设备当前占用的格临时视为障碍，重新算一条到当前目标的路径。"""
        target = robot.get("targetCellId")
        if not target or target == robot["currentCellId"]:
            return False
        others = {parse_cell_id(cell) for cell in occupied_cells if cell != robot["currentCellId"]}
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
        # 先让想进对方格子的这台后退；它退不了再让对向那台后退。
        if self._yield_one_cell(runtime, robot, occupied_cells):
            return True
        return self._yield_one_cell(runtime, blocker, occupied_cells)

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
            if robot["type"] == "ctu" and robot["waitTicks"] >= REROUTE_AFTER_BLOCK_TICKS:
                if self._reroute_ctu(runtime, robot, occupied_cells):
                    robot["waitTicks"] = 0
                    robot["status"] = "returning"
                    robot["phaseLabel"] = "返回待命位"
                elif robot["waitTicks"] >= YIELD_AFTER_BLOCK_TICKS:
                    self._yield_ctu(runtime, robot, occupied_cells, reserved_targets)
            elif robot["type"] == "shuttle" and robot["waitTicks"] >= REROUTE_AFTER_BLOCK_TICKS:
                if self._reroute_shuttle(runtime, robot, occupied_cells):
                    robot["waitTicks"] = 0
                    robot["status"] = "returning"
                    robot["phaseLabel"] = "返回待命位"
            return

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
                robot.setdefault("carrying", []).append(slot)
                del inventory[index]
                runtime.inventory_delta.append({"op": "remove", "nodeId": node_id, "layer": layer})
                return slot
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
                approach_direction = "east" if col_delta > 0 else "west"
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
        """交付到出入口：用与取货对称的升降/伸叉/放箱动作把货箱送上输送线。"""
        tick = runtime.state["tick"]
        action = robot.get("handlingAction")

        if action is None:
            # 记录实际交付到输送线上的货箱，供前端按实载箱数渲染。
            task["deliveredGoods"] = [dict(slot) for slot in (robot.get("carrying") or [])]
            robot["handlingAction"] = {
                "type": "deliver",
                "stage": "lifting",
                "targetLayer": 1,
                "slotIndex": 0,
                "cellId": robot["currentCellId"],
            }
            robot["status"] = "handling"
            robot["phaseLabel"] = "交付升降对位"
            task["phaseLabel"] = robot["phaseLabel"]
            self._record_event(runtime, tick, "ctu_handling", {"robotId": robot["id"], **robot["handlingAction"]})
            return

        if action.get("stage") == "lifting":
            action["stage"] = "extending"
            robot["status"] = "handling"
            robot["phaseLabel"] = "伸叉送箱"
            task["phaseLabel"] = robot["phaseLabel"]
            self._record_event(runtime, tick, "ctu_handling", {"robotId": robot["id"], **action})
            return

        if action.get("stage") == "extending":
            action["stage"] = "stowing"
            robot["status"] = "handling"
            robot["phaseLabel"] = "箱入输送线"
            task["phaseLabel"] = robot["phaseLabel"]
            self._record_event(runtime, tick, "ctu_handling", {"robotId": robot["id"], **action})
            return

        # stowing：货箱已落到输送线，交给转运阶段；拓扑没有拣货位则任务直接完成。
        robot["handlingAction"] = None
        if task.get("taskKind") == "relocate":
            # 倒库交付：货箱放入目标空货道（写回带深度库存），不走上带/拣货链路。
            self._complete_shuttle_relocate(runtime, robot, task)
            return
        zone = self._pick_station_for(runtime, task)
        if zone is not None:
            self._start_conveyor_to_lift(runtime, robot, task, zone, occupied_cells)
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
        """为任务选择人工拣货位（取离交付出入口最近的一个）；仅 CTU 任务启用。"""
        if task.get("robotType") != "ctu":
            return None
        zones = [
            zone
            for zone in runtime.topology.get("zones", [])
            if zone.get("type") == "pick_station"
        ]
        if not zones:
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
        """为显式分层输送模型创建逐块运输状态；旧拓扑继续使用原固定 tick。"""
        if not runtime.conveyor_graph.get("explicit"):
            task["conveyorTransport"] = None
            return None
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
        task["conveyorTransport"] = {
            "phase": phase,
            "deck": deck,
            "route": route,
            "index": -1,
            "currentBlockId": None,
            "nextBlockId": route[0],
            "waitingTicks": 0,
            "movedTicks": 0,
            "startedTick": int(runtime.state["tick"]),
        }
        return max(1, len(route))

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
        task["phaseLabel"] = (
            f"{'下层去程' if transport.get('deck') == 'lower' else '上层回程'} "
            f"{index + 1}/{len(route)}"
        )
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

    def _start_conveyor_to_lift(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
        zone: dict[str, Any],
        occupied_cells: set[str] | None = None,
    ) -> None:
        """货物已在出入口交付到下层输送线：机器人卸货，等待下层输送线把货箱运往升降移栽机。"""
        task["transitGoods"] = [dict(slot) for slot in (robot.get("carrying") or [])]
        task["deliveredGoods"] = [dict(slot) for slot in (robot.get("carrying") or [])]
        robot["carrying"] = []
        robot["load"] = 0
        task["pickStationZoneId"] = zone.get("id")
        task["pickStationCellId"] = pick_station_entry_cell(runtime.topology, zone)
        transfer_ticks = max(1, int(runtime.config.get("conveyorTransferTicks", 6) or 6))
        self._enter_timed_stage(
            runtime,
            robot,
            task,
            "conveyor_to_lift",
            transfer_ticks,
            event_extra={
                "pickStationZoneId": task["pickStationZoneId"],
                "pickStationCellId": task["pickStationCellId"],
            },
        )
        # 不要把车停在出入口堵住后面的车；转运期间原地等待。
        self._step_off_port(runtime, robot, occupied_cells)

    def _start_picking(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any]) -> None:
        """货物已升到上层并沿上层输送线到达人工拣货位，开始自动计时拣货。"""
        pick_ticks = max(1, int(runtime.config.get("pickTicks", 6) or 6))
        self._enter_timed_stage(runtime, robot, task, "picking", pick_ticks)

    def _finish_picking(self, runtime: RunRuntime, task: dict[str, Any], robot: dict[str, Any] | None) -> None:
        """拣货计时结束（或人工跳过）：拣出的货物放到放置区；拓扑没有放置区时直接进入回程。"""
        task["remainingStageTicks"] = 0
        task["status"] = "assigned"
        drop_zone = self._drop_zone_for(runtime, task)
        if drop_zone is not None:
            task["dropZoneZoneId"] = drop_zone.get("id")
            task["dropZoneCellId"] = drop_zone_entry_cell(runtime.topology, drop_zone)
            place_ticks = max(1, int(runtime.config.get("placeTicks", 3) or 3))
            self._enter_timed_stage(
                runtime,
                robot,
                task,
                "place_goods",
                place_ticks,
                event_extra={
                    "dropZoneZoneId": task["dropZoneZoneId"],
                    "dropZoneCellId": task["dropZoneCellId"],
                },
            )
        else:
            self._start_conveyor_return(runtime, robot, task)

    def _start_conveyor_return(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any]) -> None:
        """空货箱放回上层输送线，沿原路运回上层的输送线出入口。"""
        task["returnLineIndex"] = 0
        return_ticks = max(1, int(runtime.config.get("conveyorTransferTicks", 6) or 6))
        self._enter_timed_stage(runtime, robot, task, "conveyor_return", return_ticks)

    def _advance_pickup_from_port(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any]) -> None:
        """CTU 在出入口取回经上层输送线返回的货箱，开始按原库位回库。"""
        tick = runtime.state["tick"]
        goods = task.get("transitGoods") or []
        capacity = int(robot.get("capacity", ROBOT_CARRY_CAPACITY.get("ctu", 5)))
        robot["carrying"] = [dict(slot) for slot in goods]
        robot["load"] = min(capacity, len(goods))
        task["returnLineIndex"] = 0
        task["history"].append({"tick": tick, "phase": "pickup_from_port", "status": task["status"]})
        self._record_event(
            runtime,
            tick,
            "task_state_changed",
            {"taskId": task["id"], "phase": "pickup_from_port", "status": task["status"], "picked": task.get("picked", 0)},
        )
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
        if not cancelled:
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
            if task["phase"] in {"conveyor_to_lift", "conveyor_return"} and task.get("conveyorTransport"):
                if not self._advance_conveyor_transport(runtime, robot, task):
                    return
                task["conveyorTransport"] = None
                self._advance_timed_stage(runtime, robot, task)
                return
            if task["phase"] == "lift_to_upper" and not self._lift_stage_has_priority(runtime, task):
                robot["status"] = "waiting"
                robot["phaseLabel"] = "等待升降移载机"
                return
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
            # 拣出的货物落到放置区：记录快照供前端渲染放置区货物，空货箱开始回程。
            task["placedGoods"] = [dict(line) for line in (task.get("lines") or [])]
            self._start_conveyor_return(runtime, robot, task)
            return
        if phase == "conveyor_return":
            # 货箱经上层输送线回到出入口，CTU 再到出入口取回。
            tick = runtime.state["tick"]
            task["phase"] = "pickup_from_port"
            task["phaseLabel"] = self._phase_label(task["phase"])
            task["status"] = "assigned"
            robot["status"] = "moving"
            robot["handlingAction"] = None
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
        completed = [task for task in runtime.state["tasks"] if task["completedTick"] is not None and task["startedTick"] is not None]
        completed_durations = [task["completedTick"] - task["startedTick"] for task in completed]
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
            completed = [task for task in related if task.get("status") == "completed"]
            active = [task for task in related if task.get("status") in {"assigned", "running"}]
            exceptions = [task for task in related if task.get("status") in {"exception", "failed"}]
            picked_by_sku: dict[str, int] = {}
            for task in related:
                picked_count = max(0, int(task.get("picked", 0)))
                for line in task.get("lines", [])[:picked_count]:
                    sku_id = str(line.get("skuId") or "")
                    if sku_id:
                        picked_by_sku[sku_id] = picked_by_sku.get(sku_id, 0) + 1
            total_quantity = 0
            picked_quantity = 0
            for line in order.get("lines", []):
                quantity = max(1, int(line.get("quantity", 1)))
                sku_id = str(line.get("skuId") or "")
                available_picked = picked_by_sku.get(sku_id, 0)
                picked = min(quantity, available_picked)
                picked_by_sku[sku_id] = max(0, available_picked - picked)
                line["picked"] = picked
                total_quantity += quantity
                picked_quantity += picked
            order["completedTasks"] = len(completed)
            order["totalTasks"] = len(related)
            decomposition = order.setdefault("decomposition", {})
            assigned_tasks = [task for task in related if task.get("assignedRobotId")]
            decomposition["taskCount"] = len(related)
            decomposition["assignedTaskCount"] = len(assigned_tasks)
            decomposition["activeVehicleIds"] = list(dict.fromkeys(
                str(task["assignedRobotId"]) for task in assigned_tasks if task.get("assignedRobotId")
            ))
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
            pickup_progress = picked_quantity / total_quantity if total_quantity else 0
            delivery_progress = len(completed) / len(related)
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
            "pickup_from_port": "出入口取回",
            "pickup_from_station": "拣货台取回",
            "return_to_storage": "回库",
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
