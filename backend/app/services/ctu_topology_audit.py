from __future__ import annotations

from collections import Counter, deque
from typing import Any


DEFAULT_FLOOR = "1F"


def _floor(item: dict[str, Any]) -> str:
    return str(item.get("floor") or DEFAULT_FLOOR)


def _position(item: dict[str, Any]) -> tuple[str, int, int]:
    return (_floor(item), int(item["row"]), int(item["col"]))


def _road_class(cell: dict[str, Any], zones: dict[str, dict[str, Any]]) -> str:
    if cell.get("type") != "road" or not cell.get("walkable", True):
        return "invalid"
    zone = zones.get(str(cell.get("zoneId") or ""))
    if zone is None:
        return "unassigned"
    if zone.get("type") == "ctu":
        return "ctu"
    if zone.get("type") == "shuttle":
        return "shuttle"
    return "other"


def _components(cells: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_position = {_position(cell): cell for cell in cells}
    remaining = set(by_position)
    result: list[list[dict[str, Any]]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        queue = deque([seed])
        component_positions: list[tuple[str, int, int]] = []
        while queue:
            current = queue.popleft()
            component_positions.append(current)
            floor, row, col = current
            for neighbor in (
                (floor, row + 1, col),
                (floor, row - 1, col),
                (floor, row, col + 1),
                (floor, row, col - 1),
            ):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        result.append([by_position[position] for position in sorted(component_positions)])
    result.sort(key=lambda component: (-len(component), str(component[0].get("id", ""))))
    return result


def _storage_inside_zone(node: dict[str, Any], zone: dict[str, Any] | None) -> bool:
    if not zone or zone.get("type") != "ctu":
        return False
    return (
        int(zone["y"]) <= int(node["row"]) < int(zone["y"]) + int(zone["height"])
        and int(zone["x"]) <= int(node["col"]) < int(zone["x"]) + int(zone["width"])
    )


def _road_payload(cell: dict[str, Any], road_class: str, component_id: str, component_size: int) -> dict[str, Any]:
    return {
        "road_cell_id": str(cell["id"]),
        "floor": _floor(cell),
        "row": int(cell["row"]),
        "col": int(cell["col"]),
        "zone_id": cell.get("zoneId"),
        "road_class": road_class,
        "traversability": "walkable" if cell.get("walkable", True) else "blocked",
        "component_id": component_id,
        "component_size": component_size,
        "eligible_for_ctu": road_class == "ctu" and component_size >= 2,
    }


def _trial_area(
    topology: dict[str, Any],
    road_payloads: list[dict[str, Any]],
    ctu_storage_nodes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    unassigned_components: dict[str, list[dict[str, Any]]] = {}
    for road in road_payloads:
        if road["road_class"] == "unassigned":
            unassigned_components.setdefault(str(road["component_id"]), []).append(road)
    if not unassigned_components or len(ctu_storage_nodes) < 3:
        return None
    roads = min(
        unassigned_components.values(),
        key=lambda component: (-len(component), min(str(item["road_cell_id"]) for item in component)),
    )
    road_positions = [(str(item["floor"]), int(item["row"]), int(item["col"])) for item in roads]

    def distance(node: dict[str, Any]) -> tuple[int, str]:
        node_floor, node_row, node_col = _position(node)
        distances = [
            abs(node_row - row) + abs(node_col - col)
            for floor, row, col in road_positions
            if floor == node_floor
        ]
        return (min(distances) if distances else 10**9, str(node["id"]))

    storage_nodes = sorted(ctu_storage_nodes, key=distance)[:5]
    zone_ids = sorted({str(node.get("zoneId")) for node in storage_nodes if node.get("zoneId")})
    ordered_roads = sorted(roads, key=lambda item: (item["floor"], item["row"], item["col"], item["road_cell_id"]))
    return {
        "draft_key": f"{topology.get('id', 'topology')}-minimal-ctu-trial-area-v1",
        "status": "UNVERIFIED",
        "review_status": "draft",
        "vehicle_type": "ctu",
        "floor": str(roads[0]["floor"]),
        "zone_ids": zone_ids,
        "road_class": "unassigned",
        "road_cell_ids": [str(item["road_cell_id"]) for item in ordered_roads],
        "road_component_id": str(roads[0]["component_id"]),
        "vehicle_start_cell_id": str(ordered_roads[0]["road_cell_id"]),
        "storage_node_ids": [str(node["id"]) for node in storage_nodes],
        "pick_face": "unknown",
        "access_cell_id": None,
        "mapping_source": "topology_audit_draft",
        "eligible_for_planning": False,
        "reasons": [
            "候选道路是真实 road Cell，但 zoneId 未归属 CTU 库区。",
            "候选库位仅按几何距离选取，现有拓扑未声明货架取货面。",
            "车辆占用宽度、停靠空间和取货操作距离尚未定义。",
            "必须由研究生 B 确认并发布新拓扑版本后重新审计。",
        ],
    }


def audit_ctu_topology(topology: dict[str, Any]) -> dict[str, Any]:
    zones = {str(zone["id"]): zone for zone in topology.get("zones", []) if zone.get("id")}
    road_cells = [cell for cell in topology.get("cells", []) if cell.get("type") == "road"]
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in ("ctu", "shuttle", "unassigned", "other", "invalid")}
    for cell in road_cells:
        grouped[_road_class(cell, zones)].append(cell)

    road_payloads: list[dict[str, Any]] = []
    component_counts: dict[str, int] = {}
    for road_class in ("ctu", "shuttle", "unassigned", "other", "invalid"):
        for index, component in enumerate(_components(grouped[road_class]), start=1):
            component_id = f"{road_class}-component-{index}"
            component_counts[component_id] = len(component)
            road_payloads.extend(
                _road_payload(cell, road_class, component_id, len(component)) for cell in component
            )
    road_payloads.sort(key=lambda item: item["road_cell_id"])

    storage_nodes = [node for node in topology.get("nodes", []) if node.get("type") == "storage"]
    ctu_storage_nodes = [
        node for node in storage_nodes if zones.get(str(node.get("zoneId") or ""), {}).get("type") == "ctu"
    ]
    stable_storage_nodes = [
        node for node in ctu_storage_nodes if _storage_inside_zone(node, zones.get(str(node.get("zoneId") or "")))
    ]
    overlapping_ctu_roads = {
        _position(node) for node in storage_nodes
    } & {
        _position(cell) for cell in grouped["ctu"]
    }
    road_counts = Counter(item["road_class"] for item in road_payloads)

    return {
        "topology_id": str(topology.get("id", "")),
        "topology_revision": str(topology.get("meta", {}).get("version", "unknown")),
        "vehicle_type": "ctu",
        "render_contract": {
            "road_cell_color": "#2e3a52",
            "ctu_zone_geometry_color": "#0e7490",
            "shuttle_aisle_color": "#3a4f6e",
            "color_grants_traversability": False,
        },
        "road_counts": {
            "total": len(road_payloads),
            "ctu": road_counts["ctu"],
            "shuttle": road_counts["shuttle"],
            "unassigned": road_counts["unassigned"],
            "other": road_counts["other"],
            "invalid": road_counts["invalid"],
        },
        "road_components": component_counts,
        "road_cells": road_payloads,
        "ctu_road_overlap_storage_count": len(overlapping_ctu_roads),
        "ctu_road_eligible_count": sum(1 for item in road_payloads if item["eligible_for_ctu"]),
        "storage_node_count": len(storage_nodes),
        "ctu_storage_node_count": len(ctu_storage_nodes),
        "stable_ctu_storage_geometry_count": len(stable_storage_nodes),
        "pick_face_declared_count": 0,
        "ctu_vehicle_footprint_declared": False,
        "formal_access_cell_count": 0,
        "trial_area": _trial_area(topology, road_payloads, ctu_storage_nodes),
        "warnings": [
            "当前 Schema 没有 CTU road vehicle_type、Pick Face、车体占用或取货距离字段。",
            "path-demo 使用所有连通 road，不代表这些道路具备 CTU 正式取货权限。",
            "颜色只负责渲染，不能授予道路业务语义。",
        ],
    }
