from __future__ import annotations

from collections import Counter, deque
from typing import Any


DEFAULT_FLOOR = "1F"
CandidateStatus = str


def _floor(item: dict[str, Any]) -> str:
    return str(item.get("floor") or DEFAULT_FLOOR)


def _zone_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(zone["id"]): zone for zone in topology.get("zones", []) if zone.get("id")}


def _node_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(node["id"]): node for node in topology.get("nodes", []) if node.get("id")}


def _cell_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(cell["id"]): cell for cell in topology.get("cells", []) if cell.get("id")}


def _ctu_road_components(topology: dict[str, Any]) -> dict[tuple[str, int, int], int]:
    zones = _zone_lookup(topology)
    roads = {
        (_floor(cell), int(cell["row"]), int(cell["col"]))
        for cell in topology.get("cells", [])
        if cell.get("type") == "road"
        and cell.get("walkable", True)
        and zones.get(cell.get("zoneId"), {}).get("type") == "ctu"
    }
    sizes: dict[tuple[str, int, int], int] = {}
    remaining = set(roads)
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component = {seed}
        queue = deque([seed])
        while queue:
            floor, row, col = queue.popleft()
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                neighbor = (floor, row + dr, col + dc)
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        for position in component:
            sizes[position] = len(component)
    return sizes


def _rack_face_relation(node: dict[str, Any], zone: dict[str, Any], cell: dict[str, Any]) -> tuple[bool, bool]:
    """Return (on_pick_face, orientation_is_explicitly_derivable).

    One-cell-wide or one-cell-deep zones encode a rack strip. Its long sides are
    the only derivable pick faces. Broader areas do not encode rack orientation;
    an adjacent road can only become a manually reviewed candidate.
    """

    row, col = int(node["row"]), int(node["col"])
    cell_row, cell_col = int(cell["row"]), int(cell["col"])
    x, y = int(zone["x"]), int(zone["y"])
    width, height = int(zone["width"]), int(zone["height"])
    if width == 1 and height >= 1:
        node_in_strip = col == x and y <= row < y + height
        return node_in_strip and cell_row == row and cell_col in {x - 1, x + 1}, True
    if height == 1 and width >= 1:
        node_in_strip = row == y and x <= col < x + width
        return node_in_strip and cell_col == col and cell_row in {y - 1, y + 1}, True
    adjacent = abs(cell_row - row) + abs(cell_col - col) == 1
    return adjacent, False


def evaluate_access_candidate(
    topology: dict[str, Any],
    storage_node_id: str,
    access_cell_id: str,
) -> dict[str, Any]:
    nodes = _node_lookup(topology)
    cells = _cell_lookup(topology)
    zones = _zone_lookup(topology)
    node = nodes.get(storage_node_id)
    cell = cells.get(access_cell_id)
    reasons: list[str] = []
    reason_codes: list[str] = []

    def reject(code: str, message: str) -> None:
        reason_codes.append(code)
        reasons.append(message)

    if not node or node.get("type") != "storage":
        reject("STORAGE_NODE_MISSING", "目标不是当前拓扑中的真实 Storage Node。")
    if not cell:
        reject("ACCESS_CELL_MISSING", "候选 Cell 不存在于当前拓扑。")
    if not node or not cell:
        return _candidate_payload(topology, storage_node_id, access_cell_id, node, cell, "rejected", reason_codes, reasons)

    if cell.get("type") != "road" or not cell.get("walkable", True):
        reject("ACCESS_CELL_NOT_ROAD", "候选位置不是可通行 road Cell。")
    if _floor(cell) != _floor(node):
        reject("ACCESS_FLOOR_MISMATCH", "候选道路与库位不在同一楼层。")

    node_zone = zones.get(node.get("zoneId"))
    cell_zone = zones.get(cell.get("zoneId"))
    if not node_zone or node_zone.get("type") != "ctu":
        reject("STORAGE_NOT_IN_CTU_ZONE", "库位不属于真实 CTU 库区。")
    if not cell_zone:
        reject("ACCESS_ZONE_UNASSIGNED", "候选道路尚未归属 CTU 库区，只能进入审核草案，不能保存为正式映射。")
    elif cell_zone.get("type") != "ctu":
        reject("ACCESS_IN_NON_CTU_ZONE", "候选道路属于非 CTU 库区。")

    if int(cell["row"]) == int(node["row"]) and int(cell["col"]) == int(node["col"]):
        reject("ACCESS_OVERLAPS_STORAGE", "车辆停靠点不能与红色物理库位重合。")
    if any(
        other.get("type") == "storage"
        and _floor(other) == _floor(cell)
        and int(other["row"]) == int(cell["row"])
        and int(other["col"]) == int(cell["col"])
        for other in topology.get("nodes", [])
    ):
        reject("ACCESS_OCCUPIED_BY_STORAGE", "候选道路位置被物理库位占用。")

    orientation_known = False
    on_pick_face = False
    if node_zone:
        on_pick_face, orientation_known = _rack_face_relation(node, node_zone, cell)
    if not on_pick_face:
        reject("ACCESS_NOT_ON_PICK_FACE", "候选道路不在该货架可证明的取货面上，不能跨越货架或空隙取货。")

    ctu_components = _ctu_road_components(topology)
    position = (_floor(cell), int(cell["row"]), int(cell["col"]))
    if cell_zone and cell_zone.get("type") == "ctu" and ctu_components.get(position, 0) < 2:
        reject("ACCESS_ROAD_ISOLATED", "候选 CTU road Cell 未连接到至少另一个 CTU road Cell。")

    hard_rejection_codes = {
        "STORAGE_NODE_MISSING", "ACCESS_CELL_MISSING", "ACCESS_CELL_NOT_ROAD", "ACCESS_FLOOR_MISMATCH",
        "STORAGE_NOT_IN_CTU_ZONE", "ACCESS_IN_NON_CTU_ZONE", "ACCESS_OVERLAPS_STORAGE",
        "ACCESS_ZONE_UNASSIGNED", "ACCESS_OCCUPIED_BY_STORAGE", "ACCESS_NOT_ON_PICK_FACE", "ACCESS_ROAD_ISOLATED",
    }
    if any(code in hard_rejection_codes for code in reason_codes):
        status: CandidateStatus = "rejected"
    elif not orientation_known:
        status = "review_required"
        reason_codes.append("RACK_FACE_UNDECLARED")
        reasons.append("库区没有明确货架朝向；需人工确认取货面后才能保存。")
    else:
        status = "eligible"
        reason_codes.extend(["REAL_CTU_ROAD", "RACK_FACE_VERIFIED", "ROAD_NETWORK_CONNECTED"])
        reasons.extend(["真实可通行 CTU road Cell。", "位于可证明的货架取货面。", "已连接到 CTU road 网络。"])

    return _candidate_payload(topology, storage_node_id, access_cell_id, node, cell, status, reason_codes, reasons)


