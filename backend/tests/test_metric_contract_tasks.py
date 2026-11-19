"""Gate M2-1 task metrics use only an isolated temporary SQLite database."""
from __future__ import annotations

import hashlib
import importlib

import pytest
from fastapi import HTTPException

from app.core import db
from app.core.repository import (
    Repository,
    _classify_metric_task_parentage,
    _deduplicate_metric_tasks,
)


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    database_path = tmp_path / "metric-contract.db"
    monkeypatch.setattr(db, "DB_PATH", database_path)
    db.init_db()
    assert database_path.parent == tmp_path
    return Repository(), database_path


def test_parent_child_classification(isolated_repo):
    repo, _ = isolated_repo
    assert _classify_metric_task_parentage({"id": "p-missing"}) == "parent"
    assert _classify_metric_task_parentage({"id": "p-null", "parentId": None}) == "parent"
    assert _classify_metric_task_parentage({"id": "p-empty", "parentId": ""}) == "parent"
    assert _classify_metric_task_parentage({"id": "child", "parentId": "parent-1"}) == "child"
    assert _classify_metric_task_parentage({"id": "bad", "parentId": 7}) == "invalid"
    assert _classify_metric_task_parentage({"id": "bad-space", "parentId": "  "}) == "invalid"

    tasks = [
        {"id": "p-done", "status": "completed", "createdTick": 0, "completedTick": 5},
        {"id": "c-done", "parentId": "p-done", "status": "completed", "createdTick": 1, "completedTick": 4},
        {"id": "p-failed", "status": "failed", "createdTick": 0, "completedTick": 2},
        {"id": "p-cancelled", "status": "cancelled", "createdTick": 0, "completedTick": 2},
        {"id": "p-pending", "status": "pending", "createdTick": 0, "completedTick": None},
        {"id": "invalid-parent", "parentId": 12, "status": "completed", "createdTick": 0, "completedTick": 2},
    ]
    repo.create_run("classification", "completed", "topology", {"tasks": tasks}, {"completedTasks": 1})
    result = repo.get_task_metric_summary("classification")
    assert result is not None
    metrics = {item["metricId"]: item for item in result["metrics"]}
    assert metrics["PARENT_COMPLETED_TASK_COUNT"]["value"] == 1
    assert metrics["CHILD_COMPLETED_TASK_COUNT"]["value"] == 1
    assert metrics["LEGACY_COMPLETED_TASKS"]["value"] == 1
    assert metrics["PARENT_COMPLETED_TASK_COUNT_DELTA"]["value"] == 0
    assert metrics["INVALID_TASK_RECORD_COUNT"]["value"] == 1
    assert metrics["PARENT_COMPLETED_TASK_COUNT"]["dataQuality"] == "DEGRADED"

    reference_tasks = [
        {"id": f"reference-{index}", "status": "completed", "createdTick": 0, "completedTick": index}
        for index in range(9)
    ]
    repo.create_run("reference", "completed", "topology", {"tasks": reference_tasks}, {"completedTasks": 7})
    reference = repo.get_task_metric_summary("reference")
    reference_metrics = {item["metricId"]: item for item in reference["metrics"]}
    assert reference_metrics["LEGACY_COMPLETED_TASKS"]["value"] == 7
    assert reference_metrics["PARENT_COMPLETED_TASK_COUNT"]["value"] == 9
    assert reference_metrics["PARENT_COMPLETED_TASK_COUNT_DELTA"]["value"] == 2


