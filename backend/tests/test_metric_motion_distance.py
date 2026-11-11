"""P3-3A read-only mileage API tests with collection-time DB isolation."""

from __future__ import annotations

from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any

import pytest
from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = BACKEND_ROOT / "app_data" / "agv_platform.db"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def fingerprint(path: Path) -> tuple[bool, int | None, int | None, str | None]:
    if not path.exists():
        return False, None, None, None
    return True, path.stat().st_size, path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


DEFAULT_DB_BEFORE = fingerprint(DEFAULT_DB)
_IMPORT_ROOT = tempfile.TemporaryDirectory(prefix="agv-p3-3a-import-", dir=Path(__file__).parent)
_IMPORT_DB = Path(_IMPORT_ROOT.name) / "collection.db"

from app.core import config as app_config  # noqa: E402
from app.core import db as app_db  # noqa: E402

app_config.DB_PATH = _IMPORT_DB
app_db.DB_PATH = _IMPORT_DB
assert app_config.DB_PATH.resolve() != DEFAULT_DB.resolve()
assert app_db.DB_PATH.resolve() != DEFAULT_DB.resolve()

from app.api import routes  # noqa: E402
from app.core.repository import Repository  # noqa: E402


CASE_DIRS: list[Path] = []


@pytest.fixture(scope="session", autouse=True)
def protect_default_database():
    yield
    app_config.DB_PATH = _IMPORT_DB
    app_db.DB_PATH = _IMPORT_DB
    gc.collect()
    for directory in CASE_DIRS:
        shutil.rmtree(directory, ignore_errors=True)
    assert fingerprint(DEFAULT_DB) == DEFAULT_DB_BEFORE


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> Repository:
    directory = Path(tempfile.mkdtemp(prefix="case-", dir=_IMPORT_ROOT.name))
    CASE_DIRS.append(directory)
    database = directory / "metrics.db"
    monkeypatch.setattr(app_config, "DB_PATH", database)
    monkeypatch.setattr(app_db, "DB_PATH", database)
    app_db.init_db()
    yield Repository()


def physics(version: str = "physics-test-v1", meters: float = 0.8) -> dict[str, Any]:
    return {
        "physicsContractVersion": version,
        "metersPerCell": meters,
        "shuttleLevelHeightMeters": 1.4,
        "floorHeightMeters": 4.2,
        "coordinateContractVersion": "grid-test-v1",
    }


def position(col: int, *, row: int = 10, layer: int = 1, floor: str = "1F") -> dict[str, Any]:
    return {"row": row, "col": col, "floor": floor, "layer": layer, "floorIndex": int(floor[:-1])}


def movement(
    run_id: str,
    movement_id: str,
    *,
    generation: int = 1,
    robot_id: str = "ctu-1",
    robot_type: str = "ctu",
    tick: int = 1,
    sequence: int = 1,
    from_col: int = 0,
    to_col: int = 1,
    movement_type: str = "NORMAL",
    horizontal: float | None = 0.8,
    vertical: float | None = 0.0,
    version: str | None = "physics-test-v1",
    quality: str = "COMPLETE",
    from_layer: int = 1,
    to_layer: int = 1,
) -> dict[str, Any]:
    return {
        "movementId": movement_id,
        "robotId": robot_id,
        "robotType": robot_type,
        "runId": run_id,
        "runInstanceId": f"{run_id}:generation-{generation}",
        "fromPosition": position(from_col, layer=from_layer),
        "toPosition": position(to_col, layer=to_layer),
        "fromTick": max(0, tick - 1),
        "toTick": tick,
        "movementType": movement_type,
        "segmentHorizontalMeters": horizontal,
        "segmentVerticalMeters": vertical,
        "plannedPathId": None,
        "replanId": None,
        "physicsContractVersion": version,
        "dataQuality": quality,
        "issue": None,
        "contractVersion": "motion-runtime-v1",
        "generation": generation,
        "eventSequence": sequence,
        "source": "test-committed-motion",
        "observedFromPosition": position(from_col, layer=from_layer),
        "observedToPosition": position(to_col, layer=to_layer),
    }


