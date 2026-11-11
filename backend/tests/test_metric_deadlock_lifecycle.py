from __future__ import annotations

import hashlib
import json
import gc
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException


# routes.py imports runtime. Redirect both path holders before that import so test
# collection cannot initialize or seed the default business database.
_TEST_TEMP_ROOT = Path(__file__).resolve().parent
_IMPORT_DB_DIR = tempfile.TemporaryDirectory(
    prefix="agv-p2-2b-import-",
    dir=_TEST_TEMP_ROOT,
)
_IMPORT_DB_PATH = Path(_IMPORT_DB_DIR.name) / "collection.db"

from app.core import config as app_config  # noqa: E402
from app.core import db as app_db  # noqa: E402

app_config.DB_PATH = _IMPORT_DB_PATH
app_db.DB_PATH = _IMPORT_DB_PATH

from app.api import routes  # noqa: E402
from app.core.repository import Repository  # noqa: E402


_CASE_DIRECTORIES: list[Path] = []


@pytest.fixture(scope="session", autouse=True)
def _cleanup_case_databases() -> Any:
    yield
    app_config.DB_PATH = _IMPORT_DB_PATH
    app_db.DB_PATH = _IMPORT_DB_PATH
    gc.collect()
    for directory in _CASE_DIRECTORIES:
        shutil.rmtree(directory)


LIFECYCLE_TYPES = {
    "started": "deadlock_episode_started",
    "persistent": "deadlock_episode_persistent",
    "resolved": "deadlock_episode_resolved",
    "unresolved": "deadlock_episode_unresolved",
    "unverified": "deadlock_episode_unverified",
}


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> Repository:
    directory = Path(tempfile.mkdtemp(
        prefix="agv-p2-2b-case-",
        dir=_IMPORT_DB_DIR.name,
    ))
    _CASE_DIRECTORIES.append(directory)
    database = directory / "p2-2b.db"
    monkeypatch.setattr(app_config, "DB_PATH", database)
    monkeypatch.setattr(app_db, "DB_PATH", database)
    app_db.init_db()
    yield Repository()


def diagnostics(
    *,
    generation: int = 1,
    last_tick: int = 20,
    completeness: str = "COMPLETE",
    ended: bool = False,
    end_reason: str | None = None,
    blocked_ticks: int = 0,
    block_events: int = 0,
    suspected: int = 0,
    persistent: int = 0,
    resolved: int = 0,
    unresolved: int = 0,
    unverified: int = 0,
    active: list[dict[str, Any]] | None = None,
    recent: list[dict[str, Any]] | None = None,
    robot_stats: list[dict[str, Any]] | None = None,
    pending: int = 0,
    acknowledged: str | None = None,
) -> dict[str, Any]:
    return {
        "contractVersion": "deadlock-episodes-v1",
        "runId": "filled-by-test",
        "runInstanceId": f"filled-by-test:generation-{generation}",
        "generation": generation,
        "lastProcessedTick": last_tick,
        "observationCompleteness": completeness,
        "qualityIssues": [],
        "blockedRobotTicks": blocked_ticks,
        "blockEpisodeCount": block_events,
        "suspectedCycleCount": suspected,
        "persistentCycleCount": persistent,
        "resolvedCycleCount": resolved,
        "unresolvedCycleCount": unresolved,
        "unverifiedCycleCount": unverified,
        "activeEpisodes": active or [],
        "recentClosedEpisodes": recent or [],
        "robotBlockStats": robot_stats or [],
        "lastBlockedObservations": [],
        "diagnosticFailures": [],
        "eventPersistence": {
            "guarantee": "BEST_EFFORT_NOT_EXACTLY_ONCE",
            "lastAcknowledgedTransitionKey": acknowledged,
            "pendingTransitionCount": pending,
        },
        "recovery": {"historicalBackfill": False},
        "performance": {"processedTicks": last_tick},
        "ended": ended,
        "endReason": end_reason,
    }


