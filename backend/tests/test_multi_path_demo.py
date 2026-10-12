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
