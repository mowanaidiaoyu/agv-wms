from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from ..analytics.throughput_metrics import CONTRACT_VERSION as THROUGHPUT_CONTRACT_VERSION
from ..analytics.throughput_metrics import DELIVERY_EVENT_TYPE, DELIVERY_EVENT_VERSION
from .throughput_aware_assignment import ALGORITHM_VERSION as THROUGHPUT_AWARE_VERSION
from .throughput_aware_assignment import frozen_weights as freeze_throughput_aware_weights
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
from .motion_speed import (
    EPSILON_METERS,
    edge_distance_meters,
    freeze_motion_speed_contract,
    sim_tick_seconds,
    speed_for_edge,
)
from .pallet_conveyor import PALLET_ARM_ID, configure_pallet_transport
from .pathfinding import (
    build_occupied_lookup,
    build_node_lookup,
    build_walkable_lookup,
    cell_id,
    drop_zone_entry_cell,
    expand_shuttle_blocked_states,
    node_position,
    node_to_cell_id,
    parse_cell_id,
    pick_station_entry_cell,
    port_entry_cell,
    serviceable_storage_cells,
    shortest_path,
    shortest_path_shuttle,
    shuttle_collision_footprint,
    shuttle_state_is_walkable,
    shuttle_rack_slots,
    shuttle_servable_slots,
)
from .simulation_config import build_simulation_config
from .shuttle_topology import topology_revision
from .text_encoding import restore_utf8_cjk_label
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


def normalize_occupied_entries(entries: set[Any]) -> set[tuple[str, float]]:
    """Accept current layered occupancy and legacy cell-id-only test inputs."""
    normalized: set[tuple[str, float]] = set()
    for entry in entries:
        if isinstance(entry, tuple) and len(entry) == 2:
            normalized.add((str(entry[0]), float(entry[1])))
        else:
            normalized.add((str(entry), 1.0))
    return normalized


