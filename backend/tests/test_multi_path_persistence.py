from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import path_demo as path_demo_api
from app.core.repository import EvaluationSnapshotConflictError, Repository
from app.services.multi_path_evaluation import evaluate_multi_path_algorithms


@pytest.fixture
def isolated_db_path():
    path = Path(__file__).parent / f".multi-path-evaluation-{uuid4().hex}.db"
    yield path
    for candidate in (path, Path(f"{path}-shm"), Path(f"{path}-wal")):
        candidate.unlink(missing_ok=True)


def topology() -> dict:
    return {
        "id": "persist-grid",
        "name": "Persistent Grid",
        "meta": {"version": "persist-v1"},
        "cells": [
            {
                "id": f"cell-r{row}-c{col}",
                "floor": "1F",
                "row": row,
                "col": col,
                "type": "road",
                "walkable": True,
            }
            for row in range(6)
            for col in range(8)
        ],
    }


def vehicles(count: int = 2) -> list[dict]:
    colors = ["#22d3ee", "#fb923c", "#a855f7", "#22c55e", "#ef4444", "#eab308"]
    return [
        {
            "vehicle_id": f"CTU-{index + 1:02d}",
            "vehicle_type": "ctu",
            "start_cell_id": f"cell-r{index}-c0",
            "goal_cell_id": f"cell-r{index}-c5",
            "priority": index + 1,
            "display_color": colors[index],
        }
        for index in range(count)
    ]


def planning_config() -> dict:
    return {
        "time_step_seconds": 0.25,
        "speed_cells_per_second": 4.0,
        "max_time_steps": 512,
        "max_expanded_states": 200_000,
        "vehicle_width_cells": 0.58,
        "vehicle_length_cells": 0.72,
        "safety_margin_cells": 0.05,
    }


def evaluation(vehicle_count: int = 2) -> dict:
    payload = topology()
    return evaluate_multi_path_algorithms(
        topology=payload,
        topology_id=payload["id"],
        topology_name=payload["name"],
        topology_version=payload["meta"]["version"],
        mode="custom",
        scenario_id=None,
        vehicles=vehicles(vehicle_count),
        planning_config=planning_config(),
    )


