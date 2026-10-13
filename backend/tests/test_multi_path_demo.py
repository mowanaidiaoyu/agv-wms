from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import path_demo as path_demo_api
from app.services.multi_path_demo import build_multi_demo_scenarios, plan_multi_paths
from app.services.path_demo import PathDemoError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def topology_666() -> dict:
    with (PROJECT_ROOT / "docs" / "topologies" / "666.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def scenarios_666(topology_666: dict) -> list[dict]:
    scenarios = build_multi_demo_scenarios(topology_666)
    assert [scenario["id"] for scenario in scenarios] == ["parallel", "node-conflict", "edge-swap"]
    return scenarios


def config(**overrides) -> dict:
    result = {
        "time_step_seconds": 0.25,
        "speed_cells_per_second": 4.0,
        "max_time_steps": 512,
        "max_expanded_states": 200_000,
        "vehicle_width_cells": 0.58,
        "vehicle_length_cells": 0.72,
        "safety_margin_cells": 0.05,
    }
    result.update(overrides)
    return result


def scenario(scenarios: list[dict], scenario_id: str) -> dict:
    return next(item for item in scenarios if item["id"] == scenario_id)


def open_grid_topology(size: int = 14) -> dict:
    return {
        "id": "open-grid",
        "name": "Open Grid",
        "cells": [
            {
                "id": f"cell-r{row}-c{col}",
                "floor": "1F",
                "row": row,
                "col": col,
                "type": "road",
                "walkable": True,
            }
            for row in range(size)
            for col in range(size)
        ],
    }


def separated_vehicles(count: int) -> list[dict]:
    return [
        {
            "vehicle_id": f"CTU-{index + 1:02d}",
            "vehicle_type": "ctu",
            "start_cell_id": f"cell-r{index * 2}-c0",
            "goal_cell_id": f"cell-r{index * 2}-c8",
            "priority": index + 1,
            "display_color": f"#{index + 1:06x}",
        }
        for index in range(count)
    ]


def positions_by_vehicle(result: dict) -> list[list[tuple[str, int, int]]]:
    return [
        [(step["cell"]["floor"], step["cell"]["row"], step["cell"]["col"]) for step in vehicle["steps"]]
        for vehicle in result["vehicles"]
    ]


def position_at(path: list[tuple[str, int, int]], time_step: int) -> tuple[str, int, int]:
    return path[min(time_step, len(path) - 1)]


def segment_distance(a0, a1, b0, b1) -> float:
    relative = (a0[1] - b0[1], a0[2] - b0[2])
    velocity = ((a1[1] - a0[1]) - (b1[1] - b0[1]), (a1[2] - a0[2]) - (b1[2] - b0[2]))
    squared = velocity[0] ** 2 + velocity[1] ** 2
    alpha = 0.0 if squared == 0 else max(0.0, min(1.0, -(relative[0] * velocity[0] + relative[1] * velocity[1]) / squared))
    return math.hypot(relative[0] + velocity[0] * alpha, relative[1] + velocity[1] * alpha)


def assert_execution_is_safe(result: dict) -> None:
    paths = positions_by_vehicle(result)
    for time_step in range(result["makespan_steps"] + 1):
        positions = [position_at(path, time_step) for path in paths]
        assert len(positions) == len(set(positions))
        if time_step == 0:
            for first in range(len(paths)):
                for second in range(first + 1, len(paths)):
                    assert (
                        segment_distance(positions[first], positions[first], positions[second], positions[second])
                        + 1e-9
                        >= result["safety_distance_cells"]
                    )
            continue
        previous = [position_at(path, time_step - 1) for path in paths]
        for first in range(len(paths)):
            for second in range(first + 1, len(paths)):
                assert not (previous[first] == positions[second] and previous[second] == positions[first])
                assert (
                    segment_distance(previous[first], positions[first], previous[second], positions[second])
                    + 1e-9
                    >= result["safety_distance_cells"]
                )


def test_real_666_parallel_scenario_has_no_conflicts(topology_666: dict, scenarios_666: list[dict]):
    result = plan_multi_paths(topology_666, scenario(scenarios_666, "parallel")["vehicles"], "astar", config())
    assert len(result["vehicles"]) == 3
    assert result["avoidance_count"] == 0
    assert sum(result["conflicts"].values()) == 0
    assert_execution_is_safe(result)


def test_real_666_node_competition_inserts_wait(topology_666: dict, scenarios_666: list[dict]):
    result = plan_multi_paths(topology_666, scenario(scenarios_666, "node-conflict")["vehicles"], "astar", config())
    assert result["conflicts"]["detected_node_conflicts"] > 0
    assert result["conflicts"]["remaining_node_conflicts"] == 0
    assert any(vehicle["wait_time_seconds"] > 0 for vehicle in result["vehicles"])
    assert_execution_is_safe(result)


def test_real_666_edge_swap_is_detected_and_resolved(topology_666: dict, scenarios_666: list[dict]):
    result = plan_multi_paths(topology_666, scenario(scenarios_666, "edge-swap")["vehicles"], "astar", config())
    assert result["conflicts"]["detected_edge_conflicts"] > 0
    assert result["conflicts"]["remaining_edge_conflicts"] == 0
    assert any(vehicle["wait_time_seconds"] > 0 for vehicle in result["vehicles"])
    assert_execution_is_safe(result)


def test_same_input_produces_the_same_space_time_plan(topology_666: dict, scenarios_666: list[dict]):
    vehicles = scenario(scenarios_666, "edge-swap")["vehicles"]
    first = plan_multi_paths(topology_666, vehicles, "dijkstra", config())
    second = plan_multi_paths(topology_666, vehicles, "dijkstra", config())
    assert [vehicle["steps"] for vehicle in first["vehicles"]] == [vehicle["steps"] for vehicle in second["vehicles"]]
    assert first["conflicts"] == second["conflicts"]


@pytest.mark.parametrize("vehicle_count", [1, 2, 3, 5, 6])
def test_custom_vehicle_counts_are_supported(vehicle_count: int):
    result = plan_multi_paths(open_grid_topology(), separated_vehicles(vehicle_count), "astar", config())
    assert len(result["vehicles"]) == vehicle_count
    assert_execution_is_safe(result)


def test_six_custom_vehicles_run_safely_on_real_666(topology_666: dict):
    road_lookup = {
        (str(cell.get("floor") or "1F"), int(cell["row"]), int(cell["col"])): cell
        for cell in topology_666["cells"]
        if cell.get("type") == "road" and cell.get("walkable", True)
    }
    excluded: set[tuple[str, int, int]] = set()
    paths: list[list[tuple[str, int, int]]] = []
    for start in sorted(road_lookup):
        for row_delta, col_delta in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            path = [
                (start[0], start[1] + row_delta * step, start[2] + col_delta * step)
                for step in range(7)
            ]
            if not all(position in road_lookup for position in path):
                continue
            if excluded and min(
                abs(position[1] - occupied[1]) + abs(position[2] - occupied[2])
                for position in path
                for occupied in excluded
            ) < 2:
                continue
            paths.append(path)
            excluded.update(path)
            break
        if len(paths) == 6:
            break
    assert len(paths) == 6
    vehicles = [
        {
            "vehicle_id": f"CTU-{index:02d}",
            "start_cell_id": road_lookup[path[0]]["id"],
            "goal_cell_id": road_lookup[path[-1]]["id"],
            "priority": index,
        }
        for index, path in enumerate(paths, start=1)
    ]
    result = plan_multi_paths(topology_666, vehicles, "astar", config())
    assert len(result["vehicles"]) == 6
    assert_execution_is_safe(result)


@pytest.mark.parametrize("vehicle_count", [0, 7])
def test_rejects_vehicle_counts_outside_custom_limit(vehicle_count: int):
    with pytest.raises(PathDemoError, match="between 1 and 6"):
        plan_multi_paths(open_grid_topology(), separated_vehicles(vehicle_count), "astar", config())


def test_priority_controls_deterministic_reservation_order():
    vehicles = separated_vehicles(2)
    vehicles[0]["priority"] = 20
    vehicles[1]["priority"] = 10
    result = plan_multi_paths(open_grid_topology(), vehicles, "astar", config())
    assert [vehicle["vehicle_id"] for vehicle in result["vehicles"]] == ["CTU-02", "CTU-01"]
    assert [vehicle["priority"] for vehicle in result["vehicles"]] == [10, 20]


def test_legacy_requests_without_priority_keep_input_order():
    vehicles = list(reversed([
        {key: value for key, value in vehicle.items() if key not in {"priority", "vehicle_type", "display_color"}}
        for vehicle in separated_vehicles(3)
    ]))
    result = plan_multi_paths(open_grid_topology(), vehicles, "astar", config())
    assert [vehicle["vehicle_id"] for vehicle in result["vehicles"]] == ["CTU-03", "CTU-02", "CTU-01"]


def test_rejects_duplicate_vehicle_ids_starts_and_persistent_goals():
    topology = open_grid_topology()
    vehicles = separated_vehicles(2)
    vehicles[1]["vehicle_id"] = vehicles[0]["vehicle_id"]
    with pytest.raises(PathDemoError, match="IDs must be unique"):
        plan_multi_paths(topology, vehicles, "astar", config())

    vehicles = separated_vehicles(2)
    vehicles[1]["start_cell_id"] = vehicles[0]["start_cell_id"]
    with pytest.raises(PathDemoError, match="initial road node"):
        plan_multi_paths(topology, vehicles, "astar", config())

    vehicles = separated_vehicles(2)
    vehicles[1]["goal_cell_id"] = vehicles[0]["goal_cell_id"]
    with pytest.raises(PathDemoError, match="persistent goal"):
        plan_multi_paths(topology, vehicles, "astar", config())


def test_no_solution_identifies_the_failed_vehicle():
    topology = {
        "cells": [
            {"id": f"cell-r0-c{col}", "floor": "1F", "row": 0, "col": col, "type": "road", "walkable": True}
            for col in range(3)
        ]
    }
    vehicles = [
        {"vehicle_id": "CTU-01", "start_cell_id": "cell-r0-c0", "goal_cell_id": "cell-r0-c1", "priority": 1},
        {"vehicle_id": "CTU-02", "start_cell_id": "cell-r0-c2", "goal_cell_id": "cell-r0-c0", "priority": 2},
    ]
    with pytest.raises(PathDemoError, match=r"Vehicle CTU-02: No conflict-free"):
        plan_multi_paths(topology, vehicles, "astar", config(max_time_steps=20))


def test_rejects_disconnected_road_components():
    topology = {
        "cells": [
            {"id": "cell-r0-c0", "floor": "1F", "row": 0, "col": 0, "type": "road", "walkable": True},
            {"id": "cell-r0-c2", "floor": "1F", "row": 0, "col": 2, "type": "road", "walkable": True},
        ]
    }
    with pytest.raises(PathDemoError, match="different road components"):
        plan_multi_paths(
            topology,
            [{"vehicle_id": "CTU-01", "start_cell_id": "cell-r0-c0", "goal_cell_id": "cell-r0-c2"}],
            "astar",
            config(),
        )


def test_reports_search_boundary_instead_of_returning_an_unsafe_path(topology_666: dict, scenarios_666: list[dict]):
    vehicles = scenario(scenarios_666, "parallel")["vehicles"][:1]
    with pytest.raises(PathDemoError, match="max_expanded_states"):
        plan_multi_paths(topology_666, vehicles, "astar", config(max_expanded_states=1))


def test_multi_path_api_uses_an_isolated_repository(monkeypatch: pytest.MonkeyPatch, topology_666: dict):
    class FakeRepository:
        def get_topology(self, topology_id: str):
            if topology_id != topology_666["id"]:
                return None
            return {"id": topology_666["id"], "name": topology_666["name"], "payload": topology_666}

    monkeypatch.setattr(path_demo_api, "repo", FakeRepository())
    app = FastAPI()
    app.include_router(path_demo_api.router, prefix="/api/v1")
    client = TestClient(app)

    scenarios_response = client.get(f"/api/v1/path-demo/topologies/{topology_666['id']}/multi-scenarios")
    assert scenarios_response.status_code == 200
    vehicles = scenarios_response.json()["scenarios"][0]["vehicles"]
    response = client.post(
        "/api/v1/path-demo/plan-multi",
        json={"topology_id": topology_666["id"], "vehicles": vehicles, "algorithm": "astar"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["planning_status"] == "success"
    assert len(payload["vehicles"]) == 3
    assert payload["conflicts"]["remaining_node_conflicts"] == 0

    preset_response = client.post(
        "/api/v1/path-demo/plan-multi",
        json={"topology_id": topology_666["id"], "mode": "preset", "scenario_id": "node-conflict"},
    )
    assert preset_response.status_code == 200
    assert len(preset_response.json()["vehicles"]) == 3

    empty_response = client.post(
        "/api/v1/path-demo/plan-multi",
        json={"topology_id": topology_666["id"], "mode": "custom", "vehicles": []},
    )
    assert empty_response.status_code == 422

    too_many_response = client.post(
        "/api/v1/path-demo/plan-multi",
        json={"topology_id": topology_666["id"], "vehicles": separated_vehicles(7)},
    )
    assert too_many_response.status_code == 422
