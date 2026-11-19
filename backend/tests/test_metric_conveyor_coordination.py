"""Gate M2-5A conveyor coordination metrics use only an isolated database."""
from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app.core import config, db
from app.core.repository import Repository


_BUSINESS_TABLES = ("topologies", "simulation_runs", "simulation_events")


def _file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stabilize_temporary_wal(database_path):
    """Move committed fixture data into the temporary main DB before baselining."""
    assert database_path.resolve() != config.DB_PATH.resolve()
    conn = sqlite3.connect(database_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        conn.close()
    assert checkpoint == (0, 0, 0)
    wal_path = database_path.with_name(f"{database_path.name}-wal")
    assert not wal_path.exists() or wal_path.stat().st_size == 0


def _database_contract_snapshot():
    """Capture schema and complete business rows through the production read path."""
    with db.get_read_conn() as conn:
        schema = tuple(
            conn.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE type IN ('table', 'index')
                ORDER BY type, name
                """
            ).fetchall()
        )
        tables = {
            table: tuple(conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall())
            for table in _BUSINESS_TABLES
        }
    return {"schema": schema, "tables": tables}


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    database_path = tmp_path / "conveyor-coordination.db"
    monkeypatch.setattr(db, "DB_PATH", database_path)
    db.init_db()
    assert database_path.parent == tmp_path
    assert database_path != config.DB_PATH
    return Repository(), database_path


def test_conveyor_coordination_returns_current_percent_and_queue_counts(isolated_repo):
    repo, _ = isolated_repo
    repo.create_run(
        "normal",
        "completed",
        "topology",
        {"tasks": [], "robots": []},
        {
            "conveyorUtilization": 37.5,
            "conveyorGraphNodes": 10,
            "conveyorQueueDepth": 2,
            "bufferOccupancy": 3,
            "stationQueueDepth": 4,
        },
    )
    result = repo.get_conveyor_coordination_metrics("normal")
    metrics = {item["metricId"]: item for item in result["metrics"]}

    occupancy = metrics["CONVEYOR_NODE_OCCUPANCY_CURRENT"]
    assert occupancy["value"] == 37.5
    assert occupancy["unit"] == "%"
    assert occupancy["alreadyPercent"] is True
    assert occupancy["graphNodeCount"] == 10
    assert "not_physical_utilization" in occupancy["semantics"]
    assert metrics["CONVEYOR_WAIT_TASK_COUNT_CURRENT"]["value"] == 2
    assert metrics["BUFFER_WAIT_TASK_COUNT_CURRENT"]["value"] == 3
    assert metrics["STATION_QUEUE_TASK_COUNT_CURRENT"]["value"] == 4
    assert result["scope"] == "current_persisted_final_metrics"
    assert result["dataQuality"] == "GOOD"


def test_conveyor_graph_zero_missing_and_real_zero_are_distinct(isolated_repo):
    repo, _ = isolated_repo
    repo.create_run(
        "no-graph",
        "completed",
        "topology",
        {"tasks": []},
        {"conveyorGraphNodes": 0, "conveyorUtilization": 0.0},
    )
    repo.create_run(
        "missing-graph",
        "completed",
        "topology",
        {"tasks": []},
        {"conveyorUtilization": 0.0},
    )
    repo.create_run(
        "real-zero",
        "completed",
        "topology",
        {"tasks": []},
        {"conveyorGraphNodes": 8, "conveyorUtilization": 0.0},
    )

    no_graph = {
        item["metricId"]: item
        for item in repo.get_conveyor_coordination_metrics("no-graph")["metrics"]
    }
    missing_graph = {
        item["metricId"]: item
        for item in repo.get_conveyor_coordination_metrics("missing-graph")["metrics"]
    }
    real_zero = {
        item["metricId"]: item
        for item in repo.get_conveyor_coordination_metrics("real-zero")["metrics"]
    }
    assert no_graph["CONVEYOR_NODE_OCCUPANCY_CURRENT"]["value"] is None
    assert no_graph["CONVEYOR_NODE_OCCUPANCY_CURRENT"]["reason"] == "NO_CONVEYOR_GRAPH"
    assert missing_graph["CONVEYOR_NODE_OCCUPANCY_CURRENT"]["value"] is None
    assert (
        missing_graph["CONVEYOR_NODE_OCCUPANCY_CURRENT"]["reason"]
        == "CONVEYOR_GRAPH_NODES_MISSING"
    )
    assert missing_graph["CONVEYOR_NODE_OCCUPANCY_CURRENT"]["dataQuality"] == "MISSING_SOURCE"
    assert missing_graph["CONVEYOR_NODE_OCCUPANCY_CURRENT"]["acceptanceEligible"] is False
    assert real_zero["CONVEYOR_NODE_OCCUPANCY_CURRENT"]["value"] == 0
    assert real_zero["CONVEYOR_NODE_OCCUPANCY_CURRENT"]["dataQuality"] == "GOOD"


def test_queue_missing_zero_and_invalid_values_are_not_defaulted(isolated_repo):
    repo, _ = isolated_repo
    repo.create_run(
        "zeros",
        "completed",
        "topology",
        {"tasks": []},
        {
            "conveyorGraphNodes": 1,
            "conveyorUtilization": 0,
            "conveyorQueueDepth": 0,
            "bufferOccupancy": 0,
            "stationQueueDepth": 0,
        },
    )
    repo.create_run(
        "missing",
        "completed",
        "topology",
        {"tasks": []},
        {"conveyorGraphNodes": 1, "conveyorUtilization": 5},
    )
    repo.create_run(
        "invalid",
        "completed",
        "topology",
        {"tasks": []},
        {
            "conveyorGraphNodes": True,
            "conveyorUtilization": float("nan"),
            "conveyorQueueDepth": -1,
            "bufferOccupancy": False,
            "stationQueueDepth": "2",
        },
    )

    zero_metrics = {
        item["metricId"]: item
        for item in repo.get_conveyor_coordination_metrics("zeros")["metrics"]
    }
    for metric_id in (
        "CONVEYOR_WAIT_TASK_COUNT_CURRENT",
        "BUFFER_WAIT_TASK_COUNT_CURRENT",
        "STATION_QUEUE_TASK_COUNT_CURRENT",
    ):
        assert zero_metrics[metric_id]["value"] == 0
        assert zero_metrics[metric_id]["sampleCount"] == 1

    missing_metrics = {
        item["metricId"]: item
        for item in repo.get_conveyor_coordination_metrics("missing")["metrics"]
    }
    assert missing_metrics["CONVEYOR_WAIT_TASK_COUNT_CURRENT"]["value"] is None
    assert missing_metrics["CONVEYOR_WAIT_TASK_COUNT_CURRENT"]["reason"] == "FIELD_MISSING"

    invalid_result = repo.get_conveyor_coordination_metrics("invalid")
    invalid_metrics = {item["metricId"]: item for item in invalid_result["metrics"]}
    assert invalid_metrics["CONVEYOR_NODE_OCCUPANCY_CURRENT"]["value"] is None
    assert invalid_metrics["CONVEYOR_WAIT_TASK_COUNT_CURRENT"]["value"] is None
    assert invalid_metrics["BUFFER_WAIT_TASK_COUNT_CURRENT"]["value"] is None
    assert invalid_metrics["STATION_QUEUE_TASK_COUNT_CURRENT"]["value"] is None
    json.dumps(invalid_result, allow_nan=False)


def test_proxy_semantics_and_formal_metrics_remain_unavailable(isolated_repo):
    repo, _ = isolated_repo
    repo.create_run(
        "semantics",
        "completed",
        "topology",
        {"tasks": []},
        {
            "conveyorGraphNodes": 2,
            "conveyorUtilization": 50,
            "conveyorQueueDepth": 1,
            "bufferOccupancy": 1,
            "stationQueueDepth": 2,
            "activeRobots": 5,
            "throughput": 1000,
        },
    )
    result = repo.get_conveyor_coordination_metrics("semantics")
    metrics = {item["metricId"]: item for item in result["metrics"]}
    buffer_metric = metrics["BUFFER_WAIT_TASK_COUNT_CURRENT"]
    station_metric = metrics["STATION_QUEUE_TASK_COUNT_CURRENT"]
    assert "not_physical_buffer_utilization" in buffer_metric["semantics"]
    assert station_metric["displayName"] == "站台相关阶段任务数（代理）"
    assert "not_pure_waiting_queue" in station_metric["semantics"]
    for metric_id in (
        "DEVICE_UTILIZATION",
        "CONVEYOR_PHYSICAL_UTILIZATION",
        "BOTTLENECK_WAIT_DURATION",
        "THROUGHPUT_ORDER_LINES_PER_HOUR",
    ):
        assert metrics[metric_id]["value"] is None
        assert metrics[metric_id]["implementationStatus"] == "UNAVAILABLE"
        assert metrics[metric_id]["acceptanceEligible"] is False


def test_conveyor_coordination_route_is_run_scoped_read_only_and_preserves_existing_apis(
    isolated_repo,
):
    repo, database_path = isolated_repo
    for run_id, utilization in (("run-a", 10), ("run-b", 80)):
        repo.create_run(
            run_id,
            "completed",
            "topology",
            {"tasks": [], "robots": []},
            {
                "completedTasks": 0,
                "conveyorGraphNodes": 10,
                "conveyorUtilization": utilization,
                "conveyorQueueDepth": 0,
                "bufferOccupancy": 0,
                "stationQueueDepth": 0,
            },
        )
        repo.add_event(
            run_id,
            1,
            "metrics_snapshot",
            {"conveyorUtilization": 99, "conveyorGraphNodes": 10},
        )

    routes = importlib.import_module("app.api.routes")

    # Fixture writes are committed and their connections are closed, but WAL
    # checkpoint timing otherwise makes the main-file hash test-order dependent.
    _stabilize_temporary_wal(database_path)
    before_snapshot = _database_contract_snapshot()
    before_hash = _file_sha256(database_path)

    first = routes.get_simulation_conveyor_coordination_metrics("run-a")
    after_single_get = _database_contract_snapshot()
    assert after_single_get == before_snapshot

    second = routes.get_simulation_conveyor_coordination_metrics("run-a")
    run_b = routes.get_simulation_conveyor_coordination_metrics("run-b")
    assert first == second
    first_metrics = {item["metricId"]: item for item in first["metrics"]}
    run_b_metrics = {item["metricId"]: item for item in run_b["metrics"]}
    assert first_metrics["CONVEYOR_NODE_OCCUPANCY_CURRENT"]["value"] == 10
    assert run_b_metrics["CONVEYOR_NODE_OCCUPANCY_CURRENT"]["value"] == 80

    with pytest.raises(HTTPException) as error:
        routes.get_simulation_conveyor_coordination_metrics("not-present")
    assert error.value.status_code == 404

    assert routes.get_simulation_task_metric_summary("run-a")["runId"] == "run-a"
    assert routes.get_simulation_metrics_history_quality("run-a", max_points=20)["runId"] == "run-a"
    assert routes.get_simulation_assignment_contention_metrics("run-a")["runId"] == "run-a"
    assert routes.get_simulation_path_planning_metrics("run-a")["runId"] == "run-a"
    route_paths = {route.path for route in routes.router.routes}
    assert "/api/v1/simulations/{run_id}/metrics/conveyor-coordination" in route_paths

    after_combined_gets = _database_contract_snapshot()
    after_hash = _file_sha256(database_path)
    assert after_combined_gets == before_snapshot, {
        "beforeMainDbSha256": before_hash,
        "afterMainDbSha256": after_hash,
    }
    # Retain the byte hash as an auxiliary check after explicit WAL stability;
    # logical rows and schema above remain the primary read-only assertion.
    assert after_hash == before_hash

    with db.get_read_conn() as read_conn:
        assert read_conn.total_changes == 0
        query_only = read_conn.execute("PRAGMA query_only").fetchone()[0]
        assert query_only in (0, 1)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            read_conn.execute(
                """
                INSERT INTO simulation_events
                    (run_id, tick, event_type, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("run-a", 2, "write_probe", "{}", "test"),
            )
        assert read_conn.total_changes == 0

    # Prove that the logical snapshot guard catches a real committed write.
    write_conn = sqlite3.connect(database_path)
    try:
        write_conn.execute(
            """
            INSERT INTO simulation_events
                (run_id, tick, event_type, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("run-a", 2, "intentional_test_mutation", "{}", "test"),
        )
        write_conn.commit()
    finally:
        write_conn.close()
    mutation_snapshot = _database_contract_snapshot()
    assert mutation_snapshot["schema"] == before_snapshot["schema"]
    assert mutation_snapshot["tables"] != before_snapshot["tables"]
    assert len(mutation_snapshot["tables"]["simulation_events"]) == (
        len(before_snapshot["tables"]["simulation_events"]) + 1
    )

