from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import path_demo as path_demo_api
from app.services import multi_path_evaluation as evaluation_service
from app.services.multi_path_demo import build_multi_demo_scenarios
from app.services.path_demo import PathDemoError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def open_grid_topology(size: int = 14) -> dict:
    return {
        "id": "evaluation-grid",
        "name": "Evaluation Grid",
        "meta": {"version": "test-v1"},
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


def config() -> dict:
    return {
        "time_step_seconds": 0.25,
        "speed_cells_per_second": 4.0,
        "max_time_steps": 512,
        "max_expanded_states": 200_000,
        "vehicle_width_cells": 0.58,
        "vehicle_length_cells": 0.72,
        "safety_margin_cells": 0.05,
    }


def evaluate(count: int = 3) -> dict:
    topology = open_grid_topology()
    return evaluation_service.evaluate_multi_path_algorithms(
        topology=topology,
        topology_id=topology["id"],
        topology_name=topology["name"],
        topology_version=topology["meta"]["version"],
        mode="custom",
        scenario_id=None,
        vehicles=separated_vehicles(count),
        planning_config=config(),
    )


@pytest.fixture(scope="module")
def topology_666() -> dict:
    with (PROJECT_ROOT / "docs" / "topologies" / "666.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def test_both_algorithms_receive_the_same_frozen_input(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[dict, list[dict], str, dict]] = []
    real_planner = evaluation_service.plan_multi_paths

    def recording_planner(topology, vehicles, algorithm, planning_config):
        calls.append((deepcopy(topology), deepcopy(vehicles), algorithm, deepcopy(planning_config)))
        return real_planner(topology, vehicles, algorithm, planning_config)

    monkeypatch.setattr(evaluation_service, "plan_multi_paths", recording_planner)
    result = evaluate(3)

    assert [call[2] for call in calls] == ["astar", "dijkstra"]
    assert calls[0][0] == calls[1][0]
    assert calls[0][1] == calls[1][1] == result["input_snapshot"]["vehicles"]
    assert calls[0][3] == calls[1][3] == result["input_snapshot"]["planning_config"]


@pytest.mark.parametrize("scenario_id", ["parallel", "node-conflict", "edge-swap"])
def test_real_666_presets_are_evaluated_by_both_algorithms(topology_666: dict, scenario_id: str):
    scenario = next(item for item in build_multi_demo_scenarios(topology_666) if item["id"] == scenario_id)
    result = evaluation_service.evaluate_multi_path_algorithms(
        topology=topology_666,
        topology_id=topology_666["id"],
        topology_name=topology_666["name"],
        topology_version=topology_666["meta"]["version"],
        mode="preset",
        scenario_id=scenario_id,
        vehicles=scenario["vehicles"],
        planning_config=config(),
    )
    assert [item["algorithm"] for item in result["results"]] == ["astar", "dijkstra"]
    assert all(item["planning_status"] == "success" for item in result["results"])
    assert all(item["metrics"]["remaining_conflicts"] == 0 for item in result["results"])
    assert all(item["plan"]["topology_id"] == topology_666["id"] for item in result["results"])


@pytest.mark.parametrize("vehicle_count", [1, 6])
def test_evaluation_supports_legal_vehicle_limits(vehicle_count: int):
    result = evaluate(vehicle_count)
    assert [item["planning_status"] for item in result["results"]] == ["success", "success"]
    assert all(len(item["plan"]["vehicles"]) == vehicle_count for item in result["results"])


def test_metrics_are_derived_from_real_plans_and_keep_full_trajectories():
    result = evaluate(3)
    assert result["input_snapshot"]["algorithms"] == ["astar", "dijkstra"]
    for algorithm_result in result["results"]:
        plan = algorithm_result["plan"]
        metrics = algorithm_result["metrics"]
        assert metrics["total_path_distance"] == sum(vehicle["total_distance"] for vehicle in plan["vehicles"])
        assert metrics["total_wait_time_seconds"] == sum(vehicle["wait_time_seconds"] for vehicle in plan["vehicles"])
        assert metrics["average_wait_time_seconds"] == pytest.approx(metrics["total_wait_time_seconds"] / 3)
        assert metrics["makespan_seconds"] == plan["makespan_seconds"]
        assert metrics["remaining_conflicts"] == 0
        assert all(vehicle["steps"] for vehicle in plan["vehicles"])


def test_failed_algorithm_does_not_return_fake_zero_metrics(monkeypatch: pytest.MonkeyPatch):
    real_planner = evaluation_service.plan_multi_paths

    def selectively_failing_planner(topology, vehicles, algorithm, planning_config):
        if algorithm == "dijkstra":
            raise PathDemoError("Dijkstra search boundary reached", status_code=409)
        return real_planner(topology, vehicles, algorithm, planning_config)

    monkeypatch.setattr(evaluation_service, "plan_multi_paths", selectively_failing_planner)
    result = evaluate(2)
    failed = result["results"][1]
    assert failed["planning_status"] == "failed"
    assert failed["error_reason"] == "Dijkstra search boundary reached"
    assert failed["metrics"] is None
    assert failed["plan"] is None
    assert failed["planning_time_ms"] >= 0


def test_evaluation_api_uses_isolated_repository_and_is_deterministic(monkeypatch: pytest.MonkeyPatch):
    topology = open_grid_topology()

    class FakeRepository:
        def get_topology(self, topology_id: str):
            if topology_id != topology["id"]:
                return None
            return {"id": topology["id"], "name": topology["name"], "payload": topology}

    monkeypatch.setattr(path_demo_api, "repo", FakeRepository())
    app = FastAPI()
    app.include_router(path_demo_api.router, prefix="/api/v1")
    client = TestClient(app)
    payload = {
        "topology_id": topology["id"],
        "mode": "custom",
        "vehicles": separated_vehicles(3),
        "planning_config": config(),
    }

    first = client.post("/api/v1/path-demo/evaluate-multi", json=payload)
    second = client.post("/api/v1/path-demo/evaluate-multi", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    first_json = first.json()
    second_json = second.json()
    assert first_json["input_snapshot"] == second_json["input_snapshot"]
    assert [item["algorithm"] for item in first_json["results"]] == ["astar", "dijkstra"]
    assert [item["plan"]["vehicles"] for item in first_json["results"]] == [
        item["plan"]["vehicles"] for item in second_json["results"]
    ]