def _candidate_payload(
    topology: dict[str, Any],
    storage_node_id: str,
    access_cell_id: str,
    node: dict[str, Any] | None,
    cell: dict[str, Any] | None,
    status: CandidateStatus,
    reason_codes: list[str],
    reasons: list[str],
) -> dict[str, Any]:
    distance = None
    if node and cell and _floor(node) == _floor(cell):
        distance = abs(int(node["row"]) - int(cell["row"])) + abs(int(node["col"]) - int(cell["col"]))
    return {
        "topology_id": topology.get("id", ""),
        "topology_version": str(topology.get("meta", {}).get("version", "unknown")),
        "storage_node_id": storage_node_id,
        "access_cell_id": access_cell_id,
        "floor": _floor(cell or node or {}),
        "row": int(cell["row"]) if cell else None,
        "col": int(cell["col"]) if cell else None,
        "zone_id": cell.get("zoneId") if cell else None,
        "distance_cells": distance,
        "status": status,
        "reason_codes": reason_codes,
        "reasons": reasons,
    }


def access_candidates(
    topology: dict[str, Any],
    storage_node_id: str,
    *,
    limit: int = 12,
) -> dict[str, Any]:
    node = _node_lookup(topology).get(storage_node_id)
    if not node or node.get("type") != "storage":
        raise ValueError(f"Storage node {storage_node_id} does not exist")
    evaluations = [
        evaluate_access_candidate(topology, storage_node_id, str(cell["id"]))
        for cell in topology.get("cells", [])
        if cell.get("type") == "road" and cell.get("walkable", True) and _floor(cell) == _floor(node)
    ]
    rank = {"eligible": 0, "review_required": 1, "rejected": 2}
    evaluations.sort(key=lambda item: (rank[item["status"]], item["distance_cells"] or 0, item["access_cell_id"]))
    counts = Counter(item["status"] for item in evaluations)
    return {
        "topology_id": topology["id"],
        "topology_version": str(topology.get("meta", {}).get("version", "unknown")),
        "storage_node_id": storage_node_id,
        "eligible_count": counts["eligible"],
        "review_required_count": counts["review_required"],
        "rejected_count": counts["rejected"],
        "candidates": evaluations[:limit],
    }


def audit_access_candidates(topology: dict[str, Any]) -> dict[str, Any]:
    cells_at = {
        (_floor(cell), int(cell["row"]), int(cell["col"])): cell
        for cell in topology.get("cells", [])
        if cell.get("type") == "road" and cell.get("walkable", True)
    }
    storage_nodes = [node for node in topology.get("nodes", []) if node.get("type") == "storage"]
    counts = Counter()
    reason_counts = Counter()
    for node in storage_nodes:
        floor, row, col = _floor(node), int(node["row"]), int(node["col"])
        neighboring_cells = [
            cells_at[position]
            for position in ((floor, row + 1, col), (floor, row - 1, col), (floor, row, col + 1), (floor, row, col - 1))
            if position in cells_at
        ]
        evaluations = [evaluate_access_candidate(topology, str(node["id"]), str(cell["id"])) for cell in neighboring_cells]
        if any(item["status"] == "eligible" for item in evaluations):
            counts["eligible"] += 1
        elif any(item["status"] == "review_required" for item in evaluations):
            counts["review_required"] += 1
        else:
            counts["unmappable"] += 1
            if not evaluations:
                reason_counts["NO_ADJACENT_REAL_ROAD"] += 1
            else:
                reason_counts.update(code for item in evaluations for code in item["reason_codes"])
    return {
        "topology_id": topology["id"],
        "topology_version": str(topology.get("meta", {}).get("version", "unknown")),
        "storage_location_count": len(storage_nodes),
        "eligible_location_count": counts["eligible"],
        "review_required_location_count": counts["review_required"],
        "unmappable_location_count": counts["unmappable"],
        "reason_counts": dict(sorted(reason_counts.items())),
    }