def batch(run_id: str, tick: int, movements: list[dict[str, Any]], *, generation: int = 1, instance: str | None = None) -> dict[str, Any]:
    return {
        "contractVersion": "motion-runtime-v1",
        "runId": run_id,
        "runInstanceId": instance or f"{run_id}:generation-{generation}",
        "generation": generation,
        "tick": tick,
        "movementCount": len(movements),
        "movements": movements,
    }


def diagnostics(
    run_id: str,
    *,
    generation: int = 1,
    total: int = 1,
    last_tick: int = 1,
    last_id: str | None = "m1",
    last_sequence: int = 1,
    robots: dict[str, str] | None = None,
    contract: dict[str, Any] | None | object = ...,
    ended: bool = True,
    end_reason: str = "completed",
    completeness: str = "COMPLETE",
    pending: int = 0,
    failures: int = 0,
    recovery: bool = False,
) -> dict[str, Any]:
    frozen = physics() if contract is ... else contract
    version = frozen.get("physicsContractVersion") if isinstance(frozen, dict) else None
    meters = frozen.get("metersPerCell") if isinstance(frozen, dict) else None
    robot_map = robots if robots is not None else ({"ctu-1": "ctu"} if total else {"ctu-1": "ctu"})
    return {
        "contractVersion": "motion-runtime-v1",
        "runId": run_id,
        "runInstanceId": f"{run_id}:generation-{generation}",
        "generation": generation,
        "physicsContractVersion": version,
        "physicsContract": frozen,
        "metersPerCell": meters,
        "lastProcessedTick": last_tick,
        "totalMovementCount": total,
        "robotCount": len(robot_map),
        "completeTrajectoryRobotCount": len(robot_map),
        "incompleteTrajectoryRobotCount": 0,
        "perRobotSummary": {
            robot_id: {"robotId": robot_id, "robotType": robot_type, "incompleteCount": 0}
            for robot_id, robot_type in robot_map.items()
        },
        "movementTypeSummary": {},
        "duplicateCount": 0,
        "conflictCount": 0,
        "incompleteCount": 0,
        "persistenceFailureCount": failures,
        "observationCompleteness": completeness,
        "dataQuality": "COMPLETE",
        "eventPersistenceWatermark": (
            {"movementId": last_id, "tick": last_tick, "eventSequence": last_sequence} if last_id else None
        ),
        "pendingPersistenceCount": pending,
        "historicalRecoveryIncomplete": recovery,
        "ended": ended,
        "endReason": end_reason,
    }


def create_run(
    repo: Repository,
    run_id: str = "run-1",
    *,
    diag: dict[str, Any] | None | object = ...,
    status: str = "completed",
    legacy: bool = False,
) -> str:
    payload: dict[str, Any] = {"tick": 1, "tasks": []}
    if not legacy:
        payload["motionDistanceDiagnosticsV1"] = diagnostics(run_id) if diag is ... else diag
    repo.create_run(run_id, status, "topology-test", payload, {})
    return run_id


def add_batch(repo: Repository, run_id: str, event_batch: dict[str, Any]) -> None:
    repo.add_event(run_id, int(event_batch["tick"]), "robot_movement_batch_v1", event_batch)


