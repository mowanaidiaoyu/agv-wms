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
    battery_penalty = max(0.0, 50.0 - robot["battery"]) * 0.05
    zone_penalty = 0.2 if robot["type"] != task["robotType"] else 0.0
    return len(path) + battery_penalty + zone_penalty


def greedy_assign(
    robots: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    topology: dict[str, Any],
    path_policy: str,
    extra_blocked: set[tuple[str, int, int]] | None = None,
) -> list[tuple[str, str]]:
    available = [robot for robot in robots if robot["status"] == "idle"]
    remaining = [task for task in tasks if task["status"] == "pending"]
    matches: list[tuple[str, str]] = []
    while available and remaining:
        best: AssignmentInput | None = None
        for robot in available:
            for task in remaining:
                if robot["type"] != task["robotType"]:
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
    pending_tasks = [task for task in tasks if task["status"] == "pending"]
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
