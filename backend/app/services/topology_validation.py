from __future__ import annotations

from collections import deque
from typing import Any

from .pathfinding import node_to_cell_id, shortest_path

DEFAULT_FLOOR = "1F"
DEVICE_NODE_TYPES = {"ctu_robot", "shuttle_robot"}


def _floor(value: Any) -> str:
    return str(value or DEFAULT_FLOOR)


def _cell_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {cell["id"]: cell for cell in topology.get("cells", [])}


def _node_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in topology.get("nodes", [])}


def _cell_at(cells: list[dict[str, Any]], row: int, col: int, floor: str = DEFAULT_FLOOR) -> dict[str, Any] | None:
    floor_value = _floor(floor)
    return next((cell for cell in cells if cell["row"] == row and cell["col"] == col and _floor(cell.get("floor")) == floor_value), None)


def _walkable_positions(topology: dict[str, Any]) -> dict[tuple[str, int, int], dict[str, Any]]:
    return {
        (_floor(cell.get("floor")), cell["row"], cell["col"]): cell
        for cell in topology.get("cells", [])
        if cell.get("walkable", True) and cell.get("type") != "blocked"
    }


def _has_path(topology: dict[str, Any], start_node: dict[str, Any], goal_node: dict[str, Any]) -> bool:
    try:
        start = node_to_cell_id(topology, start_node["id"])
        goal = node_to_cell_id(topology, goal_node["id"])
    except KeyError:
        return False
    return bool(shortest_path(topology, start, goal))


def _has_adjacent_medium(
    walkable: dict[tuple[str, int, int], dict[str, Any]],
    row: int,
    col: int,
    allowed_types: set[str],
    include_self: bool = False,
    floor: str = DEFAULT_FLOOR,
) -> bool:
    offsets = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)) if include_self else ((1, 0), (-1, 0), (0, 1), (0, -1))
    floor_value = _floor(floor)
    for d_row, d_col in offsets:
        cell = walkable.get((floor_value, row + d_row, col + d_col))
        if cell and cell.get("type") in allowed_types:
            return True
    return False