def one_movement_result(repository: Repository, **kwargs: Any) -> dict[str, Any]:
    run_id = kwargs.pop("run_id", "run-1")
    item = movement(run_id, "m1", **kwargs)
    diag = diagnostics(
        run_id,
        robots={item["robotId"]: item["robotType"]},
        last_tick=item["toTick"],
        last_id="m1",
        last_sequence=item["eventSequence"],
    )
    create_run(repository, run_id, diag=diag)
    add_batch(repository, run_id, batch(run_id, item["toTick"], [item], generation=item["generation"]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result is not None
    return result


def value(result: dict[str, Any], name: str) -> Any:
    return result["totalMetrics"][name]["value"]


def test_ctu_normal_horizontal_movement(repository: Repository) -> None:
    result = one_movement_result(repository)
    assert value(result, "ctuHorizontalMeters") == pytest.approx(0.8)


def test_shuttle_horizontal_movement(repository: Repository) -> None:
    result = one_movement_result(repository, robot_id="shuttle-1", robot_type="shuttle", horizontal=1.6)
    assert value(result, "shuttleHorizontalMeters") == pytest.approx(1.6)


def test_shuttle_two_by_two_track_distance_is_preserved(repository: Repository) -> None:
    result = one_movement_result(repository, robot_id="shuttle-1", robot_type="shuttle", from_col=4, to_col=6, horizontal=1.6)
    assert value(result, "totalHorizontalMeters") == pytest.approx(1.6)


def test_lift_vertical_displacement_is_separate(repository: Repository) -> None:
    result = one_movement_result(repository, robot_id="shuttle-1", robot_type="shuttle", movement_type="LIFT", horizontal=0.0, vertical=1.4, from_layer=1, to_layer=2)
    assert value(result, "shuttleLiftVerticalMeters") == pytest.approx(1.4)
    assert value(result, "shuttleHorizontalMeters") == pytest.approx(0.0)


def test_ctu_and_shuttle_totals_are_separate(repository: Repository) -> None:
    run_id = "mixed"
    first = movement(run_id, "m1", robot_id="ctu-1", horizontal=0.8)
    second = movement(run_id, "m2", robot_id="shuttle-1", robot_type="shuttle", horizontal=1.6, sequence=2)
    diag = diagnostics(run_id, total=2, last_id="m2", last_sequence=2, robots={"ctu-1": "ctu", "shuttle-1": "shuttle"})
    create_run(repository, run_id, diag=diag)
    add_batch(repository, run_id, batch(run_id, 1, [first, second]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and value(result, "ctuHorizontalMeters") == pytest.approx(0.8)
    assert value(result, "shuttleHorizontalMeters") == pytest.approx(1.6)


@pytest.mark.parametrize("movement_type", ["NORMAL", "YIELD", "BACKOFF", "RETURN_TO_STANDBY", "LIFT"])
def test_movement_type_subtotals(repository: Repository, movement_type: str) -> None:
    vertical = 1.4 if movement_type == "LIFT" else 0.0
    horizontal = 0.0 if movement_type == "LIFT" else 0.8
    result = one_movement_result(repository, movement_type=movement_type, horizontal=horizontal, vertical=vertical, from_layer=1, to_layer=2 if movement_type == "LIFT" else 1)
    subtotal = result["movementTypeMetrics"][movement_type]
    assert subtotal["movementCount"] == 1
    assert subtotal["horizontalMeters"] == pytest.approx(horizontal)


def test_multiple_segments_in_same_tick(repository: Repository) -> None:
    run_id = "same-tick"
    items = [movement(run_id, f"m{i}", robot_id=f"ctu-{i}", sequence=i) for i in range(1, 4)]
    create_run(repository, run_id, diag=diagnostics(run_id, total=3, last_id="m3", last_sequence=3, robots={f"ctu-{i}": "ctu" for i in range(1, 4)}))
    add_batch(repository, run_id, batch(run_id, 1, items))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and value(result, "totalMovementCount") == 3


def test_multiple_batches_in_same_tick(repository: Repository) -> None:
    run_id = "split-batch"
    create_run(repository, run_id, diag=diagnostics(run_id, total=2, last_id="m2", last_sequence=2, robots={"ctu-1": "ctu", "ctu-2": "ctu"}))
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")]))
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m2", robot_id="ctu-2", sequence=2)]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["evidenceSummary"]["eventBatchCount"] == 2
    assert value(result, "totalHorizontalMeters") == pytest.approx(1.6)


def test_duplicate_movement_id_same_content_is_deduplicated(repository: Repository) -> None:
    run_id = "duplicate"
    item = movement(run_id, "m1")
    create_run(repository, run_id)
    add_batch(repository, run_id, batch(run_id, 1, [item]))
    add_batch(repository, run_id, batch(run_id, 1, [deepcopy(item)]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["evidenceSummary"]["duplicateCount"] == 1
    assert value(result, "totalHorizontalMeters") == pytest.approx(0.8)


def test_conflicting_movement_id_is_excluded(repository: Repository) -> None:
    run_id = "conflict"
    first = movement(run_id, "m1")
    second = movement(run_id, "m1", horizontal=1.6, to_col=2)
    create_run(repository, run_id)
    add_batch(repository, run_id, batch(run_id, 1, [first]))
    add_batch(repository, run_id, batch(run_id, 1, [second]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["evidenceSummary"]["conflictCount"] == 1
    assert value(result, "totalHorizontalMeters") is None
    assert result["dataQuality"] == "INCOMPLETE"


def test_different_robot_ids_are_isolated(repository: Repository) -> None:
    result = one_movement_result(repository, robot_id="ctu-special")
    assert result["perRobotMetrics"][0]["robotId"] == "ctu-special"


def test_latest_generation_is_selected(repository: Repository) -> None:
    run_id = "generations"
    diag = diagnostics(run_id, generation=2, total=1, last_id="g2")
    create_run(repository, run_id, diag=diag)
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "g1")], generation=1))
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "g2", generation=2)], generation=2))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["generation"] == 2 and value(result, "totalMovementCount") == 1