def create_run(
    repository: Repository,
    run_id: str = "run-1",
    *,
    diag: dict[str, Any] | None = None,
    status: str = "running",
    stop_reason: str | None = None,
    old_run: bool = False,
) -> str:
    payload: dict[str, Any] = {"tick": 20, "tasks": []}
    if not old_run:
        snapshot = diag or diagnostics()
        snapshot["runId"] = run_id
        snapshot["runInstanceId"] = f"{run_id}:generation-{snapshot['generation']}"
        payload["deadlockDiagnosticsV1"] = snapshot
    if stop_reason is not None:
        payload["stopReason"] = stop_reason
    repository.create_run(run_id, status, "topology-test", payload, {})
    return run_id


def episode_payload(
    episode_id: str,
    status: str,
    *,
    persistent_tick: int | None = None,
    resolved_tick: int | None = None,
    cells: list[str] | None = None,
    robots: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "episodeId": episode_id,
        "detectorRunId": "detector",
        "firstObservedTick": 1,
        "suspectedAtTick": 2,
        "persistentAtTick": persistent_tick,
        "resolvedAtTick": resolved_tick,
        "lastObservedTick": resolved_tick or persistent_tick or 2,
        "participantRobotIds": robots or ["ctu-1", "ctu-2"],
        "waitingEdges": [],
        "involvedCellIds": cells or ["cell-r10-c20"],
        "maxWaitTicks": 10,
        "mitigationActions": [],
        "status": status,
        "resolutionEvidence": [],
        "dataQuality": "COMPLETE",
        "cycleFingerprint": f"fp-{episode_id}",
    }


def add_transition(
    repository: Repository,
    run_id: str,
    episode_id: str,
    transition: str,
    *,
    generation: int = 1,
    tick: int = 2,
    transition_key: str | None = None,
    persistent_tick: int | None = None,
    resolved_tick: int | None = None,
    cells: list[str] | None = None,
    robots: list[str] | None = None,
) -> str:
    key = transition_key or f"{run_id}:generation-{generation}:{episode_id}:{transition}"
    status_by_transition = {
        "started": "SUSPECTED_CYCLE",
        "persistent": "PERSISTENT_WAIT_CYCLE",
        "resolved": "RESOLVED",
        "unresolved": "UNRESOLVED_AT_RUN_END",
        "unverified": "ENDED_UNVERIFIED",
    }
    repository.add_event(
        run_id,
        tick,
        LIFECYCLE_TYPES[transition],
        {
            "contractVersion": "deadlock-episodes-v1",
            "runId": run_id,
            "runInstanceId": f"{run_id}:generation-{generation}",
            "generation": generation,
            "transitionKey": key,
            "episode": episode_payload(
                episode_id,
                status_by_transition[transition],
                persistent_tick=persistent_tick,
                resolved_tick=resolved_tick,
                cells=cells,
                robots=robots,
            ),
        },
    )
    return key


def metric(result: dict[str, Any], metric_id: str) -> dict[str, Any]:
    return next(item for item in result["metrics"] if item["metricId"] == metric_id)


def add_episode_history(
    repository: Repository,
    run_id: str,
    episode_id: str,
    *,
    generation: int = 1,
    terminal: str | None = None,
    persistent_tick: int = 5,
    resolved_tick: int = 12,
    cells: list[str] | None = None,
) -> None:
    add_transition(repository, run_id, episode_id, "started", generation=generation, tick=2, cells=cells)
    add_transition(
        repository,
        run_id,
        episode_id,
        "persistent",
        generation=generation,
        tick=persistent_tick,
        persistent_tick=persistent_tick,
        cells=cells,
    )
    if terminal:
        add_transition(
            repository,
            run_id,
            episode_id,
            terminal,
            generation=generation,
            tick=resolved_tick,
            persistent_tick=persistent_tick,
            resolved_tick=resolved_tick if terminal == "resolved" else None,
            cells=cells,
        )


