from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .pathfinding import shortest_path, shortest_path_shuttle


@dataclass
class AssignmentInput:
    robot: dict[str, Any]
    task: dict[str, Any]
    score: float


def task_cost(robot: dict[str, Any], task: dict[str, Any], topology: dict[str, Any]) -> float:
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
            "astar",
        )
    else:
        path = shortest_path(topology, robot["currentCellId"], source_cell, "astar", avoid_storage=True)
    if not path:
        return 9999.0
    battery_penalty = max(0.0, 50.0 - robot["battery"]) * 0.05
    zone_penalty = 0.2 if robot["type"] != task["robotType"] else 0.0
    return len(path) + battery_penalty + zone_penalty


def greedy_assign(
    robots: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    topology: dict[str, Any],
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
                score = task_cost(robot, task, topology)
                candidate = AssignmentInput(robot, task, score)
                if best is None or candidate.score < best.score:
                    best = candidate
        if best is None or best.score >= 9999.0:
            break
        matches.append((best.robot["id"], best.task["id"]))
        available = [robot for robot in available if robot["id"] != best.robot["id"]]
        remaining = [task for task in remaining if task["id"] != best.task["id"]]
    return matches


def assign_tasks(
    robots: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    topology: dict[str, Any],
) -> list[tuple[str, str]]:
    return greedy_assign(robots, tasks, topology)
