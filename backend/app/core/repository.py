from __future__ import annotations

import math
import re
from typing import Any

from ..analytics.motion_distance_metrics import aggregate_motion_distance_metrics
from ..analytics.throughput_metrics import DELIVERY_EVENT_TYPE, build_throughput_metrics
from .db import decode_json, encode_json, get_conn, get_read_conn, row_to_dict, utc_now


def _classify_metric_task_parentage(task: dict[str, Any]) -> str:
    """Classify a persisted task without guessing from malformed parentId values."""

    if "parentId" not in task or task["parentId"] is None or task["parentId"] == "":
        return "parent"
    parent_id = task["parentId"]
    if isinstance(parent_id, str) and parent_id.strip():
        return "child"
    return "invalid"


def _deduplicate_metric_tasks(tasks: list[Any]) -> tuple[list[dict[str, Any]], int, int]:
    """Return unambiguous tasks plus invalid-record and conflicting-id counts."""

    by_id: dict[str, dict[str, Any]] = {}
    conflicting_ids: set[str] = set()
    invalid_records = 0
    for item in tasks:
        if not isinstance(item, dict):
            invalid_records += 1
            continue
        task_id = item.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            invalid_records += 1
            continue
        if _classify_metric_task_parentage(item) == "invalid":
            invalid_records += 1
            continue
        if task_id in conflicting_ids:
            continue
        previous = by_id.get(task_id)
        if previous is None:
            by_id[task_id] = item
        elif previous != item:
            conflicting_ids.add(task_id)
            by_id.pop(task_id, None)
    return list(by_id.values()), invalid_records, len(conflicting_ids)


