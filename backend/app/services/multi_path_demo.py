from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import dataclass, field
from itertools import count
from time import perf_counter
from typing import Any, Iterable, Literal

from .path_demo import PathDemoError
from .pathfinding import build_walkable_lookup, cell_position


Position = tuple[str, int, int]
ConflictKind = Literal["node", "edge", "geometry"]


@dataclass
class ConflictTracker:
    node: set[tuple[Any, ...]] = field(default_factory=set)
    edge: set[tuple[Any, ...]] = field(default_factory=set)
    geometry: set[tuple[Any, ...]] = field(default_factory=set)
    events: dict[tuple[Any, ...], dict[str, Any]] = field(default_factory=dict)

    def record(self, kind: ConflictKind, signature: tuple[Any, ...]) -> None:
        getattr(self, kind).add(signature)

    def record_event(self, kind: ConflictKind, signature: tuple[Any, ...], event: dict[str, Any]) -> None:
        self.record(kind, signature)
        event_key = (
            kind,
            event["yielding_vehicle_id"],
            event["priority_vehicle_id"],
            event["time_step"],
            event["cell"],
        )
        self.events.setdefault(event_key, event)


@dataclass(frozen=True)
class PlanningLimits:
    time_step_seconds: float
    speed_cells_per_second: float
    max_time_steps: int
    max_expanded_states: int
    vehicle_width_cells: float
    vehicle_length_cells: float
    safety_margin_cells: float

    @property
    def safety_distance_cells(self) -> float:
        return math.hypot(self.vehicle_width_cells, self.vehicle_length_cells) + self.safety_margin_cells


def _neighbors(position: Position) -> Iterable[Position]:
    floor, row, col = position
    yield floor, row + 1, col
    yield floor, row - 1, col
    yield floor, row, col + 1
    yield floor, row, col - 1


def _road_lookup(topology: dict[str, Any]) -> dict[Position, dict[str, Any]]:
    return {
        position: cell
        for position, cell in build_walkable_lookup(topology).items()
        if cell.get("type") == "road"
    }


def _road_graph(road_lookup: dict[Position, dict[str, Any]]) -> dict[Position, tuple[Position, ...]]:
    positions = set(road_lookup)
    return {
        position: tuple(sorted(neighbor for neighbor in _neighbors(position) if neighbor in positions))
        for position in positions
    }