def client_for(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(path_demo_api, "repo", repo)
    app = FastAPI()
    app.include_router(path_demo_api.router, prefix="/api/v1")
    return TestClient(app)


def test_repository_snapshot_is_immutable_idempotent_and_survives_reopen(isolated_db_path):
    repo = Repository(isolated_db_path)
    snapshot = evaluation()
    original = deepcopy(snapshot)

    repo.save_multi_path_evaluation(snapshot)
    repo.save_multi_path_evaluation(deepcopy(snapshot))
    snapshot["input_snapshot"]["vehicles"][0]["start_cell_id"] = "mutated-after-save"

    reopened = Repository(isolated_db_path)
    stored = reopened.get_multi_path_evaluation(original["experiment_id"])
    assert stored == original
    assert reopened.list_multi_path_evaluations()["total"] == 1

    conflicting = deepcopy(original)
    conflicting["input_snapshot"]["topology_version"] = "contradictory"
    with pytest.raises(EvaluationSnapshotConflictError):
        reopened.save_multi_path_evaluation(conflicting)


@pytest.mark.parametrize("vehicle_count", [1, 6])
def test_persistence_supports_the_full_legal_vehicle_range(isolated_db_path, vehicle_count: int):
    repo = Repository(isolated_db_path)
    snapshot = evaluation(vehicle_count)
    repo.save_multi_path_evaluation(snapshot)
    listing = repo.list_multi_path_evaluations()
    assert listing["items"][0]["vehicle_count"] == vehicle_count
    assert len(repo.get_multi_path_evaluation(snapshot["experiment_id"])["input_snapshot"]["vehicles"]) == vehicle_count


def test_evaluate_endpoint_persists_lists_filters_reads_and_exports(isolated_db_path, monkeypatch: pytest.MonkeyPatch):
    repo = Repository(isolated_db_path)
    payload = topology()
    repo.upsert_topology(payload["id"], payload["name"], payload)
    client = client_for(repo, monkeypatch)
    request = {
        "topology_id": payload["id"],
        "mode": "custom",
        "vehicles": vehicles(),
        "planning_config": planning_config(),
    }

    first = client.post("/api/v1/path-demo/evaluate-multi", json=request)
    second = client.post("/api/v1/path-demo/evaluate-multi", json=request)
    assert first.status_code == second.status_code == 200
    assert first.json()["experiment_id"] != second.json()["experiment_id"]
    assert first.json()["status"] == "success"

    experiment_id = first.json()["experiment_id"]
    detail = client.get(f"/api/v1/path-demo/evaluations/{experiment_id}")
    assert detail.status_code == 200
    assert detail.json() == first.json()
    assert all(vehicle["steps"] for result in detail.json()["results"] for vehicle in result["plan"]["vehicles"])

    listing = client.get(
        "/api/v1/path-demo/evaluations",
        params={"limit": 1, "offset": 0, "topology_id": payload["id"], "status": "success"},
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 2
    assert len(listing.json()["items"]) == 1
    assert listing.json()["items"][0]["vehicle_count"] == 2
    assert "plan" not in listing.json()["items"][0]["results"][0]
    next_page = client.get(
        "/api/v1/path-demo/evaluations",
        params={"limit": 1, "offset": 1, "topology_id": payload["id"]},
    )
    assert next_page.status_code == 200
    assert next_page.json()["items"][0]["experiment_id"] != listing.json()["items"][0]["experiment_id"]

    exported = client.get(f"/api/v1/path-demo/evaluations/{experiment_id}/export")
    assert exported.status_code == 200
    assert exported.json()["evaluation"] == first.json()
    assert "planning_time_ms" in exported.json()["unit_definitions"]
    assert "not a real equipment run" in exported.json()["disclaimer"]
    assert client.get("/api/v1/path-demo/evaluations/missing").status_code == 404


def test_failed_result_is_persisted_without_fake_metrics(isolated_db_path):
    repo = Repository(isolated_db_path)
    snapshot = evaluation()
    failed = snapshot["results"][1]
    failed.update({"planning_status": "failed", "error_reason": "search boundary reached", "metrics": None, "plan": None})
    snapshot["status"] = "partial_failure"

    repo.save_multi_path_evaluation(snapshot)
    stored = repo.get_multi_path_evaluation(snapshot["experiment_id"])
    assert stored is not None
    assert stored["status"] == "partial_failure"
    assert stored["results"][1]["metrics"] is None
    assert stored["results"][1]["error_reason"] == "search boundary reached"


def test_schema_init_is_repeatable_and_does_not_replace_simulation_tables(isolated_db_path):
    repo = Repository(isolated_db_path)
    repo.create_run("run-1", "completed", "topology-1", "scenario-1", {"tick": 1}, {"throughput": 1})
    repo.save_multi_path_evaluation(evaluation())

    reopened = Repository(repo.db_path)
    assert reopened.list_runs()[0]["id"] == "run-1"
    assert reopened.list_multi_path_evaluations()["total"] == 1


def test_corrupted_stored_snapshot_returns_an_explicit_error(isolated_db_path, monkeypatch: pytest.MonkeyPatch):
    repo = Repository(isolated_db_path)
    snapshot = evaluation()
    repo.save_multi_path_evaluation(snapshot)
    conn = sqlite3.connect(repo.db_path)
    try:
        conn.execute(
            "UPDATE multi_path_evaluations SET result_snapshot = ? WHERE experiment_id = ?",
            ("{not-json", snapshot["experiment_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    client = client_for(repo, monkeypatch)
    response = client.get(f"/api/v1/path-demo/evaluations/{snapshot['experiment_id']}")
    assert response.status_code == 500
    assert response.json()["detail"] == "Stored multi-path evaluation is corrupted"


def test_persistence_failure_never_returns_a_fake_success(isolated_db_path, monkeypatch: pytest.MonkeyPatch):
    repo = Repository(isolated_db_path)
    payload = topology()
    repo.upsert_topology(payload["id"], payload["name"], payload)

    def fail_to_save(_evaluation):
        raise sqlite3.OperationalError("disk unavailable")

    monkeypatch.setattr(repo, "save_multi_path_evaluation", fail_to_save)
    client = client_for(repo, monkeypatch)
    response = client.post(
        "/api/v1/path-demo/evaluate-multi",
        json={
            "topology_id": payload["id"],
            "mode": "custom",
            "vehicles": vehicles(),
            "planning_config": planning_config(),
        },
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "Multi-path evaluation could not be persisted"
