from __future__ import annotations

from typing import Any

from ..core.repository import Repository


CONTRACT_VERSION = "algorithm-comparison-v1"
TERMINAL_STATUSES = {"completed", "stopped"}


def _metric_value(payload: dict[str, Any] | None, key: str) -> float | None:
    metric = ((payload or {}).get("totalMetrics") or {}).get(key)
    value = metric.get("value") if isinstance(metric, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _deadlock_value(payload: dict[str, Any] | None, metric_id: str) -> float | None:
    for metric in (payload or {}).get("metrics") or []:
        if isinstance(metric, dict) and metric.get("metricId") == metric_id:
            value = metric.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def _run_summary(repo: Repository, run_id: str) -> dict[str, Any] | None:
    saved = repo.get_run_read_only(run_id)
    if saved is None:
        return None
    state = saved.get("payload") if isinstance(saved.get("payload"), dict) else {}
    experiment = state.get("algorithmExperimentContract")
    tasks = [
        item for item in state.get("tasks", [])
        if isinstance(item, dict) and not item.get("parentId")
    ]
    completed = [item for item in tasks if item.get("status") == "completed"]
    terminal_work = [
        item for item in tasks
        if item.get("status") in {"completed", "failed", "exception", "cancelled"}
    ]
    sim_tick = ((state.get("motionSpeedContract") or {}).get("simTickSeconds"))
    lifecycle_seconds = []
    if isinstance(sim_tick, (int, float)) and not isinstance(sim_tick, bool):
        for task in completed:
            start = task.get("createdTick")
            end = task.get("completedTick")
            if (
                isinstance(start, int) and not isinstance(start, bool)
                and isinstance(end, int) and not isinstance(end, bool)
                and end >= start
            ):
                lifecycle_seconds.append((end - start) * float(sim_tick))

    motion = repo.get_motion_distance_metrics(run_id)
    deadlock = repo.get_deadlock_lifecycle_metrics(run_id)
    planning = state.get("planningEvidence") if isinstance(state.get("planningEvidence"), list) else []
    planning_durations = [
        float(item["planningDurationMs"])
        for item in planning
        if isinstance(item, dict)
        and isinstance(item.get("planningDurationMs"), (int, float))
        and not isinstance(item.get("planningDurationMs"), bool)
    ]
    counters = state.get("performanceCounters") if isinstance(state.get("performanceCounters"), dict) else {}
    total_tasks = len(tasks)
    completion_rate = len(completed) / total_tasks * 100.0 if total_tasks else 0.0
    motion_complete = (
        isinstance(motion, dict)
        and motion.get("dataQuality") in {"COMPLETE", "VALID", "PASS"}
        and _metric_value(motion, "totalHorizontalMeters") is not None
    )
    return {
        "runId": run_id,
        "status": saved.get("status"),
        "stopReason": state.get("stopReason"),
        "experimentContract": experiment,
        "pathPolicy": (experiment or {}).get("pathPolicy"),
        "assignmentPolicy": (experiment or {}).get("assignmentPolicy"),
        "totalActualHorizontalMeters": _metric_value(motion, "totalHorizontalMeters"),
        "ctuHorizontalMeters": _metric_value(motion, "ctuHorizontalMeters"),
        "shuttleHorizontalMeters": _metric_value(motion, "shuttleHorizontalMeters"),
        "liftVerticalMeters": _metric_value(motion, "shuttleLiftVerticalMeters"),
        "completedTaskCount": len(completed),
        "totalTaskCount": total_tasks,
        "terminalTaskCount": len(terminal_work),
        "taskCompletionRatePercent": float(completion_rate),
        "simulatedSeconds": state.get("simulatedSeconds"),
        "averageTaskLifecycleSeconds": (
            sum(lifecycle_seconds) / len(lifecycle_seconds) if lifecycle_seconds else None
        ),
        "blockedRobotTicks": int(counters.get("blockedRobotTicks") or 0),
        "movementRobotTicks": int(counters.get("movementRobotTicks") or 0),
        "physicalTravelWaitRobotTicks": int(counters.get("physicalTravelWaitRobotTicks") or 0),
        "persistentCycleEpisodeCount": _deadlock_value(deadlock, "PERSISTENT_WAIT_CYCLE_COUNT"),
        "confirmedResolvedEpisodeCount": _deadlock_value(deadlock, "CONFIRMED_RESOLVED_EPISODE_COUNT"),
        "planningDurationMs": sum(planning_durations) if planning_durations else 0.0,
        "planningCallCount": len(planning),
        "replanCount": sum(1 for item in planning if isinstance(item, dict) and item.get("replanId")),
        "planningEvidence": planning,
        "motionDataQuality": (motion or {}).get("dataQuality"),
        "deadlockDataQuality": (deadlock or {}).get("dataQuality"),
        "dataComplete": bool(
            isinstance(experiment, dict)
            and saved.get("status") in TERMINAL_STATUSES
            and state.get("stopReason") != "safety_limit"
            and motion_complete
            and len(terminal_work) == total_tasks
        ),
    }


def _improvement(baseline: float | None, candidate: float | None) -> dict[str, float | None]:
    if baseline is None or candidate is None or baseline == 0:
        return {"saved": None, "percent": None}
    saved = baseline - candidate
    return {"saved": saved, "percent": saved / baseline * 100.0}


def build_run_performance_metrics(repo: Repository, run_id: str) -> dict[str, Any] | None:
    saved = repo.get_run_read_only(run_id)
    if saved is None:
        return None
    state = saved.get("payload") if isinstance(saved.get("payload"), dict) else {}
    speed = state.get("motionSpeedContract")
    tasks = [
        item for item in state.get("tasks", [])
        if isinstance(item, dict) and not item.get("parentId")
    ]
    completed = sum(1 for item in tasks if item.get("status") == "completed")
    counters = state.get("performanceCounters") if isinstance(state.get("performanceCounters"), dict) else {}
    motion = repo.get_motion_distance_metrics(run_id)
    horizontal = _metric_value(motion, "totalHorizontalMeters")
    tick_seconds = (
        float(speed["simTickSeconds"])
        if isinstance(speed, dict)
        and isinstance(speed.get("simTickSeconds"), (int, float))
        and not isinstance(speed.get("simTickSeconds"), bool)
        else None
    )
    movement_ticks = int(counters.get("movementRobotTicks") or 0)
    blocked_ticks = int(counters.get("blockedRobotTicks") or 0)
    physical_wait_ticks = int(counters.get("physicalTravelWaitRobotTicks") or 0)
    # A low-speed edge is physically in progress while distance credit is being
    # accumulated.  Count those ticks with the eventual commit tick as motion;
    # otherwise distance / time could exceed the configured physical speed.
    movement_seconds = (
        (movement_ticks + physical_wait_ticks) * tick_seconds
        if tick_seconds is not None
        else None
    )
    waiting_seconds = blocked_ticks * tick_seconds if tick_seconds is not None else None
    return {
        "contractVersion": "run-performance-metrics-v1",
        "runId": run_id,
        "status": saved.get("status"),
        "motionSpeedContract": speed,
        "simulatedSeconds": state.get("simulatedSeconds"),
        "wallClockSeconds": state.get("wallClockSeconds"),
        "playbackMultiplier": state.get("playbackMultiplier", state.get("speedMultiplier")),
        "movementRobotSeconds": movement_seconds,
        "waitingRobotSeconds": waiting_seconds,
        "averageEffectiveHorizontalSpeedMetersPerSecond": (
            horizontal / movement_seconds
            if horizontal is not None and movement_seconds is not None and movement_seconds > 0
            else None
        ),
        "completedTaskCount": completed,
        "totalTaskCount": len(tasks),
        "taskCompletionRatePercent": completed / len(tasks) * 100.0 if tasks else 0.0,
        "dataQuality": "COMPLETE" if isinstance(speed, dict) else "UNAVAILABLE",
        "reason": None if isinstance(speed, dict) else "MOTION_SPEED_CONTRACT_UNAVAILABLE",
        "semantics": {
            "movementTime": "committed transition ticks plus physical edge-credit accumulation ticks",
            "waitingTime": "logical robot ticks blocked by occupancy, reservation, or active yield",
            "wallClockExcludedFromEffectiveSpeed": True,
            "acceptanceEligible": False,
        },
    }


def compare_algorithm_experiment_runs(
    repo: Repository,
    baseline_run_id: str,
    candidate_run_id: str,
) -> dict[str, Any] | None:
    baseline = _run_summary(repo, baseline_run_id)
    candidate = _run_summary(repo, candidate_run_id)
    if baseline is None or candidate is None:
        return None
    reasons: list[str] = []
    left = baseline.get("experimentContract")
    right = candidate.get("experimentContract")
    if not isinstance(left, dict) or not isinstance(right, dict):
        reasons.append("EXPERIMENT_CONTRACT_MISSING")
    else:
        if left.get("scenarioSignature") != right.get("scenarioSignature"):
            reasons.append("SCENARIO_SIGNATURE_MISMATCH")
        if left.get("variableDimension") != right.get("variableDimension"):
            reasons.append("VARIABLE_DIMENSION_MISMATCH")
        dimension = left.get("variableDimension")
        if dimension and left.get(dimension) == right.get(dimension):
            reasons.append("ALGORITHM_SELECTION_IDENTICAL")
    if not baseline["dataComplete"] or not candidate["dataComplete"]:
        reasons.append("RUN_DATA_INCOMPLETE")
    if baseline["completedTaskCount"] != candidate["completedTaskCount"]:
        reasons.append("COMPLETED_WORK_SCOPE_MISMATCH")
    comparable = not reasons

    distance = _improvement(
        baseline["totalActualHorizontalMeters"], candidate["totalActualHorizontalMeters"]
    ) if comparable else {"saved": None, "percent": None}
    duration = _improvement(
        float(baseline["simulatedSeconds"]) if baseline["simulatedSeconds"] is not None else None,
        float(candidate["simulatedSeconds"]) if candidate["simulatedSeconds"] is not None else None,
    ) if comparable else {"saved": None, "percent": None}
    planning = _improvement(
        float(baseline["planningDurationMs"]), float(candidate["planningDurationMs"])
    ) if comparable else {"saved": None, "percent": None}
    return {
        "contractVersion": CONTRACT_VERSION,
        "baseline": baseline,
        "candidate": candidate,
        "comparable": comparable,
        "comparisonReasons": reasons,
        "distanceSavedMeters": distance["saved"],
        "distanceImprovementPercent": distance["percent"],
        "simulatedSecondsSaved": duration["saved"],
        "simulatedTimeImprovementPercent": duration["percent"],
        "planningDurationSavedMs": planning["saved"],
        "planningTimeImprovementPercent": planning["percent"],
        "blockedRobotTicksChange": (
            candidate["blockedRobotTicks"] - baseline["blockedRobotTicks"] if comparable else None
        ),
        "semantics": {
            "distanceFormula": "(baselineActualMeters - candidateActualMeters) / baselineActualMeters * 100",
            "positivePercentMeaning": "candidate used less of the named metric",
            "syntheticWaveDistanceUsed": False,
        },
    }