def test_normal_run_query_uses_snapshot_and_events(repository: Repository) -> None:
    stats = [{
        "robotId": "ctu-1", "continuousBlockedTicks": 3,
        "maxContinuousBlockedTicks": 8, "cumulativeBlockedRobotTicks": 11,
        "blockerRobotId": "ctu-2", "blockedCellId": "cell-r10-c20",
        "nextCellId": "cell-r10-c21", "dataQuality": "COMPLETE",
    }]
    create_run(repository, diag=diagnostics(blocked_ticks=11, block_events=2, suspected=1, persistent=1, robot_stats=stats))
    add_episode_history(repository, "run-1", "ep-1")
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert result is not None
    assert metric(result, "CUMULATIVE_BLOCKED_ROBOT_TICKS")["value"] == 11
    assert metric(result, "PERSISTENT_WAIT_CYCLE_COUNT")["value"] == 1
    assert result["robotBlocking"][0]["metrics"][2]["value"] == 11


def test_missing_run_returns_none_and_route_404(repository: Repository) -> None:
    assert repository.get_deadlock_lifecycle_metrics("missing") is None
    with pytest.raises(HTTPException) as error:
        routes.get_simulation_deadlock_lifecycle_metrics("missing")
    assert error.value.status_code == 404


def test_legacy_run_returns_legal_null_structure(repository: Repository) -> None:
    create_run(repository, old_run=True)
    repository.add_event("run-1", 1, "deadlock_detected", {"robots": ["ctu-1"]})
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert result is not None and result["generation"] is None
    assert result["dataQuality"] == "MISSING_SOURCE"
    assert metric(result, "SUSPECTED_CYCLE_EPISODE_COUNT")["value"] is None


def test_initial_snapshot_preserves_legal_zero(repository: Repository) -> None:
    create_run(repository)
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert result is not None
    for metric_id in (
        "CUMULATIVE_BLOCKED_ROBOT_TICKS", "BLOCK_EPISODE_COUNT",
        "SUSPECTED_CYCLE_EPISODE_COUNT", "PERSISTENT_WAIT_CYCLE_COUNT",
        "OPEN_CYCLE_EPISODE_COUNT",
    ):
        assert metric(result, metric_id)["value"] == 0


def test_single_persistent_cycle(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(suspected=1, persistent=1, active=[{"episodeId": "ep-1"}]))
    add_episode_history(repository, "run-1", "ep-1")
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert metric(result, "PERSISTENT_WAIT_CYCLE_COUNT")["value"] == 1
    assert metric(result, "OPEN_CYCLE_EPISODE_COUNT")["value"] == 1


def test_multiple_independent_cycles(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(
        suspected=2,
        persistent=2,
        active=[{"episodeId": "ep-1"}, {"episodeId": "ep-2"}],
    ))
    add_episode_history(repository, "run-1", "ep-1")
    add_episode_history(repository, "run-1", "ep-2", cells=["cell-r11-c21"])
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert metric(result, "SUSPECTED_CYCLE_EPISODE_COUNT")["value"] == 2
    assert len(result["hotspots"]) == 2


def test_duplicate_transition_key_is_deduplicated(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(suspected=1))
    key = add_transition(repository, "run-1", "ep-1", "started")
    add_transition(repository, "run-1", "ep-1", "started", tick=3, transition_key=key)
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert metric(result, "SUSPECTED_CYCLE_EPISODE_COUNT")["value"] == 1
    assert result["eventPersistenceWatermark"]["duplicateTransitionCount"] == 1


def test_duplicate_active_episode_summary_is_deduplicated(repository: Repository) -> None:
    active = [{"episodeId": "ep-1"}, {"episodeId": "ep-1"}]
    create_run(repository, diag=diagnostics(suspected=1, active=active))
    add_transition(repository, "run-1", "ep-1", "started")
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert metric(result, "OPEN_CYCLE_EPISODE_COUNT")["value"] == 1
    assert "DUPLICATE_ACTIVE_EPISODE_DEDUPLICATED" in result["qualityIssues"]


def test_persistent_cycle_can_be_confirmed_resolved(repository: Repository) -> None:
    create_run(repository, status="completed", diag=diagnostics(suspected=1, persistent=1, resolved=1, ended=True))
    add_episode_history(repository, "run-1", "ep-1", terminal="resolved")
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert metric(result, "CONFIRMED_RESOLVED_EPISODE_COUNT")["value"] == 1
    assert metric(result, "CONFIRMED_RESOLUTION_RATIO")["value"] == 100.0


