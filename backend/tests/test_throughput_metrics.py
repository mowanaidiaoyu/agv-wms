from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.analytics.algorithm_experiment_metrics import compare_algorithm_experiment_runs
from app.analytics.throughput_metrics import (
    build_throughput_metrics,
    compare_throughput_metrics,
)
from app.schemas import AlgorithmExperimentContractRequest
from app.services.simulation import SimulationService


def _state(
    *,
    tick: int = 300,
    observation_ticks: int = 300,
    signature: str = "same-scenario",
    path: str = "astar",
    assignment: str = "greedy",
    dimension: str = "pathPolicy",
) -> dict:
    return {
        "tick": tick,
        "stopReason": "observation_window_complete",
        "throughputContractVersion": "throughput-metrics-v1",
        "throughputGeneration": 1,
        "motionSpeedContract": {"simTickSeconds": 0.6},
        "algorithmExperimentContract": {
            "scenarioSignature": signature,
            "observationTicks": observation_ticks,
            "pathPolicy": path,
            "assignmentPolicy": assignment,
            "variableDimension": dimension,
            "topologyRevision": "revision",
            "initialInventoryHash": "inventory",
            "orderSnapshotHash": "orders",
            "initialVehicleSnapshotHash": "vehicles",
        },
        "orders": [
            {"id": "order-1", "status": "completed", "completedTick": 200},
            {"id": "order-2", "status": "executing", "completedTick": None},
        ],
        "tasks": [
            {"id": "task-1", "createdTick": 0, "completedTick": 200, "status": "completed"},
            {"id": "task-2", "createdTick": 0, "completedTick": None, "status": "running"},
        ],
        "performanceCounters": {
            "blockedRobotTicks": 7,
            "physicalTravelWaitRobotTicks": 11,
        },
    }


def _event(
    delivery_id: str,
    *,
    pieces: int = 1,
    tick: int = 100,
    sku: str = "SKU-1",
    boxes: int | None = 1,
    flow: str = "outbound_order",
    robot_type: str = "ctu",
    generation: int = 1,
    order_line_id: str = "line-1",
    task_id: str | None = None,
) -> dict:
    return {
        "payload": {
            "contractVersion": "throughput-delivery-event-v1",
            "deliveryId": delivery_id,
            "runId": "run-a",
            "generation": generation,
            "orderId": "order-1",
            "orderLineId": order_line_id,
            "taskId": task_id or f"task-{delivery_id}",
            "skuId": sku,
            "quantityPieces": pieces,
            "quantityBoxes": boxes,
            "tick": tick,
            "flowKind": flow,
            "originRobotType": robot_type,
            "deliveryStage": "drop_zone_received",
            "evidenceSource": "secondary_arm_dropzone_placed",
        }
    }


def _metrics(state: dict | None = None, events: list[dict] | None = None) -> dict:
    return build_throughput_metrics(
        "run-a",
        "stopped",
        state or _state(),
        events or [],
    )


def test_single_piece_delivery_is_counted_at_outbound_endpoint() -> None:
    result = _metrics(events=[_event("delivery-1")])
    assert result["deliveredPieces"] == 1
    assert result["deliveredBoxes"] == 1
    assert result["throughputPiecesPerSimulatedHour"] == pytest.approx(20.0)


def test_multiple_pieces_in_one_box_are_not_counted_as_one_piece() -> None:
    result = _metrics(events=[_event("delivery-1", pieces=20, boxes=1)])
    assert result["deliveredPieces"] == 20
    assert result["deliveredBoxes"] == 1


def test_ctu_multiple_boxes_sum_real_sku_pieces() -> None:
    result = _metrics(events=[
        _event("ctu-box-1", pieces=20),
        _event("ctu-box-2", pieces=20, tick=120),
        _event("ctu-box-3", pieces=7, tick=140),
    ])
    assert result["deliveredPieces"] == 47
    assert result["deliveredBoxes"] == 3


def test_shuttle_whole_box_uses_piece_quantity() -> None:
    result = _metrics(events=[_event("shuttle-box", pieces=24, robot_type="shuttle")])
    assert result["deliveredPieces"] == 24


def test_tail_box_counts_only_picked_and_delivered_pieces() -> None:
    result = _metrics(events=[_event("tail-box", pieces=6, boxes=1)])
    assert result["deliveredPieces"] == 6


def test_return_to_storage_is_not_valid_outbound_delivery() -> None:
    result = _metrics(events=[_event("return", pieces=20, flow="return_to_storage")])
    assert result["deliveredPieces"] is None
    assert result["dataQuality"] == "INVALID"


