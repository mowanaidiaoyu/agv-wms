from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from munkres import Munkres

from .pathfinding import shortest_path, shortest_path_shuttle


@dataclass
class AssignmentInput:
    robot: dict[str, Any]
    task: dict[str, Any]
    score: float


def task_cost(
    robot: dict[str, Any],
    task: dict[str, Any],
    topology: dict[str, Any],
    path_policy: str,
    extra_blocked: set[tuple[str, int, int]] | None = None,
) -> float:
    source_cell = task.get("sourceCellId")
    if not source_cell:
        return 9999.0
    if robot["type"] == "shuttle":
        path = shortest_path_shuttle(
            topology,
            robot["currentCellId"],
            robot.get("currentLayer", 1),
            source_cell,
            task.get("sourceLayer", 1),
            path_policy,
        )
    else:
        path = shortest_path(
            topology,
            robot["currentCellId"],
            source_cell,
            path_policy,
            avoid_storage=True,
            extra_blocked=extra_blocked,
        )
    if not path:
        return 9999.0
    delivery_cost = 0.0
    delivery_cell = task.get("deliveryCellId")
    task_lines = task.get("lines") or []
    delivery_start = task_lines[-1].get("cellId") if task_lines else source_cell
    if robot["type"] == "ctu" and delivery_cell and delivery_start:
        delivery_path = shortest_path(
            topology,
            delivery_start,
            delivery_cell,
            path_policy,
            avoid_storage=True,
        )
        if not delivery_path:
            return 9999.0
        delivery_cost = max(0, len(delivery_path) - 1)
    battery_penalty = max(0.0, 50.0 - robot["battery"]) * 0.05
    zone_penalty = 0.2 if robot["type"] != task["robotType"] else 0.0
    urgent_bonus = 18.0 if task.get("urgent") else 0.0
    aging_bonus = min(18.0, max(0.0, float(task.get("waitAgeTicks", 0))) * 0.12)
    return len(path) + delivery_cost + battery_penalty + zone_penalty - urgent_bonus - aging_bonus


def _assignable(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤掉被前置任务阻塞的任务（blockedBy 未全部完成不可派，如等待倒库让位的取货任务）。"""
    by_id = {task["id"]: task for task in tasks}

    def ready(task: dict[str, Any]) -> bool:
        return all(
            (dep_task is None or dep_task.get("status") == "completed")
            for dep_task in (by_id.get(dep_id) for dep_id in (task.get("blockedBy") or []))
        )

    return [task for task in tasks if ready(task)]


def greedy_assign(
    robots: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    topology: dict[str, Any],
    path_policy: str,
    extra_blocked: set[tuple[str, int, int]] | None = None,
) -> list[tuple[str, str]]:
    available = [robot for robot in robots if robot["status"] == "idle"]
    remaining = [task for task in _assignable(tasks) if task["status"] == "pending"]
    matches: list[tuple[str, str]] = []
    while available and remaining:
        best: AssignmentInput | None = None
        for robot in available:
            for task in remaining:
                if robot["type"] != task["robotType"]:
                    continue
                # 出货位指定的车辆：任务只派给该四向车。
                if task.get("requestedVehicleId") and robot["id"] != task["requestedVehicleId"]:
                    continue
                score = task_cost(robot, task, topology, path_policy, extra_blocked)
                candidate = AssignmentInput(robot, task, score)
                if best is None or candidate.score < best.score:
                    best = candidate
        if best is None or best.score >= 9999.0:
            break
        matches.append((best.robot["id"], best.task["id"]))
        available = [robot for robot in available if robot["id"] != best.robot["id"]]
        remaining = [task for task in remaining if task["id"] != best.task["id"]]
    return matches


def hungarian_assign(
    robots: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    topology: dict[str, Any],
    path_policy: str,
    extra_blocked: set[tuple[str, int, int]] | None = None,
) -> list[tuple[str, str]]:
    idle_robots = [robot for robot in robots if robot["status"] == "idle"]
    pending_tasks = [task for task in _assignable(tasks) if task["status"] == "pending"]
    compatible_tasks = [task for task in pending_tasks if any(robot["type"] == task["robotType"] for robot in idle_robots)]
    if not idle_robots or not compatible_tasks:
        return []

    max_dim = max(len(idle_robots), len(compatible_tasks))
    matrix = [[9999.0 for _ in range(max_dim)] for _ in range(max_dim)]
    for row_index, robot in enumerate(idle_robots):
        for col_index, task in enumerate(compatible_tasks):
            if robot["type"] == task["robotType"]:
                matrix[row_index][col_index] = task_cost(robot, task, topology, path_policy, extra_blocked)

    indexes = Munkres().compute(matrix)
    matches: list[tuple[str, str]] = []
    for row_index, col_index in indexes:
        if row_index < len(idle_robots) and col_index < len(compatible_tasks) and matrix[row_index][col_index] < 9999.0:
            matches.append((idle_robots[row_index]["id"], compatible_tasks[col_index]["id"]))
    return matches


def assign_tasks(
    policy: str,
    robots: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    topology: dict[str, Any],
    path_policy: str,
    extra_blocked: set[tuple[str, int, int]] | None = None,
) -> list[tuple[str, str]]:
    if policy == "hungarian":
        return hungarian_assign(robots, tasks, topology, path_policy, extra_blocked)
    return greedy_assign(robots, tasks, topology, path_policy, extra_blocked)
