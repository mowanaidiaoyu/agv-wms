from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import math
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable

from .shuttle_pathfinding import TRACK_SPEED_CELLS_PER_SECOND, TURN_TIME_SECONDS, plan_shuttle_path
from .shuttle_topology import SHUTTLE_COORDINATE_CONTRACT_REVISION


PLANNING_ALGORITHM_VERSION = "shuttle-prioritized-reservation-v1"
DEFAULT_TIME_STEP_SECONDS = 0.25


class ShuttleMultiPlanningError(ValueError):
    def __init__(
        self,
        reason: str,
        *,
        failure_code: str = "invalid_planning_request",
        vehicle_id: str | None = None,
        expanded_states: int | None = None,
        max_expanded_states: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.failure_code = failure_code
        self.vehicle_id = vehicle_id
        self.expanded_states = expanded_states
        self.max_expanded_states = max_expanded_states
        self.details = details or {}


@dataclass
class ConflictTracker:
    signatures: dict[str, set[tuple[Any, ...]]] = field(
        default_factory=lambda: {"node": set(), "edge": set(), "geometry": set()}
    )
    events: dict[tuple[Any, ...], dict[str, Any]] = field(default_factory=dict)

    def record(
        self,
        kind: str,
        vehicle_id: str,
        other_vehicle_id: str,
        time_step: int,
        resource_id: str,
        details: dict[str, Any],
    ) -> None:
        signature = (vehicle_id, other_vehicle_id, time_step, resource_id)
        self.signatures[kind].add(signature)
        key = (kind, *signature)
        event_type = {
            "node": "track_node_conflict_detected",
            "edge": "edge_swap_conflict_detected",
            "geometry": "geometric_conflict_detected",
        }[kind]
        self.events.setdefault(
            key,
            {
                "event_id": f"{event_type}-{vehicle_id}-{other_vehicle_id}-{time_step}-{resource_id}",
                "simulation_time_seconds": time_step * DEFAULT_TIME_STEP_SECONDS,
                "vehicle_ids": [other_vehicle_id, vehicle_id],
                "event_type": event_type,
                "resource_id": resource_id,
                "track_node_id": details.get("track_node_id"),
                "details": details,
                "resolution_status": "candidate_rejected",
            },
        )


def _seconds_to_ticks(seconds: float, time_step_seconds: float) -> int:
    if seconds < 0:
        raise ShuttleMultiPlanningError("Action duration cannot be negative")
    # Never round down a future timing value: the discrete reservation is a
    # conservative ceiling of the configured continuous duration.
    return max(0, math.ceil(seconds / time_step_seconds - 1e-12))


def _direction(source: dict[str, Any], target: dict[str, Any]) -> str | None:
    if source["shuttle_level"] != target["shuttle_level"]:
        return None
    if source["row"] != target["row"]:
        return "row"
    if source["col"] != target["col"]:
        return "col"
    return None


def _position(node: dict[str, Any]) -> tuple[float, float, float]:
    return float(node["world_x"]), float(node["world_y"]), float(node["world_z"])


def _pose(
    tick: int,
    position: tuple[float, float, float],
    *,
    node_id: str | None,
    action: str,
    lift_id: str | None = None,
    edge_from: str | None = None,
    edge_to: str | None = None,
    wait_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "time_step": tick,
        "world_x": position[0],
        "world_y": position[1],
        "world_z": position[2],
        "track_node_id": node_id,
        "action": action,
        "lift_id": lift_id,
        "edge_from": edge_from,
        "edge_to": edge_to,
        "wait_reason": wait_reason,
    }


def _minimum_relative_distance(
    first_start: dict[str, Any],
    first_end: dict[str, Any],
    second_start: dict[str, Any],
    second_end: dict[str, Any],
) -> float:
    relative = [
        float(first_start[key]) - float(second_start[key])
        for key in ("world_x", "world_y", "world_z")
    ]
    velocity = [
        (float(first_end[key]) - float(first_start[key]))
        - (float(second_end[key]) - float(second_start[key]))
        for key in ("world_x", "world_y", "world_z")
    ]
    velocity_squared = sum(value * value for value in velocity)
    alpha = 0.0
    if velocity_squared > 0:
        alpha = max(
            0.0,
            min(1.0, -sum(relative[index] * velocity[index] for index in range(3)) / velocity_squared),
        )
    return math.sqrt(sum((relative[index] + alpha * velocity[index]) ** 2 for index in range(3)))


def _pose_at(trajectory: list[dict[str, Any]], tick: int) -> dict[str, Any]:
    return trajectory[min(max(tick, 0), len(trajectory) - 1)]


def _candidate_conflict(
    samples: list[dict[str, Any]],
    reserved: list[dict[str, Any]],
    safety_distance: float,
) -> tuple[str, str, int, str, dict[str, Any]] | None:
    start_tick = int(samples[0]["time_step"])
    detected: dict[str, tuple[str, str, int, str, dict[str, Any]] | None] = {
        "node": None,
        "edge": None,
        "geometry": None,
    }
    for sample_index in range(1, len(samples)):
        tick = start_tick + sample_index
        current = samples[sample_index]
        previous = samples[sample_index - 1]
        for other in reserved:
            other_previous = _pose_at(other["trajectory"], tick - 1)
            other_current = _pose_at(other["trajectory"], tick)
            same_node = (
                current["track_node_id"] is not None
                and current["track_node_id"] == other_current["track_node_id"]
            )
            if same_node:
                node_id = str(current["track_node_id"])
                detected["node"] = detected["node"] or (
                    "node", other["vehicle_id"], tick, node_id, {"track_node_id": node_id}
                )
                continue
            reverse_edge = (
                current.get("edge_from")
                and current.get("edge_to")
                and other_current.get("edge_from") == current.get("edge_to")
                and other_current.get("edge_to") == current.get("edge_from")
            )
            if reverse_edge:
                resource = f"{current['edge_from']}<->{current['edge_to']}"
                detected["edge"] = detected["edge"] or (
                    "edge",
                    other["vehicle_id"],
                    tick,
                    resource,
                    {
                        "from_track_node": current["edge_from"],
                        "to_track_node": current["edge_to"],
                    },
                )
                continue
            minimum_distance = _minimum_relative_distance(
                previous, current, other_previous, other_current
            )
            if minimum_distance < safety_distance - 1e-9:
                resource = current.get("track_node_id") or current.get("edge_to") or "continuous-motion"
                detected["geometry"] = detected["geometry"] or (
                    "geometry",
                    other["vehicle_id"],
                    tick,
                    str(resource),
                    {
                        "track_node_id": current.get("track_node_id"),
                        "minimum_distance_cells": minimum_distance,
                        "required_distance_cells": safety_distance,
                    },
                )
    # Report the most specific discrete reservation conflict for the rejected
    # action. Geometry remains the fallback that protects between-tick motion.
    return detected["node"] or detected["edge"] or detected["geometry"]


def _wait_action(node: dict[str, Any], start_tick: int) -> dict[str, Any]:
    position = _position(node)
    return {
        "kind": "wait",
        "from_node": node["track_node_id"],
        "to_node": node["track_node_id"],
        "end_tick": start_tick + 1,
        "next_direction": None,
        "samples": [
            _pose(start_tick, position, node_id=node["track_node_id"], action="wait", wait_reason="track_reservation"),
            _pose(start_tick + 1, position, node_id=node["track_node_id"], action="wait", wait_reason="track_reservation"),
        ],
        "distance_cells": 0.0,
    }


def _track_action(
    source: dict[str, Any],
    target: dict[str, Any],
    start_tick: int,
    previous_direction: str | None,
    time_step_seconds: float,
) -> dict[str, Any]:
    direction = _direction(source, target)
    distance = math.hypot(float(source["row"] - target["row"]), float(source["col"] - target["col"]))
    move_ticks = _seconds_to_ticks(distance / TRACK_SPEED_CELLS_PER_SECOND, time_step_seconds)
    turn_ticks = _seconds_to_ticks(TURN_TIME_SECONDS, time_step_seconds) if previous_direction and direction != previous_direction else 0
    source_position = _position(source)
    target_position = _position(target)
    samples = [_pose(start_tick, source_position, node_id=source["track_node_id"], action="move")]
    for offset in range(1, turn_ticks + 1):
        samples.append(
            _pose(
                start_tick + offset,
                source_position,
                node_id=source["track_node_id"],
                action="turn",
            )
        )
    for move_index in range(1, move_ticks + 1):
        alpha = move_index / move_ticks
        position = tuple(
            source_position[index] + (target_position[index] - source_position[index]) * alpha
            for index in range(3)
        )
        samples.append(
            _pose(
                start_tick + turn_ticks + move_index,
                position,
                node_id=target["track_node_id"] if move_index == move_ticks else None,
                action="move",
                edge_from=source["track_node_id"],
                edge_to=target["track_node_id"],
            )
        )
    return {
        "kind": "track",
        "from_node": source["track_node_id"],
        "to_node": target["track_node_id"],
        "end_tick": start_tick + turn_ticks + move_ticks,
        "next_direction": direction,
        "samples": samples,
        "distance_cells": distance,
    }


def _last_lift_state(
    lift_id: str,
    lift_schedules: dict[str, list[dict[str, Any]]],
    initial_levels: dict[str, int],
) -> tuple[int, int, dict[str, Any] | None]:
    schedule = lift_schedules[lift_id]
    if not schedule:
        return 0, int(initial_levels[lift_id]), None
    previous = schedule[-1]
    return int(previous["exit_end_tick"]), int(previous["exit_level"]), previous


def _preview_lift_action(
    lift: dict[str, Any],
    source: dict[str, Any],
    target: dict[str, Any],
    arrival_tick: int,
    time_step_seconds: float,
    lift_schedules: dict[str, list[dict[str, Any]]],
    initial_levels: dict[str, int],
    vehicle_id: str,
) -> dict[str, Any]:
    lift_id = str(lift["lift_id"])
    last_end_tick, cabin_level, previous = _last_lift_state(lift_id, lift_schedules, initial_levels)
    source_level = int(source["shuttle_level"])
    target_level = int(target["shuttle_level"])
    per_level_ticks = _seconds_to_ticks(float(lift["travel_time_per_level_seconds"]), time_step_seconds)
    reposition_ticks = abs(cabin_level - source_level) * per_level_ticks
    reposition_start_tick = max(last_end_tick, arrival_tick - reposition_ticks)
    reposition_end_tick = reposition_start_tick + reposition_ticks
    enter_start_tick = max(arrival_tick, reposition_end_tick)
    enter_ticks = _seconds_to_ticks(float(lift["enter_time_seconds"]), time_step_seconds)
    transfer_ticks = abs(source_level - target_level) * per_level_ticks
    exit_ticks = _seconds_to_ticks(float(lift["exit_time_seconds"]), time_step_seconds)
    transfer_start_tick = enter_start_tick + enter_ticks
    transfer_end_tick = transfer_start_tick + transfer_ticks
    exit_end_tick = transfer_end_tick + exit_ticks
    reservation = {
        "lift_id": lift_id,
        "vehicle_id": vehicle_id,
        "entry_level": source_level,
        "exit_level": target_level,
        "request_tick": arrival_tick,
        "reposition_start_tick": reposition_start_tick,
        "reposition_end_tick": reposition_end_tick,
        "enter_start_tick": enter_start_tick,
        "transfer_start_tick": transfer_start_tick,
        "transfer_end_tick": transfer_end_tick,
        "exit_end_tick": exit_end_tick,
        "initial_cabin_level": cabin_level,
        "final_cabin_level": target_level,
        "previous_vehicle_id": previous.get("vehicle_id") if previous else None,
        "contention": bool(previous and enter_start_tick > arrival_tick),
    }
    source_position = _position(source)
    target_position = _position(target)
    samples = [_pose(arrival_tick, source_position, node_id=source["track_node_id"], action="wait")]
    for tick in range(arrival_tick + 1, enter_start_tick + 1):
        samples.append(
            _pose(
                tick,
                source_position,
                node_id=source["track_node_id"],
                action="wait",
                lift_id=lift_id,
                wait_reason="lift_resource",
            )
        )
    for tick in range(enter_start_tick + 1, transfer_start_tick + 1):
        samples.append(
            _pose(tick, source_position, node_id=source["track_node_id"], action="lift_enter", lift_id=lift_id)
        )
    for tick in range(transfer_start_tick + 1, transfer_end_tick + 1):
        alpha = (tick - transfer_start_tick) / max(1, transfer_ticks)
        position = tuple(
            source_position[index] + (target_position[index] - source_position[index]) * alpha
            for index in range(3)
        )
        samples.append(
            _pose(
                tick,
                position,
                node_id=target["track_node_id"] if tick == transfer_end_tick else None,
                action="lift",
                lift_id=lift_id,
                edge_from=source["track_node_id"],
                edge_to=target["track_node_id"],
            )
        )
    for tick in range(transfer_end_tick + 1, exit_end_tick + 1):
        samples.append(
            _pose(tick, target_position, node_id=target["track_node_id"], action="lift_exit", lift_id=lift_id)
        )
    return {
        "kind": "lift",
        "from_node": source["track_node_id"],
        "to_node": target["track_node_id"],
        "end_tick": exit_end_tick,
        "next_direction": None,
        "samples": samples,
        "distance_cells": 0.0,
        "lift_reservation": reservation,
    }


def _heuristic_ticks(
    node: dict[str, Any],
    goal: dict[str, Any],
    lifts: list[dict[str, Any]],
    time_step_seconds: float,
) -> int:
    horizontal_seconds = (
        abs(int(node["row"]) - int(goal["row"]))
        + abs(int(node["col"]) - int(goal["col"]))
    ) / TRACK_SPEED_CELLS_PER_SECOND
    result = _seconds_to_ticks(horizontal_seconds, time_step_seconds)
    level_delta = abs(int(node["shuttle_level"]) - int(goal["shuttle_level"]))
    if level_delta and lifts:
        minimum_lift_seconds = min(
            float(lift["enter_time_seconds"])
            + level_delta * float(lift["travel_time_per_level_seconds"])
            + float(lift["exit_time_seconds"])
            for lift in lifts
        )
        result += _seconds_to_ticks(minimum_lift_seconds, time_step_seconds)
    return result


def _goal_is_safe(
    node: dict[str, Any],
    arrival_tick: int,
    reserved: list[dict[str, Any]],
    safety_distance: float,
) -> bool:
    latest_tick = max((len(item["trajectory"]) - 1 for item in reserved), default=arrival_tick)
    if latest_tick <= arrival_tick:
        return True
    position = _position(node)
    samples = [
        _pose(tick, position, node_id=node["track_node_id"], action="wait")
        for tick in range(arrival_tick, latest_tick + 1)
    ]
    return _candidate_conflict(samples, reserved, safety_distance) is None


def _goal_window_is_safe(
    node: dict[str, Any],
    arrival_tick: int,
    hold_ticks: int,
    reserved: list[dict[str, Any]],
    safety_distance: float,
) -> bool:
    """Validate a finite service dwell without turning it into terminal occupancy."""
    position = _position(node)
    samples = [
        _pose(tick, position, node_id=node["track_node_id"], action="wait")
        for tick in range(arrival_tick, arrival_tick + hold_ticks + 1)
    ]
    return _candidate_conflict(samples, reserved, safety_distance) is None


def _service_action(
    node: dict[str, Any], start_tick: int, duration_ticks: int, action: str
) -> dict[str, Any]:
    position = _position(node)
    return {
        "kind": action,
        "from_node": node["track_node_id"],
        "to_node": node["track_node_id"],
        "end_tick": start_tick + duration_ticks,
        "next_direction": None,
        "samples": [
            _pose(tick, position, node_id=node["track_node_id"], action=action)
            for tick in range(start_tick, start_tick + duration_ticks + 1)
        ],
        "distance_cells": 0.0,
    }


def _reconstruct_actions(
    came_from: dict[tuple[Any, ...], tuple[tuple[Any, ...], dict[str, Any]]],
    state: tuple[Any, ...],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    while state in came_from:
        previous, action = came_from[state]
        actions.append(action)
        state = previous
    actions.reverse()
    return actions


def _search_vehicle(
    graph: dict[str, dict[str, Any]],
    lifts: list[dict[str, Any]],
    vehicle: dict[str, Any],
    reserved: list[dict[str, Any]],
    lift_schedules: dict[str, list[dict[str, Any]]],
    options: dict[str, Any],
    tracker: ConflictTracker,
    *,
    start_tick: int = 0,
    goal_hold_ticks: int = 0,
    persistent_goal: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    start_id = str(vehicle["start_track_node_id"])
    goal_id = str(vehicle["target_track_node_id"])
    start = graph[start_id]
    goal = graph[goal_id]
    time_step_seconds = float(options["time_step_seconds"])
    safety_distance = math.hypot(
        float(options["vehicle_length_cells"]), float(options["vehicle_width_cells"])
    ) + float(options["safety_margin_cells"])
    available_lifts = sorted(
        (lift for lift in lifts if lift.get("state", "idle") != "unavailable"),
        key=lambda item: str(item["lift_id"]),
    )[: int(options["max_lift_candidates"])]
    initial_levels = {key: int(value) for key, value in options["lift_initial_levels"].items()}
    sequence = itertools.count()
    start_state = (start_id, None, start_tick, False)
    first_priority = _heuristic_ticks(start, goal, available_lifts, time_step_seconds) if vehicle["algorithm"] == "astar" else 0
    frontier: list[tuple[int, int, int, tuple[Any, ...]]] = [
        (start_tick + first_priority, start_tick, next(sequence), start_state)
    ]
    visited: set[tuple[Any, ...]] = set()
    came_from: dict[tuple[Any, ...], tuple[tuple[Any, ...], dict[str, Any]]] = {}
    expanded = 0

    while frontier:
        _, tick, _, state = heapq.heappop(frontier)
        node_id, previous_direction, state_tick, used_lift = state
        if tick != state_tick or state in visited:
            continue
        visited.add(state)
        expanded += 1
        if expanded > int(options["max_expanded_states"]):
            raise ShuttleMultiPlanningError(
                f"Vehicle {vehicle['vehicle_id']} exceeded max_expanded_states={options['max_expanded_states']}. "
                "The configured search limit was reached before a safe path was found; this does not prove global infeasibility.",
                failure_code="search_limit_exceeded",
                vehicle_id=str(vehicle["vehicle_id"]),
                expanded_states=expanded,
                max_expanded_states=int(options["max_expanded_states"]),
            )
        current = graph[node_id]
        if node_id == goal_id:
            goal_safe = (
                _goal_is_safe(current, tick, reserved, safety_distance)
                if persistent_goal
                else _goal_window_is_safe(
                    current, tick, goal_hold_ticks, reserved, safety_distance
                )
            )
            if goal_safe and tick + goal_hold_ticks <= int(options["max_time_steps"]):
                return _reconstruct_actions(came_from, state), expanded
        if tick >= int(options["max_time_steps"]):
            continue

        candidates: list[dict[str, Any]] = [_wait_action(current, tick)]
        for neighbor_id in sorted(current["neighbors"]):
            candidates.append(
                _track_action(current, graph[neighbor_id], tick, previous_direction, time_step_seconds)
            )
        if int(current["shuttle_level"]) != int(goal["shuttle_level"]) and not used_lift:
            for lift in available_lifts:
                expected_entrance = lift["level_entrances"].get(f"L{current['shuttle_level']}")
                if expected_entrance != node_id:
                    continue
                target_id = lift["level_exits"].get(f"L{goal['shuttle_level']}")
                if target_id in graph:
                    candidates.append(
                        _preview_lift_action(
                            lift,
                            current,
                            graph[target_id],
                            tick,
                            time_step_seconds,
                            lift_schedules,
                            initial_levels,
                            str(vehicle["vehicle_id"]),
                        )
                    )

        for action in candidates:
            if int(action["end_tick"]) > int(options["max_time_steps"]):
                continue
            conflict = _candidate_conflict(action["samples"], reserved, safety_distance)
            if conflict:
                kind, other_vehicle_id, conflict_tick, resource_id, details = conflict
                tracker.record(
                    kind,
                    str(vehicle["vehicle_id"]),
                    other_vehicle_id,
                    conflict_tick,
                    resource_id,
                    details,
                )
                continue
            next_used_lift = bool(used_lift or action["kind"] == "lift")
            next_state = (
                action["to_node"],
                action["next_direction"] if action["kind"] == "track" else previous_direction,
                int(action["end_tick"]),
                next_used_lift,
            )
            if next_state in visited or next_state in came_from:
                continue
            came_from[next_state] = (state, action)
            next_node = graph[action["to_node"]]
            heuristic = 0
            if vehicle["algorithm"] == "astar":
                heuristic = _heuristic_ticks(next_node, goal, available_lifts, time_step_seconds)
            heapq.heappush(
                frontier,
                (int(action["end_tick"]) + heuristic, int(action["end_tick"]), next(sequence), next_state),
            )

    raise ShuttleMultiPlanningError(
        f"Vehicle {vehicle['vehicle_id']} found no safe plan within max_time_steps={options['max_time_steps']}",
        failure_code="reservation_conflict_unresolved",
        vehicle_id=str(vehicle["vehicle_id"]),
        expanded_states=expanded,
        details={"max_time_steps": int(options["max_time_steps"])},
    )


def _flatten_actions(
    start: dict[str, Any], actions: list[dict[str, Any]], time_step_seconds: float
) -> list[dict[str, Any]]:
    trajectory = [
        _pose(0, _position(start), node_id=start["track_node_id"], action="start")
    ]
    for action in actions:
        trajectory.extend(action["samples"][1:])
    for sample in trajectory:
        sample["time_seconds"] = sample["time_step"] * time_step_seconds
    return trajectory


def _wait_intervals(trajectory: list[dict[str, Any]], time_step_seconds: float) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    index = 1
    while index < len(trajectory):
        sample = trajectory[index]
        if sample["action"] != "wait" or not sample.get("wait_reason") or not sample.get("track_node_id"):
            index += 1
            continue
        reason = sample["wait_reason"]
        node_id = sample["track_node_id"]
        start_tick = index - 1
        while (
            index + 1 < len(trajectory)
            and trajectory[index + 1]["action"] == "wait"
            and trajectory[index + 1].get("wait_reason") == reason
            and trajectory[index + 1].get("track_node_id") == node_id
        ):
            index += 1
        end_tick = index
        intervals.append(
            {
                "track_node_id": node_id,
                "start_time_seconds": start_tick * time_step_seconds,
                "end_time_seconds": end_tick * time_step_seconds,
                "duration_seconds": (end_tick - start_tick) * time_step_seconds,
                "reason": reason,
            }
        )
        index += 1
    return intervals


def _external_trajectory(trajectory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = {
        "time_step",
        "time_seconds",
        "world_x",
        "world_y",
        "world_z",
        "track_node_id",
        "action",
        "lift_id",
        "edge_from",
        "edge_to",
    }
    return [{key: sample.get(key) for key in fields} for sample in trajectory]


def _lift_usage_payload(reservation: dict[str, Any], time_step_seconds: float) -> dict[str, Any]:
    def seconds(key: str) -> float:
        return int(reservation[key]) * time_step_seconds

    return {
        "lift_id": reservation["lift_id"],
        "vehicle_id": reservation["vehicle_id"],
        "entry_level": reservation["entry_level"],
        "exit_level": reservation["exit_level"],
        "request_time_seconds": seconds("request_tick"),
        "reposition_start_seconds": seconds("reposition_start_tick"),
        "reposition_end_seconds": seconds("reposition_end_tick"),
        "enter_start_seconds": seconds("enter_start_tick"),
        "transfer_start_seconds": seconds("transfer_start_tick"),
        "transfer_end_seconds": seconds("transfer_end_tick"),
        "exit_end_seconds": seconds("exit_end_tick"),
        "initial_cabin_level": reservation["initial_cabin_level"],
        "final_cabin_level": reservation["final_cabin_level"],
        "used_as_alternate": reservation.get("used_as_alternate", False),
        "reservation_status": "reserved",
    }


def _event(
    event_type: str,
    vehicle_ids: list[str],
    tick: int,
    time_step_seconds: float,
    *,
    resource_id: str | None = None,
    track_node_id: str | None = None,
    details: dict[str, Any] | None = None,
    resolution_status: str = "completed",
) -> dict[str, Any]:
    event_id = f"{event_type}-{'-'.join(vehicle_ids)}-{tick}-{resource_id or track_node_id or 'none'}"
    return {
        "event_id": event_id,
        "simulation_time_seconds": tick * time_step_seconds,
        "vehicle_ids": vehicle_ids,
        "event_type": event_type,
        "resource_id": resource_id,
        "track_node_id": track_node_id,
        "details": details or {},
        "resolution_status": resolution_status,
    }


def _selected_action_events(
    vehicle_id: str,
    actions: list[dict[str, Any]],
    wait_intervals: list[dict[str, Any]],
    time_step_seconds: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for wait in wait_intervals:
        start_tick = round(wait["start_time_seconds"] / time_step_seconds)
        end_tick = round(wait["end_time_seconds"] / time_step_seconds)
        details = {"duration_seconds": wait["duration_seconds"], "reason": wait["reason"]}
        events.append(_event("vehicle_wait_started", [vehicle_id], start_tick, time_step_seconds, track_node_id=wait["track_node_id"], details=details, resolution_status="resolved_by_wait"))
        events.append(_event("vehicle_wait_completed", [vehicle_id], end_tick, time_step_seconds, track_node_id=wait["track_node_id"], details=details, resolution_status="completed"))
    for action in actions:
        reservation = action.get("lift_reservation")
        if not reservation:
            continue
        lift_id = reservation["lift_id"]
        previous_vehicle_id = reservation.get("previous_vehicle_id")
        events.append(_event("lift_requested", [vehicle_id], reservation["request_tick"], time_step_seconds, resource_id=lift_id, details={"entry_level": reservation["entry_level"], "exit_level": reservation["exit_level"]}, resolution_status="reserved"))
        if reservation["contention"]:
            events.append(_event("lift_reservation_denied", [previous_vehicle_id, vehicle_id], reservation["request_tick"], time_step_seconds, resource_id=lift_id, details={"available_at_seconds": reservation["enter_start_tick"] * time_step_seconds}, resolution_status="resolved_by_wait"))
        if reservation["reposition_end_tick"] > reservation["reposition_start_tick"]:
            events.append(_event("lift_reposition_started", [vehicle_id], reservation["reposition_start_tick"], time_step_seconds, resource_id=lift_id, details={"from_level": reservation["initial_cabin_level"], "to_level": reservation["entry_level"]}, resolution_status="reserved"))
            events.append(_event("lift_reposition_completed", [vehicle_id], reservation["reposition_end_tick"], time_step_seconds, resource_id=lift_id, details={"level": reservation["entry_level"]}, resolution_status="completed"))
        events.extend(
            [
                _event("lift_reserved", [vehicle_id], reservation["reposition_start_tick"], time_step_seconds, resource_id=lift_id, details={"reservation_end_seconds": reservation["exit_end_tick"] * time_step_seconds}, resolution_status="reserved"),
                _event("lift_entered", [vehicle_id], reservation["enter_start_tick"], time_step_seconds, resource_id=lift_id),
                _event("lift_transfer_started", [vehicle_id], reservation["transfer_start_tick"], time_step_seconds, resource_id=lift_id),
                _event("lift_transfer_completed", [vehicle_id], reservation["transfer_end_tick"], time_step_seconds, resource_id=lift_id),
                _event("lift_exited", [vehicle_id], reservation["exit_end_tick"], time_step_seconds, resource_id=lift_id),
            ]
        )
    return events


def validate_shuttle_multi_plan(
    vehicles: list[dict[str, Any]],
    lift_reservations: list[dict[str, Any]],
    safety_distance: float,
) -> dict[str, Any]:
    counts = {
        "node_conflicts": 0,
        "edge_conflicts": 0,
        "geometric_conflicts": 0,
        "lift_resource_conflicts": 0,
        "timing_violations": 0,
    }
    for vehicle in vehicles:
        trajectory = vehicle["_trajectory"]
        expected_tick = 0
        for sample in trajectory:
            if int(sample["time_step"]) != expected_tick:
                counts["timing_violations"] += 1
            expected_tick += 1
    makespan = max((len(vehicle["_trajectory"]) - 1 for vehicle in vehicles), default=0)
    for first_index in range(len(vehicles)):
        for second_index in range(first_index + 1, len(vehicles)):
            first = vehicles[first_index]["_trajectory"]
            second = vehicles[second_index]["_trajectory"]
            for tick in range(makespan + 1):
                first_current = _pose_at(first, tick)
                second_current = _pose_at(second, tick)
                if (
                    first_current.get("track_node_id")
                    and first_current.get("track_node_id") == second_current.get("track_node_id")
                ):
                    counts["node_conflicts"] += 1
                    continue
                if tick == 0:
                    distance = _minimum_relative_distance(first_current, first_current, second_current, second_current)
                else:
                    first_previous = _pose_at(first, tick - 1)
                    second_previous = _pose_at(second, tick - 1)
                    reverse = (
                        first_current.get("edge_from")
                        and first_current.get("edge_to")
                        and second_current.get("edge_from") == first_current.get("edge_to")
                        and second_current.get("edge_to") == first_current.get("edge_from")
                    )
                    if reverse:
                        counts["edge_conflicts"] += 1
                        continue
                    distance = _minimum_relative_distance(first_previous, first_current, second_previous, second_current)
                if distance < safety_distance - 1e-9:
                    counts["geometric_conflicts"] += 1
    for first_index, first in enumerate(lift_reservations):
        if not (
            first["reposition_start_tick"] <= first["reposition_end_tick"]
            <= first["enter_start_tick"] <= first["transfer_start_tick"]
            <= first["transfer_end_tick"] <= first["exit_end_tick"]
        ):
            counts["timing_violations"] += 1
        for second in lift_reservations[first_index + 1 :]:
            if first["lift_id"] != second["lift_id"]:
                continue
            if max(first["reposition_start_tick"], second["reposition_start_tick"]) < min(
                first["exit_end_tick"], second["exit_end_tick"]
            ):
                counts["lift_resource_conflicts"] += 1
    counts["safe"] = not any(counts.values())
    return counts


def _validate_inputs(
    graph: dict[str, dict[str, Any]],
    lifts: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    options: dict[str, Any],
) -> None:
    if not 2 <= len(vehicles) <= min(4, int(options["max_vehicles"])):
        raise ShuttleMultiPlanningError(
            "Shuttle multi planning requires between 2 and 4 vehicles",
            failure_code="invalid_vehicle_count",
        )
    ids = [str(vehicle["vehicle_id"]) for vehicle in vehicles]
    if len(ids) != len(set(ids)):
        raise ShuttleMultiPlanningError(
            "vehicle_id values must be unique",
            failure_code="duplicate_vehicle_id",
        )
    for vehicle in vehicles:
        try:
            priority = int(vehicle["priority"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ShuttleMultiPlanningError(
                f"Vehicle {vehicle.get('vehicle_id', '')} has an invalid priority",
                failure_code="invalid_priority",
                vehicle_id=str(vehicle.get("vehicle_id", "")),
            ) from exc
        if not 1 <= priority <= 100:
            raise ShuttleMultiPlanningError(
                f"Vehicle {vehicle['vehicle_id']} priority must be between 1 and 100",
                failure_code="invalid_priority",
                vehicle_id=str(vehicle["vehicle_id"]),
            )
        if vehicle["algorithm"] not in {"astar", "dijkstra"}:
            raise ShuttleMultiPlanningError(
                f"Unsupported algorithm: {vehicle['algorithm']}",
                failure_code="invalid_planning_request",
                vehicle_id=str(vehicle["vehicle_id"]),
            )
        for field_name in ("start_track_node_id", "target_track_node_id"):
            node_id = str(vehicle[field_name])
            if node_id not in graph or not graph[node_id].get("traversable"):
                raise ShuttleMultiPlanningError(
                    f"Vehicle {vehicle['vehicle_id']} references a non-traversable ShuttleGraph node: {node_id}",
                    failure_code="unreachable_track",
                    vehicle_id=str(vehicle["vehicle_id"]),
                    details={"track_node_id": node_id, "endpoint": field_name},
                )
            if int(graph[node_id].get("shuttle_level", 0)) not in (1, 2, 3, 4):
                raise ShuttleMultiPlanningError(
                    f"Vehicle {vehicle['vehicle_id']} references a node with an invalid Shuttle level: {node_id}",
                    failure_code="unreachable_track",
                    vehicle_id=str(vehicle["vehicle_id"]),
                    details={"track_node_id": node_id, "endpoint": field_name},
                )
    available_lift_ids = {str(lift["lift_id"]) for lift in lifts if lift.get("state", "idle") != "unavailable"}
    for lift_id in available_lift_ids:
        if lift_id not in options["lift_initial_levels"]:
            raise ShuttleMultiPlanningError(
                f"Missing offline initial cabin level for {lift_id}",
                failure_code="invalid_planning_request",
                details={"lift_id": lift_id},
            )
    safety_distance = math.hypot(
        float(options["vehicle_length_cells"]), float(options["vehicle_width_cells"])
    ) + float(options["safety_margin_cells"])
    for first_index, first in enumerate(vehicles):
        first_node = graph[first["start_track_node_id"]]
        for second in vehicles[first_index + 1 :]:
            second_node = graph[second["start_track_node_id"]]
            if math.dist(_position(first_node), _position(second_node)) < safety_distance - 1e-9:
                raise ShuttleMultiPlanningError(
                    "Initial Shuttle geometry violates the safety distance",
                    failure_code="initial_occupancy_conflict",
                    details={
                        "vehicle_ids": [str(first["vehicle_id"]), str(second["vehicle_id"])],
                        "track_node_ids": [str(first["start_track_node_id"]), str(second["start_track_node_id"])],
                    },
                )

    destinations: dict[str, list[str]] = {}
    for vehicle in vehicles:
        destinations.setdefault(str(vehicle["target_track_node_id"]), []).append(str(vehicle["vehicle_id"]))
    for node_id, vehicle_ids in destinations.items():
        if len(vehicle_ids) > 1:
            raise ShuttleMultiPlanningError(
                f"Vehicles {', '.join(vehicle_ids)} share terminal destination {node_id}. "
                "The current goal occupancy rule is persistent; choose distinct legal destinations.",
                failure_code="duplicate_terminal_destination",
                details={
                    "track_node_id": node_id,
                    "vehicle_ids": vehicle_ids,
                    "goal_occupancy": "persistent",
                },
            )

    for first_index, first in enumerate(vehicles):
        first_node = graph[first["target_track_node_id"]]
        for second in vehicles[first_index + 1 :]:
            second_node = graph[second["target_track_node_id"]]
            if math.dist(_position(first_node), _position(second_node)) < safety_distance - 1e-9:
                raise ShuttleMultiPlanningError(
                    f"Terminal vehicle geometry overlaps for {first['vehicle_id']} and {second['vehicle_id']}",
                    failure_code="terminal_geometry_conflict",
                    details={
                        "vehicle_ids": [str(first["vehicle_id"]), str(second["vehicle_id"])],
                        "track_node_ids": [str(first["target_track_node_id"]), str(second["target_track_node_id"])],
                        "safety_distance_cells": safety_distance,
                        "world_distance": math.dist(_position(first_node), _position(second_node)),
                    },
                )

    for vehicle in vehicles:
        spatial = plan_shuttle_path(
            graph,
            lifts,
            str(vehicle["start_track_node_id"]),
            str(vehicle["target_track_node_id"]),
            str(vehicle["algorithm"]),
        )
        if spatial["result_status"] != "success":
            raise ShuttleMultiPlanningError(
                f"Vehicle {vehicle['vehicle_id']} has no legal ShuttleGraph route: {spatial['failure_reason']}",
                failure_code="unreachable_track",
                vehicle_id=str(vehicle["vehicle_id"]),
                expanded_states=int(spatial.get("visited_states", 0)),
                details={
                    "start_track_node_id": str(vehicle["start_track_node_id"]),
                    "target_track_node_id": str(vehicle["target_track_node_id"]),
                },
            )


def plan_shuttle_multi(
    graph: dict[str, dict[str, Any]],
    lifts: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    options: dict[str, Any],
    *,
    topology_id: str,
    topology_version: str,
    topology_revision: str,
    config_revision: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    _validate_inputs(graph, lifts, vehicles, options)
    ordered = sorted(vehicles, key=lambda item: (int(item["priority"]), str(item["vehicle_id"])))
    lift_schedules = {str(lift["lift_id"]): [] for lift in lifts if lift.get("state", "idle") != "unavailable"}
    reserved: list[dict[str, Any]] = []
    tracker = ConflictTracker()
    results: list[dict[str, Any]] = []
    all_lift_reservations: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    time_step_seconds = float(options["time_step_seconds"])
    safety_distance = math.hypot(
        float(options["vehicle_length_cells"]), float(options["vehicle_width_cells"])
    ) + float(options["safety_margin_cells"])

    for vehicle in ordered:
        actions, expanded = _search_vehicle(
            graph, lifts, vehicle, reserved, lift_schedules, options, tracker
        )
        trajectory = _flatten_actions(graph[vehicle["start_track_node_id"]], actions, time_step_seconds)
        waits = _wait_intervals(trajectory, time_step_seconds)
        lift_actions = [action for action in actions if action.get("lift_reservation")]
        baseline = plan_shuttle_path(
            graph,
            lifts,
            vehicle["start_track_node_id"],
            vehicle["target_track_node_id"],
            vehicle["algorithm"],
        )
        baseline_lift = baseline.get("chosen_lift")
        lift_usages = []
        for action in lift_actions:
            reservation = action["lift_reservation"]
            reservation["used_as_alternate"] = bool(
                baseline_lift and baseline_lift != reservation["lift_id"]
            )
            lift_schedules[reservation["lift_id"]].append(reservation)
            all_lift_reservations.append(reservation)
            lift_usages.append(_lift_usage_payload(reservation, time_step_seconds))
        path = [str(vehicle["start_track_node_id"])]
        horizontal_distance = 0.0
        for action in actions:
            horizontal_distance += float(action["distance_cells"])
            if action["kind"] != "wait" and action["to_node"] != path[-1]:
                path.append(action["to_node"])
        wait_time = sum(wait["duration_seconds"] for wait in waits)
        result = {
            "vehicle_id": str(vehicle["vehicle_id"]),
            "priority": int(vehicle["priority"]),
            "algorithm": vehicle["algorithm"],
            "start": graph[vehicle["start_track_node_id"]],
            "destination": graph[vehicle["target_track_node_id"]],
            "path": path,
            "timed_trajectory": _external_trajectory(trajectory),
            "wait_intervals": waits,
            "lift_usage": lift_usages,
            "arrival_time_seconds": (len(trajectory) - 1) * time_step_seconds,
            "wait_time_seconds": wait_time,
            "horizontal_distance_cells": horizontal_distance,
            "visited_states": expanded,
            "planning_status": "planned",
            "_trajectory": trajectory,
        }
        results.append(result)
        reserved.append(
            {
                "vehicle_id": str(vehicle["vehicle_id"]),
                "priority": int(vehicle["priority"]),
                "trajectory": trajectory,
            }
        )
        all_events.extend(
            _selected_action_events(str(vehicle["vehicle_id"]), actions, waits, time_step_seconds)
        )

    safety = validate_shuttle_multi_plan(results, all_lift_reservations, safety_distance)
    all_events = list(tracker.events.values()) + all_events
    all_events.sort(
        key=lambda item: (
            float(item["simulation_time_seconds"]),
            str(item["event_type"]),
            str(item["event_id"]),
        )
    )
    makespan = max((result["arrival_time_seconds"] for result in results), default=0.0)
    total_wait = sum(result["wait_time_seconds"] for result in results)
    usage_by_lift = {
        lift_id: [item for item in all_lift_reservations if item["lift_id"] == lift_id]
        for lift_id in ("shuttle-lift-a", "shuttle-lift-b")
    }
    unresolved = [] if safety["safe"] else [
        {key: value for key, value in safety.items() if key != "safe" and value}
    ]
    metrics = {
        "requested_vehicle_count": len(vehicles),
        "planned_vehicle_count": len(results),
        "makespan_seconds": makespan,
        "total_wait_time_seconds": total_wait,
        "average_wait_time_seconds": total_wait / len(vehicles),
        "detected_track_node_conflicts": len(tracker.signatures["node"]),
        "detected_edge_swap_conflicts": len(tracker.signatures["edge"]),
        "detected_geometric_conflicts": len(tracker.signatures["geometry"]),
        "lift_resource_contentions": sum(bool(item["contention"]) for item in all_lift_reservations),
        "lift_a_usage_count": len(usage_by_lift["shuttle-lift-a"]),
        "lift_b_usage_count": len(usage_by_lift["shuttle-lift-b"]),
        "lift_a_occupied_seconds": sum((item["exit_end_tick"] - item["reposition_start_tick"]) * time_step_seconds for item in usage_by_lift["shuttle-lift-a"]),
        "lift_b_occupied_seconds": sum((item["exit_end_tick"] - item["reposition_start_tick"]) * time_step_seconds for item in usage_by_lift["shuttle-lift-b"]),
        "alternate_lift_selections": sum(bool(item.get("used_as_alternate")) for item in all_lift_reservations),
        # Conflict avoidance is integrated into each bounded time-expanded
        # search.  No post-hoc path repair/replan pass is used in this version.
        "replans_used": 0,
        "unresolved_conflict_count": sum(value for key, value in safety.items() if key != "safe"),
        "planning_time_ms": (time.perf_counter() - started) * 1000.0,
    }
    canonical_input = json.dumps(
        {
            "topology_revision": topology_revision,
            "config_revision": config_revision,
            # The priority planner is intentionally insensitive to request
            # list order.  Canonicalise the frozen input the same way as the
            # search so equivalent requests receive the same traceable ID.
            "vehicles": ordered,
            "options": options,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    planning_id = f"shuttle-multi-{hashlib.sha256(canonical_input.encode()).hexdigest()[:16]}"
    external_results = [{key: value for key, value in result.items() if key != "_trajectory"} for result in results]
    return {
        "planning_id": planning_id,
        "topology_id": topology_id,
        "topology_version": topology_version,
        "topology_revision": topology_revision,
        "shuttle_config_revision": config_revision,
        "coordinate_contract_version": SHUTTLE_COORDINATE_CONTRACT_REVISION,
        "planning_algorithm_version": PLANNING_ALGORITHM_VERSION,
        "status": "planning_success" if safety["safe"] else "unsafe",
        "vehicles": external_results,
        "events": all_events,
        "metrics": metrics,
        "safety_validation": safety,
        "unresolved_conflicts": unresolved,
        "failure_reason": None if safety["safe"] else "Final safety validation detected unresolved conflicts.",
        "vehicle_initial_config": vehicles,
        "lift_initial_state": options["lift_initial_levels"],
        "time_contract": {
            "time_unit": "seconds",
            "internal_time_unit": "integer_tick",
            "time_step_seconds": time_step_seconds,
            "interval_semantics": "half_open_[start,end)",
            "duration_rounding": "ceiling_never_round_down",
            "track_speed_cells_per_second": TRACK_SPEED_CELLS_PER_SECOND,
            "turn_time_seconds": TURN_TIME_SECONDS,
            "timing_source": "synthetic_demo",
            "goal_occupancy": "persistent",
            "vehicle_length_cells": options["vehicle_length_cells"],
            "vehicle_width_cells": options["vehicle_width_cells"],
            "safety_distance_cells": safety_distance,
            "search_limits": {
                "max_vehicles": options["max_vehicles"],
                "max_time_steps": options["max_time_steps"],
                "max_expanded_states": options["max_expanded_states"],
                "max_lift_candidates": options["max_lift_candidates"],
                "max_replans": options["max_replans"],
            },
        },
    }


def plan_shuttle_multi_transfer(
    graph: dict[str, dict[str, Any]],
    lifts: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    options: dict[str, Any],
    *,
    topology_id: str,
    topology_version: str,
    topology_revision: str,
    config_revision: str,
    pickup_service_seconds: float = 1.0,
    drop_service_seconds: float = 1.0,
) -> dict[str, Any]:
    """Plan start -> pickup -> drop as one prioritized reservation problem.

    The two service dwells are part of the reserved trajectory.  The pickup is
    a finite waypoint; the final drop node keeps the existing persistent goal
    occupancy rule.  Lift schedules are committed only after both legs of a
    vehicle have succeeded.
    """
    started = time.perf_counter()
    planning_vehicles = [
        {
            "vehicle_id": item["vehicle_id"],
            "start_track_node_id": item["start_track_node_id"],
            "target_track_node_id": item["target_track_node_id"],
            "priority": item["priority"],
            "algorithm": item["algorithm"],
        }
        for item in vehicles
    ]
    _validate_inputs(graph, lifts, planning_vehicles, options)
    for vehicle in vehicles:
        pickup_id = str(vehicle["pickup_track_node_id"])
        if pickup_id not in graph or not graph[pickup_id].get("traversable"):
            raise ShuttleMultiPlanningError(
                f"Vehicle {vehicle['vehicle_id']} references a non-traversable pickup node: {pickup_id}",
                failure_code="unreachable_track",
                vehicle_id=str(vehicle["vehicle_id"]),
                details={"track_node_id": pickup_id, "endpoint": "pickup_track_node_id"},
            )
        for start_id, goal_id, stage in (
            (vehicle["start_track_node_id"], pickup_id, "to_pickup"),
            (pickup_id, vehicle["target_track_node_id"], "to_dropoff"),
        ):
            spatial = plan_shuttle_path(graph, lifts, start_id, goal_id, vehicle["algorithm"])
            if spatial["result_status"] != "success":
                raise ShuttleMultiPlanningError(
                    f"Vehicle {vehicle['vehicle_id']} has no legal {stage} route: "
                    f"{spatial.get('failure_reason') or 'unreachable track'}",
                    failure_code="unreachable_track",
                    vehicle_id=str(vehicle["vehicle_id"]),
                    expanded_states=int(spatial.get("visited_states", 0)),
                    details={"stage": stage, "start_track_node_id": start_id, "target_track_node_id": goal_id},
                )

    ordered = sorted(vehicles, key=lambda item: (int(item["priority"]), str(item["vehicle_id"])))
    lift_schedules = {
        str(lift["lift_id"]): [] for lift in lifts if lift.get("state", "idle") != "unavailable"
    }
    reserved: list[dict[str, Any]] = []
    tracker = ConflictTracker()
    results: list[dict[str, Any]] = []
    all_lift_reservations: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    time_step_seconds = float(options["time_step_seconds"])
    pickup_ticks = _seconds_to_ticks(pickup_service_seconds, time_step_seconds)
    drop_ticks = _seconds_to_ticks(drop_service_seconds, time_step_seconds)
    safety_distance = math.hypot(
        float(options["vehicle_length_cells"]), float(options["vehicle_width_cells"])
    ) + float(options["safety_margin_cells"])

    for vehicle in ordered:
        working_schedules = deepcopy(lift_schedules)
        first_vehicle = {
            **vehicle,
            "target_track_node_id": vehicle["pickup_track_node_id"],
        }
        first_actions, first_expanded = _search_vehicle(
            graph,
            lifts,
            first_vehicle,
            reserved,
            working_schedules,
            options,
            tracker,
            goal_hold_ticks=pickup_ticks,
            persistent_goal=False,
        )
        first_end_tick = int(first_actions[-1]["end_tick"]) if first_actions else 0
        pick_action = _service_action(
            graph[vehicle["pickup_track_node_id"]], first_end_tick, pickup_ticks, "pick_service"
        )
        for action in first_actions:
            reservation = action.get("lift_reservation")
            if reservation:
                working_schedules[reservation["lift_id"]].append(reservation)

        second_vehicle = {
            **vehicle,
            "start_track_node_id": vehicle["pickup_track_node_id"],
        }
        second_actions, second_expanded = _search_vehicle(
            graph,
            lifts,
            second_vehicle,
            reserved,
            working_schedules,
            options,
            tracker,
            start_tick=int(pick_action["end_tick"]),
            persistent_goal=True,
        )
        second_end_tick = (
            int(second_actions[-1]["end_tick"])
            if second_actions
            else int(pick_action["end_tick"])
        )
        drop_action = _service_action(
            graph[vehicle["target_track_node_id"]], second_end_tick, drop_ticks, "drop_service"
        )
        actions = [*first_actions, pick_action, *second_actions, drop_action]
        trajectory = _flatten_actions(
            graph[vehicle["start_track_node_id"]], actions, time_step_seconds
        )
        waits = _wait_intervals(trajectory, time_step_seconds)
        lift_usages: list[dict[str, Any]] = []
        vehicle_lift_reservations: list[dict[str, Any]] = []
        for action in [*first_actions, *second_actions]:
            reservation = action.get("lift_reservation")
            if not reservation:
                continue
            reservation["used_as_alternate"] = False
            if action in second_actions:
                working_schedules[reservation["lift_id"]].append(reservation)
            vehicle_lift_reservations.append(reservation)
            lift_usages.append(_lift_usage_payload(reservation, time_step_seconds))
        lift_schedules = working_schedules
        all_lift_reservations.extend(vehicle_lift_reservations)

        path = [str(vehicle["start_track_node_id"])]
        horizontal_distance = 0.0
        for action in actions:
            horizontal_distance += float(action["distance_cells"])
            if action["kind"] not in {"wait", "pick_service", "drop_service"} and action["to_node"] != path[-1]:
                path.append(action["to_node"])
        wait_time = sum(wait["duration_seconds"] for wait in waits)
        result = {
            "vehicle_id": str(vehicle["vehicle_id"]),
            "priority": int(vehicle["priority"]),
            "algorithm": vehicle["algorithm"],
            "start": graph[vehicle["start_track_node_id"]],
            "pickup": graph[vehicle["pickup_track_node_id"]],
            "destination": graph[vehicle["target_track_node_id"]],
            "source_location_id": vehicle.get("source_location_id"),
            "destination_location_id": vehicle.get("destination_location_id"),
            "sku_id": vehicle.get("sku_id"),
            "quantity": vehicle.get("quantity"),
            "path": path,
            "timed_trajectory": _external_trajectory(trajectory),
            "wait_intervals": waits,
            "lift_usage": lift_usages,
            "arrival_time_seconds": (len(trajectory) - 1) * time_step_seconds,
            "wait_time_seconds": wait_time,
            "horizontal_distance_cells": horizontal_distance,
            "visited_states": first_expanded + second_expanded,
            "planning_status": "planned",
            "pickup_service_start_seconds": first_end_tick * time_step_seconds,
            "pickup_service_end_seconds": int(pick_action["end_tick"]) * time_step_seconds,
            "drop_service_start_seconds": second_end_tick * time_step_seconds,
            "drop_service_end_seconds": int(drop_action["end_tick"]) * time_step_seconds,
            "_trajectory": trajectory,
        }
        results.append(result)
        reserved.append(
            {
                "vehicle_id": str(vehicle["vehicle_id"]),
                "priority": int(vehicle["priority"]),
                "trajectory": trajectory,
            }
        )
        all_events.extend(
            _selected_action_events(str(vehicle["vehicle_id"]), actions, waits, time_step_seconds)
        )

    safety = validate_shuttle_multi_plan(results, all_lift_reservations, safety_distance)
    all_events = list(tracker.events.values()) + all_events
    all_events.sort(key=lambda item: (float(item["simulation_time_seconds"]), str(item["event_type"]), str(item["event_id"])))
    makespan = max((item["arrival_time_seconds"] for item in results), default=0.0)
    total_wait = sum(item["wait_time_seconds"] for item in results)
    usage_by_lift = {
        lift_id: [item for item in all_lift_reservations if item["lift_id"] == lift_id]
        for lift_id in ("shuttle-lift-a", "shuttle-lift-b")
    }
    unresolved = [] if safety["safe"] else [
        {key: value for key, value in safety.items() if key != "safe" and value}
    ]
    canonical_input = json.dumps(
        {
            "topology_revision": topology_revision,
            "config_revision": config_revision,
            "vehicles": ordered,
            "options": options,
            "pickup_service_seconds": pickup_service_seconds,
            "drop_service_seconds": drop_service_seconds,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    external_results = [
        {key: value for key, value in result.items() if key != "_trajectory"}
        for result in results
    ]
    return {
        "planning_id": f"shuttle-multi-transfer-{hashlib.sha256(canonical_input.encode()).hexdigest()[:16]}",
        "topology_id": topology_id,
        "topology_version": topology_version,
        "topology_revision": topology_revision,
        "shuttle_config_revision": config_revision,
        "coordinate_contract_version": SHUTTLE_COORDINATE_CONTRACT_REVISION,
        "planning_algorithm_version": f"{PLANNING_ALGORITHM_VERSION}-multi-stage-v1",
        "status": "planning_success" if safety["safe"] else "unsafe",
        "vehicles": external_results,
        "events": all_events,
        "metrics": {
            "requested_vehicle_count": len(vehicles),
            "planned_vehicle_count": len(results),
            "makespan_seconds": makespan,
            "total_wait_time_seconds": total_wait,
            "average_wait_time_seconds": total_wait / len(vehicles),
            "detected_track_node_conflicts": len(tracker.signatures["node"]),
            "detected_edge_swap_conflicts": len(tracker.signatures["edge"]),
            "detected_geometric_conflicts": len(tracker.signatures["geometry"]),
            "lift_resource_contentions": sum(bool(item["contention"]) for item in all_lift_reservations),
            "lift_a_usage_count": len(usage_by_lift["shuttle-lift-a"]),
            "lift_b_usage_count": len(usage_by_lift["shuttle-lift-b"]),
            "lift_a_occupied_seconds": sum((item["exit_end_tick"] - item["reposition_start_tick"]) * time_step_seconds for item in usage_by_lift["shuttle-lift-a"]),
            "lift_b_occupied_seconds": sum((item["exit_end_tick"] - item["reposition_start_tick"]) * time_step_seconds for item in usage_by_lift["shuttle-lift-b"]),
            "alternate_lift_selections": 0,
            "replans_used": 0,
            "unresolved_conflict_count": sum(value for key, value in safety.items() if key != "safe"),
            "planning_time_ms": (time.perf_counter() - started) * 1000.0,
        },
        "safety_validation": safety,
        "unresolved_conflicts": unresolved,
        "failure_reason": None if safety["safe"] else "Final safety validation detected unresolved conflicts.",
        "vehicle_initial_config": planning_vehicles,
        "lift_initial_state": options["lift_initial_levels"],
        "time_contract": {
            "time_unit": "seconds",
            "internal_time_unit": "integer_tick",
            "time_step_seconds": time_step_seconds,
            "interval_semantics": "half_open_[start,end)",
            "duration_rounding": "ceiling_never_round_down",
            "track_speed_cells_per_second": TRACK_SPEED_CELLS_PER_SECOND,
            "turn_time_seconds": TURN_TIME_SECONDS,
            "timing_source": "synthetic_demo",
            "goal_occupancy": "persistent",
            "vehicle_length_cells": options["vehicle_length_cells"],
            "vehicle_width_cells": options["vehicle_width_cells"],
            "safety_distance_cells": safety_distance,
            "search_limits": {
                "max_vehicles": options["max_vehicles"],
                "max_time_steps": options["max_time_steps"],
                "max_expanded_states": options["max_expanded_states"],
                "max_lift_candidates": options["max_lift_candidates"],
                "max_replans": options["max_replans"],
            },
        },
        "transfer_contract": {
            "version": "shuttle-multi-transfer-service-v1",
            "pickup_service_seconds": pickup_ticks * time_step_seconds,
            "drop_service_seconds": drop_ticks * time_step_seconds,
            "pickup_occupancy": "finite_service_window",
            "dropoff_occupancy": "persistent_after_service",
            "inventory_effect": "pick/drop_applied_by_persisted_runtime_only",
        },
    }


def failed_shuttle_multi_response(
    *,
    topology_id: str,
    topology_version: str,
    topology_revision: str,
    config_revision: str,
    vehicles: list[dict[str, Any]],
    options: dict[str, Any],
    reason: str,
    started: float | None = None,
    failure_code: str = "invalid_planning_request",
    vehicle_id: str | None = None,
    expanded_states: int | None = None,
    max_expanded_states: int | None = None,
    failure_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    elapsed_ms = (time.perf_counter() - started) * 1000.0 if started else 0.0
    safety = {
        "node_conflicts": 0,
        "edge_conflicts": 0,
        "geometric_conflicts": 0,
        "lift_resource_conflicts": 0,
        "timing_violations": 0,
        "safe": False,
    }
    canonical_input = json.dumps(
        {
            "topology_revision": topology_revision,
            "config_revision": config_revision,
            "vehicles": sorted(
                vehicles,
                key=lambda item: (int(item.get("priority", 1)), str(item.get("vehicle_id", ""))),
            ),
            "options": options,
            "failure_reason": reason,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "planning_id": f"shuttle-multi-failed-{hashlib.sha256(canonical_input.encode()).hexdigest()[:16]}",
        "topology_id": topology_id,
        "topology_version": topology_version,
        "topology_revision": topology_revision,
        "shuttle_config_revision": config_revision,
        "coordinate_contract_version": SHUTTLE_COORDINATE_CONTRACT_REVISION,
        "planning_algorithm_version": PLANNING_ALGORITHM_VERSION,
        "status": "failed",
        "vehicles": [],
        "events": [],
        "metrics": {
            "requested_vehicle_count": len(vehicles),
            "planned_vehicle_count": 0,
            "makespan_seconds": 0,
            "total_wait_time_seconds": 0,
            "average_wait_time_seconds": 0,
            "detected_track_node_conflicts": 0,
            "detected_edge_swap_conflicts": 0,
            "detected_geometric_conflicts": 0,
            "lift_resource_contentions": 0,
            "lift_a_usage_count": 0,
            "lift_b_usage_count": 0,
            "lift_a_occupied_seconds": 0,
            "lift_b_occupied_seconds": 0,
            "alternate_lift_selections": 0,
            "replans_used": 0,
            "unresolved_conflict_count": 0,
            "planning_time_ms": elapsed_ms,
        },
        "safety_validation": safety,
        "unresolved_conflicts": [],
        "failure_reason": reason,
        "failure": {
            "vehicle_id": vehicle_id,
            "failure_code": failure_code,
            "expanded_states": expanded_states,
            "max_expanded_states": max_expanded_states,
            "planning_time_ms": elapsed_ms,
            "failure_reason": reason,
            "details": failure_details or {},
        },
        "vehicle_initial_config": vehicles,
        "lift_initial_state": options.get("lift_initial_levels", {}),
        "time_contract": {
            "time_unit": "seconds",
            "internal_time_unit": "integer_tick",
            "time_step_seconds": options.get("time_step_seconds", DEFAULT_TIME_STEP_SECONDS),
            "interval_semantics": "half_open_[start,end)",
            "duration_rounding": "ceiling_never_round_down",
            "timing_source": "synthetic_demo",
            "goal_occupancy": "persistent",
            "vehicle_length_cells": options.get("vehicle_length_cells"),
            "vehicle_width_cells": options.get("vehicle_width_cells"),
            "search_limits": {
                "max_vehicles": options.get("max_vehicles"),
                "max_time_steps": options.get("max_time_steps"),
                "max_expanded_states": options.get("max_expanded_states"),
                "max_lift_candidates": options.get("max_lift_candidates"),
                "max_replans": options.get("max_replans"),
            },
        },
    }


def build_shuttle_multi_scenarios(
    graph: dict[str, dict[str, Any]], lifts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    intersections = sorted(
        (
            node
            for node in graph.values()
            if int(node["shuttle_level"]) == 1 and len(node["neighbors"]) >= 4
        ),
        key=lambda node: node["track_node_id"],
    )
    if not intersections:
        raise ShuttleMultiPlanningError("The real ShuttleGraph has no four-way intersection for scenario B")
    center = intersections[0]
    neighbors = [graph[node_id] for node_id in center["neighbors"]]
    by_delta = {
        (int(node["row"]) - int(center["row"]), int(node["col"]) - int(center["col"])): node
        for node in neighbors
    }
    north, south = by_delta[(-2, 0)], by_delta[(2, 0)]
    west, east = by_delta[(0, -2)], by_delta[(0, 2)]

    parallel_level = 3
    parallel_nodes = [
        graph[f"L{parallel_level}-r{node['row']}-c{node['col']}"]
        for node in (north, south)
    ]
    lift_a = next((lift for lift in lifts if lift["lift_id"] == "shuttle-lift-a"), None)
    if not lift_a:
        raise ShuttleMultiPlanningError("Scenario C requires the verified real Lift-A")
    lift_neighbors = {
        level: sorted(graph[lift_a["level_entrances"][f"L{level}"]]["neighbors"])
        for level in (1, 2, 3, 4)
    }
    scenarios = [
        {
            "scenario_id": "parallel",
            "name": "场景 A：分层无冲突并行运行",
            "description": "两台 Shuttle 在真实 L1/L3 轨道上并行运行；相同平面投影由内部层高安全隔离。",
            "vehicle_requests": [
                {"vehicle_id": "SHUTTLE-01", "start_track_node_id": north["track_node_id"], "target_track_node_id": south["track_node_id"], "priority": 1, "algorithm": "astar"},
                {"vehicle_id": "SHUTTLE-02", "start_track_node_id": parallel_nodes[0]["track_node_id"], "target_track_node_id": parallel_nodes[1]["track_node_id"], "priority": 2, "algorithm": "astar"},
            ],
            "evidence": {"source": "real_shuttle_graph", "levels": [1, 3], "projected_intersection": center["track_node_id"]},
        },
        {
            "scenario_id": "track-conflict",
            "name": "场景 B：同层交叉轨道竞争",
            "description": "两台 Shuttle 从垂直方向同时申请真实 L1 四向交叉节点。",
            "vehicle_requests": [
                {"vehicle_id": "SHUTTLE-01", "start_track_node_id": north["track_node_id"], "target_track_node_id": south["track_node_id"], "priority": 1, "algorithm": "astar"},
                {"vehicle_id": "SHUTTLE-02", "start_track_node_id": west["track_node_id"], "target_track_node_id": east["track_node_id"], "priority": 2, "algorithm": "astar"},
            ],
            "evidence": {"source": "real_shuttle_graph", "competition_track_node_id": center["track_node_id"], "level": 1},
        },
        {
            "scenario_id": "lift-conflict",
            "name": "场景 C：Lift-A 资源竞争",
            "description": "两台跨层 Shuttle 的单车候选路线均申请真实 Lift-A，由预约器等待或改选 Lift-B。",
            "vehicle_requests": [
                {"vehicle_id": "SHUTTLE-01", "start_track_node_id": lift_neighbors[1][0], "target_track_node_id": lift_neighbors[4][-1], "priority": 1, "algorithm": "astar"},
                {"vehicle_id": "SHUTTLE-02", "start_track_node_id": lift_neighbors[2][-1], "target_track_node_id": lift_neighbors[3][0], "priority": 2, "algorithm": "astar"},
            ],
            "evidence": {"source": "real_shuttle_graph", "contested_lift_id": "shuttle-lift-a", "lift_position": {"row": lift_a["row"], "col": lift_a["col"]}},
        },
    ]
    return scenarios
