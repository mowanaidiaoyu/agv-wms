"""Gate M2-3 assignment/contention metrics use only an isolated SQLite database."""
from __future__ import annotations

import hashlib
import importlib

import pytest
from fastapi import HTTPException

from app.core import config, db
from app.core.repository import Repository


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    database_path = tmp_path / "assignment-contention.db"
    monkeypatch.setattr(db, "DB_PATH", database_path)
    db.init_db()
    assert database_path.parent == tmp_path
    assert database_path != config.DB_PATH
    return Repository(), database_path


def test_ctu_assignment_scope_excludes_shuttle_and_child_tasks(isolated_repo):
    repo, _ = isolated_repo
    tasks = [
        {
            "id": "ctu-parent",
            "robotType": "ctu",
            "status": "completed",
            "createdTick": 1,
            "startedTick": 99,
            "completedTick": 20,
        },
        {
            "id": "ctu-child",
            "parentId": "ctu-parent",
            "robotType": "ctu",
            "status": "completed",
            "createdTick": 2,
            "completedTick": 20,
        },
        {
            "id": "shuttle-parent",
            "robotType": "shuttle",
            "status": "completed",
            "createdTick": 0,
            "completedTick": 20,
        },
        {
            "id": "conveyor-child",
            "parentId": "ctu-parent",
            "robotType": "none",
            "status": "completed",
            "createdTick": 2,
            "completedTick": 20,
        },
    ]
    robots = [
        {"id": "ctu-1", "type": "ctu", "status": "idle"},
        {"id": "shuttle-1", "type": "shuttle", "status": "idle"},
    ]
    repo.create_run(
        "scope",
        "completed",
        "topology",
        {"tasks": tasks, "robots": robots},
        {"completedTasks": 2},
    )
    for task_id, robot_id in (
        ("ctu-parent", "ctu-1"),
        ("ctu-child", "ctu-1"),
        ("shuttle-parent", "shuttle-1"),
        ("conveyor-child", "ctu-1"),
    ):
        repo.add_event(
            "scope",
            5,
            "task_state_changed",
            {"taskId": task_id, "status": "assigned", "robotId": robot_id, "phase": "pickup"},
        )

    result = repo.get_assignment_contention_metrics("scope")
    metrics = {item["metricId"]: item for item in result["metrics"]}
    assert metrics["CTU_ASSIGNED_PARENT_TASK_COUNT"]["value"] == 1
    assert metrics["CTU_ASSIGNED_PARENT_TASK_COUNT"]["outOfScopeAssignmentEventCount"] == 3
    assert metrics["CTU_FIRST_ASSIGN_WAIT_AVG_TICKS"]["value"] == 4
    assert metrics["CTU_FIRST_ASSIGN_WAIT_MAX_TICKS"]["value"] == 4
    assert metrics["CTU_FIRST_ASSIGN_WAIT_SAMPLE_COUNT"]["value"] == 1
    assert metrics["CTU_VEHICLE_TASK_ASSOCIATION_COUNT"]["value"] == [
        {"robotId": "ctu-1", "taskCount": 1}
    ]
    assert metrics["CTU_FIRST_ASSIGN_WAIT_AVG_TICKS"]["reason"] == "EVENT_COLLECTION_UNCONFIRMED"
    assert metrics["CTU_FIRST_ASSIGN_WAIT_AVG_TICKS"]["acceptanceEligible"] is False


