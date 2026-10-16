from __future__ import annotations

from typing import Any

from ..ctu_routing_schemas import (
    CtuPathSegment,
    CtuRoutingCell,
    CtuRoutingPlan,
    CtuRoutingTimeModel,
)
from .pathfinding import (
    build_cell_lookup,
    build_node_lookup,
    build_occupied_lookup,
    cell_position,
    node_position,
    normalize_floor,
    shortest_path_result,
)


def _runtime_contract() -> tuple[int, float]:
    # Lazy import keeps this adapter independent from runtime construction while
    # retaining simulation.py as the source of truth for capacity and tick time.
    from .simulation import ROBOT_CARRY_CAPACITY, TICK_INTERVAL_SECONDS

    return int(ROBOT_CARRY_CAPACITY["ctu"]), float(TICK_INTERVAL_SECONDS)


def _cell_payload(cell: dict[str, Any]) -> CtuRoutingCell:
    return CtuRoutingCell(
        id=str(cell["id"]),
        floor=normalize_floor(cell.get("floor")),
        row=int(cell["row"]),
        col=int(cell["col"]),
        type=str(cell.get("type") or "road"),
    )


def _line_identity(task_id: str, line: Any, line_index: int) -> tuple[str, str]:
    if isinstance(line, dict) and line.get("lineId"):
        return str(line["lineId"]), "task_line"
    return f"{task_id}:line:{line_index}", "derived_from_task_order"


def _line_quantity(line: dict[str, Any]) -> tuple[int | None, str, str | None]:
    raw_quantity = line.get("quantity", line.get("qty"))
    if raw_quantity is None:
        return 1, "one_box_per_line_contract", None
    try:
        quantity = int(raw_quantity)
    except (TypeError, ValueError):
        return None, "task_line", "商品行数量必须是整数"
    if quantity != 1:
        return quantity, "task_line", "当前 B 运行时每个商品行只代表一箱，数量必须为 1"
    return quantity, "task_line", None


def _is_traversable(cell: dict[str, Any]) -> bool:
    return bool(cell.get("walkable", True)) and cell.get("type") != "blocked"


def _resolve_docking_cell(
    topology: dict[str, Any],
    node: dict[str, Any],
    task_cell_id: str | None,
) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
    """Resolve only an explicit or co-located docking cell; never infer nearest road."""

    cells = build_cell_lookup(topology)
    node_pos = node_position(node)
    colocated = next(
        (cell for cell in cells.values() if cell_position(cell) == node_pos),
        None,
    )

    declared_resource_ids: list[str] = []
    node_id = str(node["id"])
    for resource in topology.get("resources", []):
        if node_id not in {str(value) for value in resource.get("nodeIds", [])}:
            continue
        declared_resource_ids.extend(str(value) for value in resource.get("cellIds", []))
    declared_resource_ids = list(dict.fromkeys(declared_resource_ids))
    resource_cells = [cells[cell_id] for cell_id in declared_resource_ids if cell_id in cells]

    if task_cell_id and task_cell_id in cells:
        requested = cells[task_cell_id]
        if cell_position(requested) == node_pos:
            if not _is_traversable(requested):
                return None, None, "invalid_docking_cell", "同位置 Cell 不可通行或已 blocked"
            return requested, "co_located_traversable_cell", None, None
        if task_cell_id in declared_resource_ids:
            if not _is_traversable(requested):
                return None, None, "invalid_docking_cell", "资源声明的停靠 Cell 不可通行或已 blocked"
            return requested, "explicit_resource_mapping", None, None
        return (
            None,
            None,
            "invalid_docking_mapping",
            "任务 Cell 既不与库位同位置，也不在资源的显式 cellIds 映射中",
        )

    valid_resource_cells = [cell for cell in resource_cells if _is_traversable(cell)]
    if len(valid_resource_cells) == 1:
        return valid_resource_cells[0], "explicit_resource_mapping", None, None
    if len(valid_resource_cells) > 1:
        return None, None, "ambiguous_docking_mapping", "库位关联了多个可通行停靠 Cell，无法唯一确定"
    if declared_resource_ids and not valid_resource_cells:
        return None, None, "invalid_docking_cell", "资源声明的停靠 Cell 不存在或不可通行"
    if colocated is not None:
        if not _is_traversable(colocated):
            return None, None, "invalid_docking_cell", "同位置 Cell 不可通行或已 blocked"
        return colocated, "co_located_traversable_cell", None, None
    return (
        None,
        None,
        "missing_docking_mapping",
        "库位没有同位置真实可通行 Cell，也没有唯一的资源停靠映射",
    )


