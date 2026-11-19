"""Gate M2-2 history quality metrics use only an isolated SQLite database."""
from __future__ import annotations

import hashlib
import importlib
import json

import pytest
from fastapi import HTTPException

from app.core import config, db
from app.core.repository import Repository


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    database_path = tmp_path / "history-quality.db"
    monkeypatch.setattr(db, "DB_PATH", database_path)
    db.init_db()
    assert database_path.parent == tmp_path
    assert database_path != config.DB_PATH
    return Repository(), database_path


def test_history_quality_deduplicates_latest_snapshot_and_isolates_runs(isolated_repo):
    repo, _ = isolated_repo
    repo.create_run("run-a", "completed", "topology-a", {"tasks": []}, {"completedTasks": 0})
    repo.create_run("run-b", "completed", "topology-b", {"tasks": []}, {"completedTasks": 0})
    repo.add_event("run-a", 5, "metrics_snapshot", {"blockedRobots": 1})
    repo.add_event("run-a", 5, "metrics_snapshot", {"blockedRobots": 3})
    repo.add_event("run-b", 5, "metrics_snapshot", {"blockedRobots": 99})

    run_a = repo.get_metrics_history_quality("run-a", max_points=20)
    assert run_a is not None
    assert run_a["rawSampleCount"] == 2
    assert run_a["uniqueTickCount"] == 1
    assert run_a["duplicateSnapshotCount"] == 1
    assert run_a["outputSampleCount"] == 1
    assert run_a["points"][0]["tick"] == 5
    assert run_a["points"][0]["blockedRobots"] == 3
    assert run_a["statistics"]["blockedRobots"]["maximum"] == 3

    run_b = repo.get_metrics_history_quality("run-b", max_points=20)
    assert run_b["rawSampleCount"] == 1
    assert run_b["points"][0]["blockedRobots"] == 99
    assert run_b["statistics"]["blockedRobots"]["validSampleCount"] == 1


def test_history_quality_sampling_keeps_endpoints_and_uses_full_data(isolated_repo):
    repo, _ = isolated_repo
    repo.create_run("sampling", "completed", "topology", {"tasks": []}, {"completedTasks": 0})
    for tick in range(30):
        repo.add_event(
            "sampling",
            tick,
            "metrics_snapshot",
            {
                "blockedRobots": 999 if tick == 4 else tick,
                "activeRobots": tick,
                "completedTasks": tick,
                "pendingTasks": 30 - tick,
                "conveyorQueueDepth": tick % 4,
                "stationQueueDepth": tick % 3,
                "bufferOccupancy": tick % 2,
                "conveyorUtilization": float(tick),
            },
        )

    result = repo.get_metrics_history_quality("sampling", max_points=20)
    assert result["rawSampleCount"] == 30
    assert result["uniqueTickCount"] == 30
    assert result["duplicateSnapshotCount"] == 0
    assert result["outputSampleCount"] == 20
    assert result["points"][0]["tick"] == 0
    assert result["points"][-1]["tick"] == 29
    assert 4 not in {point["tick"] for point in result["points"]}
    blocked = result["statistics"]["blockedRobots"]
    assert blocked["maximum"] == 999
    assert blocked["maximumTick"] == 4
    assert blocked["validSampleCount"] == 30
    assert blocked["average"] == (sum(range(30)) - 4 + 999) / 30


def test_history_quality_distinguishes_missing_zero_and_no_snapshots(isolated_repo):
    repo, _ = isolated_repo
    repo.create_run("empty", "completed", "topology", {"tasks": []}, {"completedTasks": 0})
    empty = repo.get_metrics_history_quality("empty", max_points=20)
    assert empty["rawSampleCount"] == 0
    assert empty["uniqueTickCount"] == 0
    assert empty["outputSampleCount"] == 0
    assert empty["points"] == []
    assert empty["dataQuality"] == "MISSING_SOURCE"
    assert empty["reason"] == "NO_METRICS_SNAPSHOTS"
    assert empty["statistics"]["blockedRobots"]["maximum"] is None

    repo.create_run("zero", "completed", "topology", {"tasks": []}, {"completedTasks": 0})
    repo.add_event("zero", 0, "metrics_snapshot", {"blockedRobots": 0})
    repo.add_event("zero", 1, "metrics_snapshot", {"activeRobots": 1})
    result = repo.get_metrics_history_quality("zero", max_points=20)
    blocked = result["statistics"]["blockedRobots"]
    assert blocked["minimum"] == 0
    assert blocked["maximum"] == 0
    assert blocked["average"] == 0
    assert blocked["validSampleCount"] == 1
    assert blocked["missingSampleCount"] == 1
    completed = result["statistics"]["completedTasks"]
    assert completed["maximum"] is None
    assert completed["validSampleCount"] == 0
    assert completed["reason"] == "FIELD_NOT_PRESENT"