def test_first_assignment_and_vehicle_associations_handle_reassignment_and_invalid_data(isolated_repo):
    repo, _ = isolated_repo
    tasks = [
        {"id": "reassigned", "robotType": "ctu", "status": "completed", "createdTick": 2},
        {"id": "missing-event", "robotType": "ctu", "status": "pending", "createdTick": 0},
        {"id": "negative-wait", "robotType": "ctu", "status": "assigned", "createdTick": 30},
        {"id": "bad-created", "robotType": "ctu", "status": "assigned", "createdTick": "bad"},
    ]
    robots = [
        {"id": "ctu-1", "type": "ctu", "status": "idle"},
        {"id": "ctu-2", "type": "ctu", "status": "idle"},
    ]
    repo.create_run(
        "reassignment",
        "completed",
        "topology",
        {"tasks": tasks, "robots": robots},
        {"completedTasks": 1},
    )
    for tick, robot_id in ((10, "ctu-1"), (12, "ctu-1"), (20, "ctu-2")):
        repo.add_event(
            "reassignment",
            tick,
            "task_state_changed",
            {"taskId": "reassigned", "status": "assigned", "robotId": robot_id},
        )
    repo.add_event(
        "reassignment",
        20,
        "task_state_changed",
        {"taskId": "negative-wait", "status": "assigned", "robotId": "ctu-1"},
    )
    repo.add_event(
        "reassignment",
        8,
        "task_state_changed",
        {"taskId": "bad-created", "status": "assigned", "robotId": "ctu-1"},
    )
    repo.add_event(
        "reassignment",
        -1,
        "task_state_changed",
        {"taskId": "reassigned", "status": "assigned", "robotId": "ctu-1"},
    )
    repo.add_event(
        "reassignment",
        1,
        "task_state_changed",
        {"taskId": "reassigned", "status": "assigned", "robotId": ""},
    )
    repo.add_event(
        "reassignment",
        25,
        "task_state_changed",
        {"taskId": "reassigned", "status": "assigned", "phase": "place_goods"},
    )

    result = repo.get_assignment_contention_metrics("reassignment")
    metrics = {item["metricId"]: item for item in result["metrics"]}
    assert metrics["CTU_ASSIGNED_PARENT_TASK_COUNT"]["value"] == 3
    assert metrics["CTU_ASSIGNED_PARENT_TASK_COUNT"]["invalidAssignmentEventCount"] == 2
    assert metrics["CTU_ASSIGNED_PARENT_TASK_COUNT"]["ignoredAssignedStateEventCount"] == 1
    assert metrics["CTU_FIRST_ASSIGN_WAIT_AVG_TICKS"]["value"] == 8
    assert metrics["CTU_FIRST_ASSIGN_WAIT_MAX_TICKS"]["value"] == 8
    assert metrics["CTU_FIRST_ASSIGN_WAIT_SAMPLE_COUNT"]["value"] == 1
    assert metrics["CTU_FIRST_ASSIGN_WAIT_SAMPLE_COUNT"]["missingAssignmentEvidenceCount"] == 1
    assert metrics["CTU_FIRST_ASSIGN_WAIT_SAMPLE_COUNT"]["invalidCreatedTickCount"] == 1
    assert metrics["CTU_FIRST_ASSIGN_WAIT_SAMPLE_COUNT"]["negativeWaitCount"] == 1
    associations = metrics["CTU_VEHICLE_TASK_ASSOCIATION_COUNT"]
    assert associations["sampleCount"] == 4
    assert associations["value"] == [
        {"robotId": "ctu-1", "taskCount": 3},
        {"robotId": "ctu-2", "taskCount": 1},
    ]


def test_contention_events_are_observed_without_false_deadlock_claims(isolated_repo):
    repo, _ = isolated_repo
    robots = [
        {"id": "ctu-1", "type": "ctu", "status": "blocked"},
        {"id": "ctu-2", "type": "ctu", "status": "blocked"},
        {"id": "ctu-2", "type": "ctu", "status": "blocked"},
        {"id": "shuttle-1", "type": "shuttle", "status": "idle"},
        {"id": "", "type": "ctu", "status": "blocked"},
    ]
    repo.create_run(
        "contention",
        "completed",
        "topology",
        {"tasks": [], "robots": robots},
        {"completedTasks": 0},
    )
    waiting_chain = [
        {"robotId": "ctu-1", "taskId": None},
        {"robotId": "ctu-2", "taskId": "task-2"},
    ]
    repo.add_event("contention", 3, "deadlock_detected", {"waitingChain": waiting_chain})
    repo.add_event("contention", 4, "deadlock_detected", {"waitingChain": waiting_chain})
    repo.add_event(
        "contention", 5, "deadlock_detected", {"waitingChain": [{"robotId": "ctu-1"}]}
    )
    repo.add_event("contention", 6, "robot_yield", {"robotId": "ctu-1"})
    repo.add_event("contention", 7, "robot_yield", {"robotId": "ctu-1"})
    repo.add_event("contention", 8, "robot_yield", {"robotId": ""})
    repo.add_event("contention", 9, "robot_position", {"robotId": "ctu-2", "status": "yielding"})

    result = repo.get_assignment_contention_metrics("contention")
    metrics = {item["metricId"]: item for item in result["metrics"]}
    blocked = metrics["BLOCKED_ROBOT_COUNT_CURRENT"]
    assert blocked["value"] == 2
    assert blocked["invalidRobotRecordCount"] == 1
    suspected = metrics["SUSPECTED_DEADLOCK_ALERT_COUNT"]
    assert suspected["value"] == 2
    assert suspected["sampleCount"] == 2
    assert suspected["invalidEventCount"] == 1
    assert suspected["reason"] == "EVENT_COLLECTION_UNCONFIRMED"
    yielded = metrics["ROBOT_YIELD_EVENT_COUNT"]
    assert yielded["displayName"] == "CTU 主动让行事件"
    assert yielded["value"] == 2
    assert yielded["invalidEventCount"] == 1
    assert metrics["TRUE_DEADLOCK_RATE"]["value"] is None
    assert metrics["TRUE_DEADLOCK_RATE"]["implementationStatus"] == "UNAVAILABLE"
    assert metrics["DEADLOCK_UNLOCK_AVG_DURATION"]["value"] is None
    assert metrics["BLOCKAGE_DURATION"]["value"] is None


