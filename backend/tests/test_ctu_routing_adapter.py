from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.ctu_routing_adapter import plan_ctu_task_route
from app.services.simulation import SimulationService


def _cell(row: int, col: int, *, cell_type: str = "road", walkable: bool = True) -> dict:
    return {
        "id": f"cell-r{row}-c{col}",
        "floor": "1F",
        "row": row,
        "col": col,
        "type": cell_type,
        "walkable": walkable,
        "cost": 1.0,
    }


@pytest.fixture
def topology() -> dict:
    return {
        "id": "ctu-routing-test",
        "cells": [
            *[_cell(0, col) for col in range(7)],
            *[_cell(1, col) for col in range(7)],
            _cell(3, 0),
            _cell(2, 6, cell_type="blocked", walkable=False),
        ],
        "nodes": [
            {"id": "pick-a", "type": "storage", "floor": "1F", "row": 0, "col": 2},
            {"id": "storage-blocker", "type": "storage", "floor": "1F", "row": 0, "col": 3},
            {"id": "pick-b", "type": "buffer", "floor": "1F", "row": 0, "col": 4},
            {"id": "mapped-pick", "type": "storage", "floor": "1F", "row": 2, "col": 2},
            {"id": "missing-dock", "type": "storage", "floor": "1F", "row": 2, "col": 5},
            {"id": "disconnected", "type": "storage", "floor": "1F", "row": 3, "col": 0},
            {"id": "blocked-pick", "type": "storage", "floor": "1F", "row": 2, "col": 6},
            {"id": "station", "type": "station", "floor": "1F", "row": 0, "col": 6},
            {"id": "entry", "type": "entry", "floor": "1F", "row": 0, "col": 0},
        ],
        "resources": [
            {
                "id": "mapped-pick-dock",
                "type": "buffer_slot",
                "nodeIds": ["mapped-pick"],
                "cellIds": ["cell-r1-c2"],
            }
        ],
    }


def _robot(**overrides) -> dict:
    result = {
        "id": "CTU-01",
        "type": "ctu",
        "currentCellId": "cell-r0-c0",
        "currentNode": "cell-r0-c0",
        "capacity": 5,
        "load": 0,
    }
    result.update(overrides)
    return result


def _line(node_id: str, index: int, *, cell_id: str | None = None, **overrides) -> dict:
    result = {
        "lineId": f"line-{index}",
        "nodeId": node_id,
        "cellId": cell_id or {
            "pick-a": "cell-r0-c2",
            "pick-b": "cell-r0-c4",
            "mapped-pick": "cell-r2-c2",
            "missing-dock": "cell-r2-c5",
            "disconnected": "cell-r3-c0",
            "blocked-pick": "cell-r2-c6",
        }.get(node_id),
        "layer": 1,
        "skuId": f"SKU-{index:03d}",
    }
    result.update(overrides)
    return result


def _task(lines: list[dict], **overrides) -> dict:
    result = {
        "id": "task-1",
        "robotType": "ctu",
        "assignedRobotId": "CTU-01",
        "lines": lines,
        "currentLineIndex": 0,
        "picked": 0,
    }
    result.update(overrides)
    return result


def test_single_sku_uses_real_astar_and_does_not_execute_pick(topology):
    plan = plan_ctu_task_route(topology, _robot(), _task([_line("pick-a", 1)]))

    assert plan.planning_status == "success"
    assert plan.execution_status == "not_executed"
    assert plan.segments[0].pick_confirmation_status == "not_executed"
    assert plan.segments[0].algorithm == "astar"
    assert [cell.id for cell in plan.segments[0].path_cells] == [
        "cell-r0-c0",
        "cell-r0-c1",
        "cell-r0-c2",
    ]
    assert plan.segments[0].total_cost == 2.0
    assert plan.segments[0].visited_nodes > 0