def test_unresolved_episode(repository: Repository) -> None:
    create_run(repository, status="stopped", diag=diagnostics(suspected=1, persistent=1, unresolved=1, ended=True))
    add_episode_history(repository, "run-1", "ep-1", terminal="unresolved")
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert metric(result, "UNRESOLVED_AT_RUN_END_COUNT")["value"] == 1


def test_ended_unverified_episode(repository: Repository) -> None:
    create_run(repository, status="stopped", diag=diagnostics(suspected=1, unverified=1, ended=True))
    add_transition(repository, "run-1", "ep-1", "started")
    add_transition(repository, "run-1", "ep-1", "unverified", tick=8)
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert metric(result, "ENDED_UNVERIFIED_EPISODE_COUNT")["value"] == 1


def test_cycle_below_persistent_threshold_is_not_persistent(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(suspected=1, active=[{"episodeId": "ep-1"}]))
    add_transition(repository, "run-1", "ep-1", "started")
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert metric(result, "PERSISTENT_WAIT_CYCLE_COUNT")["value"] == 0
    assert metric(result, "OPEN_CYCLE_EPISODE_COUNT")["value"] == 1


def test_running_open_episode_does_not_claim_final_ratio(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(suspected=1, persistent=1, active=[{"episodeId": "ep-1"}]))
    add_episode_history(repository, "run-1", "ep-1")
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    ratio = metric(result, "CONFIRMED_RESOLUTION_RATIO")
    assert ratio["value"] is None and ratio["reason"] == "RUN_NOT_TERMINAL"


def test_generation_query_is_isolated(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(generation=2, suspected=1))
    add_transition(repository, "run-1", "old", "started", generation=1)
    add_transition(repository, "run-1", "new", "started", generation=2)
    old = repository.get_deadlock_lifecycle_metrics("run-1", generation=1)
    new = repository.get_deadlock_lifecycle_metrics("run-1", generation=2)
    assert old["generation"] == 1 and new["generation"] == 2
    assert old["recentEpisodes"][0]["episodeId"] == "old"
    assert new["recentEpisodes"][0]["episodeId"] == "new"


def test_reset_generations_do_not_mix_counts(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(generation=2, suspected=2))
    add_transition(repository, "run-1", "old", "started", generation=1)
    add_transition(repository, "run-1", "new-1", "started", generation=2)
    add_transition(repository, "run-1", "new-2", "started", generation=2)
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert metric(result, "SUSPECTED_CYCLE_EPISODE_COUNT")["value"] == 2
    assert {item["episodeId"] for item in result["recentEpisodes"]} == {"new-1", "new-2"}


def test_confirmed_resolution_duration_formula(repository: Repository) -> None:
    create_run(repository, status="completed", diag=diagnostics(suspected=1, persistent=1, resolved=1, ended=True))
    add_episode_history(repository, "run-1", "ep-1", terminal="resolved", persistent_tick=7, resolved_tick=19)
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    duration = metric(result, "AVERAGE_CONFIRMED_RESOLUTION_TICKS")
    assert duration["value"] == 12 and duration["sampleCount"] == 1


def test_no_resolution_sample_is_null_not_zero(repository: Repository) -> None:
    create_run(repository)
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    duration = metric(result, "AVERAGE_CONFIRMED_RESOLUTION_TICKS")
    assert duration["value"] is None and duration["sampleCount"] == 0


def test_incomplete_observation_is_degraded(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(completeness="INCOMPLETE"))
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert result["dataQuality"] == "DEGRADED"
    assert "OBSERVATION_INCOMPLETE" in result["qualityIssues"]


def test_recent_50_summary_does_not_cap_snapshot_counters(repository: Repository) -> None:
    recent = [episode_payload(f"ep-{index}", "RESOLVED") for index in range(50)]
    create_run(repository, diag=diagnostics(suspected=120, recent=recent))
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert metric(result, "SUSPECTED_CYCLE_EPISODE_COUNT")["value"] == 120
    assert len(result["recentEpisodes"]) == 0


