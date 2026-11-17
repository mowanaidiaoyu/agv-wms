from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .pathfinding import shortest_path, shortest_path_shuttle


ALGORITHM_VERSION = "throughput-aware-v1"
UNREACHABLE_SCORE = 1_000_000_000.0
DEFAULT_WEIGHTS: dict[str, float | str] = {
    "contractVersion": ALGORITHM_VERSION,
    "travelTimeWeight": 1.0,
    "congestionWeight": 0.65,
    "ageWeight": 0.20,
    "returnWeight": 0.25,
    "handlingTicks": 2.0,
    "maxCongestionPenaltyTicks": 30.0,
    "maxAgeBonusTicks": 60.0,
}


@dataclass(frozen=True)
class CandidateScore:
    robot_id: str
    task_id: str
    priority: int
    score: float
    evidence: dict[str, Any]


def frozen_weights(value: dict[str, Any] | None) -> dict[str, float | str]:
    result = dict(DEFAULT_WEIGHTS)
    if value:
        unknown = set(value) - set(DEFAULT_WEIGHTS)
        if unknown:
            raise ValueError(f"Unsupported throughput-aware weight fields: {sorted(unknown)}")
        result.update(value)
    if result.get("contractVersion") != ALGORITHM_VERSION:
        raise ValueError(f"throughputAwareWeights.contractVersion must be {ALGORITHM_VERSION}")
    for field in (
        "travelTimeWeight",
        "congestionWeight",
        "ageWeight",
        "returnWeight",
        "handlingTicks",
        "maxCongestionPenaltyTicks",
        "maxAgeBonusTicks",
    ):
        raw = result[field]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
            raise ValueError(f"throughputAwareWeights.{field} must be finite")
        number = float(raw)
        maximum = 10.0 if field.endswith("Weight") else 1_000.0
        if not 0.0 <= number <= maximum:
            raise ValueError(f"throughputAwareWeights.{field} must be between 0 and {maximum:g}")
        result[field] = number
    return result


def _ready_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(task.get("id")): task for task in tasks}
    return [
        task
        for task in tasks
        if task.get("status") == "pending"
        and all(
            by_id.get(str(dependency)) is None
            or by_id[str(dependency)].get("status") == "completed"
            for dependency in (task.get("blockedBy") or [])
        )
    ]


def _ctu_path(
    topology: dict[str, Any],
    start: str,
    target: str,
    path_policy: str,
    extra_blocked: set[tuple[str, int, int]] | None,
) -> list[dict[str, Any]]:
    return shortest_path(
        topology,
        start,
        target,
        path_policy,
        avoid_storage=True,
        extra_blocked=extra_blocked,
    )


def _edge_ticks(
    edges: int,
    robot_type: str,
    physics: dict[str, Any] | None,
    speed: dict[str, Any] | None,
) -> float:
    if edges <= 0:
        return 0.0
    if not isinstance(physics, dict) or not isinstance(speed, dict):
        return float(edges)
    meters = edges * float(physics.get("metersPerCell") or 1.0)
    field = "shuttleSpeedMetersPerSecond" if robot_type == "shuttle" else "ctuSpeedMetersPerSecond"
    meters_per_tick = float(speed.get(field) or 1.0) * float(speed.get("simTickSeconds") or 1.0)
    if meters_per_tick <= 0:
        return float(edges)
    # The scheduler commits at most one edge per logical tick.
    return float(max(edges, math.ceil(meters / meters_per_tick)))


def _shuttle_path_ticks(
    path: list[tuple[int, int, int]],
    physics: dict[str, Any] | None,
    speed: dict[str, Any] | None,
) -> float:
    if len(path) <= 1:
        return 0.0
    if not isinstance(physics, dict) or not isinstance(speed, dict):
        return float(len(path) - 1)
    tick_seconds = float(speed.get("simTickSeconds") or 1.0)
    total = 0
    for previous, current in zip(path, path[1:]):
        vertical = previous[2] != current[2]
        distance = float(
            physics.get("shuttleLevelHeightMeters" if vertical else "metersPerCell") or 1.0
        )
        velocity = float(
            speed.get("liftSpeedMetersPerSecond" if vertical else "shuttleSpeedMetersPerSecond") or 1.0
        )
        total += max(1, math.ceil(distance / max(velocity * tick_seconds, 1e-9)))
    return float(total)


def _path_cell_ids(path: list[dict[str, Any]]) -> set[str]:
    return {str(cell.get("id")) for cell in path if cell.get("id")}


def _congestion_penalty(
    robot: dict[str, Any],
    task: dict[str, Any],
    pickup_path: list[dict[str, Any]],
    delivery_path: list[dict[str, Any]],
    state: dict[str, Any],
    maximum: float,
) -> tuple[float, dict[str, int]]:
    path_cells = _path_cell_ids(pickup_path) | _path_cell_ids(delivery_path)
    occupied_on_route = 0
    blocked_on_route = 0
    for other in state.get("robots", []):
        if other.get("id") == robot.get("id"):
            continue
        if other.get("currentCellId") in path_cells:
            occupied_on_route += 1
            if other.get("status") == "blocked":
                blocked_on_route += 1
    same_port_backlog = sum(
        1
        for other_task in state.get("tasks", [])
        if other_task.get("id") != task.get("id")
        and other_task.get("deliveryCellId") == task.get("deliveryCellId")
        and other_task.get("status") not in {"completed", "failed", "exception", "cancelled"}
    )
    queues = state.get("queues") if isinstance(state.get("queues"), dict) else {}
    downstream_queue = sum(len(value) for value in queues.values() if isinstance(value, list))
    raw = occupied_on_route * 2.0 + blocked_on_route * 3.0 + same_port_backlog * 1.5 + downstream_queue * 0.25
    return min(maximum, raw), {
        "occupiedOnRoute": occupied_on_route,
        "blockedOnRoute": blocked_on_route,
        "samePortBacklog": same_port_backlog,
        "downstreamQueueDepth": downstream_queue,
    }


