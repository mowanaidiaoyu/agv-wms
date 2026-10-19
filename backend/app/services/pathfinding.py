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
    """可通行格 = 整张网格（grid.rows×cols）内非 blocked 的格子，叠加显式 cells 补 cost/type。
    CTU 规则是「格子上没放东西就能走」，所以不再要求格子在 cells 里被显式标成 road。"""
    grid = topology.get("grid", {})
    rows = int(grid.get("rows") or 0)
    cols = int(grid.get("cols") or 0)
    floors = [normalize_floor(floor) for floor in (topology.get("meta", {}).get("floors") or [DEFAULT_FLOOR])]
    blocked: set[tuple[str, int, int]] = set()
    for cell in topology.get("cells", []):
        if cell.get("type") == "blocked" or not cell.get("walkable", True):
            blocked.add(cell_position(cell))

    lookup: dict[tuple[str, int, int], dict[str, Any]] = {}
    if rows > 0 and cols > 0:
        for floor in floors:
            for row in range(rows):
                for col in range(cols):
                    key = (floor, row, col)
                    if key in blocked:
                        continue
                    lookup[key] = {"id": cell_id(row, col, floor), "floor": floor, "row": row, "col": col, "type": "road", "cost": 1.0}
    # 显式格子覆盖（携带真实 id/cost/type/zoneId 等）
    for cell in topology.get("cells", []):
        pos = cell_position(cell)
        if pos in blocked:
            continue
        lookup[pos] = cell
    return lookup


def build_occupied_lookup(topology: dict[str, Any]) -> set[tuple[str, int, int]]:
    """放了东西的格子：库位 storage 所在格 + 输送带 conveyor 格，CTU 不能穿行，只能作为取货终点进入。"""
    occupied: set[tuple[str, int, int]] = set()
    for node in topology.get("nodes", []):
        if node.get("type") == "storage":
            occupied.add(node_position(node))
    for cell in topology.get("cells", []):
        if cell.get("type") == "conveyor":
            occupied.add(cell_position(cell))
    return occupied


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


def shortest_path(
    topology: dict[str, Any],
    start_cell: str,
    goal_cell: str,
    policy: str = "astar",
    avoid_storage: bool = False,
    extra_blocked: set[tuple[str, int, int]] | None = None,
) -> list[dict[str, Any]]:
    if start_cell == goal_cell:
        start = cell_from_id(topology, start_cell)
        return [start] if start else []

    try:
        start = parse_cell_id(start_cell)
        goal = parse_cell_id(goal_cell)
    except ValueError:
        return []

    walkable = build_walkable_lookup(topology)
    if avoid_storage or extra_blocked:
        # CTU 规则：没有被放置任何物品的格子才可以通行；库位/输送带不能穿行。
        # extra_blocked 用于运行时绕行：把其它设备当前占用的格临时视为障碍。
        # 出发格 / 目标格允许落在库位/占用格上（机器人停靠位 / 要进去取货的终点），先记下它们的原始格子再减占用。
        endpoints = {pos: walkable[pos] for pos in (start, goal) if pos in walkable}
        if avoid_storage:
            occupied = build_occupied_lookup(topology)
            walkable = {pos: cell for pos, cell in walkable.items() if pos not in occupied}
        if extra_blocked:
            walkable = {pos: cell for pos, cell in walkable.items() if pos not in extra_blocked}
        walkable.update(endpoints)

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

        for d_row, d_col in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (current[0], current[1] + d_row, current[2] + d_col)
            nxt_cell = walkable.get(nxt)
            if nxt_cell is None:
                continue

            move_cost = float(nxt_cell.get("cost", 1.0))
            new_cost = cost_so_far[current] + move_cost
            if nxt not in cost_so_far or new_cost < cost_so_far[nxt]:
                cost_so_far[nxt] = new_cost
                priority = new_cost + heuristic(nxt, goal) if policy == "astar" else new_cost
                heapq.heappush(frontier, (priority, nxt))
                came_from[nxt] = current

    return []


# ---------------------------------------------------------------------------
# 输送线出入口（port 区域）与四向车库（shuttle 区域）几何辅助。
# ---------------------------------------------------------------------------


def port_zone_ids(topology: dict[str, Any]) -> list[str]:
    return [zone["id"] for zone in topology.get("zones", []) if zone.get("type") == "port"]