def test_exact_delivery_event_replay_is_deduplicated() -> None:
    event = _event("duplicate", pieces=8)
    result = _metrics(events=[event, deepcopy(event)])
    assert result["deliveredPieces"] == 8
    assert result["evidenceSummary"]["duplicateDeliveryCount"] == 1


def test_same_task_same_line_same_sku_can_deliver_two_physical_boxes_in_one_tick() -> None:
    result = _metrics(events=[
        _event("cargo-1", pieces=7, tick=120, task_id="shared-task"),
        _event("cargo-2", pieces=9, tick=120, task_id="shared-task"),
    ])
    assert result["deliveredPieces"] == 16
    assert result["deliveredBoxes"] == 2
    assert result["evidenceSummary"]["uniqueDeliveryCount"] == 2


def test_conflicting_payload_for_same_delivery_id_invalidates_evidence() -> None:
    first = _event("conflict", pieces=7)
    conflicting = deepcopy(first)
    conflicting["payload"]["quantityPieces"] = 8
    result = _metrics(events=[first, conflicting])
    assert result["deliveredPieces"] is None
    assert result["dataQuality"] == "INVALID"
    assert result["evidenceSummary"]["conflictingDeliveryCount"] == 1


def test_historical_generation_replay_is_excluded_from_current_generation() -> None:
    state = _state()
    state["throughputGeneration"] = 2
    result = _metrics(state=state, events=[
        _event("old-generation", pieces=99, generation=1),
        _event("current-generation", pieces=6, generation=2),
        deepcopy(_event("current-generation", pieces=6, generation=2)),
    ])
    assert result["generation"] == 2
    assert result["deliveredPieces"] == 6
    assert result["evidenceSummary"]["duplicateDeliveryCount"] == 1


def test_different_skus_are_accumulated_as_pieces() -> None:
    result = _metrics(events=[
        _event("sku-a", pieces=5, sku="SKU-A"),
        _event("sku-b", pieces=9, sku="SKU-B"),
    ])
    assert result["deliveredPieces"] == 14


def test_ctu_task_payload_preserves_order_line_identity() -> None:
    service = SimulationService(_EventRepo())
    task = service._build_task(
        {},
        "task-line-identity",
        "ctu",
        "direct",
        [{
            "nodeId": "storage-1",
            "cellId": "cell-r1-c1",
            "layer": 1,
            "qty": 4,
            "orderLineId": "line-1",
            "skuId": "SKU-1",
        }],
        "cell-r2-c2",
        False,
        2,
        order_id="order-1",
    )
    assert task["lines"][0]["orderLineId"] == "line-1"


def test_ctu_pickup_carries_order_line_identity_with_the_physical_box() -> None:
    service = SimulationService(_EventRepo())
    runtime = SimpleNamespace(
        state={"inventory": [{"nodeId": "storage-1", "layer": 1, "skuId": "SKU-1", "qty": 4}]},
        inventory_delta=[],
    )
    robot = {"carrying": []}
    carried = service._transfer_goods_to_robot(
        runtime,
        robot,
        {"nodeId": "storage-1", "layer": 1, "qty": 4, "orderLineId": "line-1"},
    )
    assert carried is not None
    assert carried["orderLineId"] == "line-1"
    assert robot["carrying"][0]["orderLineId"] == "line-1"


def test_fixed_window_excludes_later_delivery_events() -> None:
    result = _metrics(events=[
        _event("inside", pieces=5, tick=299),
        _event("outside", pieces=99, tick=301),
    ])
    assert result["observationTick"] == 300
    assert result["deliveredPieces"] == 5
    assert result["deliveryTrend"][-1]["tick"] == 300


def test_observation_window_cannot_exceed_safety_limit() -> None:
    with pytest.raises(ValueError, match="observationTicks must not exceed safetyLimitTicks"):
        AlgorithmExperimentContractRequest(
            experimentContractVersion="algorithm-experiment-v1",
            scenarioId="p6-invalid-window",
            scenarioVersion="1",
            seed=42,
            topologyRevision="revision",
            safetyLimitTicks=299,
            observationTicks=300,
            pathPolicy="astar",
            assignmentPolicy="greedy",
            variableDimension="pathPolicy",
            algorithmVersion="p6-v1",
        )


def test_window_not_reached_is_partial_and_not_acceptance_eligible() -> None:
    result = _metrics(state=_state(tick=200), events=[])
    assert result["dataQuality"] == "PARTIAL"
    assert result["reason"] == "OBSERVATION_WINDOW_INCOMPLETE"
    assert result["acceptanceEligible"] is False