def score_candidate(
    robot: dict[str, Any],
    task: dict[str, Any],
    topology: dict[str, Any],
    path_policy: str,
    state: dict[str, Any],
    config: dict[str, Any],
    extra_blocked: set[tuple[str, int, int]] | None = None,
) -> CandidateScore | None:
    source = task.get("sourceCellId")
    if not source:
        return None
    physics = config.get("motionPhysicsContract")
    speed = config.get("motionSpeedContract")
    weights = frozen_weights(config.get("throughputAwareWeights"))
    robot_type = str(robot.get("type"))
    if robot_type == "shuttle":
        pickup_shuttle = shortest_path_shuttle(
            topology,
            str(robot.get("currentCellId")),
            int(robot.get("currentLayer") or 1),
            str(source),
            int(task.get("sourceLayer") or 1),
            path_policy,
        )
        if not pickup_shuttle:
            return None
        travel_ticks = _shuttle_path_ticks(pickup_shuttle, physics, speed)
        pickup_path: list[dict[str, Any]] = []
        delivery_path: list[dict[str, Any]] = []
        return_ticks = 0.0
    else:
        pickup_path = _ctu_path(
            topology,
            str(robot.get("currentCellId")),
            str(source),
            path_policy,
            extra_blocked,
        )
        if not pickup_path:
            return None
        pickup_edges = len(pickup_path) - 1
        line_cells = [str(line.get("cellId")) for line in task.get("lines", []) if line.get("cellId")]
        current = line_cells[0] if line_cells else str(source)
        delivery_edges = 0
        delivery_path = []
        for target in [*line_cells[1:], str(task.get("deliveryCellId") or "")]:
            if not target:
                continue
            segment = _ctu_path(topology, current, target, path_policy, None)
            if not segment:
                return None
            delivery_edges += len(segment) - 1
            delivery_path.extend(segment)
            current = target
        travel_ticks = _edge_ticks(pickup_edges + delivery_edges, robot_type, physics, speed)
        home = robot.get("homeCellId")
        return_path = (
            _ctu_path(topology, current, str(home), path_policy, None)
            if home and current
            else []
        )
        return_ticks = _edge_ticks(max(0, len(return_path) - 1), robot_type, physics, speed)

    congestion, congestion_evidence = _congestion_penalty(
        robot,
        task,
        pickup_path,
        delivery_path,
        state,
        float(weights["maxCongestionPenaltyTicks"]),
    )
    current_tick = int(state.get("tick") or 0)
    age_ticks = max(0, current_tick - int(task.get("createdTick") or 0))
    age_bonus = min(float(weights["maxAgeBonusTicks"]), age_ticks * float(weights["ageWeight"]))
    score = (
        travel_ticks * float(weights["travelTimeWeight"])
        + congestion * float(weights["congestionWeight"])
        + return_ticks * float(weights["returnWeight"])
        + float(weights["handlingTicks"])
        - age_bonus
    )
    priority = 0 if task.get("urgent") else int(task.get("priority") or 999)
    evidence = {
        "algorithmVersion": ALGORITHM_VERSION,
        "estimatedTravelTicks": travel_ticks,
        "estimatedReturnReleaseTicks": return_ticks,
        "congestionPenaltyTicks": congestion,
        "taskAgeTicks": age_ticks,
        "ageBonusTicks": age_bonus,
        "handlingTicks": float(weights["handlingTicks"]),
        "score": score,
        "congestionEvidence": congestion_evidence,
        "observableAtTick": current_tick,
    }
    return CandidateScore(str(robot["id"]), str(task["id"]), priority, score, evidence)


def throughput_aware_assign(
    robots: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    topology: dict[str, Any],
    path_policy: str,
    state: dict[str, Any],
    config: dict[str, Any],
    extra_blocked: set[tuple[str, int, int]] | None = None,
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    available = sorted(
        (
            robot
            for robot in robots
            if robot.get("status") == "idle" and not robot.get("failed")
        ),
        key=lambda item: str(item.get("id")),
    )
    remaining = sorted(_ready_tasks(tasks), key=lambda item: str(item.get("id")))
    matches: list[tuple[str, str]] = []
    evidence: list[dict[str, Any]] = []
    while available and remaining:
        candidates: list[CandidateScore] = []
        for robot in available:
            for task in remaining:
                if robot.get("type") != task.get("robotType"):
                    continue
                if task.get("requestedVehicleId") and robot.get("id") != task.get("requestedVehicleId"):
                    continue
                if len(task.get("lines") or []) > int(robot.get("capacity") or 1):
                    continue
                candidate = score_candidate(
                    robot,
                    task,
                    topology,
                    path_policy,
                    state,
                    config,
                    extra_blocked,
                )
                if candidate is not None:
                    candidates.append(candidate)
        if not candidates:
            break
        best = min(candidates, key=lambda item: (item.priority, item.score, item.robot_id, item.task_id))
        matches.append((best.robot_id, best.task_id))
        evidence.append({"robotId": best.robot_id, "taskId": best.task_id, **best.evidence})
        available = [robot for robot in available if str(robot.get("id")) != best.robot_id]
        remaining = [task for task in remaining if str(task.get("id")) != best.task_id]
    return matches, evidence
