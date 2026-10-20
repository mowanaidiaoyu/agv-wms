from __future__ import annotations

from collections import deque
from typing import Any


DEFAULT_FLOOR = "1F"
DEVICE_NODE_TYPES = {"ctu_robot", "shuttle_robot"}
CATEGORY_LABELS = {
    "structure": "拓扑结构",
    "accessibility": "取货可达性",
    "runtime": "仿真运行准备",
}


def _floor(value: Any) -> str:
    return str(value or DEFAULT_FLOOR)


def _cell_at_lookup(topology: dict[str, Any]) -> dict[tuple[str, int, int], dict[str, Any]]:
    return {
        (_floor(cell.get("floor")), int(cell.get("row", -1)), int(cell.get("col", -1))): cell
        for cell in topology.get("cells", [])
    }


def _walkable_positions(topology: dict[str, Any]) -> dict[tuple[str, int, int], dict[str, Any]]:
    return {
        (_floor(cell.get("floor")), int(cell["row"]), int(cell["col"])): cell
        for cell in topology.get("cells", [])
        if cell.get("walkable", True) and cell.get("type") != "blocked"
    }


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


def _positions_are_connected(positions: set[tuple[str, int, int]]) -> bool:
    if not positions:
        return True
    queue = deque([next(iter(positions))])
    seen = {queue[0]}
    while queue:
        floor, row, col = queue.popleft()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (floor, row + dr, col + dc)
            if nxt in positions and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen == positions