def test_window_must_be_closed_by_the_authoritative_observation_stop() -> None:
    state = _state(tick=301)
    result = build_throughput_metrics("run-a", "running", state, [])
    assert result["observationTick"] == 300
    assert result["acceptanceEligible"] is False


def test_same_scenario_can_compare_different_delivered_piece_counts() -> None:
    baseline = _metrics(events=[_event("base", pieces=10)])
    candidate = _metrics(events=[_event("candidate", pieces=15)])
    candidate["runId"] = "run-b"
    candidate["experimentContract"] = {**candidate["experimentContract"], "pathPolicy": "dijkstra"}
    result = compare_throughput_metrics(baseline, candidate)
    assert result["comparable"] is True
    assert result["deliveredPiecesIncrease"] == 5
    assert result["throughputImprovementPercent"] == pytest.approx(50.0)


def test_different_observation_windows_are_not_comparable() -> None:
    baseline = _metrics(events=[_event("base", pieces=10)])
    candidate = _metrics(state=_state(tick=400, observation_ticks=400, path="dijkstra"), events=[_event("candidate", pieces=15)])
    result = compare_throughput_metrics(baseline, candidate)
    assert result["comparable"] is False
    assert "OBSERVATION_WINDOW_MISMATCH" in result["comparisonReasons"]


def test_scenario_signature_mismatch_rejects_comparison() -> None:
    baseline = _metrics(events=[_event("base", pieces=10)])
    candidate = _metrics(state=_state(signature="other", path="dijkstra"), events=[_event("candidate", pieces=15)])
    result = compare_throughput_metrics(baseline, candidate)
    assert result["comparable"] is False
    assert "SCENARIO_SIGNATURE_MISMATCH" in result["comparisonReasons"]


def test_zero_baseline_rate_has_no_percentage_division() -> None:
    baseline = _metrics(events=[])
    candidate = _metrics(events=[_event("candidate", pieces=3)])
    candidate["experimentContract"] = {**candidate["experimentContract"], "pathPolicy": "dijkstra"}
    result = compare_throughput_metrics(baseline, candidate)
    assert result["comparable"] is True
    assert result["deliveredPiecesIncrease"] == 3
    assert result["throughputImprovementPercent"] is None
    assert result["throughputImprovementReason"] == "BASELINE_THROUGHPUT_ZERO"


def test_legal_zero_percent_improvement_is_preserved() -> None:
    baseline = _metrics(events=[_event("base", pieces=10)])
    candidate = _metrics(events=[_event("candidate", pieces=10)])
    candidate["experimentContract"] = {**candidate["experimentContract"], "pathPolicy": "dijkstra"}
    result = compare_throughput_metrics(baseline, candidate)
    assert result["comparable"] is True
    assert result["throughputImprovementPercent"] == 0.0


def test_candidate_throughput_decline_is_reported_without_clamping() -> None:
    baseline = _metrics(events=[_event("base", pieces=10)])
    candidate = _metrics(events=[_event("candidate", pieces=8)])
    candidate["experimentContract"] = {**candidate["experimentContract"], "pathPolicy": "dijkstra"}
    result = compare_throughput_metrics(baseline, candidate)
    assert result["deliveredPiecesIncrease"] == -2
    assert result["throughputImprovementPercent"] == pytest.approx(-20.0)


class _EventRepo:
    def __init__(self) -> None:
        self.events: list[tuple[str, int, str, dict]] = []

    def add_event(self, run_id: str, tick: int, event_type: str, payload: dict) -> None:
        self.events.append((run_id, tick, event_type, payload))


def test_ctu_tail_delivery_emits_one_idempotent_authoritative_event() -> None:
    repo = _EventRepo()
    service = SimulationService(repo)
    root = {"id": "ctu-root", "robotType": "ctu", "orderId": "order-1"}
    parent = {"id": "pick-box", "parentId": "ctu-root", "taskKind": "picking_box"}
    delivery = {
        "id": "pick-box-placed-fwd",
        "parentId": "pick-box",
        "taskKind": "placed_goods_delivery",
        "orderId": "order-1",
        "orderLineId": "line-1",
        "transitGoods": [{"skuId": "SKU-1", "qty": 6}],
    }
    runtime = SimpleNamespace(
        run_id="run-runtime",
        state={
            "throughputGeneration": 1,
            "throughputDeliveryIds": [],
            "tasks": [root, parent, delivery],
            "eventCount": 0,
        },
    )
    service._record_outbound_delivery(runtime, delivery, 42, "cell-r110-c62")
    service._record_outbound_delivery(runtime, delivery, 42, "cell-r110-c62")
    assert len(repo.events) == 1
    payload = repo.events[0][3]
    assert payload["quantityPieces"] == 6
    assert payload["quantityBoxes"] == 1
    assert payload["originRobotType"] == "ctu"