def test_historical_generation_is_event_only(repository: Repository) -> None:
    run_id = "historical"
    create_run(repository, run_id, diag=diagnostics(run_id, generation=2, last_id="g2"))
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "g1")], generation=1))
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "g2", generation=2)], generation=2))
    result = repository.get_motion_distance_metrics(run_id, generation=1)
    assert result and result["generation"] == 1
    assert value(result, "totalMovementCount") == 1
    assert value(result, "totalHorizontalMeters") is None


def test_run_instance_mismatch_is_excluded(repository: Repository) -> None:
    run_id = "wrong-instance"
    create_run(repository, run_id)
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")], instance="other:generation-1"))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["evidenceSummary"]["excludedBatchCount"] == 1


def test_physics_version_mismatch_is_excluded(repository: Repository) -> None:
    result = one_movement_result(repository, version="other-physics")
    assert result["evidenceSummary"]["physicsVersionMismatchCount"] == 1
    assert value(result, "totalHorizontalMeters") is None


def test_missing_physics_contract_returns_null_meters(repository: Repository) -> None:
    run_id = "no-physics"
    create_run(repository, run_id, diag=diagnostics(run_id, contract=None))
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1", version=None, horizontal=None, vertical=None, quality="UNAVAILABLE")]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and value(result, "totalHorizontalMeters") is None
    assert value(result, "incompleteRobotCount") == 1
    assert result["physicsContractVersion"] is None


def test_explicit_meters_per_cell_point_eight_is_reported(repository: Repository) -> None:
    result = one_movement_result(repository)
    assert result["metersPerCell"] == pytest.approx(0.8)


def test_complete_no_movement_returns_legal_zero(repository: Repository) -> None:
    run_id = "zero"
    create_run(repository, run_id, diag=diagnostics(run_id, total=0, last_id=None, robots={"ctu-1": "ctu"}))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and value(result, "totalHorizontalMeters") == pytest.approx(0.0)
    assert value(result, "totalMovementCount") == 0


