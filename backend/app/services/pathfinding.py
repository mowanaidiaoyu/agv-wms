from __future__ import annotations

import heapq
from typing import Any

DEFAULT_FLOOR = "1F"


def normalize_floor(value: Any) -> str:
    return str(value or DEFAULT_FLOOR)


def cell_id(row: int, col: int, floor: str | None = None) -> str:
    floor_value = normalize_floor(floor)
    if floor_value == DEFAULT_FLOOR:
        return f"cell-r{row}-c{col}"
    return f"cell-{floor_value}-r{row}-c{col}"


def cell_position(cell: dict[str, Any]) -> tuple[str, int, int]:
    return (normalize_floor(cell.get("floor")), int(cell["row"]), int(cell["col"]))


def node_position(node: dict[str, Any]) -> tuple[str, int, int]:
    return (normalize_floor(node.get("floor")), int(node["row"]), int(node["col"]))


def position_key(floor: str, row: int, col: int) -> tuple[str, int, int]:
    return (normalize_floor(floor), int(row), int(col))


def same_position_cell_id(topology: dict[str, Any], floor: str, row: int, col: int) -> str:
    for cell in topology.get("cells", []):
        if cell_position(cell) == position_key(floor, row, col):
            return cell["id"]
    return cell_id(row, col, floor)


def parse_cell_id(value: str) -> tuple[str, int, int]:
    if value.startswith("cell-r") and "-c" in value:
        row_part, col_part = value[len("cell-r") :].split("-c", maxsplit=1)
        return DEFAULT_FLOOR, int(row_part), int(col_part)
    if value.startswith("cell-") and "-r" in value and "-c" in value:
        floor_part, rest = value[len("cell-") :].split("-r", maxsplit=1)
        row_part, col_part = rest.split("-c", maxsplit=1)
        return normalize_floor(floor_part), int(row_part), int(col_part)
    raise ValueError(f"Invalid cell id: {value}")


def parse_cell_id_2d(value: str) -> tuple[int, int]:
    _, row, col = parse_cell_id(value)
    return row, col


def cell_id_2d(row: int, col: int) -> str:
    return f"cell-r{row}-c{col}"


def build_cell_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {cell["id"]: cell for cell in topology.get("cells", [])}


def build_walkable_lookup(topology: dict[str, Any]) -> dict[tuple[str, int, int], dict[str, Any]]:
    lookup: dict[tuple[str, int, int], dict[str, Any]] = {}
    for cell in topology.get("cells", []):
        if cell.get("walkable", True) and cell.get("type") != "blocked":
            lookup[cell_position(cell)] = cell
    return lookup


def build_node_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in topology.get("nodes", [])}


def node_to_cell_id(topology: dict[str, Any], node_id: str) -> str:
    node = build_node_lookup(topology).get(node_id)
    if node is None:
        raise KeyError(node_id)
    return same_position_cell_id(topology, normalize_floor(node.get("floor")), node["row"], node["col"])


def cell_from_id(topology: dict[str, Any], value: str) -> dict[str, Any] | None:
    return build_cell_lookup(topology).get(value)


def heuristic(start: tuple[str, int, int], goal: tuple[str, int, int]) -> float:
    floor_cost = 4.0 if start[0] != goal[0] else 0.0
    return abs(start[1] - goal[1]) + abs(start[2] - goal[2]) + floor_cost


def reconstruct_path(
    came_from: dict[tuple[str, int, int], tuple[str, int, int]],
    current: tuple[str, int, int],
) -> list[tuple[str, int, int]]:
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


def lift_neighbors(topology: dict[str, Any], walkable: dict[tuple[str, int, int], dict[str, Any]]) -> dict[tuple[str, int, int], list[tuple[tuple[str, int, int], float]]]:
    node_lookup = build_node_lookup(topology)
    result: dict[tuple[str, int, int], list[tuple[tuple[str, int, int], float]]] = {}
    for resource in topology.get("resources", []):
        if resource.get("type") != "lift":
            continue
        lift_positions: list[tuple[str, int, int]] = []
        for node_id in resource.get("nodeIds", []):
            node = node_lookup.get(node_id)
            if not node or node.get("type") != "lift":
                continue
            pos = node_position(node)
            if pos in walkable:
                lift_positions.append(pos)
        vertical_cost = float(resource.get("meta", {}).get("verticalCost", 4.0))
        for source in lift_positions:
            for target in lift_positions:
                if source != target and source[1:] == target[1:]:
                    result.setdefault(source, []).append((target, vertical_cost))
    return result


def shortest_path(topology: dict[str, Any], start_cell: str, goal_cell: str, policy: str = "astar") -> list[dict[str, Any]]:
    if start_cell == goal_cell:
        start = cell_from_id(topology, start_cell)
        return [start] if start else []

    walkable = build_walkable_lookup(topology)
    vertical_edges = lift_neighbors(topology, walkable)
    try:
        start = parse_cell_id(start_cell)
        goal = parse_cell_id(goal_cell)
    except ValueError:
        return []

    if start not in walkable or goal not in walkable:
        return []

    frontier: list[tuple[float, tuple[str, int, int]]] = [(0.0, start)]
    came_from: dict[tuple[str, int, int], tuple[str, int, int]] = {}
    cost_so_far: dict[tuple[str, int, int], float] = {start: 0.0}

    while frontier:
        _, current = heapq.heappop(frontier)
        if current == goal:
            positions = reconstruct_path(came_from, current)
            return [walkable[position] for position in positions]

        neighbor_candidates: list[tuple[tuple[str, int, int], float | None]] = []
        for d_row, d_col in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbor_candidates.append(((current[0], current[1] + d_row, current[2] + d_col), None))
        neighbor_candidates.extend(vertical_edges.get(current, []))

        for nxt, override_cost in neighbor_candidates:
            nxt_cell = walkable.get(nxt)
            if nxt_cell is None:
                continue

            move_cost = override_cost if override_cost is not None else float(nxt_cell.get("cost", 1.0))
            new_cost = cost_so_far[current] + move_cost
            if nxt not in cost_so_far or new_cost < cost_so_far[nxt]:
                cost_so_far[nxt] = new_cost
                priority = new_cost + heuristic(nxt, goal) if policy == "astar" else new_cost
                heapq.heappush(frontier, (priority, nxt))
                came_from[nxt] = current

    return []