def _components(graph: dict[Position, tuple[Position, ...]]) -> list[list[Position]]:
    remaining = set(graph)
    result: list[list[Position]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        queue = deque([seed])
        component: list[Position] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in graph[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        result.append(sorted(component))
    result.sort(key=lambda component: (-len(component), component[0]))
    return result


def _position_at(path: list[Position], time_step: int) -> Position:
    return path[min(time_step, len(path) - 1)]


def _minimum_segment_distance(a0: Position, a1: Position, b0: Position, b1: Position) -> float:
    if a0[0] != b0[0] or a1[0] != b1[0] or a0[0] != a1[0]:
        return math.inf
    relative_row = float(a0[1] - b0[1])
    relative_col = float(a0[2] - b0[2])
    velocity_row = float((a1[1] - a0[1]) - (b1[1] - b0[1]))
    velocity_col = float((a1[2] - a0[2]) - (b1[2] - b0[2]))
    velocity_squared = velocity_row * velocity_row + velocity_col * velocity_col
    alpha = 0.0
    if velocity_squared > 0:
        alpha = max(0.0, min(1.0, -(relative_row * velocity_row + relative_col * velocity_col) / velocity_squared))
    row = relative_row + velocity_row * alpha
    col = relative_col + velocity_col * alpha
    return math.hypot(row, col)


def _transition_conflict(
    current: Position,
    nxt: Position,
    arrival_step: int,
    reservations: list[list[Position]],
    safety_distance: float,
) -> tuple[ConflictKind, int] | None:
    for reserved_index, reserved_path in enumerate(reservations):
        other_from = _position_at(reserved_path, max(0, arrival_step - 1))
        other_to = _position_at(reserved_path, arrival_step)
        if nxt == other_to:
            return "node", reserved_index
        if current == other_to and nxt == other_from and current != nxt:
            return "edge", reserved_index
        if _minimum_segment_distance(current, nxt, other_from, other_to) < safety_distance - 1e-9:
            return "geometry", reserved_index
    return None


def _can_hold_goal(
    goal: Position,
    arrival_step: int,
    reservations: list[list[Position]],
    limits: PlanningLimits,
    tracker: ConflictTracker,
) -> bool:
    latest_reserved_step = max((len(path) - 1 for path in reservations), default=arrival_step)
    for time_step in range(arrival_step + 1, max(arrival_step + 1, latest_reserved_step + 2)):
        conflict = _transition_conflict(goal, goal, time_step, reservations, limits.safety_distance_cells)
        if conflict:
            kind, other_index = conflict
            tracker.record(kind, ("goal", other_index, time_step, goal))
            return False
    return True


def _heuristic(position: Position, goal: Position) -> int:
    if position[0] != goal[0]:
        return 1_000_000
    return abs(position[1] - goal[1]) + abs(position[2] - goal[2])


def _reconstruct_timed_path(
    came_from: dict[tuple[Position, int], tuple[Position, int]],
    state: tuple[Position, int],
) -> list[Position]:
    path = [state[0]]
    while state in came_from:
        state = came_from[state]
        path.append(state[0])
    path.reverse()
    return path


def _space_time_path(
    graph: dict[Position, tuple[Position, ...]],
    start: Position,
    goal: Position,
    algorithm: str,
    reservations: list[list[Position]],
    limits: PlanningLimits,
    tracker: ConflictTracker,
    vehicle_id: str,
    vehicle_priority: int,
    reserved_vehicles: list[dict[str, Any]],
) -> tuple[list[Position], int]:
    sequence = count()
    frontier: list[tuple[int, int, int, Position]] = []
    start_priority = _heuristic(start, goal) if algorithm == "astar" else 0
    heapq.heappush(frontier, (start_priority, 0, next(sequence), start))
    came_from: dict[tuple[Position, int], tuple[Position, int]] = {}
    visited: set[tuple[Position, int]] = set()
    expanded = 0

    while frontier:
        _, time_step, _, current = heapq.heappop(frontier)
        state = (current, time_step)
        if state in visited:
            continue
        visited.add(state)
        expanded += 1
        if expanded > limits.max_expanded_states:
            raise PathDemoError(
                f"Space-time search exceeded max_expanded_states={limits.max_expanded_states}",
                status_code=409,
            )
        if current == goal and _can_hold_goal(goal, time_step, reservations, limits, tracker):
            return _reconstruct_timed_path(came_from, state), expanded
        if time_step >= limits.max_time_steps:
            continue

        # Waiting is considered first so equal-cost conflict resolution is stable and observable.
        candidates = (current, *graph[current])
        for nxt in candidates:
            arrival_step = time_step + 1
            conflict = _transition_conflict(current, nxt, arrival_step, reservations, limits.safety_distance_cells)
            if conflict:
                kind, other_index = conflict
                signature = (other_index, arrival_step, current, nxt)
                other_path = reservations[other_index]
                reserved_vehicle = reserved_vehicles[other_index]
                tracker.record_event(kind, signature, {
                    "conflict_type": kind,
                    "time_step": arrival_step,
                    "current": current,
                    "cell": nxt,
                    "reserved_from": _position_at(other_path, max(0, arrival_step - 1)),
                    "reserved_to": _position_at(other_path, arrival_step),
                    "yielding_vehicle_id": vehicle_id,
                    "yielding_priority": vehicle_priority,
                    "priority_vehicle_id": reserved_vehicle["vehicle_id"],
                    "priority": reserved_vehicle["priority"],
                })
                continue
            next_state = (nxt, arrival_step)
            if next_state in visited:
                continue
            priority = arrival_step
            if algorithm == "astar":
                priority += _heuristic(nxt, goal)
            if next_state not in came_from:
                came_from[next_state] = state
            heapq.heappush(frontier, (priority, arrival_step, next(sequence), nxt))

    raise PathDemoError(
        f"No conflict-free space-time path found within max_time_steps={limits.max_time_steps}",
        status_code=409,
    )


def _shortest_distance(graph: dict[Position, tuple[Position, ...]], start: Position, goal: Position) -> int:
    queue = deque([(start, 0)])
    visited = {start}
    while queue:
        current, distance = queue.popleft()
        if current == goal:
            return distance
        for neighbor in graph[current]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, distance + 1))
    raise PathDemoError("Vehicle start and goal are in different road components", status_code=409)