def test_trajectory_discontinuity_retains_subtotal(repository: Repository) -> None:
    run_id = "gap"
    first = movement(run_id, "m1", from_col=0, to_col=1)
    second = movement(run_id, "m2", tick=2, sequence=1, from_col=9, to_col=10)
    create_run(repository, run_id, diag=diagnostics(run_id, total=2, last_tick=2, last_id="m2"))
    add_batch(repository, run_id, batch(run_id, 1, [first]))
    add_batch(repository, run_id, batch(run_id, 2, [second]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["evidenceSummary"]["trajectoryBreakCount"] == 1
    assert value(result, "totalHorizontalMeters") == pytest.approx(1.6)
    assert result["dataQuality"] == "INCOMPLETE"


def test_snapshot_watermark_mismatch_is_incomplete(repository: Repository) -> None:
    run_id = "watermark"
    create_run(repository, run_id, diag=diagnostics(run_id, last_id="different"))
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and "SNAPSHOT_EVENT_WATERMARK_MISMATCH" in result["evidenceSummary"]["consistencyIssues"]


def test_historical_recovery_is_incomplete(repository: Repository) -> None:
    run_id = "recovery"
    create_run(repository, run_id, diag=diagnostics(run_id, recovery=True))
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["reason"] == "HISTORICAL_RECOVERY_INCOMPLETE"


def test_normal_completed_run_is_complete(repository: Repository) -> None:
    result = one_movement_result(repository)
    assert result["dataQuality"] == "COMPLETE"


def test_running_run_is_degraded(repository: Repository) -> None:
    run_id = "running"
    create_run(repository, run_id, diag=diagnostics(run_id, ended=False), status="running")
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["dataQuality"] == "DEGRADED"


def test_manual_stop_is_degraded(repository: Repository) -> None:
    run_id = "stopped"
    create_run(repository, run_id, diag=diagnostics(run_id, end_reason="user_stop"), status="stopped")
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["dataQuality"] == "DEGRADED"


@pytest.mark.parametrize("reason", ["safety_limit", "runtime_error"])
def test_abnormal_stop_is_incomplete(repository: Repository, reason: str) -> None:
    run_id = f"stop-{reason}"
    create_run(repository, run_id, diag=diagnostics(run_id, end_reason=reason), status="stopped")
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["dataQuality"] == "INCOMPLETE"


def test_legacy_run_has_null_distance_contract(repository: Repository) -> None:
    create_run(repository, "legacy", legacy=True)
    result = repository.get_motion_distance_metrics("legacy")
    assert result and result["dataQuality"] == "UNAVAILABLE"
    assert value(result, "totalHorizontalMeters") is None


def test_missing_run_returns_404(repository: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "repo", repository)
    with pytest.raises(HTTPException) as error:
        routes.get_simulation_motion_distance_metrics("missing")
    assert error.value.status_code == 404


def test_invalid_generation_returns_422(repository: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "repo", repository)
    with pytest.raises(HTTPException) as error:
        routes.get_simulation_motion_distance_metrics("run", generation=0)
    assert error.value.status_code == 422


def test_unknown_generation_returns_empty_contract(repository: Repository) -> None:
    create_run(repository, "run-1")
    result = repository.get_motion_distance_metrics("run-1", generation=9)
    assert result and result["reason"] == "GENERATION_NOT_FOUND"
    assert value(result, "totalHorizontalMeters") is None


def test_vehicle_type_groups_are_separate(repository: Repository) -> None:
    result = one_movement_result(repository, robot_id="shuttle-1", robot_type="shuttle")
    assert result["vehicleTypeMetrics"]["SHUTTLE"]["movementCount"] == 1
    assert result["vehicleTypeMetrics"]["CTU"]["movementCount"] == 0


def test_top_twenty_is_explicitly_truncated(repository: Repository) -> None:
    run_id = "top20"
    items = [movement(run_id, f"m{i}", robot_id=f"ctu-{i:02d}", sequence=i, horizontal=float(i)) for i in range(1, 26)]
    robots = {f"ctu-{i:02d}": "ctu" for i in range(1, 26)}
    create_run(repository, run_id, diag=diagnostics(run_id, total=25, last_id="m25", last_sequence=25, robots=robots))
    add_batch(repository, run_id, batch(run_id, 1, items))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and len(result["perRobotMetrics"]) == 20
    assert result["totalRobotCount"] == 25 and result["perRobotMetricsTruncated"] is True


def test_missing_plan_ids_do_not_create_optimization_rate(repository: Repository) -> None:
    result = one_movement_result(repository)
    assert result["plannedHorizontalMeters"] is None
    assert result["algorithmDistanceImprovementRate"] is None


def test_repeated_get_preserves_database_file(repository: Repository) -> None:
    run_id = "readonly"
    create_run(repository, run_id)
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")]))
    before = fingerprint(Path(app_db.DB_PATH))
    repository.get_motion_distance_metrics(run_id)
    repository.get_motion_distance_metrics(run_id)
    assert fingerprint(Path(app_db.DB_PATH)) == before


def test_repeated_get_does_not_add_events(repository: Repository) -> None:
    run_id = "no-events"
    create_run(repository, run_id)
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")]))
    with app_db.get_read_conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM simulation_events").fetchone()[0]
    repository.get_motion_distance_metrics(run_id)
    with app_db.get_read_conn() as conn:
        after = conn.execute("SELECT COUNT(*) FROM simulation_events").fetchone()[0]
    assert after == before


def test_response_is_json_serializable(repository: Repository) -> None:
    result = one_movement_result(repository)
    assert json.loads(json.dumps(result, allow_nan=False))["runId"] == "run-1"


def test_get_does_not_call_simulation_seed_or_tick(repository: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = "side-effects"
    create_run(repository, run_id)
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")]))
    monkeypatch.setattr(routes, "repo", repository)
    monkeypatch.setattr(routes.simulation_service, "start", lambda *_args, **_kwargs: pytest.fail("start called"))
    result = routes.get_simulation_motion_distance_metrics(run_id)
    assert result["runId"] == run_id


def test_deadlock_lifecycle_route_remains_registered() -> None:
    paths = {item.path for item in routes.router.routes}
    assert "/api/v1/simulations/{run_id}/metrics/deadlock-lifecycle" in paths


def test_existing_five_read_metric_routes_remain_registered() -> None:
    paths = {item.path for item in routes.router.routes}
    expected = {
        "/api/v1/simulations/{run_id}/metrics/tasks",
        "/api/v1/simulations/{run_id}/metrics/history/quality",
        "/api/v1/simulations/{run_id}/metrics/ctu-contention",
        "/api/v1/simulations/{run_id}/metrics/path-planning",
        "/api/v1/simulations/{run_id}/metrics/conveyor-coordination",
    }
    assert expected <= paths


def test_motion_distance_route_is_registered() -> None:
    paths = {item.path for item in routes.router.routes}
    assert "/api/v1/simulations/{run_id}/metrics/motion-distance" in paths


def test_every_total_metric_has_semantics(repository: Repository) -> None:
    result = one_movement_result(repository)
    for metric in result["totalMetrics"].values():
        assert {"value", "unit", "sampleCount", "dataQuality", "reason", "source"} <= metric.keys()


def test_acceptance_is_false_without_formal_site_calibration(repository: Repository) -> None:
    result = one_movement_result(repository)
    assert result["acceptanceEligible"] is False


def test_incomplete_movement_is_not_fabricated_as_zero(repository: Repository) -> None:
    run_id = "incomplete-move"
    create_run(repository, run_id)
    item = movement(run_id, "m1", horizontal=None, vertical=None, quality="INCOMPLETE")
    add_batch(repository, run_id, batch(run_id, 1, [item]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and value(result, "totalHorizontalMeters") is None


def test_pending_persistence_is_incomplete(repository: Repository) -> None:
    run_id = "pending"
    create_run(repository, run_id, diag=diagnostics(run_id, pending=1))
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["reason"] == "EVENT_PERSISTENCE_PENDING"


def test_persistence_failure_is_incomplete(repository: Repository) -> None:
    run_id = "failure"
    create_run(repository, run_id, diag=diagnostics(run_id, failures=1))
    add_batch(repository, run_id, batch(run_id, 1, [movement(run_id, "m1")]))
    result = repository.get_motion_distance_metrics(run_id)
    assert result and result["reason"] == "EVENT_PERSISTENCE_FAILED"


@pytest.mark.parametrize("movement_count", [1000, 10000])
def test_large_event_volume_is_linear_and_response_bounded(repository: Repository, movement_count: int) -> None:
    run_id = f"perf-{movement_count}"
    robot_count = 100
    robots = {f"ctu-{i:03d}": "ctu" for i in range(robot_count)}
    last_tick = (movement_count - 1) // 200 + 1
    final_in_tick = (movement_count - 1) % 200 + 1
    create_run(repository, run_id, diag=diagnostics(run_id, total=movement_count, last_tick=last_tick, last_id=f"m{movement_count - 1}", last_sequence=final_in_tick, robots=robots))
    for offset in range(0, movement_count, 200):
        tick = offset // 200 + 1
        items = []
        for local, index in enumerate(range(offset, min(offset + 200, movement_count)), start=1):
            robot_index = index % robot_count
            step = index // robot_count
            items.append(movement(run_id, f"m{index}", robot_id=f"ctu-{robot_index:03d}", tick=tick, sequence=local, from_col=step, to_col=step + 1))
        add_batch(repository, run_id, batch(run_id, tick, items))
    started = time.perf_counter()
    result = repository.get_motion_distance_metrics(run_id)
    elapsed = time.perf_counter() - started
    encoded = json.dumps(result, allow_nan=False).encode()
    assert result and value(result, "totalMovementCount") == movement_count
    assert len(result["perRobotMetrics"]) == 20
    assert len(encoded) < 100_000
    assert elapsed < 10.0