ROBOT_CARRY_CAPACITY = {"ctu": 5, "shuttle": 1}
CTU_RACK_LAYERS = 17
# 每 tick 的推进间隔（秒）。调大=仿真更慢、机器人每步更易看清。
# 每个 tick 仍只移动一个格子，缩短调度间隔即可在不改变路径、避障和取货逻辑的前提下提速 2.5 倍。
TICK_INTERVAL_SECONDS = 0.6
DEFAULT_SPEED_MULTIPLIER = 2
SUPPORTED_SPEED_MULTIPLIERS = {1, 2, 4}
TERMINAL_TASK_STATUSES = {"completed", "cancelled", "failed", "exception"}
# 空托盘回收：四向车库一个货位 = 一个托盘；货位最后一箱被取走后，托盘变空，
# 由四向车取走并送到 1F 地面输送线 (76,35)，在那里堆积计数（最多 6 层）。
EMPTY_PALLET_RETURN_CELL_ID = "cell-r76-c35"
EMPTY_PALLET_STACK_CAPACITY = 6
PALLET_ARM_PICK_TICKS = 5
PALLET_ARM_PLACE_TICKS = 7
EMPTY_PALLET_TASK_KIND = "empty_pallet_return"
# (76,35) 空托盘到 6 层后，四向车把托盘搬到 (74,47) 新机械臂入主线点。
EMPTY_PALLET_COLLECT_CELL_ID = "cell-r74-c47"
# Ground spur east of the c43/c44 turnout. Use body cells, including the
# overhang of a Shuttle at the buffer stop in c48.
PALLET_COLLECT_SPUR = frozenset(
    (row, col, 1.0) for row in (74, 75) for col in range(46, 50)
)
PALLET_COLLECT_MOUTH = frozenset(
    (row, col, 1.0) for row in (74, 75) for col in range(43, 46)
)
PALLET_COLLECT_EXIT_CELL_ID = "cell-r78-c43"
PALLET_COLLECT_EXIT_AREA = frozenset(
    (row, col, 1.0) for row in range(76, 80) for col in (43, 44)
)
PALLET_COLLECT_CLEAR_CELL_ID = "cell-r80-c43"
PALLET_COLLECT_RETREAT_CELL_IDS = ("cell-r74-c41", "cell-r75-c41", "cell-r74-c40")
EMPTY_PALLET_COLLECT_TASK_KIND = "empty_pallet_reposition"
PALLETS_ON_CONVEYOR = {"moving", "waiting_arm", "being_picked"}
# 机械臂-02（78,47）把托盘放到 row 84 col 58 上层输送主干入口。
EMPTY_PALLET_DROPOFF_CELL_ID = "cell-r84-c58"
# 5 个出货口（上层输送帽口）行号：每个出货口的放置区默认承载 1 个空托盘。
PALLET_DOCK_PORT_ROWS = (102, 106, 110, 114, 118)
DEFAULT_PALLET_DOCK_COUNT = 1
# 托盘上的余货：四向车取箱时整托盘一起抬离货位，机械臂只取走订单要的箱，
# 剩余箱随托盘回到原货位（托盘不能留在库里，也不能跟着回待命位）。
PALLET_RETURN_TASK_KIND = "pallet_return_to_lane"
# CTU 出库口排队成本：每个在途任务在出货口约占用 24 tick（与拣选三阶段节拍同量级）。
PORT_QUEUE_SERVICE_TICKS = 24
# 机械臂交接：四向车在交接处等待后，机械臂抓取（pick）与放箱（place）的阶段时长。
ARM_HANDOFF_PICK_TICKS = 3
ARM_HANDOFF_PLACE_TICKS = 4
# The handoff approach is a single-lane critical section.  Keep ownership
# until the serving Shuttle has moved outside this Manhattan radius so that a
# returning pallet cannot meet the next inbound vehicle head-on.
ARM_HANDOFF_CLEARANCE_CELLS = 8
# Fixed ground-conveyor FIFO in front of picking-handoff-01. Shuttle bodies
# occupy 2x2 grid squares, so each logical queue module advances by two cols.
# c43/c45 are deliberately excluded: they form the pallet-return clearing
# zone between the queue head and the physical service module at c47/c48.
HANDOFF_FIXED_CELL_ID = "cell-r92-c48"
HANDOFF_QUEUE_SLOT_CELL_IDS = tuple(
    f"cell-r92-c{col}" for col in (41, 39, 37, 35, 33, 31, 29, 27)
)
HANDOFF_RETURN_BUFFER_CELL_IDS = (
    "cell-r92-c43",
    "cell-r92-c45",
    "cell-r92-c47",
    "cell-r92-c48",
)
# 侧向出货口理货机械臂：抓取时长与交接臂一致；放箱阶段覆盖整段搬运动画
# （前端整周期约 6.6 秒，1× 下 3+8 tick 与之对齐），把货箱放到放置区。
ARM_DROPZONE_PICK_TICKS = 3
ARM_DROPZONE_PLACE_TICKS = 8
# A full pallet stack needs a visible grip and release even at 4x playback.
ARM_DROPZONE_PALLET_PICK_TICKS = 14
ARM_DROPZONE_PALLET_PLACE_TICKS = 18
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
# Cooperative Shuttle traffic planning.  Reservations include each vehicle's
# complete 2x2 body and the swept area between consecutive logical states.
# Rebuilding a short horizon every tick keeps the controller responsive to
# task changes while preventing two individually valid paths from entering the
# same intersection at the same time.
SHUTTLE_RESERVATION_HORIZON_TICKS = 12
SHUTTLE_FINAL_APPROACH_STEPS = 12
SHUTTLE_OSCILLATION_HISTORY_LIMIT = 8
SHUTTLE_OSCILLATION_RECOVERY_COOLDOWN_TICKS = 8
# Let reroute/backoff finish one recovery window before raising an alert.
# The former 3-tick threshold fired before mitigation even started.
DEADLOCK_ALERT_AFTER_TICKS = REROUTE_AFTER_BLOCK_TICKS * 2
MIN_SAFETY_TICKS = 10000
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
    wall_clock_started_monotonic: float = field(default_factory=time.monotonic)


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
        motion_speed_contract: dict[str, Any] | None = None,
        algorithm_experiment_contract: dict[str, Any] | None = None,
        *,
        expected_replay_evidence: dict[str, str] | None = None,
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
        config["motionSpeedContract"] = freeze_motion_speed_contract(
            motion_speed_contract,
            config.get("motionPhysicsContract"),
        )
        prepared_transfers = deepcopy(transfers or [])
        if algorithm_experiment_contract is not None:
            expected_revision = topology_revision(topology)
            if algorithm_experiment_contract.get("topologyRevision") != expected_revision:
                raise ValueError(
                    "algorithmExperimentContract.topologyRevision does not match the selected topology"
                )
            if config.get("motionPhysicsContract") is None or config.get("motionSpeedContract") is None:
                raise ValueError(
                    "algorithmExperimentContract requires both motionPhysicsContract and motionSpeedContract"
                )
            experiment = deepcopy(algorithm_experiment_contract)
            config["seed"] = int(experiment["seed"])
            config["pathPolicy"] = str(experiment["pathPolicy"])
            config["dispatchPolicy"] = str(experiment["assignmentPolicy"])
            if config["dispatchPolicy"] == "throughput_aware":
                config["throughputAwareWeights"] = freeze_throughput_aware_weights(
                    experiment.get("throughputAwareWeights")
                )
                experiment["throughputAwareWeights"] = deepcopy(config["throughputAwareWeights"])
                experiment["assignmentAlgorithmVersion"] = THROUGHPUT_AWARE_VERSION
            config["safetyLimitTicks"] = int(experiment["safetyLimitTicks"])
            for transfer in prepared_transfers:
                transfer["algorithm"] = config["pathPolicy"]
            fixed_algorithm = {
                "assignmentPolicy": config["dispatchPolicy"]
            } if experiment["variableDimension"] == "pathPolicy" else {
                "pathPolicy": config["pathPolicy"]
            }
            if (
                experiment["variableDimension"] == "pathPolicy"
                and config["dispatchPolicy"] == "throughput_aware"
            ):
                fixed_algorithm["assignmentAlgorithmVersion"] = THROUGHPUT_AWARE_VERSION
                fixed_algorithm["throughputAwareWeights"] = config["throughputAwareWeights"]
            scenario_payload = {
                "experimentContractVersion": experiment["experimentContractVersion"],
                "scenarioId": experiment["scenarioId"],
                "scenarioVersion": experiment["scenarioVersion"],
                "seed": experiment["seed"],
                "topologyId": topology["id"],
                "topologyRevision": expected_revision,
                "orders": orders or [],
                "transfers": [
                    {key: value for key, value in transfer.items() if key != "algorithm"}
                    for transfer in prepared_transfers
                ],
                "motionPhysicsContract": config["motionPhysicsContract"],
                "motionSpeedContract": config["motionSpeedContract"],
                "safetyLimitTicks": experiment["safetyLimitTicks"],
                "observationTicks": experiment.get("observationTicks"),
                "orderArrivalPlanHash": experiment.get("orderArrivalPlanHash"),
                "variableDimension": experiment["variableDimension"],
                **fixed_algorithm,
            }
            encoded = json.dumps(
                scenario_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            experiment["scenarioSignature"] = hashlib.sha256(encoded).hexdigest()
            experiment["topologyId"] = topology["id"]
            experiment["dataSource"] = "REAL_SIMULATION_EXPERIMENT"
            config["algorithmExperimentContract"] = experiment
            config["experimentReplayContract"] = {
                "contractVersion": "algorithm-experiment-replay-v1",
                "topologyId": topology["id"],
                "topologyRevision": expected_revision,
                "orders": deepcopy(orders or []),
                "transfers": deepcopy(transfers or []),
                "motionPhysicsContract": deepcopy(motion_physics_contract),
                "motionSpeedContract": deepcopy(motion_speed_contract),
                "algorithmExperimentContract": deepcopy(algorithm_experiment_contract),
                "workloadMutationCount": 0,
                "workloadMutationReasons": [],
            }
        if not config["robots"]:
            raise ValueError("当前拓扑没有 CTU 或四向车设备，无法启动仿真")
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        runtime = self._create_runtime(run_id, topology, config, orders, prepared_transfers)
        if expected_replay_evidence is not None:
            actual_contract = runtime.state.get("algorithmExperimentContract") or {}
            mismatches = [
                key for key, expected in expected_replay_evidence.items()
                if actual_contract.get(key) != expected
            ]
            if mismatches:
                raise ValueError(
                    "Candidate initial scene could not be restored: " + ", ".join(sorted(mismatches))
                )
        self.runtimes[run_id] = runtime
        self.repo.create_run(run_id, "running", topology["id"], runtime.state, runtime.metrics)
        runtime.task_handle = asyncio.create_task(self._loop(runtime))
        return {"runId": run_id, "status": "running"}

    async def create_assignment_candidate(
        self,
        baseline_run_id: str,
        assignment_policy: str,
        topology: dict[str, Any],
    ) -> dict[str, Any]:
        """Replay one authoritative frozen scene while changing assignmentPolicy only."""

        if assignment_policy not in {"greedy", "hungarian", "throughput_aware"}:
            raise ValueError("Unsupported assignmentPolicy")
        baseline = self.repo.get_run_read_only(baseline_run_id)
        if baseline is None:
            raise KeyError(baseline_run_id)
        state = baseline.get("payload") or {}
        if baseline.get("status") not in {"completed", "stopped"}:
            raise ValueError("Baseline experiment must be terminal before it can be copied")
        experiment = state.get("algorithmExperimentContract")
        replay = state.get("experimentReplayContract")
        if not isinstance(experiment, dict) or not isinstance(replay, dict):
            raise ValueError("Baseline Run lacks authoritative frozen replay evidence")
        if replay.get("contractVersion") != "algorithm-experiment-replay-v1":
            raise ValueError("Baseline Run replay contract is unsupported")
        if int(replay.get("workloadMutationCount") or 0) > 0:
            raise ValueError("Baseline Run contains unregistered order insertion or cancellation")
        if experiment.get("variableDimension") != "assignmentPolicy":
            raise ValueError("Baseline experiment does not isolate assignmentPolicy")
        if experiment.get("orderArrivalPlanHash"):
            raise ValueError("Baseline registered arrival plan cannot yet be replayed from authoritative inputs")
        if experiment.get("assignmentPolicy") == assignment_policy:
            raise ValueError("Candidate assignmentPolicy must differ from baseline")
        if replay.get("topologyId") != topology.get("id"):
            raise ValueError("Baseline topology identity no longer matches")
        if replay.get("topologyRevision") != topology_revision(topology):
            raise ValueError("Baseline topology revision can no longer be restored")

        source_contract = deepcopy(replay.get("algorithmExperimentContract"))
        if not isinstance(source_contract, dict):
            raise ValueError("Baseline Run lacks the original experiment request")
        source_contract["assignmentPolicy"] = assignment_policy
        source_contract["algorithmVersion"] = (
            THROUGHPUT_AWARE_VERSION
            if assignment_policy == "throughput_aware"
            else "agv-routing-assignment-v1"
        )
        if assignment_policy != "throughput_aware":
            source_contract.pop("throughputAwareWeights", None)
        expected = {
            key: str(experiment.get(key) or "")
            for key in (
                "scenarioSignature",
                "initialInventoryHash",
                "initialVehicleSnapshotHash",
                "orderSnapshotHash",
            )
        }
        if any(not value for value in expected.values()):
            raise ValueError("Baseline Run initial scene evidence is incomplete")
        return await self.start(
            topology,
            orders=deepcopy(replay.get("orders") or []),
            transfers=deepcopy(replay.get("transfers") or []),
            motion_physics_contract=deepcopy(replay.get("motionPhysicsContract")),
            motion_speed_contract=deepcopy(replay.get("motionSpeedContract")),
            algorithm_experiment_contract=source_contract,
            expected_replay_evidence=expected,
        )

    @staticmethod
    def _mark_experiment_workload_mutation(runtime: RunRuntime, reason: str) -> None:
        replay = runtime.state.get("experimentReplayContract")
        experiment = runtime.state.get("algorithmExperimentContract")
        if not isinstance(replay, dict) or not isinstance(experiment, dict):
            return
        replay["workloadMutationCount"] = int(replay.get("workloadMutationCount") or 0) + 1
        replay.setdefault("workloadMutationReasons", []).append(reason)
        experiment["workloadMutationCount"] = replay["workloadMutationCount"]
        experiment["workloadIntegrity"] = "MUTATED_AFTER_START"
        if isinstance(runtime.config.get("algorithmExperimentContract"), dict):
            runtime.config["algorithmExperimentContract"].update({
                "workloadMutationCount": replay["workloadMutationCount"],
                "workloadIntegrity": "MUTATED_AFTER_START",
            })

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
        runtime.state["playbackMultiplier"] = multiplier
        runtime.state["tickIntervalSeconds"] = self._tick_delay(runtime)
        runtime.state["playbackTickIntervalSeconds"] = self._tick_delay(runtime)
        runtime.state["updatedAt"] = now_dt().isoformat()
        self._record_event(runtime, runtime.state["tick"], "simulation_speed_changed", {"multiplier": multiplier})
        self._persist(runtime)
        return {"runId": run_id, "status": runtime.state["status"], "speedMultiplier": multiplier}

    async def add_order(
        self,
        run_id: str,
        order: dict[str, Any],
        *,
        registered_experiment_arrival: bool = False,
    ) -> dict[str, Any]:
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
        if not registered_experiment_arrival:
            self._mark_experiment_workload_mutation(runtime, f"ORDER_INSERTED:{order_id}")
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
        self._mark_experiment_workload_mutation(runtime, f"ORDER_CANCELLED:{order_id}")
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
        previous_throughput_generation = max(1, int(runtime.state.get("throughputGeneration") or 1))
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
        runtime.state["playbackMultiplier"] = DEFAULT_SPEED_MULTIPLIER
        runtime.state["tickIntervalSeconds"] = self._tick_delay(runtime)
        runtime.state["playbackTickIntervalSeconds"] = self._tick_delay(runtime)
        runtime.state["throughputGeneration"] = previous_throughput_generation + 1
        runtime.state["throughputDeliveryIds"] = []
        runtime.state["throughputDeliverySignatures"] = {}
        runtime.state["throughputCargoSequence"] = 0
        runtime.wall_clock_started_monotonic = time.monotonic()
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
        state = saved["payload"]
        # 兼容旧运行（空托盘承载位上线前创建）：补默认的 5 个放置区空托盘，
        # 否则前端放置区看不到任何托盘。
        if "palletDocks" not in state:
            state["palletDocks"] = {
                str(row): {"count": DEFAULT_PALLET_DOCK_COUNT, "availableEmpty": DEFAULT_PALLET_DOCK_COUNT}
                for row in PALLET_DOCK_PORT_ROWS
            }
        state.setdefault("palletConveyorItems", [])
        return state

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
        if from_position is not None and robot.get("type") == "shuttle":
            try:
                traffic_position = RuntimePosition.from_robot(robot)
            except (KeyError, TypeError, ValueError):
                traffic_position = None
            if traffic_position is not None and traffic_position != from_position:
                task = find_task(runtime.state.get("tasks", []), robot.get("currentTaskId"))
                history = list(robot.get("trafficMoveHistory") or [])
                history.append({
                    "tick": int(runtime.state.get("tick") or 0),
                    "cellId": robot.get("currentCellId"),
                    "layer": robot.get("currentLayer", 1),
                    "phase": (task or {}).get("phase"),
                    "movementType": (movement_type or MovementType.NORMAL).value,
                    "source": source,
                })
                robot["trafficMoveHistory"] = history[-SHUTTLE_OSCILLATION_HISTORY_LIMIT:]
        observer = self._ensure_motion_observer(runtime)
        if observer is None or from_position is None:
            return
        try:
            to_position = RuntimePosition.from_robot(robot)
            if from_position != to_position:
                robot["movementTicks"] = int(robot.get("movementTicks") or 0) + 1
                counters = runtime.state.setdefault("performanceCounters", {})
                counters["movementRobotTicks"] = int(counters.get("movementRobotTicks") or 0) + 1
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
                planned_path_id=robot.get("activePlanId"),
                replan_id=robot.get("activeReplanId"),
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
        pallet_transport = configure_pallet_transport(topology, conveyor_graph, secondary_arms)
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
                    "motionCreditMeters": 0.0,
                    "motionEdgeKey": None,
                    "motionCreditTick": -1,
                    "movementTicks": 0,
                    "physicalTravelWaitTicks": 0,
                    "blockedWaitTicks": 0,
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
        for robot in robots:
            robot.setdefault("motionCreditMeters", 0.0)
            robot.setdefault("motionEdgeKey", None)
            robot.setdefault("motionCreditTick", -1)
            robot.setdefault("movementTicks", 0)
            robot.setdefault("physicalTravelWaitTicks", 0)
            robot.setdefault("blockedWaitTicks", 0)

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
            "playbackMultiplier": DEFAULT_SPEED_MULTIPLIER,
            "tickIntervalSeconds": TICK_INTERVAL_SECONDS / DEFAULT_SPEED_MULTIPLIER,
            "playbackTickIntervalSeconds": TICK_INTERVAL_SECONDS / DEFAULT_SPEED_MULTIPLIER,
            "simulatedSeconds": 0.0,
            "wallClockSeconds": 0.0,
            "eventCount": len(transfer_events),
            "topologyId": topology["id"],
            "motionPhysicsContract": deepcopy(config.get("motionPhysicsContract")),
            "motionSpeedContract": deepcopy(config.get("motionSpeedContract")),
            "algorithmExperimentContract": deepcopy(config.get("algorithmExperimentContract")),
            "experimentReplayContract": deepcopy(config.get("experimentReplayContract")),
            "planningEvidence": [],
            "planningEvidenceContractVersion": "planning-evidence-v1",
            "planningSequence": 0,
            "throughputAwareAssignmentEvidence": [],
            "performanceCounters": {
                "movementRobotTicks": 0,
                "physicalTravelWaitRobotTicks": 0,
                "blockedRobotTicks": 0,
            },
            "throughputContractVersion": THROUGHPUT_CONTRACT_VERSION,
            "throughputGeneration": 1,
            "throughputDeliveryIds": [],
            "throughputDeliverySignatures": {},
            "throughputCargoSequence": 0,
            "orderDriven": bool(orders or transfers),
            "robots": robots,
            "tasks": tasks,
            "orders": order_states,
            "inventory": inventory,
            "shuttleDocks": shuttle_docks,
            # 空托盘在 (76,35) 地面输送线上的堆积层数（最多 6 层）。
            "emptyPalletStack": {
                "cellId": EMPTY_PALLET_RETURN_CELL_ID,
                "count": 0,
                "capacity": EMPTY_PALLET_STACK_CAPACITY,
                "overflow": 0,
            },
            # 5 个出货口帽口的托盘承载位（默认各 1 个空托盘）。
            # count = 托盘总数（视觉用，只增不减，表示这个帽口有几个托盘格位）
            # availableEmpty = 当前可承载新货物的空托盘数（侧臂放货 -1、输送线补充 +1）
            "palletDocks": {
                str(row): {"count": DEFAULT_PALLET_DOCK_COUNT, "availableEmpty": DEFAULT_PALLET_DOCK_COUNT}
                for row in PALLET_DOCK_PORT_ROWS
            },
            "palletArm": {
                "armId": PALLET_ARM_ID,
                "status": "idle",
                "stage": None,
                "remainingTicks": 0,
                "taskId": None,
                "cargo": None,
                "pendingQueue": [],
            },
            "palletConveyorItems": [],
            "palletTransport": pallet_transport,
            "palletDropZone": None,
            # 只持久化游标：箱/货道认领是运行时临时状态（键为元组，无法 JSON 序列化），
            # 后续插入订单时会依据在途任务重新推导。
            "shuttleBatch": {"cursor": int(shuttle_batch.get("cursor") or 0)},
            "transferInventory": transfer_inventory,
            "transferEvents": transfer_events,
            "alerts": [],
            "queues": {"conveyor": [], "station": [], "buffer": []},
            "conveyorItems": [],
            "pickingArm": {"status": "idle", "stage": None, "remainingTicks": 0, "taskId": None, "robotId": None} if arm_handoff else None,
            "handoffTraffic": {
                "ownerRobotId": None,
                "ownerTaskId": None,
                "entered": False,
                "acquiredTick": None,
                "clearanceCells": ARM_HANDOFF_CLEARANCE_CELLS,
                "queue": [],
            } if arm_handoff else None,
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
        inventory_hash = hashlib.sha256(
            json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if isinstance(state.get("algorithmExperimentContract"), dict):
            state["algorithmExperimentContract"]["initialInventoryHash"] = inventory_hash
            state["algorithmExperimentContract"]["orderSnapshotHash"] = hashlib.sha256(
                json.dumps(orders or [], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            vehicle_snapshot = [
                {
                    "id": robot.get("id"),
                    "type": robot.get("type"),
                    "cellId": robot.get("currentCellId"),
                    "layer": robot.get("currentLayer"),
                    "capacity": robot.get("capacity"),
                }
                for robot in sorted(robots, key=lambda item: str(item.get("id")))
            ]
            state["algorithmExperimentContract"]["initialVehicleSnapshotHash"] = hashlib.sha256(
                json.dumps(
                    vehicle_snapshot,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            state["algorithmExperimentContract"]["scenarioSignature"] = hashlib.sha256(
                json.dumps(
                    {
                        "baseScenarioSignature": state["algorithmExperimentContract"]["scenarioSignature"],
                        "initialInventoryHash": inventory_hash,
                        "initialVehicleSnapshotHash": state["algorithmExperimentContract"][
                            "initialVehicleSnapshotHash"
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            config["algorithmExperimentContract"] = deepcopy(
                state["algorithmExperimentContract"]
            )
            replay = state.get("experimentReplayContract")
            if isinstance(replay, dict):
                replay.update({
                    "scenarioSignature": state["algorithmExperimentContract"]["scenarioSignature"],
                    "initialInventoryHash": inventory_hash,
                    "initialVehicleSnapshotHash": state["algorithmExperimentContract"][
                        "initialVehicleSnapshotHash"
                    ],
                    "orderSnapshotHash": state["algorithmExperimentContract"]["orderSnapshotHash"],
                })
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
                        allocated_lines.append({
                            **dict(slot),
                            "qty": case_cnt,
                            "pickPieces": need_pieces,
                            "orderLineId": line_id,
                        })
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

    def _handoff_traffic_state(self, runtime: RunRuntime) -> dict[str, Any] | None:
        """Return the persisted single-capacity traffic controller state."""

        if runtime.arm_handoff is None:
            return None
        traffic = runtime.state.get("handoffTraffic")
        if not isinstance(traffic, dict):
            traffic = {
                "ownerRobotId": None,
                "ownerTaskId": None,
                "entered": False,
                "acquiredTick": None,
                "clearanceCells": ARM_HANDOFF_CLEARANCE_CELLS,
                "queue": [],
            }
            runtime.state["handoffTraffic"] = traffic
        traffic.setdefault("ownerRobotId", None)
        traffic.setdefault("ownerTaskId", None)
        traffic.setdefault("entered", False)
        traffic.setdefault("acquiredTick", None)
        traffic.setdefault("clearanceCells", ARM_HANDOFF_CLEARANCE_CELLS)
        traffic.setdefault("queue", [])
        return traffic

    def _handoff_distance(self, runtime: RunRuntime, robot: dict[str, Any]) -> int:
        target = runtime.arm_handoff or {}
        try:
            _floor, handoff_row, handoff_col = parse_cell_id(str(target["handoffCellId"]))
            _robot_floor, robot_row, robot_col = parse_cell_id(str(robot["currentCellId"]))
        except (KeyError, TypeError, ValueError):
            return 10**9
        return abs(robot_row - handoff_row) + abs(robot_col - handoff_col)

    @staticmethod
    def _uses_handoff_traffic(task: dict[str, Any] | None) -> bool:
        return bool(task and (task.get("armHandoff") or task.get("handoffTraffic")))

    def _handoff_route_states(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
    ) -> list[tuple[int, int, int]]:
        """Return the loaded route this task will use towards the arm."""

        if task.get("phase") == "pickup_from_shuttle" and not robot.get("carrying"):
            start_cell = str(task.get("sourceCellId") or robot["currentCellId"])
            start_layer = int(task.get("sourceLayer") or robot.get("currentLayer", 1))
        else:
            start_cell = str(robot["currentCellId"])
            start_layer = int(robot.get("currentLayer", 1))
        return shortest_path_shuttle(
            runtime.topology,
            start_cell,
            start_layer,
            str(task.get("deliveryCellId") or (runtime.arm_handoff or {}).get("handoffCellId") or ""),
            1,
            runtime.config.get("pathPolicy", "astar"),
        )

    def _handoff_staging_target(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
    ) -> tuple[str, int]:
        """Choose a fixed conveyor queue slot or a legacy branch holding state.

        The production handoff uses a visible FIFO on row 92. Other synthetic
        topologies retain the route-merge planner used by focused tests.
        """

        handoff_cell = str((runtime.arm_handoff or {}).get("handoffCellId") or "")
        if handoff_cell == HANDOFF_FIXED_CELL_ID:
            # Vehicles travel from different racks and therefore do not reach
            # the conveyor in task-creation order.  All of them first converge
            # on the west admission module; a final slot is allocated only when
            # the vehicle physically arrives there.  This preserves the real
            # arrival order and prevents a later vehicle from parking at c39 in
            # front of an earlier vehicle still travelling toward c41.
            return HANDOFF_QUEUE_SLOT_CELL_IDS[-1], 1

        own_path = self._handoff_route_states(runtime, robot, task)
        if len(own_path) <= 1:
            return str(robot["currentCellId"]), int(robot.get("currentLayer", 1))

        other_route_states: set[tuple[int, int, int]] = set()
        for other_task in runtime.state.get("tasks", []):
            if (
                other_task.get("id") == task.get("id")
                or other_task.get("status") in TERMINAL_TASK_STATUSES
                or not other_task.get("armHandoff")
                or not other_task.get("assignedRobotId")
            ):
                continue
            other_robot = next(
                (
                    item for item in runtime.state.get("robots", [])
                    if item.get("id") == other_task.get("assignedRobotId")
                ),
                None,
            )
            if other_robot is not None:
                other_route_states.update(self._handoff_route_states(runtime, other_robot, other_task))

        merge_index = next(
            (index for index, state in enumerate(own_path[1:], start=1) if state in other_route_states),
            len(own_path) - 1,
        )
        preferred_index = max(0, merge_index - 1)
        layers_by_coordinate: dict[tuple[int, int], set[int]] = {}
        for path_row, path_col, path_layer in own_path:
            layers_by_coordinate.setdefault((path_row, path_col), set()).add(path_layer)

        reserved_footprints: set[tuple[int, int, float]] = set()
        for other_task in runtime.state.get("tasks", []):
            if other_task.get("id") == task.get("id"):
                continue
            staging_cell = other_task.get("handoffStagingCellId")
            if not staging_cell or other_task.get("status") in TERMINAL_TASK_STATUSES:
                continue
            try:
                _floor, staging_row, staging_col = parse_cell_id(str(staging_cell))
            except ValueError:
                continue
            reserved_footprints.update(shuttle_collision_footprint(
                runtime.topology,
                staging_row,
                staging_col,
                float(other_task.get("handoffStagingLayer") or 1),
            ))

        occupied_footprints: set[tuple[int, int, float]] = set()
        for other_robot in runtime.state.get("robots", []):
            if other_robot.get("id") == robot.get("id"):
                continue
            occupied_footprints.update(self._robot_collision_footprint(runtime, other_robot))

        for index in range(preferred_index, -1, -1):
            row, col, layer = own_path[index]
            # Never park in a lift shaft.  A vehicle stopped on one lift level
            # can otherwise prevent the token owner from descending through
            # the same (row, col) coordinate on another level.
            if len(layers_by_coordinate.get((row, col), set())) > 1:
                continue
            footprint = shuttle_collision_footprint(runtime.topology, row, col, float(layer))
            if footprint.intersection(reserved_footprints):
                continue
            if index > 0 and footprint.intersection(occupied_footprints):
                continue
            return cell_id(row, col, "1F"), int(layer)

        row, col, layer = own_path[0]
        return cell_id(row, col, "1F"), int(layer)

    def _compact_handoff_queue(self, runtime: RunRuntime) -> bool:
        """Advance one FIFO waiter toward c41 while the arm corridor is busy."""

        if str((runtime.arm_handoff or {}).get("handoffCellId") or "") != HANDOFF_FIXED_CELL_ID:
            return False
        queue_tasks: list[tuple[int, str, dict[str, Any], dict[str, Any]]] = []
        for task in runtime.state.get("tasks", []):
            if task.get("status") in TERMINAL_TASK_STATUSES:
                continue
            staging_cell = str(task.get("handoffStagingCellId") or "")
            if (
                not task.get("handoffQueueAdmitted")
                or staging_cell not in HANDOFF_QUEUE_SLOT_CELL_IDS
            ):
                continue
            robot = next(
                (
                    item for item in runtime.state.get("robots", [])
                    if item.get("id") == task.get("assignedRobotId")
                ),
                None,
            )
            if robot is None:
                continue
            queue_tasks.append((
                int(task.get("handoffQueuedTick") or 0),
                str(task.get("id") or ""),
                task,
                robot,
            ))
        queue_tasks.sort(key=lambda item: (item[0], item[1]))

        for index, (_queued_tick, _task_id, task, robot) in enumerate(queue_tasks):
            if index >= len(HANDOFF_QUEUE_SLOT_CELL_IDS):
                break
            desired = HANDOFF_QUEUE_SLOT_CELL_IDS[index]
            if task.get("phase") != "waiting_handoff_slot" or robot.get("currentCellId") == desired:
                continue
            desired_footprint = self._robot_collision_footprint(runtime, robot, desired, 1)
            occupied = set()
            for other in runtime.state.get("robots", []):
                if other.get("id") != robot.get("id"):
                    occupied.update(self._robot_collision_footprint(runtime, other))
            if desired_footprint.intersection(occupied):
                continue
            if self._set_robot_leg(runtime, robot, task, "to_handoff_staging", desired, target_layer=1):
                task["handoffStagingCellId"] = desired
                task["handoffStagingLayer"] = 1
                task["handoffStagingApproachCells"] = list(robot.get("path") or [])
                task["handoffStagingApproachLayers"] = list(robot.get("pathLayers") or [])
                task["phaseLabel"] = "输送线队列向前补位"
                robot["phaseLabel"] = task["phaseLabel"]
                return True
        return False

    def _handoff_return_buffer_cleared(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
    ) -> bool:
        """Whether a returning Shuttle has branched clear of c43/c45."""

        if str((runtime.arm_handoff or {}).get("handoffCellId") or "") != HANDOFF_FIXED_CELL_ID:
            return False
        protected = self._handoff_protected_footprint(runtime)
        return not protected.intersection(self._robot_collision_footprint(runtime, robot))

    def _handoff_protected_footprint(
        self,
        runtime: RunRuntime,
    ) -> set[tuple[int, int, float]]:
        """Physical c43/c45 evacuation lane through its south branch."""

        protected: set[tuple[int, int, float]] = set()
        for protected_cell in HANDOFF_RETURN_BUFFER_CELL_IDS:
            _floor, row, col = parse_cell_id(protected_cell)
            protected.update(shuttle_collision_footprint(runtime.topology, row, col, 1))
        for row in range(92, 98):
            for col in range(43, 50):
                if shuttle_state_is_walkable(runtime.topology, row, col, 1):
                    protected.update(shuttle_collision_footprint(runtime.topology, row, col, 1))
        # Residual pallets use the c49 lift approach before branching deeper
        # into the warehouse. Keep inbound queue traffic out until the owner
        # has also cleared this short vertical/lift segment.
        for row in range(98, 101):
            for col in range(47, 50):
                if shuttle_state_is_walkable(runtime.topology, row, col, 1):
                    protected.update(shuttle_collision_footprint(runtime.topology, row, col, 1))
        for layer in range(2, 5):
            for row in range(97, 101):
                for col in range(47, 52):
                    if shuttle_state_is_walkable(runtime.topology, row, col, layer):
                        protected.update(shuttle_collision_footprint(runtime.topology, row, col, layer))
        return protected

    def _handoff_final_corridor_available(
        self,
        runtime: RunRuntime,
        requester: dict[str, Any],
    ) -> bool:
        """No other Shuttle body occupies the c43..c48 final corridor."""

        if str((runtime.arm_handoff or {}).get("handoffCellId") or "") != HANDOFF_FIXED_CELL_ID:
            return True
        protected = self._handoff_protected_footprint(runtime)
        return not any(
            other.get("id") != requester.get("id")
            and bool(other.get("currentTaskId") or other.get("carrying") or other.get("returningToStandby"))
            and bool(protected.intersection(self._robot_collision_footprint(runtime, other)))
            for other in runtime.state.get("robots", [])
        )

    def _handoff_return_route_blocked_states(
        self,
        runtime: RunRuntime,
    ) -> set[tuple[int, int, int]] | None:
        """Keep pallet returns off the occupied row-92 FIFO footprint."""

        if str((runtime.arm_handoff or {}).get("handoffCellId") or "") != HANDOFF_FIXED_CELL_ID:
            return None
        # Blocking both body rows forces the vehicle to leave through c43/c45
        # and branch north/south before travelling toward storage or its rack.
        return {
            (row, col, 1)
            for row in (92, 93)
            for col in range(20, 43)
        }

    def _handoff_queue_approach_blocked_states(
        self,
        runtime: RunRuntime,
    ) -> set[tuple[int, int, int]] | None:
        """States inbound queue traffic must avoid while a return owns the branch.

        This is deliberately wider than the row-92 buffer.  A Shuttle that is
        merely stopped at the south lift mouth still blocks the returning 2x2
        vehicle several ticks later.  New inbound vehicles are therefore
        replanned through the west conveyor entrance while the branch is
        reserved instead of being parked on the return route.
        """

        if str((runtime.arm_handoff or {}).get("handoffCellId") or "") != HANDOFF_FIXED_CELL_ID:
            return None
        blocked = {
            (row, col, 1)
            for row in range(92, 101)
            for col in range(43, 52)
            if shuttle_state_is_walkable(runtime.topology, row, col, 1)
        }
        blocked.update({
            (row, col, layer)
            for layer in range(2, 5)
            for row in range(97, 101)
            for col in range(47, 52)
            if shuttle_state_is_walkable(runtime.topology, row, col, layer)
        })
        return blocked

    def _handoff_queue_feeder_blocked_states(
        self,
        runtime: RunRuntime,
    ) -> set[tuple[int, int, int]] | None:
        """West admission turn reserved for the northbound loaded convoy."""

        if str((runtime.arm_handoff or {}).get("handoffCellId") or "") != HANDOFF_FIXED_CELL_ID:
            return None
        return {
            (row, col, 1)
            for row in range(92, 101)
            for col in range(27, 34)
            if shuttle_state_is_walkable(runtime.topology, row, col, 1)
        }

    def _handoff_queue_feeder_footprint(
        self,
        runtime: RunRuntime,
    ) -> set[tuple[int, int, float]]:
        footprint: set[tuple[int, int, float]] = set()
        for row, col, layer in self._handoff_queue_feeder_blocked_states(runtime) or set():
            footprint.update(shuttle_collision_footprint(runtime.topology, row, col, float(layer)))
        return footprint

    def _is_loaded_handoff_queue_feeder(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
    ) -> bool:
        task = find_task(runtime.state.get("tasks", []), robot.get("currentTaskId"))
        return bool(
            task
            and task.get("phase") == "to_handoff_staging"
            and robot.get("carrying")
            and str((runtime.arm_handoff or {}).get("handoffCellId") or "") == HANDOFF_FIXED_CELL_ID
        )

    def _loaded_handoff_queue_feeders_active(self, runtime: RunRuntime) -> bool:
        """Return whether a loaded Shuttle is imminently using the L1 feeder.

        Merely having a loaded vehicle in ``to_handoff_staging`` is not enough:
        it may still be several floors and dozens of cells away.  The former
        global boolean froze every empty L1 Shuttle for the complete trip.  A
        feeder becomes active only when the rolling near-term path contains an
        L1 body footprint that intersects the protected feeder area.
        """

        feeder_footprint = self._handoff_queue_feeder_footprint(runtime)
        if not feeder_footprint:
            return False
        for robot in runtime.state.get("robots", []):
            if not self._is_loaded_handoff_queue_feeder(runtime, robot):
                continue
            path = list(robot.get("path") or [robot.get("currentCellId")])
            layers = list(
                robot.get("pathLayers")
                or [robot.get("currentLayer", 1)] * len(path)
            )
            if len(layers) < len(path):
                layers.extend(
                    [layers[-1] if layers else robot.get("currentLayer", 1)]
                    * (len(path) - len(layers))
                )
            for index, path_cell in enumerate(
                path[:SHUTTLE_FINAL_APPROACH_STEPS + 1]
            ):
                layer = float(layers[index])
                if layer != 1.0:
                    continue
                if self._robot_collision_footprint(
                    runtime,
                    robot,
                    str(path_cell),
                    layer,
                ).intersection(feeder_footprint):
                    return True
        return False

    def _acquire_handoff_traffic(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
    ) -> bool:
        traffic = self._handoff_traffic_state(runtime)
        if traffic is None:
            return True
        owner_id = traffic.get("ownerRobotId")
        if owner_id not in (None, robot.get("id")):
            return False
        if owner_id is None:
            task.pop("handoffTrafficCleared", None)
            traffic.update({
                "ownerRobotId": robot["id"],
                "ownerTaskId": task["id"],
                "entered": False,
                "acquiredTick": int(runtime.state.get("tick") or 0),
            })
            self._record_event(
                runtime,
                int(runtime.state.get("tick") or 0),
                "handoff_traffic_acquired",
                {"robotId": robot["id"], "taskId": task["id"]},
            )
        else:
            task.pop("handoffTrafficCleared", None)
            traffic["ownerTaskId"] = task["id"]
        return True

    def _release_handoff_traffic(self, runtime: RunRuntime, reason: str) -> None:
        traffic = self._handoff_traffic_state(runtime)
        if traffic is None or not traffic.get("ownerRobotId"):
            return
        payload = {
            "robotId": traffic.get("ownerRobotId"),
            "taskId": traffic.get("ownerTaskId"),
            "reason": reason,
        }
        owner_task = find_task(runtime.state.get("tasks", []), traffic.get("ownerTaskId"))
        if owner_task is not None:
            owner_task["handoffTrafficCleared"] = True
        traffic.update({
            "ownerRobotId": None,
            "ownerTaskId": None,
            "entered": False,
            "acquiredTick": None,
        })
        self._record_event(
            runtime,
            int(runtime.state.get("tick") or 0),
            "handoff_traffic_released",
            payload,
        )

    def _refresh_handoff_traffic(self, runtime: RunRuntime) -> dict[str, Any] | None:
        """Reconcile ownership and release it only after the serving vehicle exits."""

        traffic = self._handoff_traffic_state(runtime)
        if traffic is None:
            return None
        # Keep the physical conveyor FIFO dense.  Only one waiter is advanced
        # per refresh and collision checks require its destination footprint to
        # be fully clear, so the visible queue closes gaps front-to-back without
        # making two 2x2 vehicles overlap.
        self._compact_handoff_queue(runtime)
        pending = [
            task["id"]
            for task in runtime.state.get("tasks", [])
            if task.get("status") not in TERMINAL_TASK_STATUSES
            and task.get("armHandoff")
            and task.get("id") != traffic.get("ownerTaskId")
        ]
        traffic["queue"] = pending
        owner_id = traffic.get("ownerRobotId")
        if owner_id is None:
            # Restored/legacy snapshots may contain an assigned handoff task but
            # no controller state.  Give ownership to the vehicle closest to
            # the handoff; every subsequent assignment is gated below.
            candidates: list[tuple[int, int, int, str, dict[str, Any], dict[str, Any]]] = []
            for task in runtime.state.get("tasks", []):
                if (
                    task.get("status") in TERMINAL_TASK_STATUSES
                    or task.get("handoffTrafficCleared")
                    or not self._uses_handoff_traffic(task)
                ):
                    continue
                robot = next(
                    (
                        item for item in runtime.state.get("robots", [])
                        if item.get("id") == task.get("assignedRobotId")
                    ),
                    None,
                )
                if robot is not None and robot.get("status") not in {"failed", "exception"}:
                    phase = str(task.get("phase") or "")
                    if task.get("armHandoff") and phase not in {
                        "waiting_handoff_slot",
                        "delivery_to_port",
                        "waiting_arm_handoff",
                    }:
                        # A loaded Shuttle must physically reach the conveyor
                        # FIFO before it may reserve the serialized final leg.
                        continue
                    if task.get("armHandoff") and not robot.get("carrying"):
                        continue
                    if (
                        task.get("armHandoff")
                        and phase == "waiting_handoff_slot"
                        and str((runtime.arm_handoff or {}).get("handoffCellId") or "") == HANDOFF_FIXED_CELL_ID
                        and (
                            task.get("handoffStagingCellId") != HANDOFF_QUEUE_SLOT_CELL_IDS[0]
                            or robot.get("currentCellId") != HANDOFF_QUEUE_SLOT_CELL_IDS[0]
                        )
                    ):
                        # The production line is a real FIFO: a vehicle may
                        # enter the final arm segment only from the c41 head
                        # module, never by overtaking from a rear slot.
                        continue
                    if (
                        task.get("armHandoff")
                        and phase == "waiting_handoff_slot"
                        and not self._handoff_final_corridor_available(runtime, robot)
                    ):
                        continue
                    # Loaded Shuttles already waiting for the arm are served
                    # before vehicles that are still travelling to a rack.
                    phase_rank = 0 if phase == "waiting_handoff_slot" else 1
                    candidates.append((
                        phase_rank,
                        int(task.get("handoffQueuedTick") or runtime.state.get("tick") or 0),
                        self._handoff_distance(runtime, robot),
                        str(robot["id"]),
                        robot,
                        task,
                    ))
            if candidates:
                _phase_rank, _queued_tick, _distance, _robot_id, robot, task = min(
                    candidates,
                    key=lambda item: (item[0], item[1], item[2], item[3]),
                )
                if self._acquire_handoff_traffic(runtime, robot, task) and task.get("phase") == "waiting_handoff_slot":
                    if self._set_robot_leg(
                        runtime,
                        robot,
                        task,
                        "delivery_to_port",
                        task.get("deliveryCellId"),
                        target_layer=1,
                    ):
                        task.pop("handoffQueuedTick", None)
                        task.pop("handoffQueueAdmitted", None)
                        task.pop("handoffStagingCellId", None)
                        task.pop("handoffStagingLayer", None)
                        task.pop("handoffStagingApproachCells", None)
                        task.pop("handoffStagingApproachLayers", None)
                        self._record_event(
                            runtime,
                            int(runtime.state.get("tick") or 0),
                            "handoff_queue_released",
                            {"robotId": robot["id"], "taskId": task["id"]},
                        )
            return traffic

        robot = next(
            (item for item in runtime.state.get("robots", []) if item.get("id") == owner_id),
            None,
        )
        if robot is None or robot.get("status") in {"failed", "exception"}:
            self._release_handoff_traffic(runtime, "owner_unavailable")
            return traffic

        clearance = int(traffic.get("clearanceCells") or ARM_HANDOFF_CLEARANCE_CELLS)
        distance = self._handoff_distance(runtime, robot)
        owner_task = find_task(runtime.state.get("tasks", []), robot.get("currentTaskId"))
        if owner_task is None:
            owner_task = find_task(runtime.state.get("tasks", []), traffic.get("ownerTaskId"))
        owner_phase = str((owner_task or {}).get("phase") or "")
        # Ownership can be granted while a loaded Shuttle is still approaching
        # or retreating from its staging point. If it has since settled, start
        # the serialized final leg immediately instead of leaving the token
        # owner parked forever.
        if owner_task is not None and owner_phase in {
            "waiting_handoff_slot",
            "yielding_handoff_staging",
        }:
            if self._set_robot_leg(
                runtime,
                robot,
                owner_task,
                "delivery_to_port",
                owner_task.get("deliveryCellId"),
                target_layer=1,
            ):
                owner_task.pop("handoffQueuedTick", None)
                owner_task.pop("handoffQueueAdmitted", None)
                owner_task.pop("handoffStagingCellId", None)
                owner_task.pop("handoffStagingLayer", None)
                owner_task.pop("handoffStagingApproachCells", None)
                owner_task.pop("handoffStagingApproachLayers", None)
                owner_phase = "delivery_to_port"
        owner_is_returning_load = owner_phase in {
            "delivery_empty_pallet",
            "return_pallet_to_lane",
        }
        if owner_is_returning_load:
            # c43 is the branch point: empty pallets turn north and residual
            # pallets turn south. Release as soon as the 2x2 vehicle body has
            # cleared c43/c45, so the next c41 waiter can feed the arm while
            # this vehicle continues independently to pallet storage/the rack.
            if traffic.get("entered") and self._handoff_return_buffer_cleared(runtime, robot):
                self._release_handoff_traffic(runtime, "owner_cleared_return_buffer")
            elif (owner_task or {}).get("status") in TERMINAL_TASK_STATUSES:
                self._release_handoff_traffic(runtime, "owner_return_completed")
            return traffic
        if owner_phase in {"delivery_to_port", "waiting_arm_handoff"}:
            # Serialize the complete inbound final leg. A valid route can
            # briefly move away from the handoff after entering its geometric
            # radius; releasing on that detour admits a second loaded Shuttle.
            if distance <= clearance:
                traffic["entered"] = True
            return traffic
        # A Shuttle can start inside the geometric clearance radius (the
        # right-most standby position is exactly eight cells from the handoff)
        # and then travel away to collect its pallet.  That is not an exit from
        # the critical section.  Entry becomes real only on the delivery/exit
        # leg, never during pickup.
        approaching_or_exiting = bool(
            self._uses_handoff_traffic(owner_task)
            and owner_phase in {"delivery_empty_pallet", "return_pallet_to_lane"}
            or robot.get("returningToStandby")
        )
        if (
            robot.get("returningToStandby")
            and traffic.get("entered")
            and self._handoff_return_buffer_cleared(runtime, robot)
        ):
            self._release_handoff_traffic(runtime, "owner_cleared_return_buffer")
        elif approaching_or_exiting and distance <= clearance:
            traffic["entered"] = True
        elif traffic.get("entered"):
            self._release_handoff_traffic(runtime, "owner_cleared_zone")
        elif not robot.get("currentTaskId") and not robot.get("returningToStandby"):
            self._release_handoff_traffic(runtime, "owner_task_ended_before_entry")
        return traffic

    def _advance_picking_arm(self, runtime: RunRuntime) -> None:
        """机械臂交接：从交接处四向车抓取货箱，放到放货点输送带；完成后四向车才释放。

        单臂逐箱服务，两阶段计时：pick（抓取）→ place（放至 L2 放货点带面）。
        等待中的四向车停在交接处（占用该格），后续车辆在地面线上自然排队。
        """
        arm = runtime.state.get("pickingArm")
        target = runtime.arm_handoff
        if not arm or not target:
            return
        traffic = self._refresh_handoff_traffic(runtime)
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
            # 只交付订单要的那箱：托盘上的余货（pallet_goods）留在车上随托盘回库。
            task["deliveredGoods"] = [
                dict(slot)
                for slot in (robot.get("carrying") or [])
                if slot.get("kind") != "pallet_goods"
            ]
            task["armPlacedCellId"] = target["dropoffCellId"]
            transport_started = self._start_arm_conveyor_delivery(runtime, task, target)
            # 机械臂把货物取走后：空托盘 → 送 (76,35) 堆叠；托盘还有余货 →
            # 余货随托盘回到原货位；两者都不成立（不留托盘的问题）才回待命位。
            pallet_returning = False
            if task.get("palletExtraBoxes"):
                pallet_returning = self._send_pallet_back_to_lane(runtime, robot, task, target)
            if not pallet_returning and bool(task.get("palletBecomesEmpty")):
                pallet_returning = self._send_empty_pallet_to_stack(
                    runtime, robot, task, target
                )
            if not pallet_returning:
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
            if traffic is not None:
                owner_id = traffic.get("ownerRobotId")
                if owner_id not in (None, robot.get("id")):
                    # Physical occupancy is authoritative.  A restored or
                    # legacy-bugged snapshot can carry stale ownership while a
                    # different loaded Shuttle is already waiting on the
                    # single-capacity handoff cell.  Repair ownership so the
                    # arm can clear the real occupant instead of deadlocking
                    # the whole approach.
                    previous_owner = owner_id
                    traffic.update({
                        "ownerRobotId": robot["id"],
                        "ownerTaskId": task["id"],
                        "entered": True,
                        "acquiredTick": int(runtime.state.get("tick") or 0),
                    })
                    task.pop("handoffTrafficCleared", None)
                    self._record_event(
                        runtime,
                        int(runtime.state.get("tick") or 0),
                        "handoff_traffic_reconciled",
                        {
                            "previousOwnerRobotId": previous_owner,
                            "robotId": robot["id"],
                            "taskId": task["id"],
                            "reason": "physical_handoff_occupant",
                        },
                    )
                if owner_id is None and not self._acquire_handoff_traffic(runtime, robot, task):
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

        每台臂从侧向出货口逐次取放。arm-01 也从同一模块取走空托盘，
        抓牢后释放输送带位置，放稳后更新放置区计数。
        """
        arm_states = runtime.state.get("pickingArms") or []
        if not arm_states or not runtime.secondary_arms:
            return
        configs = {str(config["armId"]): config for config in runtime.secondary_arms}
        tick = int(runtime.state["tick"])
        boxes = runtime.state.setdefault("armBeltBoxes", [])
        for arm_state in arm_states:
            arm = arm_state
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
                    pallet_id = (arm.get("cargo") or {}).get("palletId")
                    place_ticks = ARM_DROPZONE_PALLET_PLACE_TICKS if pallet_id else ARM_DROPZONE_PLACE_TICKS
                    arm["stage"] = "place"
                    arm["remainingTicks"] = place_ticks
                    arm["stageStartedTick"] = tick
                    arm["stageDurationTicks"] = place_ticks
                    if pallet_id:
                        runtime.state["palletConveyorItems"] = [
                            item for item in runtime.state["palletConveyorItems"] if item["id"] != pallet_id
                        ]
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
                arm = dict(arm_state)
                arm_state.update({
                    "status": "idle",
                    "stage": None,
                    "remainingTicks": 0,
                    "stageStartedTick": None,
                    "stageDurationTicks": None,
                    "taskId": None,
                    "cargoToken": None,
                    "portCellId": None,
                    "dropCellId": None,
                    "cargo": None,
                })
                cargo = arm.get("cargo") or {}
                if cargo.get("palletId"):
                    self._record_pallet_placement(runtime, arm, cargo)
                    continue
                # place 完成 → 消耗帽口的空托盘承载位（availableEmpty -1；
                # count 保持不变，表示托盘本身还在那里，视觉上不会消失）。
                port_row_key = str(arm.get("portRowKey") or "")
                if port_row_key:
                    docks = runtime.state.setdefault("palletDocks", {})
                    dock = docks.setdefault(port_row_key, {"count": 0, "availableEmpty": 0})
                    dock["count"] = int(dock.get("count") or 0)
                    dock["availableEmpty"] = max(0, int(dock.get("availableEmpty") or 0) - 1)
                    self._record_event(
                        runtime, tick, "pallet_dock_consumed",
                        {"portRow": port_row_key, "dockCount": dock["count"],
                         "availableEmpty": dock["availableEmpty"],
                         "armId": arm.get("armId"), "taskId": arm.get("taskId")},
                    )
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
                delivery_task = find_task(runtime.state.get("tasks", []), arm.get("taskId"))
                if delivery_task is not None:
                    self._record_outbound_delivery(
                        runtime,
                        delivery_task,
                        tick,
                        str(arm.get("dropCellId") or ""),
                        cargo=cargo,
                    )
                continue
            # 空闲：寻找停在本臂出货口帽口、尚未被搬走的货箱。
            # 帽口必须有托盘（count > 0)才能取；一个托盘可以承载多箱，
            # 侧臂逐箱码放到同一托盘上。availableEmpty 只用于前端区分
            # “空托盘 / 已承载”，不能再当成一次性承载位——否则同一订单的
            # 其余货箱会永远停在输送带上送不下去。
            port_cells = {str(port["cellId"]): port for port in config["ports"]}
            docks = runtime.state.get("palletDocks") or {}
            claimed_index = None
            claimed_port = None
            for index, box in enumerate(boxes):
                port = port_cells.get(str(box.get("cellId") or ""))
                if port is None:
                    continue
                dock_row = str(int(port["row"]))
                dock = docks.get(dock_row) or {}
                if int(dock.get("count") or 0) <= 0:
                    # 这个帽口没有托盘，等输送线补
                    continue
                claimed_index = index
                claimed_port = port
                break
            transport = runtime.state.get("palletTransport") or {}
            pallet = None
            if arm["armId"] == transport.get("deliveryArmId"):
                pallet = next((
                    item for item in runtime.state["palletConveyorItems"]
                    if item["status"] == "waiting_arm" and item.get("portCellId") in port_cells
                ), None)
            if pallet and (
                claimed_index is None or pallet["arrivedTick"] <= int(boxes[claimed_index].get("placedTick") or 0)
            ):
                pallet["status"] = "being_picked"
                box = {
                    "kind": "empty_pallet", "qty": pallet["palletCount"], "palletId": pallet["id"],
                    "taskId": pallet["taskId"], "cellId": pallet["portCellId"],
                    "deck": pallet["deck"], "zoneId": transport["dropZoneId"],
                }
                port_row_key = None
                drop_row, drop_col = transport["dropRow"], transport["dropCol"]
            elif claimed_index is not None:
                box = boxes.pop(claimed_index)
                drop_row = int(claimed_port["row"])
                drop_col = int(config.get("dropCol") or 67)
                port_row_key = str(drop_row)
            else:
                continue
            pick_ticks = ARM_DROPZONE_PALLET_PICK_TICKS if box.get("palletId") else ARM_DROPZONE_PICK_TICKS
            arm.update({
                "status": "busy",
                "stage": "pick",
                "remainingTicks": pick_ticks,
                "stageStartedTick": tick,
                "stageDurationTicks": pick_ticks,
                "taskId": box.get("taskId"),
                "cargoToken": f"{arm.get('armId')}:{box.get('taskId') or 'box'}:{tick}",
                "portCellId": str(box.get("cellId")),
                "portRowKey": port_row_key,
                "dropCellId": cell_id(drop_row, drop_col),
                "dropRow": drop_row,
                "dropCol": drop_col,
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

    def _record_pallet_placement(self, runtime: RunRuntime, arm: dict, cargo: dict) -> None:
        zone = runtime.state.get("palletDropZone") or {
            "zoneId": cargo["zoneId"], "row": arm["dropRow"], "col": arm["dropCol"],
            "deck": cargo["deck"], "armId": arm["armId"], "count": 0,
        }
        zone["count"] += cargo["qty"]
        runtime.state["palletDropZone"] = zone
        self._record_event(runtime, int(runtime.state["tick"]), "pallet_drop_zone_arrived", {
            "palletId": cargo["palletId"], "palletCount": cargo["qty"], "taskId": cargo["taskId"],
            "armId": arm["armId"], "portCellId": arm["portCellId"], "cellId": arm["dropCellId"],
            "zoneId": zone["zoneId"], "totalPallets": zone["count"], "deck": zone["deck"],
        })

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
            runtime.state.setdefault("armBeltBoxes", []).append(
                self._outbound_cargo_snapshot(
                    runtime,
                    task,
                    box,
                    taskId=task.get("id"),
                    cellId=target.get("dropoffCellId"),
                    level=target.get("dropoffLevel", 2),
                    beltHeight=target.get("dropoffBeltHeight", 3.0),
                    placedTick=tick,
                )
            )
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

    def _record_outbound_delivery(
        self,
        runtime: RunRuntime,
        task: dict[str, Any],
        tick: int,
        endpoint_cell_id: str,
        *,
        cargo: dict[str, Any] | None = None,
    ) -> None:
        """Persist SKU-piece evidence only after the side arm places cargo in drop_zone."""

        if task.get("taskKind") not in {"shuttle_order_pick", "placed_goods_delivery"}:
            return
        order_id = task.get("orderId")
        if not order_id:
            return
        goods = (
            [cargo]
            if isinstance(cargo, dict) and cargo.get("skuId")
            else task.get("transitGoods") or task.get("deliveredGoods") or task.get("lines") or []
        )
        pieces_by_line_and_sku: dict[tuple[str, str], dict[str, Any]] = {}
        for good in goods:
            if not isinstance(good, dict):
                continue
            sku_id = str(good.get("skuId") or "")
            if not sku_id:
                continue
            order_line_id = str(good.get("orderLineId") or task.get("orderLineId") or "unknown-line")
            quantity = max(0, int(good.get("qty") or 0))
            if task.get("taskKind") == "shuttle_order_pick":
                quantity = max(0, int(task.get("demandPieces") or quantity))
            if quantity <= 0:
                continue
            item = pieces_by_line_and_sku.setdefault((order_line_id, sku_id), {
                "skuId": sku_id,
                "skuName": good.get("skuName"),
                "orderLineId": order_line_id,
                "quantityPieces": 0,
            })
            item["quantityPieces"] += quantity

        parent = find_task(runtime.state.get("tasks", []), task.get("parentId"))
        root = parent
        while root and root.get("parentId"):
            root = find_task(runtime.state.get("tasks", []), root.get("parentId"))
        authoritative_box_count = 1 if (
            task.get("taskKind") == "shuttle_order_pick"
            or (parent or {}).get("taskKind") == "picking_box"
        ) else None
        generation = max(1, int(runtime.state.get("throughputGeneration") or 1))
        recorded_ids = runtime.state.setdefault("throughputDeliveryIds", [])
        recorded_signatures = runtime.state.setdefault("throughputDeliverySignatures", {})
        physical_cargo_id = str((cargo or {}).get("physicalCargoId") or "")
        delivery_sequence = (cargo or {}).get("deliverySequence")
        legacy_delivery_key = f"task:{task.get('id')}"
        delivery_key = physical_cargo_id or legacy_delivery_key
        for item_index, item in enumerate(pieces_by_line_and_sku.values()):
            order_line_id = str(item.get("orderLineId") or "unknown-line")
            delivery_id = (
                f"{runtime.run_id}:g{generation}:{delivery_key}:{order_id}:"
                f"{order_line_id}:{item['skuId']}"
            )
            payload = {
                "contractVersion": DELIVERY_EVENT_VERSION,
                "deliveryId": delivery_id,
                "runId": runtime.run_id,
                "generation": generation,
                "orderId": order_id,
                "orderLineId": None if order_line_id == "unknown-line" else order_line_id,
                "taskId": task.get("id"),
                "parentTaskId": task.get("parentId"),
                "rootTaskId": (root or task).get("id"),
                "taskKind": task.get("taskKind"),
                "originRobotType": (root or task).get("robotType"),
                "skuId": item["skuId"],
                "skuName": item.get("skuName"),
                "quantityPieces": int(item["quantityPieces"]),
                # One physical cargo can theoretically contain more than one SKU.
                # Count that box once, while every SKU line keeps its piece evidence.
                "quantityBoxes": authoritative_box_count if item_index == 0 else 0,
                "physicalCargoId": physical_cargo_id or None,
                "deliverySequence": delivery_sequence,
                "tick": tick,
                "deliveryStage": "drop_zone_received",
                "endpointCellId": endpoint_cell_id,
                "outputPortBlockId": task.get("outputPortBlockId"),
                "flowKind": "outbound_order",
                "evidenceSource": "secondary_arm_dropzone_placed",
            }
            signature = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if delivery_id in recorded_ids:
                previous_signature = recorded_signatures.get(delivery_id)
                if previous_signature is None or previous_signature == signature:
                    continue
                # Persist contradictory evidence with the same deliveryId so
                # the read-only aggregator can mark the metric INVALID.
                self._record_event(runtime, tick, DELIVERY_EVENT_TYPE, payload)
                continue
            self._record_event(runtime, tick, DELIVERY_EVENT_TYPE, payload)
            recorded_ids.append(delivery_id)
            recorded_signatures[delivery_id] = signature

    def _outbound_cargo_snapshot(
        self,
        runtime: RunRuntime,
        task: dict[str, Any],
        box: dict[str, Any],
        **position: Any,
    ) -> dict[str, Any]:
        """Attach a stable, per-generation physical identity before a box reaches a side arm."""

        sequence = max(0, int(runtime.state.get("throughputCargoSequence") or 0)) + 1
        runtime.state["throughputCargoSequence"] = sequence
        generation = max(1, int(runtime.state.get("throughputGeneration") or 1))
        return {
            **box,
            **position,
            "physicalCargoId": f"{runtime.run_id}:g{generation}:cargo-{sequence}",
            "deliverySequence": sequence,
        }

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
        runtime.state.setdefault("armBeltBoxes", []).append(
            self._outbound_cargo_snapshot(
                runtime,
                task,
                box,
                taskId=task.get("id"),
                cellId=final_cell,
                level=2,
                beltHeight=3.0,
                placedTick=tick,
            )
        )
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
                    "label": restore_utf8_cjk_label(str(zone.get("label") or zone.get("id") or entry)),
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
            if line.get("orderLineId") not in (None, ""):
                entry["orderLineId"] = str(line["orderLineId"])
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
        experiment = runtime.config.get("algorithmExperimentContract")
        observation_ticks = (
            int(experiment["observationTicks"])
            if isinstance(experiment, dict) and experiment.get("observationTicks") is not None
            else None
        )
        if experiment is not None:
            safety_limit = int(runtime.config["safetyLimitTicks"])
        else:
            safety_limit = max(
                MIN_SAFETY_TICKS,
                nominal_duration * 20,
                int(runtime.config.get("maxDurationTicks") or 0),
            )
        try:
            while (
                runtime.state["tick"] < safety_limit
                and (observation_ticks is None or runtime.state["tick"] < observation_ticks)
            ):
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
        elif observation_ticks is not None and runtime.state["tick"] >= observation_ticks:
            runtime.state["status"] = "stopped"
            runtime.state["stopReason"] = "observation_window_complete"
            runtime.state["throughputObservation"] = {
                "contractVersion": THROUGHPUT_CONTRACT_VERSION,
                "observationTick": observation_ticks,
                "observationSeconds": round(
                    observation_ticks * self._sim_tick_seconds(runtime),
                    9,
                ),
                "completedNaturally": False,
            }
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
        pallet_arm = runtime.state.get("palletArm") or {}
        pallet_transfer_pending = (
            pallet_arm.get("status") == "busy"
            or bool(pallet_arm.get("pendingQueue"))
            or any(item.get("status") in PALLETS_ON_CONVEYOR for item in runtime.state.get("palletConveyorItems", []))
            or any((arm.get("cargo") or {}).get("palletId") for arm in runtime.state.get("pickingArms", []))
        )
        batch_pending = (
            runtime.state.get("palletTransport")
            and (runtime.state.get("emptyPalletStack") or {}).get("stacks")
            and any(
                robot["type"] == "shuttle" and robot.get("status") not in {"failed", "exception"}
                for robot in runtime.state["robots"]
            )
        )
        return tasks_terminal and orders_terminal and not (pallet_transfer_pending or batch_pending)

    def _tick_delay(self, runtime: RunRuntime) -> float:
        multiplier = runtime.speed_multiplier if runtime.speed_multiplier in SUPPORTED_SPEED_MULTIPLIERS else DEFAULT_SPEED_MULTIPLIER
        return TICK_INTERVAL_SECONDS / multiplier

    def _sim_tick_seconds(self, runtime: RunRuntime) -> float:
        return sim_tick_seconds(runtime.config, TICK_INTERVAL_SECONDS)

    def _physical_movement_ready(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        next_cell_id: str,
        next_layer: float,
    ) -> bool:
        """Accrue one logical tick of distance credit without claiming blockage.

        Occupancy is still checked by the existing scheduler after this returns
        true. Credit is consumed only after that check succeeds, so waiting at a
        reserved cell cannot manufacture distance or repeated mileage.
        """

        speed_contract = runtime.config.get("motionSpeedContract")
        physics_contract = runtime.config.get("motionPhysicsContract")
        if not isinstance(speed_contract, dict) or not isinstance(physics_contract, dict):
            return True
        _floor, next_row, next_col = parse_cell_id(next_cell_id)
        edge_key = f"{next_cell_id}@{float(next_layer):.9f}"
        previous_key = robot.get("motionEdgeKey")
        if previous_key is not None and previous_key != edge_key:
            robot["motionCreditMeters"] = 0.0
        robot["motionEdgeKey"] = edge_key
        required = edge_distance_meters(
            physics_contract,
            int(robot.get("currentRow") or 0),
            int(robot.get("currentCol") or 0),
            float(robot.get("currentLayer") or 1),
            int(next_row),
            int(next_col),
            float(next_layer),
        )
        robot["motionEdgeRequiredMeters"] = required
        if required <= EPSILON_METERS:
            return True
        tick = int(runtime.state.get("tick") or 0)
        current_credit = float(robot.get("motionCreditMeters") or 0.0)
        if current_credit + EPSILON_METERS < required and int(robot.get("motionCreditTick", -1)) != tick:
            speed = speed_for_edge(
                speed_contract,
                str(robot.get("type") or "ctu"),
                float(robot.get("currentLayer") or 1),
                float(next_layer),
            )
            robot["motionCreditMeters"] = current_credit + speed * self._sim_tick_seconds(runtime)
            robot["motionCreditTick"] = tick
        if float(robot.get("motionCreditMeters") or 0.0) + EPSILON_METERS < required:
            robot["status"] = "moving"
            robot["physicalTravelWaitTicks"] = int(robot.get("physicalTravelWaitTicks") or 0) + 1
            counters = runtime.state.setdefault("performanceCounters", {})
            counters["physicalTravelWaitRobotTicks"] = int(
                counters.get("physicalTravelWaitRobotTicks") or 0
            ) + 1
            return False
        return True

    def _consume_motion_credit(self, robot: dict[str, Any]) -> None:
        required = float(robot.get("motionEdgeRequiredMeters") or 0.0)
        credit = float(robot.get("motionCreditMeters") or 0.0)
        robot["motionCreditMeters"] = max(0.0, credit - required)
        robot["motionEdgeKey"] = None
        robot["motionEdgeRequiredMeters"] = 0.0

    def _step_runtime(self, runtime: RunRuntime, *, persist: bool) -> None:
        runtime.state["tick"] += 1
        runtime.state["simulatedSeconds"] = round(
            int(runtime.state["tick"]) * self._sim_tick_seconds(runtime),
            9,
        )
        runtime.state["wallClockSeconds"] = round(
            max(0.0, time.monotonic() - runtime.wall_clock_started_monotonic),
            6,
        )
        runtime.state["updatedAt"] = now_dt().isoformat()
        tick = runtime.state["tick"]
        self._begin_deadlock_observation(runtime, tick)
        self._begin_motion_observation(runtime, tick)
        self._inject_faults(runtime, tick)
        self._inject_urgent_order(runtime, tick)
        self._assign_tasks(runtime)
        if runtime.state.get("palletTransport"):
            stack = runtime.state["emptyPalletStack"]
            if stack.get("stacks") and (
                len(stack["stacks"]) >= EMPTY_PALLET_STACK_CAPACITY
                or all(
                    task.get("status") in TERMINAL_TASK_STATUSES
                    or task.get("taskKind") == EMPTY_PALLET_COLLECT_TASK_KIND
                    for task in runtime.state["tasks"]
                )
            ):
                self._spawn_pallet_reposition(runtime)
        self._move_robots(runtime)
        counters = runtime.state.setdefault("performanceCounters", {})
        blocked_count = sum(
            1 for robot in runtime.state.get("robots", []) if robot.get("status") == "blocked"
        )
        counters["blockedRobotTicks"] = int(counters.get("blockedRobotTicks") or 0) + blocked_count
        for robot in runtime.state.get("robots", []):
            if robot.get("status") == "blocked":
                robot["blockedWaitTicks"] = int(robot.get("blockedWaitTicks") or 0) + 1
        self._advance_picking_arm(runtime)
        self._advance_picking_arms(runtime)
        self._advance_pallet_arm(runtime)
        self._advance_pallet_conveyor(runtime)
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
        self._refresh_handoff_traffic(runtime)
        pending = [task for task in runtime.state["tasks"] if task["status"] == "pending"]
        if not pending:
            return
        pending.sort(key=lambda task: (task["priority"], task["id"]))
        # Assignment and handoff admission are separate resources.  Every idle
        # Shuttle may fetch a different pallet in parallel; only the loaded
        # approach to the single-capacity arm is serialized later.
        dispatchable = list(pending)
        gated_task_ids: set[str] = set()
        # 故障设备停在原地，占的格子对其它设备不可通行：寻路和代价计算都要排除。
        failed_cells = {
            parse_cell_id(robot["currentCellId"]) for robot in runtime.state["robots"] if robot["failed"]
        }
        transfer_task_ids: set[str] = set()
        for task in dispatchable:
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
            [task for task in dispatchable if task["id"] not in transfer_task_ids],
            runtime.topology,
            runtime.config.get("pathPolicy", "astar"),
            failed_cells,
            runtime_state=runtime.state,
            runtime_config=runtime.config,
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
                task_id=task.get("id"),
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
            if task["id"] in matched_task_ids or task["id"] in gated_task_ids or task.get("status") != "pending":
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

    def _robot_collision_footprint(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        cell_id_value: str | None = None,
        layer: int | float | None = None,
    ) -> frozenset[tuple[int, int, float]]:
        """Return the physical cells occupied by a robot at one position."""
        target_cell = cell_id_value or str(robot["currentCellId"])
        _floor, row, col = parse_cell_id(target_cell)
        target_layer = float(robot.get("currentLayer", 1) if layer is None else layer)
        if robot.get("type") == "shuttle":
            return shuttle_collision_footprint(runtime.topology, row, col, target_layer)
        return frozenset({(row, col, target_layer)})

    def _physical_move_blocker(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        target_cell: str,
        target_layer: int | float,
        reserved_footprint: set[tuple[int, int, float]],
    ) -> tuple[dict[str, Any] | None, bool]:
        """Return the blocking robot and whether a same-tick reservation blocks.

        Legacy topology data can already contain overlapping Shuttle start
        nodes.  A proposed move may leave that overlap only when it strictly
        reduces it; otherwise each Shuttle/CTU footprint is a moving obstacle.
        """
        candidate = self._robot_collision_footprint(runtime, robot, target_cell, target_layer)
        current = self._robot_collision_footprint(runtime, robot)
        for other in runtime.state.get("robots", []):
            if other.get("id") == robot.get("id"):
                continue
            other_footprint = self._robot_collision_footprint(runtime, other)
            candidate_overlap = candidate.intersection(other_footprint)
            if not candidate_overlap:
                continue
            current_overlap = current.intersection(other_footprint)
            if not current_overlap or len(candidate_overlap) >= len(current_overlap):
                return other, False
        entering_spur = bool(candidate & PALLET_COLLECT_SPUR and not current & PALLET_COLLECT_SPUR)
        entering_mouth = bool(candidate & PALLET_COLLECT_MOUTH and not current & PALLET_COLLECT_MOUTH)
        if robot.get("type") == "shuttle" and (entering_spur or entering_mouth) and self._has_pallet_collect_spur(runtime):
            task = find_task(runtime.state["tasks"], robot.get("currentTaskId"))
            delivering = bool(
                task and task.get("taskKind") == EMPTY_PALLET_COLLECT_TASK_KIND
                and task.get("phase") == "delivery_to_collect"
            )
            # Hold a following carrier before the turnout so the vehicle
            # with the spur can still turn out of it.
            if delivering and not current & PALLET_COLLECT_SPUR and self._pallet_collect_spur_is_occupied(runtime, robot):
                return None, True
            if entering_spur and (
                not delivering or not self._pallet_collect_maneuver_path(
                    runtime, robot, PALLET_COLLECT_EXIT_CELL_ID, start=EMPTY_PALLET_COLLECT_CELL_ID,
                )
            ):
                return None, True
        return None, bool(candidate.intersection(reserved_footprint))

    def _has_pallet_collect_spur(self, runtime: RunRuntime) -> bool:
        topology = runtime.topology
        return (
            shuttle_state_is_walkable(topology, 74, 47, 1)
            and shuttle_state_is_walkable(topology, 74, 48, 1)
            and shuttle_state_is_walkable(topology, 78, 43, 1)
            and not shuttle_state_is_walkable(topology, 74, 49, 1)
            and not shuttle_state_is_walkable(topology, 76, 47, 1)
        )

    def _pallet_collect_spur_is_occupied(self, runtime: RunRuntime, entrant: dict[str, Any]) -> bool:
        return any(
            other is not entrant
            and (
                other.get("palletCollectEvacuating")
                or self._robot_collision_footprint(runtime, other).intersection(PALLET_COLLECT_SPUR)
                or (find_task(runtime.state["tasks"], other.get("currentTaskId")) or {}).get("phase")
                == "clearing_pallet_collect"
            )
            for other in runtime.state["robots"]
        )

    def _pallet_collect_maneuver_path(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        goal: str,
        *,
        start: str | None = None,
        relocated: tuple[dict[str, Any], str] | None = None,
    ) -> list[str]:
        """Plan a short ground route against actual or proposed parked bodies."""
        _, goal_row, goal_col = parse_cell_id(goal)
        if not shuttle_state_is_walkable(runtime.topology, goal_row, goal_col, 1):
            return []
        row_limit = max(78, goal_row)
        obstacles: set[tuple[int, int, float]] = set()
        for other in runtime.state["robots"]:
            if other is robot:
                continue
            if relocated is not None and other is relocated[0]:
                obstacles.update(self._robot_collision_footprint(runtime, other, relocated[1], 1))
            else:
                obstacles.update(self._robot_collision_footprint(runtime, other))
        if self._robot_collision_footprint(runtime, robot, goal, 1).intersection(obstacles):
            return []
        blocked = {
            (row, col, 1)
            for row in range(74, row_limit + 1)
            for col in range(40, 49)
            if shuttle_collision_footprint(runtime.topology, row, col, 1).intersection(obstacles)
        }
        path = shortest_path_shuttle(
            runtime.topology, start or robot["currentCellId"], 1, goal, 1,
            runtime.config["pathPolicy"], extra_blocked=blocked,
        )
        if 1 < len(path) <= 16 and all(
            74 <= row <= row_limit and 40 <= col <= 48 and layer == 1
            and shuttle_state_is_walkable(runtime.topology, row, col, layer)
            for row, col, layer in path[1:]
        ):
            return [cell_id(row, col) for row, col, _ in path]
        return []

    def _set_pallet_collect_maneuver(
        self,
        robot: dict[str, Any],
        path: list[str],
        phase: str,
        label: str,
    ) -> None:
        robot.update(
            path=path,
            pathLayers=[1] * len(path),
            pathStages=[phase] * len(path),
            targetCellId=path[-1],
            targetLayer=1,
            waitTicks=0,
            yieldTicks=0,
            status="moving",
            phaseLabel=label,
        )

    def _coordinate_pallet_collect_spur(self, runtime: RunRuntime) -> None:
        if not self._has_pallet_collect_spur(runtime):
            return
        tasks = runtime.state["tasks"]
        shuttles = [item for item in runtime.state["robots"] if item.get("type") == "shuttle"]
        for robot in shuttles:
            if robot.get("palletCollectEvacuating") and len(robot["path"]) <= 1:
                robot.pop("palletCollectEvacuating")
                home = robot.get("homeCellId")
                if home and home != robot["currentCellId"] and self._robot_path_to(runtime, robot, home, 1):
                    robot.update(status="returning", returningToStandby=True)
                else:
                    robot.update(status="idle", returningToStandby=False, phaseLabel="待命")

        carrier_at_mouth = any(
            self._robot_collision_footprint(runtime, carrier).intersection(PALLET_COLLECT_MOUTH)
            and (task := find_task(tasks, carrier.get("currentTaskId")))
            and task.get("taskKind") == EMPTY_PALLET_COLLECT_TASK_KIND
            and task.get("phase") == "delivery_to_collect"
            for carrier in shuttles
        )
        for idle in shuttles:
            if (
                idle.get("currentTaskId") or idle.get("carrying")
                or idle.get("status") in {"failed", "exception"}
                or idle.get("palletCollectEvacuating")
            ):
                continue
            footprint = self._robot_collision_footprint(runtime, idle)
            if carrier_at_mouth and footprint & PALLET_COLLECT_EXIT_AREA:
                path = self._pallet_collect_maneuver_path(runtime, idle, PALLET_COLLECT_CLEAR_CELL_ID)
                if path:
                    self._set_pallet_collect_maneuver(idle, path, "idle_yield", "空托盘支线 / 让出出口")
                    idle.update(palletCollectEvacuating=True, returningToStandby=True)
                continue
            if not footprint & PALLET_COLLECT_SPUR:
                continue
            exit_path = self._pallet_collect_maneuver_path(runtime, idle, PALLET_COLLECT_EXIT_CELL_ID)
            if exit_path:
                self._set_pallet_collect_maneuver(idle, exit_path, "idle_yield", "空托盘支线 / 退出至岔口外")
                idle.update(palletCollectEvacuating=True, returningToStandby=True)
                continue
            # A legacy snapshot can already contain a carrier between the
            # idle Shuttle and the turnout. Verify both legs before backing
            # the carrier out. Recheck the exit when the carrier has parked.
            for carrier in shuttles:
                if not (
                    carrier.get("currentLayer") == 1
                    and carrier.get("currentRow") in {74, 75}
                    and 42 <= carrier.get("currentCol", 0) <= 48
                ):
                    continue
                task = find_task(tasks, carrier.get("currentTaskId"))
                if (
                    not task or task.get("taskKind") != EMPTY_PALLET_COLLECT_TASK_KIND
                    or task.get("phase") != "delivery_to_collect"
                ):
                    continue
                for stop in PALLET_COLLECT_RETREAT_CELL_IDS:
                    retreat_path = self._pallet_collect_maneuver_path(runtime, carrier, stop)
                    if not retreat_path or not self._pallet_collect_maneuver_path(
                        runtime, idle, PALLET_COLLECT_EXIT_CELL_ID, relocated=(carrier, stop),
                    ):
                        continue
                    task.update(phase="retreat_from_pallet_collect", phaseLabel="空托盘支线 / 后退让行")
                    self._set_pallet_collect_maneuver(carrier, retreat_path, task["phase"], task["phaseLabel"])
                    break

    def _update_shuttle_progress_watchdogs(self, runtime: RunRuntime) -> None:
        """Track real task progress independently from status-label changes."""

        for robot in runtime.state.get("robots", []):
            if robot.get("type") != "shuttle":
                continue
            task = find_task(runtime.state.get("tasks", []), robot.get("currentTaskId"))
            token = "|".join((
                str(robot.get("currentCellId") or ""),
                str(robot.get("currentLayer") or 1),
                str(robot.get("currentTaskId") or ""),
                str((task or {}).get("phase") or ""),
                str((task or {}).get("picked") or 0),
                str((task or {}).get("status") or ""),
            ))
            previous = str(robot.get("trafficProgressToken") or "")
            active = bool(
                robot.get("currentTaskId")
                or robot.get("returningToStandby")
            ) and robot.get("status") not in {"failed", "exception", "idle"}
            if active and previous == token:
                robot["trafficNoProgressTicks"] = int(robot.get("trafficNoProgressTicks") or 0) + 1
            else:
                robot["trafficNoProgressTicks"] = 0
            robot["trafficProgressToken"] = token

    def _refresh_empty_pallet_traffic(self, runtime: RunRuntime) -> dict[str, Any]:
        """Serialize the final approach to the single-capacity pallet stack."""

        tick = int(runtime.state.get("tick") or 0)
        traffic = runtime.state.setdefault("emptyPalletTraffic", {
            "contractVersion": "empty-pallet-traffic-v1",
            "ownerRobotId": None,
            "ownerTaskId": None,
            "acquiredTick": None,
            "queue": [],
        })
        candidates: list[tuple[int, int, str, str, dict[str, Any], dict[str, Any]]] = []
        for task in runtime.state.get("tasks", []):
            if (
                task.get("status") in TERMINAL_TASK_STATUSES
                or task.get("phase") != "delivery_empty_pallet"
            ):
                continue
            assigned_robot_id = task.get("assignedRobotId")
            robot = next(
                (
                    item for item in runtime.state.get("robots", [])
                    if (
                        item.get("id") == assigned_robot_id
                        if assigned_robot_id
                        else item.get("currentTaskId") == task.get("id")
                    )
                ),
                None,
            )
            if robot is None or robot.get("status") in {"failed", "exception"}:
                continue
            task.setdefault("emptyPalletQueuedTick", tick)
            candidates.append((
                max(0, len(robot.get("path") or []) - 1),
                int(task.get("emptyPalletQueuedTick") or tick),
                str(robot.get("id")),
                str(task.get("id")),
                robot,
                task,
            ))

        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        owner_pair = (
            str(traffic.get("ownerRobotId") or ""),
            str(traffic.get("ownerTaskId") or ""),
        )
        owner = next((item for item in candidates if item[2:4] == owner_pair), None)
        # A vehicle already ahead in the final approach must be allowed to
        # empty the lane. The token owner cannot drive through its footprint,
        # and sending the vehicle ahead backwards can jam the entrance.
        downstream = False
        if owner is not None and candidates and 0 < candidates[0][0] < owner[0]:
            front = candidates[0][4]
            owner_robot = owner[4]
            if owner[0] <= SHUTTLE_FINAL_APPROACH_STEPS:
                layers = owner_robot.get("pathLayers") or []
                blocker, _ = self._physical_move_blocker(
                    runtime,
                    owner_robot,
                    owner_robot["path"][1],
                    layers[1] if len(layers) > 1 else owner_robot.get("currentLayer", 1),
                    set(),
                )
                downstream = blocker is front
        if owner is None or downstream:
            if traffic.get("ownerRobotId"):
                self._record_event(
                    runtime,
                    tick,
                    "empty_pallet_traffic_released",
                    {
                        "robotId": traffic.get("ownerRobotId"),
                        "taskId": traffic.get("ownerTaskId"),
                    },
                )
            traffic.update({
                "ownerRobotId": None,
                "ownerTaskId": None,
                "acquiredTick": None,
            })
        if traffic.get("ownerRobotId") is None and candidates:
            _distance, _queued, robot_id, task_id, _robot, _task = candidates[0]
            traffic.update({
                "ownerRobotId": robot_id,
                "ownerTaskId": task_id,
                "acquiredTick": tick,
            })
            self._record_event(
                runtime,
                tick,
                "empty_pallet_traffic_acquired",
                {"robotId": robot_id, "taskId": task_id},
            )
        traffic["queue"] = [
            item[3] for item in candidates
            if item[3] != traffic.get("ownerTaskId")
        ]
        return traffic

    def _shuttle_traffic_priority(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
    ) -> tuple[int, int, int, str]:
        """Return one stable, aging-aware priority for all Shuttle decisions."""

        task = find_task(runtime.state.get("tasks", []), robot.get("currentTaskId"))
        phase = str((task or {}).get("phase") or "")
        task_kind = str((task or {}).get("taskKind") or "")
        handoff = self._handoff_traffic_state(runtime)
        empty_pallet = runtime.state.get("emptyPalletTraffic") or {}
        if (
            phase.startswith("yielding_")
            or phase in {"retreat_from_pallet_collect", "clearing_pallet_collect"}
            or robot.get("palletCollectEvacuating")
        ):
            rank = 0
        elif robot.get("id") in {
            (handoff or {}).get("ownerRobotId"),
            empty_pallet.get("ownerRobotId"),
        }:
            rank = 1
        elif task_kind in {EMPTY_PALLET_TASK_KIND, PALLET_RETURN_TASK_KIND}:
            rank = 2
        elif robot.get("carrying"):
            rank = 3
        elif phase == "to_handoff_staging":
            rank = 4
        elif phase == "pickup_from_shuttle":
            rank = 5
        elif robot.get("returningToStandby"):
            rank = 7
        else:
            rank = 6
        age = max(
            int(robot.get("waitTicks") or 0),
            int(robot.get("trafficNoProgressTicks") or 0),
        )
        queued_tick = int(
            (task or {}).get("emptyPalletQueuedTick")
            or (task or {}).get("handoffQueuedTick")
            or (task or {}).get("startedTick")
            or runtime.state.get("tick")
            or 0
        )
        return rank, -age, queued_tick, str(robot.get("id") or "")

    def _movement_priority_key(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
    ) -> tuple[Any, ...]:
        # Preserve the former CTU-before-Shuttle execution order while making
        # every Shuttle decision use the same deterministic traffic priority.
        if robot.get("type") != "shuttle":
            return (0, str(robot.get("id") or ""))
        return (1, *self._shuttle_traffic_priority(runtime, robot))

    def _build_shuttle_reservation_plan(
        self,
        runtime: RunRuntime,
        movable: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Build a rolling cooperative reservation table for Shuttle bodies."""

        horizon = SHUTTLE_RESERVATION_HORIZON_TICKS
        footprints: dict[int, list[tuple[str, frozenset[tuple[int, int, float]]]]] = {
            offset: [] for offset in range(1, horizon + 1)
        }
        sweeps: dict[int, list[tuple[str, frozenset[tuple[int, int, float]]]]] = {
            offset: [] for offset in range(1, horizon + 1)
        }
        edges: dict[int, list[tuple[str, tuple[str, float], tuple[str, float]]]] = {
            offset: [] for offset in range(1, horizon + 1)
        }
        decisions: dict[str, dict[str, Any]] = {}
        shuttles = sorted(
            (robot for robot in movable if robot.get("type") == "shuttle"),
            key=lambda item: self._shuttle_traffic_priority(runtime, item),
        )
        for robot in shuttles:
            robot_id = str(robot.get("id") or "")
            path = list(robot.get("path") or [robot.get("currentCellId")])
            if not path or path[0] != robot.get("currentCellId"):
                path = [str(robot.get("currentCellId"))] + path
            layers = list(
                robot.get("pathLayers")
                or [robot.get("currentLayer", 1)] * len(path)
            )
            if len(layers) < len(path):
                layers.extend([layers[-1] if layers else robot.get("currentLayer", 1)] * (len(path) - len(layers)))
            previous_cell = str(robot.get("currentCellId"))
            previous_layer = float(robot.get("currentLayer", 1))
            previous_footprint = self._robot_collision_footprint(
                runtime,
                robot,
                previous_cell,
                previous_layer,
            )
            reserved_steps = 0
            blocker_id: str | None = None
            conflict_tick: int | None = None
            conflict_reason: str | None = None
            for offset in range(1, horizon + 1):
                path_index = min(offset, len(path) - 1)
                candidate_cell = str(path[path_index])
                candidate_layer = float(layers[path_index])
                candidate_footprint = self._robot_collision_footprint(
                    runtime,
                    robot,
                    candidate_cell,
                    candidate_layer,
                )
                swept_footprint = frozenset(previous_footprint | candidate_footprint)
                candidate_entry = (candidate_cell, candidate_layer)
                previous_entry = (previous_cell, previous_layer)
                conflict = next(
                    (
                        owner_id for owner_id, reserved in footprints[offset]
                        if candidate_footprint.intersection(reserved)
                    ),
                    None,
                )
                if conflict is None:
                    conflict = next(
                        (
                            owner_id for owner_id, reserved in sweeps[offset]
                            if swept_footprint.intersection(reserved)
                        ),
                        None,
                    )
                    if conflict is not None:
                        conflict_reason = "SWEPT_FOOTPRINT_CONFLICT"
                else:
                    conflict_reason = "FUTURE_FOOTPRINT_CONFLICT"
                if conflict is None:
                    conflict = next(
                        (
                            owner_id for owner_id, start, end in edges[offset]
                            if start == candidate_entry and end == previous_entry
                        ),
                        None,
                    )
                    if conflict is not None:
                        conflict_reason = "EDGE_SWAP_CONFLICT"
                if conflict is not None:
                    blocker_id = conflict
                    conflict_tick = offset
                    break
                footprints[offset].append((robot_id, candidate_footprint))
                sweeps[offset].append((robot_id, swept_footprint))
                edges[offset].append((robot_id, previous_entry, candidate_entry))
                reserved_steps += 1
                previous_cell = candidate_cell
                previous_layer = candidate_layer
                previous_footprint = candidate_footprint
            decisions[robot_id] = {
                "allowed": reserved_steps > 0,
                "reservedSteps": reserved_steps,
                "conflictTick": conflict_tick,
                "blockerRobotId": blocker_id,
                "reason": conflict_reason,
                "priority": list(self._shuttle_traffic_priority(runtime, robot)[:-1]),
            }

        runtime.state["shuttleTrafficReservations"] = {
            "contractVersion": "shuttle-space-time-reservations-v1",
            "tick": int(runtime.state.get("tick") or 0),
            "horizonTicks": horizon,
            "decisions": [
                {"robotId": robot_id, **decision}
                for robot_id, decision in sorted(decisions.items())
            ],
        }
        return decisions

    def _would_repeat_shuttle_oscillation(
        self,
        robot: dict[str, Any],
        task: dict[str, Any],
        next_cell_id: str,
        next_layer: int | float,
    ) -> bool:
        """Detect only a planned continuation of an established A-B-A-B loop.

        A-B-A followed by a newly planned B-C route is a valid escape, not a
        livelock.  The old three-sample check stopped that escape forever
        because no committed move could ever change the history.  Require two
        complete cycles and verify that the planned step after A returns to B.
        Layer is part of the state so a lift transition at the same cell is not
        mistaken for oscillation.
        """

        history = list(robot.get("trafficMoveHistory") or [])
        if len(history) < 4:
            return False
        recent = history[-4:]
        phase = str(task.get("phase") or "")
        if any(str(item.get("phase") or "") != phase for item in recent):
            return False
        states = [
            (
                str(item.get("cellId") or ""),
                float(item.get("layer") or robot.get("currentLayer", 1)),
            )
            for item in recent
        ]
        if not (
            states[0] == states[2]
            and states[1] == states[3]
            and states[0] != states[1]
        ):
            return False
        if (str(next_cell_id), float(next_layer)) != states[0]:
            return False

        path = list(robot.get("path") or [])
        layers = list(robot.get("pathLayers") or [])
        # Reaching a one-step destination, or proceeding from A to a new C,
        # finishes/leaves the cycle.  Only A followed by B repeats it.
        if len(path) <= 2:
            return False
        following_layer = (
            float(layers[2])
            if len(layers) > 2
            else float(robot.get("currentLayer", 1))
        )
        return (str(path[2]), following_layer) == states[1]

    def _erase_shuttle_path_cycles(self, robot: dict[str, Any]) -> bool:
        """Remove closed state loops while preserving valid lift transitions."""

        path = list(robot.get("path") or [])
        if len(path) <= 2:
            return False
        layers = list(
            robot.get("pathLayers")
            or [robot.get("currentLayer", 1)] * len(path)
        )
        if len(layers) < len(path):
            layers.extend(
                [layers[-1] if layers else robot.get("currentLayer", 1)]
                * (len(path) - len(layers))
            )
        stages = list(robot.get("pathStages") or [])
        if len(stages) < len(path):
            stages.extend(
                [stages[-1] if stages else "moving"]
                * (len(path) - len(stages))
            )

        compact_path: list[str] = []
        compact_layers: list[int | float] = []
        compact_stages: list[str] = []
        state_indexes: dict[tuple[str, float], int] = {}
        changed = False
        for cell, layer, stage in zip(path, layers, stages):
            state = (str(cell), float(layer))
            repeated_at = state_indexes.get(state)
            if repeated_at is not None:
                changed = True
                for removed_cell, removed_layer in zip(
                    compact_path[repeated_at + 1:],
                    compact_layers[repeated_at + 1:],
                ):
                    state_indexes.pop((str(removed_cell), float(removed_layer)), None)
                compact_path = compact_path[:repeated_at + 1]
                compact_layers = compact_layers[:repeated_at + 1]
                compact_stages = compact_stages[:repeated_at + 1]
                continue
            state_indexes[state] = len(compact_path)
            compact_path.append(str(cell))
            compact_layers.append(layer)
            compact_stages.append(str(stage))

        if not changed:
            return False
        robot["path"] = compact_path
        robot["pathLayers"] = compact_layers
        robot["pathStages"] = compact_stages
        return True

    def _move_robots(self, runtime: RunRuntime) -> None:
        self._coordinate_pallet_collect_spur(runtime)
        self._update_shuttle_progress_watchdogs(runtime)
        empty_pallet_traffic = self._refresh_empty_pallet_traffic(runtime)
        movable = [robot for robot in runtime.state["robots"] if robot["status"] not in {"failed", "idle", "exception"}]
        for robot in movable:
            if robot.get("type") == "shuttle":
                self._erase_shuttle_path_cycles(robot)
        movable.sort(key=lambda item: self._movement_priority_key(runtime, item))
        # occupied_cells: (cell-id, layer) tuple —— 同 2D 位置不同层是不同 entry，
        # discard 只释放当前车的那一层，不会误伤其他层的车。
        occupied_cells: set[tuple[str, float]] = {
            (robot["currentCellId"], float(robot.get("currentLayer", 1)))
            for robot in runtime.state["robots"]
        }
        reserved_targets: set[tuple[str, float]] = set()
        reserved_footprint: set[tuple[int, int, float]] = set()
        shuttle_reservations = self._build_shuttle_reservation_plan(runtime, movable)

        for robot in movable:
            task = find_task(runtime.state["tasks"], robot["currentTaskId"])
            if task is None:
                if robot.get("returningToStandby"):
                    reservation = shuttle_reservations.get(str(robot.get("id") or ""))
                    if (
                        robot.get("type") == "shuttle"
                        and reservation is not None
                        and not reservation.get("allowed")
                    ):
                        standby_path = list(robot.get("path") or [])
                        robot["status"] = "blocked"
                        robot["waitTicks"] = int(robot.get("waitTicks") or 0) + 1
                        robot["phaseLabel"] = "返回待命位 / 等待时空预留"
                        self._observe_robot_block(
                            runtime,
                            robot,
                            standby_path[1] if len(standby_path) > 1 else None,
                            str(reservation.get("blockerRobotId") or "") or None,
                            ResourceType.RESERVATION,
                            str(reservation.get("reason") or "SPACE_TIME_RESERVATION_CONFLICT"),
                        )
                        if robot["waitTicks"] >= REROUTE_AFTER_BLOCK_TICKS:
                            blocker_id = str(reservation.get("blockerRobotId") or "")
                            blocker = next(
                                (
                                    item for item in runtime.state.get("robots", [])
                                    if item.get("id") == blocker_id
                                ),
                                None,
                            )
                            if blocker is not None and self._clear_shuttle_head_on(
                                runtime,
                                robot,
                                blocker,
                                occupied_cells,
                                reserved_targets,
                                reserved_footprint,
                            ):
                                robot["waitTicks"] = 0
                        continue
                    self._move_robot_to_standby(
                        runtime,
                        robot,
                        occupied_cells,
                        reserved_targets,
                        reserved_footprint,
                    )
                    continue
                robot["status"] = "idle"
                robot["phaseLabel"] = "待命"
                robot["path"] = [robot["currentCellId"]]
                robot["pathLayers"] = [robot.get("currentLayer", 1)]
                continue

            if robot.get("type") == "shuttle" and task.get("phase") == "yielding_for_traffic":
                priority_id = str(task.get("trafficYieldForRobotId") or "")
                priority = next(
                    (
                        item for item in runtime.state.get("robots", [])
                        if str(item.get("id") or "") == priority_id
                    ),
                    None,
                )
                priority_released = priority is None or (
                    not priority.get("currentTaskId")
                    and not priority.get("carrying")
                )
                if priority_released:
                    # The vehicle for which this Shuttle entered a side bay has
                    # finished its productive movement.  Do not insist on
                    # reaching a now-stale clearance endpoint: another idle
                    # Shuttle may legitimately park there in the meantime and
                    # turn the temporary yield into a permanent deadlock.
                    resume_phase = str(task.get("trafficYieldResumePhase") or "moving")
                    resume_cell = str(task.get("trafficYieldResumeCellId") or "")
                    resume_layer = int(
                        task.get("trafficYieldResumeLayer")
                        or robot.get("currentLayer", 1)
                    )
                    if resume_cell and self._set_robot_leg(
                        runtime,
                        robot,
                        task,
                        resume_phase,
                        resume_cell,
                        target_layer=resume_layer,
                    ):
                        robot["waitTicks"] = 0
                        robot["yieldTicks"] = 0
                        for key in (
                            "trafficYieldResumePhase",
                            "trafficYieldResumeCellId",
                            "trafficYieldResumeLayer",
                            "trafficYieldForRobotId",
                        ):
                            task.pop(key, None)
                        self._record_event(
                            runtime,
                            int(runtime.state.get("tick") or 0),
                            "shuttle_clearance_yield_released",
                            {
                                "robotId": robot.get("id"),
                                "taskId": task.get("id"),
                                "priorityRobotId": priority_id or None,
                                "resumePhase": resume_phase,
                            },
                        )
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
            if (
                robot.get("type") == "shuttle"
                and task.get("phase") == "delivery_empty_pallet"
                and empty_pallet_traffic.get("ownerRobotId") != robot.get("id")
                and len(path) - 1 <= SHUTTLE_FINAL_APPROACH_STEPS
            ):
                robot["status"] = "waiting"
                robot["waitTicks"] = int(robot.get("waitTicks") or 0) + 1
                robot["phaseLabel"] = "空托盘存放入口排队 / 等待通行令牌"
                self._observe_robot_block(
                    runtime,
                    robot,
                    next_cell_id,
                    str(empty_pallet_traffic.get("ownerRobotId") or "") or None,
                    ResourceType.SHUTTLE_TRACK,
                    "EMPTY_PALLET_FINAL_APPROACH_RESERVED",
                )
                continue

            reservation = shuttle_reservations.get(str(robot.get("id") or ""))
            if (
                robot.get("type") == "shuttle"
                and task.get("phase") != "yielding_for_traffic"
                and reservation is not None
                and not reservation.get("allowed")
            ):
                robot["status"] = "blocked"
                robot["waitTicks"] = int(robot.get("waitTicks") or 0) + 1
                robot["phaseLabel"] = f"{task['phaseLabel']} / 等待时空预留"
                blocker_id = str(reservation.get("blockerRobotId") or "")
                self._observe_robot_block(
                    runtime,
                    robot,
                    next_cell_id,
                    blocker_id or None,
                    ResourceType.RESERVATION,
                    str(reservation.get("reason") or "SPACE_TIME_RESERVATION_CONFLICT"),
                )
                if robot["waitTicks"] >= REROUTE_AFTER_BLOCK_TICKS:
                    blocker = next(
                        (
                            item for item in runtime.state.get("robots", [])
                            if item.get("id") == blocker_id
                        ),
                        None,
                    )
                    priority_vehicle = bool(
                        robot.get("carrying")
                        or robot.get("id") in {
                            (self._handoff_traffic_state(runtime) or {}).get("ownerRobotId"),
                            (runtime.state.get("emptyPalletTraffic") or {}).get("ownerRobotId"),
                        }
                        or task.get("taskKind") in {
                            EMPTY_PALLET_TASK_KIND,
                            PALLET_RETURN_TASK_KIND,
                        }
                    )
                    cleared_for_priority = bool(
                        priority_vehicle
                        and blocker is not None
                        and self._clear_shuttle_head_on(
                            runtime,
                            robot,
                            blocker,
                            occupied_cells,
                            reserved_targets,
                            reserved_footprint,
                        )
                    )
                    if cleared_for_priority:
                        robot["waitTicks"] = 0
                    elif self._reroute_shuttle(runtime, robot, occupied_cells):
                        self._observe_deadlock_mitigation(
                            runtime,
                            robot,
                            "REROUTE",
                            "space_time_reservation",
                        )
                        robot["waitTicks"] = 0
                        robot["status"] = "moving"
                    elif blocker is not None and self._clear_shuttle_head_on(
                        runtime,
                        robot,
                        blocker,
                        occupied_cells,
                        reserved_targets,
                        reserved_footprint,
                    ):
                        robot["waitTicks"] = 0
                continue

            if (
                robot.get("type") == "shuttle"
                and task.get("phase") != "yielding_for_traffic"
                and self._would_repeat_shuttle_oscillation(
                    robot,
                    task,
                    next_cell_id,
                    next_layer,
                )
            ):
                tick = int(runtime.state.get("tick") or 0)
                signature = "|".join((
                    str(task.get("id") or ""),
                    str(task.get("phase") or ""),
                    str(robot.get("currentCellId") or ""),
                    str(next_cell_id),
                    str(float(next_layer)),
                ))
                last_signature = str(robot.get("trafficOscillationSignature") or "")
                last_tick = int(robot.get("trafficOscillationRecoveryTick") or -999999)
                recovery_ready = (
                    signature != last_signature
                    or tick - last_tick >= SHUTTLE_OSCILLATION_RECOVERY_COOLDOWN_TICKS
                )
                if recovery_ready:
                    robot["trafficOscillationSignature"] = signature
                    robot["trafficOscillationRecoveryTick"] = tick
                    robot["status"] = "blocked"
                    robot["waitTicks"] = max(
                        REROUTE_AFTER_BLOCK_TICKS,
                        int(robot.get("waitTicks") or 0) + 1,
                    )
                    robot["phaseLabel"] = f"{task['phaseLabel']} / 检测到往返活锁，正在重新规划"
                    self._observe_robot_block(
                        runtime,
                        robot,
                        next_cell_id,
                        None,
                        ResourceType.OTHER,
                        "A_B_A_B_OSCILLATION_GUARD",
                    )
                    if self._reroute_shuttle(runtime, robot, occupied_cells):
                        self._erase_shuttle_path_cycles(robot)
                        self._observe_deadlock_mitigation(
                            runtime,
                            robot,
                            "REROUTE",
                            "oscillation_guard",
                        )
                        robot["waitTicks"] = 0
                        robot["status"] = "moving"
                    # Pause at most this tick. If the same recovery cannot find
                    # an alternative, the cooldown lets the reservation-safe
                    # move proceed next tick instead of self-locking forever.
                    continue
            if (
                robot.get("type") == "shuttle"
                and str((runtime.arm_handoff or {}).get("handoffCellId") or "") == HANDOFF_FIXED_CELL_ID
            ):
                feeder_footprint = self._handoff_queue_feeder_footprint(runtime)
                current_feeder_overlap = bool(
                    self._robot_collision_footprint(runtime, robot).intersection(feeder_footprint)
                )
                next_feeder_overlap = bool(
                    self._robot_collision_footprint(
                        runtime,
                        robot,
                        next_cell_id,
                        next_layer,
                    ).intersection(feeder_footprint)
                )
                entering_feeder = not current_feeder_overlap and next_feeder_overlap
                is_loaded_feeder = self._is_loaded_handoff_queue_feeder(runtime, robot)
                if is_loaded_feeder and entering_feeder:
                    # Do not freeze a loaded convoy merely because an empty
                    # Shuttle is somewhere in the broad feeder area.  That
                    # created a circular wait: the empty Shuttle respected the
                    # loaded vehicle's future reservation while the loaded
                    # vehicle waited for the empty one to leave.  Wait only for
                    # an immediate physical conflict; later steps are still
                    # protected by the rolling 2x2 space-time reservation.
                    next_footprint = self._robot_collision_footprint(
                        runtime,
                        robot,
                        next_cell_id,
                        next_layer,
                    )
                    opposing_ahead = any(
                        other.get("id") != robot.get("id")
                        and not self._is_loaded_handoff_queue_feeder(runtime, other)
                        and bool(other.get("currentTaskId") or other.get("returningToStandby"))
                        and bool(
                            self._robot_collision_footprint(runtime, other).intersection(next_footprint)
                        )
                        for other in runtime.state.get("robots", [])
                    )
                    if opposing_ahead:
                        robot["status"] = "waiting"
                        robot["phaseLabel"] = "输送线入口等待空车退出"
                        continue
                elif (
                    not is_loaded_feeder
                    and entering_feeder
                    and self._loaded_handoff_queue_feeders_active(runtime)
                ):
                    # Do not park an empty vehicle directly in front of the
                    # loaded convoy at the reservation boundary.  If the next
                    # step of a feeder already needs this vehicle's current
                    # footprint, let the empty vehicle continue through and
                    # make the loaded convoy wait until it has evacuated.
                    current_footprint = self._robot_collision_footprint(runtime, robot)
                    blocks_loaded_approach = False
                    for feeder in runtime.state.get("robots", []):
                        if not self._is_loaded_handoff_queue_feeder(runtime, feeder):
                            continue
                        feeder_path = feeder.get("path") or []
                        if len(feeder_path) <= 1:
                            continue
                        feeder_layers = feeder.get("pathLayers") or [feeder.get("currentLayer", 1)] * len(feeder_path)
                        feeder_next_layer = feeder_layers[1] if len(feeder_layers) > 1 else feeder.get("currentLayer", 1)
                        if current_footprint.intersection(
                            self._robot_collision_footprint(
                                runtime,
                                feeder,
                                feeder_path[1],
                                feeder_next_layer,
                            )
                        ):
                            blocks_loaded_approach = True
                            break
                    if blocks_loaded_approach:
                        pass
                    else:
                        target_cell = str(robot.get("targetCellId") or "")
                        target_layer = int(robot.get("targetLayer") or robot.get("currentLayer", 1))
                        rerouted = bool(
                            target_cell
                            and self._robot_path_to(
                                runtime,
                                robot,
                                target_cell,
                                target_layer,
                                extra_blocked=self._handoff_queue_feeder_blocked_states(runtime),
                                task_id=task.get("id"),
                                evidence_kind="replan",
                            )
                        )
                        rerouted_path = list(robot.get("path") or [])
                        rerouted_layers = list(
                            robot.get("pathLayers")
                            or [robot.get("currentLayer", 1)] * len(rerouted_path)
                        )
                        rerouted_away = False
                        if rerouted and len(rerouted_path) > 1:
                            rerouted_next_layer = (
                                rerouted_layers[1]
                                if len(rerouted_layers) > 1
                                else robot.get("currentLayer", 1)
                            )
                            rerouted_away = not bool(
                                self._robot_collision_footprint(
                                    runtime,
                                    robot,
                                    rerouted_path[1],
                                    rerouted_next_layer,
                                ).intersection(feeder_footprint)
                            )
                        if rerouted_away:
                            robot["status"] = "moving"
                            robot["waitTicks"] = 0
                            robot["phaseLabel"] = "绕行输送线入队口"
                        else:
                            robot["status"] = "waiting"
                            robot["phaseLabel"] = "等待载货车队通过"
                        continue
            if (
                robot.get("type") == "shuttle"
                and task.get("phase") == "to_handoff_staging"
                and str((runtime.arm_handoff or {}).get("handoffCellId") or "") == HANDOFF_FIXED_CELL_ID
            ):
                protected = self._handoff_protected_footprint(runtime)
                current_footprint = self._robot_collision_footprint(runtime, robot)
                next_footprint = self._robot_collision_footprint(
                    runtime,
                    robot,
                    next_cell_id,
                    next_layer,
                )
                traffic = self._handoff_traffic_state(runtime)
                queue_head_ready = any(
                    queued_task.get("phase") == "waiting_handoff_slot"
                    and queued_task.get("handoffStagingCellId") == HANDOFF_QUEUE_SLOT_CELL_IDS[0]
                    for queued_task in runtime.state.get("tasks", [])
                )
                corridor_reserved = bool((traffic or {}).get("ownerRobotId") or queue_head_ready)
                entering_corridor = (
                    not current_footprint.intersection(protected)
                    and bool(next_footprint.intersection(protected))
                )
                if corridor_reserved and entering_corridor:
                    staging_cell = str(task.get("handoffStagingCellId") or "")
                    staging_layer = int(task.get("handoffStagingLayer") or 1)
                    rerouted = bool(
                        staging_cell
                        and self._robot_path_to(
                            runtime,
                            robot,
                            staging_cell,
                            staging_layer,
                            extra_blocked=self._handoff_queue_approach_blocked_states(runtime),
                            task_id=task.get("id"),
                            evidence_kind="replan",
                        )
                    )
                    if rerouted:
                        robot["status"] = "moving"
                        robot["waitTicks"] = 0
                        robot["phaseLabel"] = "绕行返程通道 / 前往输送线队列"
                        task["phaseLabel"] = robot["phaseLabel"]
                    else:
                        robot["status"] = "waiting"
                        robot["phaseLabel"] = "输送线入口等待交接区清空"
                    continue
            if not self._physical_movement_ready(runtime, robot, next_cell_id, float(next_layer)):
                robot["phaseLabel"] = task["phaseLabel"]
                continue

            # 碰撞检查：目标格(含 layer)不是自己当前位置 且 被其他车/预占占用
            # 关键：保持 occupied_cells 完整，不 discard 当前车自己的 entry。
            # 只有在真正移动成功时才 discard 旧格 + add 新格。
            current_entry = (current_cell_id, float(robot.get("currentLayer", 1)))
            next_entry = (next_cell_id, float(next_layer))
            physical_blocker: dict[str, Any] | None = None
            footprint_reserved = False
            if next_entry != current_entry:
                physical_blocker, footprint_reserved = self._physical_move_blocker(
                    runtime,
                    robot,
                    next_cell_id,
                    next_layer,
                    reserved_footprint,
                )
            if next_entry != current_entry and (
                next_entry in occupied_cells
                or next_entry in reserved_targets
                or physical_blocker is not None
                or footprint_reserved
            ):
                robot["status"] = "blocked"
                robot["waitTicks"] += 1
                robot["phaseLabel"] = f"{task['phaseLabel']} / 等待让行"
                floor, row, col = parse_cell_id(robot["currentCellId"])
                self._record_event(
                    runtime,
                    runtime.state["tick"],
                    "robot_position",
                    {
                        "robotId": robot["id"],
                        "cellId": robot["currentCellId"],
                        "floor": floor,
                        "row": row,
                        "col": col,
                        "layer": float(robot.get("currentLayer", 1)),
                        "status": "blocked",
                    },
                )
                exact_blocker = next(
                    (item for item in movable if (item["currentCellId"], float(item.get("currentLayer", 1))) == next_entry),
                    None,
                )
                blocker = physical_blocker or exact_blocker
                observed_blocker = physical_blocker or next(
                    (
                        item
                        for item in runtime.state["robots"]
                        if item.get("id") != robot.get("id")
                        and (item["currentCellId"], float(item.get("currentLayer", 1))) == next_entry
                    ),
                    None,
                )
                block_resource = (
                    ResourceType.ROBOT
                    if observed_blocker is not None
                    else ResourceType.RESERVATION
                    if next_entry in reserved_targets or footprint_reserved
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
                        if next_entry in reserved_targets or footprint_reserved
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
                    fixed_queue_feeder = bool(
                        task.get("phase") == "to_handoff_staging"
                        and str((runtime.arm_handoff or {}).get("handoffCellId") or "") == HANDOFF_FIXED_CELL_ID
                    )
                    idle_blocker = bool(
                        blocker is not None
                        and not blocker.get("currentTaskId")
                        and not blocker.get("carrying")
                    )
                    # Loaded FIFO feeders keep their one-way route. Resolve an
                    # opposing unladen pickup first; replanning the loaded car
                    # to the same corridor merely reset waitTicks forever.
                    cleared_head_on = bool(
                        (fixed_queue_feeder or idle_blocker)
                        and self._clear_shuttle_head_on(
                            runtime,
                            robot,
                            blocker,
                            occupied_cells,
                            reserved_targets,
                            reserved_footprint,
                        )
                    )
                    if cleared_head_on:
                        robot["waitTicks"] = 0
                        robot["status"] = "moving"
                        robot["phaseLabel"] = task["phaseLabel"]
                    elif self._reroute_shuttle(runtime, robot, occupied_cells):
                        self._observe_deadlock_mitigation(runtime, robot, "REROUTE")
                        robot["waitTicks"] = 0
                        robot["status"] = "moving"
                        robot["phaseLabel"] = task["phaseLabel"]
                    elif self._clear_shuttle_head_on(
                        runtime,
                        robot,
                        blocker,
                        occupied_cells,
                        reserved_targets,
                        reserved_footprint,
                    ):
                        robot["waitTicks"] = 0
                        robot["status"] = "moving"
                        robot["phaseLabel"] = task["phaseLabel"]
                continue

            motion_from = self._motion_position(runtime, robot)
            self._consume_motion_credit(robot)
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
            # 真正移动成功：释放旧格(含 layer)，占用新格(含 layer)
            occupied_cells.discard(current_entry)
            occupied_cells.add(next_entry)
            reserved_targets.add(next_entry)
            reserved_footprint.update(self._robot_collision_footprint(runtime, robot))
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

    def _reroute_ctu(self, runtime: RunRuntime, robot: dict[str, Any], occupied_cells: set[tuple[str, float]]) -> bool:
        """CTU 被堵时绕行：把其它设备当前占用的格临时视为障碍，重新算一条到当前目标的路径。"""
        target = robot.get("targetCellId")
        if not target or target == robot["currentCellId"]:
            return False
        # occupied_cells 是 (cell-id, layer) tuple 集合，CTU 用 2D pathfinding → 只取 cell-id 部分
        current_entry = (robot["currentCellId"], float(robot.get("currentLayer", 1)))
        occupied_entries = normalize_occupied_entries(occupied_cells)
        others = {parse_cell_id(cell) for cell, layer_f in occupied_entries if (cell, layer_f) != current_entry}
        # 普通绕行不得立即返回上一格，否则两辆相邻 CTU 会同步在两条巷道间
        # A-B-A-B 往返。真正需要后退时由 _clear_head_on/_yield_ctu 显式执行。
        last_cell = robot.get("lastCellId")
        if last_cell and last_cell != target:
            others.add(parse_cell_id(last_cell))
        diagnostic: dict[str, Any] = {}
        path_cells = shortest_path(
            runtime.topology,
            robot["currentCellId"],
            target,
            runtime.config["pathPolicy"],
            avoid_storage=True,
            extra_blocked=others,
            evidence=diagnostic,
        )
        task = find_task(runtime.state["tasks"], robot.get("currentTaskId"))
        if task and task.get("phase") == "pickup_from_ctu" and len(path_cells) > 1:
            path_cells = path_cells[:-1]
        if not path_cells or len(path_cells) <= 1 or (path_cells[1]["id"], float(robot.get("currentLayer", 1))) in occupied_cells:
            return False
        robot["path"] = [cell["id"] for cell in path_cells]
        robot["pathLayers"] = [1] * len(robot["path"])
        robot["targetCellId"] = target
        self._record_planning_evidence(
            runtime,
            robot,
            robot.get("currentTaskId"),
            list(robot["path"]),
            list(robot["pathLayers"]),
            "replan",
            diagnostic,
        )
        return True

    def _reroute_shuttle(self, runtime: RunRuntime, robot: dict[str, Any], occupied_cells: set[tuple[str, float]]) -> bool:
        """四向车被堵时绕行：把其它四向车占用的格（含其下一步意图格）视为动态障碍，
        重新规划到原路径终点（path[-1]，即当前阶段目标）。轨道回放任务不重排。"""
        task = find_task(runtime.state["tasks"], robot.get("currentTaskId"))
        if task and task.get("mode") == "shuttle_transfer":
            return False
        path = robot.get("path") or []
        if len(path) <= 1:
            return False
        original_path = [str(value) for value in path]
        original_layers = [
            float(value)
            for value in (
                robot.get("pathLayers")
                or [robot.get("currentLayer", 1)] * len(path)
            )
        ]
        goal_cell = path[-1]
        goal_layer = int((robot.get("pathLayers") or [robot.get("currentLayer", 1)] * len(path))[-1])
        current_layer = int(robot.get("currentLayer", 1))
        others: set[tuple[int, int, int]] = set()
        current_footprint = self._robot_collision_footprint(runtime, robot)
        for item in runtime.state["robots"]:
            if item["id"] == robot["id"] or item.get("type") != "shuttle":
                continue
            if current_footprint.intersection(self._robot_collision_footprint(runtime, item)):
                continue
            layer = int(item.get("currentLayer", 1))
            others.add((int(item["currentRow"]), int(item["currentCol"]), layer))
            blocker_path = item.get("path") or []
            if len(blocker_path) > 1 and blocker_path[1] != item["currentCellId"]:
                _floor, nr, nc = parse_cell_id(blocker_path[1])
                blocker_layers = item.get("pathLayers") or [layer] * len(blocker_path)
                next_layer = int(blocker_layers[1]) if len(blocker_layers) > 1 else layer
                others.add((nr, nc, next_layer))
        others.discard((int(robot["currentRow"]), int(robot["currentCol"]), current_layer))
        physical_blocked = expand_shuttle_blocked_states(runtime.topology, others)
        if task and task.get("phase") in {"delivery_empty_pallet", "return_pallet_to_lane"}:
            physical_blocked.update(self._handoff_return_route_blocked_states(runtime) or set())
        diagnostic: dict[str, Any] = {}
        path3d = shortest_path_shuttle(
            runtime.topology,
            robot["currentCellId"],
            current_layer,
            goal_cell,
            goal_layer,
            runtime.config["pathPolicy"],
            extra_blocked=physical_blocked,
            evidence=diagnostic,
        )
        if not path3d or len(path3d) <= 1:
            return False
        first = cell_id(path3d[1][0], path3d[1][1], "1F")
        first_layer = float(path3d[1][2]) if len(path3d[1]) > 2 else current_layer
        # A normal replan must not immediately undo the last committed move.
        # Around the handoff this produced an endless c47 <-> c48 oscillation
        # in front of an idle Shuttle and prevented the dedicated idle-blocker
        # yielding path from ever running.
        if first == robot.get("lastCellId") and first_layer == float(robot.get("currentLayer", 1)):
            return False
        if (first, first_layer) in occupied_cells:
            return False
        stage = task.get("phase", "moving") if task else "moving"
        replanned_path = [cell_id(row, col, "1F") for row, col, _ in path3d]
        replanned_layers = [layer for _, _, layer in path3d]
        if (
            replanned_path == original_path
            and [float(value) for value in replanned_layers] == original_layers
        ):
            # A deterministic planner often returns the exact route that is
            # already blocked. Treating that as mitigation reset waitTicks on
            # every retry and prevented the side-yield/backoff stage forever.
            return False
        robot["path"] = replanned_path
        robot["pathLayers"] = replanned_layers
        robot["pathStages"] = [stage] * len(robot["path"])
        self._record_planning_evidence(
            runtime,
            robot,
            robot.get("currentTaskId"),
            list(robot["path"]),
            list(robot["pathLayers"]),
            "replan",
            diagnostic,
        )
        return True

    def _yield_shuttle_to_clearance(
        self,
        runtime: RunRuntime,
        yielding: dict[str, Any],
        priority: dict[str, Any],
        occupied_cells: set[tuple[str, float]],
        reserved_targets: set[tuple[str, float]],
        reserved_footprint: set[tuple[int, int, float]],
    ) -> bool:
        """Route one Shuttle into a verified side position until traffic clears.

        A one-cell back-off is not sufficient for a snapped 2x2 Shuttle: it can
        alternate between two logical cells while both physical positions still
        block the other vehicle. On any floor, select the nearest reachable
        state whose complete body is outside the priority vehicle's remaining
        corridor, then resume the original task through dynamic replanning.
        """

        task = find_task(runtime.state.get("tasks", []), yielding.get("currentTaskId"))
        if task is None or task.get("phase") == "yielding_for_traffic":
            return False
        if yielding.get("type") != "shuttle" or priority.get("type") != "shuttle":
            return False

        layer = int(yielding.get("currentLayer", 1))
        if layer != int(priority.get("currentLayer", 1)):
            return False
        try:
            _floor, current_row, current_col = parse_cell_id(str(yielding["currentCellId"]))
        except (KeyError, TypeError, ValueError):
            return False
        resume_cell = str(yielding.get("targetCellId") or "")
        if not resume_cell:
            return False
        resume_layer = int(yielding.get("targetLayer") or layer)
        resume_phase = str(task.get("phase") or "moving")

        priority_path = list(priority.get("path") or [priority.get("currentCellId")])
        priority_layers = list(
            priority.get("pathLayers")
            or [priority.get("currentLayer", 1)] * len(priority_path)
        )
        priority_corridor: set[tuple[int, int, float]] = set()
        for index, path_cell in enumerate(priority_path[:16]):
            try:
                _floor, path_row, path_col = parse_cell_id(str(path_cell))
            except (TypeError, ValueError):
                continue
            path_layer = int(
                priority_layers[index]
                if index < len(priority_layers)
                else priority.get("currentLayer", 1)
            )
            priority_corridor.update(
                shuttle_collision_footprint(
                    runtime.topology,
                    path_row,
                    path_col,
                    path_layer,
                )
            )
        if not priority_corridor:
            return False

        # Do not feed the priority vehicle's *future* corridor into the path
        # finder's hard blocked-state set.  A 2x2 Shuttle that is already in
        # that corridor often needs one or two logical bridge states before it
        # can reach a side bay.  Treating all future states as walls makes the
        # escape impossible before the monotonic-overlap validation below even
        # gets a chance to evaluate it.  Current physical footprints remain a
        # strict collision barrier, while the future corridor is handled by
        # the decreasing-overlap rule.
        other_states: set[tuple[int, int, int]] = set()
        other_footprints: set[tuple[int, int, float]] = set()
        for other in runtime.state.get("robots", []):
            if other.get("id") == yielding.get("id") or other.get("type") != "shuttle":
                continue
            other_layer = int(other.get("currentLayer", 1))
            other_states.add((int(other["currentRow"]), int(other["currentCol"]), other_layer))
            other_footprints.update(self._robot_collision_footprint(runtime, other))
        blocked_states = expand_shuttle_blocked_states(runtime.topology, other_states)

        handoff_cell = str((runtime.arm_handoff or {}).get("handoffCellId") or "")
        has_collect_spur = self._has_pallet_collect_spur(runtime)

        chosen_path: list[tuple[int, int, int]] | None = None
        # Eight logical cells is deliberately bounded: this is a local escape,
        # not a replacement for the task's normal route planner.
        for radius in range(1, 9):
            candidates: list[tuple[int, int]] = []
            for d_row in range(-radius, radius + 1):
                d_col = radius - abs(d_row)
                candidates.append((current_row + d_row, current_col + d_col))
                if d_col:
                    candidates.append((current_row + d_row, current_col - d_col))
            for candidate_row, candidate_col in sorted(set(candidates)):
                if not shuttle_state_is_walkable(
                    runtime.topology,
                    candidate_row,
                    candidate_col,
                    layer,
                ):
                    continue
                candidate_cell = cell_id(candidate_row, candidate_col, "1F")
                if candidate_cell == handoff_cell:
                    continue
                candidate_entry = (candidate_cell, float(layer))
                if candidate_entry in occupied_cells or candidate_entry in reserved_targets:
                    continue
                candidate_footprint = shuttle_collision_footprint(
                    runtime.topology,
                    candidate_row,
                    candidate_col,
                    layer,
                )
                if (
                    candidate_footprint.intersection(priority_corridor)
                    or candidate_footprint.intersection(other_footprints)
                    or candidate_footprint.intersection(reserved_footprint)
                    or (has_collect_spur and candidate_footprint.intersection(PALLET_COLLECT_SPUR))
                ):
                    continue
                path = shortest_path_shuttle(
                    runtime.topology,
                    str(yielding["currentCellId"]),
                    layer,
                    candidate_cell,
                    layer,
                    runtime.config["pathPolicy"],
                    extra_blocked=blocked_states,
                )
                if not path or len(path) <= 1:
                    continue
                # A snapped 2x2 body can need one or more logical bridge steps
                # before its footprint leaves the priority corridor. Permit
                # those steps only while overlap is monotonically decreasing;
                # real overlap with another vehicle/reservation is never
                # allowed, and the selected destination is already required to
                # be completely outside the priority corridor.
                previous_overlap = len(
                    self._robot_collision_footprint(runtime, yielding).intersection(
                        priority_corridor
                    )
                )
                escape_valid = True
                for row, col, state_layer in path[1:]:
                    step_footprint = shuttle_collision_footprint(
                        runtime.topology,
                        row,
                        col,
                        state_layer,
                    )
                    if step_footprint.intersection(
                        other_footprints | reserved_footprint
                    ) or (has_collect_spur and step_footprint.intersection(PALLET_COLLECT_SPUR)):
                        escape_valid = False
                        break
                    overlap = len(step_footprint.intersection(priority_corridor))
                    if overlap > previous_overlap:
                        escape_valid = False
                        break
                    previous_overlap = overlap
                if not escape_valid or previous_overlap != 0:
                    continue
                chosen_path = path
                break
            if chosen_path is not None:
                break
        if chosen_path is None:
            return False

        task["trafficYieldResumePhase"] = resume_phase
        task["trafficYieldResumeCellId"] = resume_cell
        task["trafficYieldResumeLayer"] = resume_layer
        task["trafficYieldForRobotId"] = priority.get("id")
        task["phase"] = "yielding_for_traffic"
        task["phaseLabel"] = "驶入安全侧位 / 等待冲突通道清空"
        task.setdefault("history", []).append({
            "tick": int(runtime.state.get("tick") or 0),
            "phase": task["phase"],
            "status": task.get("status", "in_progress"),
        })
        yielding.update({
            "path": [cell_id(row, col, "1F") for row, col, _ in chosen_path],
            "pathLayers": [state_layer for _, _, state_layer in chosen_path],
            "pathStages": [task["phase"]] * len(chosen_path),
            "targetCellId": cell_id(chosen_path[-1][0], chosen_path[-1][1], "1F"),
            "targetLayer": chosen_path[-1][2],
            "status": "moving",
            "phaseLabel": task["phaseLabel"],
            "waitTicks": 0,
            "yieldTicks": 0,
        })
        self._observe_deadlock_mitigation(
            runtime,
            yielding,
            "YIELD_CLEARANCE",
            f"priority={priority.get('id')}",
        )
        self._record_event(
            runtime,
            int(runtime.state.get("tick") or 0),
            "shuttle_clearance_yield_planned",
            {
                "robotId": yielding.get("id"),
                "priorityRobotId": priority.get("id"),
                "taskId": task.get("id"),
                "fromCellId": yielding.get("currentCellId"),
                "toCellId": yielding.get("targetCellId"),
            },
        )
        return True

    def _back_off_shuttle(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        occupied_cells: set[tuple[str, float]],
        reserved_targets: set[tuple[str, float]],
        reserved_footprint: set[tuple[int, int, float]] | None = None,
        *,
        allow_same_footprint_bridge: bool = False,
    ) -> bool:
        """Move a Shuttle back to its previous same-layer cell to break a stand-off."""
        current_layer = float(robot.get("currentLayer", 1))
        last = robot.get("lastCellId")
        last_entry = (last, current_layer)
        if not last or last == robot["currentCellId"] or last_entry in occupied_cells or last_entry in reserved_targets:
            return False
        try:
            _last_floor, last_row, last_col = parse_cell_id(str(last))
            _cur_floor, current_row, current_col = parse_cell_id(str(robot["currentCellId"]))
        except (TypeError, ValueError):
            return False
        # Recovery positions have no endpoint exemption: they must be a real
        # Shuttle route state and a direct graph-neighbour of the current state.
        # This prevents coordinates such as cell-r94-c47 from being invented by
        # deadlock handling.
        explicit_last = next(
            (cell for cell in runtime.topology.get("cells", []) if cell.get("id") == last),
            None,
        )
        if not shuttle_state_is_walkable(runtime.topology, last_row, last_col, current_layer) and not (
            explicit_last is not None
            and explicit_last.get("type") != "blocked"
            and explicit_last.get("walkable", True)
        ):
            return False
        step_distance = abs(last_row - current_row) + abs(last_col - current_col)
        if step_distance < 1 or step_distance > 2:
            return False
        if (
            not allow_same_footprint_bridge
            and self._robot_collision_footprint(runtime, robot, str(last), current_layer)
            == self._robot_collision_footprint(runtime, robot)
        ):
            return False
        physical_blocker, footprint_reserved = self._physical_move_blocker(
            runtime,
            robot,
            last,
            current_layer,
            reserved_footprint or set(),
        )
        if physical_blocker is not None or footprint_reserved:
            return False
        if not self._physical_movement_ready(
            runtime,
            robot,
            last,
            current_layer,
        ):
            return False
        motion_from = self._motion_position(runtime, robot)
        self._consume_motion_credit(robot)
        old_current = robot["currentCellId"]
        old_path = robot.get("path") or [robot["currentCellId"]]
        old_layers = robot.get("pathLayers") or [current_layer] * len(old_path)
        floor, row, col = parse_cell_id(last)
        robot["currentCellId"] = last
        robot["currentNode"] = last
        robot["currentFloor"] = floor
        robot["currentRow"] = row
        robot["currentCol"] = col
        robot["lastCellId"] = old_current
        robot["path"] = [last] + list(old_path)
        robot["pathLayers"] = [current_layer] + list(old_layers)
        task = find_task(runtime.state["tasks"], robot.get("currentTaskId"))
        stage = task.get("phase", "moving") if task else "moving"
        robot["pathStages"] = [stage] * len(robot["path"])
        occupied_cells.discard((old_current, current_layer))
        occupied_cells.add(last_entry)
        reserved_targets.add(last_entry)
        if reserved_footprint is not None:
            reserved_footprint.update(self._robot_collision_footprint(runtime, robot))
        self._record_committed_motion(
            runtime,
            robot,
            motion_from,
            MovementType.BACKOFF,
            "_back_off_shuttle",
        )
        self._observe_deadlock_mitigation(runtime, robot, "BACKTRACK", "shuttle_head_on")
        return True

    def _retreat_handoff_staging(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
    ) -> bool:
        """Send a loaded waiting Shuttle back along its verified approach.

        A branch-local holding point can still overlap the return footprint of
        the Shuttle that owns the serialized arm corridor. Reversing the
        already planned approach is safer than inventing a side-step: every
        state is known to be valid and normal collision checks still apply.
        """

        task = find_task(runtime.state.get("tasks", []), robot.get("currentTaskId"))
        if task is None or task.get("phase") != "waiting_handoff_slot":
            return False
        cells = list(task.get("handoffStagingApproachCells") or [])
        layers = list(task.get("handoffStagingApproachLayers") or [])
        if len(cells) <= 1 or cells[-1] != robot.get("currentCellId"):
            return False
        if len(layers) != len(cells):
            layers = [robot.get("currentLayer", 1)] * len(cells)

        # One Shuttle footprint only needs a short pull-back to clear the
        # branch mouth. Returning all the way to the rack would create new
        # head-on conflicts and waste the benefit of staging.
        retreat_length = min(len(cells), 6)
        retreat_cells = list(reversed(cells))[:retreat_length]
        retreat_layers = list(reversed(layers))[:retreat_length]
        retained_length = len(cells) - retreat_length + 1
        task["handoffStagingApproachCells"] = cells[:retained_length]
        task["handoffStagingApproachLayers"] = layers[:retained_length]
        task["phase"] = "yielding_handoff_staging"
        task["phaseLabel"] = "等待区主动退出 / 让返程车先行"
        robot.update({
            "status": "moving",
            "phaseLabel": task["phaseLabel"],
            "path": retreat_cells,
            "pathLayers": retreat_layers,
            "pathStages": [task["phase"]] * len(retreat_cells),
            "targetCellId": retreat_cells[-1],
            "targetLayer": retreat_layers[-1],
            "waitTicks": 0,
        })
        self._record_event(
            runtime,
            int(runtime.state.get("tick") or 0),
            "handoff_staging_retreat",
            {
                "robotId": robot["id"],
                "taskId": task["id"],
                "fromCellId": retreat_cells[0],
                "toCellId": retreat_cells[-1],
            },
        )
        return True

    def _idle_shuttle_clearance_step(
        self,
        runtime: RunRuntime,
        idle: dict[str, Any],
        row: int,
        col: int,
        layer: float,
        corridor: frozenset[tuple[int, int, float]],
        occupied_cells: set[tuple[str, float]],
        reserved_targets: set[tuple[str, float]],
        reserved_footprint: set[tuple[int, int, float]],
    ) -> tuple[int, int] | None:
        """Find the first step of a short, physically clear exit."""
        start = (row, col)
        pending = deque([(start, start, 0)])
        visited = {start}
        handoff_cell = (runtime.arm_handoff or {}).get("handoffCellId")
        has_collect_spur = self._has_pallet_collect_spur(runtime)
        obstacles = set(reserved_footprint)
        for other in runtime.state["robots"]:
            if other is not idle:
                obstacles.update(self._robot_collision_footprint(runtime, other))
        while pending:
            position, first_step, distance = pending.popleft()
            if distance:
                footprint = shuttle_collision_footprint(runtime.topology, *position, layer)
                if not footprint.intersection(corridor):
                    return first_step
            if distance >= 8:
                continue
            r, c = position
            for neighbor in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                target = cell_id(*neighbor, "1F")
                entry = (target, layer)
                if (
                    target == handoff_cell
                    or entry in occupied_cells
                    or entry in reserved_targets
                    or not shuttle_state_is_walkable(runtime.topology, *neighbor, layer)
                ):
                    continue
                footprint = shuttle_collision_footprint(runtime.topology, *neighbor, layer)
                if not footprint.intersection(obstacles) and not (
                    has_collect_spur and footprint.intersection(PALLET_COLLECT_SPUR)
                ):
                    pending.append((neighbor, first_step if distance else neighbor, distance + 1))
        return None

    def _yield_idle_shuttle(
        self,
        runtime: RunRuntime,
        idle: dict[str, Any],
        requester: dict[str, Any],
        occupied_cells: set[tuple[str, float]],
        reserved_targets: set[tuple[str, float]],
        reserved_footprint: set[tuple[int, int, float]],
        *,
        depth: int = 0,
        visited: frozenset[str] = frozenset(),
        corridor: frozenset[tuple[int, int, float]] | None = None,
    ) -> bool:
        """Move an idle blocker to a verified neighbouring Shuttle state.

        Every recursive push uses the original requester's remaining corridor.
        A narrow two-cell branch can require one temporary step farther into
        that corridor before the idle vehicle can leave it, so overlap growth
        is ranked last instead of being rejected outright.
        """

        idle_id = str(idle.get("id"))
        if depth > 4 or idle_id in visited:
            return False
        next_visited = visited | {idle_id}
        layer = float(idle.get("currentLayer", 1))
        try:
            _floor, row, col = parse_cell_id(str(idle["currentCellId"]))
            _request_floor, request_row, request_col = parse_cell_id(str(requester["currentCellId"]))
        except (KeyError, TypeError, ValueError):
            return False
        if corridor is None:
            corridor_cells: set[tuple[int, int, float]] = set()
            requester_path = requester.get("path") or [requester["currentCellId"]]
            requester_layers = requester.get("pathLayers") or [requester.get("currentLayer", 1)] * len(requester_path)
            for index, path_cell in enumerate(requester_path):
                path_layer = float(
                    requester_layers[index]
                    if index < len(requester_layers)
                    else requester.get("currentLayer", 1)
                )
                if path_layer == layer:
                    corridor_cells.update(
                        self._robot_collision_footprint(runtime, requester, path_cell, path_layer)
                    )
            corridor = frozenset(corridor_cells)
        current_overlap = len(self._robot_collision_footprint(runtime, idle).intersection(corridor))
        current_footprint = self._robot_collision_footprint(runtime, idle)
        handoff_cell = str((runtime.arm_handoff or {}).get("handoffCellId") or "")
        clearance_step = self._idle_shuttle_clearance_step(
            runtime, idle, row, col, layer, corridor, occupied_cells,
            reserved_targets, reserved_footprint,
        )
        candidates: list[tuple[int, int, int, int, str, int, int]] = []
        idle_chain_pending = False
        neighbours = (
            (row - 1, col),
            (row + 1, col),
            (row, col - 1),
            (row, col + 1),
        )
        for next_row, next_col in (clearance_step,) if clearance_step else neighbours:
            if not shuttle_state_is_walkable(runtime.topology, next_row, next_col, layer):
                continue
            target_cell = cell_id(next_row, next_col, "1F")
            target_entry = (target_cell, layer)
            if target_cell == handoff_cell:
                continue
            if target_entry in occupied_cells or target_entry in reserved_targets:
                continue
            target_footprint = self._robot_collision_footprint(runtime, idle, target_cell, layer)
            changes_footprint = target_footprint != current_footprint
            if not changes_footprint and clearance_step is None:
                # Some two-cell-wide branches require a logical intermediate
                # whose 2x2 footprint is unchanged.  Admit it only when the
                # intermediate has a following state that really changes the
                # footprint; otherwise it is pure coordinate oscillation.
                bridge_has_exit = False
                for exit_row, exit_col in (
                    (next_row - 1, next_col),
                    (next_row + 1, next_col),
                    (next_row, next_col - 1),
                    (next_row, next_col + 1),
                ):
                    if (exit_row, exit_col) == (row, col):
                        continue
                    if not shuttle_state_is_walkable(runtime.topology, exit_row, exit_col, layer):
                        continue
                    exit_cell = cell_id(exit_row, exit_col, "1F")
                    if exit_cell == handoff_cell:
                        continue
                    if self._robot_collision_footprint(runtime, idle, exit_cell, layer) != current_footprint:
                        bridge_has_exit = True
                        break
                if not bridge_has_exit:
                    continue
            candidate_overlap = len(target_footprint.intersection(corridor))
            physical_blocker, footprint_reserved = self._physical_move_blocker(
                runtime,
                idle,
                target_cell,
                layer,
                reserved_footprint,
            )
            if footprint_reserved:
                continue
            if physical_blocker is not None:
                if (
                    physical_blocker.get("type") == "shuttle"
                    and not physical_blocker.get("currentTaskId")
                    and not physical_blocker.get("carrying")
                    and str(physical_blocker.get("id")) not in next_visited
                    and depth < 4
                ):
                    if self._yield_idle_shuttle(
                        runtime,
                        physical_blocker,
                        idle,
                        occupied_cells,
                        reserved_targets,
                        reserved_footprint,
                        depth=depth + 1,
                        visited=next_visited,
                        corridor=corridor,
                    ):
                        # One vehicle in the blocking chain moved this tick. Let
                        # the scheduler re-evaluate the direct blocker next tick.
                        return True
                    idle_chain_pending = True
                continue
            distance = abs(next_row - request_row) + abs(next_col - request_col)
            candidates.append((
                current_overlap - candidate_overlap,
                int(changes_footprint),
                int(target_cell != idle.get("lastCellId")),
                distance,
                target_cell,
                next_row,
                next_col,
            ))
        if not candidates:
            return False
        if idle_chain_pending and not any(candidate[1] for candidate in candidates):
            # Give the recursive push enough ticks to accrue physical motion
            # credit instead of escaping through a no-op logical bridge.
            return False

        (
            _overlap_reduction,
            _changes_footprint,
            _not_reverse,
            _distance,
            target_cell,
            target_row,
            target_col,
        ) = max(
            candidates,
            # Releasing physical space outranks a temporary increase in route
            # overlap.  Otherwise an unchanged-footprint bridge can win and
            # bounce forever between two logical coordinates.
            key=lambda item: (item[1], item[2], item[0], item[3], item[4]),
        )
        if not self._physical_movement_ready(runtime, idle, target_cell, layer):
            return False
        motion_from = self._motion_position(runtime, idle)
        self._consume_motion_credit(idle)
        previous = idle["currentCellId"]
        occupied_cells.discard((previous, layer))
        idle.update({
            "lastCellId": previous,
            "currentCellId": target_cell,
            "currentNode": target_cell,
            "currentFloor": "1F",
            "currentRow": target_row,
            "currentCol": target_col,
            "path": [target_cell],
            "pathLayers": [layer],
            "pathStages": ["idle_yield"],
            "targetCellId": target_cell,
            "targetLayer": layer,
            "status": "idle",
            "phaseLabel": "待命 / 主动让行",
            "waitTicks": 0,
            "returningToStandby": False,
        })
        occupied_cells.add((target_cell, layer))
        reserved_targets.add((target_cell, layer))
        reserved_footprint.update(self._robot_collision_footprint(runtime, idle))
        self._record_committed_motion(
            runtime,
            idle,
            motion_from,
            MovementType.BACKOFF,
            "_yield_idle_shuttle",
        )
        self._record_event(
            runtime,
            int(runtime.state.get("tick") or 0),
            "robot_position",
            {
                "robotId": idle["id"],
                "cellId": target_cell,
                "floor": "1F",
                "row": target_row,
                "col": target_col,
                "layer": layer,
                "status": "yielding",
            },
        )
        self._observe_deadlock_mitigation(runtime, idle, "YIELD_IDLE", "shuttle_idle_blocker")
        return True

    def _clear_shuttle_head_on(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        blocker: dict[str, Any] | None,
        occupied_cells: set[tuple[str, float]],
        reserved_targets: set[tuple[str, float]],
        reserved_footprint: set[tuple[int, int, float]],
    ) -> bool:
        """Break a Shuttle stand-off by retreating on a verified route edge.

        Never synthesize a side coordinate here.  The old ``row +/- 1`` rule
        assumed every conveyor row was a passing bay and could move a vehicle
        onto a non-existent topology cell.
        """
        if blocker is None or blocker.get("type") != "shuttle" or blocker.get("status") in {"failed", "exception"}:
            return False
        # A planned spur evacuation must reach its exit without ordinary
        # one-cell recovery changing its route. A parked body inside the
        # spur is handled by the coordinator, including a two-vehicle retreat.
        if any(
            item.get("palletCollectEvacuating")
            or (
                not item.get("currentTaskId")
                and self._robot_collision_footprint(runtime, item).intersection(PALLET_COLLECT_SPUR)
                and self._has_pallet_collect_spur(runtime)
            )
            for item in (robot, blocker)
        ):
            return False
        traffic = self._handoff_traffic_state(runtime)
        owner_id = traffic.get("ownerRobotId") if traffic else None
        # The Shuttle leaving the arm corridor must never be trapped by a
        # branch holding point. Ask the loaded waiter to retrace its verified
        # staging approach; movement itself remains collision checked.
        if robot.get("id") == owner_id and blocker.get("id") != owner_id:
            if self._retreat_handoff_staging(runtime, blocker):
                return True
        # A taskless Shuttle returning to standby is operationally idle too.
        # Treating only status == idle as movable left those vehicles parked on
        # lift mouths after a completed delivery, where they could block the
        # next serialized handoff owner indefinitely.
        blocker_is_idle = not blocker.get("currentTaskId") and not blocker.get("carrying")
        blocker_path = blocker.get("path") or []
        if not blocker_is_idle:
            if len(blocker_path) <= 1:
                return False
            blocker_layers = blocker.get("pathLayers") or [blocker.get("currentLayer", 1)] * len(blocker_path)
            blocker_next_layer = blocker_layers[1] if len(blocker_layers) > 1 else blocker.get("currentLayer", 1)
            blocker_next_footprint = self._robot_collision_footprint(
                runtime,
                blocker,
                blocker_path[1],
                blocker_next_layer,
            )
            if not blocker_next_footprint.intersection(self._robot_collision_footprint(runtime, robot)):
                return False

        if blocker_is_idle and self._yield_idle_shuttle(
            runtime,
            blocker,
            robot,
            occupied_cells,
            reserved_targets,
            reserved_footprint,
        ):
            return True

        robot_task = find_task(runtime.state.get("tasks", []), robot.get("currentTaskId"))
        blocker_task = find_task(runtime.state.get("tasks", []), blocker.get("currentTaskId"))
        loaded_feeder = bool(
            robot_task
            and robot_task.get("phase") == "to_handoff_staging"
            and robot.get("carrying")
            and str((runtime.arm_handoff or {}).get("handoffCellId") or "") == HANDOFF_FIXED_CELL_ID
        )
        unladen_pickup = bool(
            blocker_task
            and blocker_task.get("phase") == "pickup_from_shuttle"
            and not blocker.get("carrying")
        )
        if loaded_feeder and unladen_pickup:
            # The loaded conveyor feeder has fixed one-way priority.  When an
            # empty pickup approaches head-on, first try to give that empty car
            # a genuinely different route around the feeder's remaining path.
            # A simple one-cell retreat otherwise sends it straight back into
            # the same conflict after its yield timer expires.
            feeder_path = list(robot.get("path") or [robot.get("currentCellId")])
            feeder_layers = list(robot.get("pathLayers") or [robot.get("currentLayer", 1)] * len(feeder_path))
            feeder_states: set[tuple[int, int, int]] = set()
            for index, feeder_cell in enumerate(feeder_path[1:24], start=1):
                try:
                    _floor, feeder_row, feeder_col = parse_cell_id(str(feeder_cell))
                except ValueError:
                    continue
                feeder_layer = int(
                    feeder_layers[index]
                    if index < len(feeder_layers)
                    else robot.get("currentLayer", 1)
                )
                feeder_states.add((feeder_row, feeder_col, feeder_layer))
            target_cell = str(blocker.get("targetCellId") or "")
            target_layer = int(blocker.get("targetLayer") or blocker.get("currentLayer", 1))
            old_path = [str(value) for value in (blocker.get("path") or [])]
            old_layers = [
                float(value)
                for value in (
                    blocker.get("pathLayers")
                    or [blocker.get("currentLayer", 1)] * len(old_path)
                )
            ]
            replanned = bool(
                target_cell
                and self._robot_path_to(
                    runtime,
                    blocker,
                    target_cell,
                    target_layer,
                    extra_blocked=feeder_states,
                    task_id=(blocker_task or {}).get("id"),
                    evidence_kind="replan",
                )
            )
            new_path = [str(value) for value in (blocker.get("path") or [])]
            new_layers = [
                float(value)
                for value in (
                    blocker.get("pathLayers")
                    or [blocker.get("currentLayer", 1)] * len(new_path)
                )
            ]
            if replanned and (new_path != old_path or new_layers != old_layers):
                blocker["waitTicks"] = 0
                blocker["yieldTicks"] = 0
                blocker["status"] = "moving"
                blocker["phaseLabel"] = "空车绕行 / 载货队列优先"
                return True

        def yield_rank(item: dict[str, Any]) -> tuple[int, int, str]:
            task = find_task(runtime.state.get("tasks", []), item.get("currentTaskId"))
            is_owner = item.get("id") == owner_id
            phase = str((task or {}).get("phase") or "")
            is_outbound = bool(
                item.get("returningToStandby")
                or task and task.get("taskKind") in {EMPTY_PALLET_TASK_KIND, PALLET_RETURN_TASK_KIND}
            )
            is_inbound = bool(task and task.get("armHandoff"))
            is_unladen_pickup = bool(phase == "pickup_from_shuttle" and not item.get("carrying"))
            # Prefer moving an idle/non-owner/inbound vehicle.  The vehicle
            # already leaving the handoff must keep right of way.
            score = (
                4 if is_unladen_pickup
                else 3 if item.get("status") == "idle"
                else 2 if is_inbound
                else 1
            )
            # The serialized handoff owner has absolute right of way.  A
            # taskless vehicle returning to standby is outbound too, but must
            # retreat when it meets the owner on a single-lane segment.
            protected = 2 if is_owner else 1 if is_outbound else 0
            return (score, -protected, str(item.get("id")))

        candidates = sorted((robot, blocker), key=yield_rank, reverse=True)
        non_owner_candidates = [
            item for item in candidates if item.get("id") != owner_id
        ]
        taskless_candidates = [
            item for item in candidates
            if item.get("id") != owner_id
            and not item.get("currentTaskId") and not item.get("carrying")
        ]
        if taskless_candidates:
            # Standby is best-effort. It must not repeatedly interrupt a
            # productive task to reserve a route back to the home position.
            yield_candidates = taskless_candidates
        elif owner_id and non_owner_candidates:
            # The vehicle leaving the serialized arm owns the return corridor,
            # even when the opposing vehicle also carries a load.
            yield_candidates = non_owner_candidates
        else:
            non_loaded_candidates = [
                item for item in candidates
                if not self._is_loaded_handoff_queue_feeder(runtime, item)
            ]
            # Without a handoff owner, a loaded inbound convoy outranks an
            # unladen pickup/standby return.
            yield_candidates = non_loaded_candidates or candidates
        for yielding in yield_candidates:
            priority = blocker if yielding is robot else robot
            if self._yield_shuttle_to_clearance(
                runtime,
                yielding,
                priority,
                occupied_cells,
                reserved_targets,
                reserved_footprint,
            ):
                return True
        for yielding in yield_candidates:
            if self._retreat_unladen_shuttle_two_cells(
                runtime,
                yielding,
                occupied_cells,
                reserved_targets,
                reserved_footprint,
            ):
                yielding["yieldTicks"] = 2
                yielding["waitTicks"] = 0
                yielding["status"] = "blocked"
                yielding["phaseLabel"] = "高层巷道退入侧位让行"
                return True
        for yielding in yield_candidates:
            if self._back_off_shuttle(
                runtime,
                yielding,
                occupied_cells,
                reserved_targets,
                reserved_footprint,
            ):
                if yielding.get("returningToStandby") and not yielding.get("currentTaskId"):
                    # A taskless standby return is best-effort.  After it has
                    # retreated for the traffic owner, do not immediately send
                    # it back into the same contested corridor; park it at the
                    # verified cell and let a later task replan from there.
                    current_cell = yielding["currentCellId"]
                    current_layer = yielding.get("currentLayer", 1)
                    yielding.update({
                        "returningToStandby": False,
                        "yieldTicks": 0,
                        "waitTicks": 0,
                        "status": "idle",
                        "phaseLabel": "待命 / 让行驻车",
                        "path": [current_cell],
                        "pathLayers": [current_layer],
                        "pathStages": ["idle_yield"],
                        "targetCellId": current_cell,
                        "targetLayer": current_layer,
                    })
                    return True
                yielding["yieldTicks"] = 3
                yielding["waitTicks"] = 0
                yielding["status"] = "blocked"
                yielding["phaseLabel"] = "沿合法路径退让"
                return True
        return False

    def _retreat_unladen_shuttle_two_cells(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        occupied_cells: set[tuple[str, float]],
        reserved_targets: set[tuple[str, float]],
        reserved_footprint: set[tuple[int, int, float]],
    ) -> bool:
        """Retreat an empty high-level pickup far enough to clear a 2x2 body."""

        task = find_task(runtime.state.get("tasks", []), robot.get("currentTaskId"))
        if (
            task is None
            or task.get("phase") != "pickup_from_shuttle"
            or robot.get("carrying")
        ):
            return False
        last_cell = str(robot.get("lastCellId") or "")
        if not last_cell or last_cell == robot.get("currentCellId"):
            return False
        try:
            _floor, current_row, current_col = parse_cell_id(str(robot["currentCellId"]))
            _last_floor, last_row, last_col = parse_cell_id(last_cell)
        except ValueError:
            return False
        d_row = last_row - current_row
        d_col = last_col - current_col
        if abs(d_row) + abs(d_col) != 1:
            return False
        second_row = last_row + d_row
        second_col = last_col + d_col
        layer = int(robot.get("currentLayer", 1))
        if not shuttle_state_is_walkable(runtime.topology, second_row, second_col, layer):
            return False
        second_cell = cell_id(second_row, second_col, "1F")
        second_entry = (second_cell, float(layer))
        if second_entry in occupied_cells or second_entry in reserved_targets:
            return False
        second_blocker, second_reserved = self._physical_move_blocker(
            runtime,
            robot,
            second_cell,
            layer,
            reserved_footprint,
        )
        if second_blocker is not None or second_reserved:
            return False

        resume_cell = str(robot.get("targetCellId") or (robot.get("path") or [""])[-1])
        resume_layer = int(robot.get("targetLayer") or (robot.get("pathLayers") or [layer])[-1])
        resume_phase = str(task.get("phase") or "pickup_from_shuttle")
        if not self._back_off_shuttle(
            runtime,
            robot,
            occupied_cells,
            reserved_targets,
            reserved_footprint,
            allow_same_footprint_bridge=True,
        ):
            return False
        task["yieldResumePhase"] = resume_phase
        task["yieldResumeCellId"] = resume_cell
        task["yieldResumeLayer"] = resume_layer
        task["phase"] = "yielding_for_handoff_queue"
        task["phaseLabel"] = "高层巷道退入侧位让行"
        robot.update({
            "path": [last_cell, second_cell],
            "pathLayers": [layer, layer],
            "pathStages": [task["phase"], task["phase"]],
            "targetCellId": second_cell,
            "targetLayer": layer,
            "phaseLabel": task["phaseLabel"],
        })
        return True

    def _clear_head_on(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        blocker: dict[str, Any] | None,
        occupied_cells: set[tuple[str, float]],
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

    def _yield_one_cell(self, runtime: RunRuntime, robot: dict[str, Any], occupied_cells: set[tuple[str, float]]) -> bool:
        """后退到上一格，并从那里重新规划到原目标；退不了或规划失败返回 False。"""
        last = robot.get("lastCellId")
        cur_layer_f = float(robot.get("currentLayer", 1))
        last_entry = (last, cur_layer_f)
        if not last or last == robot["currentCellId"] or last_entry in occupied_cells:
            return False
        # 不能退进货位/输送带格（上一格是刚取完货的库位时会出现）。
        if parse_cell_id(last) in build_occupied_lookup(runtime.topology):
            return False
        target = robot.get("targetCellId")
        if not target:
            return False
        # occupied_cells 是 (cell-id, layer) tuple 集合，2D pathfinding 只取 cell-id 部分
        cur_entry = (robot["currentCellId"], cur_layer_f)
        occupied_entries = normalize_occupied_entries(occupied_cells)
        blocked = {parse_cell_id(cell) for cell, _layer in occupied_entries if (cell, _layer) != cur_entry}
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
        if not self._physical_movement_ready(
            runtime,
            robot,
            last,
            float(robot.get("currentLayer", 1)),
        ):
            return False
        motion_from = self._motion_position(runtime, robot)
        self._consume_motion_credit(robot)
        occupied_cells.discard((robot["currentCellId"], cur_layer_f))
        robot["lastCellId"] = robot["currentCellId"]
        robot["currentCellId"] = last
        robot["currentNode"] = last
        floor, row, col = parse_cell_id(last)
        robot["currentFloor"] = floor
        robot["currentRow"] = row
        robot["currentCol"] = col
        robot["path"] = [cell["id"] for cell in path_cells]
        robot["pathLayers"] = [1] * len(robot["path"])
        occupied_cells.add((last, cur_layer_f))
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
        occupied_cells: set[tuple[str, float]],
        reserved_targets: set[tuple[str, float]],
        reserved_footprint: set[tuple[int, int, float]] | None = None,
    ) -> None:
        """已交付但暂时没有任务的 CTU 返回起始待命位，避免长期占住唯一出库口。"""
        if reserved_footprint is None:
            reserved_footprint = set()
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
        path_layers = robot.get("pathLayers") or [1] * len(path)
        next_layer = path_layers[1] if len(path_layers) > 1 else 1
        cur_layer_f = float(robot.get("currentLayer", 1))
        next_layer_f = float(next_layer)
        current_entry = (current_cell_id, cur_layer_f)
        next_entry = (next_cell_id, next_layer_f)
        if (
            robot.get("type") == "shuttle"
            and self._loaded_handoff_queue_feeders_active(runtime)
        ):
            feeder_footprint = self._handoff_queue_feeder_footprint(runtime)
            current_overlap = bool(
                self._robot_collision_footprint(runtime, robot).intersection(feeder_footprint)
            )
            next_overlap = bool(
                self._robot_collision_footprint(
                    runtime,
                    robot,
                    next_cell_id,
                    next_layer_f,
                ).intersection(feeder_footprint)
            )
            if not current_overlap and next_overlap:
                target_cell = str(robot.get("targetCellId") or "")
                target_layer = int(robot.get("targetLayer") or robot.get("currentLayer", 1))
                if target_cell and self._robot_path_to(
                    runtime,
                    robot,
                    target_cell,
                    target_layer,
                    extra_blocked=self._handoff_queue_feeder_blocked_states(runtime),
                    evidence_kind="replan",
                ):
                    robot["status"] = "returning"
                    robot["waitTicks"] = 0
                    robot["phaseLabel"] = "返回待命位 / 绕行输送线入队口"
                else:
                    robot["status"] = "waiting"
                    robot["phaseLabel"] = "返回待命位 / 等待载货车队通过"
                return
        if not self._physical_movement_ready(runtime, robot, next_cell_id, next_layer_f):
            robot["phaseLabel"] = "Returning to standby / physical travel"
            return
        physical_blocker: dict[str, Any] | None = None
        footprint_reserved = False
        if next_entry != current_entry:
            physical_blocker, footprint_reserved = self._physical_move_blocker(
                runtime,
                robot,
                next_cell_id,
                next_layer_f,
                reserved_footprint,
            )
        if next_entry != current_entry and (
            next_entry in occupied_cells
            or next_entry in reserved_targets
            or physical_blocker is not None
            or footprint_reserved
        ):
            robot["status"] = "blocked"
            robot["waitTicks"] += 1
            robot["phaseLabel"] = "返回待命位 / 等待让行"
            observed_blocker = physical_blocker or next(
                (
                    item
                    for item in runtime.state["robots"]
                    if item.get("id") != robot.get("id")
                    and (item["currentCellId"], float(item.get("currentLayer", 1))) == next_entry
                ),
                None,
            )
            block_resource = (
                ResourceType.ROBOT
                if observed_blocker is not None
                else ResourceType.RESERVATION
                if next_entry in reserved_targets or footprint_reserved
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
                    if next_entry in reserved_targets or footprint_reserved
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
                elif observed_blocker is not None and self._clear_shuttle_head_on(
                    runtime,
                    robot,
                    observed_blocker,
                    occupied_cells,
                    reserved_targets,
                    reserved_footprint,
                ):
                    robot["waitTicks"] = 0
            return

        motion_from = self._motion_position(runtime, robot)
        self._consume_motion_credit(robot)
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
        robot["status"] = "returning"
        robot["phaseLabel"] = "返回待命位"
        occupied_cells.discard(current_entry)
        occupied_cells.add(next_entry)
        reserved_targets.add(next_entry)
        reserved_footprint.update(self._robot_collision_footprint(runtime, robot))
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
        if len(robot["path"]) <= 1 and not robot.get("palletCollectEvacuating"):
            robot["returningToStandby"] = False
            robot["status"] = "idle"
            robot["phaseLabel"] = "待命"

    def _yield_ctu(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        occupied_cells: set[tuple[str, float]],
        reserved_targets: set[tuple[str, float]],
    ) -> bool:
        """无可用绕行路线时退到相邻空闲格，给对向车辆释放当前通道。"""
        try:
            floor, row, col = parse_cell_id(robot["currentCellId"])
            target_floor, target_row, target_col = parse_cell_id(robot.get("targetCellId") or robot["currentCellId"])
        except (TypeError, ValueError):
            return False

        walkable = build_walkable_lookup(runtime.topology)
        fixed_occupied = build_occupied_lookup(runtime.topology)
        # unavailable 用于 cell-id 级别的 2D 检查（CTU 在 L1）
        cur_layer_f = float(robot.get("currentLayer", 1))
        occupied_cells_2d = {cell for cell, _layer in normalize_occupied_entries(occupied_cells)}
        reserved_targets_2d = {cell for cell, _layer in reserved_targets}
        unavailable = occupied_cells_2d | reserved_targets_2d
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
        if not self._physical_movement_ready(runtime, robot, yield_cell_id, 1.0):
            return False
        motion_from = self._motion_position(runtime, robot)
        self._consume_motion_credit(robot)
        occupied_cells.discard((previous_cell, cur_layer_f))
        occupied_cells.add((yield_cell_id, 1.0))
        reserved_targets.add((yield_cell_id, 1.0))
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
                if line.get("orderLineId") not in (None, ""):
                    carried["orderLineId"] = str(line["orderLineId"])
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
        occupied_cells: set[tuple[str, float]] | None = None,
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

    def _send_empty_pallet_to_stack(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
        target: dict[str, Any],
    ) -> bool:
        """机械臂取走货物后托盘变空：四向车带着空托盘直接送到 (76,35) 堆叠。

        空托盘只产生于交接处（货物被机械臂搬上输送线之后），不会回库位，
        因此不新建「去货位取托盘」的路段，而是把托盘当作车载物直接开往目标格。
        """
        tick = int(runtime.state["tick"])
        if not self._robot_path_to(
            runtime,
            robot,
            EMPTY_PALLET_RETURN_CELL_ID,
            1,
            extra_blocked=self._handoff_return_route_blocked_states(runtime),
        ):
            self._record_event(
                runtime,
                tick,
                "empty_pallet_unreachable",
                {"taskId": task["id"], "robotId": robot["id"], "cellId": EMPTY_PALLET_RETURN_CELL_ID},
            )
            return False
        return_task = self._build_task(
            runtime.topology,
            f"task-empty-pallet-{task['id']}-{tick}",
            "shuttle",
            "cross_zone",
            [{"cellId": str(target.get("handoffCellId") or ""), "layer": 1}],
            EMPTY_PALLET_RETURN_CELL_ID,
            False,
            3,
            created_tick=tick,
        )
        return_task["taskKind"] = EMPTY_PALLET_TASK_KIND
        return_task["handoffTraffic"] = True
        return_task["status"] = "in_progress"
        return_task["startedTick"] = tick
        return_task["phase"] = "delivery_empty_pallet"
        return_task["phaseLabel"] = "回收空托盘"
        return_task["etaTicks"] = max(len(robot.get("path") or []) - 1, 1)
        return_task["assignedRobotId"] = robot["id"]
        return_task["palletSourceCellId"] = str(task.get("palletSourceCellId") or "")
        return_task["palletSourceLayer"] = int(task.get("palletSourceLayer") or 1)
        runtime.state["tasks"].append(return_task)
        traffic = self._handoff_traffic_state(runtime)
        if traffic is not None and traffic.get("ownerRobotId") == robot.get("id"):
            traffic["ownerTaskId"] = return_task["id"]

        # 原订单任务转由输送线独立推进，四向车腾出来专门运空托盘。
        task["assignedRobotId"] = None
        robot["currentTaskId"] = return_task["id"]
        robot["carrying"] = [{
            "cellId": return_task["palletSourceCellId"],
            "layer": return_task["palletSourceLayer"],
            "kind": "empty_pallet",
        }]
        robot["load"] = 1
        robot["handlingAction"] = None
        robot["waitTicks"] = 0
        robot["returningToStandby"] = False
        robot["status"] = "moving"
        robot["phaseLabel"] = "回收空托盘"
        self._record_event(
            runtime,
            tick,
            "empty_pallet_carry",
            {
                "taskId": return_task["id"],
                "robotId": robot["id"],
                "cellId": return_task["palletSourceCellId"],
                "layer": return_task["palletSourceLayer"],
            },
        )
        return True

    def _deliver_empty_pallet(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any], occupied_cells: set[tuple[str, float]] | None = None) -> None:
        """Add the returned tray to the waiting count at (76,35)."""
        tick = runtime.state["tick"]
        stack = runtime.state.setdefault("emptyPalletStack", {})
        count = int(stack.get("count") or 0)
        source_cell = str(task.get("palletSourceCellId") or "")
        source_layer = int(task.get("palletSourceLayer") or 1)
        # 存来源信息：后续 reposition 任务需要知道搬的是哪一层。
        stack.setdefault("stacks", []).append({
            "sourceCellId": source_cell,
            "sourceLayer": source_layer,
        })
        stack["count"] = count + 1
        if count >= EMPTY_PALLET_STACK_CAPACITY:
            stack["overflow"] = int(stack.get("overflow") or 0) + 1
        task["palletReturnedTick"] = tick
        self._record_event(
            runtime,
            tick,
            "empty_pallet_returned",
            {
                "taskId": task["id"],
                "cellId": source_cell,
                "layer": source_layer,
                "stackCount": int(stack.get("count") or 0),
                "overflow": int(stack.get("overflow") or 0),
            },
        )
        # 满层触发 reposition：搬一个托盘到 (74,47) 新机械臂入主线点。
        if not runtime.state.get("palletTransport") and int(stack.get("count") or 0) >= EMPTY_PALLET_STACK_CAPACITY:
            self._spawn_pallet_reposition(runtime)
        robot["carrying"] = []
        robot["load"] = 0
        self._complete_task(runtime, robot, task, occupied_cells)

    def _spawn_pallet_reposition(self, runtime: RunRuntime) -> None:
        """Dispatch one Shuttle to pick up the oldest available pallet stack."""
        stack = runtime.state.get("emptyPalletStack") or {}
        stacks = stack.get("stacks") or []
        if not stacks:
            return
        # 去重：已有同 source/dest 的在途任务则不再生成。
        for t in runtime.state.get("tasks", []):
            if (
                t.get("taskKind") == EMPTY_PALLET_COLLECT_TASK_KIND
                and t.get("status") in {"pending", "in_progress"}
            ):
                return
        pickup_cell = EMPTY_PALLET_RETURN_CELL_ID
        robot = next(
            (
                r for r in runtime.state.get("robots", [])
                if r.get("type") == "shuttle" and r.get("status") == "idle" and not r.get("currentTaskId")
                and self._robot_path_to(runtime, r, pickup_cell, 1)
            ),
            None,
        )
        if robot is None:
            return
        tick = int(runtime.state["tick"])
        tray = stacks[0]
        # 规划 shuttle 路径：从当前位置 → (76,35) → (74,47)。
        # 先让 robot 走到 (76,35) pickup，再到 (74,47) dropoff。
        repo = runtime.topology
        reposition = self._build_task(
            repo,
            f"task-pallet-reposition-{tick}-{tray.get('sourceCellId','').replace('cell-','').replace('-','_')}",
            "shuttle",
            "cross_zone",
            [{"cellId": pickup_cell, "layer": 1, "sourceCellId": tray.get("sourceCellId"), "sourceLayer": tray.get("sourceLayer")}],
            EMPTY_PALLET_COLLECT_CELL_ID,
            False,
            3,
            created_tick=tick,
        )
        reposition["taskKind"] = EMPTY_PALLET_COLLECT_TASK_KIND
        reposition["status"] = "in_progress"
        reposition["startedTick"] = tick
        reposition["phase"] = "pallet_pickup_from_stack"
        reposition["phaseLabel"] = "搬空托盘到主线"
        reposition["assignedRobotId"] = robot["id"]
        reposition["palletSourceCellId"] = tray.get("sourceCellId")
        reposition["palletSourceLayer"] = tray.get("sourceLayer")
        if runtime.state.get("palletTransport"):
            reposition["palletBatch"] = []
            reposition["palletCount"] = min(len(stacks), EMPTY_PALLET_STACK_CAPACITY)
        else:
            stacks.pop(0)  # Legacy tasks reserve one tray at dispatch.
        reposition["etaTicks"] = max(len(robot.get("path") or []) + self._shuttle_path_len(runtime, EMPTY_PALLET_COLLECT_CELL_ID) - 1, 1)
        robot["currentTaskId"] = reposition["id"]
        robot["status"] = "moving"
        robot["phaseLabel"] = reposition["phaseLabel"]
        runtime.state["tasks"].append(reposition)
        self._record_event(
            runtime, tick, "pallet_reposition_started",
            {"taskId": reposition["id"], "robotId": robot["id"], "traySource": tray,
             "palletCount": reposition.get("palletCount", 1)},
        )

    def _shuttle_path_len(self, runtime: RunRuntime, cell_id: str) -> int:
        """reposition eta 兜底：从 (76,35) 到 (74,47) 地面走约 3 格。"""
        return 3

    # Kept for existing snapshots and layouts without the new drop-zone connection.
    PALLET_PORT_ROWS = [102, 106, 110, 114, 118]
    PALLET_CONVEYOR_SPEED_TICKS = 3  # 每段输送（2 格）走 3 tick，与现有 delivery 节拍接近
    PALLET_DOCK_MAX_LAYERS = 3  # 帽口处最多堆 3 层空托盘

    def _start_pallet_arm_handoff(
        self, runtime: RunRuntime, task: dict[str, Any], robot: dict[str, Any]
    ) -> None:
        arm_state = runtime.state.setdefault("palletArm", {
            "armId": PALLET_ARM_ID, "status": "idle", "pendingQueue": [],
        })
        job = {"taskId": task["id"], "robotId": robot["id"], "palletCount": int(task.get("palletCount") or 1)}
        if arm_state.get("status") != "idle":
            arm_state.setdefault("pendingQueue", []).append(job)
            self._record_event(
                runtime, int(runtime.state["tick"]), "pallet_arm_queue",
                {"taskId": task["id"], "queueSize": len(arm_state.get("pendingQueue", []))},
            )
            return
        self._begin_pallet_arm_pick(runtime, arm_state, job)

    def _begin_pallet_arm_pick(self, runtime: RunRuntime, arm: dict, job: dict) -> None:
        tick = int(runtime.state["tick"])
        arm.update(
            status="busy", stage="pick", remainingTicks=PALLET_ARM_PICK_TICKS,
            stageStartedTick=tick, stageDurationTicks=PALLET_ARM_PICK_TICKS,
            taskId=job["taskId"], robotId=job.get("robotId"),
            cargo={"kind": "empty_pallet", "qty": int(job.get("palletCount") or 1), "acceptedTick": tick},
        )
        self._record_event(
            runtime, tick, "pallet_arm_pick_start",
            {"taskId": job["taskId"], "armId": arm.get("armId"), "palletCount": arm["cargo"]["qty"]},
        )

    def _advance_pallet_arm(self, runtime: RunRuntime) -> None:
        arm_state = runtime.state.get("palletArm")
        if not arm_state or arm_state.get("status") != "busy":
            return
        tick = int(runtime.state["tick"])
        if arm_state.get("stageStartedTick") == tick:
            return
        remaining = int(arm_state.get("remainingTicks", 0)) - 1
        arm_state["remainingTicks"] = max(0, remaining)
        if remaining > 0:
            return
        stage = arm_state.get("stage")
        if stage == "pick":
            robot = next((r for r in runtime.state["robots"] if r["id"] == arm_state.get("robotId")), None)
            task = find_task(runtime.state["tasks"], arm_state.get("taskId"))
            if robot and task and task.get("phase") == "waiting_pallet_collect_arm":
                robot.update(carrying=[], load=0)
                if "palletBatch" not in task:
                    stack = runtime.state["emptyPalletStack"]
                    stack["count"] = max(0, int(stack.get("count") or 0) - 1)
                if self._has_pallet_collect_spur(runtime):
                    task.update(phase="clearing_pallet_collect", phaseLabel="空托盘已交接 / 驶离支线")
                    task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
                    self._advance_robot_phase(runtime, robot, task)
                else:
                    self._complete_task(runtime, robot, task)
            arm_state.update(
                stage="place", remainingTicks=PALLET_ARM_PLACE_TICKS,
                stageStartedTick=tick, stageDurationTicks=PALLET_ARM_PLACE_TICKS,
            )
            self._record_event(
                runtime, tick, "pallet_arm_place_start",
                {"taskId": arm_state.get("taskId"), "armId": arm_state.get("armId")},
            )
            return
        if stage == "place":
            if not self._spawn_pallet_conveyor_item(runtime, arm_state):
                return
            # 重置 arm 状态
            arm_state["status"] = "idle"
            arm_state["stage"] = None
            arm_state["remainingTicks"] = 0
            arm_state["taskId"] = None
            arm_state["robotId"] = None
            arm_state["cargo"] = None
            # 处理排队
            pending = arm_state.get("pendingQueue") or []
            if pending:
                next_task = pending.pop(0)
                self._record_event(
                    runtime, tick, "pallet_arm_dequeue",
                    {"taskId": next_task.get("taskId")},
                )
                self._begin_pallet_arm_pick(runtime, arm_state, next_task)

    def _pallet_belt_has_room(self, runtime: RunRuntime, block_id: str) -> bool:
        capacity = int(runtime.conveyor_graph.get("nodes", {}).get(block_id, {}).get("capacity") or 1)
        cartons = sum(
            (task.get("conveyorTransport") or {}).get("currentBlockId") == block_id
            for task in runtime.state["tasks"]
            if task.get("status") not in TERMINAL_TASK_STATUSES and not task.get("pendingTransport")
        )
        pallets = sum(
            item.get("currentBlockId") == block_id and item.get("status") in PALLETS_ON_CONVEYOR
            for item in runtime.state["palletConveyorItems"]
        )
        transport = runtime.state.get("palletTransport") or {}
        if transport.get("portCellId") and block_id == transport["route"][-1]["blockId"]:
            port_cell_id = transport["portCellId"]
            if any(box.get("cellId") == port_cell_id for box in runtime.state.get("armBeltBoxes", [])):
                return False
            if any(
                arm.get("stage") == "pick" and arm.get("portCellId") == port_cell_id
                for arm in runtime.state.get("pickingArms", [])
            ):
                return False
        return cartons + pallets < capacity

    def _spawn_pallet_conveyor_item(self, runtime: RunRuntime, arm_state: dict[str, Any]) -> bool:
        tick = int(runtime.state["tick"])
        pallet_count = int((arm_state.get("cargo") or {}).get("qty") or 1)
        transport = runtime.state.get("palletTransport")
        if transport:
            head = transport["route"][0]
            if not self._pallet_belt_has_room(runtime, head["blockId"]):
                return False
            dropoff = transport["route"][-1]
            item = {
                "id": f"pallet-cv-{tick}-{arm_state['taskId']}",
                "taskId": arm_state["taskId"], "kind": "empty_pallet", "palletCount": pallet_count,
                "currentRow": head["row"], "currentCol": head["col"], "deck": head["deck"],
                "currentBlockId": head["blockId"], "routeIndex": 0,
                "targetRow": dropoff["row"], "targetCol": dropoff["col"],
                "speedTicks": self.PALLET_CONVEYOR_SPEED_TICKS, "tickCounter": 0,
                "acceptedTick": tick, "status": "moving",
            }
            runtime.state["palletConveyorItems"].append(item)
            self._record_event(runtime, tick, "pallet_conveyor_spawned", {
                "palletId": item["id"], "taskId": item["taskId"], "palletCount": pallet_count,
                "cellId": transport["dropoffCellId"], "dropZoneId": transport["dropZoneId"],
                "deck": item["deck"], "blockId": item["currentBlockId"],
            })
            return True
        docks = runtime.state.setdefault("palletDocks", {})
        # FIFO 轮询分配帽口：按 dock count 最小的优先（最简单是 round-robin）
        assigned_row = self.PALLET_PORT_ROWS[0]
        for r in self.PALLET_PORT_ROWS:
            dock = docks.get(str(r)) or {"count": 0}
            if int(dock.get("count") or 0) < self.PALLET_DOCK_MAX_LAYERS:
                assigned_row = r
                break
        item = {
            "id": f"pallet-cv-{tick}-{arm_state.get('taskId','').replace('task-','')}",
            "kind": "empty_pallet",
            "palletCount": pallet_count,
            "currentRow": 84,
            "currentCol": 58,
            "deck": "upper",
            "targetRow": assigned_row,
            "targetCol": 62,
            "speedTicks": self.PALLET_CONVEYOR_SPEED_TICKS,
            "tickCounter": 0,
            "acceptedTick": tick,
            "status": "moving",
        }
        runtime.state.setdefault("palletConveyorItems", []).append(item)
        self._record_event(
            runtime, tick, "pallet_conveyor_spawned",
            {"palletId": item["id"], "targetRow": assigned_row, "targetCol": 62},
        )
        return True

    def _advance_pallet_conveyor(self, runtime: RunRuntime) -> None:
        """Advance each pallet stack by real conveyor blocks."""
        items = runtime.state.get("palletConveyorItems") or []
        tick = int(runtime.state["tick"])
        transport = runtime.state.get("palletTransport")
        for item in items:
            if item.get("status") != "moving":
                continue
            item["tickCounter"] = int(item.get("tickCounter", 0)) + 1
            if int(item.get("tickCounter", 0)) < int(item.get("speedTicks", 0)):
                continue
            item["tickCounter"] = 0
            if transport and "routeIndex" in item:
                route = transport["route"]
                index = int(item["routeIndex"])
                if index == len(route) - 1:
                    if transport.get("deliveryArmId"):
                        item.update(status="waiting_arm", arrivedTick=tick, portCellId=transport["portCellId"])
                        self._record_event(runtime, tick, "pallet_outlet_arrived", {
                            "palletId": item["id"], "palletCount": item["palletCount"],
                            "portCellId": item["portCellId"], "armId": transport["deliveryArmId"],
                        })
                        continue
                    quantity = int(item["palletCount"])
                    zone = runtime.state.get("palletDropZone") or {
                        "zoneId": transport["dropZoneId"], "row": item["currentRow"],
                        "col": item["currentCol"], "deck": item["deck"], "count": 0,
                    }
                    zone["count"] += quantity
                    runtime.state["palletDropZone"] = zone
                    item["status"] = "delivered"
                    self._record_event(runtime, tick, "pallet_drop_zone_arrived", {
                        "taskId": item["taskId"], "palletCount": quantity,
                        "zoneId": zone["zoneId"], "cellId": cell_id(zone["row"], zone["col"]),
                        "totalPallets": zone["count"], "deck": zone["deck"],
                    })
                else:
                    next_block = route[index + 1]
                    if not self._pallet_belt_has_room(runtime, next_block["blockId"]):
                        item["tickCounter"] = item["speedTicks"]
                        continue
                    item.update(
                        routeIndex=index + 1, currentBlockId=next_block["blockId"],
                        currentRow=next_block["row"], currentCol=next_block["col"], deck=next_block["deck"],
                    )
                    self._record_event(runtime, tick, "pallet_conveyor_moved", {
                        "palletId": item["id"], "row": item["currentRow"], "col": item["currentCol"],
                        "blockId": item["currentBlockId"], "palletCount": item["palletCount"],
                    })
                continue
            cur_row = int(item["currentRow"])
            cur_col = int(item["currentCol"])
            target_row = int(item["targetRow"])
            target_col = int(item["targetCol"])
            # 还在主干上：沿 col 60 向南走（从 84:58 → 84:60 主干 → 向南逐段到 target_row）
            if cur_row < target_row:
                # 2 格一段（2×2 模块）
                next_row = cur_row + 2
                if next_row > target_row:
                    next_row = target_row
                item["currentRow"] = next_row
                # 主干切换：从 col 58 到主干 col 60（只走一段就转 90 度向东）
                if cur_col == 58 and next_row >= 86:
                    item["currentCol"] = 60
                self._record_event(
                    runtime, tick, "pallet_conveyor_moved",
                    {"palletId": item["id"], "row": next_row, "col": item["currentCol"]},
                )
                continue
            if cur_row == target_row and cur_col < target_col:
                # 到达目标 row，向东转向帽口 col 62
                next_col = cur_col + 2
                if next_col > target_col:
                    next_col = target_col
                item["currentCol"] = next_col
                self._record_event(
                    runtime, tick, "pallet_conveyor_moved",
                    {"palletId": item["id"], "row": cur_row, "col": next_col},
                )
                continue
            if cur_row == target_row and cur_col == target_col:
                # 到达帽口：入 palletDocks
                docks = runtime.state.setdefault("palletDocks", {})
                dock = docks.setdefault(str(target_row), {"count": 0, "availableEmpty": 0})
                quantity = int(item.get("palletCount") or 1)
                dock["count"] = int(dock.get("count") or 0) + quantity
                dock["availableEmpty"] = int(dock.get("availableEmpty") or 0) + quantity
                item["status"] = "delivered"
                self._record_event(
                    runtime, tick, "pallet_dock_arrived",
                    {"palletId": item["id"], "portRow": target_row, "portCol": target_col,
                     "dockCount": dock["count"], "availableEmpty": dock["availableEmpty"]},
                )
        # 清理已送达的 item
        runtime.state["palletConveyorItems"] = [
            it for it in items if it.get("status") in PALLETS_ON_CONVEYOR
        ]


    def _send_pallet_back_to_lane(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task: dict[str, Any],
        target: dict[str, Any],
    ) -> bool:
        """托盘上还有余货：余货随托盘回到原货位，不能留在车上回待命位。"""
        extras = task.get("palletExtraBoxes") or []
        source_cell = str(task.get("palletSourceCellId") or "")
        source_layer = int(task.get("palletSourceLayer") or 1)
        if not extras or not source_cell:
            return False
        tick = int(runtime.state["tick"])
        if not self._robot_path_to(
            runtime,
            robot,
            source_cell,
            source_layer,
            extra_blocked=self._handoff_return_route_blocked_states(runtime),
        ):
            self._record_event(
                runtime,
                tick,
                "pallet_return_unreachable",
                {"taskId": task["id"], "robotId": robot["id"], "cellId": source_cell},
            )
            return False
        return_task = self._build_task(
            runtime.topology,
            f"task-return-pallet-{task['id']}-{tick}",
            "shuttle",
            "cross_zone",
            [{"cellId": str(target.get("handoffCellId") or ""), "layer": 1}],
            source_cell,
            False,
            3,
            created_tick=tick,
        )
        return_task["taskKind"] = PALLET_RETURN_TASK_KIND
        return_task["handoffTraffic"] = True
        return_task["status"] = "in_progress"
        return_task["startedTick"] = tick
        return_task["phase"] = "return_pallet_to_lane"
        return_task["phaseLabel"] = "余货随托盘回库"
        return_task["etaTicks"] = max(len(robot.get("path") or []) - 1, 1)
        return_task["assignedRobotId"] = robot["id"]
        return_task["palletSourceCellId"] = source_cell
        return_task["palletSourceLayer"] = source_layer
        return_task["palletExtraBoxes"] = [dict(box) for box in extras]
        # 回库终点是原货位的原层：同步任务目标，避免界面显示成「同层任务」却在坐升降机。
        return_task["targetCellId"] = source_cell
        return_task["targetLayer"] = source_layer
        runtime.state["tasks"].append(return_task)
        traffic = self._handoff_traffic_state(runtime)
        if traffic is not None and traffic.get("ownerRobotId") == robot.get("id"):
            traffic["ownerTaskId"] = return_task["id"]

        # 原订单任务转由输送线独立推进，四向车腾出来把余货送回货位。
        task["assignedRobotId"] = None
        robot["currentTaskId"] = return_task["id"]
        robot["carrying"] = [{**box, "kind": "pallet_goods"} for box in extras]
        # 载货单元是「1 个托盘」，余箱数由 carrying 表达，避免 load/capacity 出现 3/1。
        robot["load"] = 1
        robot["handlingAction"] = None
        robot["waitTicks"] = 0
        robot["returningToStandby"] = False
        robot["status"] = "moving"
        robot["phaseLabel"] = "余货随托盘回库"
        self._record_event(
            runtime,
            tick,
            "pallet_return_started",
            {
                "taskId": return_task["id"],
                "robotId": robot["id"],
                "cellId": source_cell,
                "layer": source_layer,
                "extraBoxes": len(extras),
            },
        )
        return True

    def _deliver_pallet_back(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any], occupied_cells: set[tuple[str, float]] | None = None) -> None:
        """托盘回到原货位：把余货写回库存，托盘留在货位里。"""
        tick = runtime.state["tick"]
        cell_id = str(task.get("palletSourceCellId") or task.get("deliveryCellId") or "")
        layer = int(task.get("palletSourceLayer") or 1)
        inventory = runtime.state.setdefault("inventory", [])
        restored = 0
        for box in task.get("palletExtraBoxes") or robot.get("carrying") or []:
            if cell_id and not any(
                str(item.get("cellId")) == cell_id
                and int(item.get("layer", 1)) == layer
                and int(item.get("depth", 1)) == int(box.get("depth", 1))
                for item in inventory
            ):
                record = {key: value for key, value in box.items() if key != "kind"}
                record["cellId"] = cell_id
                record["layer"] = layer
                inventory.append(record)
                restored += 1
        task["palletRestoredTick"] = tick
        self._record_event(
            runtime,
            tick,
            "pallet_returned_to_lane",
            {"taskId": task["id"], "cellId": cell_id, "layer": layer, "restored": restored},
        )
        robot["carrying"] = []
        robot["load"] = 0
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
            "orderLineId": parent_task.get("orderLineId"),
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
        occupied_cells: set[tuple[str, float]] | None = None,
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
                "orderLineId": slot.get("orderLineId") or task.get("orderLineId"),
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
        if any(
            item.get("currentBlockId") == head_block and item.get("status") in PALLETS_ON_CONVEYOR
            for item in runtime.state.get("palletConveyorItems", [])
        ):
            return False
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
        child_id = f"{task['id']}-return"
        if any(t.get("id") == child_id for t in runtime.state.get("tasks", [])):
            return
        slot = (task.get("transitGoods") or [{}])[0]
        if not station_block:
            # Legacy/non-explicit fixtures have no compiled branch identity.
            # Preserve inventory conservation by exposing the box directly at
            # the configured port for CTU pickup instead of silently dropping it.
            tick = int(runtime.state["tick"])
            child = {
                "id": child_id,
                "parentId": task["id"],
                "taskKind": "picking_return",
                "robotType": "none",
                "status": "assigned",
                "phase": "pickup_from_port",
                "phaseLabel": self._phase_label("pickup_from_port"),
                "lines": [dict(slot)],
                "transitGoods": [dict(slot)],
                "placedGoods": [],
                "deliveredGoods": [],
                "picked": 0,
                "history": [{"tick": tick, "phase": "pickup_from_port", "status": "assigned"}],
                "pickStationCellId": task.get("pickStationCellId"),
                "assignedPortId": task.get("assignedPortId"),
                "deliveryCellId": task.get("deliveryCellId"),
                "createdTick": tick,
                "startedTick": tick,
                "completedTick": None,
                "urgent": bool(task.get("urgent")),
                "orderId": task.get("orderId"),
                "orderLineId": slot.get("orderLineId") or task.get("orderLineId"),
                "legacyReturnFallback": True,
            }
            runtime.state.setdefault("tasks", []).append(child)
            self._record_event(
                runtime,
                tick,
                "task_spawned",
                {
                    "taskId": child_id,
                    "parentId": task["id"],
                    "taskKind": "picking_return",
                    "legacyReturnFallback": True,
                },
            )
            return
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
            # Conveyor physics advances on logical simulation time. Playback
            # multiplier only changes wall-clock pacing of the outer loop.
            progress_seconds = float(transport.get("travelProgressSeconds", 0.0)) + self._sim_tick_seconds(runtime)
            if progress_seconds + 1e-9 < seconds_per_block:
                transport["travelProgressSeconds"] = progress_seconds
                return False
            # 保留不足一个 tick 的余量，使离散模块事件长期平均速度精确
            # 对齐前端连续动画，而不是每段都向上取整造成越走越慢。
            transport["travelProgressSeconds"] = max(0.0, progress_seconds - seconds_per_block)

        next_block = route[index + 1]
        node = runtime.conveyor_graph.get("nodes", {}).get(next_block, {})
        capacity = max(1, int(node.get("capacity", 1) or 1))
        occupying = sum(
            item.get("currentBlockId") == next_block and item.get("status") in PALLETS_ON_CONVEYOR
            for item in runtime.state.get("palletConveyorItems", [])
        )
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
        occupied_cells: set[tuple[str, float]] | None = None,
        *,
        return_to_standby: bool = False,
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
        if (
            (has_unfinished_tasks or return_to_standby)
            and home_cell
            and home_cell != robot["currentCellId"]
            and self._robot_path_to(runtime, robot, home_cell, 1)
        ):
            robot["returningToStandby"] = True
            robot["status"] = "returning"
            robot["phaseLabel"] = "返回待命位"
        else:
            robot["returningToStandby"] = False
            robot["status"] = "idle"
            robot["phaseLabel"] = "待命"
        if (
            not cancelled
            and not task.get("parentId")
            and task.get("taskKind") not in {EMPTY_PALLET_TASK_KIND, PALLET_RETURN_TASK_KIND}
        ):
            # 子任务（拣货分裂）不重复累加指标：父任务完成时已计一次。
            # 空托盘回收 / 余货随托盘回库属内部维护任务，不计入订单吞吐。
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
        occupied_cells: set[tuple[str, float]] | None = None,
    ) -> None:
        tick = runtime.state["tick"]
        if task.get("phase") == "yielding_for_traffic":
            resume_phase = str(task.get("trafficYieldResumePhase") or "moving")
            resume_cell = str(task.get("trafficYieldResumeCellId") or "")
            resume_layer = int(task.get("trafficYieldResumeLayer") or robot.get("currentLayer", 1))
            if not resume_cell:
                self._fail_active_task(runtime, robot, task, "traffic_yield_resume_target_missing")
                return

            occupied_states: set[tuple[int, int, int]] = set()
            for other in runtime.state.get("robots", []):
                if other.get("id") == robot.get("id") or other.get("type") != "shuttle":
                    continue
                other_layer = int(other.get("currentLayer", 1))
                occupied_states.add((int(other["currentRow"]), int(other["currentCol"]), other_layer))
                other_path = list(other.get("path") or [])
                if len(other_path) > 1:
                    try:
                        _floor, next_row, next_col = parse_cell_id(str(other_path[1]))
                    except (TypeError, ValueError):
                        continue
                    other_layers = list(
                        other.get("pathLayers")
                        or [other_layer] * len(other_path)
                    )
                    next_layer = int(other_layers[1]) if len(other_layers) > 1 else other_layer
                    occupied_states.add((next_row, next_col, next_layer))
            dynamic_blocked = expand_shuttle_blocked_states(runtime.topology, occupied_states)
            if not self._robot_path_to(
                runtime,
                robot,
                resume_cell,
                resume_layer,
                extra_blocked=dynamic_blocked,
                task_id=task.get("id"),
                evidence_kind="replan",
            ):
                robot["status"] = "waiting"
                robot["phaseLabel"] = "安全侧位等待 / 冲突通道未清空"
                robot["path"] = [robot["currentCellId"]]
                robot["pathLayers"] = [robot.get("currentLayer", 1)]
                robot["pathStages"] = [task["phase"]]
                return

            path = list(robot.get("path") or [])
            path_layers = list(robot.get("pathLayers") or [])
            if len(path) > 1:
                next_layer = path_layers[1] if len(path_layers) > 1 else robot.get("currentLayer", 1)
                physical_blocker, footprint_reserved = self._physical_move_blocker(
                    runtime,
                    robot,
                    path[1],
                    next_layer,
                    set(),
                )
                if physical_blocker is not None or footprint_reserved:
                    robot["status"] = "waiting"
                    robot["phaseLabel"] = "安全侧位等待 / 冲突通道未清空"
                    robot["path"] = [robot["currentCellId"]]
                    robot["pathLayers"] = [robot.get("currentLayer", 1)]
                    robot["pathStages"] = [task["phase"]]
                    return

            task["phase"] = resume_phase
            task["phaseLabel"] = self._phase_label(resume_phase)
            for key in (
                "trafficYieldResumePhase",
                "trafficYieldResumeCellId",
                "trafficYieldResumeLayer",
                "trafficYieldForRobotId",
            ):
                task.pop(key, None)
            robot["status"] = "moving"
            robot["phaseLabel"] = task["phaseLabel"]
            robot["pathStages"] = [resume_phase] * len(robot.get("path") or [])
            return

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
                target_index = next(
                    (
                        index
                        for index, item in enumerate(inventory)
                        if item.get("cellId") == line.get("cellId")
                        and int(item.get("layer", 1)) == int(line.get("layer", 1))
                        and int(item.get("depth", 1)) == int(line["depth"])
                    ),
                    None,
                )
                if target_index is None:
                    # 托盘已被同一货位的另一趟整托盘取货带走：等它回库后再取，
                    # 不能凭空造出一个不存在的箱子。
                    robot["status"] = "waiting"
                    robot["phaseLabel"] = "等待同货位托盘回库"
                    return
                inventory.pop(target_index)
                # 一货位一托盘：整托盘随车抬出。货位还有余箱时一并抬走，
                # 机械臂只取订单要的那箱，其余随托盘回到原货位。
                remaining = any(
                    str(item.get("cellId")) == str(line.get("cellId"))
                    and int(item.get("layer", 1)) == int(line.get("layer", 1))
                    for item in inventory
                )
                if not remaining and not task.get("parentId") and task.get("taskKind") != "relocate":
                    task["palletBecomesEmpty"] = True
                    task["palletSourceCellId"] = str(line.get("cellId"))
                    task["palletSourceLayer"] = int(line.get("layer", 1))
                elif remaining and task.get("taskKind") != "relocate":
                    lifted: list[dict[str, Any]] = []
                    for item in list(inventory):
                        if (
                            str(item.get("cellId")) == str(line.get("cellId"))
                            and int(item.get("layer", 1)) == int(line.get("layer", 1))
                        ):
                            lifted.append(dict(item))
                            inventory.remove(item)
                    if lifted:
                        task["palletExtraBoxes"] = lifted
                        task["palletSourceCellId"] = str(line.get("cellId"))
                        task["palletSourceLayer"] = int(line.get("layer", 1))
            robot["load"] = min(robot.get("capacity", 1), robot.get("load", 0) + 1)
            task["picked"] = task.get("picked", 0) + 1
            # 记录车载货箱，交付动作才能把实载数据写进 deliveredGoods（前端按它渲染交付货箱）。
            robot["carrying"] = [dict(line_item) for line_item in (task.get("lines") or [])]
            for box in task.get("palletExtraBoxes") or []:
                robot["carrying"].append({**box, "kind": "pallet_goods"})
            # 倒库任务交付到库内目标货道（其层随目标货道）；出货任务交付到地面线（L1）。
            delivery_layer = int(task.get("targetLayer", 1)) if task.get("taskKind") == "relocate" else 1
            traffic = self._handoff_traffic_state(runtime) if task.get("armHandoff") else None
            if traffic is not None and traffic.get("ownerRobotId") != robot.get("id"):
                # Fetching and branch transport are fully parallel.  A loaded
                # non-owner proceeds to a branch-local holding state and stops
                # before the first shared single-lane segment.
                staging_cell, staging_layer = self._handoff_staging_target(runtime, robot, task)
                task["handoffStagingCellId"] = staging_cell
                task["handoffStagingLayer"] = staging_layer
                if str((runtime.arm_handoff or {}).get("handoffCellId") or "") == HANDOFF_FIXED_CELL_ID:
                    task["handoffQueueAdmitted"] = False
                    task.pop("handoffQueuedTick", None)
                else:
                    task["handoffQueuedTick"] = tick
                task["handoffStagingOriginCellId"] = robot.get("currentCellId")
                task["handoffStagingOriginLayer"] = robot.get("currentLayer", 1)
                if (
                    staging_cell != robot.get("currentCellId")
                    or staging_layer != int(robot.get("currentLayer", 1))
                ) and self._set_robot_leg(
                    runtime,
                    robot,
                    task,
                    "to_handoff_staging",
                    staging_cell,
                    target_layer=staging_layer,
                ):
                    task["handoffStagingApproachCells"] = list(robot.get("path") or [])
                    task["handoffStagingApproachLayers"] = list(robot.get("pathLayers") or [])
                    task["phaseLabel"] = "载货前往机械臂等待区"
                    robot["phaseLabel"] = task["phaseLabel"]
                else:
                    task["phase"] = "waiting_handoff_slot"
                    task["phaseLabel"] = "已到等待区 / 等待机械臂通行时隙"
                    robot["status"] = "waiting"
                    robot["phaseLabel"] = task["phaseLabel"]
                    robot["path"] = [robot["currentCellId"]]
                    robot["pathLayers"] = [robot.get("currentLayer", 1)]
                    robot["pathStages"] = [task["phase"]]
                    self._record_event(
                        runtime,
                        tick,
                        "handoff_queue_joined",
                        {"robotId": robot["id"], "taskId": task["id"], "cellId": robot["currentCellId"]},
                    )
            elif self._set_robot_leg(
                runtime,
                robot,
                task,
                "delivery_to_port",
                task["deliveryCellId"],
                target_layer=delivery_layer,
            ):
                task["phaseLabel"] = "倒库移位" if task.get("taskKind") == "relocate" else "输送线交付"
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            self._record_event(runtime, tick, "task_state_changed", {"taskId": task["id"], "phase": task["phase"], "status": task["status"]})
            return

        if task["phase"] == "delivery_empty_pallet":
            # 空托盘到达 (76,35)：堆到地面输送线上并计数，不进入拣货链路。
            self._deliver_empty_pallet(runtime, robot, task, occupied_cells)
            return

        if task["phase"] == "pallet_pickup_from_stack":
            if not self._robot_path_to(runtime, robot, EMPTY_PALLET_COLLECT_CELL_ID, 1):
                robot.update(status="waiting", phaseLabel="等待路径到入主线点")
                return
            stack = runtime.state["emptyPalletStack"]
            if "palletBatch" in task:
                records = stack["stacks"]
                batch = records[:EMPTY_PALLET_STACK_CAPACITY]
                if not batch:
                    self._complete_task(runtime, robot, task, occupied_cells)
                    return
                del records[:len(batch)]
                stack["count"] = len(records)
                task.update(palletBatch=batch, palletCount=len(batch))
            count = int(task.get("palletCount") or 1)
            robot["carrying"] = [{
                "kind": "empty_pallet",
                "sourceCellId": task.get("palletSourceCellId"),
                "sourceLayer": int(task.get("palletSourceLayer") or 1),
                "qty": count,
            }]
            robot["load"] = count
            self._record_event(
                runtime, tick, "pallet_picked_from_stack",
                {"taskId": task["id"], "robotId": robot["id"], "palletCount": count,
                 "stackCount": stack["count"]},
            )
            task.update(phase="delivery_to_collect", phaseLabel="送到机械臂入主线点",
                        etaTicks=max(len(robot["path"]) - 1, 1))
            robot.update(status="moving", phaseLabel=task["phaseLabel"])
            task["history"].append({"tick": tick, "phase": task["phase"], "status": task["status"]})
            return

        if task["phase"] in {"retreat_from_pallet_collect", "waiting_pallet_collect_entry"}:
            task.update(phase="waiting_pallet_collect_entry", phaseLabel="空托盘支线 / 等待通道清空")
            robot.update(status="waiting", phaseLabel=task["phaseLabel"])
            if (
                not self._pallet_collect_spur_is_occupied(runtime, robot)
                and self._pallet_collect_maneuver_path(
                    runtime, robot, PALLET_COLLECT_EXIT_CELL_ID, start=EMPTY_PALLET_COLLECT_CELL_ID,
                )
            ):
                self._set_robot_leg(
                    runtime, robot, task, "delivery_to_collect", EMPTY_PALLET_COLLECT_CELL_ID, target_layer=1,
                )
            return

        if task["phase"] == "clearing_pallet_collect":
            if robot["currentCellId"] == PALLET_COLLECT_EXIT_CELL_ID:
                self._complete_task(runtime, robot, task, occupied_cells, return_to_standby=True)
                return
            exit_path = self._pallet_collect_maneuver_path(runtime, robot, PALLET_COLLECT_EXIT_CELL_ID)
            if exit_path:
                self._set_pallet_collect_maneuver(robot, exit_path, task["phase"], task["phaseLabel"])
            else:
                robot.update(status="waiting", phaseLabel=task["phaseLabel"])
            return

        if task["phase"] == "waiting_pallet_collect_arm":
            return

        if task["phase"] == "delivery_to_collect":
            if robot["currentCellId"] != EMPTY_PALLET_COLLECT_CELL_ID or robot.get("currentLayer") != 1:
                self._set_robot_leg(
                    runtime, robot, task, "delivery_to_collect", EMPTY_PALLET_COLLECT_CELL_ID, target_layer=1,
                )
                return
            task.update(phase="waiting_pallet_collect_arm", phaseLabel="等待机械臂 02 取空托盘")
            robot.update(status="handling", phaseLabel=task["phaseLabel"])
            self._record_event(
                runtime, tick, "pallet_delivered_to_collect",
                {"taskId": task["id"], "robotId": robot["id"], "cellId": EMPTY_PALLET_COLLECT_CELL_ID,
                 "palletCount": int(task.get("palletCount") or 1)},
            )
            self._start_pallet_arm_handoff(runtime, task, robot)
            return

        if task["phase"] == "return_pallet_to_lane":
            # 托盘带余货回到原货位：余货写回库存，托盘留在货位里。
            self._deliver_pallet_back(runtime, robot, task, occupied_cells)
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

        if task["phase"] == "yielding_for_handoff_queue":
            resume_phase = str(task.pop("yieldResumePhase", "pickup_from_shuttle"))
            resume_cell = str(task.pop("yieldResumeCellId", task.get("sourceCellId") or ""))
            resume_layer = int(task.pop("yieldResumeLayer", task.get("sourceLayer") or 1))
            self._set_robot_leg(
                runtime,
                robot,
                task,
                resume_phase,
                resume_cell,
                target_layer=resume_layer,
            )
            return

        if task["phase"] == "to_handoff_staging":
            staging_cell = str(task.get("handoffStagingCellId") or "")
            staging_layer = int(task.get("handoffStagingLayer") or 1)
            if (
                staging_cell
                and (
                    robot.get("currentCellId") != staging_cell
                    or int(robot.get("currentLayer", 1)) != staging_layer
                )
            ):
                # Recovery/back-off may temporarily reduce a route to one
                # state. Never convert that intermediate coordinate into a
                # queue stop; re-establish the assigned odd-column module.
                self._set_robot_leg(
                    runtime,
                    robot,
                    task,
                    "to_handoff_staging",
                    staging_cell,
                    target_layer=staging_layer,
                )
                return
            if (
                str((runtime.arm_handoff or {}).get("handoffCellId") or "") == HANDOFF_FIXED_CELL_ID
                and not task.get("handoffQueueAdmitted")
            ):
                reserved_slots = {
                    str(other_task.get("handoffStagingCellId") or "")
                    for other_task in runtime.state.get("tasks", [])
                    if other_task.get("id") != task.get("id")
                    and other_task.get("status") not in TERMINAL_TASK_STATUSES
                    and other_task.get("handoffQueueAdmitted")
                }
                queue_slot = next(
                    (slot for slot in HANDOFF_QUEUE_SLOT_CELL_IDS if slot not in reserved_slots),
                    None,
                )
                if queue_slot is None:
                    robot["status"] = "waiting"
                    task["phaseLabel"] = "输送线入口等待队列空位"
                    robot["phaseLabel"] = task["phaseLabel"]
                    return
                task["handoffQueueAdmitted"] = True
                task["handoffQueuedTick"] = tick
                task["handoffStagingCellId"] = queue_slot
                task["handoffStagingLayer"] = 1
                if queue_slot != robot.get("currentCellId"):
                    if self._set_robot_leg(
                        runtime,
                        robot,
                        task,
                        "to_handoff_staging",
                        queue_slot,
                        target_layer=1,
                    ):
                        task["handoffStagingApproachCells"] = list(robot.get("path") or [])
                        task["handoffStagingApproachLayers"] = list(robot.get("pathLayers") or [])
                        task["phaseLabel"] = "已进入输送线队列 / 向前排队"
                        robot["phaseLabel"] = task["phaseLabel"]
                        return
                    task["handoffQueueAdmitted"] = False
                    task.pop("handoffQueuedTick", None)
                    task["handoffStagingCellId"] = HANDOFF_QUEUE_SLOT_CELL_IDS[-1]
                    robot["status"] = "waiting"
                    robot["phaseLabel"] = "输送线入口等待队列通路"
                    return
            traffic = self._handoff_traffic_state(runtime)
            if traffic is not None and traffic.get("ownerRobotId") == robot.get("id"):
                if self._set_robot_leg(
                    runtime,
                    robot,
                    task,
                    "delivery_to_port",
                    task.get("deliveryCellId"),
                    target_layer=1,
                ):
                    task.pop("handoffStagingCellId", None)
                    task.pop("handoffStagingLayer", None)
                return
            task["phase"] = "waiting_handoff_slot"
            task["phaseLabel"] = "已到等待区 / 等待机械臂通行时隙"
            robot["status"] = "waiting"
            robot["phaseLabel"] = task["phaseLabel"]
            robot["path"] = [robot["currentCellId"]]
            robot["pathLayers"] = [robot.get("currentLayer", 1)]
            robot["pathStages"] = [task["phase"]]
            self._record_event(
                runtime,
                tick,
                "handoff_queue_joined",
                {"robotId": robot["id"], "taskId": task["id"], "cellId": robot["currentCellId"]},
            )
            return

        if task["phase"] == "yielding_handoff_staging":
            traffic = self._handoff_traffic_state(runtime)
            if traffic is not None and traffic.get("ownerRobotId") == robot.get("id"):
                if self._set_robot_leg(
                    runtime,
                    robot,
                    task,
                    "delivery_to_port",
                    task.get("deliveryCellId"),
                    target_layer=1,
                ):
                    task.pop("handoffStagingCellId", None)
                    task.pop("handoffStagingLayer", None)
                return
            task["phase"] = "waiting_handoff_slot"
            task["phaseLabel"] = "已退回支路等待区 / 等待机械臂通行时隙"
            task["handoffStagingCellId"] = robot.get("currentCellId")
            task["handoffStagingLayer"] = robot.get("currentLayer", 1)
            robot["status"] = "waiting"
            robot["phaseLabel"] = task["phaseLabel"]
            robot["path"] = [robot["currentCellId"]]
            robot["pathLayers"] = [robot.get("currentLayer", 1)]
            robot["pathStages"] = [task["phase"]]
            return

        if task["phase"] == "waiting_handoff_slot":
            # _refresh_handoff_traffic selects the next loaded Shuttle and
            # promotes it to delivery_to_port.  Until then it stays at the
            # already verified pickup state and remains a dynamic obstacle.
            robot["status"] = "waiting"
            robot["phaseLabel"] = task["phaseLabel"]
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

    def _step_off_port(self, runtime: RunRuntime, robot: dict[str, Any], occupied_cells: set[tuple[str, float]] | None) -> None:
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
        if not self._physical_movement_ready(
            runtime,
            robot,
            back,
            float(robot.get("currentLayer", 1)),
        ):
            return
        motion_from = self._motion_position(runtime, robot)
        self._consume_motion_credit(robot)
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
            cur_layer_f = float(robot.get("currentLayer", 1))
            occupied_cells.discard((current, cur_layer_f))
            occupied_cells.add((back, cur_layer_f))
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
        speed_contract = runtime.config.get("motionSpeedContract")
        physics_contract = runtime.config.get("motionPhysicsContract")
        paths, layers, stages = build_playback_route(
            task["plan"],
            self._sim_tick_seconds(runtime),
            lift_speed_meters_per_second=(
                float(speed_contract["liftSpeedMetersPerSecond"])
                if isinstance(speed_contract, dict)
                else None
            ),
            shuttle_level_height_meters=(
                float(physics_contract["shuttleLevelHeightMeters"])
                if isinstance(physics_contract, dict)
                else None
            ),
        )
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
        self._record_planning_evidence(
            runtime,
            robot,
            task.get("id"),
            paths,
            layers,
            "plan",
            {
                "planningDurationMs": task["plan"].get("planning_time_ms"),
                "visitedStateCount": task["plan"].get("visited_states"),
            },
        )

    def _record_planning_evidence(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        task_id: str | None,
        path: list[str],
        layers: list[float | int],
        kind: str,
        diagnostic: dict[str, Any],
    ) -> None:
        if not isinstance(runtime.config.get("algorithmExperimentContract"), dict):
            return
        physics = runtime.config.get("motionPhysicsContract") or {}
        horizontal = 0.0
        vertical = 0.0
        for index in range(1, len(path)):
            _from_floor, from_row, from_col = parse_cell_id(path[index - 1])
            _to_floor, to_row, to_col = parse_cell_id(path[index])
            horizontal += (
                ((to_row - from_row) ** 2 + (to_col - from_col) ** 2) ** 0.5
                * float(physics.get("metersPerCell") or 0.0)
            )
            vertical += abs(float(layers[index]) - float(layers[index - 1])) * float(
                physics.get("shuttleLevelHeightMeters") or 0.0
            )
        sequence = int(runtime.state.get("planningSequence") or 0) + 1
        runtime.state["planningSequence"] = sequence
        generation = int(
            (runtime.state.get(MOTION_DIAGNOSTICS_NAMESPACE) or {}).get("generation") or 1
        )
        plan_id = f"{runtime.run_id}:g{generation}:plan:{sequence}"
        replan_id = f"{runtime.run_id}:g{generation}:replan:{sequence}" if kind == "replan" else None
        experiment = runtime.config["algorithmExperimentContract"]
        record = {
            "planId": plan_id,
            "replanId": replan_id,
            "planKind": kind,
            "algorithm": runtime.config.get("pathPolicy", "astar"),
            "algorithmVersion": experiment.get("algorithmVersion"),
            "plannedPath": [
                {"cellId": cell, "layer": float(layers[index])}
                for index, cell in enumerate(path)
            ],
            "plannedDistanceMeters": round(horizontal, 9),
            "plannedVerticalMeters": round(vertical, 9),
            "planningDurationMs": diagnostic.get("planningDurationMs"),
            "visitedStateCount": diagnostic.get("visitedStateCount"),
            "taskId": task_id,
            "robotId": robot.get("id"),
            "generation": generation,
            "topologyRevision": experiment.get("topologyRevision"),
            "tick": int(runtime.state.get("tick") or 0),
        }
        runtime.state.setdefault("planningEvidence", []).append(record)
        robot["activePlanId"] = plan_id
        robot["activeReplanId"] = replan_id

    def _robot_path_to(
        self,
        runtime: RunRuntime,
        robot: dict[str, Any],
        target_cell_id: str,
        target_layer: int | None = None,
        *,
        extra_blocked: set[tuple[str, int, int]] | None = None,
        stop_adjacent: bool = False,
        task_id: str | None = None,
        evidence_kind: str = "plan",
    ) -> bool:
        """计算并写入 robot 的 path/pathLayers/target；CTU 走自由网格寻路，四向车走巷道+电梯寻路。"""
        diagnostic: dict[str, Any] = {}
        if robot["type"] == "shuttle":
            layer = target_layer if target_layer is not None else robot.get("currentLayer", 1)
            shuttle_blocked: set[tuple[int, int, int]] = set()
            for blocked_state in extra_blocked or set():
                if not isinstance(blocked_state, tuple) or len(blocked_state) != 3:
                    continue
                first, second, third = blocked_state
                if isinstance(first, str):
                    # Legacy failed-cell inputs are (floor, row, col).
                    shuttle_blocked.add((int(second), int(third), 1))
                else:
                    shuttle_blocked.add((int(first), int(second), int(third)))
            path3d = shortest_path_shuttle(
                runtime.topology,
                robot["currentCellId"],
                robot["currentLayer"],
                target_cell_id,
                layer,
                runtime.config["pathPolicy"],
                extra_blocked=shuttle_blocked or None,
                evidence=diagnostic,
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
                evidence=diagnostic,
            )
            if not path_cells:
                return False
            if stop_adjacent and len(path_cells) > 1:
                path_cells = path_cells[:-1]
            robot["path"] = [cell["id"] for cell in path_cells]
            robot["pathLayers"] = [1] * len(robot["path"])
            robot["targetLayer"] = max(1, min(CTU_RACK_LAYERS, int(target_layer or 1)))
        robot["targetCellId"] = target_cell_id
        self._record_planning_evidence(
            runtime,
            robot,
            task_id or robot.get("currentTaskId"),
            list(robot["path"]),
            list(robot.get("pathLayers") or [1] * len(robot["path"])),
            evidence_kind,
            diagnostic,
        )
        return True

    def _set_robot_leg(self, runtime: RunRuntime, robot: dict[str, Any], task: dict[str, Any], phase: str, target_cell_id: str | None, target_layer: int | None = None) -> bool:
        if not target_cell_id:
            self._fail_active_task(runtime, robot, task, "target_missing")
            return False
        extra_blocked: set[tuple[int, int, int]] | None = None
        if (
            robot.get("type") == "shuttle"
            and phase == "to_handoff_staging"
            and str((runtime.arm_handoff or {}).get("handoffCellId") or "") == HANDOFF_FIXED_CELL_ID
        ):
            # Every loaded Shuttle joins the FIFO from its west end.  Allowing
            # an east-side shortcut makes a later vehicle cross occupied queue
            # slots (c41, c39, ...), which is an unavoidable physical deadlock.
            extra_blocked = self._handoff_queue_approach_blocked_states(runtime)
        if not self._robot_path_to(
            runtime,
            robot,
            target_cell_id,
            target_layer,
            extra_blocked=extra_blocked,
            stop_adjacent=phase in {"pickup_from_ctu", "return_to_storage"},
            task_id=task.get("id"),
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
        blocked = [
            robot
            for robot in runtime.state["robots"]
            if robot["status"] == "blocked"
            # A taskless standby return is best-effort and is automatically
            # parked/yielded for productive traffic.  Including it in the
            # business deadlock chain emits repeated false alarms while that
            # mitigation is already working.
            and not (robot.get("returningToStandby") and not robot.get("currentTaskId"))
            # Loaded Shuttles feeding the fixed row-92 FIFO intentionally form
            # a convoy behind one another. Their waitTicks describe queueing,
            # not a cyclic dependency; the end-to-end queue replay verifies
            # forward progress separately.
            and not (
                str((runtime.arm_handoff or {}).get("handoffCellId") or "") == HANDOFF_FIXED_CELL_ID
                and (
                    find_task(runtime.state.get("tasks", []), robot.get("currentTaskId")) or {}
                ).get("phase") == "to_handoff_staging"
            )
        ]

        if len(blocked) >= 2 and all(
            robot["waitTicks"] >= DEADLOCK_ALERT_AFTER_TICKS for robot in blocked
        ):
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
            if task.get("completedTick") is not None
            and task.get("startedTick") is not None
            # 空托盘回收是内部维护任务，不计入订单平均耗时。
            and task.get("taskKind") != EMPTY_PALLET_TASK_KIND
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
            "to_handoff_staging": "载货前往机械臂等待区",
            "yielding_for_handoff_queue": "空车退入侧位让行",
            "yielding_handoff_staging": "等待区主动退出让行",
            "waiting_handoff_slot": "已取货 / 等待机械臂通行时隙",
            "delivery_to_port": "输送线交付",
            "delivery_to_collect": "送到机械臂入主线点",
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