def _cell_payload(cell: dict[str, Any], component_id: str) -> dict[str, Any]:
    return {
        "id": cell["id"],
        "floor": str(cell.get("floor") or "1F"),
        "row": int(cell["row"]),
        "col": int(cell["col"]),
        "type": str(cell["type"]),
        "component_id": component_id,
    }


def _remaining_conflicts(paths: list[list[Position]], safety_distance: float) -> dict[str, int]:
    counts = {"node": 0, "edge": 0, "geometry": 0}
    makespan = max((len(path) - 1 for path in paths), default=0)
    for time_step in range(makespan + 1):
        for first_index in range(len(paths)):
            for second_index in range(first_index + 1, len(paths)):
                first = _position_at(paths[first_index], time_step)
                second = _position_at(paths[second_index], time_step)
                if first == second:
                    counts["node"] += 1
                    continue
                if time_step == 0:
                    if _minimum_segment_distance(first, first, second, second) < safety_distance - 1e-9:
                        counts["geometry"] += 1
                    continue
                first_previous = _position_at(paths[first_index], time_step - 1)
                second_previous = _position_at(paths[second_index], time_step - 1)
                if first_previous == second and second_previous == first and first_previous != first:
                    counts["edge"] += 1
                elif _minimum_segment_distance(first_previous, first, second_previous, second) < safety_distance - 1e-9:
                    counts["geometry"] += 1
    return counts


def _wait_intervals(path: list[Position], time_step_seconds: float) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    index = 1
    while index < len(path):
        if path[index] != path[index - 1]:
            index += 1
            continue
        start_step = index - 1
        cell = path[index]
        while index + 1 < len(path) and path[index + 1] == cell:
            index += 1
        end_step = index
        intervals.append({
            "cell": cell,
            "start_time_seconds": start_step * time_step_seconds,
            "end_time_seconds": end_step * time_step_seconds,
            "duration_seconds": (end_step - start_step) * time_step_seconds,
        })
        index += 1
    return intervals


