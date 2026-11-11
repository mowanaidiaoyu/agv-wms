"""Gate M2-4 path metrics read persisted plans from an isolated database."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import json

import pytest
from fastapi import HTTPException

from app.core import config, db
from app.core.repository import Repository


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    database_path = tmp_path / "path-planning.db"
    monkeypatch.setattr(db, "DB_PATH", database_path)
    db.init_db()
    assert database_path.parent == tmp_path
    assert database_path != config.DB_PATH
    return Repository(), database_path


def _plan_task(
    task_id: str,
    *,
    algorithm: str = "astar",
    planning_time=1.0,
    distance=10.0,
    visited=20,
    result_status: str = "success",
    mode: str = "shuttle_transfer",
    parent_id=None,
    topology_revision: str | None = "topology-v1",
    config_revision: str | None = "config-v1",
    coordinate_revision: str | None = "coordinate-v1",
):
    task = {
        "id": task_id,
        "mode": mode,
        "robotType": "shuttle" if mode == "shuttle_transfer" else "ctu",
        "status": "completed",
        "plan": {
            "algorithm": algorithm,
            "result_status": result_status,
            "planning_time_ms": planning_time,
            "horizontal_distance_cells": distance,
            "visited_states": visited,
        },
    }
    if parent_id is not None:
        task["parentId"] = parent_id
    for key, value in (
        ("topologyRevision", topology_revision),
        ("configRevision", config_revision),
        ("coordinateContractRevision", coordinate_revision),
    ):
        if value is not None:
            task["plan"][key] = value
    return task


def test_path_planning_filters_scope_and_groups_algorithms(isolated_repo):
    repo, _ = isolated_repo
    tasks = [
        _plan_task("astar-ok", algorithm="astar", planning_time=2.0, distance=11, visited=21),
        _plan_task("dijkstra-ok", algorithm="dijkstra", planning_time=3.0, distance=12, visited=22),
        _plan_task("ctu", mode="direct"),
        _plan_task("child", parent_id="astar-ok"),
        _plan_task("failed", result_status="failed"),
        _plan_task("unknown", algorithm="greedy"),
    ]
    repo.create_run("scope", "completed", "topology", {"tasks": tasks}, {"completedTasks": 0})
    result = repo.get_path_planning_metrics("scope")
    metrics = {item["metricId"]: item for item in result["metrics"]}

    success = metrics["PATH_SUCCESS_SAMPLE_COUNT"]
    assert success["value"] == 2
    assert success["failedPlanCount"] == 1
    assert success["excludedChildTaskCount"] == 1
    assert success["excludedNonShuttleTaskCount"] == 1
    assert success["excludedInvalidPlanCount"] == 1
    assert metrics["PATH_ALGORITHM"]["value"] == [
        {"algorithm": "astar", "sampleCount": 1},
        {"algorithm": "dijkstra", "sampleCount": 1},
    ]
    planning_groups = metrics["PATH_PLANNING_TIME_MS"]["value"]
    assert {group["group"]["algorithm"] for group in planning_groups} == {"astar", "dijkstra"}
    assert metrics["PATH_HORIZONTAL_DISTANCE_CELLS"]["sampleCount"] == 2
    assert metrics["PATH_VISITED_STATE_COUNT"]["sampleCount"] == 2
    assert metrics["PATH_PLANNING_TIME_MS"]["unit"] == "ms"
    assert metrics["PATH_HORIZONTAL_DISTANCE_CELLS"]["unit"] == "cell"
    assert result["sampleKind"] == "successful_shuttle_transfer_plan"


def test_path_metric_fields_are_independent_and_json_safe(isolated_repo):
    repo, _ = isolated_repo
    tasks = [
        _plan_task("zero", planning_time=0.0, distance=0.0, visited=0),
        _plan_task("none", planning_time=None, distance=2.0, visited=2),
        _plan_task("nan", planning_time=float("nan"), distance=3.0, visited=3),
        _plan_task("infinity", planning_time=float("inf"), distance=4.0, visited=4),
        _plan_task("negative", planning_time=-1.0, distance=5.0, visited=5),
        _plan_task("boolean", planning_time=True, distance=6.0, visited=True),
    ]
    repo.create_run("fields", "completed", "topology", {"tasks": tasks}, {"completedTasks": 0})
    result = repo.get_path_planning_metrics("fields")
    metrics = {item["metricId"]: item for item in result["metrics"]}

    assert metrics["PATH_SUCCESS_SAMPLE_COUNT"]["value"] == 6
    planning = metrics["PATH_PLANNING_TIME_MS"]
    assert planning["sampleCount"] == 1
    assert planning["invalidSampleCount"] == 5
    assert planning["value"][0]["minimum"] == 0
    assert planning["value"][0]["maximum"] == 0
    assert metrics["PATH_HORIZONTAL_DISTANCE_CELLS"]["sampleCount"] == 6
    assert metrics["PATH_VISITED_STATE_COUNT"]["sampleCount"] == 5
    assert metrics["PATH_VISITED_STATE_COUNT"]["invalidSampleCount"] == 1
    json.dumps(result, allow_nan=False)


def test_quantile_thresholds_and_nearest_rank_are_grouped(isolated_repo):
    repo, _ = isolated_repo
    tasks = []
    group_specs = (
        ("astar", "a4", 4),
        ("astar", "a5", 5),
        ("dijkstra", "d9", 9),
        ("dijkstra", "d10", 10),
        ("astar", "a99", 99),
        ("dijkstra", "d100", 100),
    )
    for algorithm, topology_revision, count in group_specs:
        tasks.extend(
            _plan_task(
                f"{topology_revision}-{index}",
                algorithm=algorithm,
                planning_time=float(index),
                topology_revision=topology_revision,
            )
            for index in range(1, count + 1)
        )
    repo.create_run("quantiles", "completed", "topology", {"tasks": tasks}, {"completedTasks": 0})
    result = repo.get_path_planning_metrics("quantiles")
    metrics = {item["metricId"]: item for item in result["metrics"]}
    quantile_metric = metrics["PATH_PLANNING_TIME_QUANTILES_MS"]
    groups = {
        group["group"]["topologyRevision"]: group for group in quantile_metric["value"]
    }

    assert groups["a4"]["quantiles"]["p50"]["value"] is None
    assert groups["a4"]["quantiles"]["p50"]["reason"] == "INSUFFICIENT_SAMPLE"
    assert groups["a5"]["quantiles"]["p50"]["value"] == 3
    assert groups["d9"]["quantiles"]["p90"]["value"] is None
    assert groups["d10"]["quantiles"]["p90"]["value"] == 9
    assert groups["a99"]["quantiles"]["p99"]["value"] is None
    assert groups["a99"]["quantiles"]["p99"]["actualSampleCount"] == 99
    assert groups["d100"]["quantiles"]["p99"]["value"] == 99
    assert groups["d100"]["quantiles"]["p99"]["requiredSampleCount"] == 100
    assert groups["a5"]["group"]["algorithm"] == "astar"
    assert groups["d10"]["group"]["algorithm"] == "dijkstra"
    assert quantile_metric["acceptanceEligible"] is False


def test_duplicate_conflict_missing_versions_and_no_samples(isolated_repo):
    repo, _ = isolated_repo
    exact = _plan_task("exact", planning_time=2.0)
    conflict_a = _plan_task("conflict", planning_time=3.0)
    conflict_b = _plan_task("conflict", planning_time=4.0)
    missing_versions = _plan_task(
        "missing-versions",
        planning_time=5.0,
        topology_revision=None,
        config_revision=None,
        coordinate_revision=None,
    )
    repo.create_run(
        "duplicates",
        "completed",
        "topology",
        {"tasks": [exact, deepcopy(exact), conflict_a, conflict_b, missing_versions]},
        {"completedTasks": 0},
    )
    result = repo.get_path_planning_metrics("duplicates")
    metrics = {item["metricId"]: item for item in result["metrics"]}
    assert metrics["PATH_SUCCESS_SAMPLE_COUNT"]["value"] == 2
    assert metrics["PATH_SUCCESS_SAMPLE_COUNT"]["conflictingTaskIdCount"] == 1
    quantile_groups = metrics["PATH_PLANNING_TIME_QUANTILES_MS"]["value"]
    missing_group = next(
        group for group in quantile_groups if group["group"]["topologyRevision"] is None
    )
    assert missing_group["dataQuality"] == "DEGRADED"
    assert missing_group["reason"] == "VERSION_METADATA_MISSING"

    repo.create_run("empty", "completed", "topology", {"tasks": []}, {"completedTasks": 0})
    empty = repo.get_path_planning_metrics("empty")
    empty_metrics = {item["metricId"]: item for item in empty["metrics"]}
    assert empty_metrics["PATH_SUCCESS_SAMPLE_COUNT"]["value"] == 0
    assert empty_metrics["PATH_PLANNING_TIME_MS"]["value"] is None
    assert empty_metrics["PATH_PLANNING_TIME_QUANTILES_MS"]["value"] is None
    assert empty_metrics["PATH_PLANNING_TIME_QUANTILES_MS"]["reason"] == "NO_SAMPLES"
    assert empty_metrics["PATH_REPLAN_SUCCESS_RATE"]["value"] is None
    assert empty_metrics["PATH_REPLAN_SUCCESS_RATE"]["implementationStatus"] == "UNAVAILABLE"
    assert empty_metrics["PATH_LENGTH_OPTIMIZATION_RATE"]["value"] is None


def test_path_planning_route_is_run_scoped_read_only_and_preserves_existing_apis(
    isolated_repo, monkeypatch
):
    repo, database_path = isolated_repo
    repo.create_run(
        "run-a",
        "completed",
        "topology-a",
        {"tasks": [_plan_task("a", algorithm="astar", planning_time=1.0)], "robots": []},
        {"completedTasks": 1},
    )
    repo.create_run(
        "run-b",
        "completed",
        "topology-b",
        {"tasks": [_plan_task("b", algorithm="dijkstra", planning_time=2.0)], "robots": []},
        {"completedTasks": 1},
    )
    repo.add_event("run-a", 1, "metrics_snapshot", {"completedTasks": 1})
    repo.add_event("run-b", 2, "metrics_snapshot", {"completedTasks": 1})

    routes = importlib.import_module("app.api.routes")
    shuttle_pathfinding = importlib.import_module("app.services.shuttle_pathfinding")
    unified_transfer = importlib.import_module("app.services.unified_transfer")

    def forbidden_planner_call(*args, **kwargs):
        raise AssertionError("path planning must not be called by metrics query")

    monkeypatch.setattr(shuttle_pathfinding, "plan_shuttle_path", forbidden_planner_call)
    monkeypatch.setattr(unified_transfer, "plan_shuttle_path", forbidden_planner_call)
    before_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()

    first = routes.get_simulation_path_planning_metrics("run-a")
    second = routes.get_simulation_path_planning_metrics("run-a")
    run_b = routes.get_simulation_path_planning_metrics("run-b")
    assert first == second
    first_metrics = {item["metricId"]: item for item in first["metrics"]}
    run_b_metrics = {item["metricId"]: item for item in run_b["metrics"]}
    assert first_metrics["PATH_ALGORITHM"]["value"] == [
        {"algorithm": "astar", "sampleCount": 1}
    ]
    assert run_b_metrics["PATH_ALGORITHM"]["value"] == [
        {"algorithm": "dijkstra", "sampleCount": 1}
    ]

    with pytest.raises(HTTPException) as error:
        routes.get_simulation_path_planning_metrics("not-present")
    assert error.value.status_code == 404

    assert routes.get_simulation_task_metric_summary("run-a")["runId"] == "run-a"
    assert routes.get_simulation_metrics_history_quality("run-a", max_points=20)["runId"] == "run-a"
    assert routes.get_simulation_assignment_contention_metrics("run-a")["runId"] == "run-a"
    route_paths = {route.path for route in routes.router.routes}
    assert "/api/v1/simulations/{run_id}/metrics/path-planning" in route_paths
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before_hash