def validate_topology_business_rules(topology: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    grid = topology.get("grid", {})
    rows = int(grid.get("rows", 0))
    cols = int(grid.get("cols", 0))
    cells = topology.get("cells", [])
    nodes = topology.get("nodes", [])
    resources = topology.get("resources", [])

    if rows <= 0 or cols <= 0:
        errors.append("网格行列数必须大于 0。")
    if not cells:
        errors.append("拓扑缺少可编辑格子，请先铺设道路、障碍或输送带。")
    if not nodes:
        errors.append("拓扑缺少业务节点，请至少添加库位节点和站台节点。")

    cell_lookup = _cell_lookup(topology)
    node_lookup = _node_lookup(topology)
    walkable = _walkable_positions(topology)

    stations = [node for node in nodes if node.get("type") == "station"]
    storages = [node for node in nodes if node.get("type") == "storage"]
    entries = [node for node in nodes if node.get("type") == "entry"]
    conveyor_gates = [node for node in nodes if node.get("type") == "conveyor_gate"]
    buffers = [node for node in nodes if node.get("type") == "buffer"]
    device_nodes = [node for node in nodes if node.get("type") in DEVICE_NODE_TYPES]

    if not stations:
        errors.append("至少需要一个站台节点。")
    if not storages:
        errors.append("至少需要一个库位节点。")
    if not entries:
        warnings.append("当前没有入口节点，插单和交付语义会变弱。")
    if not device_nodes:
        warnings.append("当前拓扑还没有放置 CTU 或四向车设备，暂时不能直接启动仿真。")

    for node in nodes:
        cell = _cell_at(cells, node["row"], node["col"], _floor(node.get("floor")))
        if cell is None:
            errors.append(f"节点 {node['id']} 没有落在已定义格子上。")
            continue
        if not cell.get("walkable", True) or cell.get("type") == "blocked":
            errors.append(f"节点 {node['id']} 落在不可通行格子上。")

    for buffer_node in buffers:
        cell = _cell_at(cells, buffer_node["row"], buffer_node["col"], _floor(buffer_node.get("floor")))
        if cell is None:
            continue
        if cell.get("type") not in {"road", "conveyor"}:
            errors.append(f"缓存节点 {buffer_node['id']} 必须放在通行格或输送带格上。")
            continue
        if not _has_adjacent_medium(
            walkable,
            buffer_node["row"],
            buffer_node["col"],
            {"road", "conveyor"},
            include_self=True,
            floor=_floor(buffer_node.get("floor")),
        ):
            errors.append(f"缓存节点 {buffer_node['id']} 周边缺少可通行接驳格。")

    if stations and storages and not any(_has_path(topology, storage, station) for storage in storages for station in stations):
        errors.append("当前拓扑中库位节点与站台节点之间不存在可达路径。")

    conveyor_cells = [cell for cell in cells if cell.get("type") == "conveyor" and cell.get("walkable", True)]
    if conveyor_cells:
        conveyor_positions = {(_floor(cell.get("floor")), cell["row"], cell["col"]) for cell in conveyor_cells}
        queue = deque([next(iter(conveyor_positions))])
        seen = {queue[0]}
        while queue:
            floor, row, col = queue.popleft()
            for d_row, d_col in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (floor, row + d_row, col + d_col)
                if nxt in conveyor_positions and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        if seen != conveyor_positions:
            errors.append("输送带格子存在断裂，请保持输送带连续。")

    for gate in conveyor_gates:
        if not _has_adjacent_medium(
            walkable,
            gate["row"],
            gate["col"],
            {"conveyor"},
            include_self=True,
            floor=_floor(gate.get("floor")),
        ):
            errors.append(f"输送带闸口 {gate['id']} 必须位于输送带格上或紧邻输送带。")

    buffer_resource_bound = False
    for resource in resources:
        if resource.get("type") == "buffer_slot":
            buffer_resource_bound = True
            for cell_id in resource.get("cellIds", []):
                cell = cell_lookup.get(cell_id)
                if cell is None:
                    continue
                if cell.get("type") not in {"road", "conveyor"} or not cell.get("walkable", True):
                    errors.append(f"缓存资源 {resource['id']} 绑定了不可用格子 {cell_id}。")
            for node_id in resource.get("nodeIds", []):
                node = node_lookup.get(node_id)
                if node is None:
                    continue
                if node.get("type") != "buffer":
                    errors.append(f"缓存资源 {resource['id']} 绑定的节点 {node_id} 不是缓存节点。")
        if resource.get("type") == "station":
            if not resource.get("nodeIds"):
                errors.append(f"站台资源 {resource['id']} 必须绑定站台节点。")
            for node_id in resource.get("nodeIds", []):
                node = node_lookup.get(node_id)
                if node is None:
                    continue
                if node.get("type") != "station":
                    errors.append(f"站台资源 {resource['id']} 绑定的节点 {node_id} 不是站台节点。")
        if resource.get("type") == "lift":
            lift_nodes = [node_lookup.get(node_id) for node_id in resource.get("nodeIds", [])]
            lift_nodes = [node for node in lift_nodes if node is not None]
            lift_floors = {_floor(node.get("floor")) for node in lift_nodes}
            if len(lift_nodes) < 2 or len(lift_floors) < 2:
                errors.append(f"提升机资源 {resource['id']} 需要绑定至少两个不同楼层的提升机节点。")
            for node in lift_nodes:
                if node.get("type") != "lift":
                    errors.append(f"提升机资源 {resource['id']} 绑定的节点 {node['id']} 不是提升机节点。")
        if resource.get("type") == "conveyor_segment":
            if not resource.get("cellIds"):
                errors.append(f"输送线资源 {resource['id']} 必须绑定 conveyor 格子。")
            for cell_id in resource.get("cellIds", []):
                cell = cell_lookup.get(cell_id)
                if cell is None:
                    continue
                if cell.get("type") != "conveyor":
                    errors.append(f"输送线资源 {resource['id']} 绑定了非 conveyor 格子 {cell_id}。")

    if buffers and not buffer_resource_bound:
        warnings.append("存在缓存节点，但没有对应的缓存资源。")

    return {"valid": not errors, "errors": errors, "warnings": warnings}