def _conflict_event_payloads(
    tracker: ConflictTracker,
    reservations: list[list[Position]],
    vehicle_results: list[dict[str, Any]],
    road_lookup: dict[Position, dict[str, Any]],
    graph: dict[Position, tuple[Position, ...]],
    component_by_position: dict[Position, str],
    limits: PlanningLimits,
) -> list[dict[str, Any]]:
    result_by_id = {item["vehicle_id"]: item for item in vehicle_results}
    path_by_id = {
        item["vehicle_id"]: reservations[index]
        for index, item in enumerate(vehicle_results)
    }
    payloads: list[dict[str, Any]] = []
    for event in sorted(
        tracker.events.values(),
        key=lambda item: (item["time_step"], item["conflict_type"], item["yielding_vehicle_id"], item["cell"]),
    ):
        yielding_id = event["yielding_vehicle_id"]
        yielding_result = result_by_id[yielding_id]
        waits = _wait_intervals(path_by_id[yielding_id], limits.time_step_seconds)
        event_time_seconds = event["time_step"] * limits.time_step_seconds
        wait = next(
            (
                item for item in waits
                if item["end_time_seconds"] + 1e-9 >= event_time_seconds
            ),
            None,
        )
        detoured = yielding_result["total_distance"] > _shortest_distance(
            graph,
            path_by_id[yielding_id][0],
            path_by_id[yielding_id][-1],
        )
        avoidance_action = "reroute_and_wait" if detoured and wait else "wait" if wait else "reroute"
        cell = event["cell"]
        from_cell = event["current"]
        wait_cell = wait["cell"] if wait else None
        event_id = (
            f"{event['conflict_type']}-{yielding_id}-{event['priority_vehicle_id']}-"
            f"{event['time_step']}-{road_lookup[cell]['id']}"
        )
        payloads.append({
            "event_id": event_id,
            "conflict_type": event["conflict_type"],
            "status": "resolved",
            "time_step": event["time_step"],
            "time_seconds": event_time_seconds,
            "cell": _cell_payload(road_lookup[cell], component_by_position[cell]),
            "from_cell": _cell_payload(road_lookup[from_cell], component_by_position[from_cell]),
            "involved_vehicle_ids": [event["priority_vehicle_id"], yielding_id],
            "priority_vehicle_id": event["priority_vehicle_id"],
            "priority": event["priority"],
            "yielding_vehicle_id": yielding_id,
            "yielding_priority": event["yielding_priority"],
            "reservation_action": "candidate_rejected",
            "avoidance_action": avoidance_action,
            "wait_cell": _cell_payload(road_lookup[wait_cell], component_by_position[wait_cell]) if wait_cell else None,
            "wait_start_time_seconds": wait["start_time_seconds"] if wait else None,
            "wait_end_time_seconds": wait["end_time_seconds"] if wait else None,
            "wait_duration_seconds": wait["duration_seconds"] if wait else 0.0,
            "result": "conflict_free_plan",
        })
    return payloads


