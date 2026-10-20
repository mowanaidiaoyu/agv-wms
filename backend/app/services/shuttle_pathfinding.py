from __future__ import annotations

import heapq
import itertools
import math
import time
from typing import Any

from .shuttle_topology import SHUTTLE_LEVELS, add_lift_neighbors


TRACK_SPEED_CELLS_PER_SECOND = 2.0
TURN_TIME_SECONDS = 0.25


def _direction(source: dict[str, Any], target: dict[str, Any]) -> str | None:
    if source["shuttle_level"] != target["shuttle_level"]:
        return None
    if source["row"] != target["row"]:
        return "row"
    if source["col"] != target["col"]:
        return "col"
    return None


def _edge_cost(
    source: dict[str, Any],
    target: dict[str, Any],
    metadata: dict[str, Any],
    previous_direction: str | None,
) -> tuple[float, float, float]:
    if metadata["edge_type"] == "lift":
        lift = metadata["lift"]
        level_delta = abs(int(source["shuttle_level"]) - int(target["shuttle_level"]))
        travel = level_delta * float(lift["travel_time_per_level_seconds"])
        return (
            float(lift["enter_time_seconds"]) + travel + float(lift["exit_time_seconds"]),
            0.0,
            0.0,
        )
    distance = math.hypot(float(source["row"] - target["row"]), float(source["col"] - target["col"]))
    direction = _direction(source, target)
    turn = TURN_TIME_SECONDS if previous_direction and direction != previous_direction else 0.0
    return distance / TRACK_SPEED_CELLS_PER_SECOND + turn, distance, turn


def _heuristic(node: dict[str, Any], goal: dict[str, Any], lifts: list[dict[str, Any]]) -> float:
    horizontal = (abs(int(node["row"]) - int(goal["row"])) + abs(int(node["col"]) - int(goal["col"]))) / TRACK_SPEED_CELLS_PER_SECOND
    level_delta = abs(int(node["shuttle_level"]) - int(goal["shuttle_level"]))
    if level_delta == 0 or not lifts:
        return horizontal
    minimum_vertical = min(
        float(lift["enter_time_seconds"])
        + level_delta * float(lift["travel_time_per_level_seconds"])
        + float(lift["exit_time_seconds"])
        for lift in lifts
    )
    # Horizontal distance ignores the detour to a lift. The result is therefore
    # a lower bound and remains admissible for A*.
    return horizontal + minimum_vertical