def test_history_quality_rejects_invalid_json_nan_infinity_and_no_fallback(isolated_repo):
    repo, _ = isolated_repo
    repo.create_run("invalid", "completed", "topology", {"tasks": []}, {"completedTasks": 0})
    repo.add_event("invalid", 1, "metrics_snapshot", {"blockedRobots": 4})
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO simulation_events (run_id, tick, event_type, payload, created_at)
               VALUES (?, ?, 'metrics_snapshot', ?, ?)""",
            ("invalid", 1, "{not-json", "test-only"),
        )
    repo.add_event("invalid", 2, "metrics_snapshot", {"blockedRobots": float("nan")})
    repo.add_event("invalid", 3, "metrics_snapshot", {"blockedRobots": float("inf")})
    repo.add_event("invalid", 4, "metrics_snapshot", {"blockedRobots": 0})

    result = repo.get_metrics_history_quality("invalid", max_points=20)
    assert result["rawSampleCount"] == 5
    assert result["uniqueTickCount"] == 4
    assert result["duplicateSnapshotCount"] == 1
    assert result["invalidSnapshotCount"] == 1
    tick_one = next(point for point in result["points"] if point["tick"] == 1)
    assert "blockedRobots" not in tick_one
    assert tick_one["reason"] == "INVALID_SNAPSHOT_PAYLOAD"
    blocked = result["statistics"]["blockedRobots"]
    assert blocked["validSampleCount"] == 1
    assert blocked["invalidSampleCount"] == 3
    assert blocked["maximum"] == 0
    assert blocked["maximumTick"] == 4
    assert "INVALID_SNAPSHOT_PAYLOAD" in result["qualityIssues"]
    assert "INVALID_METRIC_VALUES" in result["qualityIssues"]
    json.dumps(result, allow_nan=False)


def test_history_quality_routes_validate_and_queries_are_read_only(isolated_repo):
    repo, database_path = isolated_repo
    repo.create_run(
        "run-api",
        "completed",
        "topology",
        {
            "tasks": [
                {"id": "parent", "status": "completed", "createdTick": 0, "completedTick": 2}
            ]
        },
        {"completedTasks": 1},
    )
    repo.add_event("run-api", 2, "metrics_snapshot", {"completedTasks": 1, "blockedRobots": 0})

    routes = importlib.import_module("app.api.routes")
    before_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()
    first = routes.get_simulation_metrics_history_quality("run-api", max_points=20)
    second = routes.get_simulation_metrics_history_quality("run-api", max_points=20)
    assert first == second

    with pytest.raises(HTTPException) as missing_error:
        routes.get_simulation_metrics_history_quality("not-present", max_points=20)
    assert missing_error.value.status_code == 404
    for invalid_max_points in (19, 1001):
        with pytest.raises(HTTPException) as validation_error:
            routes.get_simulation_metrics_history_quality(
                "run-api", max_points=invalid_max_points
            )
        assert validation_error.value.status_code == 422

    task_metrics = routes.get_simulation_task_metric_summary("run-api")
    task_metric_map = {item["metricId"]: item for item in task_metrics["metrics"]}
    assert task_metric_map["PARENT_COMPLETED_TASK_COUNT"]["value"] == 1

    route_paths = {route.path for route in routes.router.routes}
    assert "/api/v1/simulations/{run_id}/metrics/history" not in route_paths
    assert "/api/v1/simulations/{run_id}/metrics/history/quality" in route_paths
    assert "/api/v1/simulations/{run_id}/metrics/tasks" in route_paths
    after_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()
    assert after_hash == before_hash

