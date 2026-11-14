from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


CONTRACT_VERSION = "throughput-metrics-v1"
DELIVERY_EVENT_TYPE = "outbound_delivery_v1"
DELIVERY_EVENT_VERSION = "throughput-delivery-event-v1"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _deadlock_value(deadlock: dict[str, Any] | None, metric_id: str) -> float | None:
    for metric in (deadlock or {}).get("metrics") or []:
        if isinstance(metric, dict) and metric.get("metricId") == metric_id:
            return _number(metric.get("value"))
    return None


def build_throughput_metrics(
    run_id: str,
    run_status: str,
    state: dict[str, Any],
    event_rows: Iterable[dict[str, Any]],
    *,
    generation: int | None = None,
    deadlock_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate authoritative outbound deliveries without mutating Run data."""

    current_generation = max(1, int(state.get("throughputGeneration") or 1))
    selected_generation = generation or current_generation
    experiment = state.get("algorithmExperimentContract")
    experiment = experiment if isinstance(experiment, dict) else None
    configured_ticks = _int((experiment or {}).get("observationTicks"))
    current_tick = max(0, int(state.get("tick") or 0))
    observation_tick = min(current_tick, configured_ticks) if configured_ticks else current_tick
    speed = state.get("motionSpeedContract")
    speed = speed if isinstance(speed, dict) else None
    sim_tick_seconds = _number((speed or {}).get("simTickSeconds"))
    observation_seconds = (
        round(observation_tick * sim_tick_seconds, 9) if sim_tick_seconds is not None else None
    )

    unique: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    conflict_count = 0
    invalid_count = 0
    for row in event_rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            invalid_count += 1
            continue
        if payload.get("contractVersion") != DELIVERY_EVENT_VERSION:
            invalid_count += 1
            continue
        event_generation = _int(payload.get("generation"))
        if event_generation is None:
            invalid_count += 1
            continue
        if event_generation != selected_generation:
            continue
        tick = _int(payload.get("tick"))
        quantity = _int(payload.get("quantityPieces"))
        delivery_id = payload.get("deliveryId")
        if tick is not None and tick > observation_tick:
            continue
        if (
            not isinstance(delivery_id, str)
            or not delivery_id
            or tick is None
            or tick < 0
            or quantity is None
            or quantity <= 0
            or payload.get("flowKind") != "outbound_order"
            or payload.get("deliveryStage") != "drop_zone_received"
            or payload.get("evidenceSource") != "secondary_arm_dropzone_placed"
            or payload.get("runId") != run_id
            or not payload.get("orderId")
            or not payload.get("orderLineId")
            or not payload.get("skuId")
        ):
            invalid_count += 1
            continue
        previous = unique.get(delivery_id)
        if previous is None:
            unique[delivery_id] = payload
        elif previous == payload:
            duplicate_count += 1
        else:
            conflict_count += 1

    delivered_pieces = sum(int(item["quantityPieces"]) for item in unique.values())
    box_values = [item.get("quantityBoxes") for item in unique.values()]
    delivered_boxes = (
        sum(int(value) for value in box_values)
        if box_values and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in box_values)
        else None
    )
    by_tick: dict[int, int] = defaultdict(int)
    for item in unique.values():
        by_tick[int(item["tick"])] += int(item["quantityPieces"])
    cumulative = 0
    trend = [{"tick": 0, "simulatedSeconds": 0.0, "cumulativeDeliveredPieces": 0}]
    for tick in sorted(by_tick):
        cumulative += by_tick[tick]
        trend.append({
            "tick": tick,
            "simulatedSeconds": round(tick * sim_tick_seconds, 9) if sim_tick_seconds is not None else None,
            "cumulativeDeliveredPieces": cumulative,
        })
    if observation_tick and trend[-1]["tick"] != observation_tick:
        trend.append({
            "tick": observation_tick,
            "simulatedSeconds": observation_seconds,
            "cumulativeDeliveredPieces": cumulative,
        })

    orders = [item for item in state.get("orders", []) if isinstance(item, dict)]
    completed_orders = sum(
        1
        for order in orders
        if order.get("status") == "completed"
        and isinstance(order.get("completedTick"), int)
        and int(order["completedTick"]) <= observation_tick
    )
    root_tasks = [
        item for item in state.get("tasks", [])
        if isinstance(item, dict) and not item.get("parentId")
    ]
    lifecycle_seconds: list[float] = []
    if sim_tick_seconds is not None:
        for task in root_tasks:
            created = _int(task.get("createdTick"))
            completed = _int(task.get("completedTick"))
            if created is not None and completed is not None and created <= completed <= observation_tick:
                lifecycle_seconds.append((completed - created) * sim_tick_seconds)

    counters = state.get("performanceCounters")
    counters = counters if isinstance(counters, dict) else {}
    blocked_robot_ticks = _int(counters.get("blockedRobotTicks"))
    physical_wait_ticks = _int(counters.get("physicalTravelWaitRobotTicks"))
    counters_valid = (
        blocked_robot_ticks is not None
        and blocked_robot_ticks >= 0
        and physical_wait_ticks is not None
        and physical_wait_ticks >= 0
    )
    contract_available = state.get("throughputContractVersion") == CONTRACT_VERSION
    window_reached = configured_ticks is not None and current_tick >= configured_ticks
    evidence_valid = contract_available and not invalid_count and not conflict_count
    if not contract_available:
        quality = "UNAVAILABLE"
        reason = "THROUGHPUT_CONTRACT_UNAVAILABLE"
    elif invalid_count or conflict_count:
        quality = "INVALID"
        reason = "DELIVERY_EVIDENCE_INVALID"
    elif sim_tick_seconds is None or sim_tick_seconds <= 0:
        quality = "PARTIAL"
        reason = "SIM_TICK_SECONDS_MISSING"
    elif not counters_valid:
        quality = "PARTIAL"
        reason = "RUN_COUNTERS_INCOMPLETE"
    elif configured_ticks is not None and not window_reached:
        quality = "PARTIAL"
        reason = "OBSERVATION_WINDOW_INCOMPLETE"
    else:
        quality = "COMPLETE"
        reason = None
    throughput_rate = (
        delivered_pieces / (observation_seconds / 3600.0)
        if observation_seconds is not None and observation_seconds > 0 and evidence_valid
        else None
    )
    completion_rate = completed_orders / len(orders) * 100.0 if orders else None
    acceptance_eligible = bool(
        quality == "COMPLETE"
        and configured_ticks is not None
        and window_reached
        and current_tick == configured_ticks
        and observation_tick == configured_ticks
        and observation_seconds is not None
        and isinstance(experiment, dict)
        and experiment.get("scenarioSignature")
        and run_status == "stopped"
        and state.get("stopReason") == "observation_window_complete"
    )
    return {
        "contractVersion": CONTRACT_VERSION,
        "runId": run_id,
        "generation": selected_generation,
        "status": run_status,
        "stopReason": state.get("stopReason"),
        "algorithm": {
            "pathPolicy": (experiment or {}).get("pathPolicy"),
            "assignmentPolicy": (experiment or {}).get("assignmentPolicy"),
            "variableDimension": (experiment or {}).get("variableDimension"),
        },
        "scenarioSignature": (experiment or {}).get("scenarioSignature"),
        "experimentContract": experiment,
        "observationTick": observation_tick,
        "configuredObservationTicks": configured_ticks,
        "observationSeconds": observation_seconds,
        "deliveredPieces": delivered_pieces if contract_available and quality != "INVALID" else None,
        "deliveredBoxes": delivered_boxes if contract_available and quality != "INVALID" else None,
        "completedOrderCount": completed_orders,
        "totalOrderCount": len(orders),
        "orderCompletionRatePercent": completion_rate,
        "throughputPiecesPerSimulatedHour": throughput_rate,
        "deliveryTrend": trend if contract_available and quality != "INVALID" else [],
        "blockedRobotTicks": blocked_robot_ticks,
        "physicalTravelWaitRobotTicks": physical_wait_ticks,
        "persistentCycleEpisodeCount": _deadlock_value(deadlock_metrics, "PERSISTENT_WAIT_CYCLE_COUNT"),
        "averageTaskLifecycleSeconds": (
            sum(lifecycle_seconds) / len(lifecycle_seconds) if lifecycle_seconds else None
        ),
        "dataQuality": quality,
        "reason": reason,
        "acceptanceEligible": acceptance_eligible,
        "evidenceSummary": {
            "eventType": DELIVERY_EVENT_TYPE,
            "uniqueDeliveryCount": len(unique),
            "duplicateDeliveryCount": duplicate_count,
            "conflictingDeliveryCount": conflict_count,
            "invalidDeliveryCount": invalid_count,
            "windowReached": window_reached,
        },
        "semantics": {
            "deliveredPieces": "SKU pieces physically placed in the contracted outbound drop_zone",
            "pickedPiecesExcluded": True,
            "inventoryReservationsExcluded": True,
            "returnAndRelocationExcluded": True,
            "transferQuantitySeparated": True,
        },
    }


def compare_throughput_metrics(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
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
    if baseline.get("configuredObservationTicks") != candidate.get("configuredObservationTicks"):
        reasons.append("OBSERVATION_WINDOW_MISMATCH")
    if baseline.get("observationSeconds") != candidate.get("observationSeconds"):
        reasons.append("OBSERVATION_DURATION_MISMATCH")
    if not baseline.get("acceptanceEligible") or not candidate.get("acceptanceEligible"):
        reasons.append("DELIVERY_EVIDENCE_INCOMPLETE")
    comparable = not reasons
    baseline_pieces = baseline.get("deliveredPieces")
    candidate_pieces = candidate.get("deliveredPieces")
    baseline_rate = baseline.get("throughputPiecesPerSimulatedHour")
    candidate_rate = candidate.get("throughputPiecesPerSimulatedHour")
    increase = (
        int(candidate_pieces) - int(baseline_pieces)
        if comparable and isinstance(baseline_pieces, int) and isinstance(candidate_pieces, int)
        else None
    )
    improvement = (
        (float(candidate_rate) - float(baseline_rate)) / float(baseline_rate) * 100.0
        if comparable
        and isinstance(baseline_rate, (int, float))
        and isinstance(candidate_rate, (int, float))
        and float(baseline_rate) != 0.0
        else None
    )
    return {
        "contractVersion": "throughput-comparison-v1",
        "comparisonType": "THROUGHPUT_COMPARISON",
        "baseline": baseline,
        "candidate": candidate,
        "comparable": comparable,
        "comparisonReasons": reasons,
        "deliveredPiecesIncrease": increase,
        "throughputImprovementPercent": improvement,
        "throughputImprovementReason": (
            "BASELINE_THROUGHPUT_ZERO"
            if comparable and baseline_rate == 0
            else None
        ),
        "blockedRobotTicksChange": (
            int(candidate["blockedRobotTicks"]) - int(baseline["blockedRobotTicks"])
            if comparable else None
        ),
        "physicalTravelWaitRobotTicksChange": (
            int(candidate["physicalTravelWaitRobotTicks"])
            - int(baseline["physicalTravelWaitRobotTicks"])
            if comparable else None
        ),
        "semantics": {
            "rateFormula": "deliveredPieces / (observationSeconds / 3600)",
            "improvementFormula": "(candidateRate - baselineRate) / baselineRate * 100",
            "completedWorkScopeMayDiffer": True,
            "pathDistanceComparisonRulesUnchanged": True,
        },
    }