def port_entry_cell(topology: dict[str, Any], zone: dict[str, Any]) -> str:
    """port 区域「下方入」入口格：取区域底边中点（行号最大那一行的中点列）。"""
    row = int(zone["y"]) + int(zone["height"]) - 1
    col = int(zone["x"]) + int(zone["width"]) // 2
    return same_position_cell_id(topology, DEFAULT_FLOOR, row, col)


def shuttle_slot_key(zone: dict[str, Any], row: int, col: int) -> str:
    """2×2 货位锚点（区域左上角起每 2 格一个槽），与前端 shuttleSlotsInBox 口径一致。"""
    y = int(zone["y"])
    x = int(zone["x"])
    anchor_row = y + ((row - y) // 2) * 2
    anchor_col = x + ((col - x) // 2) * 2
    return f"{anchor_row},{anchor_col}"


def shuttle_slot_aisle_mask(
    zone: dict[str, Any],
    cell_type: dict[tuple[int, int], str],
    anchor_row: int,
    anchor_col: int,
) -> int:
    """槽位 4-bit 巷道掩码：优先读 slotAisles；缺省按「4 格全 road = 全层巷道」回退（前端同口径）。"""
    stored = (zone.get("slotAisles") or {}).get(f"{anchor_row},{anchor_col}")
    if stored is not None:
        return int(stored)
    for dr in range(2):
        for dc in range(2):
            if cell_type.get((anchor_row + dr, anchor_col + dc)) != "road":
                return 0
    return 0b1111


# ---------------------------------------------------------------------------
# 四向车：只在四向车库内连续巷道里水平移动，经升降电梯竖井在 1..4 层间上下。
# 车库之外按普通空格（仅地面层）通行，便于四向车驶出车库、前往输送线出入口交付。
# ---------------------------------------------------------------------------

SHUTTLE_LAYERS = 4


def build_shuttle_lookup(
    topology: dict[str, Any],
) -> tuple[dict[tuple[int, int], dict[str, Any]], set[tuple[int, int, int]], set[tuple[int, int]]]:
    """返回 (zone_at, aisle, lift)。

    - zone_at: {(row, col): zone}，标记每个格子属于哪个四向车库（判「车库内」用）。
    - aisle: {(row, col, layer)} 车库内该层可水平通行的巷道格（含电梯竖井格，竖井在任一层都可停留）。
    - lift: {(row, col)} 电梯竖井格（可跨层）。
    """
    cell_type: dict[tuple[int, int], str] = {}
    for cell in topology.get("cells", []):
        if normalize_floor(cell.get("floor")) == DEFAULT_FLOOR:
            cell_type[(int(cell["row"]), int(cell["col"]))] = cell.get("type", "road")

    zone_at: dict[tuple[int, int], dict[str, Any]] = {}
    aisle: set[tuple[int, int, int]] = set()
    lift: set[tuple[int, int]] = set()
    for zone in topology.get("zones", []):
        if zone.get("type") != "shuttle":
            continue
        x = int(zone["x"])
        y = int(zone["y"])
        width = int(zone["width"])
        height = int(zone["height"])
        for row in range(y, y + height):
            for col in range(x, x + width):
                zone_at[(row, col)] = zone

        removed = set(zone.get("removedSlots") or [])
        lifts = set(zone.get("slotLifts") or [])
        for anchor_row in range(y, y + height - 1, 2):
            for anchor_col in range(x, x + width - 1, 2):
                key = f"{anchor_row},{anchor_col}"
                if key in removed:
                    continue
                cells = [(anchor_row + dr, anchor_col + dc) for dr in range(2) for dc in range(2)]
                if key in lifts:
                    for cell in cells:
                        lift.add(cell)
                        for layer in range(1, SHUTTLE_LAYERS + 1):
                            aisle.add((cell[0], cell[1], layer))
                    continue
                mask = shuttle_slot_aisle_mask(zone, cell_type, anchor_row, anchor_col)
                for layer in range(1, SHUTTLE_LAYERS + 1):
                    if int(mask) & (1 << (layer - 1)):
                        for cell in cells:
                            aisle.add((cell[0], cell[1], layer))
    return zone_at, aisle, lift


def shuttle_rack_slots(topology: dict[str, Any]) -> list[tuple[str, int]]:
    """四向车库里的货位（非巷道、非电梯、非 removed 的 2×2 槽）。返回 [(cellId, layer), ...]。"""
    cell_type: dict[tuple[int, int], str] = {}
    for cell in topology.get("cells", []):
        if normalize_floor(cell.get("floor")) == DEFAULT_FLOOR:
            cell_type[(int(cell["row"]), int(cell["col"]))] = cell.get("type", "road")

    result: list[tuple[str, int]] = []
    for zone in topology.get("zones", []):
        if zone.get("type") != "shuttle":
            continue
        x = int(zone["x"])
        y = int(zone["y"])
        width = int(zone["width"])
        height = int(zone["height"])
        removed = set(zone.get("removedSlots") or [])
        lifts = set(zone.get("slotLifts") or [])
        for anchor_row in range(y, y + height - 1, 2):
            for anchor_col in range(x, x + width - 1, 2):
                key = f"{anchor_row},{anchor_col}"
                if key in removed or key in lifts:
                    continue
                mask = shuttle_slot_aisle_mask(zone, cell_type, anchor_row, anchor_col)
                rack_layers = [layer for layer in range(1, SHUTTLE_LAYERS + 1) if not (int(mask) & (1 << (layer - 1)))]
                if rack_layers:
                    result.append((cell_id(anchor_row, anchor_col, DEFAULT_FLOOR), rack_layers[0]))
    return result


def shortest_path_shuttle(
    topology: dict[str, Any],
    start_cell: str,
    start_layer: int,
    goal_cell: str,
    goal_layer: int,
    policy: str = "astar",
) -> list[tuple[int, int, int]]:
    """四向车 A*：状态为 (row, col, layer)。车库内只在巷道格水平移动、经电梯竖井跨层；
    车库外视为地面层（layer=1）的普通空格。返回 [(row, col, layer), ...]，首元素为起点。"""
    try:
        _, start_row, start_col = parse_cell_id(start_cell)
        _, goal_row, goal_col = parse_cell_id(goal_cell)
    except ValueError:
        return []

    zone_at, aisle, lift = build_shuttle_lookup(topology)

    blocked: set[tuple[int, int]] = set()
    conveyor: set[tuple[int, int]] = set()
    for cell in topology.get("cells", []):
        if cell.get("type") == "blocked" or not cell.get("walkable", True):
            blocked.add((int(cell["row"]), int(cell["col"])))
        elif cell.get("type") == "conveyor":
            conveyor.add((int(cell["row"]), int(cell["col"])))

    grid = topology.get("grid", {})
    rows = int(grid.get("rows") or 0)
    cols = int(grid.get("cols") or 0)

    start = (int(start_row), int(start_col), int(start_layer))
    goal = (int(goal_row), int(goal_col), int(goal_layer))

    def is_walkable(state: tuple[int, int, int]) -> bool:
        row, col, layer = state
        if state == start or state == goal:
            return True
        zone = zone_at.get((row, col))
        if zone is not None:
            # 车库内：巷道格（或电梯竖井格，已在 aisle 里登记）。
            return (row, col, layer) in aisle
        # 车库外：仅地面层、非 blocked/输送带 的普通空格。
        if layer != 1:
            return False
        if (row, col) in blocked or (row, col) in conveyor:
            return False
        if rows > 0 and cols > 0 and not (0 <= row < rows and 0 <= col < cols):
            return False
        return True

    if start == goal:
        return [start]

    frontier: list[tuple[float, tuple[int, int, int]]] = [(0.0, start)]
    came_from: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    cost_so_far: dict[tuple[int, int, int], float] = {start: 0.0}

    while frontier:
        _, current = heapq.heappop(frontier)
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        row, col, layer = current
        neighbors: list[tuple[int, int, int]] = []
        for d_row, d_col in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbors.append((row + d_row, col + d_col, layer))
        # 电梯竖井：跨层（仅车库内格子才有 lift 标记，外面不会误触发）。
        if (row, col) in lift:
            if layer > 1:
                neighbors.append((row, col, layer - 1))
            if layer < SHUTTLE_LAYERS:
                neighbors.append((row, col, layer + 1))

        for nxt in neighbors:
            if not is_walkable(nxt):
                continue
            new_cost = cost_so_far[current] + 1.0
            if nxt not in cost_so_far or new_cost < cost_so_far[nxt]:
                cost_so_far[nxt] = new_cost
                priority = (
                    new_cost + abs(nxt[0] - goal[0]) + abs(nxt[1] - goal[1]) + abs(nxt[2] - goal[2])
                    if policy == "astar"
                    else new_cost
                )
                heapq.heappush(frontier, (priority, nxt))
                came_from[nxt] = current

    return []