def validate_topology_business_rules(
    topology: dict[str, Any],
    *,
    access_mappings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate structure, optional pick accessibility, and runtime readiness separately.

    A physical storage node is not a traversable cell. It must be inside the
    warehouse and belong to a real zone, but it does not need a TopologyCell at
    the same position. CTU access is represented by an explicit mapping.
    """

    issues: list[dict[str, Any]] = []

    def add(
        category: str,
        severity: str,
        code: str,
        message: str,
        entity_kind: str | None = None,
        entity_id: str | None = None,
    ) -> None:
        issues.append({
            "category": category,
            "severity": severity,
            "code": code,
            "message": message,
            "entity_kind": entity_kind,
            "entity_id": entity_id,
        })

    grid = topology.get("grid", {})
    rows = int(grid.get("rows", 0))
    cols = int(grid.get("cols", 0))
    cells = topology.get("cells", [])
    nodes = topology.get("nodes", [])
    zones = topology.get("zones", [])
    resources = topology.get("resources", [])
    floors = set(topology.get("meta", {}).get("floors") or [DEFAULT_FLOOR])
    zone_lookup = {zone.get("id"): zone for zone in zones if zone.get("id")}
    cell_lookup = {cell.get("id"): cell for cell in cells if cell.get("id")}
    node_lookup = {node.get("id"): node for node in nodes if node.get("id")}
    cells_at = _cell_at_lookup(topology)
    walkable = _walkable_positions(topology)

    if rows <= 0 or cols <= 0:
        add("structure", "error", "GRID_INVALID", "网格行列数必须大于 0。")
    if not cells:
        add("structure", "error", "CELLS_MISSING", "拓扑缺少可编辑格子，请先铺设道路、障碍或输送带。")
    if not nodes:
        add("structure", "error", "NODES_MISSING", "拓扑缺少业务节点。")

    seen_cell_ids: set[str] = set()
    for cell in cells:
        cell_id = str(cell.get("id") or "")
        if not cell_id:
            add("structure", "error", "CELL_ID_MISSING", "存在没有合法 ID 的格子。", "cell")
        elif cell_id in seen_cell_ids:
            add("structure", "error", "CELL_ID_DUPLICATE", f"格子 ID {cell_id} 重复。", "cell", cell_id)
        seen_cell_ids.add(cell_id)
        row, col = int(cell.get("row", -1)), int(cell.get("col", -1))
        if row < 0 or row >= rows or col < 0 or col >= cols:
            add("structure", "error", "CELL_OUT_OF_BOUNDS", f"格子 {cell_id} 超出仓库坐标边界。", "cell", cell_id)
        if _floor(cell.get("floor")) not in floors:
            add("structure", "error", "CELL_FLOOR_INVALID", f"格子 {cell_id} 使用了未定义楼层。", "cell", cell_id)
        if cell.get("zoneId") and cell.get("zoneId") not in zone_lookup:
            add("structure", "error", "CELL_ZONE_MISSING", f"格子 {cell_id} 引用了不存在的库区 {cell.get('zoneId')}。", "cell", cell_id)

    seen_node_ids: set[str] = set()
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            add("structure", "error", "NODE_ID_MISSING", "存在没有合法 ID 的节点。", "node")
        elif node_id in seen_node_ids:
            add("structure", "error", "NODE_ID_DUPLICATE", f"节点 ID {node_id} 重复。", "node", node_id)
        seen_node_ids.add(node_id)
        row, col = int(node.get("row", -1)), int(node.get("col", -1))
        floor = _floor(node.get("floor"))
        if row < 0 or row >= rows or col < 0 or col >= cols:
            add("structure", "error", "NODE_OUT_OF_BOUNDS", f"节点 {node_id} 超出仓库坐标边界。", "node", node_id)
        if floor not in floors:
            add("structure", "error", "NODE_FLOOR_INVALID", f"节点 {node_id} 使用了未定义楼层 {floor}。", "node", node_id)
        if node.get("type") == "storage" and not node.get("zoneId"):
            add("structure", "error", "STORAGE_ZONE_MISSING", f"库位节点 {node_id} 必须属于真实库区。", "node", node_id)
        elif node.get("zoneId") and node.get("zoneId") not in zone_lookup:
            add("structure", "error", "NODE_ZONE_MISSING", f"节点 {node_id} 引用了不存在的库区 {node.get('zoneId')}。", "node", node_id)

        if node.get("type") == "storage":
            continue
        cell = cells_at.get((floor, row, col))
        if cell is None:
            add("structure", "error", "OPERATIONAL_NODE_CELL_MISSING", f"运行节点 {node_id} 没有落在已定义格子上。", "node", node_id)
        elif not cell.get("walkable", True) or cell.get("type") == "blocked":
            add("structure", "error", "OPERATIONAL_NODE_BLOCKED", f"运行节点 {node_id} 落在不可通行格子上。", "node", node_id)

    stations = [node for node in nodes if node.get("type") == "station"]
    storages = [node for node in nodes if node.get("type") == "storage"]
    entries = [node for node in nodes if node.get("type") == "entry"]
    conveyor_gates = [node for node in nodes if node.get("type") == "conveyor_gate"]
    buffers = [node for node in nodes if node.get("type") == "buffer"]
    device_nodes = [node for node in nodes if node.get("type") in DEVICE_NODE_TYPES]

    if not storages:
        add("structure", "error", "STORAGE_MISSING", "至少需要一个库位节点。")
    if not stations:
        add("runtime", "warning", "STATION_MISSING", "正式任务仿真至少需要一个站台节点；库存查看和普通路径演示不受影响。")
    if not entries:
        add("runtime", "warning", "ENTRY_MISSING", "当前没有入口节点，插单和交付语义会变弱。")
    if not device_nodes:
        add("runtime", "warning", "ROBOT_MISSING", "当前拓扑还没有放置 CTU 或四向车设备，不能直接启动正式仿真。")

    for buffer_node in buffers:
        cell = cells_at.get((_floor(buffer_node.get("floor")), int(buffer_node["row"]), int(buffer_node["col"])))
        if cell and cell.get("type") not in {"road", "conveyor"}:
            add("structure", "error", "BUFFER_CELL_INVALID", f"缓存节点 {buffer_node['id']} 必须放在通行格或输送带格上。", "node", buffer_node["id"])
        elif cell and not _has_adjacent_medium(walkable, int(buffer_node["row"]), int(buffer_node["col"]), {"road", "conveyor"}, include_self=True, floor=_floor(buffer_node.get("floor"))):
            add("structure", "error", "BUFFER_ACCESS_MISSING", f"缓存节点 {buffer_node['id']} 周边缺少可通行接驳格。", "node", buffer_node["id"])

    for gate in conveyor_gates:
        if not _has_adjacent_medium(walkable, int(gate["row"]), int(gate["col"]), {"conveyor"}, include_self=True, floor=_floor(gate.get("floor"))):
            add("structure", "error", "CONVEYOR_GATE_DISCONNECTED", f"输送带闸口 {gate['id']} 必须位于输送带格上或紧邻输送带。", "node", gate["id"])

    buffer_resource_bound = False
    bound_conveyor_cells: set[str] = set()
    for resource in resources:
        resource_id = str(resource.get("id") or "")
        resource_type = resource.get("type")
        for cell_id in resource.get("cellIds", []):
            if cell_id not in cell_lookup:
                add(
                    "structure", "error", "RESOURCE_CELL_REFERENCE_MISSING",
                    f"资源 {resource_id} 引用了不存在的格子 {cell_id}。", "resource", resource_id,
                )
        for node_id in resource.get("nodeIds", []):
            if node_id not in node_lookup:
                add(
                    "structure", "error", "RESOURCE_NODE_REFERENCE_MISSING",
                    f"资源 {resource_id} 引用了不存在的节点 {node_id}。", "resource", resource_id,
                )
        if resource_type == "buffer_slot":
            buffer_resource_bound = True
            for cell_id in resource.get("cellIds", []):
                cell = cell_lookup.get(cell_id)
                if cell and (cell.get("type") not in {"road", "conveyor"} or not cell.get("walkable", True)):
                    add("structure", "error", "BUFFER_RESOURCE_CELL_INVALID", f"缓存资源 {resource_id} 绑定了不可用格子 {cell_id}。", "resource", resource_id)
            for node_id in resource.get("nodeIds", []):
                node = node_lookup.get(node_id)
                if node and node.get("type") != "buffer":
                    add("structure", "error", "BUFFER_RESOURCE_NODE_INVALID", f"缓存资源 {resource_id} 绑定的节点 {node_id} 不是缓存节点。", "resource", resource_id)
        elif resource_type == "station":
            if not resource.get("nodeIds"):
                add("structure", "error", "STATION_RESOURCE_EMPTY", f"站台资源 {resource_id} 必须绑定站台节点。", "resource", resource_id)
            for node_id in resource.get("nodeIds", []):
                node = node_lookup.get(node_id)
                if node and node.get("type") != "station":
                    add("structure", "error", "STATION_RESOURCE_NODE_INVALID", f"站台资源 {resource_id} 绑定的节点 {node_id} 不是站台节点。", "resource", resource_id)
        elif resource_type == "lift":
            lift_nodes = [node_lookup.get(node_id) for node_id in resource.get("nodeIds", [])]
            lift_nodes = [node for node in lift_nodes if node is not None]
            if len(lift_nodes) < 2 or len({_floor(node.get("floor")) for node in lift_nodes}) < 2:
                add("structure", "error", "LIFT_RESOURCE_INCOMPLETE", f"提升机资源 {resource_id} 需要绑定至少两个不同楼层的提升机节点。", "resource", resource_id)
            for node in lift_nodes:
                if node.get("type") != "lift":
                    add("structure", "error", "LIFT_RESOURCE_NODE_INVALID", f"提升机资源 {resource_id} 绑定的节点 {node['id']} 不是提升机节点。", "resource", resource_id)
        elif resource_type == "conveyor_segment":
            cell_ids = resource.get("cellIds", [])
            if not cell_ids:
                add("structure", "error", "CONVEYOR_RESOURCE_EMPTY", f"输送线资源 {resource_id} 必须绑定 conveyor 格子。", "resource", resource_id)
                continue
            positions: set[tuple[str, int, int]] = set()
            for cell_id in cell_ids:
                bound_conveyor_cells.add(cell_id)
                cell = cell_lookup.get(cell_id)
                if cell and cell.get("type") != "conveyor":
                    add("structure", "error", "CONVEYOR_RESOURCE_CELL_INVALID", f"输送线资源 {resource_id} 绑定了无效 conveyor 格子 {cell_id}。", "resource", resource_id)
                elif cell:
                    positions.add((_floor(cell.get("floor")), int(cell["row"]), int(cell["col"])))
            if positions and not _positions_are_connected(positions):
                add("structure", "error", "CONVEYOR_RESOURCE_DISCONNECTED", f"输送线资源 {resource_id} 内部存在断裂。", "resource", resource_id)

    if buffers and not buffer_resource_bound:
        add("runtime", "warning", "BUFFER_RESOURCE_MISSING", "存在缓存节点，但没有对应的缓存资源。")
    unbound_conveyors = [cell for cell in cells if cell.get("type") == "conveyor" and cell.get("id") not in bound_conveyor_cells]
    if unbound_conveyors:
        add("runtime", "warning", "CONVEYOR_CELLS_UNBOUND", f"有 {len(unbound_conveyors)} 个输送带格尚未绑定输送线资源；不要求不同输送线彼此连通。")

    if access_mappings is not None:
        mapping_lookup = {mapping.get("storage_node_id"): mapping for mapping in access_mappings}
        topology_version = str(topology.get("meta", {}).get("version", "unknown"))
        road_positions = {
            (_floor(cell.get("floor")), int(cell["row"]), int(cell["col"])): cell
            for cell in cells
            if cell.get("type") == "road" and cell.get("walkable", True)
        }
        for storage in storages:
            storage_id = storage["id"]
            mapping = mapping_lookup.get(storage_id)
            if not mapping:
                add("accessibility", "warning", "ACCESS_UNCONFIGURED", f"库位 {storage_id} 尚未配置取货停靠点。", "node", storage_id)
                continue
            access_cell = cell_lookup.get(mapping.get("access_cell_id"))
            if mapping.get("topology_version") != topology_version:
                add("accessibility", "error", "ACCESS_VERSION_MISMATCH", f"库位 {storage_id} 的停靠点映射对应旧拓扑版本。", "node", storage_id)
            elif not access_cell:
                add("accessibility", "error", "ACCESS_CELL_MISSING", f"库位 {storage_id} 的停靠点 {mapping.get('access_cell_id')} 不存在。", "node", storage_id)
            elif access_cell.get("type") != "road" or not access_cell.get("walkable", True):
                add("accessibility", "error", "ACCESS_CELL_NOT_ROAD", f"库位 {storage_id} 的停靠点不是可通行道路。", "node", storage_id)
            else:
                storage_pos = (_floor(storage.get("floor")), int(storage["row"]), int(storage["col"]))
                access_pos = (_floor(access_cell.get("floor")), int(access_cell["row"]), int(access_cell["col"]))
                if storage_pos[0] != access_pos[0] or abs(storage_pos[1] - access_pos[1]) + abs(storage_pos[2] - access_pos[2]) != 1:
                    add("accessibility", "error", "ACCESS_NOT_ADJACENT", f"库位 {storage_id} 的停靠点与库位不相邻。", "node", storage_id)
                elif not any((access_pos[0], access_pos[1] + dr, access_pos[2] + dc) in road_positions for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    add("accessibility", "error", "ACCESS_DISCONNECTED", f"库位 {storage_id} 的停靠点未连接到道路网络。", "node", storage_id)

    errors = [issue["message"] for issue in issues if issue["severity"] == "error"]
    warnings = [issue["message"] for issue in issues if issue["severity"] == "warning"]
    categories = []
    for category, label in CATEGORY_LABELS.items():
        category_issues = [issue for issue in issues if issue["category"] == category]
        categories.append({
            "category": category,
            "label": label,
            "error_count": sum(issue["severity"] == "error" for issue in category_issues),
            "warning_count": sum(issue["severity"] == "warning" for issue in category_issues),
            "issues": category_issues,
        })
    return {"valid": not errors, "errors": errors, "warnings": warnings, "categories": categories}