def plan_multi_paths(
    topology: dict[str, Any],
    vehicles: list[dict[str, Any]],
    algorithm: str,
    planning_config: dict[str, Any],
) -> dict[str, Any]:
    if algorithm not in {"astar", "dijkstra"}:
        raise PathDemoError(f"Unsupported algorithm: {algorithm}")
    if not 1 <= len(vehicles) <= 6:
        raise PathDemoError("Multi-vehicle planning requires between 1 and 6 vehicles")
    limits = PlanningLimits(**planning_config)
    if not math.isclose(limits.time_step_seconds * limits.speed_cells_per_second, 1.0, abs_tol=1e-9):
        raise PathDemoError(
            "The offline multi-vehicle contract requires exactly one cell per time step "
            "(time_step_seconds * speed_cells_per_second must equal 1)",
        )

    started_at = perf_counter()
    road_lookup = _road_lookup(topology)
    graph = _road_graph(road_lookup)
    components = _components(graph)
    component_by_position = {
        position: f"road-component-{index}"
        for index, component in enumerate(components, start=1)
        for position in component
    }
    cells_by_id = {cell["id"]: cell for cell in road_lookup.values()}
    vehicle_ids = [vehicle["vehicle_id"] for vehicle in vehicles]
    if len(set(vehicle_ids)) != len(vehicle_ids):
        raise PathDemoError("Vehicle IDs must be unique")

    ordered_vehicles = [
        vehicle
        for _, vehicle in sorted(
            enumerate(vehicles),
            key=lambda item: (int(item[1].get("priority", 1)), item[0]),
        )
    ]
    goal_ids = [vehicle["goal_cell_id"] for vehicle in ordered_vehicles]
    if len(set(goal_ids)) != len(goal_ids):
        raise PathDemoError("Vehicles cannot share a persistent goal road node", status_code=409)

    normalized: list[tuple[dict[str, Any], Position, Position]] = []
    for vehicle in ordered_vehicles:
        start_cell = cells_by_id.get(vehicle["start_cell_id"])
        goal_cell = cells_by_id.get(vehicle["goal_cell_id"])
        if start_cell is None:
            raise PathDemoError(f"Vehicle {vehicle['vehicle_id']} start is not a traversable road cell")
        if goal_cell is None:
            raise PathDemoError(f"Vehicle {vehicle['vehicle_id']} goal is not a traversable road cell")
        start = cell_position(start_cell)
        goal = cell_position(goal_cell)
        if component_by_position[start] != component_by_position[goal]:
            raise PathDemoError(
                f"Vehicle {vehicle['vehicle_id']} start and goal are in different road components",
                status_code=409,
            )
        normalized.append((vehicle, start, goal))

    for first_index, (_, first_start, _) in enumerate(normalized):
        for second_index in range(first_index + 1, len(normalized)):
            second_start = normalized[second_index][1]
            if first_start == second_start:
                raise PathDemoError("Vehicles cannot share an initial road node", status_code=409)
            if _minimum_segment_distance(first_start, first_start, second_start, second_start) < limits.safety_distance_cells - 1e-9:
                raise PathDemoError("Initial vehicle geometry violates the configured safety distance", status_code=409)

    reservations: list[list[Position]] = []
    reserved_vehicles: list[dict[str, Any]] = []
    tracker = ConflictTracker()
    vehicle_results: list[dict[str, Any]] = []
    for vehicle, start, goal in normalized:
        baseline_distance = _shortest_distance(graph, start, goal)
        try:
            path, visited_states = _space_time_path(
                graph,
                start,
                goal,
                algorithm,
                reservations,
                limits,
                tracker,
                str(vehicle["vehicle_id"]),
                int(vehicle.get("priority", 1)),
                reserved_vehicles,
            )
        except PathDemoError as exc:
            raise PathDemoError(f"Vehicle {vehicle['vehicle_id']}: {exc.detail}", status_code=exc.status_code) from exc
        reservations.append(path)
        reserved_vehicles.append({
            "vehicle_id": str(vehicle["vehicle_id"]),
            "priority": int(vehicle.get("priority", 1)),
        })
        movement_steps = sum(first != second for first, second in zip(path, path[1:]))
        wait_steps = len(path) - 1 - movement_steps
        detour_steps = max(0, movement_steps - baseline_distance)
        component_id = component_by_position[start]
        steps = []
        for time_step, position in enumerate(path):
            previous = path[time_step - 1] if time_step else None
            steps.append(
                {
                    "time_step": time_step,
                    "time_seconds": time_step * limits.time_step_seconds,
                    "action": "start" if previous is None else "wait" if previous == position else "move",
                    "cell": _cell_payload(road_lookup[position], component_id),
                }
            )
        vehicle_results.append(
            {
                "vehicle_id": vehicle["vehicle_id"],
                "vehicle_type": vehicle.get("vehicle_type", "ctu"),
                "priority": int(vehicle.get("priority", 1)),
                "display_color": vehicle.get("display_color"),
                "start": _cell_payload(road_lookup[start], component_id),
                "goal": _cell_payload(road_lookup[goal], component_id),
                "steps": steps,
                "total_distance": float(movement_steps),
                "estimated_duration_seconds": (len(path) - 1) * limits.time_step_seconds,
                "wait_time_seconds": wait_steps * limits.time_step_seconds,
                "avoidance_count": wait_steps + detour_steps,
                "visited_states": visited_states,
            }
        )

    remaining = _remaining_conflicts(reservations, limits.safety_distance_cells)
    if any(remaining.values()):
        raise RuntimeError(f"Planner produced an unsafe execution plan: {remaining}")
    makespan_steps = max((len(path) - 1 for path in reservations), default=0)
    detected_node = len(tracker.node)
    detected_edge = len(tracker.edge)
    detected_geometry = len(tracker.geometry)
    conflict_events = _conflict_event_payloads(
        tracker,
        reservations,
        vehicle_results,
        road_lookup,
        graph,
        component_by_position,
        limits,
    )
    return {
        "algorithm": algorithm,
        "planning_status": "success",
        "error_reason": None,
        "time_step_seconds": limits.time_step_seconds,
        "speed_cells_per_second": limits.speed_cells_per_second,
        "vehicle_width_cells": limits.vehicle_width_cells,
        "vehicle_length_cells": limits.vehicle_length_cells,
        "safety_distance_cells": limits.safety_distance_cells,
        "makespan_steps": makespan_steps,
        "makespan_seconds": makespan_steps * limits.time_step_seconds,
        "vehicles": vehicle_results,
        "conflicts": {
            "detected_node_conflicts": detected_node,
            "detected_edge_conflicts": detected_edge,
            "detected_geometry_conflicts": detected_geometry,
            "resolved_node_conflicts": detected_node,
            "resolved_edge_conflicts": detected_edge,
            "resolved_geometry_conflicts": detected_geometry,
            "remaining_node_conflicts": remaining["node"],
            "remaining_edge_conflicts": remaining["edge"],
            "remaining_geometry_conflicts": remaining["geometry"],
        },
        "conflict_events": conflict_events,
        "avoidance_count": sum(result["avoidance_count"] for result in vehicle_results),
        "planning_time_ms": (perf_counter() - started_at) * 1000,
    }


