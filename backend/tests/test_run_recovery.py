from __future__ import annotations

import asyncio
import sqlite3
from copy import deepcopy
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import db
from app.core.repository import Repository
from app.services.simulation import RunControlConflict, RunRuntime, SimulationService


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch) -> Repository:
    database = tmp_path / "run-recovery.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    return Repository()


def _state(run_id: str, status: str) -> dict[str, Any]:
    return {
        "id": run_id,
        "topologyId": "topology-recovery",
        "status": status,
        "stopReason": None,
        "tick": 0,
        "eventCount": 0,
        "orders": [{"id": "order-1", "status": "waiting_dispatch"}],
        "tasks": [{"id": "task-1", "status": "pending"}],
        "inventory": [{"cargoId": "cargo-1", "reservedBy": "task-1"}],
        "robots": [{"id": "ctu-1", "status": "idle", "currentTaskId": None}],
        "queues": {"conveyor": [], "station": [], "buffer": []},
        "createdAt": "2026-10-29T00:00:00+00:00",
        "updatedAt": "2026-10-29T00:00:00+00:00",
    }


def _persist(repo: Repository, run_id: str, status: str) -> dict[str, Any]:
    state = _state(run_id, status)
    repo.create_run(run_id, status, state["topologyId"], state, {"completedTasks": 0})
    return state


def _runtime(service: SimulationService, state: dict[str, Any]) -> RunRuntime:
    runtime = RunRuntime(
        run_id=state["id"],
        topology={},
        config={},
        state=deepcopy(state),
        metrics={"completedTasks": 0},
        paused=state["status"] == "paused",
        initial_state=deepcopy(state),
        initial_metrics={"completedTasks": 0},
    )
    service.runtimes[runtime.run_id] = runtime
    assert service._claim_run_control(runtime) is True
    return runtime


@pytest.mark.parametrize("status", ["running", "paused"])
def test_in_memory_active_run_stops_with_existing_semantics(
    isolated_repo: Repository,
    status: str,
) -> None:
    run_id = f"run-memory-{status}"
    original = _persist(isolated_repo, run_id, status)
    service = SimulationService(isolated_repo, instance_id="backend-a")
    runtime = _runtime(service, original)

    result = asyncio.run(service.stop(run_id))
    saved = isolated_repo.get_run(run_id)

    assert result == {"runId": run_id, "status": "stopped"}
    assert runtime.state["status"] == "stopped"
    assert saved is not None
    assert saved["status"] == "stopped"
    assert saved["payload"]["stopReason"] == "user"
    assert saved["payload"]["orders"] == original["orders"]
    assert saved["payload"]["tasks"] == original["tasks"]
    assert saved["payload"]["inventory"] == original["inventory"]
    assert saved["payload"]["robots"] == original["robots"]


def test_persisted_paused_run_stops_after_backend_restart_without_starting_loop(
    isolated_repo: Repository,
) -> None:
    run_id = "run-restarted-paused"
    original = _persist(isolated_repo, run_id, "paused")
    restarted = SimulationService(isolated_repo, instance_id="backend-after-restart")

    result = asyncio.run(restarted.stop(run_id))
    saved = isolated_repo.get_run(run_id)

    assert result["status"] == "stopped"
    assert restarted.runtimes == {}
    assert saved is not None
    assert saved["payload"]["tick"] == 0
    assert saved["payload"]["orders"] == original["orders"]
    assert saved["payload"]["tasks"] == original["tasks"]
    assert saved["payload"]["inventory"] == original["inventory"]


def test_missing_run_maps_to_http_404(isolated_repo: Repository, monkeypatch) -> None:
    from app.api import routes

    service = SimulationService(isolated_repo, instance_id="backend-http")
    monkeypatch.setattr(routes, "simulation_service", service)
    monkeypatch.setattr(routes, "repo", isolated_repo)
    test_app = FastAPI()
    test_app.include_router(routes.router)

    response = TestClient(test_app).post("/api/v1/simulations/run-missing/stop")

    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found"}