def test_no_events_are_null_while_current_blocked_zero_is_real(isolated_repo):
    repo, _ = isolated_repo
    repo.create_run(
        "no-events",
        "completed",
        "topology",
        {
            "tasks": [
                {"id": "ctu-task", "robotType": "ctu", "status": "pending", "createdTick": 0}
            ],
            "robots": [{"id": "ctu-1", "type": "ctu", "status": "idle"}],
        },
        {"completedTasks": 0},
    )
    result = repo.get_assignment_contention_metrics("no-events")
    metrics = {item["metricId"]: item for item in result["metrics"]}
    assert metrics["CTU_ASSIGNED_PARENT_TASK_COUNT"]["value"] is None
    assert metrics["CTU_FIRST_ASSIGN_WAIT_SAMPLE_COUNT"]["value"] is None
    assert metrics["CTU_VEHICLE_TASK_ASSOCIATION_COUNT"]["value"] is None
    assert metrics["SUSPECTED_DEADLOCK_ALERT_COUNT"]["value"] is None
    assert metrics["ROBOT_YIELD_EVENT_COUNT"]["value"] is None
    assert metrics["BLOCKED_ROBOT_COUNT_CURRENT"]["value"] == 0
    assert metrics["BLOCKED_ROBOT_COUNT_CURRENT"]["dataQuality"] == "GOOD"
    assert metrics["CTU_ASSIGNMENT_COST"]["value"] is None
    assert metrics["CTU_DEVICE_UTILIZATION"]["value"] is None
    assert result["eventCollectionCompleteness"] == "UNCONFIRMED"


def test_assignment_contention_route_is_run_scoped_read_only_and_preserves_existing_apis(isolated_repo):
    repo, database_path = isolated_repo
    for run_id, robot_id in (("run-a", "ctu-a"), ("run-b", "ctu-b")):
        repo.create_run(
            run_id,
            "completed",
            "topology",
            {
                "tasks": [
                    {"id": f"task-{run_id}", "robotType": "ctu", "status": "completed", "createdTick": 0, "completedTick": 5}
                ],
                "robots": [{"id": robot_id, "type": "ctu", "status": "idle"}],
            },
            {"completedTasks": 1},
        )
        repo.add_event(
            run_id,
            1 if run_id == "run-a" else 3,
            "task_state_changed",
            {"taskId": f"task-{run_id}", "status": "assigned", "robotId": robot_id},
        )
        repo.add_event(run_id, 5, "metrics_snapshot", {"completedTasks": 1})

    routes = importlib.import_module("app.api.routes")
    before_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()
    first = routes.get_simulation_assignment_contention_metrics("run-a")
    second = routes.get_simulation_assignment_contention_metrics("run-a")
    run_b = routes.get_simulation_assignment_contention_metrics("run-b")
    assert first == second
    first_metrics = {item["metricId"]: item for item in first["metrics"]}
    run_b_metrics = {item["metricId"]: item for item in run_b["metrics"]}
    assert first_metrics["CTU_FIRST_ASSIGN_WAIT_AVG_TICKS"]["value"] == 1
    assert run_b_metrics["CTU_FIRST_ASSIGN_WAIT_AVG_TICKS"]["value"] == 3

    with pytest.raises(HTTPException) as error:
        routes.get_simulation_assignment_contention_metrics("not-present")
    assert error.value.status_code == 404

    task_metrics = routes.get_simulation_task_metric_summary("run-a")
    task_metric_map = {item["metricId"]: item for item in task_metrics["metrics"]}
    assert task_metric_map["PARENT_COMPLETED_TASK_COUNT"]["value"] == 1
    quality_history = routes.get_simulation_metrics_history_quality("run-a", max_points=20)
    assert quality_history["rawSampleCount"] == 1
    route_paths = {route.path for route in routes.router.routes}
    assert "/api/v1/simulations/{run_id}/metrics/ctu-contention" in route_paths
    assert "/api/v1/simulations/{run_id}/metrics/tasks" in route_paths
    assert "/api/v1/simulations/{run_id}/metrics/history/quality" in route_paths
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before_hash