def _spatial_path(graph: dict[Position, tuple[Position, ...]], start: Position, goal: Position) -> list[Position]:
    queue = deque([start])
    came_from: dict[Position, Position] = {}
    visited = {start}
    while queue:
        current = queue.popleft()
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            return list(reversed(path))
        for neighbor in graph[current]:
            if neighbor not in visited:
                visited.add(neighbor)
                came_from[neighbor] = current
                queue.append(neighbor)
    return []


def _distance_without(
    graph: dict[Position, tuple[Position, ...]],
    start: Position,
    goal: Position,
    blocked: Position,
) -> int | None:
    queue = deque([(start, 0)])
    visited = {start, blocked}
    while queue:
        current, distance = queue.popleft()
        if current == goal:
            return distance
        for neighbor in graph[current]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, distance + 1))
    return None


def _far_straight_path(
    component: set[Position],
    excluded: set[Position],
    length: int = 6,
    minimum_distance: int = 10,
) -> list[Position] | None:
    for start in sorted(component):
        for row_delta, col_delta in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            path = [(start[0], start[1] + row_delta * step, start[2] + col_delta * step) for step in range(length + 1)]
            if not all(position in component for position in path):
                continue
            if excluded and min(
                abs(position[1] - blocked[1]) + abs(position[2] - blocked[2])
                for position in path
                for blocked in excluded
            ) < minimum_distance:
                continue
            return path
    return None