def _vertical_pairs(topology: dict[str, Any]) -> set[tuple[tuple[str, int, int], tuple[str, int, int]]]:
    nodes = build_node_lookup(topology)
    pairs: set[tuple[tuple[str, int, int], tuple[str, int, int]]] = set()
    for resource in topology.get("resources", []):
        if resource.get("type") != "lift":
            continue
        positions = [
            node_position(nodes[node_id])
            for node_id in resource.get("nodeIds", [])
            if node_id in nodes and nodes[node_id].get("type") == "lift"
        ]
        for source in positions:
            for target in positions:
                if source != target and source[1:] == target[1:]:
                    pairs.add((source, target))
    return pairs


def _validate_path(
    topology: dict[str, Any],
    path: list[dict[str, Any]],
    start_cell_id: str,
    goal_cell_id: str,
) -> tuple[str | None, str | None]:
    if not path or path[0].get("id") != start_cell_id or path[-1].get("id") != goal_cell_id:
        return "invalid_path_endpoints", "路径首尾与航段起终点不一致"

    vertical_pairs = _vertical_pairs(topology)
    for previous, current in zip(path, path[1:]):
        source = cell_position(previous)
        target = cell_position(current)
        horizontal_neighbor = source[0] == target[0] and (
            abs(source[1] - target[1]) + abs(source[2] - target[2]) == 1
        )
        if not horizontal_neighbor and (source, target) not in vertical_pairs:
            return "non_contiguous_path", "路径包含非相邻 Cell 跳跃"

    occupied = build_occupied_lookup(topology)
    for cell in path[1:-1]:
        if cell_position(cell) in occupied:
            return "illegal_storage_transit", "路径中间穿越了 storage/buffer 占用位置"
    return None, None


def _time_model() -> CtuRoutingTimeModel:
    _, tick_seconds = _runtime_contract()
    return CtuRoutingTimeModel(live_simulation_tick_seconds=tick_seconds)


def _failed_plan(
    *,
    task_id: str | None,
    vehicle_id: str | None,
    algorithm: str,
    failure_code: str,
    failure_reason: str,
    capacity: int | None = None,
    load: int | None = None,
    current_line_index: int | None = None,
    picked_count: int | None = None,
    unplanned_line_ids: list[str] | None = None,
) -> CtuRoutingPlan:
    remaining_capacity = capacity - load if capacity is not None and load is not None else None
    return CtuRoutingPlan(
        task_id=task_id,
        vehicle_id=vehicle_id,
        algorithm=algorithm,
        task_state="invalid",
        planning_status="failed",
        failure_code=failure_code,
        failure_reason=failure_reason,
        vehicle_capacity=capacity,
        vehicle_load=load,
        remaining_capacity=remaining_capacity,
        current_line_index=current_line_index,
        picked_count=picked_count,
        segments=[],
        combined_path_cell_ids=[],
        unplanned_line_ids=unplanned_line_ids or [],
        total_cost=0.0,
        total_visited_nodes=0,
        time_model=_time_model(),
    )