def test_runtime_same_task_delivers_distinct_physical_boxes_without_tick_identity() -> None:
    repo = _EventRepo()
    service = SimulationService(repo)
    delivery = {
        "id": "shared-task",
        "parentId": "pick-box",
        "taskKind": "placed_goods_delivery",
        "orderId": "order-1",
        "orderLineId": "line-1",
        "transitGoods": [{"skuId": "SKU-1", "qty": 7, "orderLineId": "line-1"}],
    }
    runtime = SimpleNamespace(
        run_id="run-runtime",
        state={
            "throughputGeneration": 1,
            "throughputDeliveryIds": [],
            "throughputCargoSequence": 0,
            "tasks": [
                {"id": "ctu-root", "robotType": "ctu", "orderId": "order-1"},
                {"id": "pick-box", "parentId": "ctu-root", "taskKind": "picking_box"},
                delivery,
            ],
            "eventCount": 0,
        },
    )
    first = service._outbound_cargo_snapshot(
        runtime, delivery, {"skuId": "SKU-1", "qty": 7, "orderLineId": "line-1"}
    )
    second = service._outbound_cargo_snapshot(
        runtime, delivery, {"skuId": "SKU-1", "qty": 9, "orderLineId": "line-1"}
    )
    service._record_outbound_delivery(runtime, delivery, 42, "cell-r110-c67", cargo=first)
    service._record_outbound_delivery(runtime, delivery, 42, "cell-r110-c67", cargo=second)
    service._record_outbound_delivery(runtime, delivery, 42, "cell-r110-c67", cargo=first)

    assert len(repo.events) == 2
    assert {event[3]["quantityPieces"] for event in repo.events} == {7, 9}
    assert len({event[3]["physicalCargoId"] for event in repo.events}) == 2
    assert {event[3]["tick"] for event in repo.events} == {42}


def test_runtime_persists_conflicting_replay_for_invalid_quality_audit() -> None:
    repo = _EventRepo()
    service = SimulationService(repo)
    delivery = {
        "id": "shared-task",
        "parentId": "pick-box",
        "taskKind": "placed_goods_delivery",
        "orderId": "order-1",
        "orderLineId": "line-1",
        "transitGoods": [{"skuId": "SKU-1", "qty": 7, "orderLineId": "line-1"}],
    }
    runtime = SimpleNamespace(
        run_id="run-runtime",
        state={
            "throughputGeneration": 1,
            "throughputDeliveryIds": [],
            "throughputDeliverySignatures": {},
            "throughputCargoSequence": 0,
            "tasks": [
                {"id": "ctu-root", "robotType": "ctu", "orderId": "order-1"},
                {"id": "pick-box", "parentId": "ctu-root", "taskKind": "picking_box"},
                delivery,
            ],
            "eventCount": 0,
        },
    )
    cargo = service._outbound_cargo_snapshot(
        runtime, delivery, {"skuId": "SKU-1", "qty": 7, "orderLineId": "line-1"}
    )
    service._record_outbound_delivery(runtime, delivery, 42, "cell-r110-c67", cargo=cargo)
    service._record_outbound_delivery(runtime, delivery, 42, "cell-r110-c67", cargo=dict(cargo))
    service._record_outbound_delivery(
        runtime,
        delivery,
        42,
        "cell-r110-c67",
        cargo={**cargo, "qty": 8},
    )

    assert len(repo.events) == 2
    result = build_throughput_metrics(
        "run-runtime",
        "stopped",
        {
            **_state(),
            "throughputDeliveryIds": runtime.state["throughputDeliveryIds"],
            "throughputDeliverySignatures": runtime.state["throughputDeliverySignatures"],
        },
        [{"payload": event[3]} for event in repo.events],
    )
    assert result["dataQuality"] == "INVALID"
    assert result["deliveredPieces"] is None
    assert result["evidenceSummary"]["conflictingDeliveryCount"] == 1


