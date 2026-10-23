from __future__ import annotations

import asyncio
import random
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from ..core.repository import Repository
from .assignment import assign_tasks
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
)
from .simulation_config import build_simulation_config


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
# 设备连续等待多少 tick 仍无法前进时，尝试绕开其它设备重新寻路（避免头对头死锁）。
REROUTE_AFTER_BLOCK_TICKS = 2
YIELD_AFTER_BLOCK_TICKS = 4
MIN_SAFETY_TICKS = 2000


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
                await websocket.send_json(message)
            except Exception:
                self.disconnect(run_id, websocket)


class SimulationService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.hub = WebSocketHub()
        self.runtimes: dict[str, RunRuntime] = {}

    async def start(self, topology: dict[str, Any], orders: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        config = build_simulation_config(topology)
        if not config["robots"]:
            raise ValueError("当前拓扑没有 CTU 或四向车设备，无法启动仿真")
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        runtime = self._create_runtime(run_id, topology, config, orders)
        self.runtimes[run_id] = runtime
        self.repo.create_run(run_id, "running", topology["id"], runtime.state, runtime.metrics)
        runtime.task_handle = asyncio.create_task(self._loop(runtime))
        return {"runId": run_id, "status": "running"}

    def pause(self, run_id: str) -> dict[str, Any]:
        runtime = self._require_runtime(run_id)
        runtime.paused = True
        runtime.state["status"] = "paused"
        self._persist(runtime)
        return {"runId": run_id, "status": "paused"}

    def resume(self, run_id: str) -> dict[str, Any]:
        runtime = self._require_runtime(run_id)
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
    ) -> RunRuntime:
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

        inventory = build_inventory(topology)
        if orders:
            tasks, order_states = self._seed_order_tasks(topology, orders, inventory)
        else:
            tasks = self._seed_tasks(topology, config, inventory)
            order_states = []
        state = {
            "id": run_id,
            "status": "running",
            "stopReason": None,
            "tick": 0,
            "topologyId": topology["id"],
            "orderDriven": bool(orders),
            "robots": robots,
            "tasks": tasks,
            "orders": order_states,
            "inventory": inventory,
            "alerts": [],
            "queues": {"conveyor": [], "station": [], "buffer": []},
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
        return RunRuntime(
            run_id=run_id,
            topology=deepcopy(topology),
            config=deepcopy(config),
            state=state,
            metrics=metrics,
        )

    def _seed_order_tasks(
        self,
        topology: dict[str, Any],
        orders: list[dict[str, Any]],
        inventory: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """将演示订单匹配到真实库存槽，并拆成可由 CTU 执行的多箱任务。"""
        delivery = self._first_delivery_cell(topology)
        if not delivery:
            raise ValueError("当前拓扑没有输送线出入口，无法为订单生成交付任务")

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

            order_task_ids: list[str] = []
            for task_index in range(0, len(allocated_lines), ROBOT_CARRY_CAPACITY["ctu"]):
                task_lines = allocated_lines[task_index : task_index + ROBOT_CARRY_CAPACITY["ctu"]]
                task_id = f"task-{order['id']}-{task_index // ROBOT_CARRY_CAPACITY['ctu'] + 1}"
                order_task_ids.append(task_id)
                tasks.append(
                    self._build_task(
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
                )

            order_states.append(
                {
                    "id": order["id"],
                    "priority": order.get("priority", "normal"),
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
                }
            )
        return tasks, order_states

    def _first_delivery_cell(self, topology: dict[str, Any]) -> str:
        """输送线出入口（port 区域）入口格；无 port 区域时返回空串。"""
        for zone in topology.get("zones", []):
            if zone.get("type") == "port":
                return port_entry_cell(topology, zone)
        return ""

    def _seed_tasks(self, topology: dict[str, Any], config: dict[str, Any], inventory: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
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
        shuttle_sources = shuttle_rack_slots(topology)

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

        max_orders = max(1, int(order_gen.get("maxOrders", 8)))
        lines_min = max(1, int(order_gen.get("linesPerOrderMin", 1)))
        lines_max = max(lines_min, int(order_gen.get("linesPerOrderMax", lines_min)))
        # 单趟最多装满一车；订单行数暂不超载箱容量（多趟回程留待后续）。
        lines_max = min(lines_max, ROBOT_CARRY_CAPACITY["ctu"])

        tasks: list[dict[str, Any]] = []
        for index in range(max_orders):
            delivery = delivery_cells[index % len(delivery_cells)]
            line_count = rng.randint(lines_min, lines_max)
            lines = take_lines(line_count)
            if not lines:
                break
            tasks.append(self._build_task(topology, f"task-ctu-{index + 1}", "ctu", "direct", lines, delivery, False, 2))

        for index in range(6):
            if not shuttle_sources:
                break
            cell, layer = shuttle_sources[index % len(shuttle_sources)]
            delivery = delivery_cells[index % len(delivery_cells)]
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
        while runtime.state["tick"] < safety_limit:
            if runtime.stopped:
                break
            if runtime.paused:
                await asyncio.sleep(0.25)
                continue
            self._step_runtime(runtime, persist=True, broadcast=True)
            if self._all_tasks_completed(runtime):
                break
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

        if runtime.stopped:
            runtime.state["status"] = "stopped"
            runtime.state["stopReason"] = runtime.state.get("stopReason") or "user"
        elif self._all_tasks_completed(runtime):
            runtime.state["status"] = "completed"
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

    def _all_tasks_completed(self, runtime: RunRuntime) -> bool:
        tasks = runtime.state.get("tasks", [])
        return not tasks or all(task.get("status") == "completed" for task in tasks)

    def _step_runtime(self, runtime: RunRuntime, *, persist: bool, broadcast: bool) -> None:
        runtime.state["tick"] += 1
        runtime.state["updatedAt"] = now_dt().isoformat()
        tick = runtime.state["tick"]
        self._inject_faults(runtime, tick)
        self._inject_urgent_order(runtime, tick)
        self._assign_tasks(runtime)
        self._move_robots(runtime)
        self._detect_contention(runtime)
        self._refresh_orders(runtime)
        self._refresh_metrics(runtime)
        if broadcast:
            asyncio.create_task(self._broadcast_tick(runtime))
        if persist:
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
        matches = assign_tasks(
            runtime.config.get("dispatchPolicy", "greedy"),
            runtime.state["robots"],
            pending,
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
            current_cell_id = robot["currentCellId"]
            next_cell_id = path[1]
            next_layer = path_layers[1] if len(path_layers) > 1 else robot.get("currentLayer", 1)
            occupied_cells.discard(current_cell_id)

            if next_cell_id in occupied_cells or next_cell_id in reserved_targets:
                robot["status"] = "blocked"
                robot["waitTicks"] += 1
                robot["phaseLabel"] = f"{task['phaseLabel']} / 等待让行"
                occupied_cells.add(current_cell_id)
                blocker = next((item for item in movable if item["currentCellId"] == next_cell_id), None)
                # 连续等待后仍无法前进：CTU 先绕开其它设备重新寻路，绕不开再处理头对头死锁。
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
            robot["battery"] = max(10.0, robot["battery"] - 0.35)
            robot["waitTicks"] = 0
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
                self._set_robot_leg(runtime, robot, task, "delivery_to_port", task["deliveryCellId"])
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
                task["status"] = "exception"
                task["phaseLabel"] = robot["phaseLabel"]
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
                self._set_robot_leg(runtime, robot, task, "pickup_from_ctu", next_line["cellId"], next_line.get("layer", 1))
                task["phaseLabel"] = f"CTU 取货 {task['picked']}/{len(lines)}"
                robot["phaseLabel"] = f"CTU 取货 {robot['load']}/{capacity}"
            else:
                self._set_robot_leg(runtime, robot, task, "delivery_to_port", task["deliveryCellId"])
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
        zone = self._pick_station_for(runtime, task)
        if zone is not None:
            self._start_conveyor_to_lift(runtime, robot, task, zone, occupied_cells)
        else:
            self._complete_task(runtime, robot, task, occupied_cells)

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
        task["remainingStageTicks"] = max(1, int(ticks))
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
        lines = task.get("lines") or []
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
        lines = task.get("lines") or []
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
        task["status"] = "completed"
        task["phase"] = "completed"
        task["phaseLabel"] = self._phase_label(task["phase"])
        task["completedTick"] = tick
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
            other["id"] != task["id"] and other.get("status") != "completed"
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
        runtime.metrics["completedTasks"] += 1
        runtime.metrics["throughput"] += 1
        if task.get("urgent"):
            runtime.metrics["urgentOrdersHandled"] += 1
        task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
        self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "phase": task["phase"], "status": task["status"]})

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
        if task["phase"] == "pickup_from_ctu":
            self._advance_ctu_pickup_action(runtime, robot, task)
            return

        if task["phase"] == "pickup_from_shuttle":
            robot["load"] = min(robot.get("capacity", 1), robot.get("load", 0) + 1)
            task["picked"] = task.get("picked", 0) + 1
            self._set_robot_leg(runtime, robot, task, "delivery_to_port", task["deliveryCellId"], target_layer=1)
            task["phaseLabel"] = "输送线交付"
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "phase": task["phase"], "status": task["status"]})
            return

        if task["phase"] == "delivery_to_port":
            self._advance_ctu_delivery_action(runtime, robot, task, occupied_cells)
            return

        if task["phase"] in {
            "conveyor_to_lift",
            "lift_to_upper",
            "conveyor_to_pick",
            "picking",
            "place_goods",
            "conveyor_return",
        }:
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
            transfer_ticks = max(1, int(runtime.config.get("conveyorTransferTicks", 6) or 6))
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

    def _set_robot_leg(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any], phase: str, target_cell_id: str | None, target_layer: int | None = None) -> None:
        if not target_cell_id:
            return
        self._robot_path_to(
            runtime,
            robot,
            target_cell_id,
            target_layer,
            stop_adjacent=phase in {"pickup_from_ctu", "return_to_storage"},
        )
        robot["handlingAction"] = None
        robot["status"] = "moving"
        robot["phaseLabel"] = self._phase_label(phase)
        task["phase"] = phase
        task["phaseLabel"] = self._phase_label(phase)
        task["etaTicks"] = max(len(robot["path"]) - 1, 1)

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
        pending = [task for task in runtime.state["tasks"] if task["status"] != "completed"]
        active = [robot for robot in runtime.state["robots"] if robot["status"] in {"moving", "handling", "returning"}]
        blocked = [robot for robot in runtime.state["robots"] if robot["status"] == "blocked"]
        completed = [task for task in runtime.state["tasks"] if task["completedTick"] is not None and task["startedTick"] is not None]
        completed_durations = [task["completedTick"] - task["startedTick"] for task in completed]

        runtime.metrics.update(
            {
                "pendingTasks": len(pending),
                "activeRobots": len(active),
                "blockedRobots": len(blocked),
                "conveyorUtilization": 0.0,
                "avgEtaTicks": round(sum(task.get("etaTicks", 0) for task in pending) / len(pending), 2) if pending else 0.0,
                "avgCompletionTicks": round(sum(completed_durations) / len(completed_durations), 2) if completed_durations else 0.0,
                "stationQueueDepth": 0,
                "conveyorQueueDepth": 0,
                "bufferOccupancy": 0,
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
            exceptions = [task for task in related if task.get("status") == "exception"]
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
            # 取货占 80%，送达占 20%，让订单执行中也能给出真实进度而不是长期停在 0%。
            pickup_progress = picked_quantity / total_quantity if total_quantity else 0
            delivery_progress = len(completed) / len(related)
            order["progress"] = round(pickup_progress * 80 + delivery_progress * 20)
            if len(completed) == len(related):
                order["status"] = "completed"
                order["statusLabel"] = "已完成"
                order["progress"] = 100
                order["completedTick"] = max(task.get("completedTick") or 0 for task in completed)
            elif exceptions:
                order["status"] = "exception"
                order["statusLabel"] = exceptions[0].get("phaseLabel") or "取货异常"
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
        inventory = runtime.state.get("inventory") or []
        payload = {key: value for key, value in runtime.state.items() if key != "inventory"}
        if len(inventory) != runtime.last_broadcast_inventory_size:
            payload["inventory"] = inventory
            runtime.last_broadcast_inventory_size = len(inventory)
        return payload

    async def _broadcast_tick(self, runtime: RunRuntime) -> None:
        tick = runtime.state["tick"]
        self._record_event(runtime, tick, "metrics_snapshot", runtime.metrics)
        self._record_event(
            runtime,
            tick,
            "resource_occupied",
            {
                "conveyorUtilization": runtime.metrics["conveyorUtilization"],
                "conveyorQueueDepth": runtime.metrics["conveyorQueueDepth"],
                "stationQueueDepth": runtime.metrics["stationQueueDepth"],
                "bufferOccupancy": runtime.metrics["bufferOccupancy"],
            },
        )
        # 库存可能包含数万条货箱记录。每个 tick 重复发送会造成大量 JSON 编解码和前端重建。
        # 常规 tick 只发送动态状态；库存仅在数量发生变化时附带，前端与上一帧合并。
        tick_payload = self._tick_payload(runtime)
        await self.hub.broadcast(runtime.run_id, {"type": "tick", "tick": tick, "payload": tick_payload})
        await self.hub.broadcast(runtime.run_id, {"type": "metrics_snapshot", "tick": tick, "payload": runtime.metrics})
        for robot in runtime.state["robots"]:
            await self.hub.broadcast(
                runtime.run_id,
                {
                    "type": "robot_position",
                    "tick": tick,
                    "payload": {
                        "robotId": robot["id"],
                        "cellId": robot["currentCellId"],
                        "floor": robot.get("currentFloor", "1F"),
                        "row": robot["currentRow"],
                        "col": robot["currentCol"],
                        "layer": robot.get("currentLayer", 1),
                        "status": robot["status"],
                        "phaseLabel": robot["phaseLabel"],
                    },
                },
            )

    def _persist(self, runtime: RunRuntime) -> None:
        self.repo.update_run(runtime.run_id, runtime.state["status"], runtime.state, runtime.metrics)