def plan_ctu_task_route(
    topology: dict[str, Any],
    robot: dict[str, Any],
    task: dict[str, Any],
    algorithm: str = "astar",
) -> CtuRoutingPlan:
    """Plan ordered CTU picking legs without mutating topology, task, robot, or runtime."""

    task_id = str(task.get("id")) if isinstance(task, dict) and task.get("id") else None
    vehicle_id = str(robot.get("id")) if isinstance(robot, dict) and robot.get("id") else None
    if algorithm not in {"astar", "dijkstra"}:
        return _failed_plan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            failure_code="unsupported_algorithm",
            failure_reason=f"不支持的路径算法: {algorithm}",
        )
    if not isinstance(topology, dict) or not isinstance(robot, dict) or not isinstance(task, dict):
        return _failed_plan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            failure_code="invalid_input",
            failure_reason="topology、robot 和 task 必须为对象",
        )
    if not task_id or not vehicle_id:
        return _failed_plan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            failure_code="missing_identity",
            failure_reason="任务 ID 和车辆 ID 均不能为空",
        )
    if robot.get("type") != "ctu" or task.get("robotType") != "ctu":
        return _failed_plan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            failure_code="invalid_vehicle_type",
            failure_reason="本适配器只支持 CTU 车辆与 CTU 任务",
        )
    assigned_vehicle = task.get("assignedRobotId")
    if assigned_vehicle is not None and str(assigned_vehicle) != vehicle_id:
        return _failed_plan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            failure_code="vehicle_assignment_mismatch",
            failure_reason="任务已分配给其他车辆",
        )

    try:
        capacity = int(robot["capacity"])
        load = int(robot.get("load", 0))
    except (KeyError, TypeError, ValueError):
        return _failed_plan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            failure_code="invalid_capacity_state",
            failure_reason="车辆 capacity/load 缺失或不是整数",
        )
    maximum_capacity, _ = _runtime_contract()
    if capacity < 1 or capacity > maximum_capacity or load < 0 or load > capacity:
        return _failed_plan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            failure_code="invalid_capacity_state",
            failure_reason=f"CTU 容量必须在 1～{maximum_capacity} 箱且载荷不能超过容量",
            capacity=capacity,
            load=load,
        )

    lines = task.get("lines")
    if not isinstance(lines, list) or not lines:
        return _failed_plan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            failure_code="invalid_task_lines",
            failure_reason="任务 lines 必须是非空列表",
            capacity=capacity,
            load=load,
        )
    try:
        current_line_index = int(task.get("currentLineIndex", 0))
        picked_count = int(task.get("picked", 0))
    except (TypeError, ValueError):
        current_line_index = picked_count = -1

    all_line_ids = [_line_identity(task_id, line, index)[0] for index, line in enumerate(lines)]
    if (
        current_line_index < 0
        or current_line_index > len(lines)
        or picked_count < 0
        or picked_count > len(lines)
        or picked_count != current_line_index
    ):
        return _failed_plan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            failure_code="invalid_task_progress",
            failure_reason="currentLineIndex 与 picked 必须一致且位于商品行范围内",
            capacity=capacity,
            load=load,
            current_line_index=current_line_index,
            picked_count=picked_count,
            unplanned_line_ids=all_line_ids,
        )
    if len(lines) > capacity or len(lines) - current_line_index > capacity - load:
        return _failed_plan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            failure_code="capacity_exceeded",
            failure_reason="未完成商品行数量超过 CTU 当前剩余容量",
            capacity=capacity,
            load=load,
            current_line_index=current_line_index,
            picked_count=picked_count,
            unplanned_line_ids=all_line_ids[current_line_index:],
        )

    start_cell_id = str(robot.get("currentCellId") or robot.get("currentNode") or "")
    cells = build_cell_lookup(topology)
    start_cell = cells.get(start_cell_id)
    if start_cell is None or not _is_traversable(start_cell):
        return _failed_plan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            failure_code="invalid_vehicle_start",
            failure_reason="车辆当前位置不是拓扑中的真实可通行 Cell",
            capacity=capacity,
            load=load,
            current_line_index=current_line_index,
            picked_count=picked_count,
            unplanned_line_ids=all_line_ids[current_line_index:],
        )

    task_state = "picking_complete" if current_line_index == len(lines) else (
        "picking_in_progress" if current_line_index > 0 else "pending_pick"
    )
    if task_state == "picking_complete":
        return CtuRoutingPlan(
            task_id=task_id,
            vehicle_id=vehicle_id,
            algorithm=algorithm,
            task_state=task_state,
            planning_status="success",
            vehicle_capacity=capacity,
            vehicle_load=load,
            remaining_capacity=capacity - load,
            current_line_index=current_line_index,
            picked_count=picked_count,
            segments=[],
            combined_path_cell_ids=[],
            unplanned_line_ids=[],
            total_cost=0.0,
            total_visited_nodes=0,
            time_model=_time_model(),
        )

    nodes = build_node_lookup(topology)
    segments: list[CtuPathSegment] = []
    combined_path: list[str] = []
    total_cost = 0.0
    total_visited_nodes = 0
    current_start = start_cell_id

    for line_index in range(current_line_index, len(lines)):
        line = lines[line_index]
        line_id, line_id_source = _line_identity(task_id, line, line_index)
        remaining_ids = all_line_ids[line_index:]
        if not isinstance(line, dict):
            failure_code, failure_reason = "invalid_task_line", "商品行必须是对象"
            quantity, quantity_source = 1, "one_box_per_line_contract"
            node_id = None
            sku_id = None
            task_cell_id = None
        else:
            quantity, quantity_source, quantity_error = _line_quantity(line)
            node_id = str(line.get("nodeId")) if line.get("nodeId") else None
            sku_id = str(line.get("skuId")) if line.get("skuId") else None
            task_cell_id = str(line.get("cellId")) if line.get("cellId") else None
            failure_code = "unsupported_line_quantity" if quantity_error else None
            failure_reason = quantity_error

        node = nodes.get(node_id) if node_id else None
        docking_cell = None
        docking_source = None
        if failure_code is None and node is None:
            failure_code, failure_reason = "missing_storage_node", "商品行引用的库位节点不存在"
        if failure_code is None and node.get("type") not in {"storage", "buffer"}:
            failure_code, failure_reason = "invalid_pick_node_type", "取货节点必须是 storage 或 buffer"
        if failure_code is None:
            docking_cell, docking_source, failure_code, failure_reason = _resolve_docking_cell(
                topology, node, task_cell_id
            )

        if failure_code is not None:
            segment = CtuPathSegment(
                segment_index=len(segments),
                task_id=task_id,
                vehicle_id=vehicle_id,
                line_index=line_index,
                line_id=line_id,
                line_id_source=line_id_source,
                sku_id=sku_id,
                quantity=quantity or 0,
                quantity_source=quantity_source,
                storage_node_id=node_id,
                task_cell_id=task_cell_id,
                docking_cell_id=None,
                docking_source=None,
                start_cell_id=current_start,
                goal_cell_id=None,
                algorithm=algorithm,
                planning_status="failed",
                failure_code=failure_code,
                failure_reason=failure_reason,
                path_cells=[],
            )
            segments.append(segment)
            return CtuRoutingPlan(
                task_id=task_id,
                vehicle_id=vehicle_id,
                algorithm=algorithm,
                task_state=task_state,
                planning_status="partial_failure" if len(segments) > 1 else "failed",
                failure_code=failure_code,
                failure_reason=failure_reason,
                vehicle_capacity=capacity,
                vehicle_load=load,
                remaining_capacity=capacity - load,
                current_line_index=current_line_index,
                picked_count=picked_count,
                segments=segments,
                combined_path_cell_ids=combined_path,
                unplanned_line_ids=remaining_ids,
                total_cost=total_cost,
                total_visited_nodes=total_visited_nodes,
                time_model=_time_model(),
            )

        goal_cell_id = str(docking_cell["id"])
        search = shortest_path_result(
            topology,
            current_start,
            goal_cell_id,
            policy=algorithm,
            avoid_storage=True,
        )
        failure_code = failure_reason = None
        if not search.path:
            failure_code, failure_reason = "unreachable_segment", "起点与合法停靠点之间不存在可行路径"
        else:
            failure_code, failure_reason = _validate_path(
                topology, search.path, current_start, goal_cell_id
            )

        segment = CtuPathSegment(
            segment_index=len(segments),
            task_id=task_id,
            vehicle_id=vehicle_id,
            line_index=line_index,
            line_id=line_id,
            line_id_source=line_id_source,
            sku_id=sku_id,
            quantity=quantity or 0,
            quantity_source=quantity_source,
            storage_node_id=node_id,
            task_cell_id=task_cell_id,
            docking_cell_id=goal_cell_id,
            docking_source=docking_source,
            start_cell_id=current_start,
            goal_cell_id=goal_cell_id,
            algorithm=algorithm,
            planning_status="failed" if failure_code else "success",
            failure_code=failure_code,
            failure_reason=failure_reason,
            path_cells=[_cell_payload(cell) for cell in search.path] if not failure_code else [],
            total_cost=search.total_cost if not failure_code else None,
            visited_nodes=search.visited_nodes,
        )
        segments.append(segment)
        if failure_code:
            return CtuRoutingPlan(
                task_id=task_id,
                vehicle_id=vehicle_id,
                algorithm=algorithm,
                task_state=task_state,
                planning_status="partial_failure" if len(segments) > 1 else "failed",
                failure_code=failure_code,
                failure_reason=failure_reason,
                vehicle_capacity=capacity,
                vehicle_load=load,
                remaining_capacity=capacity - load,
                current_line_index=current_line_index,
                picked_count=picked_count,
                segments=segments,
                combined_path_cell_ids=combined_path,
                unplanned_line_ids=remaining_ids,
                total_cost=total_cost,
                total_visited_nodes=total_visited_nodes + search.visited_nodes,
                time_model=_time_model(),
            )

        segment_ids = [str(cell["id"]) for cell in search.path]
        combined_path.extend(segment_ids if not combined_path else segment_ids[1:])
        total_cost += search.total_cost
        total_visited_nodes += search.visited_nodes
        current_start = goal_cell_id

    return CtuRoutingPlan(
        task_id=task_id,
        vehicle_id=vehicle_id,
        algorithm=algorithm,
        task_state=task_state,
        planning_status="success",
        vehicle_capacity=capacity,
        vehicle_load=load,
        remaining_capacity=capacity - load,
        current_line_index=current_line_index,
        picked_count=picked_count,
        segments=segments,
        combined_path_cell_ids=combined_path,
        unplanned_line_ids=[],
        total_cost=total_cost,
        total_visited_nodes=total_visited_nodes,
        time_model=_time_model(),
    )