def test_shuttle_delivery_uses_demand_pieces_not_default_box_count() -> None:
    repo = _EventRepo()
    service = SimulationService(repo)
    delivery = {
        "id": "shuttle-order",
        "taskKind": "shuttle_order_pick",
        "robotType": "shuttle",
        "orderId": "order-1",
        "orderLineId": "line-1",
        "demandPieces": 24,
        "transitGoods": [{"skuId": "SKU-1", "qty": 1}],
    }
    runtime = SimpleNamespace(
        run_id="run-runtime",
        state={"throughputGeneration": 1, "throughputDeliveryIds": [], "tasks": [delivery], "eventCount": 0},
    )
    service._record_outbound_delivery(runtime, delivery, 42, "cell-r110-c62")
    assert repo.events[0][3]["quantityPieces"] == 24


def test_authoritative_event_is_emitted_when_secondary_arm_places_in_drop_zone() -> None:
    repo = _EventRepo()
    service = SimulationService(repo)
    delivery = {
        "id": "pick-box-placed-fwd",
        "parentId": "pick-box",
        "taskKind": "placed_goods_delivery",
        "orderId": "order-1",
        "orderLineId": "line-1",
        "transitGoods": [{"skuId": "SKU-1", "qty": 6}],
    }
    runtime = SimpleNamespace(
        run_id="run-runtime",
        secondary_arms=[{
            "armId": "arm-01",
            "dropCol": 67,
            "ports": [{"cellId": "cell-r110-c62", "row": 110}],
        }],
        state={
            "tick": 50,
            "throughputGeneration": 1,
            "throughputDeliveryIds": [],
            "tasks": [
                {"id": "ctu-root", "robotType": "ctu", "orderId": "order-1"},
                {"id": "pick-box", "parentId": "ctu-root", "taskKind": "picking_box"},
                delivery,
            ],
            "pickingArms": [{
                "armId": "arm-01",
                "status": "busy",
                "stage": "place",
                "remainingTicks": 1,
                "taskId": delivery["id"],
                "dropCellId": "cell-r110-c67",
                "dropRow": 110,
                "dropCol": 67,
                "cargo": {"taskId": delivery["id"], "skuId": "SKU-1", "qty": 6},
            }],
            "armBeltBoxes": [],
            "armDropBoxes": [],
            "eventCount": 0,
        },
    )

    service._advance_picking_arms(runtime)

    delivery_events = [event for event in repo.events if event[2] == "outbound_delivery_v1"]
    assert len(delivery_events) == 1
    payload = delivery_events[0][3]
    assert payload["deliveryStage"] == "drop_zone_received"
    assert payload["endpointCellId"] == "cell-r110-c67"
    assert payload["quantityPieces"] == 6
    assert runtime.state["armDropBoxes"][0]["cellId"] == "cell-r110-c67"


class _DistanceRepo:
    def __init__(self, completed: int):
        self.completed = completed

    def get_run_read_only(self, run_id: str) -> dict:
        tasks = [
            {"id": f"{run_id}-{index}", "status": "completed", "createdTick": 0, "completedTick": 1}
            for index in range(self.completed)
        ]
        return {
            "id": run_id,
            "status": "completed",
            "payload": {
                "tasks": tasks,
                "stopReason": None,
                "simulatedSeconds": 1.0,
                "motionSpeedContract": {"simTickSeconds": 1.0},
                "algorithmExperimentContract": {
                    "scenarioSignature": "same",
                    "variableDimension": "pathPolicy",
                    "pathPolicy": "astar" if run_id == "left" else "dijkstra",
                },
            },
        }

    def get_motion_distance_metrics(self, run_id: str) -> dict:
        return {"dataQuality": "COMPLETE", "totalMetrics": {"totalHorizontalMeters": {"value": 1.0}}}

    def get_deadlock_lifecycle_metrics(self, run_id: str) -> dict:
        return {"dataQuality": "GOOD", "metrics": []}


def test_original_distance_comparison_still_rejects_completed_work_scope_mismatch() -> None:
    result = compare_algorithm_experiment_runs(_DistanceRepo(completed=1), "left", "right")
    assert result is not None
    # Swap one side's work scope without weakening the original P5 rule.
    repo = _DistanceRepo(completed=1)
    original = repo.get_run_read_only
    repo.get_run_read_only = lambda run_id: (
        _DistanceRepo(completed=2).get_run_read_only(run_id)
        if run_id == "right" else original(run_id)
    )
    result = compare_algorithm_experiment_runs(repo, "left", "right")
    assert result is not None
    assert result["comparable"] is False
    assert "COMPLETED_WORK_SCOPE_MISMATCH" in result["comparisonReasons"]
