from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from munkres import Munkres

from .pathfinding import shortest_path


@dataclass
class AssignmentInput:
    robot: dict[str, Any]
    task: dict[str, Any]
    score: float


def task_cost(robot: dict[str, Any], task: dict[str, Any], topology: dict[str, Any], path_policy: str) -> float:
    path = shortest_path(topology, robot["currentNode"], task["sourceNode"], path_policy)
    battery_penalty = max(0.0, 50.0 - robot["battery"]) * 0.05
    zone_penalty = 0.2 if robot["type"] != task["robotType"] else 0.0
    return len(path) + battery_penalty + zone_penalty


def greedy_assign(
    robots: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    topology: dict[str, Any],
    path_policy: str,
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
                score = task_cost(robot, task, topology, path_policy)
                candidate = AssignmentInput(robot, task, score)
                if best is None or candidate.score < best.score:
                    best = candidate
        if best is None:
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
) -> list[tuple[str, str]]:
    idle_robots = [robot for robot in robots if robot["status"] == "idle"]
    pending_tasks = [task for task in tasks if task["status"] == "pending"]
    compatible_tasks = [task for task in pending_tasks if any(robot["type"] == task["robotType"] for robot in idle_robots)]
    if not idle_robots or not compatible_tasks:
        return []

    max_dim = max(len(idle_robots), len(compatible_tasks))
    matrix = [[9999.0 for _ in range(max_dim)] for _ in range(max_dim)]
    for i, robot in enumerate(idle_robots):
        for j, task in enumerate(compatible_tasks):
            if robot["type"] == task["robotType"]:
                matrix[i][j] = task_cost(robot, task, topology, path_policy)

    munkres = Munkres()
    indexes = munkres.compute(matrix)
    matches: list[tuple[str, str]] = []
    for row, col in indexes:
        if row < len(idle_robots) and col < len(compatible_tasks):
            if matrix[row][col] < 9999.0:
                matches.append((idle_robots[row]["id"], compatible_tasks[col]["id"]))
    return matches


def assign_tasks(
    policy: str,
    robots: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    topology: dict[str, Any],
    path_policy: str,
) -> list[tuple[str, str]]:
    if policy == "hungarian":
        return hungarian_assign(robots, tasks, topology, path_policy)
    return greedy_assign(robots, tasks, topology, path_policy)