def test_task_metric_deduplication(isolated_repo):
    repo, _ = isolated_repo
    task = {"id": "same", "status": "completed", "createdTick": 0, "completedTick": 2}
    deduplicated, invalid_count, conflict_count = _deduplicate_metric_tasks([task, dict(task)])
    assert len(deduplicated) == 1
    assert invalid_count == 0
    assert conflict_count == 0

    repo.create_run("exact-duplicate", "completed", "topology", {"tasks": [task, dict(task)]}, {"completedTasks": 1})
    exact = repo.get_task_metric_summary("exact-duplicate")
    exact_metrics = {item["metricId"]: item for item in exact["metrics"]}
    assert exact_metrics["PARENT_COMPLETED_TASK_COUNT"]["value"] == 1
    assert exact_metrics["CONFLICTING_DUPLICATE_TASK_ID_COUNT"]["value"] == 0

    conflict = dict(task, status="failed")
    repo.create_run("conflict", "completed", "topology", {"tasks": [task, conflict]}, {"completedTasks": 1})
    result = repo.get_task_metric_summary("conflict")
    metrics = {item["metricId"]: item for item in result["metrics"]}
    assert metrics["CONFLICTING_DUPLICATE_TASK_ID_COUNT"]["value"] == 1
    assert metrics["PARENT_COMPLETED_TASK_COUNT"]["value"] is None
    assert metrics["PARENT_COMPLETED_TASK_COUNT"]["reason"] == "DUPLICATE_TASK_CONFLICT"
    assert metrics["PARENT_CREATE_TO_COMPLETE_AVG_TICKS"]["value"] is None
    assert metrics["PARENT_CREATE_TO_COMPLETE_AVG_TICKS"]["reason"] == "DUPLICATE_TASK_CONFLICT"


def test_task_duration_null_rules(isolated_repo):
    repo, _ = isolated_repo
    tasks = [
        {"id": "p-ten", "status": "completed", "createdTick": 0, "completedTick": 10},
        {"id": "p-zero", "status": "completed", "createdTick": 5, "completedTick": 5},
        {"id": "p-negative", "status": "completed", "createdTick": 20, "completedTick": 10},
        {"id": "p-missing", "status": "completed", "createdTick": 2},
        {"id": "child-six", "parentId": "p-ten", "status": "completed", "createdTick": 2, "completedTick": 8},
    ]
    repo.create_run("durations", "completed", "topology", {"tasks": tasks}, {"completedTasks": 4})
    result = repo.get_task_metric_summary("durations")
    metrics = {item["metricId"]: item for item in result["metrics"]}

    assert metrics["PARENT_CREATE_TO_COMPLETE_AVG_TICKS"]["value"] == 5
    assert metrics["PARENT_CREATE_TO_COMPLETE_MAX_TICKS"]["value"] == 10
    assert metrics["PARENT_CREATE_TO_COMPLETE_SAMPLE_COUNT"]["value"] == 2
    assert metrics["PARENT_CREATE_TO_COMPLETE_EXCLUDED_COUNT"]["value"] == 2
    assert metrics["CHILD_CREATE_TO_COMPLETE_AVG_TICKS"]["value"] == 6
    assert metrics["CHILD_CREATE_TO_COMPLETE_MAX_TICKS"]["value"] == 6
    assert metrics["CHILD_CREATE_TO_COMPLETE_SAMPLE_COUNT"]["value"] == 1
    assert metrics["PARENT_FIRST_ASSIGN_TO_COMPLETE_AVG_TICKS"]["value"] is None
    assert metrics["PARENT_FIRST_ASSIGN_TO_COMPLETE_SAMPLE_COUNT"]["value"] == 0
    assert metrics["PARENT_FIRST_ASSIGN_TO_COMPLETE_MISSING_COUNT"]["value"] == 4
    assert metrics["PARENT_FIRST_ASSIGN_TO_COMPLETE_AVG_TICKS"]["unit"] == "Tick"
    assert metrics["PARENT_FIRST_ASSIGN_TO_COMPLETE_AVG_TICKS"]["acceptanceEligible"] is False


