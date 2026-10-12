from __future__ import annotations

from collections import deque
from time import perf_counter
from typing import Any, Iterable

from .pathfinding import build_walkable_lookup, cell_position, shortest_path_result


class PathDemoError(ValueError):
    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


Position = tuple[str, int, int]


def _neighbors(position: Position) -> Iterable[Position]:
    floor, row, col = position
    yield floor, row + 1, col
    yield floor, row - 1, col
    yield floor, row, col + 1
    yield floor, row, col - 1


def _components(topology: dict[str, Any]) -> list[list[Position]]:
    walkable = build_walkable_lookup(topology)
    remaining = set(walkable)
    components: list[list[Position]] = []

    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        queue = deque([seed])
        component: list[Position] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in _neighbors(current):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))

    components.sort(key=lambda item: (-len(item), item[0] if item else ("", 0, 0)))
    return components


def _farthest(seed: Position, allowed: set[Position], candidates: set[Position] | None = None) -> Position:
    distances = {seed: 0}
    queue = deque([seed])
    while queue:
        current = queue.popleft()
        for neighbor in _neighbors(current):
            if neighbor in allowed and neighbor not in distances:
                distances[neighbor] = distances[current] + 1
                queue.append(neighbor)
    endpoint_distances = {position: distance for position, distance in distances.items() if not candidates or position in candidates}
    max_distance = max(endpoint_distances.values())
    return min(position for position, distance in endpoint_distances.items() if distance == max_distance)


def _cell_payload(cell: dict[str, Any], component_id: str | None = None) -> dict[str, Any]:
    return {
        "id": cell["id"],
        "floor": str(cell.get("floor") or "1F"),
        "row": int(cell["row"]),
        "col": int(cell["col"]),
        "type": str(cell["type"]),
        "component_id": component_id,
    }
def analyze_path_demo_topology(topology: dict[str, Any]) -> dict[str, Any]:
    walkable = build_walkable_lookup(topology)
    components = _components(topology)
    if not components:
        raise PathDemoError("Topology has no traversable cells")

    component_ids: dict[Position, str] = {}
    component_payloads: list[dict[str, Any]] = []
    for index, component in enumerate(components, start=1):
        component_id = f"component-{index}"
        for position in component:
            component_ids[position] = component_id
        component_payloads.append(
            {
                "id": component_id,
                "floor": component[0][0],
                "cell_count": len(component),
            }
        )

    largest = set(components[0])
    road_endpoints = {position for position in largest if walkable[position].get("type") == "road"}
    if len(road_endpoints) < 2:
        raise PathDemoError("Largest connected component does not contain two traversable road cells")
    first = _farthest(min(road_endpoints), largest, road_endpoints)
    second = _farthest(first, largest, road_endpoints)
    largest_component_id = component_ids[first]

    cells = [
        _cell_payload(walkable[position], component_ids[position])
        for position in sorted(walkable)
    ]
    return {
        "components": component_payloads,
        "largest_component_id": largest_component_id,
        "traversable_cells": cells,
        "default_start": _cell_payload(walkable[first], largest_component_id),
        "default_goal": _cell_payload(walkable[second], largest_component_id),
    }


def plan_path_demo(
    topology: dict[str, Any],
    start_cell_id: str,
    goal_cell_id: str,
    algorithm: str,
) -> dict[str, Any]:
    if algorithm not in {"astar", "dijkstra"}:
        raise PathDemoError(f"Unsupported algorithm: {algorithm}")

    cells_by_id = {cell["id"]: cell for cell in topology.get("cells", [])}
    start = cells_by_id.get(start_cell_id)
    goal = cells_by_id.get(goal_cell_id)
    if start is None:
        raise PathDemoError(f"Start cell does not exist: {start_cell_id}")
    if goal is None:
        raise PathDemoError(f"Goal cell does not exist: {goal_cell_id}")

    for label, cell in (("Start", start), ("Goal", goal)):
        if not cell.get("walkable", True) or cell.get("type") != "road":
            raise PathDemoError(f"{label} cell is not a traversable road cell: {cell['id']}")

    start_position = cell_position(start)
    goal_position = cell_position(goal)
    if start_position[0] != goal_position[0]:
        raise PathDemoError("Path demo only supports start and goal on the same floor")

    analysis = analyze_path_demo_topology(topology)
    component_by_cell = {
        item["id"]: item["component_id"]
        for item in analysis["traversable_cells"]
    }
    start_component = component_by_cell[start_cell_id]
    goal_component = component_by_cell[goal_cell_id]
    if start_component != goal_component:
        raise PathDemoError(
            f"Start and goal are unreachable because they belong to different connected components "
            f"({start_component} != {goal_component})",
            status_code=409,
        )

    started_at = perf_counter()
    result = shortest_path_result(topology, start_cell_id, goal_cell_id, algorithm)
    planning_time_ms = (perf_counter() - started_at) * 1000
    if not result.path:
        raise PathDemoError("No traversable path exists between start and goal", status_code=409)

    for previous, current in zip(result.path, result.path[1:]):
        previous_position = cell_position(previous)
        current_position = cell_position(current)
        if previous_position[0] != current_position[0] or (
            abs(previous_position[1] - current_position[1])
            + abs(previous_position[2] - current_position[2])
            != 1
        ):
            raise RuntimeError("Pathfinder returned a non-adjacent path segment")

    return {
        "algorithm": algorithm,
        "start": _cell_payload(start, start_component),
        "goal": _cell_payload(goal, goal_component),
        "path_cells": [_cell_payload(cell, start_component) for cell in result.path],
        "total_distance": float(max(0, len(result.path) - 1)),
        "total_cost": result.total_cost,
        "visited_nodes": result.visited_nodes,
        "planning_time_ms": planning_time_ms,
    }
