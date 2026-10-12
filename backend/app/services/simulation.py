from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from ..core.repository import Repository
from .assignment import assign_tasks
from .pathfinding import build_node_lookup, cell_id, node_to_cell_id, parse_cell_id, shortest_path


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def find_task(tasks: list[dict[str, Any]], task_id: str | None) -> dict[str, Any] | None:
    if not task_id:
        return None
    return next((task for task in tasks if task["id"] == task_id), None)


DEVICE_NODE_TO_ROBOT_TYPE = {
    "ctu_robot": "ctu",
    "shuttle_robot": "shuttle",
}
ROBOT_TYPE_TO_DEVICE_NODE = {value: key for key, value in DEVICE_NODE_TO_ROBOT_TYPE.items()}


def build_device_node_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        node["id"]: node
        for node in topology.get("nodes", [])
        if node.get("type") in DEVICE_NODE_TO_ROBOT_TYPE
    }


def validate_runtime_inputs(topology: dict[str, Any], scenario: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    topology_id = topology.get("id")
    scenario_topology_id = scenario.get("topologyId")
    if topology_id and scenario_topology_id and topology_id != scenario_topology_id:
        errors.append(
            f"Scenario {scenario.get('id', '')} references topology {scenario_topology_id}, but runtime requested {topology_id}."
        )

    node_lookup = build_node_lookup(topology)
    device_node_lookup = build_device_node_lookup(topology)
    scenario_robot_lookup = {
        robot.get("id"): robot
        for robot in scenario.get("robots", [])
        if robot.get("id")
    }

    if not device_node_lookup:
        errors.append(
            f"Topology {topology_id} has no CTU or shuttle device nodes. Add ctu_robot or shuttle_robot nodes before starting simulation."
        )

    for device_id, device_node in device_node_lookup.items():
        robot = scenario_robot_lookup.get(device_id)
        if robot is None:
            errors.append(
                f"Topology device node {device_id} is missing from scenario {scenario.get('id', '')}. Open the scenario page and sync device parameters."
            )
            continue

        expected_type = DEVICE_NODE_TO_ROBOT_TYPE[device_node["type"]]
        if robot.get("type") != expected_type:
            errors.append(
                f"Scenario robot {device_id} has type {robot.get('type')}, but topology node {device_id} is {device_node.get('type')}."
            )
        if robot.get("startNode") != device_id:
            errors.append(
                f"Scenario robot {device_id} must use startNode {device_id}, but received {robot.get('startNode')}."
            )

    for robot in scenario.get("robots", []):
        robot_id = robot.get("id", "")
        start_node_id = robot.get("startNode")
        device_node = device_node_lookup.get(robot_id)
        if device_node is None:
            errors.append(
                f"Scenario robot {robot_id} has no matching topology device node. Add a CTU/四向车 node with the same id in topology first."
            )
            continue

        expected_node_type = ROBOT_TYPE_TO_DEVICE_NODE.get(robot.get("type"))
        if device_node.get("type") != expected_node_type:
            errors.append(
                f"Scenario robot {robot_id} expects topology node type {expected_node_type}, but found {device_node.get('type')}."
            )
        if start_node_id not in node_lookup:
            errors.append(
                f"Robot {robot_id} references missing start node {start_node_id} in topology {topology_id}."
            )
    return errors


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
        runtime_errors = validate_runtime_inputs(topology, scenario)
        if runtime_errors:
            raise ValueError(" ".join(runtime_errors))

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
                    "status": "idle",
                    "battery": robot["battery"],
                    "path": [start_cell],
                    "targetCellId": start_cell,
                    "currentTaskId": None,
                    "waitTicks": 0,
                    "failed": False,
                    "phaseLabel": "待命",
                }
            )

        tasks = self._seed_tasks(topology)
        state = {
            "id": run_id,
            "status": "running",
            "tick": 0,
            "topologyId": topology["id"],
            "scenarioId": scenario["id"],
            "robots": robots,
            "tasks": tasks,
            "resources": self._build_resource_state(topology),
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

    def _build_resource_state(self, topology: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": resource["id"],
                "label": resource["label"],
                "type": resource["type"],
                "capacity": resource.get("capacity", 1),
                "cellIds": resource.get("cellIds", []),
                "nodeIds": resource.get("nodeIds", []),
                "occupiedBy": [],
                "queue": [],
                "meta": resource.get("meta", {}),
            }
            for resource in topology.get("resources", [])
        ]

    def _seed_tasks(self, topology: dict[str, Any]) -> list[dict[str, Any]]:
        nodes = topology["nodes"]
        ctu_sources = [node["id"] for node in nodes if node["type"] in {"storage", "buffer"} and node.get("zoneId") == "zone-ctu"]
        shuttle_sources = [node["id"] for node in nodes if node["type"] in {"storage", "buffer"} and node.get("zoneId") == "zone-shuttle"]
        stations = [node["id"] for node in nodes if node["type"] == "station"]
        entry_nodes = [node["id"] for node in nodes if node["type"] == "entry"]
        transfer_in = next((node["id"] for node in nodes if node["type"] == "conveyor_gate" and node.get("zoneId") == "zone-conveyor"), stations[0] if stations else "")
        transfer_out = next((node["id"] for node in reversed(nodes) if node["type"] == "conveyor_gate"), transfer_in)

        if not ctu_sources:
            ctu_sources = [node["id"] for node in nodes if node["type"] in {"storage", "buffer"}]
        if not shuttle_sources:
            shuttle_sources = [node["id"] for node in nodes if node["type"] in {"storage", "buffer"}]
        if not stations:
            stations = [node["id"] for node in nodes]
        if not entry_nodes:
            entry_nodes = stations

        tasks: list[dict[str, Any]] = []
        for index in range(8):
            source = ctu_sources[index % len(ctu_sources)]
            station = stations[index % len(stations)]
            entry = entry_nodes[index % len(entry_nodes)]
            tasks.append(self._build_task(topology, f"task-ctu-{index + 1}", "ctu", "direct", source, station, entry, None, None, False, 2))

        for index in range(6):
            source = shuttle_sources[index % len(shuttle_sources)]
            station = stations[index % len(stations)]
            entry = entry_nodes[index % len(entry_nodes)]
            tasks.append(self._build_task(topology, f"task-shuttle-{index + 1}", "shuttle", "cross_zone", source, station, entry, transfer_out, transfer_in, False, 2))
        return tasks

    def _build_task(
        self,
        topology: dict[str, Any],
        task_id: str,
        robot_type: str,
        mode: str,
        source_node: str,
        station_node: str,
        entry_node: str,
        handoff_node: str | None,
        conveyor_exit_node: str | None,
        urgent: bool,
        priority: int,
        *,
        created_tick: int = 0,
    ) -> dict[str, Any]:
        return {
            "id": task_id,
            "robotType": robot_type,
            "mode": mode,
            "status": "pending",
            "phase": "pending_assignment",
            "phaseLabel": "待分配",
            "sourceNode": source_node,
            "targetNode": station_node,
            "stationNode": station_node,
            "entryNode": entry_node,
            "handoffNode": handoff_node,
            "conveyorExitNode": conveyor_exit_node,
            "sourceCellId": node_to_cell_id(topology, source_node),
            "targetCellId": node_to_cell_id(topology, station_node),
            "stationCellId": node_to_cell_id(topology, station_node),
            "entryCellId": node_to_cell_id(topology, entry_node),
            "handoffCellId": node_to_cell_id(topology, handoff_node) if handoff_node else None,
            "conveyorExitCellId": node_to_cell_id(topology, conveyor_exit_node) if conveyor_exit_node else None,
            "stationResourceId": None,
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
            await asyncio.sleep(0.2)

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
        self._process_conveyor(runtime)
        self._process_station(runtime)
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

        if config.get("conveyorJammedTick") == tick:
            conveyor = self._resource(runtime, "conveyor_segment")
            if conveyor:
                conveyor["meta"]["jammedUntil"] = tick + 6
            self._emit_alert(
                runtime,
                tick,
                category="conveyor_jam",
                level="warning",
                message=f"输送线在 tick {tick} 进入拥堵状态。",
                details={"resourceId": conveyor["id"] if conveyor else None},
                suggestion="降低放行阈值，或启用缓存位缓冲。",
            )

    def _inject_urgent_order(self, runtime: RunRuntime, tick: int) -> None:
        urgent_tick = runtime.scenario["orderGenerator"].get("urgentInsertionTick")
        if urgent_tick != tick:
            return
        if any(task["urgent"] and task["createdTick"] == urgent_tick for task in runtime.state["tasks"]):
            return
        source_node = self._first_node(runtime.topology, {"storage", "buffer"})
        station_node = self._first_node(runtime.topology, {"station"}, fallback=source_node)
        entry_node = self._first_node(runtime.topology, {"entry"}, fallback=station_node)
        if not source_node or not station_node or not entry_node:
            return
        task = self._build_task(runtime.topology, f"task-urgent-{tick}", "ctu", "direct", source_node, station_node, entry_node, None, None, True, 1, created_tick=tick)
        runtime.state["tasks"].insert(0, task)
        self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "status": "pending", "urgent": True})

    def _assign_tasks(self, runtime: RunRuntime) -> None:
        pending = [task for task in runtime.state["tasks"] if task["status"] == "pending"]
        if not pending:
            return
        pending.sort(key=lambda task: (task["priority"], task["id"]))
        matches = assign_tasks(runtime.scenario["dispatchPolicy"], runtime.state["robots"], pending, runtime.topology, runtime.scenario["pathPolicy"])
        for robot_id, task_id in matches:
            robot = next(item for item in runtime.state["robots"] if item["id"] == robot_id)
            task = find_task(runtime.state["tasks"], task_id)
            if task is None:
                continue
            phase = "pickup_from_ctu" if task["mode"] == "direct" else "pickup_from_shuttle"
            path_cells = shortest_path(runtime.topology, robot["currentCellId"], task["sourceCellId"], runtime.scenario["pathPolicy"])
            if not path_cells:
                continue
            robot["path"] = [cell["id"] for cell in path_cells]
            robot["targetCellId"] = task["sourceCellId"]
            robot["currentTaskId"] = task_id
            robot["status"] = "moving"
            robot["waitTicks"] = 0
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
                continue

            if task["phase"] in {"waiting_for_conveyor", "conveyor_transfer", "station_queue"}:
                robot["status"] = "idle"
                robot["phaseLabel"] = "待命"
                robot["currentTaskId"] = None
                robot["path"] = [robot["currentCellId"]]
                continue

            path = robot["path"]
            if len(path) <= 1:
                self._advance_robot_phase(runtime, robot, task)
                continue

            current_cell_id = robot["currentCellId"]
            next_cell_id = path[1]
            occupied_cells.discard(current_cell_id)

            if next_cell_id in occupied_cells or next_cell_id in reserved_targets:
                robot["status"] = "blocked"
                robot["waitTicks"] += 1
                robot["phaseLabel"] = f"{task['phaseLabel']} / 等待让行"
                occupied_cells.add(current_cell_id)
                continue

            robot["status"] = "handoff" if task["phase"] == "handoff_to_conveyor" else "moving"
            robot["currentCellId"] = next_cell_id
            robot["currentNode"] = next_cell_id
            floor, row, col = parse_cell_id(next_cell_id)
            robot["currentFloor"] = floor
            robot["currentRow"] = row
            robot["currentCol"] = col
            robot["path"] = path[1:]
            robot["battery"] = max(10.0, robot["battery"] - 0.35)
            robot["waitTicks"] = 0
            robot["phaseLabel"] = task["phaseLabel"]
            occupied_cells.add(next_cell_id)
            reserved_targets.add(next_cell_id)
            self._record_event(runtime, runtime.state["tick"], "robot_position", {"robotId": robot["id"], "cellId": robot["currentCellId"], "floor": floor, "row": row, "col": col, "status": robot["status"]})

            if len(robot["path"]) <= 1:
                self._advance_robot_phase(runtime, robot, task)

    def _advance_robot_phase(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any]) -> None:
        tick = runtime.state["tick"]
        if task["phase"] == "pickup_from_ctu":
            self._set_robot_leg(runtime, robot, task, "delivery_to_station", task["stationCellId"])
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "phase": task["phase"], "status": task["status"]})
            return

        if task["phase"] == "pickup_from_shuttle":
            self._set_robot_leg(runtime, robot, task, "handoff_to_conveyor", task["handoffCellId"])
            robot["status"] = "handoff"
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "phase": task["phase"], "status": task["status"]})
            return

        if task["phase"] == "handoff_to_conveyor":
            task["status"] = "waiting_for_conveyor"
            task["phase"] = "waiting_for_conveyor"
            task["phaseLabel"] = self._phase_label(task["phase"])
            robot["status"] = "idle"
            robot["phaseLabel"] = "待命"
            robot["currentTaskId"] = None
            robot["path"] = [robot["currentCellId"]]
            if task["id"] not in runtime.state["queues"]["conveyor"]:
                runtime.state["queues"]["conveyor"].append(task["id"])
            self._sync_resource_queues(runtime)
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "phase": task["phase"], "status": task["status"]})
            return

        if task["phase"] == "delivery_to_station":
            task["status"] = "station_queue"
            task["phase"] = "station_queue"
            task["phaseLabel"] = self._phase_label(task["phase"])
            task["remainingTicks"] = 1
            task["stationResourceId"] = self._resolve_station_resource_id(runtime, task)
            if task["id"] not in runtime.state["queues"]["station"]:
                runtime.state["queues"]["station"].append(task["id"])
            robot["status"] = "idle"
            robot["phaseLabel"] = "待命"
            robot["currentTaskId"] = None
            robot["path"] = [robot["currentCellId"]]
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._sync_resource_queues(runtime)
            self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "phase": task["phase"], "status": task["status"]})

    def _set_robot_leg(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any], phase: str, target_cell_id: str | None) -> None:
        if not target_cell_id:
            return
        path_cells = shortest_path(runtime.topology, robot["currentCellId"], target_cell_id, runtime.scenario["pathPolicy"])
        robot["path"] = [cell["id"] for cell in path_cells] if path_cells else [robot["currentCellId"]]
        robot["targetCellId"] = target_cell_id
        robot["status"] = "moving"
        robot["phaseLabel"] = self._phase_label(phase)
        task["phase"] = phase
        task["phaseLabel"] = self._phase_label(phase)
        task["etaTicks"] = max(len(robot["path"]) - 1, 1)

    def _process_conveyor(self, runtime: RunRuntime) -> None:
        conveyor = self._resource(runtime, "conveyor_segment")
        if conveyor is None:
            return

        tick = runtime.state["tick"]
        queue = runtime.state["queues"]["conveyor"]
        jammed_until = int(conveyor.get("meta", {}).get("jammedUntil", 0))
        threshold = runtime.scenario["conveyorPolicy"]["releaseThreshold"]
        capacity = max(int(round(conveyor["capacity"] * threshold)) or 1, 1)

        while queue and len(conveyor["occupiedBy"]) < capacity and tick >= jammed_until:
            task_id = queue.pop(0)
            task = find_task(runtime.state["tasks"], task_id)
            if task is None:
                continue
            task["status"] = "conveyor_transfer"
            task["phase"] = "conveyor_transfer"
            task["phaseLabel"] = self._phase_label(task["phase"])
            task["remainingTicks"] = 3
            task["stationResourceId"] = self._resolve_station_resource_id(runtime, task)
            conveyor["occupiedBy"].append(task_id)
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._record_event(runtime, tick, "resource_occupied", {"resourceId": conveyor["id"], "taskId": task_id, "action": "enter"})

        for task_id in list(conveyor["occupiedBy"]):
            task = find_task(runtime.state["tasks"], task_id)
            if task is None:
                conveyor["occupiedBy"].remove(task_id)
                continue
            task["remainingTicks"] -= 1
            if task["remainingTicks"] <= 0:
                conveyor["occupiedBy"].remove(task_id)
                task["status"] = "station_queue"
                task["phase"] = "station_queue"
                task["phaseLabel"] = self._phase_label(task["phase"])
                task["remainingTicks"] = 1
                if task_id not in runtime.state["queues"]["station"]:
                    runtime.state["queues"]["station"].append(task_id)
                task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
                self._record_event(runtime, tick, "task_state_changed", {"taskId": task_id, "phase": task["phase"], "status": task["status"]})
                self._record_event(runtime, tick, "resource_occupied", {"resourceId": conveyor["id"], "taskId": task_id, "action": "leave"})

        self._sync_resource_queues(runtime)

    def _process_station(self, runtime: RunRuntime) -> None:
        station_resources = self._resources_by_type(runtime, "station")
        if not station_resources:
            return

        queue = runtime.state["queues"]["station"]
        tick = runtime.state["tick"]

        for station_resource in station_resources:
            while len(station_resource["occupiedBy"]) < station_resource["capacity"]:
                task_id = next((queued_task_id for queued_task_id in queue if self._task_station_resource_id(runtime, queued_task_id) == station_resource["id"]), None)
                if task_id is None:
                    break
                queue.remove(task_id)
                task = find_task(runtime.state["tasks"], task_id)
                if task is None:
                    continue
                station_resource["occupiedBy"].append(task_id)
                task["remainingTicks"] = max(task.get("remainingTicks", 1), 1)
                self._record_event(runtime, tick, "resource_occupied", {"resourceId": station_resource["id"], "taskId": task_id, "action": "enter"})

            for task_id in list(station_resource["occupiedBy"]):
                task = find_task(runtime.state["tasks"], task_id)
                if task is None:
                    station_resource["occupiedBy"].remove(task_id)
                    continue
                task["remainingTicks"] -= 1
                if task["remainingTicks"] <= 0:
                    station_resource["occupiedBy"].remove(task_id)
                    task["status"] = "completed"
                    task["phase"] = "completed"
                    task["phaseLabel"] = self._phase_label(task["phase"])
                    task["completedTick"] = tick
                    task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
                    runtime.metrics["completedTasks"] += 1
                    runtime.metrics["throughput"] += 1
                    if task.get("urgent"):
                        runtime.metrics["urgentOrdersHandled"] += 1
                    self._record_event(runtime, tick, "task_state_changed", {"taskId": task_id, "phase": task["phase"], "status": task["status"]})
                    self._record_event(runtime, tick, "resource_occupied", {"resourceId": station_resource["id"], "taskId": task_id, "action": "leave"})

        self._sync_resource_queues(runtime)

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
                suggestion="优先降低输送线放行阈值，必要时回退等待时间最长的设备。",
            ):
                runtime.metrics["deadlocksDetected"] += 1
                self._record_event(runtime, tick, "deadlock_detected", {"waitingChain": waiting_chain, "message": "检测到潜在死锁"})

        if len(runtime.state["queues"]["conveyor"]) >= 2 and self._emit_alert(
            runtime,
            tick,
            category="conveyor_congestion",
            level="warning",
            message="输送线等待队列持续增长。",
            details={"queueDepth": len(runtime.state["queues"]["conveyor"]), "tasks": runtime.state["queues"]["conveyor"][:]},
            suggestion="增加缓存位容量，或降低跨区任务的放行速度。",
        ):
            runtime.metrics["congestionEvents"] += 1

        if len(runtime.state["queues"]["station"]) >= 2 and self._emit_alert(
            runtime,
            tick,
            category="station_blockage",
            level="info",
            message="站台排队加剧，交付节拍出现拥堵。",
            details={"queueDepth": len(runtime.state["queues"]["station"]), "tasks": runtime.state["queues"]["station"][:]},
            suggestion="优先释放站台资源，或降低直送任务的放行速度。",
        ):
            runtime.metrics["congestionEvents"] += 1

    def _refresh_metrics(self, runtime: RunRuntime) -> None:
        pending = [task for task in runtime.state["tasks"] if task["status"] != "completed"]
        active = [robot for robot in runtime.state["robots"] if robot["status"] in {"moving", "handoff"}]
        blocked = [robot for robot in runtime.state["robots"] if robot["status"] == "blocked"]
        completed = [task for task in runtime.state["tasks"] if task["completedTick"] is not None and task["startedTick"] is not None]
        conveyor = self._resource(runtime, "conveyor_segment")
        buffer_resource = self._resource(runtime, "buffer_slot")
        conveyor_capacity = max(conveyor["capacity"], 1) if conveyor else 1
        completed_durations = [task["completedTick"] - task["startedTick"] for task in completed]

        runtime.metrics.update(
            {
                "pendingTasks": len(pending),
                "activeRobots": len(active),
                "blockedRobots": len(blocked),
                "conveyorUtilization": round((len(conveyor["occupiedBy"]) / conveyor_capacity) if conveyor else 0.0, 2),
                "avgEtaTicks": round(sum(task.get("etaTicks", 0) for task in pending) / len(pending), 2) if pending else 0.0,
                "avgCompletionTicks": round(sum(completed_durations) / len(completed_durations), 2) if completed_durations else 0.0,
                "stationQueueDepth": len(runtime.state["queues"]["station"]),
                "conveyorQueueDepth": len(runtime.state["queues"]["conveyor"]),
                "bufferOccupancy": len(buffer_resource["occupiedBy"]) if buffer_resource else 0,
            }
        )
        self._sync_resource_queues(runtime)

    def _resource(self, runtime: RunRuntime, resource_type: str) -> dict[str, Any] | None:
        return next((resource for resource in runtime.state["resources"] if resource["type"] == resource_type), None)

    def _resources_by_type(self, runtime: RunRuntime, resource_type: str) -> list[dict[str, Any]]:
        return [resource for resource in runtime.state["resources"] if resource["type"] == resource_type]

    def _sync_resource_queues(self, runtime: RunRuntime) -> None:
        conveyor = self._resource(runtime, "conveyor_segment")
        if conveyor:
            conveyor["queue"] = runtime.state["queues"]["conveyor"][:]

        waiting_for_conveyor = [task["id"] for task in runtime.state["tasks"] if task["phase"] == "waiting_for_conveyor" and task["status"] == "waiting_for_conveyor"]
        buffer_resource = self._resource(runtime, "buffer_slot")
        if buffer_resource:
            buffer_resource["occupiedBy"] = waiting_for_conveyor[: buffer_resource["capacity"]]
            buffer_resource["queue"] = waiting_for_conveyor[:]
            runtime.state["queues"]["buffer"] = waiting_for_conveyor[:]

        for station_resource in self._resources_by_type(runtime, "station"):
            station_resource["queue"] = [task_id for task_id in runtime.state["queues"]["station"] if self._task_station_resource_id(runtime, task_id) == station_resource["id"]]

    def _resolve_station_resource_id(self, runtime: RunRuntime, task: dict[str, Any]) -> str | None:
        target_station = task.get("stationNode") or task.get("targetNode")
        for resource in self._resources_by_type(runtime, "station"):
            if target_station in resource.get("nodeIds", []):
                return resource["id"]
        stations = self._resources_by_type(runtime, "station")
        return stations[0]["id"] if stations else None

    def _task_station_resource_id(self, runtime: RunRuntime, task_id: str) -> str | None:
        task = find_task(runtime.state["tasks"], task_id)
        if task is None:
            return None
        if task.get("stationResourceId"):
            return task["stationResourceId"]
        return self._resolve_station_resource_id(runtime, task)

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
            "delivery_to_station": "站台交付",
            "pickup_from_shuttle": "四向车取货",
            "handoff_to_conveyor": "交接到输送线",
            "waiting_for_conveyor": "等待输送线",
            "conveyor_transfer": "输送中",
            "station_queue": "站台排队",
            "completed": "已完成",
        }
        return mapping.get(phase, phase)

    def _first_node(self, topology: dict[str, Any], node_types: set[str], *, fallback: str = "") -> str:
        return next((node["id"] for node in topology["nodes"] if node["type"] in node_types), fallback)

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
        await self.hub.broadcast(runtime.run_id, {"type": "resource_occupied", "tick": tick, "payload": {"resources": runtime.state["resources"]}})
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
                        "status": robot["status"],
                        "phaseLabel": robot["phaseLabel"],
                    },
                },
            )

    def _persist(self, runtime: RunRuntime) -> None:
        self.repo.update_run(runtime.run_id, runtime.state["status"], runtime.state, runtime.metrics)
