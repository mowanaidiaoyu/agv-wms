"""Pure, read-only aggregation for persisted P3 movement evidence.

The module deliberately has no application, database, runtime, or framework
imports.  Persisted ``robot_movement_batch_v1`` events are the authority for
executed movement; the bounded Run diagnostic snapshot is used only to select
the frozen physics contract and to assess persistence completeness.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from math import isfinite
from typing import Any, Iterable, Mapping


EVENT_TYPE = "robot_movement_batch_v1"
DIAGNOSTICS_NAMESPACE = "motionDistanceDiagnosticsV1"
MOVEMENT_TYPES = (
    "NORMAL",
    "YIELD",
    "BACKOFF",
    "RETURN_TO_STANDBY",
    "LIFT",
    "UNKNOWN",
)
TOP_ROBOT_LIMIT = 20
SOURCE = "simulation_events.robot_movement_batch_v1"


def _integer(value: object, *, minimum: int = 0) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def _number(value: object, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not isfinite(result) or (result <= 0 if positive else result < 0):
        return None
    return result


def _identifier(value: object) -> str | None:
    return value if isinstance(value, str) and bool(value.strip()) else None


def _payload(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _canonical(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return repr(value)


def _position(value: object) -> tuple[float | None, float | None, str | None, int | None, int | None] | None:
    if not isinstance(value, Mapping):
        return None
    row = value.get("row")
    col = value.get("col")
    if row is not None:
        row = _number(row)
        if row is None:
            return None
    if col is not None:
        col = _number(col)
        if col is None:
            return None
    floor = value.get("floor")
    if floor is not None and _identifier(floor) is None:
        return None
    layer = value.get("layer")
    if layer is not None:
        if isinstance(layer, bool) or not isinstance(layer, (int, float)) or not isfinite(float(layer)):
            return None
        rounded = round(float(layer))
        if abs(float(layer) - rounded) > 1e-9:
            return None
        layer = int(rounded)
    floor_index = value.get("floorIndex")
    if floor_index is not None and _integer(floor_index) is None:
        return None
    return row, col, floor, layer, floor_index


def _same_position(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
    return left == right


def _metric(
    value: int | float | None,
    unit: str,
    sample_count: int,
    quality: str,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "sampleCount": sample_count,
        "dataQuality": quality,
        "reason": reason,
        "source": SOURCE,
    }


@dataclass
class _Movement:
    movement_id: str
    robot_id: str
    robot_type: str
    movement_type: str
    from_tick: int
    to_tick: int
    event_sequence: int
    from_position: tuple[Any, ...]
    to_position: tuple[Any, ...]
    horizontal: float | None
    vertical: float | None
    quality: str
    physics_version: str | None
    batch_tick: int


def _extract_movement(
    item: Mapping[str, Any],
    *,
    run_id: str,
    generation: int,
    run_instance_id: str,
    batch_tick: int,
) -> tuple[_Movement | None, str | None]:
    movement_id = _identifier(item.get("movementId"))
    robot_id = _identifier(item.get("robotId"))
    robot_type = _identifier(item.get("robotType"))
    from_tick = _integer(item.get("fromTick"))
    to_tick = _integer(item.get("toTick"))
    event_sequence = _integer(item.get("eventSequence"), minimum=1)
    from_position = _position(item.get("fromPosition"))
    to_position = _position(item.get("toPosition"))
    if not all((movement_id, robot_id, robot_type)):
        return None, "INVALID_IDENTIFIER"
    if item.get("runId") != run_id or item.get("generation") != generation:
        return None, "MOVEMENT_SCOPE_MISMATCH"
    if item.get("runInstanceId") != run_instance_id:
        return None, "RUN_INSTANCE_MISMATCH"
    if from_tick is None or to_tick is None or from_tick > to_tick or to_tick != batch_tick:
        return None, "INVALID_TICK"
    if event_sequence is None:
        return None, "INVALID_EVENT_SEQUENCE"
    if from_position is None or to_position is None:
        return None, "INVALID_POSITION"
    movement_type = item.get("movementType")
    if movement_type not in MOVEMENT_TYPES:
        movement_type = "UNKNOWN"
    horizontal = _number(item.get("segmentHorizontalMeters"))
    vertical = _number(item.get("segmentVerticalMeters"))
    quality = item.get("dataQuality")
    if quality not in {"COMPLETE", "INCOMPLETE", "UNAVAILABLE", "INVALID"}:
        return None, "INVALID_DATA_QUALITY"
    return (
        _Movement(
            movement_id=movement_id,
            robot_id=robot_id,
            robot_type=robot_type,
            movement_type=movement_type,
            from_tick=from_tick,
            to_tick=to_tick,
            event_sequence=event_sequence,
            from_position=from_position,
            to_position=to_position,
            horizontal=horizontal,
            vertical=vertical,
            quality=quality,
            physics_version=_identifier(item.get("physicsContractVersion")),
            batch_tick=batch_tick,
        ),
        None,
    )


def _snapshot_contract(snapshot: Mapping[str, Any] | None, target_generation: int) -> tuple[Mapping[str, Any] | None, str | None]:
    if not snapshot or snapshot.get("generation") != target_generation:
        return None, None
    contract = _payload(snapshot.get("physicsContract"))
    if contract is None:
        return None, None
    version = _identifier(contract.get("physicsContractVersion"))
    meters = _number(contract.get("metersPerCell"), positive=True)
    shuttle_height = _number(contract.get("shuttleLevelHeightMeters"), positive=True)
    floor_height = _number(contract.get("floorHeightMeters"), positive=True)
    coordinate_version = _identifier(contract.get("coordinateContractVersion"))
    if not version or meters is None or shuttle_height is None or floor_height is None or not coordinate_version:
        return None, None
    if snapshot.get("physicsContractVersion") != version or snapshot.get("metersPerCell") != contract.get("metersPerCell"):
        return None, None
    return contract, version


def _quality_for_run(status: str, snapshot: Mapping[str, Any] | None) -> tuple[str, str | None]:
    if snapshot is None:
        return "INCOMPLETE", "MOTION_DIAGNOSTIC_SNAPSHOT_MISSING"
    if snapshot.get("historicalRecoveryIncomplete") is True:
        return "INCOMPLETE", "HISTORICAL_RECOVERY_INCOMPLETE"
    if (_integer(snapshot.get("persistenceFailureCount")) or 0) > 0:
        return "INCOMPLETE", "EVENT_PERSISTENCE_FAILED"
    if (_integer(snapshot.get("pendingPersistenceCount")) or 0) > 0:
        return "INCOMPLETE", "EVENT_PERSISTENCE_PENDING"
    if snapshot.get("observationCompleteness") != "COMPLETE":
        return "INCOMPLETE", "OBSERVATION_INCOMPLETE"
    if status in {"running", "paused", "pending"} or snapshot.get("ended") is not True:
        return "DEGRADED", "RUN_IN_PROGRESS"
    end_reason = str(snapshot.get("endReason") or "")
    if end_reason in {"safety_limit", "runtime_error"}:
        return "INCOMPLETE", end_reason.upper()
    if status in {"stopped", "cancelled"}:
        return "DEGRADED", "RUN_STOPPED_BEFORE_NORMAL_COMPLETION"
    return "COMPLETE", None


def aggregate_motion_distance_metrics(
    run_id: str,
    run_status: str,
    run_payload: Mapping[str, Any] | None,
    event_rows: Iterable[Mapping[str, Any]],
    requested_generation: int | None = None,
) -> dict[str, Any]:
    """Aggregate one generation without mutating inputs or deriving missing motion."""

    state = run_payload if isinstance(run_payload, Mapping) else {}
    snapshot_value = state.get(DIAGNOSTICS_NAMESPACE)
    snapshot = snapshot_value if isinstance(snapshot_value, Mapping) else None
    snapshot_generation = _integer(snapshot.get("generation"), minimum=1) if snapshot else None
    target_generation = requested_generation or snapshot_generation
    selected_batches: list[tuple[int, Mapping[str, Any]]] = []
    observed_generations: set[int] = set()
    malformed_batch_count = 0

    for row in event_rows:
        batch = _payload(row.get("payload"))
        if batch is None:
            malformed_batch_count += 1
            continue
        batch_generation = _integer(batch.get("generation"), minimum=1)
        if batch.get("runId") != run_id or batch_generation is None:
            malformed_batch_count += 1
            continue
        observed_generations.add(batch_generation)
        if requested_generation is None and (target_generation is None or batch_generation > target_generation):
            target_generation = batch_generation
            selected_batches.clear()
        if target_generation == batch_generation:
            row_tick = _integer(row.get("tick"))
            if row_tick is None:
                malformed_batch_count += 1
                continue
            selected_batches.append((row_tick, batch))

    if target_generation is None:
        return _empty_contract(run_id, None, malformed_batch_count, "NO_MOTION_DISTANCE_EVIDENCE")

    expected_instance = f"{run_id}:generation-{target_generation}"
    target_snapshot = snapshot if snapshot_generation == target_generation and snapshot.get("runInstanceId") == expected_instance else None
    contract, physics_version = _snapshot_contract(target_snapshot, target_generation)
    physics_available = contract is not None and physics_version is not None
    meters_per_cell = contract.get("metersPerCell") if contract else None
    frozen_contract = _payload(state.get("motionPhysicsContract"))
    physics_contract_source = (
        frozen_contract.get("source")
        if frozen_contract and frozen_contract.get("source") == "USER_DEFINED_SIMULATION"
        else None
    )
    calibration_status = (
        frozen_contract.get("calibrationStatus")
        if frozen_contract and frozen_contract.get("calibrationStatus") == "UNVERIFIED"
        else None
    )

    raw_count = 0
    event_batch_count = 0
    excluded_batch_count = 0
    invalid_count = 0
    invalid_reasons: Counter[str] = Counter()
    grouped: dict[str, dict[str, tuple[Mapping[str, Any], _Movement]]] = defaultdict(dict)
    occurrences: Counter[tuple[str, str]] = Counter()

    for row_tick, batch in selected_batches:
        batch_tick = _integer(batch.get("tick"))
        movements = batch.get("movements")
        if batch.get("runInstanceId") != expected_instance or batch_tick != row_tick or not isinstance(movements, list):
            excluded_batch_count += 1
            continue
        event_batch_count += 1
        declared_count = _integer(batch.get("movementCount"))
        if declared_count != len(movements) or not 1 <= len(movements) <= 256:
            invalid_reasons["INVALID_BATCH_COUNT"] += len(movements) or 1
        for item in movements:
            raw_count += 1
            if not isinstance(item, Mapping):
                invalid_count += 1
                invalid_reasons["INVALID_MOVEMENT_PAYLOAD"] += 1
                continue
            movement, issue = _extract_movement(
                item,
                run_id=run_id,
                generation=target_generation,
                run_instance_id=expected_instance,
                batch_tick=batch_tick,
            )
            if movement is None:
                invalid_count += 1
                invalid_reasons[issue or "INVALID_MOVEMENT"] += 1
                continue
            signature = _canonical(item)
            occurrences[(movement.movement_id, signature)] += 1
            grouped[movement.movement_id][signature] = (item, movement)

    duplicate_count = sum(count - 1 for count in occurrences.values() if count > 1)
    conflict_ids = {movement_id for movement_id, variants in grouped.items() if len(variants) > 1}
    conflict_count = len(conflict_ids)
    unique_candidates: list[_Movement] = []
    for movement_id, variants in grouped.items():
        if movement_id not in conflict_ids:
            unique_candidates.append(next(iter(variants.values()))[1])

    physics_mismatch_count = 0
    incomplete_movement_count = invalid_count + conflict_count
    trusted: list[_Movement] = []
    for movement in unique_candidates:
        if physics_version is not None and movement.physics_version != physics_version:
            physics_mismatch_count += 1
            incomplete_movement_count += 1
            continue
        if movement.quality != "COMPLETE":
            incomplete_movement_count += 1
        elif movement.horizontal is None or movement.vertical is None:
            incomplete_movement_count += 1
        trusted.append(movement)

    by_robot: dict[str, list[_Movement]] = defaultdict(list)
    for movement in trusted:
        by_robot[movement.robot_id].append(movement)
    trajectory_breaks = 0
    incomplete_robots: set[str] = {
        movement.robot_id for movement in trusted if movement.quality != "COMPLETE"
    }
    for robot_id, movements in by_robot.items():
        movements.sort(key=lambda item: (item.to_tick, item.event_sequence, item.movement_id))
        previous: _Movement | None = None
        for movement in movements:
            if previous is not None and not _same_position(previous.to_position, movement.from_position):
                trajectory_breaks += 1
                incomplete_robots.add(robot_id)
            previous = movement

    snapshot_robots = target_snapshot.get("perRobotSummary") if target_snapshot else None
    robot_types: dict[str, str] = {}
    if isinstance(snapshot_robots, Mapping):
        for robot_id, item in snapshot_robots.items():
            if isinstance(robot_id, str) and isinstance(item, Mapping):
                robot_types[robot_id] = str(item.get("robotType") or "unknown")
                if (_integer(item.get("incompleteCount")) or 0) > 0:
                    incomplete_robots.add(robot_id)
    for movement in trusted:
        existing = robot_types.get(movement.robot_id)
        if existing is not None and existing != movement.robot_type:
            incomplete_robots.add(movement.robot_id)
        robot_types[movement.robot_id] = movement.robot_type

    snapshot_quality, snapshot_reason = _quality_for_run(run_status, target_snapshot)
    completeness_issues: list[str] = []
    if malformed_batch_count or excluded_batch_count or invalid_count:
        completeness_issues.append("MALFORMED_OR_SCOPE_MISMATCH_EVIDENCE")
    if conflict_count:
        completeness_issues.append("MOVEMENT_ID_CONFLICT")
    if physics_mismatch_count:
        completeness_issues.append("PHYSICS_VERSION_MISMATCH")
    if trajectory_breaks:
        completeness_issues.append("TRAJECTORY_DISCONTINUITY")

    last_movement = max(trusted, key=lambda item: (item.to_tick, item.event_sequence, item.movement_id), default=None)
    event_watermark = (
        {"movementId": last_movement.movement_id, "tick": last_movement.to_tick, "eventSequence": last_movement.event_sequence}
        if last_movement else None
    )
    snapshot_watermark = target_snapshot.get("eventPersistenceWatermark") if target_snapshot else None
    snapshot_total = _integer(target_snapshot.get("totalMovementCount")) if target_snapshot else None
    unique_count = len(unique_candidates)
    if target_snapshot and snapshot_total != unique_count:
        completeness_issues.append("SNAPSHOT_EVENT_COUNT_MISMATCH")
    if target_snapshot and snapshot_watermark != event_watermark:
        # Explicit zero movement has no watermark on either side and is not a mismatch.
        completeness_issues.append("SNAPSHOT_EVENT_WATERMARK_MISMATCH")

    generation_exists = target_generation in observed_generations or target_snapshot is not None
    if not generation_exists:
        return _empty_contract(run_id, target_generation, malformed_batch_count, "GENERATION_NOT_FOUND")

    if snapshot_quality == "UNAVAILABLE":
        overall_quality = "UNAVAILABLE"
    elif snapshot_quality == "INCOMPLETE" or completeness_issues:
        overall_quality = "INCOMPLETE"
    else:
        overall_quality = snapshot_quality
    if not physics_available and overall_quality == "COMPLETE":
        overall_quality = "UNAVAILABLE"
    overall_reason = snapshot_reason or (completeness_issues[0] if completeness_issues else None)
    if not physics_available:
        overall_reason = overall_reason or "PHYSICS_CONTRACT_UNAVAILABLE"

    known = [m for m in trusted if m.quality == "COMPLETE" and m.horizontal is not None and m.vertical is not None]
    known_ids = {m.movement_id for m in known}
    distance_values_allowed = physics_available
    known_horizontal = sum(m.horizontal or 0.0 for m in known)
    known_vertical = sum(m.vertical or 0.0 for m in known)
    valid_explicit_zero = bool(
        physics_available
        and target_snapshot
        and snapshot_total == 0
        and unique_count == 0
        and snapshot_quality in {"COMPLETE", "DEGRADED"}
        and not completeness_issues
    )
    distance_quality = overall_quality if distance_values_allowed else "UNAVAILABLE"
    distance_reason = overall_reason if distance_values_allowed else "PHYSICS_CONTRACT_UNAVAILABLE"
    distance_sample_count = len(known)
    if not known and not valid_explicit_zero:
        total_horizontal: float | None = None
        total_vertical: float | None = None
    else:
        total_horizontal = known_horizontal if distance_values_allowed else None
        total_vertical = known_vertical if distance_values_allowed else None

    def horizontal_for(robot_type: str) -> float | None:
        matching = [m for m in known if m.robot_type.lower() == robot_type]
        if not matching and not valid_explicit_zero:
            return None
        return sum(m.horizontal or 0.0 for m in matching) if distance_values_allowed else None

    lift_movements = [m for m in known if m.movement_type == "LIFT"]
    lift_vertical = (
        sum(m.vertical or 0.0 for m in lift_movements) if distance_values_allowed and (lift_movements or valid_explicit_zero) else None
    )

    total_metrics = {
        "totalHorizontalMeters": _metric(total_horizontal, "m", distance_sample_count, distance_quality, distance_reason),
        "totalVerticalMeters": _metric(total_vertical, "m", distance_sample_count, distance_quality, distance_reason),
        "ctuHorizontalMeters": _metric(horizontal_for("ctu"), "m", sum(m.robot_type.lower() == "ctu" for m in known), distance_quality, distance_reason),
        "shuttleHorizontalMeters": _metric(horizontal_for("shuttle"), "m", sum(m.robot_type.lower() == "shuttle" for m in known), distance_quality, distance_reason),
        "shuttleLiftVerticalMeters": _metric(lift_vertical, "m", len(lift_movements), distance_quality, distance_reason),
        "totalMovementCount": _metric(unique_count, "movement", raw_count, overall_quality, overall_reason),
        "robotCount": _metric(len(robot_types), "robot", len(robot_types), overall_quality, overall_reason),
        "completeRobotCount": _metric(len(robot_types) - len(incomplete_robots), "robot", len(robot_types), overall_quality, overall_reason),
        "incompleteRobotCount": _metric(len(incomplete_robots), "robot", len(robot_types), overall_quality, overall_reason),
    }

    vehicle_metrics: dict[str, Any] = {}
    for vehicle_type in ("ctu", "shuttle"):
        items = [m for m in known if m.robot_type.lower() == vehicle_type]
        value = sum(m.horizontal or 0.0 for m in items) if distance_values_allowed and (items or valid_explicit_zero) else None
        vehicle_metrics[vehicle_type.upper()] = {
            "horizontalMeters": _metric(value, "m", len(items), distance_quality, distance_reason),
            "movementCount": len([m for m in trusted if m.robot_type.lower() == vehicle_type]),
        }

    movement_metrics: dict[str, Any] = {}
    for movement_type in MOVEMENT_TYPES:
        items = [m for m in known if m.movement_type == movement_type]
        evidence_items = [m for m in trusted if m.movement_type == movement_type]
        quality = "INCOMPLETE" if any(m.robot_id in incomplete_robots or m.quality != "COMPLETE" for m in evidence_items) else distance_quality
        movement_metrics[movement_type] = {
            "movementCount": len(evidence_items),
            "horizontalMeters": sum(m.horizontal or 0.0 for m in items) if distance_values_allowed and (items or valid_explicit_zero) else None,
            "verticalMeters": sum(m.vertical or 0.0 for m in items) if distance_values_allowed and (items or valid_explicit_zero) else None,
            "unit": "m",
            "dataQuality": quality,
            "source": SOURCE,
        }

    robot_results: list[dict[str, Any]] = []
    for robot_id, robot_type in robot_types.items():
        items = by_robot.get(robot_id, [])
        robot_known = [m for m in items if m.movement_id in known_ids]
        robot_incomplete = robot_id in incomplete_robots or any(m.quality != "COMPLETE" for m in items)
        robot_quality = "INCOMPLETE" if robot_incomplete else distance_quality
        has_zero_proof = valid_explicit_zero and not items
        horizontal = sum(m.horizontal or 0.0 for m in robot_known) if distance_values_allowed and (robot_known or has_zero_proof) else None
        vertical = sum(m.vertical or 0.0 for m in robot_known) if distance_values_allowed and (robot_known or has_zero_proof) else None
        robot_results.append(
            {
                "robotId": robot_id,
                "robotType": robot_type,
                "horizontalMeters": horizontal,
                "verticalMeters": vertical,
                "movementCount": len(items),
                "yieldMeters": sum(m.horizontal or 0.0 for m in robot_known if m.movement_type == "YIELD") if horizontal is not None else None,
                "backoffMeters": sum(m.horizontal or 0.0 for m in robot_known if m.movement_type == "BACKOFF") if horizontal is not None else None,
                "returnToStandbyMeters": sum(m.horizontal or 0.0 for m in robot_known if m.movement_type == "RETURN_TO_STANDBY") if horizontal is not None else None,
                "observationCompleteness": "INCOMPLETE" if robot_incomplete else "COMPLETE",
                "dataQuality": robot_quality,
            }
        )
    robot_results.sort(
        key=lambda item: (
            -(
                (item["horizontalMeters"] or 0.0) + (item["verticalMeters"] or 0.0)
                if item["horizontalMeters"] is not None or item["verticalMeters"] is not None
                else -1.0
            ),
            -(item["horizontalMeters"] if item["horizontalMeters"] is not None else -1.0),
            item["robotId"],
        )
    )
    total_robot_count = len(robot_results)
    robot_results = robot_results[:TOP_ROBOT_LIMIT]

    return {
        "contractVersion": "motion-distance-metrics-v1",
        "runId": run_id,
        "generation": target_generation,
        "runInstanceId": expected_instance,
        "physicsContractVersion": physics_version,
        "metersPerCell": meters_per_cell,
        "physicsContractSource": physics_contract_source,
        "calibrationStatus": calibration_status,
        "snapshotAsOfTick": target_snapshot.get("lastProcessedTick") if target_snapshot else None,
        "eventPersistenceWatermark": event_watermark,
        "totalMetrics": total_metrics,
        "vehicleTypeMetrics": vehicle_metrics,
        "movementTypeMetrics": movement_metrics,
        "perRobotMetrics": robot_results,
        "perRobotMetricsLimit": TOP_ROBOT_LIMIT,
        "totalRobotCount": total_robot_count,
        "perRobotMetricsTruncated": total_robot_count > TOP_ROBOT_LIMIT,
        "evidenceSummary": {
            "rawMovementCount": raw_count,
            "uniqueMovementCount": unique_count,
            "duplicateCount": duplicate_count,
            "conflictCount": conflict_count,
            "incompleteMovementCount": incomplete_movement_count,
            "eventBatchCount": event_batch_count,
            "malformedBatchCount": malformed_batch_count,
            "excludedBatchCount": excluded_batch_count,
            "physicsVersionMismatchCount": physics_mismatch_count,
            "trajectoryBreakCount": trajectory_breaks,
            "invalidReasonCounts": dict(sorted(invalid_reasons.items())),
            "eventPersistenceWatermark": event_watermark,
            "snapshotEventPersistenceWatermark": snapshot_watermark,
            "snapshotAsOfTick": target_snapshot.get("lastProcessedTick") if target_snapshot else None,
            "snapshotMovementCount": snapshot_total,
            "observationCompleteness": "INCOMPLETE" if overall_quality == "INCOMPLETE" else "COMPLETE",
            "consistencyIssues": completeness_issues,
        },
        "plannedHorizontalMeters": None,
        "extraDistanceMeters": None,
        "attributableReplanExtraMeters": None,
        "algorithmDistanceImprovementRate": None,
        "observationCompleteness": "INCOMPLETE" if overall_quality == "INCOMPLETE" else "COMPLETE",
        "dataQuality": overall_quality,
        "reason": overall_reason,
        "acceptanceEligible": False,
        "semantics": {
            "actualMovementSource": SOURCE,
            "liftVerticalMetersMeaning": "lift displacement; not autonomous vehicle horizontal mileage",
            "topRobotListIsComplete": total_robot_count <= TOP_ROBOT_LIMIT,
            "perRobotSort": "horizontalMeters + verticalMeters descending; robotId tie-break",
            "plannedActualComparisonAvailable": False,
            "exactlyOnceClaimed": False,
        },
    }


def _empty_contract(run_id: str, generation: int | None, malformed: int, reason: str) -> dict[str, Any]:
    unavailable = lambda unit: _metric(None, unit, 0, "UNAVAILABLE", reason)
    return {
        "contractVersion": "motion-distance-metrics-v1",
        "runId": run_id,
        "generation": generation,
        "runInstanceId": None,
        "physicsContractVersion": None,
        "metersPerCell": None,
        "physicsContractSource": None,
        "calibrationStatus": None,
        "snapshotAsOfTick": None,
        "eventPersistenceWatermark": None,
        "totalMetrics": {
            "totalHorizontalMeters": unavailable("m"),
            "totalVerticalMeters": unavailable("m"),
            "ctuHorizontalMeters": unavailable("m"),
            "shuttleHorizontalMeters": unavailable("m"),
            "shuttleLiftVerticalMeters": unavailable("m"),
            "totalMovementCount": _metric(0, "movement", 0, "UNAVAILABLE", reason),
            "robotCount": _metric(0, "robot", 0, "UNAVAILABLE", reason),
            "completeRobotCount": _metric(0, "robot", 0, "UNAVAILABLE", reason),
            "incompleteRobotCount": _metric(0, "robot", 0, "UNAVAILABLE", reason),
        },
        "vehicleTypeMetrics": {
            "CTU": {"horizontalMeters": unavailable("m"), "movementCount": 0},
            "SHUTTLE": {"horizontalMeters": unavailable("m"), "movementCount": 0},
        },
        "movementTypeMetrics": {
            name: {"movementCount": 0, "horizontalMeters": None, "verticalMeters": None, "unit": "m", "dataQuality": "UNAVAILABLE", "source": SOURCE}
            for name in MOVEMENT_TYPES
        },
        "perRobotMetrics": [],
        "perRobotMetricsLimit": TOP_ROBOT_LIMIT,
        "totalRobotCount": 0,
        "perRobotMetricsTruncated": False,
        "evidenceSummary": {
            "rawMovementCount": 0,
            "uniqueMovementCount": 0,
            "duplicateCount": 0,
            "conflictCount": 0,
            "incompleteMovementCount": 0,
            "eventBatchCount": 0,
            "malformedBatchCount": malformed,
            "eventPersistenceWatermark": None,
            "snapshotAsOfTick": None,
            "observationCompleteness": "UNAVAILABLE",
            "consistencyIssues": [],
        },
        "plannedHorizontalMeters": None,
        "extraDistanceMeters": None,
        "attributableReplanExtraMeters": None,
        "algorithmDistanceImprovementRate": None,
        "observationCompleteness": "UNAVAILABLE",
        "dataQuality": "UNAVAILABLE",
        "reason": reason,
        "acceptanceEligible": False,
        "semantics": {
            "actualMovementSource": SOURCE,
            "liftVerticalMetersMeaning": "lift displacement; not autonomous vehicle horizontal mileage",
            "topRobotListIsComplete": True,
            "perRobotSort": "horizontalMeters + verticalMeters descending; robotId tie-break",
            "plannedActualComparisonAvailable": False,
            "exactlyOnceClaimed": False,
        },
    }


__all__ = ["aggregate_motion_distance_metrics"]