def test_consumes_task_built_by_b_simulation_service(topology):
    service = SimulationService(None)  # _build_task is pure and does not access the repository.
    task = service._build_task(
        topology,
        "task-from-b",
        "ctu",
        "direct",
        [
            {"nodeId": "pick-a", "layer": 2, "skuId": "SKU-A"},
            {"nodeId": "pick-b", "layer": 3, "skuId": "SKU-B"},
        ],
        "station",
        "entry",
        None,
        None,
        False,
        2,
    )

    plan = plan_ctu_task_route(topology, _robot(), task)

    assert plan.planning_status == "success"
    assert [segment.sku_id for segment in plan.segments] == ["SKU-A", "SKU-B"]
    assert [segment.line_id_source for segment in plan.segments] == [
        "derived_from_task_order",
        "derived_from_task_order",
    ]
    assert all(segment.quantity == 1 for segment in plan.segments)


def test_multi_sku_preserves_order_and_segment_continuity(topology):
    plan = plan_ctu_task_route(
        topology,
        _robot(),
        _task([_line("pick-a", 1), _line("pick-b", 2)]),
    )

    assert plan.planning_status == "success"
    assert [segment.line_id for segment in plan.segments] == ["line-1", "line-2"]
    assert [segment.sku_id for segment in plan.segments] == ["SKU-001", "SKU-002"]
    assert plan.segments[0].goal_cell_id == plan.segments[1].start_cell_id
    assert plan.combined_path_cell_ids[0] == "cell-r0-c0"
    assert plan.combined_path_cell_ids[-1] == "cell-r0-c4"
    assert all(
        left.goal_cell_id == right.start_cell_id
        for left, right in zip(plan.segments, plan.segments[1:])
    )


def test_capacity_accepts_five_boxes_and_rejects_six(topology):
    five = [_line("pick-a" if index % 2 else "pick-b", index) for index in range(1, 6)]
    six = five + [_line("pick-a", 6)]

    assert plan_ctu_task_route(topology, _robot(), _task(five)).planning_status == "success"
    rejected = plan_ctu_task_route(topology, _robot(), _task(six))
    assert rejected.planning_status == "failed"
    assert rejected.failure_code == "capacity_exceeded"


def test_dijkstra_uses_same_legal_endpoints(topology):
    task = _task([_line("pick-a", 1), _line("pick-b", 2)])
    astar = plan_ctu_task_route(topology, _robot(), task, "astar")
    dijkstra = plan_ctu_task_route(topology, _robot(), task, "dijkstra")

    assert dijkstra.planning_status == "success"
    assert all(segment.algorithm == "dijkstra" for segment in dijkstra.segments)
    assert dijkstra.total_cost == astar.total_cost
    assert dijkstra.combined_path_cell_ids[0] == astar.combined_path_cell_ids[0]
    assert dijkstra.combined_path_cell_ids[-1] == astar.combined_path_cell_ids[-1]


def test_avoid_storage_forces_detour_around_intermediate_storage(topology):
    plan = plan_ctu_task_route(
        topology,
        _robot(currentCellId="cell-r0-c2"),
        _task([_line("pick-b", 2)]),
    )

    path_ids = [cell.id for cell in plan.segments[0].path_cells]
    assert plan.planning_status == "success"
    assert plan.segments[0].avoid_storage is True
    assert "cell-r0-c3" not in path_ids
    assert any(cell_id.startswith("cell-r1-") for cell_id in path_ids)


def test_explicit_resource_mapping_is_accepted_without_nearest_cell_inference(topology):
    plan = plan_ctu_task_route(topology, _robot(), _task([_line("mapped-pick", 1)]))

    assert plan.planning_status == "success"
    assert plan.segments[0].task_cell_id == "cell-r2-c2"
    assert plan.segments[0].docking_cell_id == "cell-r1-c2"
    assert plan.segments[0].docking_source == "explicit_resource_mapping"


def test_missing_legal_docking_mapping_is_explicit_failure(topology):
    plan = plan_ctu_task_route(topology, _robot(), _task([_line("missing-dock", 1)]))

    assert plan.planning_status == "failed"
    assert plan.failure_code == "missing_docking_mapping"
    assert plan.segments[0].path_cells == []
    assert plan.segments[0].goal_cell_id is None