def test_first_assignment_requires_history_evidence(isolated_repo):
    repo, _ = isolated_repo
    tasks = [
        {
            "id": "reassigned",
            "status": "completed",
            "createdTick": 0,
            "startedTick": 20,
            "completedTick": 30,
            "history": [
                {"tick": 5, "status": "assigned", "phase": "pickup_from_ctu"},
                {"tick": 10, "status": "pending", "phase": "pending_assignment"},
                {"tick": 20, "status": "assigned", "phase": "pickup_from_ctu"},
            ],
        },
        {
            "id": "started-only",
            "status": "completed",
            "createdTick": 0,
            "startedTick": 1,
            "completedTick": 10,
            "history": [],
        },
        {
            "id": "event-child",
            "parentId": "reassigned",
            "status": "completed",
            "createdTick": 1,
            "startedTick": 9,
            "completedTick": 10,
            "history": [],
        },
    ]
    repo.create_run("assignment", "completed", "topology", {"tasks": tasks}, {"completedTasks": 2})
    repo.add_event(
        "assignment",
        4,
        "task_state_changed",
        {"taskId": "event-child", "status": "assigned", "robotId": "ctu-1"},
    )
    result = repo.get_task_metric_summary("assignment")
    metrics = {item["metricId"]: item for item in result["metrics"]}

    assert metrics["PARENT_FIRST_ASSIGN_TO_COMPLETE_AVG_TICKS"]["value"] == 25
    assert metrics["PARENT_FIRST_ASSIGN_TO_COMPLETE_MAX_TICKS"]["value"] == 25
    assert metrics["PARENT_FIRST_ASSIGN_TO_COMPLETE_SAMPLE_COUNT"]["value"] == 1
    assert metrics["PARENT_FIRST_ASSIGN_TO_COMPLETE_MISSING_COUNT"]["value"] == 1
    assert metrics["CHILD_FIRST_ASSIGN_TO_COMPLETE_AVG_TICKS"]["value"] == 6
    assert metrics["CHILD_FIRST_ASSIGN_TO_COMPLETE_SAMPLE_COUNT"]["value"] == 1


def test_task_metric_query_is_run_scoped_and_read_only(isolated_repo):
    repo, database_path = isolated_repo
    repo.create_run(
        "run-a",
        "completed",
        "topology-a",
        {"tasks": [{"id": "a", "status": "completed", "createdTick": 0, "completedTick": 3}]},
        {"completedTasks": 1},
    )
    repo.create_run(
        "run-b",
        "completed",
        "topology-b",
        {
            "tasks": [
                {"id": f"b-{index}", "status": "completed", "createdTick": 0, "completedTick": 5}
                for index in range(5)
            ]
        },
        {"completedTasks": 5},
    )
    repo.add_event("run-a", 3, "metrics_snapshot", {"completedTasks": 1})
    repo.add_event("run-b", 5, "metrics_snapshot", {"completedTasks": 5})

    routes = importlib.import_module("app.api.routes")
    before_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()

    first = routes.get_simulation_task_metric_summary("run-a")
    second = routes.get_simulation_task_metric_summary("run-a")
    run_b = routes.get_simulation_task_metric_summary("run-b")
    first_metrics = {item["metricId"]: item for item in first["metrics"]}
    run_b_metrics = {item["metricId"]: item for item in run_b["metrics"]}
    assert first == second
    assert first_metrics["PARENT_COMPLETED_TASK_COUNT"]["value"] == 1
    assert run_b_metrics["PARENT_COMPLETED_TASK_COUNT"]["value"] == 5

    with pytest.raises(HTTPException) as error:
        routes.get_simulation_task_metric_summary("not-present")
    assert error.value.status_code == 404

    route_paths = {route.path for route in routes.router.routes}
    assert "/api/v1/simulations/{run_id}/metrics" in route_paths
    assert "/api/v1/simulations/{run_id}/metrics/tasks" in route_paths
    assert "/api/v1/simulations/{run_id}/metrics/history" not in route_paths

    after_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()
    assert after_hash == before_hash


def test_running_run_metrics_use_persisted_payload_without_state_synchronization(isolated_repo):
    """The read endpoint must not infer up to nine newer ticks from events or runtime state."""
    repo, _ = isolated_repo
    repo.create_run(
        "running-lag",
        "running",
        "topology",
        {
            "tick": 10,
            "tasks": [
                {
                    "id": "persisted-parent",
                    "robotType": "ctu",
                    "status": "completed",
                    "createdTick": 0,
                    "completedTick": 10,
                    "taskKind": "picking",
                }
            ],
        },
        {"completedTasks": 1},
    )
    repo.add_event(
        "running-lag",
        19,
        "task_state_changed",
        {"taskId": "not-yet-persisted", "status": "completed", "phase": "completed"},
    )

    result = repo.get_task_metric_summary("running-lag")
    metrics = {item["metricId"]: item for item in result["metrics"]}
    assert metrics["PARENT_COMPLETED_TASK_COUNT"]["value"] == 1
    assert metrics["LEGACY_COMPLETED_TASKS"]["value"] == 1
    assert all(item["metricId"] != "not-yet-persisted" for item in result["metrics"])