def test_repeated_stop_is_idempotent_and_does_not_repeat_side_effects(
    isolated_repo: Repository,
) -> None:
    run_id = "run-idempotent"
    original = _persist(isolated_repo, run_id, "paused")
    service = SimulationService(isolated_repo, instance_id="backend-idempotent")

    first = asyncio.run(service.stop(run_id))
    after_first = isolated_repo.get_run(run_id)
    second = asyncio.run(service.stop(run_id))
    after_second = isolated_repo.get_run(run_id)
    with db.get_conn() as conn:
        event_count = conn.execute(
            "SELECT COUNT(*) FROM simulation_events WHERE run_id = ? AND event_type = 'simulation_stopped'",
            (run_id,),
        ).fetchone()[0]

    assert first == second == {"runId": run_id, "status": "stopped"}
    assert after_first is not None and after_second is not None
    assert after_second["payload"] == after_first["payload"]
    assert after_second["payload"]["orders"] == original["orders"]
    assert after_second["payload"]["tasks"] == original["tasks"]
    assert after_second["payload"]["inventory"] == original["inventory"]
    assert after_second["payload"]["robots"] == original["robots"]
    assert event_count == 1


def test_completed_run_is_not_changed(isolated_repo: Repository) -> None:
    run_id = "run-completed"
    original = _persist(isolated_repo, run_id, "completed")
    service = SimulationService(isolated_repo, instance_id="backend-completed")

    with pytest.raises(RunControlConflict, match="Completed"):
        asyncio.run(service.stop(run_id))

    saved = isolated_repo.get_run(run_id)
    assert saved is not None
    assert saved["status"] == "completed"
    assert saved["payload"] == original


def test_stop_transaction_rolls_back_when_event_write_fails(
    isolated_repo: Repository,
) -> None:
    run_id = "run-rollback"
    original = _persist(isolated_repo, run_id, "paused")
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_stop_event
            BEFORE INSERT ON simulation_events
            WHEN NEW.event_type = 'simulation_stopped'
            BEGIN
              SELECT RAISE(ABORT, 'injected stop failure');
            END
            """
        )
    service = SimulationService(isolated_repo, instance_id="backend-rollback")

    with pytest.raises(sqlite3.IntegrityError, match="injected stop failure"):
        asyncio.run(service.stop(run_id))

    saved = isolated_repo.get_run(run_id)
    assert saved is not None
    assert saved["status"] == "paused"
    assert saved["payload"] == original


def test_live_owner_in_another_process_rejects_recovery_stop(
    isolated_repo: Repository,
) -> None:
    run_id = "run-owned-elsewhere"
    original = _persist(isolated_repo, run_id, "paused")
    assert isolated_repo.claim_run_control(run_id, "backend-owner", 60) is True
    competitor = SimulationService(isolated_repo, instance_id="backend-competitor")

    with pytest.raises(RunControlConflict, match="another live backend"):
        asyncio.run(competitor.stop(run_id))

    saved = isolated_repo.get_run(run_id)
    assert saved is not None
    assert saved["status"] == "paused"
    assert saved["payload"] == original


def test_persisted_running_run_without_owner_is_rejected(
    isolated_repo: Repository,
) -> None:
    run_id = "run-unowned-running"
    original = _persist(isolated_repo, run_id, "running")
    service = SimulationService(isolated_repo, instance_id="backend-recovery")

    with pytest.raises(RunControlConflict, match="without live control ownership"):
        asyncio.run(service.stop(run_id))

    saved = isolated_repo.get_run(run_id)
    assert saved is not None
    assert saved["status"] == "running"
    assert saved["payload"] == original


def test_run_list_and_state_read_the_same_terminal_status(
    isolated_repo: Repository,
) -> None:
    run_id = "run-list-consistency"
    _persist(isolated_repo, run_id, "paused")
    service = SimulationService(isolated_repo, instance_id="backend-list")

    asyncio.run(service.stop(run_id))

    state = service.get_state(run_id)
    listed = isolated_repo.list_runs("topology-recovery")
    assert state["status"] == "stopped"
    assert [(item["id"], item["status"]) for item in listed] == [(run_id, "stopped")]