def _compute_task_duration_summary(
    tasks: list[dict[str, Any]],
    first_assignment_ticks: dict[str, int],
) -> dict[str, dict[str, Any]]:
    """Compute Tick durations from validated final tasks and historical assignment evidence."""

    result: dict[str, dict[str, Any]] = {
        "parent": {
            "completedCount": 0,
            "createDurations": [],
            "createExcludedReasonCounts": {},
            "firstAssignmentDurations": [],
            "firstAssignmentExcludedReasonCounts": {},
        },
        "child": {
            "completedCount": 0,
            "createDurations": [],
            "createExcludedReasonCounts": {},
            "firstAssignmentDurations": [],
            "firstAssignmentExcludedReasonCounts": {},
        },
    }

    def valid_tick(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def exclude(bucket: dict[str, Any], key: str, reason: str) -> None:
        reasons = bucket[key]
        reasons[reason] = reasons.get(reason, 0) + 1

    for task in tasks:
        if task.get("status") != "completed":
            continue
        parentage = _classify_metric_task_parentage(task)
        if parentage not in result:
            continue
        bucket = result[parentage]
        bucket["completedCount"] += 1
        created_tick = task.get("createdTick")
        completed_tick = task.get("completedTick")

        if not valid_tick(created_tick) or not valid_tick(completed_tick):
            exclude(bucket, "createExcludedReasonCounts", "MISSING_OR_INVALID_TICK")
        else:
            duration = completed_tick - created_tick
            if duration < 0:
                exclude(bucket, "createExcludedReasonCounts", "NEGATIVE_DURATION")
            else:
                bucket["createDurations"].append(duration)

        first_assignment_tick = first_assignment_ticks.get(task["id"])
        if first_assignment_tick is None:
            exclude(
                bucket,
                "firstAssignmentExcludedReasonCounts",
                "FIRST_ASSIGNMENT_EVIDENCE_MISSING",
            )
        elif not valid_tick(completed_tick):
            exclude(
                bucket,
                "firstAssignmentExcludedReasonCounts",
                "MISSING_OR_INVALID_COMPLETED_TICK",
            )
        else:
            duration = completed_tick - first_assignment_tick
            if duration < 0:
                exclude(
                    bucket,
                    "firstAssignmentExcludedReasonCounts",
                    "NEGATIVE_DURATION",
                )
            else:
                bucket["firstAssignmentDurations"].append(duration)

    return result


_DEADLOCK_LIFECYCLE_EVENT_TYPES = (
    "deadlock_episode_started",
    "deadlock_episode_persistent",
    "deadlock_mitigation_observed",
    "deadlock_episode_resolved",
    "deadlock_episode_unverified",
    "deadlock_episode_unresolved",
    "deadlock_diagnostics_failed",
)
_DEADLOCK_CELL_PATTERN = re.compile(r"^cell-r(-?\d+)-c(-?\d+)$")
_DEADLOCK_RECENT_EPISODE_LIMIT = 20
_DEADLOCK_HOTSPOT_LIMIT = 20


def _valid_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _deadlock_metric(
    metric_id: str,
    display_name: str,
    value: int | float | None,
    unit: str,
    sample_count: int,
    data_quality: str,
    reason: str | None,
    source: str,
    generation: int | None,
    snapshot_as_of_tick: int | None,
) -> dict[str, Any]:
    return {
        "metricId": metric_id,
        "displayName": display_name,
        "value": value,
        "unit": unit,
        "sampleCount": sample_count,
        "dataQuality": data_quality,
        "reason": reason,
        "acceptanceEligible": False,
        "source": source,
        "generation": generation,
        "snapshotAsOfTick": snapshot_as_of_tick,
    }


def _build_deadlock_lifecycle_metrics(
    run_id: str,
    run_row: Any,
    event_rows: list[Any],
    requested_generation: int | None,
) -> dict[str, Any]:
    """Build one generation's bounded read-only deadlock lifecycle summary."""

    try:
        state = decode_json(run_row["payload"])
    except (TypeError, ValueError):
        state = None
    state = state if isinstance(state, dict) else {}
    diagnostics = state.get("deadlockDiagnosticsV1")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else None
    snapshot_generation = (
        diagnostics.get("generation")
        if diagnostics and _valid_nonnegative_int(diagnostics.get("generation"))
        and diagnostics.get("generation") >= 1
        else None
    )

    decoded_events: list[tuple[Any, dict[str, Any] | None]] = []
    event_generations: set[int] = set()
    invalid_payload_count = 0
    for row in event_rows:
        try:
            payload = decode_json(row["payload"])
        except (TypeError, ValueError):
            payload = None
        if not isinstance(payload, dict):
            payload = None
            invalid_payload_count += 1
        else:
            generation = payload.get("generation")
            if _valid_nonnegative_int(generation) and generation >= 1:
                event_generations.add(generation)
        decoded_events.append((row, payload))

    available_generations = set(event_generations)
    if snapshot_generation is not None:
        available_generations.add(snapshot_generation)
    target_generation = (
        requested_generation
        if requested_generation is not None
        else snapshot_generation
        if snapshot_generation is not None
        else max(event_generations)
        if event_generations
        else None
    )
    snapshot_for_generation = (
        diagnostics
        if diagnostics is not None and snapshot_generation == target_generation
        else None
    )

    selected_events: list[tuple[Any, dict[str, Any]]] = []
    missing_generation_event_count = 0
    for row, payload in decoded_events:
        if payload is None:
            continue
        generation = payload.get("generation")
        if not _valid_nonnegative_int(generation) or generation < 1:
            missing_generation_event_count += 1
            continue
        if generation == target_generation:
            selected_events.append((row, payload))

    unique_events: dict[str, tuple[Any, dict[str, Any]]] = {}
    duplicate_transition_count = 0
    conflicting_transition_count = 0
    invalid_transition_count = 0
    for row, payload in selected_events:
        transition_key = payload.get("transitionKey")
        if not isinstance(transition_key, str) or not transition_key.strip():
            invalid_transition_count += 1
            continue
        previous = unique_events.get(transition_key)
        if previous is not None:
            duplicate_transition_count += 1
            if previous[1] != payload:
                conflicting_transition_count += 1
            if int(row["id"]) > int(previous[0]["id"]):
                unique_events[transition_key] = (row, payload)
            continue
        unique_events[transition_key] = (row, payload)
    ordered_events = sorted(unique_events.values(), key=lambda item: int(item[0]["id"]))

    episode_sets: dict[str, set[str]] = {
        "deadlock_episode_started": set(),
        "deadlock_episode_persistent": set(),
        "deadlock_episode_resolved": set(),
        "deadlock_episode_unverified": set(),
        "deadlock_episode_unresolved": set(),
    }
    episode_latest: dict[str, tuple[int, int, dict[str, Any]]] = {}
    invalid_episode_event_count = 0
    diagnostic_failure_event_count = 0
    for row, payload in ordered_events:
        event_type = row["event_type"]
        if event_type == "deadlock_diagnostics_failed":
            diagnostic_failure_event_count += 1
            continue
        episode = payload.get("episode")
        episode_id = episode.get("episodeId") if isinstance(episode, dict) else None
        if not isinstance(episode_id, str) or not episode_id.strip():
            invalid_episode_event_count += 1
            continue
        if event_type in episode_sets:
            episode_sets[event_type].add(episode_id)
        episode_latest[episode_id] = (int(row["id"]), int(row["tick"]), episode)

    started_ids = episode_sets["deadlock_episode_started"]
    persistent_ids = episode_sets["deadlock_episode_persistent"]
    resolved_ids = episode_sets["deadlock_episode_resolved"]
    unresolved_ids = episode_sets["deadlock_episode_unresolved"]
    unverified_ids = episode_sets["deadlock_episode_unverified"]
    terminal_ids = resolved_ids | unresolved_ids | unverified_ids
    open_ids = started_ids - terminal_ids

    duration_samples: list[int] = []
    invalid_duration_count = 0
    for episode_id in sorted(resolved_ids):
        latest = episode_latest.get(episode_id)
        episode = latest[2] if latest else None
        persistent_tick = episode.get("persistentAtTick") if isinstance(episode, dict) else None
        resolved_tick = episode.get("resolvedAtTick") if isinstance(episode, dict) else None
        if not _valid_nonnegative_int(persistent_tick) or not _valid_nonnegative_int(resolved_tick):
            invalid_duration_count += 1
            continue
        duration = resolved_tick - persistent_tick
        if duration < 0:
            invalid_duration_count += 1
            continue
        duration_samples.append(duration)

    quality_issues: list[str] = []
    if invalid_payload_count:
        quality_issues.append("INVALID_EVENT_PAYLOAD")
    if missing_generation_event_count:
        quality_issues.append("EVENT_GENERATION_MISSING")
    if invalid_transition_count or invalid_episode_event_count:
        quality_issues.append("INVALID_LIFECYCLE_EVENT")
    if conflicting_transition_count:
        quality_issues.append("CONFLICTING_TRANSITION_KEY")
    if duplicate_transition_count:
        quality_issues.append("DUPLICATE_TRANSITION_KEY_DEDUPLICATED")
    if invalid_duration_count:
        quality_issues.append("INVALID_RESOLUTION_DURATION")
    if diagnostic_failure_event_count:
        quality_issues.append("DIAGNOSTIC_FAILURE_EVENT_PRESENT")

    active_episode_ids: set[str] = set()
    invalid_active_episode_count = 0
    duplicate_active_episode_count = 0
    if snapshot_for_generation is not None:
        active_episodes = snapshot_for_generation.get("activeEpisodes")
        if isinstance(active_episodes, list):
            for episode in active_episodes:
                episode_id = episode.get("episodeId") if isinstance(episode, dict) else None
                if not isinstance(episode_id, str) or not episode_id.strip():
                    invalid_active_episode_count += 1
                elif episode_id in active_episode_ids:
                    duplicate_active_episode_count += 1
                else:
                    active_episode_ids.add(episode_id)
        else:
            invalid_active_episode_count += 1
    if invalid_active_episode_count:
        quality_issues.append("INVALID_ACTIVE_EPISODE_SUMMARY")
    if duplicate_active_episode_count:
        quality_issues.append("DUPLICATE_ACTIVE_EPISODE_DEDUPLICATED")

    generation_available = target_generation is not None and (
        snapshot_for_generation is not None or target_generation in event_generations
    )
    if target_generation is None:
        base_quality = "MISSING_SOURCE"
        base_reason = "DEADLOCK_DIAGNOSTICS_V1_NOT_AVAILABLE"
    elif not generation_available:
        base_quality = "MISSING_SOURCE"
        base_reason = "GENERATION_NOT_FOUND"
    elif snapshot_for_generation is None:
        base_quality = "DEGRADED"
        base_reason = "SNAPSHOT_NOT_AVAILABLE_FOR_GENERATION"
        quality_issues.append("EVENT_ONLY_GENERATION")
    else:
        completeness = snapshot_for_generation.get("observationCompleteness")
        if completeness != "COMPLETE":
            base_quality = "DEGRADED"
            base_reason = "OBSERVATION_INCOMPLETE"
            quality_issues.append("OBSERVATION_INCOMPLETE")
        else:
            base_quality = "GOOD"
            base_reason = None

    snapshot_as_of_tick = (
        snapshot_for_generation.get("lastProcessedTick")
        if snapshot_for_generation
        and _valid_nonnegative_int(snapshot_for_generation.get("lastProcessedTick"))
        else None
    )
    latest_event = ordered_events[-1] if ordered_events else None
    latest_event_id = int(latest_event[0]["id"]) if latest_event else None
    latest_event_tick = int(latest_event[0]["tick"]) if latest_event else None
    latest_transition_key = latest_event[1].get("transitionKey") if latest_event else None
    event_persistence = (
        snapshot_for_generation.get("eventPersistence")
        if snapshot_for_generation and isinstance(snapshot_for_generation.get("eventPersistence"), dict)
        else {}
    )
    pending_transition_count = event_persistence.get("pendingTransitionCount")
    pending_transition_count = pending_transition_count if _valid_nonnegative_int(pending_transition_count) else None
    acknowledged_transition_key = event_persistence.get("lastAcknowledgedTransitionKey")
    if pending_transition_count:
        quality_issues.append("EVENT_PERSISTENCE_PENDING")
    if (
        isinstance(acknowledged_transition_key, str)
        and acknowledged_transition_key
        and acknowledged_transition_key not in unique_events
    ):
        quality_issues.append("ACKNOWLEDGED_TRANSITION_NOT_FOUND")

    def snapshot_count(name: str) -> int | None:
        value = snapshot_for_generation.get(name) if snapshot_for_generation else None
        return value if _valid_nonnegative_int(value) else None

    snapshot_counts = {
        "suspectedCycleCount": snapshot_count("suspectedCycleCount"),
        "persistentCycleCount": snapshot_count("persistentCycleCount"),
        "resolvedCycleCount": snapshot_count("resolvedCycleCount"),
        "unresolvedCycleCount": snapshot_count("unresolvedCycleCount"),
        "unverifiedCycleCount": snapshot_count("unverifiedCycleCount"),
    }
    event_counts = {
        "suspectedCycleCount": len(started_ids),
        "persistentCycleCount": len(persistent_ids),
        "resolvedCycleCount": len(resolved_ids),
        "unresolvedCycleCount": len(unresolved_ids),
        "unverifiedCycleCount": len(unverified_ids),
    }
    if snapshot_for_generation is not None:
        for name, snapshot_value in snapshot_counts.items():
            if snapshot_value is not None and snapshot_value != event_counts[name]:
                quality_issues.append("SNAPSHOT_EVENT_COUNT_MISMATCH")
                break

    quality_issues = list(dict.fromkeys(quality_issues))
    if base_quality == "GOOD" and quality_issues:
        base_quality = "DEGRADED"
        base_reason = quality_issues[0]
    elif base_reason is None and quality_issues:
        base_reason = quality_issues[0]

    source_snapshot = "RUN_PAYLOAD.deadlockDiagnosticsV1"
    source_events = "simulation_events.lifecycle_transition_key_deduplicated"
    missing_snapshot_reason = (
        base_reason
        if target_generation is None or not generation_available
        else "SNAPSHOT_NOT_AVAILABLE_FOR_GENERATION"
    )
    processed_ticks = None
    if snapshot_for_generation and isinstance(snapshot_for_generation.get("performance"), dict):
        candidate = snapshot_for_generation["performance"].get("processedTicks")
        processed_ticks = candidate if _valid_nonnegative_int(candidate) else None
    robot_stats = (
        snapshot_for_generation.get("robotBlockStats")
        if snapshot_for_generation and isinstance(snapshot_for_generation.get("robotBlockStats"), list)
        else []
    )
    valid_robot_stats = [item for item in robot_stats if isinstance(item, dict)]
    max_continuous_samples = [
        item.get("maxContinuousBlockedTicks")
        for item in valid_robot_stats
        if _valid_nonnegative_int(item.get("maxContinuousBlockedTicks"))
    ]
    current_blocked_count = sum(
        1
        for item in valid_robot_stats
        if _valid_nonnegative_int(item.get("continuousBlockedTicks"))
        and item["continuousBlockedTicks"] > 0
    )

    def count_value(snapshot_name: str, event_name: str) -> tuple[int | None, str, str | None]:
        snapshot_value = snapshot_counts.get(snapshot_name)
        if snapshot_value is not None:
            return snapshot_value, source_snapshot, base_reason
        if generation_available and target_generation in event_generations:
            return event_counts[event_name], source_events, "SNAPSHOT_NOT_AVAILABLE_FOR_GENERATION"
        return None, source_snapshot, missing_snapshot_reason

    suspected_count, suspected_source, suspected_reason = count_value(
        "suspectedCycleCount", "suspectedCycleCount"
    )
    persistent_count, persistent_source, persistent_reason = count_value(
        "persistentCycleCount", "persistentCycleCount"
    )
    resolved_count, resolved_source, resolved_reason = count_value(
        "resolvedCycleCount", "resolvedCycleCount"
    )
    unresolved_count, unresolved_source, unresolved_reason = count_value(
        "unresolvedCycleCount", "unresolvedCycleCount"
    )
    unverified_count, unverified_source, unverified_reason = count_value(
        "unverifiedCycleCount", "unverifiedCycleCount"
    )
    open_count = (
        len(active_episode_ids)
        if snapshot_for_generation is not None
        else len(open_ids)
        if generation_available and target_generation in event_generations
        else None
    )
    open_source = source_snapshot if snapshot_for_generation else source_events
    open_reason = base_reason if snapshot_for_generation else (
        "SNAPSHOT_NOT_AVAILABLE_FOR_GENERATION" if generation_available else missing_snapshot_reason
    )

    blocked_robot_ticks = snapshot_count("blockedRobotTicks")
    block_episode_count = snapshot_count("blockEpisodeCount")
    average_resolution_ticks = (
        sum(duration_samples) / len(duration_samples) if duration_samples else None
    )
    average_reason = (
        None
        if duration_samples and not invalid_duration_count
        else "PARTIAL_VALID_RESOLUTION_SAMPLES"
        if duration_samples
        else "NO_CONFIRMED_RESOLUTION_SAMPLES"
    )
    run_status = run_row["status"]
    stop_reason = state.get("stopReason")
    run_ended = bool(snapshot_for_generation and snapshot_for_generation.get("ended"))
    ratio_value: float | None = None
    if not run_ended or run_status not in {"completed", "stopped"}:
        ratio_reason = "RUN_NOT_TERMINAL"
    elif snapshot_for_generation is None:
        ratio_reason = "SNAPSHOT_NOT_AVAILABLE_FOR_GENERATION"
    elif snapshot_for_generation.get("observationCompleteness") != "COMPLETE":
        ratio_reason = "OBSERVATION_INCOMPLETE"
    elif "SNAPSHOT_EVENT_COUNT_MISMATCH" in quality_issues:
        ratio_reason = "SNAPSHOT_EVENT_COUNT_MISMATCH"
    elif persistent_count is None or persistent_count == 0:
        ratio_reason = "NO_PERSISTENT_EPISODES"
    elif resolved_count is None:
        ratio_reason = "RESOLVED_EPISODE_COUNT_MISSING"
    else:
        ratio_value = resolved_count / persistent_count * 100.0
        ratio_reason = "THRESHOLDS_NOT_FIELD_CALIBRATED"

    metrics = [
        _deadlock_metric(
            "CUMULATIVE_BLOCKED_ROBOT_TICKS",
            "累计阻塞车辆Tick",
            blocked_robot_ticks,
            "robot·Tick",
            processed_ticks or 0,
            base_quality if blocked_robot_ticks is not None else "MISSING_SOURCE",
            base_reason if blocked_robot_ticks is not None else missing_snapshot_reason,
            source_snapshot,
            target_generation,
            snapshot_as_of_tick,
        ),
        _deadlock_metric(
            "BLOCK_EPISODE_COUNT",
            "阻塞事件次数",
            block_episode_count,
            "event",
            block_episode_count or 0,
            base_quality if block_episode_count is not None else "MISSING_SOURCE",
            base_reason if block_episode_count is not None else missing_snapshot_reason,
            source_snapshot,
            target_generation,
            snapshot_as_of_tick,
        ),
        _deadlock_metric(
            "CURRENT_BLOCKED_ROBOT_COUNT",
            "当前或最后快照阻塞车辆数",
            current_blocked_count if snapshot_for_generation is not None else None,
            "robot",
            len(valid_robot_stats),
            base_quality if snapshot_for_generation is not None else "MISSING_SOURCE",
            base_reason if snapshot_for_generation is not None else missing_snapshot_reason,
            source_snapshot,
            target_generation,
            snapshot_as_of_tick,
        ),
        _deadlock_metric(
            "MAX_CONTINUOUS_BLOCKED_TICKS",
            "单车最长连续阻塞Tick",
            max(max_continuous_samples) if max_continuous_samples else None,
            "Tick",
            len(max_continuous_samples),
            base_quality if max_continuous_samples else "MISSING_SOURCE",
            base_reason if max_continuous_samples else missing_snapshot_reason,
            source_snapshot,
            target_generation,
            snapshot_as_of_tick,
        ),
    ]

    count_metrics = (
        ("SUSPECTED_CYCLE_EPISODE_COUNT", "疑似循环Episode数", suspected_count, suspected_source, suspected_reason),
        ("PERSISTENT_WAIT_CYCLE_COUNT", "持续循环Episode数", persistent_count, persistent_source, persistent_reason),
        ("OPEN_CYCLE_EPISODE_COUNT", "当前开放Episode数", open_count, open_source, open_reason),
        ("CONFIRMED_RESOLVED_EPISODE_COUNT", "已确认恢复Episode数", resolved_count, resolved_source, resolved_reason),
        ("UNRESOLVED_AT_RUN_END_COUNT", "Run结束未解除Episode数", unresolved_count, unresolved_source, unresolved_reason),
        ("ENDED_UNVERIFIED_EPISODE_COUNT", "证据不足Episode数", unverified_count, unverified_source, unverified_reason),
    )
    for metric_id, display_name, value, source, reason in count_metrics:
        metrics.append(
            _deadlock_metric(
                metric_id,
                display_name,
                value,
                "episode",
                value or 0,
                base_quality if value is not None else "MISSING_SOURCE",
                reason,
                source,
                target_generation,
                snapshot_as_of_tick,
            )
        )
    metrics.extend(
        [
            _deadlock_metric(
                "AVERAGE_CONFIRMED_RESOLUTION_TICKS",
                "平均确认恢复耗时",
                average_resolution_ticks,
                "Tick",
                len(duration_samples),
                (
                    "DEGRADED" if invalid_duration_count else base_quality
                ) if duration_samples else "MISSING_SOURCE",
                average_reason,
                source_events,
                target_generation,
                latest_event_tick,
            ),
            _deadlock_metric(
                "CONFIRMED_RESOLUTION_RATIO",
                "确认恢复比例（非正式成功率）",
                ratio_value,
                "%",
                persistent_count or 0,
                base_quality if ratio_value is not None else "MISSING_SOURCE",
                ratio_reason,
                f"{source_snapshot}+{source_events}",
                target_generation,
                snapshot_as_of_tick,
            ),
        ]
    )

    robot_blocking: list[dict[str, Any]] = []
    for item in sorted(valid_robot_stats, key=lambda value: str(value.get("robotId") or "")):
        robot_id = item.get("robotId")
        if not isinstance(robot_id, str) or not robot_id:
            continue
        robot_quality = item.get("dataQuality") if item.get("dataQuality") in {"COMPLETE", "INCOMPLETE"} else "INCOMPLETE"
        metric_quality = base_quality if robot_quality == "COMPLETE" else "DEGRADED"
        metric_reason = base_reason if robot_quality == "COMPLETE" else "ROBOT_OBSERVATION_INCOMPLETE"
        robot_blocking.append(
            {
                "robotId": robot_id,
                "blockerRobotId": item.get("blockerRobotId"),
                "blockedCellId": item.get("blockedCellId"),
                "nextCellId": item.get("nextCellId"),
                "metrics": [
                    _deadlock_metric(
                        "ROBOT_CONTINUOUS_BLOCKED_TICKS",
                        "当前连续阻塞Tick",
                        item.get("continuousBlockedTicks") if _valid_nonnegative_int(item.get("continuousBlockedTicks")) else None,
                        "Tick",
                        1,
                        metric_quality,
                        metric_reason,
                        f"{source_snapshot}.robotBlockStats",
                        target_generation,
                        snapshot_as_of_tick,
                    ),
                    _deadlock_metric(
                        "ROBOT_MAX_CONTINUOUS_BLOCKED_TICKS",
                        "单车最长连续阻塞Tick",
                        item.get("maxContinuousBlockedTicks") if _valid_nonnegative_int(item.get("maxContinuousBlockedTicks")) else None,
                        "Tick",
                        1,
                        metric_quality,
                        metric_reason,
                        f"{source_snapshot}.robotBlockStats",
                        target_generation,
                        snapshot_as_of_tick,
                    ),
                    _deadlock_metric(
                        "ROBOT_CUMULATIVE_BLOCKED_TICKS",
                        "单车累计阻塞Tick",
                        item.get("cumulativeBlockedRobotTicks") if _valid_nonnegative_int(item.get("cumulativeBlockedRobotTicks")) else None,
                        "robot·Tick",
                        1,
                        metric_quality,
                        metric_reason,
                        f"{source_snapshot}.robotBlockStats",
                        target_generation,
                        snapshot_as_of_tick,
                    ),
                ],
                "dataQuality": metric_quality,
                "reason": metric_reason,
                "acceptanceEligible": False,
                "source": f"{source_snapshot}.robotBlockStats",
                "generation": target_generation,
                "snapshotAsOfTick": snapshot_as_of_tick,
            }
        )

    persistent_episodes = {
        episode_id: episode_latest[episode_id][2]
        for episode_id in persistent_ids
        if episode_id in episode_latest
    }
    hotspot_accumulator: dict[str, dict[str, Any]] = {}
    for episode_id, episode in persistent_episodes.items():
        participants = {
            robot_id
            for robot_id in episode.get("participantRobotIds") or []
            if isinstance(robot_id, str) and robot_id
        }
        for cell_id in set(episode.get("involvedCellIds") or []):
            if not isinstance(cell_id, str) or not cell_id:
                continue
            hotspot = hotspot_accumulator.setdefault(
                cell_id,
                {"episodeIds": set(), "robotIds": set()},
            )
            hotspot["episodeIds"].add(episode_id)
            hotspot["robotIds"].update(participants)
    hotspots: list[dict[str, Any]] = []
    for cell_id, values in hotspot_accumulator.items():
        match = _DEADLOCK_CELL_PATTERN.match(cell_id)
        row_value = int(match.group(1)) if match else None
        col_value = int(match.group(2)) if match else None
        reason = "FLOOR_NOT_RECORDED_IN_EPISODE"
        if match is None:
            reason = "CELL_COORDINATE_NOT_PARSEABLE_AND_FLOOR_NOT_RECORDED"
        hotspots.append(
            {
                "cellId": cell_id,
                "row": row_value,
                "col": col_value,
                "floor": None,
                "observedEpisodeCount": len(values["episodeIds"]),
                "unit": "episode",
                "involvedRobotCount": len(values["robotIds"]),
                "sampleCount": len(values["episodeIds"]),
                "dataQuality": "DEGRADED",
                "reason": reason,
                "acceptanceEligible": False,
                "source": f"{source_events}.deadlock_episode_persistent",
                "generation": target_generation,
                "snapshotAsOfTick": latest_event_tick,
            }
        )
    hotspots = sorted(
        hotspots,
        key=lambda item: (-item["observedEpisodeCount"], item["cellId"]),
    )[:_DEADLOCK_HOTSPOT_LIMIT]

    recent_episodes = [
        {
            "episodeId": episode_id,
            "status": episode.get("status"),
            "firstObservedTick": episode.get("firstObservedTick"),
            "persistentAtTick": episode.get("persistentAtTick"),
            "resolvedAtTick": episode.get("resolvedAtTick"),
            "lastObservedTick": episode.get("lastObservedTick"),
            "participantRobotIds": episode.get("participantRobotIds") or [],
            "involvedCellIds": episode.get("involvedCellIds") or [],
            "dataQuality": episode.get("dataQuality"),
            "source": source_events,
            "generation": target_generation,
            "snapshotAsOfTick": event_tick,
        }
        for episode_id, (_event_id, event_tick, episode) in sorted(
            episode_latest.items(),
            key=lambda item: item[1][0],
            reverse=True,
        )[:_DEADLOCK_RECENT_EPISODE_LIMIT]
    ]

    return {
        "contractVersion": "deadlock-lifecycle-metrics-v1",
        "runId": run_id,
        "generation": target_generation,
        "availableGenerations": sorted(available_generations),
        "runStatus": run_status,
        "stopReason": stop_reason,
        "snapshotAsOfTick": snapshot_as_of_tick,
        "observationCompleteness": (
            snapshot_for_generation.get("observationCompleteness")
            if snapshot_for_generation
            else "MISSING"
        ),
        "dataQuality": base_quality,
        "reason": base_reason,
        "qualityIssues": quality_issues,
        "acceptanceEligible": False,
        "eventPersistenceWatermark": {
            "databaseEventId": latest_event_id,
            "databaseEventTick": latest_event_tick,
            "lastTransitionKey": latest_transition_key,
            "uniqueTransitionCount": len(ordered_events),
            "duplicateTransitionCount": duplicate_transition_count,
            "conflictingTransitionCount": conflicting_transition_count,
            "invalidEventCount": (
                invalid_payload_count
                + missing_generation_event_count
                + invalid_transition_count
                + invalid_episode_event_count
            ),
            "snapshotLastAcknowledgedTransitionKey": acknowledged_transition_key,
            "snapshotPendingTransitionCount": pending_transition_count,
        },
        "metrics": metrics,
        "robotBlocking": robot_blocking,
        "hotspots": hotspots,
        "recentEpisodes": recent_episodes,
        "limits": {
            "hotspotLimit": _DEADLOCK_HOTSPOT_LIMIT,
            "recentEpisodeLimit": _DEADLOCK_RECENT_EPISODE_LIMIT,
            "fullEpisodeHistoryReturned": False,
        },
        "semantics": {
            "persistentCycleMeaning": "仿真中达到持续阈值的循环等待，不等于物理设备不可恢复死锁",
            "durationFormula": "resolvedAtTick - persistentAtTick",
            "blockedRobotTicksUnit": "robot·Tick",
            "thresholdCalibration": "P2-1默认阈值尚未现场标定",
            "legacyDeadlockDetectedUsed": False,
        },
    }


class Repository:
    def upsert_topology(self, topology_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO topologies (id, name, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,
                  payload=excluded.payload,
                  updated_at=excluded.updated_at
                """,
                (topology_id, name, encode_json(payload), now, now),
            )
            row = conn.execute("SELECT * FROM topologies WHERE id = ?", (topology_id,)).fetchone()
        return self._decode_payload(row_to_dict(row))

    def list_topologies(self) -> list[dict[str, Any]]:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM topologies ORDER BY updated_at DESC").fetchall()
        return [self._decode_payload(row_to_dict(row)) for row in rows]

    def get_topology(self, topology_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM topologies WHERE id = ?", (topology_id,)).fetchone()
        return self._decode_payload(row_to_dict(row)) if row else None

    def get_topology_read_only(self, topology_id: str) -> dict[str, Any] | None:
        with get_read_conn() as conn:
            row = conn.execute("SELECT * FROM topologies WHERE id = ?", (topology_id,)).fetchone()
        return self._decode_payload(row_to_dict(row)) if row else None

    def delete_topology(self, topology_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM topologies WHERE id = ?", (topology_id,)).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM topologies WHERE id = ?", (topology_id,))
        return self._decode_payload(row_to_dict(row))

    def create_run(
        self,
        run_id: str,
        status: str,
        topology_id: str,
        payload: dict[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO simulation_runs (id, status, topology_id, payload, metrics, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, status, topology_id, encode_json(payload), encode_json(metrics), now, now),
            )
            row = conn.execute("SELECT * FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode_run(row_to_dict(row))

    def update_run(self, run_id: str, status: str, payload: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE simulation_runs
                SET status = ?, payload = ?, metrics = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, encode_json(payload), encode_json(metrics), now, run_id),
            )
        # 直接用内存对象构造返回值；此前每 tick 把整份状态 JSON 回读解码一遍，
        # 而 _persist 并不使用返回值，纯属浪费。
        return {
            "id": run_id,
            "status": status,
            "payload": payload,
            "metrics": metrics,
            "updated_at": now,
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode_run(row_to_dict(row)) if row else None

    def get_run_read_only(self, run_id: str) -> dict[str, Any] | None:
        with get_read_conn() as conn:
            row = conn.execute("SELECT * FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode_run(row_to_dict(row)) if row else None

    def list_algorithm_experiment_runs(self) -> list[dict[str, Any]]:
        """Return bounded summaries; never seed or mutate persisted Run data."""

        with get_read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM simulation_runs ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            decoded = self._decode_run(row_to_dict(row))
            payload = decoded.get("payload") or {}
            contract = payload.get("algorithmExperimentContract")
            if not isinstance(contract, dict):
                continue
            result.append(
                {
                    "runId": decoded["id"],
                    "status": decoded["status"],
                    "topologyId": decoded.get("topology_id"),
                    "scenarioId": contract.get("scenarioId"),
                    "scenarioVersion": contract.get("scenarioVersion"),
                    "scenarioSignature": contract.get("scenarioSignature"),
                    "variableDimension": contract.get("variableDimension"),
                    "pathPolicy": contract.get("pathPolicy"),
                    "assignmentPolicy": contract.get("assignmentPolicy"),
                    "algorithmVersion": contract.get("algorithmVersion"),
                    "observationTicks": contract.get("observationTicks"),
                    "simulatedSeconds": payload.get("simulatedSeconds"),
                    "tick": payload.get("tick"),
                    "createdAt": decoded.get("created_at"),
                }
            )
        return result

    def get_deadlock_lifecycle_metrics(
        self,
        run_id: str,
        generation: int | None = None,
    ) -> dict[str, Any] | None:
        """Return one generation's persisted deadlock evidence without database writes."""

        if generation is not None and (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            raise ValueError("generation must be a positive integer")

        placeholders = ",".join("?" for _ in _DEADLOCK_LIFECYCLE_EVENT_TYPES)
        with get_read_conn() as conn:
            run_row = conn.execute(
                "SELECT status, payload FROM simulation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run_row is None:
                return None
            event_rows = conn.execute(
                f"""SELECT id, tick, event_type, payload
                    FROM simulation_events
                    WHERE run_id = ? AND event_type IN ({placeholders})
                    ORDER BY id ASC""",
                (run_id, *_DEADLOCK_LIFECYCLE_EVENT_TYPES),
            ).fetchall()

        return _build_deadlock_lifecycle_metrics(
            run_id,
            run_row,
            event_rows,
            generation,
        )

    def get_motion_distance_metrics(
        self,
        run_id: str,
        generation: int | None = None,
    ) -> dict[str, Any] | None:
        """Aggregate persisted movement evidence through a strictly read-only connection."""

        if generation is not None and (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            raise ValueError("generation must be a positive integer")

        with get_read_conn() as conn:
            run_row = conn.execute(
                "SELECT status, payload FROM simulation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run_row is None:
                return None

            try:
                run_payload = decode_json(run_row["payload"])
            except (TypeError, ValueError):
                run_payload = {}

            cursor = conn.execute(
                """SELECT id, tick, payload
                   FROM simulation_events
                   WHERE run_id = ? AND event_type = 'robot_movement_batch_v1'
                   ORDER BY id ASC""",
                (run_id,),
            )

            def decoded_rows():
                while True:
                    rows = cursor.fetchmany(512)
                    if not rows:
                        break
                    for row in rows:
                        try:
                            payload = decode_json(row["payload"])
                        except (TypeError, ValueError):
                            payload = None
                        yield {"id": row["id"], "tick": row["tick"], "payload": payload}

            return aggregate_motion_distance_metrics(
                run_id,
                str(run_row["status"]),
                run_payload,
                decoded_rows(),
                generation,
            )

    def get_metrics_history_quality(self, run_id: str, max_points: int = 240) -> dict[str, Any] | None:
        """Return deduplicated history quality and full-sample statistics without writes."""

        max_points = int(max_points)
        with get_read_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM simulation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if not exists:
                return None
            rows = conn.execute(
                """SELECT id, run_id, tick, payload FROM simulation_events
                   WHERE run_id = ? AND event_type = 'metrics_snapshot'
                   ORDER BY tick ASC, id ASC""",
                (run_id,),
            ).fetchall()

        field_contracts: dict[str, dict[str, str]] = {
            "blockedRobots": {
                "unit": "robot",
                "description": "阻塞车辆数历史聚合；不是死锁数或死锁持续时间",
            },
            "activeRobots": {
                "unit": "robot",
                "description": "活动车辆数历史聚合",
            },
            "completedTasks": {
                "unit": "task",
                "description": "原 completedTasks 累计字段历史值；不是状态重算父任务数",
            },
            "pendingTasks": {
                "unit": "task",
                "description": "待处理任务数历史聚合",
            },
            "conveyorQueueDepth": {
                "unit": "task",
                "description": "输送等待任务数历史聚合；不是等待时长",
            },
            "stationQueueDepth": {
                "unit": "task",
                "description": "站台排队任务数历史聚合；不是等待时长",
            },
            "bufferOccupancy": {
                "unit": "task",
                "description": "缓存等待任务数历史聚合；不是缓存物理利用率",
            },
            "conveyorUtilization": {
                "unit": "%",
                "description": "输送节点占用率代理历史聚合；不是输送线物理利用率",
            },
        }
        count_fields = set(field_contracts) - {"conveyorUtilization"}
        raw_sample_count = len(rows)
        latest_by_tick: dict[int, Any] = {}
        for row in rows:
            tick = row["tick"]
            previous = latest_by_tick.get(tick)
            if previous is None or int(row["id"]) > int(previous["id"]):
                latest_by_tick[tick] = row
        deduplicated_rows = sorted(latest_by_tick.values(), key=lambda item: item["tick"])
        unique_tick_count = len(deduplicated_rows)
        duplicate_snapshot_count = raw_sample_count - unique_tick_count

        full_points: list[dict[str, Any]] = []
        samples: dict[str, list[tuple[int, int | float]]] = {
            field: [] for field in field_contracts
        }
        missing_counts = {field: 0 for field in field_contracts}
        invalid_counts = {field: 0 for field in field_contracts}
        invalid_snapshot_count = 0

        for row in deduplicated_rows:
            tick = int(row["tick"])
            point: dict[str, Any] = {"tick": tick}
            try:
                payload = decode_json(row["payload"])
            except (TypeError, ValueError):
                payload = None
            if not isinstance(payload, dict):
                invalid_snapshot_count += 1
                point.update(
                    dataQuality="INVALID",
                    reason="INVALID_SNAPSHOT_PAYLOAD",
                )
                for field in field_contracts:
                    invalid_counts[field] += 1
                full_points.append(point)
                continue

            missing_fields: list[str] = []
            invalid_fields: list[str] = []
            for field in field_contracts:
                if field not in payload:
                    missing_counts[field] += 1
                    missing_fields.append(field)
                    continue
                value = payload[field]
                valid = False
                if field in count_fields:
                    valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    valid = math.isfinite(value) and 0 <= value <= 100
                if not valid:
                    invalid_counts[field] += 1
                    invalid_fields.append(field)
                    continue
                point[field] = value
                samples[field].append((tick, value))
            if invalid_fields:
                point["invalidFields"] = invalid_fields
            if missing_fields:
                point["missingFields"] = missing_fields
            point["dataQuality"] = "DEGRADED" if invalid_fields or missing_fields else "GOOD"
            point["reason"] = (
                "INVALID_OR_MISSING_FIELDS" if invalid_fields or missing_fields else None
            )
            full_points.append(point)

        if unique_tick_count <= max_points:
            selected_points = full_points
        else:
            indices = sorted(
                set(
                    round(index * (unique_tick_count - 1) / (max_points - 1))
                    for index in range(max_points)
                )
            )
            selected_points = [full_points[index] for index in indices]

        quality_issues: list[str] = []
        if invalid_snapshot_count:
            quality_issues.append("INVALID_SNAPSHOT_PAYLOAD")
        if any(invalid_counts.values()):
            quality_issues.append("INVALID_METRIC_VALUES")
        if any(missing_counts.values()) and unique_tick_count:
            quality_issues.append("MISSING_METRIC_FIELDS")
        if duplicate_snapshot_count:
            quality_issues.append("DUPLICATE_SNAPSHOTS_DEDUPLICATED")
        if unique_tick_count:
            quality_issues.append("COLLECTION_COMPLETENESS_UNCONFIRMED")

        if not raw_sample_count:
            data_quality = "MISSING_SOURCE"
            reason = "NO_METRICS_SNAPSHOTS"
        else:
            data_quality = "DEGRADED"
            reason = quality_issues[0] if quality_issues else "COLLECTION_COMPLETENESS_UNCONFIRMED"

        statistics: dict[str, dict[str, Any]] = {}
        for field, contract in field_contracts.items():
            field_samples = samples[field]
            valid_sample_count = len(field_samples)
            if valid_sample_count:
                values = [value for _, value in field_samples]
                minimum = min(values)
                maximum = max(values)
                maximum_tick = next(tick for tick, value in field_samples if value == maximum)
                average = sum(values) / valid_sample_count
                if missing_counts[field] or invalid_counts[field]:
                    field_quality = "DEGRADED"
                    field_reason = "PARTIAL_VALID_SAMPLES"
                else:
                    field_quality = "DEGRADED"
                    field_reason = "COLLECTION_COMPLETENESS_UNCONFIRMED"
            else:
                minimum = maximum = maximum_tick = average = None
                if not raw_sample_count:
                    field_quality = "MISSING_SOURCE"
                    field_reason = "NO_METRICS_SNAPSHOTS"
                elif missing_counts[field] and not invalid_counts[field]:
                    field_quality = "MISSING_SOURCE"
                    field_reason = "FIELD_NOT_PRESENT"
                else:
                    field_quality = "DEGRADED"
                    field_reason = "NO_VALID_NUMERIC_SAMPLES"
            statistics[field] = {
                "description": contract["description"],
                "unit": contract["unit"],
                "minimum": minimum,
                "maximum": maximum,
                "maximumTick": maximum_tick,
                "average": average,
                "validSampleCount": valid_sample_count,
                "missingSampleCount": missing_counts[field],
                "invalidSampleCount": invalid_counts[field],
                "dataQuality": field_quality,
                "reason": field_reason,
                "acceptanceEligible": False,
            }

        return {
            "runId": run_id,
            "dataQuality": data_quality,
            "reason": reason,
            "collectionCompleteness": "UNCONFIRMED" if raw_sample_count else "MISSING",
            "qualityIssues": quality_issues,
            "rawSampleCount": raw_sample_count,
            "uniqueTickCount": unique_tick_count,
            "duplicateSnapshotCount": duplicate_snapshot_count,
            "outputSampleCount": len(selected_points),
            "invalidSnapshotCount": invalid_snapshot_count,
            "points": selected_points,
            "statistics": statistics,
            "acceptanceEligible": False,
        }

    def get_assignment_contention_metrics(self, run_id: str) -> dict[str, Any] | None:
        """Return observed CTU assignment and contention metrics without database writes."""

        with get_read_conn() as conn:
            row = conn.execute(
                "SELECT payload FROM simulation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            event_rows = conn.execute(
                """SELECT id, tick, event_type, payload FROM simulation_events
                   WHERE run_id = ?
                     AND event_type IN ('task_state_changed', 'robot_yield', 'deadlock_detected')
                   ORDER BY id ASC""",
                (run_id,),
            ).fetchall()

        try:
            state = decode_json(row["payload"])
        except (TypeError, ValueError):
            state = None

        tasks_source = state.get("tasks") if isinstance(state, dict) else None
        tasks_available = isinstance(tasks_source, list)
        tasks, invalid_task_count, conflicting_task_id_count = (
            _deduplicate_metric_tasks(tasks_source) if tasks_available else ([], 0, 0)
        )

        ctu_tasks = {
            task["id"]: task
            for task in tasks
            if task.get("robotType") == "ctu"
            and _classify_metric_task_parentage(task) == "parent"
        }

        robots_source = state.get("robots") if isinstance(state, dict) else None
        robots_available = isinstance(robots_source, list)
        robots_by_id: dict[str, dict[str, Any]] = {}
        conflicting_robot_ids: set[str] = set()
        invalid_robot_count = 0
        if robots_available:
            for item in robots_source:
                if not isinstance(item, dict):
                    invalid_robot_count += 1
                    continue
                robot_id = item.get("id")
                status = item.get("status")
                if (
                    not isinstance(robot_id, str)
                    or not robot_id.strip()
                    or not isinstance(status, str)
                    or not status.strip()
                ):
                    invalid_robot_count += 1
                    continue
                if robot_id in conflicting_robot_ids:
                    continue
                previous = robots_by_id.get(robot_id)
                if previous is None:
                    robots_by_id[robot_id] = item
                elif previous != item:
                    conflicting_robot_ids.add(robot_id)
                    robots_by_id.pop(robot_id, None)

        first_assignment_ticks: dict[str, int] = {}
        associations: set[tuple[str, str]] = set()
        valid_assignment_event_count = 0
        invalid_assignment_event_count = 0
        ignored_assigned_state_event_count = 0
        out_of_scope_assignment_event_count = 0
        valid_deadlock_events = 0
        invalid_deadlock_event_count = 0
        valid_yield_events = 0
        invalid_yield_event_count = 0

        def valid_tick(value: Any) -> bool:
            return isinstance(value, int) and not isinstance(value, bool) and value >= 0

        for event_row in event_rows:
            event_type = event_row["event_type"]
            tick = event_row["tick"]
            try:
                event_payload = decode_json(event_row["payload"])
            except (TypeError, ValueError):
                event_payload = None

            if event_type == "task_state_changed":
                if not isinstance(event_payload, dict) or event_payload.get("status") != "assigned":
                    continue
                if "robotId" not in event_payload:
                    ignored_assigned_state_event_count += 1
                    continue
                task_id = event_payload.get("taskId")
                robot_id = event_payload.get("robotId")
                if (
                    not valid_tick(tick)
                    or not isinstance(task_id, str)
                    or not task_id.strip()
                    or not isinstance(robot_id, str)
                    or not robot_id.strip()
                ):
                    invalid_assignment_event_count += 1
                    continue
                if task_id not in ctu_tasks:
                    out_of_scope_assignment_event_count += 1
                    continue
                valid_assignment_event_count += 1
                current = first_assignment_ticks.get(task_id)
                first_assignment_ticks[task_id] = tick if current is None else min(current, tick)
                associations.add((robot_id, task_id))
                continue

            if event_type == "robot_yield":
                robot_id = event_payload.get("robotId") if isinstance(event_payload, dict) else None
                if (
                    not valid_tick(tick)
                    or not isinstance(robot_id, str)
                    or not robot_id.strip()
                ):
                    invalid_yield_event_count += 1
                    continue
                valid_yield_events += 1
                continue

            if event_type == "deadlock_detected":
                waiting_chain = (
                    event_payload.get("waitingChain") if isinstance(event_payload, dict) else None
                )
                chain_valid = isinstance(waiting_chain, list) and len(waiting_chain) >= 2
                if chain_valid:
                    for item in waiting_chain:
                        if not isinstance(item, dict):
                            chain_valid = False
                            break
                        robot_id = item.get("robotId")
                        task_id = item.get("taskId")
                        if not isinstance(robot_id, str) or not robot_id.strip():
                            chain_valid = False
                            break
                        if task_id is not None and (
                            not isinstance(task_id, str) or not task_id.strip()
                        ):
                            chain_valid = False
                            break
                if not valid_tick(tick) or not chain_valid:
                    invalid_deadlock_event_count += 1
                    continue
                valid_deadlock_events += 1

        wait_durations: list[int] = []
        invalid_created_tick_count = 0
        negative_wait_count = 0
        missing_assignment_evidence_count = 0
        for task_id, task in ctu_tasks.items():
            first_tick = first_assignment_ticks.get(task_id)
            if first_tick is None:
                missing_assignment_evidence_count += 1
                continue
            created_tick = task.get("createdTick")
            if not valid_tick(created_tick):
                invalid_created_tick_count += 1
                continue
            wait_ticks = first_tick - created_tick
            if wait_ticks < 0:
                negative_wait_count += 1
                continue
            wait_durations.append(wait_ticks)

        metrics: list[dict[str, Any]] = []

        def add_metric(
            metric_id: str,
            display_name: str,
            value: Any,
            unit: str,
            sample_count: int | None,
            implementation_status: str,
            data_quality: str,
            reason: str | None,
            **extra: Any,
        ) -> None:
            metric = {
                "metricId": metric_id,
                "displayName": display_name,
                "value": value,
                "unit": unit,
                "sampleCount": sample_count,
                "implementationStatus": implementation_status,
                "dataQuality": data_quality,
                "reason": reason,
                "acceptanceEligible": False,
            }
            metric.update(extra)
            metrics.append(metric)

        ctu_source_reason: str | None = None
        ctu_source_quality = "DEGRADED"
        if not tasks_available:
            ctu_source_reason = "TASKS_SOURCE_MISSING"
            ctu_source_quality = "MISSING_SOURCE"
        elif conflicting_task_id_count:
            ctu_source_reason = "DUPLICATE_TASK_CONFLICT"
        elif not first_assignment_ticks:
            ctu_source_reason = "EVENT_COLLECTION_UNCONFIRMED"
        else:
            ctu_source_reason = "EVENT_COLLECTION_UNCONFIRMED"

        if not tasks_available or conflicting_task_id_count or not first_assignment_ticks:
            assigned_task_value = None
            assigned_task_sample_count = 0 if tasks_available else None
        else:
            assigned_task_value = len(first_assignment_ticks)
            assigned_task_sample_count = len(first_assignment_ticks)
        add_metric(
            "CTU_ASSIGNED_PARENT_TASK_COUNT",
            "已观察到分配证据的 CTU 父任务数",
            assigned_task_value,
            "task",
            assigned_task_sample_count,
            "PARTIAL",
            ctu_source_quality,
            ctu_source_reason,
            observedAssignmentEventCount=valid_assignment_event_count,
            invalidAssignmentEventCount=invalid_assignment_event_count,
            ignoredAssignedStateEventCount=ignored_assigned_state_event_count,
            outOfScopeAssignmentEventCount=out_of_scope_assignment_event_count,
        )

        if wait_durations and not conflicting_task_id_count:
            wait_average: float | None = sum(wait_durations) / len(wait_durations)
            wait_maximum: int | None = max(wait_durations)
            wait_sample_value: int | None = len(wait_durations)
            wait_reason = "EVENT_COLLECTION_UNCONFIRMED"
            wait_quality = "DEGRADED"
        else:
            wait_average = wait_maximum = wait_sample_value = None
            wait_quality = "MISSING_SOURCE" if not tasks_available else "DEGRADED"
            wait_reason = (
                "TASKS_SOURCE_MISSING"
                if not tasks_available
                else "DUPLICATE_TASK_CONFLICT"
                if conflicting_task_id_count
                else "NO_VALID_ASSIGNMENT_WAIT_SAMPLES"
            )
        wait_excluded_count = (
            missing_assignment_evidence_count
            + invalid_created_tick_count
            + negative_wait_count
        )
        wait_extra = {
            "excludedSampleCount": wait_excluded_count,
            "missingAssignmentEvidenceCount": missing_assignment_evidence_count,
            "invalidCreatedTickCount": invalid_created_tick_count,
            "negativeWaitCount": negative_wait_count,
        }
        add_metric(
            "CTU_FIRST_ASSIGN_WAIT_AVG_TICKS",
            "CTU 首次分配平均等待 Tick",
            wait_average,
            "Tick",
            len(wait_durations),
            "PARTIAL",
            wait_quality,
            wait_reason,
            **wait_extra,
        )
        add_metric(
            "CTU_FIRST_ASSIGN_WAIT_MAX_TICKS",
            "CTU 首次分配最大等待 Tick",
            wait_maximum,
            "Tick",
            len(wait_durations),
            "PARTIAL",
            wait_quality,
            wait_reason,
            **wait_extra,
        )
        add_metric(
            "CTU_FIRST_ASSIGN_WAIT_SAMPLE_COUNT",
            "CTU 首次分配等待有效样本数",
            wait_sample_value,
            "task",
            len(wait_durations),
            "PARTIAL",
            wait_quality,
            wait_reason,
            **wait_extra,
        )

        if associations and not conflicting_task_id_count:
            association_counts: dict[str, int] = {}
            for robot_id, _ in associations:
                association_counts[robot_id] = association_counts.get(robot_id, 0) + 1
            association_value: Any = [
                {"robotId": robot_id, "taskCount": association_counts[robot_id]}
                for robot_id in sorted(association_counts)
            ]
            association_sample_count: int | None = len(associations)
            association_reason = "EVENT_COLLECTION_UNCONFIRMED"
            association_quality = "DEGRADED"
        else:
            association_value = None
            association_sample_count = 0 if tasks_available else None
            association_quality = "MISSING_SOURCE" if not tasks_available else "DEGRADED"
            association_reason = (
                "TASKS_SOURCE_MISSING"
                if not tasks_available
                else "DUPLICATE_TASK_CONFLICT"
                if conflicting_task_id_count
                else "EVENT_COLLECTION_UNCONFIRMED"
            )
        add_metric(
            "CTU_VEHICLE_TASK_ASSOCIATION_COUNT",
            "各 CTU 已观察历史任务关联数",
            association_value,
            "task",
            association_sample_count,
            "PARTIAL",
            association_quality,
            association_reason,
            vehicleCount=len(association_value) if isinstance(association_value, list) else 0,
            associationSemantics="observed_distinct_robot_task_pairs_not_utilization",
        )

        if not robots_available:
            blocked_value = None
            blocked_sample_count = None
            blocked_quality = "MISSING_SOURCE"
            blocked_reason = "ROBOTS_SOURCE_MISSING"
        elif conflicting_robot_ids:
            blocked_value = None
            blocked_sample_count = None
            blocked_quality = "DEGRADED"
            blocked_reason = "DUPLICATE_ROBOT_CONFLICT"
        else:
            blocked_value = sum(
                1 for robot in robots_by_id.values() if robot.get("status") == "blocked"
            )
            blocked_sample_count = len(robots_by_id)
            blocked_quality = "DEGRADED" if invalid_robot_count else "GOOD"
            blocked_reason = "INVALID_ROBOT_RECORDS_EXCLUDED" if invalid_robot_count else None
        add_metric(
            "BLOCKED_ROBOT_COUNT_CURRENT",
            "当前阻塞车辆数",
            blocked_value,
            "robot",
            blocked_sample_count,
            "PARTIAL",
            blocked_quality,
            blocked_reason,
            invalidRobotRecordCount=invalid_robot_count,
            conflictingRobotIdCount=len(conflicting_robot_ids),
        )

        def add_observed_event_metric(
            metric_id: str,
            display_name: str,
            observed_count: int,
            invalid_count: int,
        ) -> None:
            value = observed_count if observed_count else None
            add_metric(
                metric_id,
                display_name,
                value,
                "event",
                observed_count,
                "IMPLEMENTED",
                "DEGRADED",
                "EVENT_COLLECTION_UNCONFIRMED",
                observedCount=observed_count,
                invalidEventCount=invalid_count,
                countSemantics="observed_persisted_events_not_confirmed_complete_total",
            )

        add_observed_event_metric(
            "SUSPECTED_DEADLOCK_ALERT_COUNT",
            "疑似死锁告警事件数",
            valid_deadlock_events,
            invalid_deadlock_event_count,
        )
        add_observed_event_metric(
            "ROBOT_YIELD_EVENT_COUNT",
            "CTU 主动让行事件",
            valid_yield_events,
            invalid_yield_event_count,
        )

        for metric_id, display_name, unit, reason in (
            (
                "TRUE_DEADLOCK_RATE",
                "真实死锁率",
                "event_per_1000_tasks",
                "NO_CONFIRMED_WAIT_CYCLE_EVENT",
            ),
            (
                "DEADLOCK_UNLOCK_AVG_DURATION",
                "平均死锁解锁时间",
                "second",
                "NO_CONFIRMED_RESOLUTION_LINK",
            ),
            (
                "BLOCKAGE_DURATION",
                "堵塞持续时长",
                "second",
                "NO_RESOURCE_BLOCKAGE_INTERVALS",
            ),
            (
                "CTU_ASSIGNMENT_COST",
                "CTU 实际分配成本",
                "cost_point",
                "NO_PERSISTED_ASSIGNMENT_COST",
            ),
            (
                "CTU_DEVICE_UTILIZATION",
                "CTU 设备利用率",
                "%",
                "NO_DEVICE_BUSY_INTERVALS",
            ),
        ):
            add_metric(
                metric_id,
                display_name,
                None,
                unit,
                0,
                "UNAVAILABLE",
                "MISSING_SOURCE",
                reason,
            )

        return {
            "runId": run_id,
            "eventCollectionCompleteness": "UNCONFIRMED",
            "dataQuality": "DEGRADED",
            "reason": "EVENT_COLLECTION_UNCONFIRMED",
            "metrics": metrics,
            "quality": {
                "ctuParentTaskCount": len(ctu_tasks) if tasks_available else None,
                "invalidTaskRecordCount": invalid_task_count,
                "conflictingTaskIdCount": conflicting_task_id_count,
                "validAssignmentEventCount": valid_assignment_event_count,
                "invalidAssignmentEventCount": invalid_assignment_event_count,
                "ignoredAssignedStateEventCount": ignored_assigned_state_event_count,
                "outOfScopeAssignmentEventCount": out_of_scope_assignment_event_count,
                "validDeadlockEventCount": valid_deadlock_events,
                "invalidDeadlockEventCount": invalid_deadlock_event_count,
                "validYieldEventCount": valid_yield_events,
                "invalidYieldEventCount": invalid_yield_event_count,
            },
            "acceptanceEligible": False,
        }

    def get_path_planning_metrics(self, run_id: str) -> dict[str, Any] | None:
        """Aggregate persisted successful shuttle-transfer plans without replanning."""

        with get_read_conn() as conn:
            row = conn.execute(
                "SELECT payload FROM simulation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None

        try:
            state = decode_json(row["payload"])
        except (TypeError, ValueError):
            state = None
        tasks_source = state.get("tasks") if isinstance(state, dict) else None
        tasks_available = isinstance(tasks_source, list)
        tasks, invalid_task_record_count, conflicting_task_id_count = (
            _deduplicate_metric_tasks(tasks_source) if tasks_available else ([], 0, 0)
        )

        sample_kind = "successful_shuttle_transfer_plan"
        supported_algorithms = ("astar", "dijkstra")
        successful_samples: list[dict[str, Any]] = []
        non_shuttle_task_count = 0
        child_task_count = 0
        missing_or_invalid_plan_count = 0
        failed_plan_count = 0
        invalid_algorithm_count = 0

        for task in tasks:
            if task.get("mode") != "shuttle_transfer":
                non_shuttle_task_count += 1
                continue
            if _classify_metric_task_parentage(task) != "parent":
                child_task_count += 1
                continue
            plan = task.get("plan")
            if not isinstance(plan, dict):
                missing_or_invalid_plan_count += 1
                continue
            if plan.get("result_status") != "success":
                failed_plan_count += 1
                continue
            algorithm = plan.get("algorithm")
            if algorithm not in supported_algorithms:
                invalid_algorithm_count += 1
                continue
            versions: dict[str, str | None] = {}
            for source_key, output_key in (
                ("topologyRevision", "topologyRevision"),
                ("configRevision", "configRevision"),
                ("coordinateContractRevision", "coordinateContractRevision"),
            ):
                value = plan.get(source_key)
                versions[output_key] = value if isinstance(value, str) and value.strip() else None
            successful_samples.append(
                {
                    "taskId": task["id"],
                    "algorithm": algorithm,
                    "plan": plan,
                    **versions,
                }
            )

        algorithm_counts = {
            algorithm: sum(1 for sample in successful_samples if sample["algorithm"] == algorithm)
            for algorithm in supported_algorithms
        }
        algorithm_value = [
            {"algorithm": algorithm, "sampleCount": algorithm_counts[algorithm]}
            for algorithm in supported_algorithms
            if algorithm_counts[algorithm]
        ]

        field_specs: dict[str, dict[str, str]] = {
            "planning_time_ms": {
                "metricId": "PATH_PLANNING_TIME_MS",
                "displayName": "成功跨层初始规划耗时",
                "unit": "ms",
            },
            "horizontal_distance_cells": {
                "metricId": "PATH_HORIZONTAL_DISTANCE_CELLS",
                "displayName": "成功跨层初始规划水平路径距离",
                "unit": "cell",
            },
            "visited_states": {
                "metricId": "PATH_VISITED_STATE_COUNT",
                "displayName": "成功跨层初始规划访问状态数",
                "unit": "state",
            },
        }
        field_samples: dict[str, dict[tuple[Any, ...], list[tuple[str, int | float]]]] = {
            field: {} for field in field_specs
        }
        field_invalid_counts = {field: 0 for field in field_specs}

        def group_key(sample: dict[str, Any]) -> tuple[Any, ...]:
            return (
                sample["algorithm"],
                sample["topologyRevision"],
                sample["configRevision"],
                sample["coordinateContractRevision"],
            )

        for sample in successful_samples:
            plan = sample["plan"]
            for field in field_specs:
                value = plan.get(field)
                if field == "visited_states":
                    valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
                else:
                    valid = (
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and math.isfinite(value)
                        and value >= 0
                    )
                if not valid:
                    field_invalid_counts[field] += 1
                    continue
                field_samples[field].setdefault(group_key(sample), []).append(
                    (sample["taskId"], value)
                )

        metrics: list[dict[str, Any]] = []

        def add_metric(
            metric_id: str,
            display_name: str,
            value: Any,
            unit: str,
            sample_count: int | None,
            implementation_status: str,
            data_quality: str,
            reason: str | None,
            **extra: Any,
        ) -> None:
            metric = {
                "metricId": metric_id,
                "displayName": display_name,
                "value": value,
                "unit": unit,
                "sampleCount": sample_count,
                "implementationStatus": implementation_status,
                "dataQuality": data_quality,
                "reason": reason,
                "acceptanceEligible": False,
            }
            metric.update(extra)
            metrics.append(metric)

        base_excluded_count = (
            invalid_task_record_count
            + conflicting_task_id_count
            + missing_or_invalid_plan_count
            + failed_plan_count
            + invalid_algorithm_count
            + child_task_count
        )
        if not tasks_available:
            success_value = None
            success_sample_count = None
            success_quality = "MISSING_SOURCE"
            success_reason = "TASKS_SOURCE_MISSING"
        else:
            success_value = len(successful_samples)
            success_sample_count = len(successful_samples)
            if successful_samples:
                success_quality = "DEGRADED" if base_excluded_count else "SUCCESS_ONLY_SAMPLE"
                success_reason = (
                    "INVALID_OR_EXCLUDED_PLAN_SAMPLES"
                    if base_excluded_count
                    else "SUCCESS_ONLY_SAMPLE_BIAS"
                )
            else:
                success_quality = "DEGRADED" if base_excluded_count else "GOOD"
                success_reason = (
                    "INVALID_OR_EXCLUDED_PLAN_SAMPLES" if base_excluded_count else "NO_SAMPLES"
                )
        add_metric(
            "PATH_SUCCESS_SAMPLE_COUNT",
            "成功跨层初始规划样本数",
            success_value,
            "plan",
            success_sample_count,
            "IMPLEMENTED",
            success_quality,
            success_reason,
            sampleKind=sample_kind,
            excludedInvalidPlanCount=(
                invalid_task_record_count
                + conflicting_task_id_count
                + missing_or_invalid_plan_count
                + invalid_algorithm_count
            ),
            failedPlanCount=failed_plan_count,
            excludedChildTaskCount=child_task_count,
            excludedNonShuttleTaskCount=non_shuttle_task_count,
            conflictingTaskIdCount=conflicting_task_id_count,
        )

        if successful_samples:
            algorithm_metric_value: Any = algorithm_value
            algorithm_quality = "SUCCESS_ONLY_SAMPLE"
            algorithm_reason = "SUCCESS_ONLY_SAMPLE_BIAS"
        else:
            algorithm_metric_value = None
            algorithm_quality = "MISSING_SOURCE" if not tasks_available else "DEGRADED"
            algorithm_reason = "TASKS_SOURCE_MISSING" if not tasks_available else "NO_SAMPLES"
        add_metric(
            "PATH_ALGORITHM",
            "成功跨层初始规划算法分组",
            algorithm_metric_value,
            "enum",
            len(successful_samples) if tasks_available else None,
            "IMPLEMENTED",
            algorithm_quality,
            algorithm_reason,
            sampleKind=sample_kind,
        )

        for field, spec in field_specs.items():
            grouped_values: list[dict[str, Any]] = []
            total_valid = 0
            missing_version_group = False
            for key in sorted(
                field_samples[field],
                key=lambda item: tuple("" if part is None else str(part) for part in item),
            ):
                algorithm, topology_revision, config_revision, coordinate_revision = key
                values_with_ids = field_samples[field][key]
                values = [value for _, value in values_with_ids]
                total_valid += len(values)
                version_missing = any(
                    value is None
                    for value in (topology_revision, config_revision, coordinate_revision)
                )
                missing_version_group = missing_version_group or version_missing
                grouped_values.append(
                    {
                        "group": {
                            "runId": run_id,
                            "algorithm": algorithm,
                            "topologyRevision": topology_revision,
                            "configRevision": config_revision,
                            "coordinateContractRevision": coordinate_revision,
                            "sampleKind": sample_kind,
                        },
                        "minimum": min(values),
                        "maximum": max(values),
                        "average": sum(values) / len(values),
                        "validSampleCount": len(values),
                        "samplePreview": [
                            {"taskId": task_id, "value": value}
                            for task_id, value in values_with_ids[:100]
                        ],
                        "previewSampleCount": min(len(values_with_ids), 100),
                        "dataQuality": "DEGRADED" if version_missing else "SUCCESS_ONLY_SAMPLE",
                        "reason": (
                            "VERSION_METADATA_MISSING"
                            if version_missing
                            else "SUCCESS_ONLY_SAMPLE_BIAS"
                        ),
                    }
                )
            if total_valid:
                field_value: Any = grouped_values
                field_quality = (
                    "DEGRADED"
                    if field_invalid_counts[field] or missing_version_group
                    else "SUCCESS_ONLY_SAMPLE"
                )
                field_reason = (
                    "INVALID_FIELD_SAMPLES_EXCLUDED"
                    if field_invalid_counts[field]
                    else "VERSION_METADATA_MISSING"
                    if missing_version_group
                    else "SUCCESS_ONLY_SAMPLE_BIAS"
                )
            else:
                field_value = None
                field_quality = "MISSING_SOURCE" if not tasks_available else "DEGRADED"
                field_reason = "TASKS_SOURCE_MISSING" if not tasks_available else "NO_SAMPLES"
            add_metric(
                spec["metricId"],
                spec["displayName"],
                field_value,
                spec["unit"],
                total_valid if tasks_available else None,
                "IMPLEMENTED",
                field_quality,
                field_reason,
                sampleKind=sample_kind,
                invalidSampleCount=field_invalid_counts[field],
                previewLimitPerGroup=100,
            )

        quantile_groups: list[dict[str, Any]] = []
        for key in sorted(
            field_samples["planning_time_ms"],
            key=lambda item: tuple("" if part is None else str(part) for part in item),
        ):
            algorithm, topology_revision, config_revision, coordinate_revision = key
            values = sorted(value for _, value in field_samples["planning_time_ms"][key])
            count = len(values)
            version_missing = any(
                value is None
                for value in (topology_revision, config_revision, coordinate_revision)
            )

            def quantile(percentile: float, required: int) -> dict[str, Any]:
                if count < required:
                    return {
                        "value": None,
                        "unit": "ms",
                        "requiredSampleCount": required,
                        "actualSampleCount": count,
                        "reason": "NO_SAMPLES" if count == 0 else "INSUFFICIENT_SAMPLE",
                    }
                rank = math.ceil(percentile * count)
                return {
                    "value": values[rank - 1],
                    "unit": "ms",
                    "requiredSampleCount": required,
                    "actualSampleCount": count,
                    "reason": None,
                }

            quantile_groups.append(
                {
                    "group": {
                        "runId": run_id,
                        "algorithm": algorithm,
                        "topologyRevision": topology_revision,
                        "configRevision": config_revision,
                        "coordinateContractRevision": coordinate_revision,
                        "sampleKind": sample_kind,
                    },
                    "sampleCount": count,
                    "quantiles": {
                        "p50": quantile(0.50, 5),
                        "p90": quantile(0.90, 10),
                        "p99": quantile(0.99, 100),
                    },
                    "implementationStatus": "PARTIAL",
                    "dataQuality": "DEGRADED" if version_missing else "SUCCESS_ONLY_SAMPLE",
                    "reason": (
                        "VERSION_METADATA_MISSING"
                        if version_missing
                        else "SUCCESS_ONLY_SAMPLE_BIAS"
                    ),
                    "acceptanceEligible": False,
                }
            )
        add_metric(
            "PATH_PLANNING_TIME_QUANTILES_MS",
            "成功跨层初始规划耗时 P50/P90/P99",
            quantile_groups if quantile_groups else None,
            "ms",
            sum(group["sampleCount"] for group in quantile_groups),
            "PARTIAL",
            (
                "DEGRADED"
                if any(group["dataQuality"] == "DEGRADED" for group in quantile_groups)
                or field_invalid_counts["planning_time_ms"]
                else "SUCCESS_ONLY_SAMPLE"
                if quantile_groups
                else "MISSING_SOURCE"
                if not tasks_available
                else "DEGRADED"
            ),
            (
                "INVALID_FIELD_SAMPLES_EXCLUDED"
                if field_invalid_counts["planning_time_ms"]
                else "VERSION_METADATA_MISSING"
                if any(group["dataQuality"] == "DEGRADED" for group in quantile_groups)
                else "SUCCESS_ONLY_SAMPLE_BIAS"
                if quantile_groups
                else "TASKS_SOURCE_MISSING"
                if not tasks_available
                else "NO_SAMPLES"
            ),
            sampleKind=sample_kind,
            invalidSampleCount=field_invalid_counts["planning_time_ms"],
            quantileAlgorithm="nearest_rank_ceil_p_times_n_one_based_no_interpolation",
        )

        add_metric(
            "PATH_REPLAN_SUCCESS_RATE",
            "路径重规划成功率",
            None,
            "%",
            0,
            "UNAVAILABLE",
            "MISSING_SOURCE",
            "NO_REPLAN_TRIGGER_AND_RESULT_EVENTS",
        )
        add_metric(
            "PATH_LENGTH_OPTIMIZATION_RATE",
            "路径长度优化率",
            None,
            "%",
            0,
            "UNAVAILABLE",
            "MISSING_SOURCE",
            "NO_PAIRED_SAME_OD_ENVIRONMENT_EXPERIMENT",
        )

        return {
            "runId": run_id,
            "sampleKind": sample_kind,
            "dataQuality": (
                "MISSING_SOURCE"
                if not tasks_available
                else "DEGRADED"
                if not successful_samples
                else "DEGRADED"
                if base_excluded_count
                or any(field_invalid_counts.values())
                or any(
                    sample[key] is None
                    for sample in successful_samples
                    for key in (
                        "topologyRevision",
                        "configRevision",
                        "coordinateContractRevision",
                    )
                )
                else "SUCCESS_ONLY_SAMPLE"
            ),
            "reason": (
                "TASKS_SOURCE_MISSING"
                if not tasks_available
                else "INVALID_OR_EXCLUDED_PLAN_SAMPLES"
                if not successful_samples and base_excluded_count
                else "NO_SAMPLES"
                if not successful_samples
                else "INVALID_OR_EXCLUDED_PLAN_SAMPLES"
                if base_excluded_count or any(field_invalid_counts.values())
                else "VERSION_METADATA_MISSING"
                if any(
                    sample[key] is None
                    for sample in successful_samples
                    for key in (
                        "topologyRevision",
                        "configRevision",
                        "coordinateContractRevision",
                    )
                )
                else "SUCCESS_ONLY_SAMPLE_BIAS"
            ),
            "metrics": metrics,
            "quality": {
                "inputTaskCount": len(tasks_source) if tasks_available else None,
                "deduplicatedTaskCount": len(tasks),
                "successfulSampleCount": len(successful_samples),
                "invalidTaskRecordCount": invalid_task_record_count,
                "conflictingTaskIdCount": conflicting_task_id_count,
                "missingOrInvalidPlanCount": missing_or_invalid_plan_count,
                "failedPlanCount": failed_plan_count,
                "invalidAlgorithmCount": invalid_algorithm_count,
                "excludedChildTaskCount": child_task_count,
                "excludedNonShuttleTaskCount": non_shuttle_task_count,
                "invalidPlanningTimeCount": field_invalid_counts["planning_time_ms"],
                "invalidHorizontalDistanceCount": field_invalid_counts[
                    "horizontal_distance_cells"
                ],
                "invalidVisitedStateCount": field_invalid_counts["visited_states"],
            },
            "acceptanceEligible": False,
        }

    def get_conveyor_coordination_metrics(self, run_id: str) -> dict[str, Any] | None:
        """Return current persisted conveyor/queue proxies without changing simulation state."""

        with get_read_conn() as conn:
            row = conn.execute(
                "SELECT metrics FROM simulation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None

        try:
            source_metrics = decode_json(row["metrics"])
        except (TypeError, ValueError):
            source_metrics = None
        metrics_available = isinstance(source_metrics, dict)
        metrics: list[dict[str, Any]] = []

        def add_metric(
            metric_id: str,
            display_name: str,
            value: Any,
            unit: str,
            sample_count: int,
            implementation_status: str,
            data_quality: str,
            reason: str | None,
            **extra: Any,
        ) -> None:
            metric = {
                "metricId": metric_id,
                "displayName": display_name,
                "value": value,
                "unit": unit,
                "sampleCount": sample_count,
                "implementationStatus": implementation_status,
                "dataQuality": data_quality,
                "reason": reason,
                "acceptanceEligible": False,
            }
            metric.update(extra)
            metrics.append(metric)

        graph_nodes = source_metrics.get("conveyorGraphNodes") if metrics_available else None
        utilization = source_metrics.get("conveyorUtilization") if metrics_available else None
        graph_nodes_valid = (
            isinstance(graph_nodes, int) and not isinstance(graph_nodes, bool) and graph_nodes >= 0
        )
        utilization_valid = (
            isinstance(utilization, (int, float))
            and not isinstance(utilization, bool)
            and math.isfinite(utilization)
            and 0 <= utilization <= 100
        )
        if not metrics_available:
            occupancy_value = None
            occupancy_quality = "MISSING_SOURCE"
            occupancy_reason = "METRICS_SOURCE_MISSING"
        elif "conveyorGraphNodes" not in source_metrics:
            occupancy_value = None
            occupancy_quality = "MISSING_SOURCE"
            occupancy_reason = "CONVEYOR_GRAPH_NODES_MISSING"
        elif not graph_nodes_valid:
            occupancy_value = None
            occupancy_quality = "DEGRADED"
            occupancy_reason = "INVALID_CONVEYOR_GRAPH_NODES"
        elif graph_nodes == 0:
            occupancy_value = None
            occupancy_quality = "MISSING_SOURCE"
            occupancy_reason = "NO_CONVEYOR_GRAPH"
        elif "conveyorUtilization" not in source_metrics:
            occupancy_value = None
            occupancy_quality = "MISSING_SOURCE"
            occupancy_reason = "CONVEYOR_UTILIZATION_MISSING"
        elif not utilization_valid:
            occupancy_value = None
            occupancy_quality = "DEGRADED"
            occupancy_reason = "INVALID_CONVEYOR_UTILIZATION"
        else:
            occupancy_value = utilization
            occupancy_quality = "GOOD"
            occupancy_reason = None
        add_metric(
            "CONVEYOR_NODE_OCCUPANCY_CURRENT",
            "当前输送节点占用率",
            occupancy_value,
            "%",
            1 if occupancy_value is not None else 0,
            "IMPLEMENTED",
            occupancy_quality,
            occupancy_reason,
            sourceFields=["conveyorUtilization", "conveyorGraphNodes"],
            graphNodeCount=graph_nodes if graph_nodes_valid else None,
            semantics="occupied_distinct_conveyor_nodes_percent_not_physical_utilization",
            alreadyPercent=True,
        )

        queue_specs = (
            (
                "CONVEYOR_WAIT_TASK_COUNT_CURRENT",
                "当前输送等待任务数",
                "conveyorQueueDepth",
                "waiting_tasks_with_conveyor_transport_waiting_ticks_above_zero_not_wait_duration",
            ),
            (
                "BUFFER_WAIT_TASK_COUNT_CURRENT",
                "当前缓存相关等待任务数",
                "bufferOccupancy",
                "tasks_in_waiting_delivery_slot_or_pickup_from_port_not_physical_buffer_utilization",
            ),
            (
                "STATION_QUEUE_TASK_COUNT_CURRENT",
                "站台相关阶段任务数（代理）",
                "stationQueueDepth",
                "tasks_in_conveyor_to_pick_picking_or_place_goods_not_pure_waiting_queue",
            ),
        )
        for metric_id, display_name, field, semantics in queue_specs:
            if not metrics_available:
                value = None
                quality = "MISSING_SOURCE"
                reason = "METRICS_SOURCE_MISSING"
            elif field not in source_metrics:
                value = None
                quality = "MISSING_SOURCE"
                reason = "FIELD_MISSING"
            else:
                candidate = source_metrics[field]
                if (
                    isinstance(candidate, int)
                    and not isinstance(candidate, bool)
                    and candidate >= 0
                ):
                    value = candidate
                    quality = "GOOD"
                    reason = None
                else:
                    value = None
                    quality = "DEGRADED"
                    reason = "INVALID_FIELD_VALUE"
            add_metric(
                metric_id,
                display_name,
                value,
                "task",
                1 if value is not None else 0,
                "IMPLEMENTED",
                quality,
                reason,
                sourceField=field,
                semantics=semantics,
            )

        for metric_id, display_name, unit, reason in (
            (
                "DEVICE_UTILIZATION",
                "协同设备利用率",
                "%",
                "NO_PER_DEVICE_BUSY_AND_AVAILABLE_INTERVALS",
            ),
            (
                "CONVEYOR_PHYSICAL_UTILIZATION",
                "输送线物理利用率",
                "%",
                "NO_CAPACITY_AND_TIME_WEIGHTED_OCCUPANCY",
            ),
            (
                "BOTTLENECK_WAIT_DURATION",
                "瓶颈资源等待时间",
                "second",
                "NO_RESOURCE_WAIT_START_END_INTERVALS",
            ),
            (
                "THROUGHPUT_ORDER_LINES_PER_HOUR",
                "吞吐量（订单行/小时）",
                "order_line/hour",
                "NO_UNIFIED_ORDER_LINE_AND_REAL_TIME_WINDOW",
            ),
        ):
            add_metric(
                metric_id,
                display_name,
                None,
                unit,
                0,
                "UNAVAILABLE",
                "MISSING_SOURCE",
                reason,
            )

        implemented = [
            metric for metric in metrics if metric["implementationStatus"] == "IMPLEMENTED"
        ]
        invalid_or_missing = [metric for metric in implemented if metric["value"] is None]
        return {
            "runId": run_id,
            "scope": "current_persisted_final_metrics",
            "dataQuality": (
                "MISSING_SOURCE"
                if not metrics_available
                else "DEGRADED"
                if invalid_or_missing
                else "GOOD"
            ),
            "reason": (
                "METRICS_SOURCE_MISSING"
                if not metrics_available
                else "CURRENT_METRIC_FIELDS_MISSING_OR_INVALID"
                if invalid_or_missing
                else None
            ),
            "metrics": metrics,
            "historyTrendSource": "/api/v1/simulations/{run_id}/metrics/history/quality",
            "acceptanceEligible": False,
        }

    def get_task_metric_summary(self, run_id: str) -> dict[str, Any] | None:
        """Return run-scoped task metrics without initializing or mutating the database."""

        with get_read_conn() as conn:
            row = conn.execute(
                "SELECT payload, metrics FROM simulation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            event_rows = conn.execute(
                """SELECT tick, event_type, payload FROM simulation_events
                   WHERE run_id = ?
                     AND event_type IN ('task_state_changed', 'transfer_stage')
                   ORDER BY tick ASC, id ASC""",
                (run_id,),
            ).fetchall()

        try:
            payload = decode_json(row["payload"])
        except (TypeError, ValueError):
            payload = None
        try:
            legacy_metrics = decode_json(row["metrics"])
        except (TypeError, ValueError):
            legacy_metrics = None

        tasks_source = payload.get("tasks") if isinstance(payload, dict) else None
        tasks_available = isinstance(tasks_source, list)
        tasks, invalid_task_count, conflict_count = (
            _deduplicate_metric_tasks(tasks_source) if tasks_available else ([], 0, 0)
        )

        first_assignment_ticks: dict[str, int] = {}
        for event_row in event_rows:
            tick = event_row["tick"]
            if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
                continue
            try:
                event_payload = decode_json(event_row["payload"])
            except (TypeError, ValueError):
                continue
            if not isinstance(event_payload, dict):
                continue
            task_id = event_payload.get("taskId")
            robot_id = event_payload.get("robotId")
            if not isinstance(task_id, str) or not task_id.strip():
                continue
            if not isinstance(robot_id, str) or not robot_id.strip():
                continue
            is_assignment = (
                event_row["event_type"] == "task_state_changed"
                and event_payload.get("status") == "assigned"
            ) or (
                event_row["event_type"] == "transfer_stage"
                and event_payload.get("stage") == "pickup_wait"
            )
            if is_assignment:
                current = first_assignment_ticks.get(task_id)
                first_assignment_ticks[task_id] = tick if current is None else min(current, tick)

        for task in tasks:
            task_id = task["id"]
            history = task.get("history")
            if not isinstance(history, list):
                continue
            is_shuttle = task.get("mode") == "shuttle_transfer" or task.get("robotType") == "shuttle"
            for entry in history:
                if not isinstance(entry, dict) or entry.get("status") != "assigned":
                    continue
                tick = entry.get("tick")
                phase = entry.get("phase")
                if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
                    continue
                if not isinstance(phase, str) or not phase.strip():
                    continue
                if is_shuttle and phase != "pickup_wait":
                    continue
                if not is_shuttle and phase == "pending_assignment":
                    continue
                current = first_assignment_ticks.get(task_id)
                first_assignment_ticks[task_id] = tick if current is None else min(current, tick)

        summary = _compute_task_duration_summary(tasks, first_assignment_ticks)
        legacy_value = None
        if isinstance(legacy_metrics, dict):
            candidate = legacy_metrics.get("completedTasks")
            if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
                legacy_value = candidate

        metrics: list[dict[str, Any]] = []

        def add_metric(
            metric_id: str,
            value: Any,
            unit: str,
            sample_count: int | None,
            data_quality: str,
            reason: str | None,
        ) -> None:
            metrics.append(
                {
                    "metricId": metric_id,
                    "value": value,
                    "unit": unit,
                    "sampleCount": sample_count,
                    "implementationStatus": "PARTIAL",
                    "dataQuality": data_quality,
                    "reason": reason,
                    "acceptanceEligible": False,
                }
            )

        if not tasks_available:
            source_reason = "TASKS_SOURCE_MISSING"
            for metric_id, unit in (
                ("PARENT_COMPLETED_TASK_COUNT", "task"),
                ("CHILD_COMPLETED_TASK_COUNT", "task"),
                ("PARENT_COMPLETED_TASK_COUNT_DELTA", "task"),
                ("INVALID_TASK_RECORD_COUNT", "task_record"),
                ("CONFLICTING_DUPLICATE_TASK_ID_COUNT", "task_id"),
            ):
                add_metric(metric_id, None, unit, None, "MISSING_SOURCE", source_reason)
        else:
            base_quality = "DEGRADED" if invalid_task_count else "GOOD"
            base_reason = "INVALID_TASK_RECORDS_EXCLUDED" if invalid_task_count else None
            if conflict_count:
                for metric_id in ("PARENT_COMPLETED_TASK_COUNT", "CHILD_COMPLETED_TASK_COUNT"):
                    add_metric(metric_id, None, "task", None, "DEGRADED", "DUPLICATE_TASK_CONFLICT")
                add_metric(
                    "PARENT_COMPLETED_TASK_COUNT_DELTA",
                    None,
                    "task",
                    None,
                    "DEGRADED",
                    "DUPLICATE_TASK_CONFLICT",
                )
            else:
                parent_completed = summary["parent"]["completedCount"]
                child_completed = summary["child"]["completedCount"]
                add_metric(
                    "PARENT_COMPLETED_TASK_COUNT",
                    parent_completed,
                    "task",
                    parent_completed,
                    base_quality,
                    base_reason,
                )
                add_metric(
                    "CHILD_COMPLETED_TASK_COUNT",
                    child_completed,
                    "task",
                    child_completed,
                    base_quality,
                    base_reason,
                )
                delta = parent_completed - legacy_value if legacy_value is not None else None
                add_metric(
                    "PARENT_COMPLETED_TASK_COUNT_DELTA",
                    delta,
                    "task",
                    parent_completed,
                    base_quality if legacy_value is not None else "MISSING_SOURCE",
                    base_reason if legacy_value is not None else "LEGACY_COMPLETED_TASKS_MISSING",
                )
            add_metric(
                "INVALID_TASK_RECORD_COUNT",
                invalid_task_count,
                "task_record",
                invalid_task_count,
                base_quality,
                base_reason,
            )
            add_metric(
                "CONFLICTING_DUPLICATE_TASK_ID_COUNT",
                conflict_count,
                "task_id",
                conflict_count,
                "DEGRADED" if conflict_count else "GOOD",
                "DUPLICATE_TASK_CONFLICT" if conflict_count else None,
            )

        add_metric(
            "LEGACY_COMPLETED_TASKS",
            legacy_value,
            "task",
            None,
            "GOOD" if legacy_value is not None else "MISSING_SOURCE",
            None if legacy_value is not None else "LEGACY_COMPLETED_TASKS_MISSING",
        )

        for parentage, prefix in (("parent", "PARENT"), ("child", "CHILD")):
            bucket = summary[parentage]
            for duration_key, metric_stem, excluded_key, excluded_metric_id in (
                (
                    "createDurations",
                    "CREATE_TO_COMPLETE",
                    "createExcludedReasonCounts",
                    f"{prefix}_CREATE_TO_COMPLETE_EXCLUDED_COUNT",
                ),
                (
                    "firstAssignmentDurations",
                    "FIRST_ASSIGN_TO_COMPLETE",
                    "firstAssignmentExcludedReasonCounts",
                    f"{prefix}_FIRST_ASSIGN_TO_COMPLETE_MISSING_COUNT",
                ),
            ):
                values = bucket[duration_key]
                excluded_reasons = bucket[excluded_key]
                excluded_count = sum(excluded_reasons.values())
                if not tasks_available:
                    avg_value = max_value = None
                    quality = "MISSING_SOURCE"
                    reason = "TASKS_SOURCE_MISSING"
                    sample_count = None
                    count_value = None
                elif conflict_count:
                    avg_value = max_value = None
                    quality = "DEGRADED"
                    reason = "DUPLICATE_TASK_CONFLICT"
                    sample_count = None
                    count_value = None
                else:
                    sample_count = len(values)
                    count_value = sample_count
                    avg_value = sum(values) / sample_count if sample_count else None
                    max_value = max(values) if sample_count else None
                    quality = "DEGRADED" if excluded_count or invalid_task_count else "GOOD"
                    if sample_count == 0:
                        reason = "NO_VALID_DURATION_SAMPLES"
                    elif excluded_count:
                        reason = "PARTIAL_SAMPLES_EXCLUDED"
                    elif invalid_task_count:
                        reason = "INVALID_TASK_RECORDS_EXCLUDED"
                    else:
                        reason = None
                add_metric(
                    f"{prefix}_{metric_stem}_AVG_TICKS",
                    avg_value,
                    "Tick",
                    sample_count,
                    quality,
                    reason,
                )
                add_metric(
                    f"{prefix}_{metric_stem}_MAX_TICKS",
                    max_value,
                    "Tick",
                    sample_count,
                    quality,
                    reason,
                )
                add_metric(
                    f"{prefix}_{metric_stem}_SAMPLE_COUNT",
                    count_value,
                    "task",
                    sample_count,
                    quality,
                    reason if count_value is None else None,
                )
                add_metric(
                    excluded_metric_id,
                    None if not tasks_available or conflict_count else excluded_count,
                    "task",
                    None if not tasks_available or conflict_count else excluded_count,
                    quality,
                    reason if not tasks_available or conflict_count else (
                        ",".join(sorted(excluded_reasons)) if excluded_reasons else None
                    ),
                )

        return {"runId": run_id, "metrics": metrics}

    def get_throughput_metrics(
        self,
        run_id: str,
        generation: int | None = None,
    ) -> dict[str, Any] | None:
        """Aggregate outbound-delivery evidence through read-only SQLite connections."""

        if generation is not None and (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            raise ValueError("generation must be a positive integer")
        with get_read_conn() as conn:
            run_row = conn.execute(
                "SELECT status, payload FROM simulation_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run_row is None:
                return None
            event_rows = conn.execute(
                """SELECT id, tick, event_type, payload
                   FROM simulation_events
                   WHERE run_id = ? AND event_type = ?
                   ORDER BY id ASC""",
                (run_id, DELIVERY_EVENT_TYPE),
            ).fetchall()
        state = decode_json(run_row["payload"])
        decoded_events = [
            {
                "id": int(row["id"]),
                "tick": int(row["tick"]),
                "event_type": row["event_type"],
                "payload": decode_json(row["payload"]),
            }
            for row in event_rows
        ]
        deadlock = self.get_deadlock_lifecycle_metrics(run_id, generation=generation)
        return build_throughput_metrics(
            run_id,
            str(run_row["status"]),
            state,
            decoded_events,
            generation=generation,
            deadlock_metrics=deadlock,
        )

    def add_event(self, run_id: str, tick: int, event_type: str, payload: dict[str, Any]) -> None:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO simulation_events (run_id, tick, event_type, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, tick, event_type, encode_json(payload), utc_now()),
            )

    @staticmethod
    def _decode_payload(item: dict[str, Any]) -> dict[str, Any]:
        if item:
            item["payload"] = decode_json(item["payload"])
        return item

    @staticmethod
    def _decode_run(item: dict[str, Any]) -> dict[str, Any]:
        if item:
            item["payload"] = decode_json(item["payload"])
            item["metrics"] = decode_json(item["metrics"])
        return item
