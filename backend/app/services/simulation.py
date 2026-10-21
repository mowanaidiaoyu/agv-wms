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
    build_node_lookup,
    build_occupied_lookup,
    cell_id,
    node_position,
    node_to_cell_id,
    parse_cell_id,
    port_entry_cell,
    serviceable_storage_cells,
    shortest_path,
    shortest_path_shuttle,
    shuttle_rack_slots,
)


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def find_task(tasks: list[dict[str, Any]], task_id: str | None) -> dict[str, Any] | None:
    if not task_id:
        return None
    return next((task for task in tasks if task["id"] == task_id), None)


ROBOT_CARRY_CAPACITY = {"ctu": 5, "shuttle": 1}
CTU_RACK_LAYERS = 17
# 每 tick 的推进间隔（秒）。调大=仿真更慢、机器人每步更易看清。
TICK_INTERVAL_SECONDS = 1.5
# 设备连续等待多少 tick 仍无法前进时，尝试绕开其它设备重新寻路（避免头对头死锁）。
REROUTE_AFTER_BLOCK_TICKS = 2


@dataclass
class RunRuntime:
    run_id: str
    topology: dict[str, Any]
    scenario: dict[str, Any]
    state: dict[str, Any]
    metrics: dict[str, Any]
    alerts: list[dict[str, Any]] = field(default_factory=list)
    paused: bool = False
    stopped: bool = False
    record_events: bool = True
    task_handle: asyncio.Task | None = None
    alert_memory: dict[str, int] = field(default_factory=dict)


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

    async def start(self, topology: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        runtime = self._create_runtime(run_id, topology, scenario)
        self.runtimes[run_id] = runtime
        self.repo.create_run(run_id, "running", topology["id"], scenario["id"], runtime.state, runtime.metrics)
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

    def get_events(self, run_id: str) -> list[dict[str, Any]]:
        return self.repo.list_events(run_id)

    def evaluate_algorithms(self, topology: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, Any]]:
        evaluation_cases = [
            ("dispatch", "Greedy 分配", {"dispatchPolicy": "greedy"}),
            ("dispatch", "Hungarian 分配", {"dispatchPolicy": "hungarian"}),
            ("path", "A* 路径", {"pathPolicy": "astar"}),
            ("path", "Dijkstra 路径", {"pathPolicy": "dijkstra"}),
            ("conveyor", "放行阈值 50%", {"conveyorPolicy": {"releaseThreshold": 0.5, "fifo": True}}),
            ("conveyor", "放行阈值 80%", {"conveyorPolicy": {"releaseThreshold": 0.8, "fifo": True}}),
        ]
        results: list[dict[str, Any]] = []
        for category, label, overrides in evaluation_cases:
            payload = self._merge_scenario_overrides(deepcopy(scenario), overrides)
            runtime = self._create_runtime("eval", topology, payload, record_events=False)
            self._run_offline(runtime)
            results.append(
                {
                    "category": category,
                    "label": label,
                    "dispatchPolicy": payload["dispatchPolicy"],
                    "pathPolicy": payload["pathPolicy"],
                    "releaseThreshold": payload["conveyorPolicy"]["releaseThreshold"],
                    "throughput": runtime.metrics["throughput"],
                    "avgCompletionTicks": runtime.metrics["avgCompletionTicks"],
                    "conveyorUtilization": runtime.metrics["conveyorUtilization"],
                    "congestionEvents": runtime.metrics["congestionEvents"],
                    "deadlocksDetected": runtime.metrics["deadlocksDetected"],
                    "urgentOrdersHandled": runtime.metrics["urgentOrdersHandled"],
                    "summary": "吞吐量越高越好，平均完成时长、拥堵次数和死锁次数越低越好。",
                }
            )
        return results

    def _merge_scenario_overrides(self, scenario: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(scenario.get(key), dict):
                scenario[key] = {**scenario[key], **value}
            else:
                scenario[key] = value
        return scenario

    def _require_runtime(self, run_id: str) -> RunRuntime:
        runtime = self.runtimes.get(run_id)
        if not runtime:
            raise KeyError(run_id)
        return runtime

    def _create_runtime(
        self,
        run_id: str,
        topology: dict[str, Any],
        scenario: dict[str, Any],
        *,
        record_events: bool = True,
    ) -> RunRuntime:
        node_lookup = build_node_lookup(topology)
        robots: list[dict[str, Any]] = []
        for robot in scenario["robots"]:
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
                    "waitTicks": 0,
                    "failed": False,
                    "phaseLabel": "待命",
                    "load": 0,
                    "carrying": [],
                    "capacity": ROBOT_CARRY_CAPACITY.get(robot["type"], 1),
                }
            )

        inventory = build_inventory(topology)
        tasks = self._seed_tasks(topology, scenario, inventory)
        state = {
            "id": run_id,
            "status": "running",
            "tick": 0,
            "topologyId": topology["id"],
            "scenarioId": scenario["id"],
            "robots": robots,
            "tasks": tasks,
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
            scenario=deepcopy(scenario),
            state=state,
            metrics=metrics,
            record_events=record_events,
        )

    def _first_delivery_cell(self, topology: dict[str, Any]) -> str:
        """输送线出入口（port 区域）入口格；无 port 区域时返回空串。"""
        for zone in topology.get("zones", []):
            if zone.get("type") == "port":
                return port_entry_cell(topology, zone)
        return ""

    def _seed_tasks(self, topology: dict[str, Any], scenario: dict[str, Any], inventory: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if inventory is None:
            inventory = build_inventory(topology)
        order_gen = scenario.get("orderGenerator") or {}
        rng = random.Random(scenario.get("seed", 0))

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
            "robotType": robot_type,
            "mode": mode,
            "status": "pending",
            "phase": "pending_assignment",
            "phaseLabel": "待分配",
            "sourceCellId": source_cell,
            "sourceLayer": source_layer,
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
            "history": [],
            "createdTick": created_tick,
            "startedTick": None,
            "completedTick": None,
        }

    async def _loop(self, runtime: RunRuntime) -> None:
        duration = runtime.scenario["durationTicks"]
        while runtime.state["tick"] < duration:
            if runtime.stopped:
                break
            if runtime.paused:
                await asyncio.sleep(0.25)
                continue
            self._step_runtime(runtime, persist=True, broadcast=True)
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

        runtime.state["status"] = "stopped" if runtime.stopped else "completed"
        self._persist(runtime)
        await self.hub.broadcast(runtime.run_id, {"type": "simulation_completed", "tick": runtime.state["tick"], "payload": runtime.metrics})

    def _run_offline(self, runtime: RunRuntime) -> None:
        duration = runtime.scenario["durationTicks"]
        while runtime.state["tick"] < duration and not runtime.stopped:
            self._step_runtime(runtime, persist=False, broadcast=False)
        runtime.state["status"] = "completed"

    def _step_runtime(self, runtime: RunRuntime, *, persist: bool, broadcast: bool) -> None:
        runtime.state["tick"] += 1
        runtime.state["updatedAt"] = now_dt().isoformat()
        tick = runtime.state["tick"]
        self._inject_faults(runtime, tick)
        self._inject_urgent_order(runtime, tick)
        self._assign_tasks(runtime)
        self._move_robots(runtime)
        self._detect_contention(runtime)
        self._refresh_metrics(runtime)
        if broadcast:
            asyncio.create_task(self._broadcast_tick(runtime))
        if persist:
            self._persist(runtime)

    def _inject_faults(self, runtime: RunRuntime, tick: int) -> None:
        config = runtime.scenario["faultInjection"]
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
                        runtime.scenario["pathPolicy"],
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
        urgent_tick = runtime.scenario["orderGenerator"].get("urgentInsertionTick")
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
            runtime.scenario["dispatchPolicy"],
            runtime.state["robots"],
            pending,
            runtime.topology,
            runtime.scenario["pathPolicy"],
            failed_cells,
        )
        for robot_id, task_id in matches:
            robot = next(item for item in runtime.state["robots"] if item["id"] == robot_id)
            task = find_task(runtime.state["tasks"], task_id)
            if task is None:
                continue
            phase = "pickup_from_ctu" if task["mode"] == "direct" else "pickup_from_shuttle"
            if not self._robot_path_to(runtime, robot, task["sourceCellId"], task["sourceLayer"], failed_cells):
                continue
            robot["currentTaskId"] = task_id
            robot["status"] = "moving"
            robot["waitTicks"] = 0
            robot["load"] = 0
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
        movable = [robot for robot in runtime.state["robots"] if robot["status"] not in {"failed", "idle"}]
        movable.sort(key=lambda item: item["id"])
        occupied_cells = {robot["currentCellId"] for robot in runtime.state["robots"]}
        reserved_targets: set[str] = set()

        for robot in movable:
            task = find_task(runtime.state["tasks"], robot["currentTaskId"])
            if task is None:
                robot["status"] = "idle"
                robot["phaseLabel"] = "待命"
                robot["path"] = [robot["currentCellId"]]
                robot["pathLayers"] = [robot.get("currentLayer", 1)]
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
            runtime.scenario["pathPolicy"],
            avoid_storage=True,
            extra_blocked=others,
        )
        if not path_cells or len(path_cells) <= 1:
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
        if blocker is None or blocker["status"] in {"failed", "idle"}:
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
            runtime.scenario["pathPolicy"],
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

    def _transfer_goods_to_robot(self, runtime: RunRuntime, robot: dict[str, Any], line: dict[str, Any]) -> None:
        """CTU 取到一箱：把该库位槽从库存移除，并把货物挂到机器人 carrying，前端据此把货箱从库位移到车上。"""
        node_id = line.get("nodeId")
        if node_id is None:
            return
        layer = int(line.get("layer", 1))
        inventory = runtime.state.get("inventory") or []
        for index, slot in enumerate(inventory):
            if slot.get("nodeId") == node_id and int(slot.get("layer", 1)) == layer:
                robot.setdefault("carrying", []).append(slot)
                del inventory[index]
                return

    def _advance_robot_phase(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
        occupied_cells: set[str] | None = None,
    ) -> None:
        tick = runtime.state["tick"]
        if task["phase"] == "pickup_from_ctu":
            capacity = robot.get("capacity", ROBOT_CARRY_CAPACITY["ctu"])
            lines = task.get("lines", [])
            line_index = task.get("currentLineIndex", 0)
            picked_line = lines[line_index] if 0 <= line_index < len(lines) else {}
            self._transfer_goods_to_robot(runtime, robot, picked_line)
            robot["load"] = min(capacity, robot.get("load", 0) + 1)
            task["picked"] = task.get("picked", 0) + 1
            task["currentLineIndex"] = line_index + 1
            if task["currentLineIndex"] < len(lines) and robot["load"] < capacity:
                next_cell = lines[task["currentLineIndex"]]["cellId"]
                self._set_robot_leg(runtime, robot, task, "pickup_from_ctu", next_cell)
                task["phaseLabel"] = f"CTU 取货 {task['picked']}/{len(lines)}"
                robot["phaseLabel"] = f"CTU 取货 {robot['load']}/{capacity}"
            else:
                self._set_robot_leg(runtime, robot, task, "delivery_to_port", task["deliveryCellId"])
                task["phaseLabel"] = f"输送线交付（{task['picked']} 箱）"
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            event: dict[str, Any] = {"taskId": task["id"], "phase": task["phase"], "status": task["status"], "picked": task["picked"], "load": robot["load"]}
            if picked_line.get("skuId"):
                event["skuId"] = picked_line["skuId"]
                event["skuName"] = picked_line.get("skuName")
            self._record_event(runtime, tick, "task_state_changed", event)
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
            task["status"] = "completed"
            task["phase"] = "completed"
            task["phaseLabel"] = self._phase_label(task["phase"])
            task["completedTick"] = tick
            robot["status"] = "idle"
            robot["phaseLabel"] = "待命"
            robot["currentTaskId"] = None
            robot["path"] = [robot["currentCellId"]]
            robot["pathLayers"] = [robot.get("currentLayer", 1)]
            robot["load"] = 0
            robot["carrying"] = []
            runtime.metrics["completedTasks"] += 1
            runtime.metrics["throughput"] += 1
            if task.get("urgent"):
                runtime.metrics["urgentOrdersHandled"] += 1
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "phase": task["phase"], "status": task["status"]})
            # 交付完成后退回一格，别把空车停在输送线出入口堵住后面的车。
            self._step_off_port(runtime, robot, occupied_cells)
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
        extra_blocked: set[tuple[str, int, int]] | None = None,
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
                runtime.scenario["pathPolicy"],
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
                runtime.scenario["pathPolicy"],
                avoid_storage=True,
                extra_blocked=extra_blocked,
            )
            if not path_cells:
                return False
            robot["path"] = [cell["id"] for cell in path_cells]
            robot["pathLayers"] = [1] * len(robot["path"])
        robot["targetCellId"] = target_cell_id
        return True

    def _set_robot_leg(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any], phase: str, target_cell_id: str | None, target_layer: int | None = None) -> None:
        if not target_cell_id:
            return
        self._robot_path_to(runtime, robot, target_cell_id, target_layer)
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
        active = [robot for robot in runtime.state["robots"] if robot["status"] == "moving"]
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

    def _record_event(self, runtime: RunRuntime, tick: int, event_type: str, payload: dict[str, Any]) -> None:
        if runtime.record_events:
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
            "completed": "已完成",
        }
        return mapping.get(phase, phase)

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
        await self.hub.broadcast(runtime.run_id, {"type": "tick", "tick": tick, "payload": runtime.state})
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