def test_recent_100_block_evidence_is_not_used_as_full_hotspot_history(repository: Repository) -> None:
    diag = diagnostics(suspected=1, persistent=1)
    diag["lastBlockedObservations"] = [
        {"currentCellId": f"cell-r{index}-c1", "robotId": "ctu-1"}
        for index in range(100)
    ]
    create_run(repository, diag=diag)
    add_episode_history(repository, "run-1", "ep-1", cells=["cell-r7-c9"])
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert [item["cellId"] for item in result["hotspots"]] == ["cell-r7-c9"]


def test_snapshot_event_count_mismatch_is_explicit(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(suspected=2, pending=1))
    add_transition(repository, "run-1", "ep-1", "started")
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert result["dataQuality"] == "DEGRADED"
    assert "SNAPSHOT_EVENT_COUNT_MISMATCH" in result["qualityIssues"]
    assert "EVENT_PERSISTENCE_PENDING" in result["qualityIssues"]


@pytest.mark.parametrize("stop_reason", ["safety_limit", "runtime_error"])
def test_abnormal_stop_reasons_are_reported(repository: Repository, stop_reason: str) -> None:
    create_run(
        repository,
        status="stopped",
        stop_reason=stop_reason,
        diag=diagnostics(ended=True, end_reason=stop_reason),
    )
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert result["stopReason"] == stop_reason
    assert result["runStatus"] == "stopped"


def test_read_only_get_preserves_database_bytes_and_business_rows(repository: Repository) -> None:
    create_run(repository)
    database = Path(app_db.DB_PATH)
    before_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    with app_db.get_read_conn() as conn:
        before = tuple(conn.execute("SELECT id, status, payload, metrics FROM simulation_runs"))
        before_event_count = conn.execute("SELECT COUNT(*) FROM simulation_events").fetchone()[0]
    routes.get_simulation_deadlock_lifecycle_metrics("run-1")
    after_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    with app_db.get_read_conn() as conn:
        after = tuple(conn.execute("SELECT id, status, payload, metrics FROM simulation_runs"))
        after_event_count = conn.execute("SELECT COUNT(*) FROM simulation_events").fetchone()[0]
    assert before_hash == after_hash
    assert [tuple(row) for row in before] == [tuple(row) for row in after]
    assert before_event_count == after_event_count


def test_repeated_get_does_not_create_events(repository: Repository) -> None:
    create_run(repository)
    for _ in range(5):
        repository.get_deadlock_lifecycle_metrics("run-1")
    with app_db.get_read_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM simulation_events").fetchone()[0] == 0


def test_existing_five_metric_routes_are_still_registered() -> None:
    paths = {route.path for route in routes.router.routes}
    assert {
        "/api/v1/simulations/{run_id}/metrics/tasks",
        "/api/v1/simulations/{run_id}/metrics/history/quality",
        "/api/v1/simulations/{run_id}/metrics/ctu-contention",
        "/api/v1/simulations/{run_id}/metrics/path-planning",
        "/api/v1/simulations/{run_id}/metrics/conveyor-coordination",
    }.issubset(paths)


def test_invalid_generation_is_rejected(repository: Repository) -> None:
    create_run(repository)
    with pytest.raises(ValueError):
        repository.get_deadlock_lifecycle_metrics("run-1", generation=0)
    with pytest.raises(HTTPException) as error:
        routes.get_simulation_deadlock_lifecycle_metrics("run-1", generation=0)
    assert error.value.status_code == 422


def test_unknown_generation_returns_empty_generation_specific_contract(repository: Repository) -> None:
    create_run(repository)
    result = repository.get_deadlock_lifecycle_metrics("run-1", generation=9)
    assert result["generation"] == 9
    assert result["reason"] == "GENERATION_NOT_FOUND"
    assert metric(result, "PERSISTENT_WAIT_CYCLE_COUNT")["value"] is None


def test_hotspot_does_not_invent_floor(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(suspected=1, persistent=1))
    add_episode_history(repository, "run-1", "ep-1", cells=["cell-r109-c63"])
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    hotspot = result["hotspots"][0]
    assert (hotspot["row"], hotspot["col"], hotspot["floor"]) == (109, 63, None)
    assert hotspot["dataQuality"] == "DEGRADED"