def plan_shuttle_path(
    graph: dict[str, dict[str, Any]],
    lifts: list[dict[str, Any]],
    start_node_id: str,
    end_node_id: str,
    algorithm: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    if algorithm not in {"astar", "dijkstra"}:
        raise ValueError(f"Unsupported shuttle path algorithm: {algorithm}")
    start = graph.get(start_node_id)
    goal = graph.get(end_node_id)
    if start is None or goal is None:
        missing = start_node_id if start is None else end_node_id
        return _failure(algorithm, f"Track node does not exist or is not traversable: {missing}", started)
    available_lifts = [lift for lift in lifts if lift.get("state", "idle") != "unavailable"]
    adjacency = add_lift_neighbors(graph, available_lifts)
    if start["shuttle_level"] != goal["shuttle_level"] and not available_lifts:
        return _failure(algorithm, "No verified shuttle lift is available for cross-level travel.", started)

    start_state = (start_node_id, None)
    sequence = itertools.count()
    frontier: list[tuple[float, float, int, str, str | None]] = [(0.0, 0.0, next(sequence), start_node_id, None)]
    costs: dict[tuple[str, str | None], float] = {start_state: 0.0}
    came_from: dict[tuple[str, str | None], tuple[tuple[str, str | None], dict[str, Any]]] = {}
    visited: set[tuple[str, str | None]] = set()
    final_state: tuple[str, str | None] | None = None

    while frontier:
        _, current_cost, _, current_id, previous_direction = heapq.heappop(frontier)
        state = (current_id, previous_direction)
        if state in visited or current_cost > costs.get(state, math.inf) + 1e-9:
            continue
        visited.add(state)
        if current_id == end_node_id:
            final_state = state
            break
        current = graph[current_id]
        for neighbor_id, metadata in adjacency.get(current_id, []):
            neighbor = graph[neighbor_id]
            edge_cost, distance, turn = _edge_cost(current, neighbor, metadata, previous_direction)
            direction = _direction(current, neighbor)
            next_direction = direction if direction is not None else previous_direction
            next_state = (neighbor_id, next_direction)
            new_cost = current_cost + edge_cost
            if new_cost + 1e-9 >= costs.get(next_state, math.inf):
                continue
            costs[next_state] = new_cost
            edge = {
                **metadata,
                "duration_seconds": edge_cost,
                "distance_cells": distance,
                "turn_time_seconds": turn,
            }
            came_from[next_state] = (state, edge)
            estimate = _heuristic(neighbor, goal, available_lifts) if algorithm == "astar" else 0.0
            heapq.heappush(frontier, (new_cost + estimate, new_cost, next(sequence), neighbor_id, next_direction))

    if final_state is None:
        return _failure(algorithm, "No legal shuttle track path connects the requested nodes.", started, len(visited))

    states = [final_state]
    edges: list[dict[str, Any]] = []
    while states[-1] != start_state:
        previous, edge = came_from[states[-1]]
        edges.append(edge)
        states.append(previous)
    states.reverse()
    edges.reverse()
    node_ids = [state[0] for state in states]

    elapsed = 0.0
    path_nodes: list[dict[str, Any]] = [{**graph[node_ids[0]], "arrival_seconds": 0.0, "phase": "start"}]
    path_edges: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    horizontal_distance = 0.0
    chosen_lifts: list[str] = []
    for index, edge in enumerate(edges):
        source = graph[node_ids[index]]
        target = graph[node_ids[index + 1]]
        start_seconds = elapsed
        elapsed += float(edge["duration_seconds"])
        if edge["edge_type"] == "lift":
            lift = edge["lift"]
            enter = float(lift["enter_time_seconds"])
            exit_time = float(lift["exit_time_seconds"])
            lift_start = start_seconds + enter
            arrive_level = elapsed - exit_time
            motion_start = lift_start
            motion_end = arrive_level
            chosen_lifts.append(str(lift["lift_id"]))
            transitions.append(
                {
                    "lift_id": lift["lift_id"],
                    "from_level": source["shuttle_level"],
                    "to_level": target["shuttle_level"],
                    "entrance_track_node": source["track_node_id"],
                    "exit_track_node": target["track_node_id"],
                    "enter_at_seconds": start_seconds,
                    "lift_start_seconds": lift_start,
                    "arrive_level_seconds": arrive_level,
                    "exit_at_seconds": elapsed,
                    "events": ["enter_lift", "lift_started", "arrive_level", "exit_lift"],
                }
            )
        else:
            motion_start = start_seconds + float(edge["turn_time_seconds"])
            motion_end = elapsed
            horizontal_distance += float(edge["distance_cells"])
        path_edges.append(
            {
                "from_track_node": source["track_node_id"],
                "to_track_node": target["track_node_id"],
                "edge_type": edge["edge_type"],
                "lift_id": edge.get("lift", {}).get("lift_id"),
                "duration_seconds": float(edge["duration_seconds"]),
                "start_seconds": start_seconds,
                "motion_start_seconds": motion_start,
                "motion_end_seconds": motion_end,
                "end_seconds": elapsed,
                "distance_cells": float(edge["distance_cells"]),
                "turn_time_seconds": float(edge["turn_time_seconds"]),
            }
        )
        path_nodes.append(
            {
                **target,
                "arrival_seconds": elapsed,
                "phase": "lift" if edge["edge_type"] == "lift" else "track",
            }
        )

    return {
        "algorithm": algorithm,
        "start": start,
        "destination": goal,
        "chosen_lift": chosen_lifts[0] if chosen_lifts else None,
        "path_nodes": path_nodes,
        "path_edges": path_edges,
        "lift_transitions": transitions,
        "horizontal_distance_cells": horizontal_distance,
        "estimated_duration_seconds": elapsed,
        "visited_states": len(visited),
        "planning_time_ms": (time.perf_counter() - started) * 1000.0,
        "result_status": "success",
        "failure_reason": None,
        "timing_source": "synthetic_demo",
        "cost_contract": {
            "time_unit": "seconds",
            "track_speed_cells_per_second": TRACK_SPEED_CELLS_PER_SECOND,
            "turn_time_seconds": TURN_TIME_SECONDS,
        },
    }

def _failure(
    algorithm: str,
    reason: str,
    started: float,
    visited_states: int = 0,
) -> dict[str, Any]:
    return {
        "algorithm": algorithm,
        "start": None,
        "destination": None,
        "chosen_lift": None,
        "path_nodes": [],
        "path_edges": [],
        "lift_transitions": [],
        "horizontal_distance_cells": 0.0,
        "estimated_duration_seconds": 0.0,
        "visited_states": visited_states,
        "planning_time_ms": (time.perf_counter() - started) * 1000.0,
        "result_status": "failed",
        "failure_reason": reason,
        "timing_source": "synthetic_demo",
        "cost_contract": {
            "time_unit": "seconds",
            "track_speed_cells_per_second": TRACK_SPEED_CELLS_PER_SECOND,
            "turn_time_seconds": TURN_TIME_SECONDS,
        },
    }

# End of the failure response contract.