def test_disconnected_docking_cell_is_unreachable(topology):
    plan = plan_ctu_task_route(topology, _robot(), _task([_line("disconnected", 1)]))

    assert plan.planning_status == "failed"
    assert plan.failure_code == "unreachable_segment"
    assert plan.segments[0].docking_cell_id == "cell-r3-c0"


@pytest.mark.parametrize(
    ("robot", "task", "failure_code"),
    [
        (_robot(currentCellId="cell-r99-c99"), _task([_line("pick-a", 1)]), "invalid_vehicle_start"),
        (_robot(), _task([_line("not-found", 1, cell_id="cell-r0-c1")]), "missing_storage_node"),
        (_robot(), _task([_line("pick-a", 1)], currentLineIndex=1, picked=0), "invalid_task_progress"),
    ],
)
def test_invalid_vehicle_task_or_storage_is_rejected(topology, robot, task, failure_code):
    plan = plan_ctu_task_route(topology, robot, task)
    assert plan.planning_status == "failed"
    assert plan.failure_code == failure_code


def test_partial_failure_keeps_only_real_completed_prefix(topology):
    plan = plan_ctu_task_route(
        topology,
        _robot(),
        _task([_line("pick-a", 1), _line("disconnected", 2)]),
    )

    assert plan.planning_status == "partial_failure"
    assert [segment.planning_status for segment in plan.segments] == ["success", "failed"]
    assert plan.failure_code == "unreachable_segment"
    assert plan.combined_path_cell_ids[-1] == "cell-r0-c2"
    assert plan.unplanned_line_ids == ["line-2"]


def test_in_progress_task_plans_only_unfinished_lines(topology):
    task = _task(
        [_line("pick-a", 1), _line("pick-b", 2)],
        currentLineIndex=1,
        picked=1,
    )
    plan = plan_ctu_task_route(
        topology,
        _robot(currentCellId="cell-r0-c2", load=1),
        task,
    )

    assert plan.task_state == "picking_in_progress"
    assert [segment.line_id for segment in plan.segments] == ["line-2"]
    assert plan.segments[0].start_cell_id == "cell-r0-c2"


def test_completed_pick_task_has_no_route_and_does_not_claim_delivery(topology):
    task = _task(
        [_line("pick-a", 1), _line("pick-b", 2)],
        currentLineIndex=2,
        picked=2,
    )
    plan = plan_ctu_task_route(
        topology,
        _robot(currentCellId="cell-r0-c4", load=2),
        task,
    )

    assert plan.task_state == "picking_complete"
    assert plan.planning_status == "success"
    assert plan.segments == []
    assert plan.execution_status == "not_executed"


def test_line_without_source_id_gets_deterministic_derived_reference(topology):
    line = _line("pick-a", 1)
    line.pop("lineId")
    plan = plan_ctu_task_route(topology, _robot(), _task([line]))

    assert plan.segments[0].line_id == "task-1:line:0"
    assert plan.segments[0].line_id_source == "derived_from_task_order"


def test_non_unit_quantity_is_rejected_instead_of_changing_runtime_semantics(topology):
    plan = plan_ctu_task_route(
        topology,
        _robot(),
        _task([_line("pick-a", 1, quantity=2)]),
    )
    assert plan.planning_status == "failed"
    assert plan.failure_code == "unsupported_line_quantity"


def test_result_is_deterministic_and_inputs_are_not_mutated(topology):
    robot = _robot()
    task = _task([_line("pick-a", 1), _line("pick-b", 2)])
    originals = deepcopy((topology, robot, task))

    first = plan_ctu_task_route(topology, robot, task)
    second = plan_ctu_task_route(topology, robot, task)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert (topology, robot, task) == originals


def test_time_contract_is_geometry_only_and_does_not_mix_reservation_slots(topology):
    plan = plan_ctu_task_route(topology, _robot(), _task([_line("pick-a", 1)]))

    assert plan.time_model.planning_basis == "geometric_path_only"
    assert plan.time_model.live_simulation_tick_seconds == 1.5
    assert plan.time_model.reservation_time_step_seconds is None
    assert plan.time_model.execution_eta_seconds is None
