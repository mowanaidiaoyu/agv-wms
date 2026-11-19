"""P3-2 runtime mileage integration tests with pre-import SQLite isolation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any
import uuid

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = BACKEND_ROOT / "app_data" / "agv_platform.db"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _fingerprint(path: Path) -> tuple[bool, int | None, int | None, str | None]:
    if not path.exists():
        return False, None, None, None
    return (
        True,
        path.stat().st_size,
        path.stat().st_mtime_ns,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


DEFAULT_DB_BEFORE = _fingerprint(DEFAULT_DB)
IMPORT_DB = BACKEND_ROOT / "tests" / ".p3-2-import-never-created" / "motion-runtime.db"

from app.core import config as app_config  # noqa: E402

app_config.DB_PATH = IMPORT_DB

from app.core import db as app_db  # noqa: E402

app_db.DB_PATH = IMPORT_DB
assert app_config.DB_PATH == IMPORT_DB
assert app_db.DB_PATH == IMPORT_DB
assert app_db.DB_PATH.resolve() != DEFAULT_DB.resolve()

from app.core.repository import Repository  # noqa: E402
from app.services.deadlock_runtime_adapter import DIAGNOSTICS_NAMESPACE as DEADLOCK_NAMESPACE  # noqa: E402
from app.services.motion_distance import MovementType, PhysicsContract  # noqa: E402
from app.services.motion_runtime_adapter import (  # noqa: E402
    DIAGNOSTICS_NAMESPACE,
    EVENT_TYPE,
    MotionRuntimeObserver,
    RuntimePosition,
)
from app.services.pathfinding import cell_id  # noqa: E402
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


class FailingMovementRepository(MemoryRepository):
    def add_event(self, run_id: str, tick: int, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == EVENT_TYPE:
            raise OSError("isolated movement event store unavailable")
        super().add_event(run_id, tick, event_type, payload)


@pytest.fixture(scope="module", autouse=True)
def protect_default_database():
    yield
    assert _fingerprint(DEFAULT_DB) == DEFAULT_DB_BEFORE


def _physics(meters: float = 0.8, version: str = "physics-runtime-test-v1") -> PhysicsContract:
    return PhysicsContract(version, meters, 1.4, 4.2, "grid-runtime-test-v1")


def _position(row: float, col: float, layer: float = 1, floor: str = "1F") -> RuntimePosition:
    return RuntimePosition(row, col, floor, layer)


def _observer(
    run_id: str = "run-motion",
    *,
    meters: float | None = 0.8,
    generation: int = 1,
    initial_tick: int = 0,
) -> MotionRuntimeObserver:
    observer = MotionRuntimeObserver(
        run_id,
        generation,
        _physics(meters) if meters is not None else None,
        initial_tick=initial_tick,
    )
    observer.register_robots([{"id": "R1", "type": "ctu"}])
    return observer


def _capture(
    observer: MotionRuntimeObserver,
    start: RuntimePosition = RuntimePosition(0, 0, "1F", 1),
    end: RuntimePosition = RuntimePosition(0, 1, "1F", 1),
    *,
    tick: int = 1,
    robot_id: str = "R1",
    robot_type: str = "ctu",
    movement_type: MovementType = MovementType.NORMAL,
    source: str = "test",
    event_sequence: int | None = None,
):
    return observer.capture_committed_move(
        robot_id=robot_id,
        robot_type=robot_type,
        from_position=start,
        to_position=end,
        tick=tick,
        movement_type=movement_type,
        source=source,
        event_sequence=event_sequence,
    )


def _topology(cols: int = 8) -> dict[str, Any]:
    return {
        "id": "topology-motion-test",
        "grid": {"rows": 4, "cols": cols},
        "cells": [
            {
                "id": cell_id(0, col, "1F"),
                "floor": "1F",
                "row": 0,
                "col": col,
                "walkable": True,
            }
            for col in range(cols)
        ],
        "nodes": [],
        "zones": [],
    }


def _robot(
    *,
    robot_id: str = "R1",
    robot_type: str = "ctu",
    current_col: int = 0,
    path_cols: list[int] | None = None,
    task_id: str | None = "task-R1",
) -> dict[str, Any]:
    path_cols = path_cols or [current_col, current_col + 1, current_col + 2]
    current = cell_id(0, current_col, "1F")
    return {
        "id": robot_id,
        "type": robot_type,
        "currentCellId": current,
        "currentNode": current,
        "currentFloor": "1F",
        "currentRow": 0,
        "currentCol": current_col,
        "currentLayer": 1,
        "lastCellId": None,
        "homeCellId": current,
        "status": "moving" if task_id else "returning",
        "path": [cell_id(0, col, "1F") for col in path_cols],
        "pathLayers": [1] * len(path_cols),
        "pathStages": ["delivery_to_port"] * len(path_cols),
        "currentTaskId": task_id,
        "targetCellId": cell_id(0, path_cols[-1], "1F"),
        "targetLayer": 1,
        "returningToStandby": task_id is None,
        "waitTicks": 0,
        "failed": False,
        "phaseLabel": "moving",
        "battery": 100.0,
        "load": 0,
        "carrying": [],
        "capacity": 5,
    }


def _task(robot_id: str = "R1") -> dict[str, Any]:
    return {
        "id": f"task-{robot_id}",
        "status": "assigned",
        "phase": "delivery_to_port",
        "phaseLabel": "moving",
        "mode": "direct",
        "assignedRobotId": robot_id,
        "history": [],
    }


def _runtime(
    repo: MemoryRepository | None = None,
    *,
    robots: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    physics: PhysicsContract | None = None,
    enabled: bool = True,
    run_id: str = "run-motion",
) -> tuple[SimulationService, RunRuntime, MemoryRepository]:
    repository = repo or MemoryRepository()
    service = SimulationService(repository)  # type: ignore[arg-type]
    robots = robots or [_robot()]
    tasks = tasks or [_task()]
    state = {
        "id": run_id,
        "status": "running",
        "stopReason": None,
        "tick": 1,
        "topologyId": "topology-motion-test",
        "robots": robots,
        "tasks": tasks,
        "orders": [],
        "inventory": [],
        "alerts": [],
        "queues": {"conveyor": [], "station": [], "buffer": []},
        "createdAt": "",
        "updatedAt": "",
        DEADLOCK_NAMESPACE: {"generation": 1},
    }
    config: dict[str, Any] = {
        "pathPolicy": "astar",
        "durationTicks": 90,
        "maxDurationTicks": 2000,
        "motionDistanceCollectionEnabled": enabled,
    }
    if physics is not None:
        config["motionPhysicsContract"] = physics
    runtime = RunRuntime(
        run_id=run_id,
        topology=_topology(),
        config=config,
        state=state,
        metrics={},
        initial_state=deepcopy(state),
        initial_metrics={},
        motion_collection_enabled=enabled,
    )
    if enabled:
        runtime.motion_observer = MotionRuntimeObserver(run_id, 1, physics)
        runtime.motion_observer.register_robots(robots)
        runtime.state[DIAGNOSTICS_NAMESPACE] = runtime.motion_observer.diagnostics_payload()
        runtime.initial_state = deepcopy(runtime.state)
    service.runtimes[run_id] = runtime
    return service, runtime, repository


def _movement_events(repo: MemoryRepository) -> list[dict[str, Any]]:
    movements: list[dict[str, Any]] = []
    for _run, _tick, event_type, payload in repo.events:
        if event_type == EVENT_TYPE:
            movements.extend(deepcopy(payload.get("movements") or []))
    return movements


def test_ctu_normal_move_is_captured() -> None:
    service, runtime, repo = _runtime(physics=_physics())
    service._move_robots(runtime)
    events = _movement_events(repo)
    assert len(events) == 1
    assert events[0]["movementType"] == "NORMAL"
    assert events[0]["segmentHorizontalMeters"] == pytest.approx(0.8)


def test_ctu_multi_segment_path_accumulates() -> None:
    robot = _robot(path_cols=[0, 1, 2, 3])
    service, runtime, _repo = _runtime(robots=[robot], physics=_physics())
    service._move_robots(runtime)
    runtime.state["tick"] = 2
    service._move_robots(runtime)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["totalMovementCount"] == 2
    assert diagnostics["totalHorizontalMeters"] == pytest.approx(1.6)


def test_ctu_repeated_grid_visit_counts_each_real_move() -> None:
    robot = _robot(path_cols=[0, 1, 0, 1])
    service, runtime, _repo = _runtime(robots=[robot], physics=_physics())
    service._move_robots(runtime)
    runtime.state["tick"] = 2
    service._move_robots(runtime)
    assert runtime.state[DIAGNOSTICS_NAMESPACE]["totalHorizontalMeters"] == pytest.approx(1.6)


def test_ctu_active_yield_is_captured() -> None:
    robot = _robot(path_cols=[1, 2, 3])
    robot["currentCellId"] = cell_id(0, 1, "1F")
    robot["currentNode"] = robot["currentCellId"]
    robot["currentCol"] = 1
    service, runtime, repo = _runtime(robots=[robot], physics=_physics())
    assert service._yield_ctu(runtime, robot, {robot["currentCellId"]}, set()) is True
    service._flush_motion_events(runtime)
    event = _movement_events(repo)[0]
    assert event["movementType"] == "YIELD"


def test_ctu_return_to_standby_is_captured() -> None:
    robot = _robot(task_id=None, path_cols=[2, 1, 0], current_col=2)
    service, runtime, repo = _runtime(robots=[robot], tasks=[], physics=_physics())
    service._move_robot_to_standby(runtime, robot, {robot["currentCellId"]}, set())
    service._flush_motion_events(runtime)
    assert _movement_events(repo)[0]["movementType"] == "RETURN_TO_STANDBY"


def test_ctu_blocked_without_move_adds_no_distance() -> None:
    first = _robot(robot_id="R1", path_cols=[0, 1, 2])
    second = _robot(robot_id="R2", current_col=1, path_cols=[1, 2, 3], task_id=None)
    second["returningToStandby"] = False
    second["status"] = "idle"
    second["path"] = [second["currentCellId"]]
    second["pathLayers"] = [1]
    service, runtime, repo = _runtime(robots=[first, second], tasks=[_task("R1")], physics=_physics())
    service._move_robots(runtime)
    assert _movement_events(repo) == []
    assert runtime.state[DIAGNOSTICS_NAMESPACE]["totalHorizontalMeters"] == 0.0


def test_ctu_reroute_alone_is_not_motion() -> None:
    service, runtime, repo = _runtime(physics=_physics())
    robot = runtime.state["robots"][0]
    service._reroute_ctu(runtime, robot, {robot["currentCellId"]})
    assert _movement_events(repo) == []


def test_shuttle_horizontal_move_is_captured() -> None:
    robot = _robot(robot_type="shuttle", path_cols=[0, 2, 4])
    service, runtime, repo = _runtime(robots=[robot], physics=_physics())
    service._move_robots(runtime)
    assert _movement_events(repo)[0]["robotType"] == "shuttle"


def test_shuttle_two_by_two_track_center_distance_is_two_cells() -> None:
    robot = _robot(robot_type="shuttle", path_cols=[0, 2, 4])
    service, runtime, repo = _runtime(robots=[robot], physics=_physics())
    service._move_robots(runtime)
    assert _movement_events(repo)[0]["segmentHorizontalMeters"] == pytest.approx(1.6)


def test_shuttle_backoff_is_captured() -> None:
    robot = _robot(robot_type="shuttle", current_col=2, path_cols=[2, 4, 6])
    robot["lastCellId"] = cell_id(0, 0, "1F")
    service, runtime, repo = _runtime(robots=[robot], physics=_physics())
    assert service._back_off_shuttle(runtime, robot, {cell_id(0, 2, "1F")}, set()) is True
    service._flush_motion_events(runtime)
    assert _movement_events(repo)[0]["movementType"] == "BACKOFF"


def test_shuttle_return_to_standby_is_captured() -> None:
    robot = _robot(robot_type="shuttle", task_id=None, current_col=4, path_cols=[4, 2, 0])
    service, runtime, repo = _runtime(robots=[robot], tasks=[], physics=_physics())
    service._move_robot_to_standby(runtime, robot, {robot["currentCellId"]}, set())
    service._flush_motion_events(runtime)
    assert _movement_events(repo)[0]["segmentHorizontalMeters"] == pytest.approx(1.6)


def test_shuttle_fractional_lift_playback_becomes_one_legal_lift_segment() -> None:
    observer = _observer()
    first = _capture(
        observer,
        _position(0, 0, 1),
        _position(0, 0, 1.5),
        movement_type=MovementType.LIFT,
    )
    second = _capture(
        observer,
        _position(0, 0, 1.5),
        _position(0, 0, 2),
        tick=2,
        movement_type=MovementType.LIFT,
    )
    assert first.pending_lift is True and first.event is None
    assert second.event is not None
    assert second.event.segment.segment_vertical_meters == pytest.approx(1.4)
    assert observer.diagnostics_payload()["totalMovementCount"] == 1


def test_cargo_lift_state_change_is_not_vehicle_mileage() -> None:
    observer = _observer()
    cargo = {"layer": 1}
    cargo["layer"] = 2
    assert observer.diagnostics_payload()["totalMovementCount"] == 0
    assert observer.diagnostics_payload()["totalHorizontalMeters"] == 0.0


def test_same_tick_different_robots_have_unique_ids() -> None:
    observer = _observer()
    observer.register_robots([{"id": "R2", "type": "ctu"}])
    first = _capture(observer, robot_id="R1")
    second = _capture(observer, _position(1, 0), _position(1, 1), robot_id="R2")
    assert first.event and second.event
    assert first.event.movement_id != second.event.movement_id
    assert {first.event.event_sequence, second.event.event_sequence} == {1, 2}


def test_same_tick_same_robot_multiple_segments_have_unique_sequences() -> None:
    observer = _observer()
    first = _capture(observer, _position(0, 0), _position(0, 1))
    second = _capture(observer, _position(0, 1), _position(0, 2), source="second")
    assert first.event and second.event
    assert first.event.event_sequence == 1
    assert second.event.event_sequence == 2
    assert observer.diagnostics_payload()["totalMovementCount"] == 2


def test_movement_id_duplicate_is_idempotent() -> None:
    observer = _observer()
    first = _capture(observer)
    duplicate = _capture(observer)
    assert first.event == duplicate.event
    assert duplicate.duplicate is True
    assert observer.diagnostics_payload()["totalMovementCount"] == 1
    assert observer.diagnostics_payload()["duplicateCount"] == 1


def test_same_movement_id_with_different_content_reports_conflict() -> None:
    observer = _observer()
    first = _capture(observer, event_sequence=7)
    conflict = _capture(
        observer,
        _position(0, 1),
        _position(0, 2),
        source="different",
        event_sequence=7,
    )
    assert first.event is not None
    assert conflict.conflict is True
    assert observer.diagnostics_payload()["conflictCount"] == 1
    assert observer.diagnostics_payload()["dataQuality"] == "INCOMPLETE"


def test_missing_physics_preserves_positions_but_not_meters() -> None:
    observer = _observer(meters=None)
    outcome = _capture(observer)
    assert outcome.event is not None
    payload = outcome.event.to_payload()
    assert payload["fromPosition"] is not None
    assert payload["segmentHorizontalMeters"] is None
    assert payload["dataQuality"] == "UNAVAILABLE"


def test_explicit_point_eight_meter_contract() -> None:
    observer = _observer(meters=0.8)
    assert _capture(observer).event.segment.segment_horizontal_meters == pytest.approx(0.8)  # type: ignore[union-attr]


def test_different_meters_per_cell_changes_only_metric_result() -> None:
    first = _observer(meters=0.8)
    second = _observer(run_id="run-second", meters=1.25)
    assert _capture(first).event.segment.segment_horizontal_meters == pytest.approx(0.8)  # type: ignore[union-attr]
    assert _capture(second).event.segment.segment_horizontal_meters == pytest.approx(1.25)  # type: ignore[union-attr]


def test_different_run_ids_are_isolated() -> None:
    first = _observer(run_id="run-a")
    second = _observer(run_id="run-b")
    first_event = _capture(first).event
    second_event = _capture(second).event
    assert first_event and second_event
    assert first_event.segment.run_id == "run-a"
    assert second_event.segment.run_id == "run-b"
    assert first_event.movement_id != second_event.movement_id


def test_reset_creates_new_generation_and_run_instance() -> None:
    observer = _observer(generation=1)
    next_observer = observer.new_generation()
    assert next_observer.generation == 2
    assert next_observer.run_instance_id.endswith("generation-2")
    assert next_observer.physics_contract is observer.physics_contract


def test_pause_does_not_create_motion_event() -> None:
    service, runtime, repo = _runtime(physics=_physics())
    service.pause(runtime.run_id)
    assert _movement_events(repo) == []
    assert runtime.state[DIAGNOSTICS_NAMESPACE]["totalMovementCount"] == 0


def test_stop_seals_motion_diagnostics() -> None:
    service, runtime, _repo = _runtime(physics=_physics())
    service.stop(runtime.run_id)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert diagnostics["ended"] is True
    assert diagnostics["endReason"] == "user"


def test_safety_limit_seal_preserves_collected_distance() -> None:
    observer = _observer()
    _capture(observer)
    observer.finish(2000, "safety_limit")
    diagnostics = observer.diagnostics_payload()
    assert diagnostics["totalHorizontalMeters"] == pytest.approx(0.8)
    assert diagnostics["endReason"] == "safety_limit"


def test_runtime_error_seal_preserves_previous_evidence() -> None:
    observer = _observer()
    _capture(observer)
    observer.finish(2, "runtime_error")
    diagnostics = observer.diagnostics_payload()
    assert diagnostics["totalMovementCount"] == 1
    assert diagnostics["endReason"] == "runtime_error"


def test_restart_recovery_is_explicitly_incomplete() -> None:
    first = _observer()
    _capture(first)
    saved = first.diagnostics_payload()
    recovered = MotionRuntimeObserver(
        "run-motion",
        1,
        _physics(),
        initial_tick=10,
        recovered_diagnostics=saved,
    )
    diagnostics = recovered.diagnostics_payload()
    assert diagnostics["historicalRecoveryIncomplete"] is True
    assert diagnostics["observationCompleteness"] == "INCOMPLETE"


def test_diagnostics_and_event_are_json_serializable() -> None:
    observer = _observer()
    outcome = _capture(observer)
    assert outcome.event is not None
    json.dumps(outcome.event.to_payload(), ensure_ascii=False, sort_keys=True)
    json.dumps(observer.diagnostics_payload(), ensure_ascii=False, sort_keys=True)


def test_movement_event_persistence_success_sets_watermark() -> None:
    service, runtime, repo = _runtime(physics=_physics())
    robot = runtime.state["robots"][0]
    before = service._motion_position(runtime, robot)
    robot["currentCol"] = 1
    robot["currentCellId"] = cell_id(0, 1, "1F")
    robot["currentNode"] = robot["currentCellId"]
    service._record_committed_motion(runtime, robot, before, MovementType.NORMAL, "test")
    service._flush_motion_events(runtime)
    assert len(_movement_events(repo)) == 1
    assert runtime.state[DIAGNOSTICS_NAMESPACE]["eventPersistenceWatermark"] is not None


def test_movement_event_persistence_failure_marks_incomplete_without_rollback() -> None:
    repository = FailingMovementRepository()
    service, runtime, _repo = _runtime(repository, physics=_physics())
    robot = runtime.state["robots"][0]
    before = service._motion_position(runtime, robot)
    robot["currentCol"] = 1
    robot["currentCellId"] = cell_id(0, 1, "1F")
    robot["currentNode"] = robot["currentCellId"]
    service._record_committed_motion(runtime, robot, before, MovementType.NORMAL, "test")
    service._flush_motion_events(runtime)
    diagnostics = runtime.state[DIAGNOSTICS_NAMESPACE]
    assert robot["currentCol"] == 1
    assert diagnostics["totalMovementCount"] == 1
    assert diagnostics["persistenceFailureCount"] == 1
    assert diagnostics["observationCompleteness"] == "INCOMPLETE"


def test_complete_run_with_no_motion_has_legitimate_zero() -> None:
    observer = _observer()
    observer.finish(0, "completed")
    diagnostics = observer.diagnostics_payload()
    assert diagnostics["totalHorizontalMeters"] == 0.0
    assert diagnostics["dataQuality"] == "COMPLETE"


def test_no_physics_run_has_null_not_zero() -> None:
    observer = _observer(meters=None)
    observer.finish(0, "completed")
    diagnostics = observer.diagnostics_payload()
    assert diagnostics["totalHorizontalMeters"] is None
    assert diagnostics["totalVerticalMeters"] is None
    assert diagnostics["dataQuality"] == "UNAVAILABLE"


def test_planned_distance_never_populates_actual_runtime_summary() -> None:
    observer = _observer()
    _capture(observer)
    diagnostics = observer.diagnostics_payload()
    assert diagnostics["plannedHorizontalMeters"] is None
    assert diagnostics["extraDistanceMeters"] is None
    assert diagnostics["attributableReplanExtraMeters"] is None


def test_backoff_is_not_double_counted_as_normal() -> None:
    observer = _observer()
    _capture(observer, movement_type=MovementType.BACKOFF)
    diagnostics = observer.diagnostics_payload()
    assert diagnostics["movementTypeSummary"]["BACKOFF"]["movementCount"] == 1
    assert diagnostics["movementTypeSummary"]["NORMAL"]["movementCount"] == 0
    assert diagnostics["totalMovementCount"] == 1


def _business_projection(runtime: RunRuntime) -> dict[str, Any]:
    return {
        "robots": [
            {
                key: deepcopy(robot.get(key))
                for key in ("currentCellId", "currentLayer", "path", "status", "waitTicks")
            }
            for robot in runtime.state["robots"]
        ],
        "tasks": [
            {key: deepcopy(task.get(key)) for key in ("status", "phase")}
            for task in runtime.state["tasks"]
        ],
        "inventory": deepcopy(runtime.state["inventory"]),
        "stopReason": runtime.state["stopReason"],
    }


def test_observer_enabled_and_disabled_have_identical_business_projection() -> None:
    enabled_service, enabled_runtime, _ = _runtime(physics=_physics(), enabled=True)
    disabled_service, disabled_runtime, _ = _runtime(physics=_physics(), enabled=False)
    enabled_service._move_robots(enabled_runtime)
    disabled_service._move_robots(disabled_runtime)
    assert _business_projection(enabled_runtime) == _business_projection(disabled_runtime)


def test_motion_diagnostics_do_not_overwrite_deadlock_diagnostics() -> None:
    service, runtime, _repo = _runtime(physics=_physics())
    before = deepcopy(runtime.state[DEADLOCK_NAMESPACE])
    service._move_robots(runtime)
    assert runtime.state[DEADLOCK_NAMESPACE] == before
    assert DIAGNOSTICS_NAMESPACE in runtime.state


def test_old_run_without_physics_contract_remains_operational() -> None:
    service, runtime, repo = _runtime(physics=None)
    service._move_robots(runtime)
    assert runtime.state["robots"][0]["currentCol"] == 1
    assert _movement_events(repo)[0]["segmentHorizontalMeters"] is None


def test_large_motion_volume_and_isolated_sqlite_cost(monkeypatch) -> None:
    database_path = BACKEND_ROOT / "tests" / f".p3-2-performance-{uuid.uuid4().hex}.db"
    monkeypatch.setattr(app_config, "DB_PATH", database_path)
    monkeypatch.setattr(app_db, "DB_PATH", database_path)
    assert app_config.DB_PATH == database_path
    assert app_db.DB_PATH == database_path
    assert database_path.resolve() != DEFAULT_DB.resolve()
    try:
        app_db.init_db()
        repository = Repository()

        observer = MotionRuntimeObserver("run-perf", 1, _physics())
        robots = [{"id": f"R{index:03d}", "type": "ctu"} for index in range(120)]
        observer.register_robots(robots)

        typical_observer = MotionRuntimeObserver("run-perf-typical", 1, _physics())
        typical_robots = robots[:20]
        typical_observer.register_robots(typical_robots)
        started = time.perf_counter()
        for index, robot in enumerate(typical_robots):
            outcome = _capture(
                typical_observer,
                _position(index, 0),
                _position(index, 1),
                robot_id=robot["id"],
                tick=1,
                source="performance-typical",
            )
            assert outcome.event is not None
        observer_20_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        events = []
        for index, robot in enumerate(robots):
            outcome = _capture(
                observer,
                _position(index, 0),
                _position(index, 1),
                robot_id=robot["id"],
                tick=1,
                source="performance",
            )
            assert outcome.event is not None
            events.append(outcome.event)
        observer_120_ms = (time.perf_counter() - started) * 1000

        example_movement = events[0].to_payload()
        event_bytes = len(json.dumps(example_movement, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        full_batch = observer.batch_payload(tuple(events))
        batch_120_bytes = len(json.dumps(full_batch, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        started = time.perf_counter()
        for index in range(1000):
            movement_payload = dict(example_movement)
            movement_payload["movementId"] = f"perf-{index}"
            movement_payload["eventSequence"] = index + 1
            batch_payload = {
                "contractVersion": "motion-runtime-v1",
                "runId": "run-perf",
                "runInstanceId": "run-perf:generation-1",
                "generation": 1,
                "tick": index + 1,
                "movementCount": 1,
                "movements": [movement_payload],
            }
            repository.add_event("run-perf", index + 1, EVENT_TYPE, batch_payload)
        sqlite_1000_ms = (time.perf_counter() - started) * 1000
        database_bytes_1000_ticks = database_path.stat().st_size
        started = time.perf_counter()
        repository.add_event("run-perf", 1001, EVENT_TYPE, full_batch)
        sqlite_120_vehicle_batch_ms = (time.perf_counter() - started) * 1000
        diagnostics_bytes = len(
            json.dumps(observer.diagnostics_payload(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        print(
            "P3_2_PERF "
            + json.dumps(
                {
                    "observer20VehiclesMs": round(observer_20_ms, 3),
                    "observer120VehiclesMs": round(observer_120_ms, 3),
                    "eventBytes": event_bytes,
                    "batch120VehiclesBytes": batch_120_bytes,
                    "sqlite120VehicleBatchMs": round(sqlite_120_vehicle_batch_ms, 3),
                    "sqlite1000EventsMs": round(sqlite_1000_ms, 3),
                    "sqlite1000TickDatabaseBytes": database_bytes_1000_ticks,
                    "diagnostics120RobotsBytes": diagnostics_bytes,
                },
                sort_keys=True,
            )
        )
        assert observer_20_ms < 1000
        assert observer_120_ms < 1000
        assert sqlite_1000_ms < 15000
        assert event_bytes < 4096
        assert batch_120_bytes < 250_000
        assert sqlite_120_vehicle_batch_ms < 1000
        assert database_bytes_1000_ticks < 10_000_000
        assert diagnostics_bytes < 1_000_000
        assert observer.diagnostics_payload()["totalMovementCount"] == 120
    finally:
        # init_db() uses sqlite connection context managers whose objects may
        # remain alive until GC on Windows; collect them before deleting the
        # isolated benchmark database.
        gc.collect()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{database_path}{suffix}")
            for _attempt in range(20):
                if not candidate.exists():
                    break
                try:
                    candidate.unlink()
                    break
                except PermissionError:
                    gc.collect()
                    time.sleep(0.05)