def test_response_lists_are_bounded(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(suspected=25, persistent=25))
    for index in range(25):
        add_episode_history(repository, "run-1", f"ep-{index}", cells=[f"cell-r{index}-c1"])
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    assert len(result["hotspots"]) == 20
    assert len(result["recentEpisodes"]) == 20
    assert result["limits"]["fullEpisodeHistoryReturned"] is False


def test_old_generation_is_event_only_and_snapshot_metrics_are_null(repository: Repository) -> None:
    create_run(repository, diag=diagnostics(generation=2))
    add_episode_history(repository, "run-1", "old", generation=1)
    result = repository.get_deadlock_lifecycle_metrics("run-1", generation=1)
    assert result["dataQuality"] == "DEGRADED"
    assert metric(result, "PERSISTENT_WAIT_CYCLE_COUNT")["value"] == 1
    assert metric(result, "CUMULATIVE_BLOCKED_ROBOT_TICKS")["value"] is None


def test_every_metric_has_required_semantics(repository: Repository) -> None:
    create_run(repository)
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    required = {
        "unit", "sampleCount", "dataQuality", "reason", "acceptanceEligible",
        "source", "generation", "snapshotAsOfTick",
    }
    assert result["metrics"]
    assert all(required.issubset(item) for item in result["metrics"])
    assert all(item["acceptanceEligible"] is False for item in result["metrics"])


def test_response_is_json_serializable(repository: Repository) -> None:
    create_run(repository)
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    encoded = json.dumps(result, ensure_ascii=False)
    assert "deadlock-lifecycle-metrics-v1" in encoded


def test_large_episode_history_query_and_response_are_bounded(repository: Repository) -> None:
    episode_count = 1_000
    create_run(
        repository,
        status="completed",
        diag=diagnostics(
            last_tick=2_000,
            ended=True,
            suspected=episode_count,
            persistent=episode_count,
            resolved=episode_count,
        ),
    )
    created_at = app_db.utc_now()
    rows: list[tuple[str, int, str, str, str]] = []
    event_id = 0
    for index in range(episode_count):
        episode_id = f"bulk-{index}"
        cell_id = f"cell-r{index % 100}-c{index % 20}"
        for transition, tick in (("started", 2), ("persistent", 5), ("resolved", 12)):
            event_id += 1
            payload = {
                "contractVersion": "deadlock-episodes-v1",
                "runId": "run-1",
                "runInstanceId": "run-1:generation-1",
                "generation": 1,
                "transitionKey": f"run-1:generation-1:{episode_id}:{transition}",
                "episode": episode_payload(
                    episode_id,
                    {
                        "started": "SUSPECTED_CYCLE",
                        "persistent": "PERSISTENT_WAIT_CYCLE",
                        "resolved": "RESOLVED",
                    }[transition],
                    persistent_tick=5 if transition != "started" else None,
                    resolved_tick=12 if transition == "resolved" else None,
                    cells=[cell_id],
                ),
            }
            rows.append(
                (
                    "run-1",
                    tick,
                    LIFECYCLE_TYPES[transition],
                    app_db.encode_json(payload),
                    created_at,
                )
            )
    with app_db.get_conn() as connection:
        connection.executemany(
            """INSERT INTO simulation_events
               (run_id, tick, event_type, payload, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            rows,
        )

    started = time.perf_counter()
    result = repository.get_deadlock_lifecycle_metrics("run-1")
    elapsed_ms = (time.perf_counter() - started) * 1_000
    response_bytes = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    print(
        f"P2-2B benchmark events={len(rows)} "
        f"query_ms={elapsed_ms:.3f} response_bytes={response_bytes}"
    )
    assert metric(result, "PERSISTENT_WAIT_CYCLE_COUNT")["value"] == episode_count
    assert len(result["hotspots"]) <= 20
    assert len(result["recentEpisodes"]) <= 20
    assert elapsed_ms < 5_000
    assert response_bytes < 100_000
