"""P2-2A runtime integration tests with pre-import SQLite isolation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = BACKEND_ROOT / "app_data" / "agv_platform.db"


def _file_fingerprint(path: Path) -> tuple[bool, int | None, int | None, str | None]:
    if not path.exists():
        return False, None, None, None
    return (
        True,
        path.stat().st_size,
        path.stat().st_mtime_ns,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


DEFAULT_DB_BEFORE = _file_fingerprint(DEFAULT_DB)
_IMPORT_ISOLATION = tempfile.TemporaryDirectory(prefix="p2-2a-import-")
IMPORT_DB = Path(_IMPORT_ISOLATION.name) / "runtime-integration.db"

from app.core import config as app_config  # noqa: E402

app_config.DB_PATH = IMPORT_DB

from app.core import db as app_db  # noqa: E402

app_db.DB_PATH = IMPORT_DB
assert app_config.DB_PATH == IMPORT_DB
assert app_db.DB_PATH == IMPORT_DB
assert app_db.DB_PATH.resolve() != DEFAULT_DB.resolve()

from app.core.repository import Repository  # noqa: E402
from app.services.deadlock_episodes import EpisodeStatus, ResourceType  # noqa: E402
from app.services.deadlock_runtime_adapter import (  # noqa: E402
    DIAGNOSTICS_NAMESPACE,
    DeadlockRuntimeObserver,
)
from app.services.simulation import RunRuntime, SimulationService  # noqa: E402


class MemoryRepository:
    def __init__(self) -> None:
        self.events: list[tuple[str, int, str, dict[str, Any]]] = []
        self.states: list[dict[str, Any]] = []

    def add_event(self, run_id: str, tick: int, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((run_id, tick, event_type, deepcopy(payload)))

    def update_run(
        self,
        _run_id: str,
        _status: str,
        payload: dict[str, Any],
        _metrics: dict[str, Any],
    ) -> dict[str, Any]:
        self.states.append(deepcopy(payload))
        return {}

    def create_run(
        self,
        _run_id: str,
        _status: str,
        _topology_id: str,
        payload: dict[str, Any],
        _metrics: dict[str, Any],
    ) -> dict[str, Any]:
        self.states.append(deepcopy(payload))
        return {}


class FlakyEventRepository(MemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self.fail_events = True

    def add_event(self, run_id: str, tick: int, event_type: str, payload: dict[str, Any]) -> None:
        if self.fail_events:
            raise OSError("isolated event store unavailable")
        super().add_event(run_id, tick, event_type, payload)


@pytest.fixture(scope="module", autouse=True)
def protect_default_database():
    yield
    assert _file_fingerprint(DEFAULT_DB) == DEFAULT_DB_BEFORE


@pytest.fixture
def isolated_repository(tmp_path, monkeypatch):
    database_path = tmp_path / "p2-2a.db"
    monkeypatch.setattr(app_config, "DB_PATH", database_path)
    monkeypatch.setattr(app_db, "DB_PATH", database_path)
    assert app_config.DB_PATH == database_path
    assert app_db.DB_PATH == database_path
    assert database_path.resolve() != DEFAULT_DB.resolve()
    app_db.init_db()
    return Repository(), database_path


def _topology(robot_count: int = 2) -> dict[str, Any]:
    cell_count = max(robot_count + 4, 8)
    return {
        "id": "topology-deadlock-runtime",
        "cells": [
            {
                "id": f"cell-r0-c{index}",
                "floor": "1F",
                "row": 0,
                "col": index,
                "walkable": True,
            }
            for index in range(cell_count)
        ],
        "nodes": [],
        "zones": [],
    }


def _runtime(
    run_id: str = "run-deadlock-runtime",
    *,
    robot_count: int = 2,
    repository: MemoryRepository | Repository | None = None,
) -> tuple[SimulationService, RunRuntime]:
    topology = _topology(robot_count)
    robots: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for index in range(robot_count):
        robot_id = f"R{index + 1}"
        current = f"cell-r0-c{index}"
        next_cell = f"cell-r0-c{(index + 1) % robot_count}" if robot_count > 1 else "cell-r0-c1"
        task_id = f"task-{robot_id}"
        robots.append(
            {
                "id": robot_id,
                "type": "ctu",
                "currentCellId": current,
                "currentNode": current,
                "currentFloor": "1F",
                "currentRow": 0,
                "currentCol": index,
                "currentLayer": 1,
                "lastCellId": None,
                "status": "moving" if robot_count > 1 else "idle",
                "path": [current, next_cell] if robot_count > 1 else [current],
                "pathLayers": [1, 1] if robot_count > 1 else [1],
                "currentTaskId": task_id if robot_count > 1 else None,
                "targetCellId": next_cell,
                "targetLayer": 1,
                "returningToStandby": False,
                "waitTicks": 0,
                "failed": False,
                "phaseLabel": "运输中",
                "battery": 100.0,
                "load": 0,
                "carrying": [],
                "capacity": 5,
            }
        )
        tasks.append(
            {
                "id": task_id,
                "status": "assigned",
                "phase": "delivery_to_port",
                "phaseLabel": "运输中",
                "remainingStageTicks": 0,
                "picked": 0,
                "history": [],
            }
        )
    state = {
        "id": run_id,
        "status": "running",
        "stopReason": None,
        "tick": 0,
        "eventCount": 0,
        "topologyId": topology["id"],
        "robots": robots,
        "tasks": tasks,
        "orders": [],
        "inventory": [{"id": "inventory-sentinel"}],
        "alerts": [],
        "queues": {"conveyor": [], "station": [], "buffer": []},
        "createdAt": "2026-11-11T00:00:00Z",
        "updatedAt": "2026-11-11T00:00:00Z",
    }
    runtime = RunRuntime(
        run_id=run_id,
        topology=deepcopy(topology),
        config={"durationTicks": 10, "maxDurationTicks": 20, "pathPolicy": {}},
        state=state,
        metrics={
            "throughput": 0,
            "completedTasks": 0,
            "pendingTasks": len(tasks),
            "activeRobots": 0,
            "blockedRobots": 0,
            "conveyorUtilization": 0.0,
            "deadlocksDetected": 0,
            "congestionEvents": 0,
            "urgentOrdersHandled": 0,
            "avgEtaTicks": 0.0,
            "avgCompletionTicks": 0.0,
            "stationQueueDepth": 0,
            "conveyorQueueDepth": 0,
            "bufferOccupancy": 0,
        },
    )
    runtime.deadlock_observer = DeadlockRuntimeObserver(run_id, 1, topology)
    runtime.state[DIAGNOSTICS_NAMESPACE] = runtime.deadlock_observer.diagnostics_payload()
    runtime.initial_state = deepcopy(runtime.state)
    runtime.initial_metrics = {}
    service = SimulationService(repository or MemoryRepository())  # type: ignore[arg-type]
    service.runtimes[run_id] = runtime
    return service, runtime


def _event_types(service: SimulationService) -> list[str]:
    repository = service.repo
    return [item[2] for item in getattr(repository, "events", [])]


def _drive_cycle_tick(service: SimulationService, runtime: RunRuntime, tick: int) -> None:
    runtime.state["tick"] = tick
    service._begin_deadlock_observation(runtime, tick)
    service._move_robots(runtime)
    service._advance_deadlock_observation(runtime, tick)


def _drive_evidence_cycle_tick(service: SimulationService, runtime: RunRuntime, tick: int) -> None:
    runtime.state["tick"] = tick
    service._begin_deadlock_observation(runtime, tick)
    first, second = runtime.state["robots"][:2]
    service._observe_robot_block(
        runtime,
        first,
        second["currentCellId"],
        second["id"],
        ResourceType.ROBOT,
        "NEXT_CELL_OCCUPIED",
    )
    service._observe_robot_block(
        runtime,
        second,
        first["currentCellId"],
        first["id"],
        ResourceType.ROBOT,
        "NEXT_CELL_OCCUPIED",
    )
    service._advance_deadlock_observation(runtime, tick)


def _drive_clear_tick(
    service: SimulationService,
    runtime: RunRuntime,
    tick: int,
    *,
    moved: bool,
    mitigation: str | None = None,
) -> None:
    runtime.state["tick"] = tick
    service._begin_deadlock_observation(runtime, tick)
    first = runtime.state["robots"][0]
    if mitigation:
        service._observe_deadlock_mitigation(runtime, first, mitigation)
    if moved:
        first["currentCellId"] = "cell-r0-c4"
        first["currentNode"] = "cell-r0-c4"
        first["currentCol"] = 4
    for robot in runtime.state["robots"]:
        robot["status"] = "idle"
        robot["path"] = [robot["currentCellId"]]
        robot["waitTicks"] = 0
    service._advance_deadlock_observation(runtime, tick)


def _business_projection(runtime: RunRuntime) -> dict[str, Any]:
    return {
        "robots": [
            {
                "id": robot["id"],
                "currentCellId": robot["currentCellId"],
                "path": deepcopy(robot.get("path")),
                "status": robot.get("status"),
                "waitTicks": robot.get("waitTicks"),
            }
            for robot in runtime.state["robots"]
        ],
        "tasks": [
            {"id": task["id"], "status": task.get("status"), "phase": task.get("phase")}
            for task in runtime.state["tasks"]
        ],
        "inventory": deepcopy(runtime.state["inventory"]),
        "stopReason": runtime.state.get("stopReason"),
    }


def _disable_tick_business(service: SimulationService, monkeypatch) -> None:
    for name in (
        "_inject_faults",
        "_inject_urgent_order",
        "_assign_tasks",
        "_move_robots",
        "_advance_picking_arm",
        "_advance_picking_arms",
        "_advance_detached_conveyor_tasks",
        "_advance_detached_picking_tasks",
        "_advance_feeding_parents",
        "_detect_contention",
        "_refresh_orders",
        "_refresh_metrics",
    ):
        monkeypatch.setattr(service, name, lambda *_args, **_kwargs: None)


def test_normal_tick_without_blocking_advances_observer_once(monkeypatch) -> None:
    service, runtime = _runtime(robot_count=1)
    _disable_tick_business(service, monkeypatch)
    service._step_runtime(runtime, persist=False)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["lastProcessedTick"] == 1
    assert diagnostics["blockedRobotTicks"] == 0
    assert diagnostics["persistentCycleCount"] == 0


def test_single_robot_block_is_counted_without_cycle() -> None:
    service, runtime = _runtime(robot_count=1)
    robot = runtime.state["robots"][0]
    runtime.state["tick"] = 1
    service._begin_deadlock_observation(runtime, 1)
    service._observe_robot_block(
        runtime,
        robot,
        "cell-r0-c1",
        None,
        ResourceType.UNKNOWN_BLOCKER,
        "UNKNOWN_BLOCKER",
    )
    service._advance_deadlock_observation(runtime, 1)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["blockedRobotTicks"] == 1
    assert diagnostics["suspectedCycleCount"] == 0


def test_two_robot_runtime_movement_creates_verified_cycle() -> None:
    service, runtime = _runtime(robot_count=2)
    _drive_cycle_tick(service, runtime, 1)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["suspectedCycleCount"] == 1
    assert diagnostics["activeEpisodes"][0]["participantRobotIds"] == ["R1", "R2"]
    assert _event_types(service).count("deadlock_episode_started") == 1


def test_three_robot_runtime_movement_creates_one_cycle() -> None:
    service, runtime = _runtime(robot_count=3)
    _drive_cycle_tick(service, runtime, 1)
    participants = runtime.state[DIAGNOSTICS_NAMESPACE]["activeEpisodes"][0]["participantRobotIds"]
    assert participants == ["R1", "R2", "R3"]


def test_transient_cycle_does_not_become_persistent() -> None:
    service, runtime = _runtime()
    _drive_cycle_tick(service, runtime, 1)
    _drive_clear_tick(service, runtime, 2, moved=True)
    _drive_clear_tick(service, runtime, 3, moved=True)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["persistentCycleCount"] == 0
    assert "deadlock_episode_persistent" not in _event_types(service)


def test_continuous_cycle_reaches_persistent_threshold() -> None:
    service, runtime = _runtime()
    for tick in range(1, 4):
        _drive_cycle_tick(service, runtime, tick)
    assert runtime.state[DIAGNOSTICS_NAMESPACE]["persistentCycleCount"] == 1
    assert _event_types(service).count("deadlock_episode_persistent") == 1


def test_real_yield_progress_resolves_episode() -> None:
    service, runtime = _runtime()
    for tick in range(1, 4):
        _drive_cycle_tick(service, runtime, tick)
    _drive_clear_tick(service, runtime, 4, moved=True, mitigation="YIELD")
    _drive_clear_tick(service, runtime, 5, moved=True)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["resolvedCycleCount"] == 1
    assert "deadlock_mitigation_observed" in _event_types(service)
    assert "deadlock_episode_resolved" in _event_types(service)


def test_successful_reroute_progress_resolves_episode() -> None:
    service, runtime = _runtime()
    for tick in range(1, 4):
        _drive_cycle_tick(service, runtime, tick)
    _drive_clear_tick(service, runtime, 4, moved=True, mitigation="REROUTE")
    _drive_clear_tick(service, runtime, 5, moved=True)
    assert runtime.state[DIAGNOSTICS_NAMESPACE]["resolvedCycleCount"] == 1


def test_mitigation_without_progress_is_not_resolution() -> None:
    service, runtime = _runtime()
    for tick in range(1, 4):
        _drive_cycle_tick(service, runtime, tick)
    _drive_clear_tick(service, runtime, 4, moved=False, mitigation="YIELD")
    _drive_clear_tick(service, runtime, 5, moved=False)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["resolvedCycleCount"] == 0
    assert diagnostics["unverifiedCycleCount"] == 1


def test_unknown_blocker_does_not_create_vehicle_cycle() -> None:
    service, runtime = _runtime()
    runtime.state["tick"] = 1
    service._begin_deadlock_observation(runtime, 1)
    for robot in runtime.state["robots"]:
        service._observe_robot_block(
            runtime,
            robot,
            "cell-r0-c7",
            None,
            ResourceType.UNKNOWN_BLOCKER,
            "UNKNOWN_BLOCKER",
        )
    service._advance_deadlock_observation(runtime, 1)
    assert runtime.state[DIAGNOSTICS_NAMESPACE]["suspectedCycleCount"] == 0


def test_resource_block_does_not_create_vehicle_cycle() -> None:
    service, runtime = _runtime()
    runtime.state["tick"] = 1
    service._begin_deadlock_observation(runtime, 1)
    for robot in runtime.state["robots"]:
        service._observe_robot_block(
            runtime,
            robot,
            robot["path"][1],
            None,
            ResourceType.RESERVATION,
            "TARGET_RESERVED",
        )
    service._advance_deadlock_observation(runtime, 1)
    observations = runtime.state[DIAGNOSTICS_NAMESPACE]["lastBlockedObservations"]
    assert {item["resourceType"] for item in observations} == {"RESERVATION"}
    assert runtime.state[DIAGNOSTICS_NAMESPACE]["suspectedCycleCount"] == 0


def test_same_tick_processing_is_idempotent() -> None:
    service, runtime = _runtime()
    _drive_cycle_tick(service, runtime, 1)
    before = deepcopy(runtime.state[DIAGNOSTICS_NAMESPACE])
    event_count = len(getattr(service.repo, "events"))
    service._advance_deadlock_observation(runtime, 1)
    after = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert after["blockedRobotTicks"] == before["blockedRobotTicks"]
    assert len(getattr(service.repo, "events")) == event_count


def test_missing_tick_is_not_interpolated() -> None:
    service, runtime = _runtime()
    _drive_cycle_tick(service, runtime, 1)
    _drive_cycle_tick(service, runtime, 3)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["observationCompleteness"] == "INCOMPLETE"
    assert diagnostics["blockedRobotTicks"] == 4
    assert diagnostics["unverifiedCycleCount"] == 1


def test_pause_without_tick_does_not_accumulate() -> None:
    service, runtime = _runtime()
    _drive_cycle_tick(service, runtime, 1)
    runtime.paused = True
    runtime.state["status"] = "paused"
    before = deepcopy(runtime.state[DIAGNOSTICS_NAMESPACE])
    assert runtime.state[DIAGNOSTICS_NAMESPACE] == before
    assert before["lastProcessedTick"] == 1


def test_empty_run_normal_completion_seals_observer() -> None:
    service, runtime = _runtime(robot_count=1)
    runtime.state["tasks"] = []
    runtime.state["orders"] = []
    asyncio.run(service._loop(runtime))
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert runtime.state["status"] == "completed"
    assert diagnostics["ended"] is True
    assert diagnostics["endReason"] == "completed"


def test_manual_stop_seals_active_episode_once() -> None:
    service, runtime = _runtime()
    for tick in range(1, 4):
        _drive_cycle_tick(service, runtime, tick)
    service.stop(runtime.run_id)
    service.stop(runtime.run_id)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["unresolvedCycleCount"] == 1
    assert _event_types(service).count("deadlock_episode_unresolved") == 1


def test_safety_limit_seals_observer(monkeypatch) -> None:
    service, runtime = _runtime(robot_count=1)
    runtime.config.update(durationTicks=1, maxDurationTicks=1)
    _disable_tick_business(service, monkeypatch)
    monkeypatch.setattr("app.services.simulation.MIN_SAFETY_TICKS", 1)
    monkeypatch.setattr(service, "_broadcast_tick", lambda _runtime: asyncio.sleep(0))
    asyncio.run(service._loop(runtime))
    assert runtime.state["stopReason"] == "safety_limit"
    assert runtime.state[DIAGNOSTICS_NAMESPACE]["ended"] is True


def test_runtime_error_seals_observer_without_losing_episode() -> None:
    service, runtime = _runtime()
    for tick in range(1, 4):
        _drive_cycle_tick(service, runtime, tick)
    asyncio.run(service._handle_loop_failure(runtime, RuntimeError("isolated failure")))
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert runtime.state["stopReason"] == "runtime_error"
    assert diagnostics["unresolvedCycleCount"] == 1
    assert "deadlock_episode_unresolved" in _event_types(service)


def test_reset_creates_new_generation(monkeypatch) -> None:
    service, runtime = _runtime()
    _drive_cycle_tick(service, runtime, 1)

    async def scenario() -> None:
        await service.reset(runtime.run_id)
        assert runtime.state[DIAGNOSTICS_NAMESPACE]["generation"] == 2
        assert runtime.state[DIAGNOSTICS_NAMESPACE]["runInstanceId"].endswith("generation-2")
        assert runtime.task_handle is not None
        runtime.task_handle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runtime.task_handle

    asyncio.run(scenario())
    assert any(
        event[3].get("generation") == 1
        for event in getattr(service.repo, "events")
        if event[2] == "deadlock_episode_unresolved"
    )


def test_failed_event_insert_is_retried_without_duplicate() -> None:
    repository = FlakyEventRepository()
    service, runtime = _runtime(repository=repository)
    _drive_cycle_tick(service, runtime, 1)
    assert runtime.state[DIAGNOSTICS_NAMESPACE]["eventPersistence"]["pendingTransitionCount"] == 1
    repository.fail_events = False
    _drive_cycle_tick(service, runtime, 2)
    assert _event_types(service).count("deadlock_episode_started") == 1
    assert runtime.state[DIAGNOSTICS_NAMESPACE]["eventPersistence"]["pendingTransitionCount"] == 0


def test_legacy_runtime_starts_incomplete_without_backfill() -> None:
    service, runtime = _runtime()
    runtime.deadlock_observer = None
    runtime.state.pop(DIAGNOSTICS_NAMESPACE, None)
    runtime.state["tick"] = 10
    service._begin_deadlock_observation(runtime, 11)
    service._advance_deadlock_observation(runtime, 11)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["observationCompleteness"] == "INCOMPLETE"
    assert diagnostics["lastProcessedTick"] == 11
    assert diagnostics["recovery"]["historicalBackfill"] is False


def test_process_restart_closes_saved_active_episode_unverified() -> None:
    service, runtime = _runtime()
    _drive_cycle_tick(service, runtime, 1)
    saved = deepcopy(runtime.state[DIAGNOSTICS_NAMESPACE])
    service.repo.events.clear()  # type: ignore[attr-defined]
    runtime.deadlock_observer = None
    runtime.state[DIAGNOSTICS_NAMESPACE] = saved
    runtime.state["tick"] = 1
    service._begin_deadlock_observation(runtime, 2)
    for robot in runtime.state["robots"]:
        robot["status"] = "idle"
        robot["path"] = [robot["currentCellId"]]
    service._advance_deadlock_observation(runtime, 2)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["observationCompleteness"] == "INCOMPLETE"
    assert diagnostics["unverifiedCycleCount"] == 1
    assert diagnostics["activeEpisodes"] == []
    assert diagnostics["recentClosedEpisodes"][0]["status"] == "ENDED_UNVERIFIED"
    assert _event_types(service).count("deadlock_episode_unverified") == 1
    assert _event_types(service).count("deadlock_episode_started") == 0


def test_episode_event_is_readable_from_isolated_sqlite(isolated_repository) -> None:
    repository, database_path = isolated_repository
    service, runtime = _runtime("run-sqlite-event", repository=repository)
    repository.create_run(
        runtime.run_id,
        "running",
        runtime.state["topologyId"],
        runtime.state,
        runtime.metrics,
    )
    _drive_cycle_tick(service, runtime, 1)
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT event_type, payload FROM simulation_events WHERE run_id = ?",
            (runtime.run_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "deadlock_episode_started"
    payload = json.loads(row[1])
    assert payload["contractVersion"] == "deadlock-episodes-v1"
    assert payload["generation"] == 1


def test_diagnostics_state_is_json_serializable() -> None:
    service, runtime = _runtime()
    for tick in range(1, 4):
        _drive_cycle_tick(service, runtime, tick)
    encoded = json.dumps(runtime.state[DIAGNOSTICS_NAMESPACE], ensure_ascii=False)
    assert "deadlock-episodes-v1" in encoded


def test_observer_does_not_change_business_projection(monkeypatch) -> None:
    observed_service, observed_runtime = _runtime("run-observed")
    control_service, control_runtime = _runtime("run-control")
    observed_runtime.state["tick"] = 1
    control_runtime.state["tick"] = 1
    observed_service._begin_deadlock_observation(observed_runtime, 1)
    monkeypatch.setattr(control_service, "_observe_robot_block", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(control_service, "_observe_deadlock_mitigation", lambda *_args, **_kwargs: None)
    observed_service._move_robots(observed_runtime)
    control_service._move_robots(control_runtime)
    observed_service._advance_deadlock_observation(observed_runtime, 1)
    assert _business_projection(observed_runtime) == _business_projection(control_runtime)


def test_legacy_deadlock_alert_remains_separate() -> None:
    service, runtime = _runtime()
    for robot in runtime.state["robots"]:
        robot["status"] = "blocked"
        robot["waitTicks"] = 3
    runtime.state["tick"] = 3
    service._detect_contention(runtime)
    assert "deadlock_detected" in _event_types(service)
    assert "deadlock_episode_started" not in _event_types(service)


def test_detection_failure_is_reported_without_raising(monkeypatch) -> None:
    service, runtime = _runtime(robot_count=1)
    observer = runtime.deadlock_observer
    assert observer is not None
    monkeypatch.setattr(observer, "process_tick", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad evidence")))
    runtime.state["tick"] = 1
    service._begin_deadlock_observation(runtime, 1)
    service._advance_deadlock_observation(runtime, 1)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["observationCompleteness"] == "INCOMPLETE"
    assert diagnostics["diagnosticFailures"][0]["type"] == "ValueError"
    assert "deadlock_diagnostics_failed" in _event_types(service)


def test_lifecycle_events_are_not_written_every_tick() -> None:
    service, runtime = _runtime()
    for tick in range(1, 11):
        _drive_evidence_cycle_tick(service, runtime, tick)
    lifecycle = [
        event_type for event_type in _event_types(service)
        if event_type.startswith("deadlock_episode_")
    ]
    assert lifecycle.count("deadlock_episode_started") == 1
    assert lifecycle.count("deadlock_episode_persistent") == 1
    assert len(lifecycle) == 2


def test_bounded_diagnostics_and_performance_fields() -> None:
    service, runtime = _runtime()
    tick = 0
    for _ in range(55):
        tick += 1
        _drive_cycle_tick(service, runtime, tick)
        tick += 1
        _drive_clear_tick(service, runtime, tick, moved=True)
        tick += 1
        _drive_clear_tick(service, runtime, tick, moved=True)
        runtime.state["robots"][0]["currentCellId"] = "cell-r0-c0"
        runtime.state["robots"][0]["path"] = ["cell-r0-c0", "cell-r0-c1"]
        runtime.state["robots"][1]["currentCellId"] = "cell-r0-c1"
        runtime.state["robots"][1]["path"] = ["cell-r0-c1", "cell-r0-c0"]
        for robot in runtime.state["robots"]:
            robot["status"] = "moving"
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert len(diagnostics["recentClosedEpisodes"]) == 50
    assert diagnostics["stateStoreBytes"] < 500_000
    assert diagnostics["performance"]["processedTicks"] == tick
    assert diagnostics["performance"]["maxDetectionMs"] >= 0


def test_default_database_remains_outside_test_database() -> None:
    assert app_db.DB_PATH.resolve() != DEFAULT_DB.resolve()
    assert _file_fingerprint(DEFAULT_DB) == DEFAULT_DB_BEFORE