def build_multi_demo_scenarios(topology: dict[str, Any]) -> list[dict[str, Any]]:
    road_lookup = _road_lookup(topology)
    graph = _road_graph(road_lookup)
    components = _components(graph)
    if not components:
        raise PathDemoError("Topology has no connected road component")
    largest = set(components[0])

    parallel_paths: list[list[Position]] = []
    excluded: set[Position] = set()
    for _ in range(3):
        path = _far_straight_path(largest, excluded)
        if path is None:
            raise PathDemoError("Unable to construct three separated parallel routes on this topology")
        parallel_paths.append(path)
        excluded.update(path)

    node_conflict_setup: tuple[Position, Position, Position, Position] | None = None
    for candidate in sorted(largest):
        if len(graph[candidate]) < 3:
            continue
        candidate_neighbors = list(graph[candidate])
        for first_start in candidate_neighbors:
            for first_goal in candidate_neighbors:
                if first_goal == first_start:
                    continue
                for second_start in candidate_neighbors:
                    if second_start in {first_start, first_goal}:
                        continue
                    detour_distance = _distance_without(graph, second_start, first_start, candidate)
                    if detour_distance is None or detour_distance > 3:
                        node_conflict_setup = (candidate, first_start, first_goal, second_start)
                        break
                if node_conflict_setup:
                    break
            if node_conflict_setup:
                break
        if node_conflict_setup:
            break
    if node_conflict_setup is None:
        raise PathDemoError("Unable to construct a node-conflict scenario on this topology")
    junction, first_start, first_goal, second_start = node_conflict_setup
    junction_neighbors = list(graph[junction])
    node_third = _far_straight_path(largest, {junction, *junction_neighbors})
    if node_third is None:
        raise PathDemoError("Unable to place the third vehicle away from the node-conflict scenario")

    edge_vehicle_pairs: tuple[Position, Position, Position, Position] | None = None
    for first in sorted(largest):
        for second in graph[first]:
            if first >= second or len(graph[first]) < 3 or len(graph[second]) < 3:
                continue
            first_sides = [position for position in graph[first] if position != second]
            second_sides = [position for position in graph[second] if position != first]
            for first_start in first_sides:
                for second_goal in first_sides:
                    if second_goal == first_start:
                        continue
                    for second_start in second_sides:
                        for first_goal in second_sides:
                            if first_goal == second_start:
                                continue
                            first_path = _spatial_path(graph, first_start, first_goal)
                            second_path = _spatial_path(graph, second_start, second_goal)
                            for time_step in range(min(len(first_path), len(second_path)) - 1):
                                if (
                                    first_path[time_step] == first
                                    and first_path[time_step + 1] == second
                                    and second_path[time_step] == second
                                    and second_path[time_step + 1] == first
                                ):
                                    edge_vehicle_pairs = (first_start, first_goal, second_start, second_goal)
                                    break
                            if edge_vehicle_pairs:
                                break
                        if edge_vehicle_pairs:
                            break
                    if edge_vehicle_pairs:
                        break
                if edge_vehicle_pairs:
                    break
            if edge_vehicle_pairs:
                break
        if edge_vehicle_pairs:
            break
    if edge_vehicle_pairs is None:
        raise PathDemoError("Unable to construct an edge-swap scenario on this topology")
    edge_third = _far_straight_path(largest, set(edge_vehicle_pairs))
    if edge_third is None:
        raise PathDemoError("Unable to place the third vehicle away from the edge-swap scenario")

    def vehicle_payload(vehicle_id: str, start: Position, goal: Position) -> dict[str, Any]:
        priority = int(vehicle_id.rsplit("-", 1)[-1])
        return {
            "vehicle_id": vehicle_id,
            "vehicle_type": "ctu",
            "start_cell_id": road_lookup[start]["id"],
            "goal_cell_id": road_lookup[goal]["id"],
            "priority": priority,
        }

    return [
        {
            "id": "parallel",
            "name": "场景 A：无冲突并行运行",
            "description": "三辆车在最大道路连通分量内沿相互分离的路线并行运行。",
            "vehicles": [
                vehicle_payload(f"CTU-0{index}", path[0], path[-1])
                for index, path in enumerate(parallel_paths, start=1)
            ],
        },
        {
            "id": "node-conflict",
            "name": "场景 B：交叉节点竞争",
            "description": "CTU-01 与 CTU-02 同时竞争一个真实道路交叉节点，低优先级车辆需要避让。",
            "vehicles": [
                vehicle_payload("CTU-01", first_start, first_goal),
                vehicle_payload("CTU-02", second_start, first_start),
                vehicle_payload("CTU-03", node_third[0], node_third[-1]),
            ],
        },
        {
            "id": "edge-swap",
            "name": "场景 C：相向边交换风险",
            "description": "CTU-01 与 CTU-02 的空间最短路在同一真实道路边上相向，时空规划必须等待或绕行。",
            "vehicles": [
                vehicle_payload("CTU-01", edge_vehicle_pairs[0], edge_vehicle_pairs[1]),
                vehicle_payload("CTU-02", edge_vehicle_pairs[2], edge_vehicle_pairs[3]),
                vehicle_payload("CTU-03", edge_third[0], edge_third[-1]),
            ],
        },
    ]


def analyze_multi_road_topology(topology: dict[str, Any]) -> dict[str, Any]:
    road_lookup = _road_lookup(topology)
    graph = _road_graph(road_lookup)
    components = _components(graph)
    if not components:
        raise PathDemoError("Topology has no connected road component")
    component_by_position = {
        position: f"road-component-{index}"
        for index, component in enumerate(components, start=1)
        for position in component
    }
    largest_component_id = "road-component-1"
    return {
        "largest_road_component_id": largest_component_id,
        "road_cells": [
            _cell_payload(road_lookup[position], component_by_position[position])
            for position in components[0]
        ],
    }
