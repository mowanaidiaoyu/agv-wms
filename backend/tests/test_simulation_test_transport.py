from __future__ import annotations

import asyncio
import atexit
from copy import deepcopy
import gc
import json
from pathlib import Path
import sqlite3
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes
from app.core import db
from app.core.repository import Repository
from app.schemas import TopologyPayload
from app.services.pathfinding import build_occupied_lookup
from app.services.fulfillment_data import (
    validate_general_simulation_orders,
    validate_simulation_test_scenario,
)
from app.services.goods import load_catalog
from app.services.right_handoff import (
    SIMULATION_TEST_RUN_MODE,
    RightHandoffValidationError,
    validate_shuttle_route_frames,
)
from app.services.simulation import RunControlConflict, SimulationService


TOPOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "topology_666.json"
SCENARIO_ID = "right-left-110-case-test"
EXPECTED_REVISION = "2.0.0:01845f1f27d1f457"
REQUIRED_EVENT_FIELDS = {
    "runId", "tick", "eventId", "orderId", "taskId", "cargoId",
    "fromNodeId", "toNodeId", "deviceId", "quantityBase", "dataSource",
}


def _general_order(quantity: int) -> dict:
    return {
        "id": f"ORD-GENERAL-{quantity}",
        "priority": "normal",
        "dispatchMode": "balanced",
        "maxParallelCtu": 7,
        "targetStation": "simulation-merge",
        "createdAt": "2026-10-31T00:00:00+08:00",
        "lines": [{
            "id": f"line-{quantity}",
            "skuId": "13448419-4.0-4.0",
            "skuName": "simulation SKU",
            "quantityBase": quantity,
            "unitsPerCase": 20,
        }],
    }


def _general_order_for_sku(
    order_id: str,
    sku_id: str,
    quantity: int,
    units_per_case: int,
) -> dict:
    order = _general_order(quantity)
    order["id"] = order_id
    order["lines"][0].update({
        "id": f"line-{order_id}",
        "skuId": sku_id,
        "skuName": f"simulation SKU {sku_id}",
        "quantityBase": quantity,
        "unitsPerCase": units_per_case,
    })
    return order


@pytest.fixture(scope="module")
def audited_scenario() -> tuple[dict, dict]:
    topology = TopologyPayload.model_validate(
        json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    ).model_dump(by_alias=True)
    audit = validate_simulation_test_scenario(topology, load_catalog(), SCENARIO_ID)
    return topology, audit


@pytest.fixture
def isolated_repo(monkeypatch: pytest.MonkeyPatch):
    # Use a unique file directly under the writable test tree. Some managed
    # Windows runners deny traversal of pytest's auto-created tmp_path folder.
    database = Path(__file__).resolve().parent / f".f3b-{uuid.uuid4().hex}.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    db.init_db()
    yield Repository()
    gc.collect()
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{database}{suffix}")
        for _ in range(10):
            if not path.exists():
                break
            try:
                path.unlink()
                break
            except PermissionError:
                gc.collect()
                time.sleep(0.01)
        if path.exists():
            atexit.register(path.unlink, missing_ok=True)


def build_persisted_runtime(
    repo: Repository,
    audited_scenario: tuple[dict, dict],
    *,
    run_id: str = "run-test-f3b",
    instance_id: str = "f3b-test-owner",
) -> tuple[SimulationService, object]:
    topology, audit = audited_scenario
    repo.upsert_topology(topology["id"], topology["name"], topology)
    service = SimulationService(repo, instance_id=instance_id, control_lease_seconds=30)
    runtime = service._build_simulation_test_runtime(run_id, topology, audit)
    service.runtimes[run_id] = runtime
    repo.create_run(run_id, "running", topology["id"], runtime.state, runtime.metrics)
    assert service._claim_run_control(runtime) is True
    service._persist(runtime)
    return service, runtime


def assert_single_ownership(sim: dict) -> None:
    owners: dict[str, list[str]] = {item["cargoId"]: [] for item in sim["cargo"]}
    for cargo_id in sim["shuttle"]["occupiedCargoIds"]:
        owners[cargo_id].append(sim["shuttle"]["deviceId"])
    for cargo_id in sim["lift"]["occupiedCargoIds"]:
        owners[cargo_id].append(sim["lift"]["deviceId"])
    for facility in sim["facilities"]:
        assert len(facility["occupiedCargoIds"]) <= facility["capacity"] == 1
        for cargo_id in facility["occupiedCargoIds"]:
            owners[cargo_id].append(facility["deviceId"])
    for block, cargo_id in sim["conveyor"]["occupiedByBlock"].items():
        if cargo_id:
            owners[cargo_id].append(block)
    for cargo_id in sim["armInfeed"]["occupiedCargoIds"]:
        owners[cargo_id].append(sim["armInfeed"]["nodeId"])
    if sim["arm"]["activeCargoId"] and sim["arm"]["cargoPlacement"] == "gripper":
        owners[sim["arm"]["activeCargoId"]].append(sim["arm"]["armId"])
    for node_id, cargo_id in sim["mergeConveyor"]["occupiedByNode"].items():
        if cargo_id:
            owners[cargo_id].append(node_id)
    assert all(len(locations) <= 1 for locations in owners.values()), owners


def test_general_40_unit_right_only_run_uses_dynamic_counts_and_same_tick(
    isolated_repo: Repository,
) -> None:
    topology = TopologyPayload.model_validate(
        json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    ).model_dump(by_alias=True)
    audit = validate_general_simulation_orders(
        topology,
        load_catalog(),
        "general-order-simulation",
        [_general_order(40)],
        require_ready=True,
    )
    isolated_repo.upsert_topology(topology["id"], topology["name"], topology)
    service = SimulationService(isolated_repo, instance_id="general-run-test")
    runtime = service._build_simulation_test_runtime("run-general-40", topology, audit)
    service.runtimes[runtime.run_id] = runtime
    isolated_repo.create_run(runtime.run_id, "running", topology["id"], runtime.state, runtime.metrics)
    assert service._claim_run_control(runtime)
    for _ in range(500):
        service._step_runtime(runtime, persist=True)
        if runtime.stopped:
            break
    sim = runtime.state["simulationTest"]
    assert runtime.state["runMode"] == "simulation"
    assert len(sim["cargo"]) == 2
    assert sim["leftFlows"] == []
    assert sim["mergePoint"]["acceptedCount"] == 2
    assert sim["order"]["quantityAccounted"] == 40
    assert runtime.state["orders"][0]["formalOrderCompleted"] is False
    assert runtime.state["orders"][0]["physicalDeliveryConfirmed"] is False
    assert runtime.stopped is True


def test_general_multi_order_two_pack_sizes_share_one_tick_and_keep_ledgers_isolated(
    isolated_repo: Repository,
) -> None:
    topology = TopologyPayload.model_validate(
        json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    ).model_dump(by_alias=True)
    orders = [
        _general_order_for_sku("ORD-35-U20", "13448419-4.0-4.0", 35, 20),
        _general_order_for_sku("ORD-25-U12", "13495233-15.0-15.0", 25, 12),
    ]
    audit = validate_general_simulation_orders(
        topology,
        load_catalog(),
        "general-order-simulation",
        orders,
        require_ready=True,
    )
    isolated_repo.upsert_topology(topology["id"], topology["name"], topology)
    service = SimulationService(isolated_repo, instance_id="general-multi-run-test")
    runtime = service._build_simulation_test_runtime("run-general-multi", topology, audit)
    service.runtimes[runtime.run_id] = runtime
    isolated_repo.create_run(runtime.run_id, "running", topology["id"], runtime.state, runtime.metrics)
    assert service._claim_run_control(runtime)

    sim = runtime.state["simulationTest"]
    assert len(sim["cargo"]) == 3
    assert len(sim["leftFlows"]) == 2
    assert sum(item["quantityBase"] for item in sim["cargo"]) == 44
    assert sum(item["pickQuantityBase"] for item in sim["leftFlows"]) == 16
    assert sum(item["returnQuantityBase"] for item in sim["leftFlows"]) == 16
    assert len({item["cargoId"] for item in sim["cargo"]}) == 3
    assert len({item["cargo"]["cargoId"] for item in sim["leftFlows"]}) == 2

    for _ in range(700):
        service._step_runtime(runtime, persist=True)
        if runtime.stopped:
            break

    sim = runtime.state["simulationTest"]
    assert runtime.stopped is True
    assert sim["mergePoint"]["acceptedCount"] == 3
    assert [item["status"] for item in sim["leftFlows"]] == ["returned", "returned"]
    assert sim["order"]["rightMergeAcceptedQuantityBase"] == 44
    assert sim["order"]["leftPickedQuantityBase"] == 16
    assert sim["order"]["leftReturnedQuantityBase"] == 16
    assert sim["order"]["quantityAccounted"] == 60
    assert all(item["formalOrderCompleted"] is False for item in runtime.state["orders"])
    assert all(item["physicalDeliveryConfirmed"] is False for item in runtime.state["orders"])
    assert {item["id"]: item["quantityAccounted"] for item in runtime.state["orders"]} == {
        "ORD-35-U20": 35,
        "ORD-25-U12": 25,
    }


def test_general_order_can_cancel_before_pick_without_touching_other_order_reservations(
    isolated_repo: Repository,
) -> None:
    topology = TopologyPayload.model_validate(
        json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    ).model_dump(by_alias=True)
    orders = [
        _general_order_for_sku("ORD-CANCEL", "13448419-4.0-4.0", 35, 20),
        _general_order_for_sku("ORD-KEEP", "13495233-15.0-15.0", 24, 12),
    ]
    audit = validate_general_simulation_orders(
        topology,
        load_catalog(),
        "general-order-simulation",
        orders,
        require_ready=True,
    )
    isolated_repo.upsert_topology(topology["id"], topology["name"], topology)
    service = SimulationService(isolated_repo, instance_id="general-cancel-test")
    runtime = service._build_simulation_test_runtime("run-general-cancel", topology, audit)
    service.runtimes[runtime.run_id] = runtime
    isolated_repo.create_run(runtime.run_id, "running", topology["id"], runtime.state, runtime.metrics)
    assert service._claim_run_control(runtime)

    state = asyncio.run(service.cancel_order(runtime.run_id, "ORD-CANCEL", "test cancellation"))
    sim = state["simulationTest"]
    assert next(item for item in state["orders"] if item["id"] == "ORD-CANCEL")["status"] == "cancelled"
    assert {item["orderId"] for item in sim["cargo"]} == {"ORD-KEEP"}
    assert sim["leftFlows"] == []
    assert sim["order"]["quantityBase"] == 24
    keep_record = next(
        item for item in sim["inventory"]["records"]
        if item["skuId"] == "13495233-15.0-15.0" and item["sourceWarehouse"] == "right"
    )
    cancelled_right = next(
        item for item in sim["inventory"]["records"]
        if item["skuId"] == "13448419-4.0-4.0" and item["sourceWarehouse"] == "right"
    )
    assert keep_record["reservedCases"] == 2
    assert cancelled_right["reservedCases"] == 0
    assert state["inventory"] == []


def test_initial_snapshot_has_five_stable_cases_and_run_local_inventory(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
) -> None:
    _, runtime = build_persisted_runtime(isolated_repo, audited_scenario)
    state = runtime.state
    sim = state["simulationTest"]
    cargo_ids = [item["cargoId"] for item in sim["cargo"]]
    assert state["runMode"] == SIMULATION_TEST_RUN_MODE
    assert state["topologyRevision"] == EXPECTED_REVISION
    assert len(cargo_ids) == len(set(cargo_ids)) == 5
    assert sum(item["quantityBase"] for item in sim["cargo"]) == 100
    assert sim["inventory"]["isolationMode"] == "run_local_snapshot"
    assert sim["inventory"]["right"] == {
        "initialCases": 5, "availableCases": 5, "inTransitCases": 0,
        "waitingArmCases": 0, "mergeArrivedCases": 0,
    }
    assert sim["armInfeed"] == {
        "nodeId": "cell-r92-c48", "capacity": 1,
        "occupiedCargoIds": [], "status": "simulation_only_waiting_buffer",
    }
    assert sim["arm"]["phase"] == "idle"
    assert sim["arm"]["activeCargoId"] is None
    assert sim["inventory"]["left"] == {
        "sourceLocationId": "storage-01:L1",
        "sourceQuantityBase": 20,
        "pickQuantityBase": 10,
        "returnQuantityBase": 10,
        "status": "waiting_split",
        "dataSource": "simulation_test",
    }
    left = sim["leftFlow"]
    assert left["sourceCellId"] == "cell-r0-c6"
    assert left["sourceStopCellId"] == "cell-r0-c7"
    assert left["station"]["positionCellId"] == "cell-r72-c66"
    assert left["station"]["inputNodeId"] == "cell-r72-c65"
    assert left["station"]["validationStatus"] == "simulation_only"
    assert left["ledger"] == {
        "initialSourceQuantityBase": 20, "sourceQuantityBase": 20,
        "ctuQuantityBase": 0, "stationQuantityBase": 0,
        "pickedQuantityBase": 0, "returnedQuantityBase": 0,
        "returnApplied": False,
    }
    assert state["inventory"] == []
    assert sim["formalRouteStatus"] == "BLOCKED"
    assert sim["simulationRouteStatus"] == "PASS_SIMULATION_ONLY"
    assert sim["conveyor"]["beltHeight"] == pytest.approx(0.6)
    assert all(item["movementState"] == {
        "fromNodeId": item["currentPosition"]["nodeId"],
        "toNodeId": item["currentPosition"]["nodeId"],
        "segmentProgress": 1.0,
        "movementDurationSeconds": 0.6,
        "transportState": "waiting_pick",
        "tick": 0,
    } for item in sim["cargo"])


def test_single_tick_pipeline_moves_all_cases_without_loss_duplication_or_early_completion(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
) -> None:
    service, runtime = build_persisted_runtime(isolated_repo, audited_scenario)
    expected_cargo_ids = [item["cargoId"] for item in runtime.state["simulationTest"]["cargo"]]
    observed_statuses: set[str] = set()
    observed_shuttle_bound_statuses: set[str] = set()
    observed_arm_phases: set[str] = set()
    observed_arm_phase_endpoints: set[str] = set()
    maximum_infeed_occupancy = 0
    right_completed_tick: int | None = None
    left_completed_tick: int | None = None
    for _ in range(450):
        service._step_runtime(runtime, persist=True)
        sim = runtime.state["simulationTest"]
        observed_statuses.update(item["status"] for item in sim["cargo"])
        maximum_infeed_occupancy = max(
            maximum_infeed_occupancy, len(sim["armInfeed"]["occupiedCargoIds"]),
        )
        assert_single_ownership(sim)
        assert len(sim["armInfeed"]["occupiedCargoIds"]) <= sim["armInfeed"]["capacity"] == 1
        assert sum(item["status"] == "waiting_arm" for item in sim["cargo"]) <= 1
        assert len(sim["arm"]["completedCargoIds"]) == len(set(sim["arm"]["completedCargoIds"]))
        arm = sim["arm"]
        assert 0 <= arm["phaseProgress"] <= 1
        observed_arm_phases.add(arm["phase"])
        if arm["phase"] != "idle" and arm["phaseProgress"] == 1:
            observed_arm_phase_endpoints.add(arm["phase"])
        assert len({item["cargoId"] for item in sim["cargo"]}) == 5
        assert sum(item["quantityBase"] for item in sim["cargo"]) == 100
        assert sim["inventory"]["left"]["sourceQuantityBase"] == 20
        left = sim["leftFlow"]
        ledger = left["ledger"]
        assert (
            ledger["sourceQuantityBase"]
            + ledger["ctuQuantityBase"]
            + ledger["stationQuantityBase"]
            + ledger["pickedQuantityBase"]
        ) == 20
        if sim["mergePoint"]["acceptedCount"] == 5 and sim["arm"]["phase"] == "idle":
            right_completed_tick = right_completed_tick or runtime.state["tick"]
            if left["status"] != "returned":
                assert runtime.stopped is False
        if left["status"] == "returned":
            left_completed_tick = left_completed_tick or runtime.state["tick"]
        for item in sim["cargo"]:
            movement = item["movementState"]
            assert movement["segmentProgress"] == 1.0
            assert movement["movementDurationSeconds"] == pytest.approx(0.6)
            assert movement["tick"] <= runtime.state["tick"]
            if item["status"] == "conveyor_transporting":
                assert item["currentPosition"]["worldY"] == pytest.approx(0.6)
            if item["status"] == "merge_conveyor_transporting":
                assert item["currentPosition"]["worldY"] == pytest.approx(3.0)
        assert all(not item["currentNodeId"].startswith("cell-") for item in sim["cargo"] if item["currentDeviceId"] == sim["shuttle"]["deviceId"])
        shuttle_bound = next((
            item for item in sim["cargo"]
            if item["status"] in {
                "shuttle_picking", "shuttle_transporting", "lift_waiting",
                "lift_moving", "shuttle_to_handoff",
            }
        ), None)
        if shuttle_bound is not None:
            observed_shuttle_bound_statuses.add(shuttle_bound["status"])
            robot = next(item for item in runtime.state["robots"] if item["id"] == sim["shuttle"]["deviceId"])
            assert [item["cargoId"] for item in robot["carrying"]] == [shuttle_bound["cargoId"]]
            assert robot["currentNode"] == sim["shuttle"]["currentPosition"]["nodeId"]
            assert robot["currentLayer"] == sim["shuttle"]["currentPosition"]["level"]
        assert runtime.state["orders"][0]["status"] != "completed"
        if runtime.stopped:
            break

    sim = runtime.state["simulationTest"]
    assert runtime.stopped is True
    assert runtime.state["stopReason"] == "simulation_test_quantity_accounted"
    assert right_completed_tick is not None and left_completed_tick is not None
    assert right_completed_tick < left_completed_tick
    assert {item["status"] for item in sim["cargo"]} == {"merge_arrived"}
    assert {item["currentNodeId"] for item in sim["cargo"]} == {"cell-r108-c68"}
    assert sim["armInfeed"]["occupiedCargoIds"] == []
    assert maximum_infeed_occupancy == 1
    assert sim["arm"]["completedCargoIds"] == expected_cargo_ids
    assert sim["mergePoint"]["topologyNodeId"] == "lift_transfer-09"
    assert sim["mergePoint"]["acceptedCargoIds"] == expected_cargo_ids
    assert sim["mergePoint"]["acceptedCount"] == 5
    assert sim["inventory"]["right"] == {
        "initialCases": 5, "availableCases": 0, "inTransitCases": 0,
        "waitingArmCases": 0, "mergeArrivedCases": 5,
    }
    assert sim["leftFlow"]["status"] == "returned"
    assert sim["leftFlow"]["partialCase"]["caseType"] == "partial_case"
    assert sim["leftFlow"]["partialCase"]["quantityBase"] == 10
    assert sim["leftFlow"]["ledger"] == {
        "initialSourceQuantityBase": 20, "sourceQuantityBase": 10,
        "ctuQuantityBase": 0, "stationQuantityBase": 0,
        "pickedQuantityBase": 10, "returnedQuantityBase": 10,
        "returnApplied": True,
    }
    assert sim["order"]["rightMergeAcceptedQuantityBase"] == 100
    assert sim["order"]["leftPickedQuantityBase"] == 10
    assert sim["order"]["leftReturnedQuantityBase"] == 10
    assert sim["order"]["quantityAccounted"] == 110
    assert sim["order"]["formalOrderCompleted"] is False
    assert sim["order"]["physicalDeliveryConfirmed"] is False
    assert {
        "waiting_pick", "shuttle_picking", "shuttle_transporting", "lift_waiting",
        "lift_moving", "shuttle_to_handoff", "waiting_dock", "dock_transferring",
        "conveyor_transporting", "waiting_arm", "arm_processing",
        "merge_conveyor_transporting", "merge_arrived",
    }.issubset(observed_statuses)
    assert observed_shuttle_bound_statuses == {
        "shuttle_picking", "shuttle_transporting", "lift_waiting",
        "lift_moving", "shuttle_to_handoff",
    }
    expected_arm_phases = {
        "approach", "grip", "lift", "rotate", "place", "release", "return_home",
    }
    assert expected_arm_phases <= observed_arm_phases
    assert observed_arm_phase_endpoints == expected_arm_phases
    assert runtime.state["orders"][0]["status"] == "executing"
    assert runtime.state["orders"][0]["completedTick"] is None
    assert runtime.state["orders"][0]["fulfillmentPlan"]["canDeclareCompleted"] is False
    nested_tasks = runtime.state["orders"][0]["fulfillmentPlan"]["tasks"]
    assert {task["status"] for task in nested_tasks if task.get("cargoId")} == {"merge_arrived"}
    assert {task["status"] for task in nested_tasks if not task.get("cargoId")} == {"returned"}

    merge_path = sim["mergeConveyor"]["path"]
    coordinates = [(item["row"], item["col"]) for item in merge_path]
    for waypoint in ((86, 50), (86, 60), (108, 60), (108, 66), (108, 67), (108, 68)):
        assert waypoint in coordinates
    assert coordinates.index((86, 50)) < coordinates.index((86, 60))
    assert coordinates.index((86, 60)) < coordinates.index((108, 60))
    assert coordinates.index((108, 60)) < coordinates.index((108, 66))
    assert coordinates.index((108, 66)) < coordinates.index((108, 67)) < coordinates.index((108, 68))
    assert next(item for item in merge_path if (item["row"], item["col"]) == (86, 60))["transferDeviceId"] == "turn_transfer-15"
    assert next(item for item in merge_path if (item["row"], item["col"]) == (108, 60))["transferDeviceId"] == "turn_transfer-13"


def test_shuttle_playback_frames_follow_cardinal_graph_edges_and_lift_has_no_horizontal_drift(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
) -> None:
    _, runtime = build_persisted_runtime(isolated_repo, audited_scenario)
    shuttle = runtime.state["simulationTest"]["shuttle"]
    for route_name in ("outboundFrames", "returnFrames"):
        frames = shuttle[route_name]
        validate_shuttle_route_frames(frames)
        assert frames
        for previous, current in zip(frames, frames[1:]):
            left = previous["position"]
            right = current["position"]
            row_changed = left["row"] != right["row"]
            col_changed = left["col"] != right["col"]
            level_changed = left["level"] != right["level"]
            assert not (row_changed and col_changed)
            if level_changed:
                assert row_changed is False
                assert col_changed is False
                assert left["worldX"] == right["worldX"]
                assert left["worldZ"] == right["worldZ"]
            else:
                assert abs(int(right["row"]) - int(left["row"])) in {0, 2}
                assert abs(int(right["col"]) - int(left["col"])) in {0, 2}


def test_f5_ctu_routes_are_real_cardinal_roads_and_cargo_has_single_owner(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
) -> None:
    topology, _ = audited_scenario
    service, runtime = build_persisted_runtime(isolated_repo, audited_scenario)
    left = runtime.state["simulationTest"]["leftFlow"]
    assert left["ctu"]["deviceId"] == "ctu-07"
    assert left["ctu"]["toSourceFrames"][-1]["nodeId"] == "cell-r0-c7"
    assert left["ctu"]["toStationFrames"][-1]["nodeId"] == "cell-r72-c65"
    assert left["ctu"]["toStorageFrames"][-1]["nodeId"] == "cell-r0-c7"
    occupied = build_occupied_lookup(topology)
    for route_name in ("toSourceFrames", "toStationFrames", "toStorageFrames"):
        frames = left["ctu"][route_name]
        assert frames
        for frame in frames:
            point = frame["position"]
            assert (point["floor"], point["row"], point["col"]) not in occupied
            assert point["worldY"] == 0
        for previous, current in zip(frames, frames[1:]):
            assert (
                abs(current["position"]["row"] - previous["position"]["row"])
                + abs(current["position"]["col"] - previous["position"]["col"])
            ) == 1

    observed_station_owner = False
    observed_full_case_ctu_owner = False
    observed_partial_case_ctu_owner = False
    while not runtime.stopped:
        service._step_runtime(runtime, persist=True)
        left = runtime.state["simulationTest"]["leftFlow"]
        robot = next(
            item for item in runtime.state["robots"]
            if item["id"] == left["ctu"]["deviceId"]
        )
        active = left["ctu"]["activeCargoId"]
        carried_ids = [item["cargoId"] for item in robot["carrying"]]
        assert carried_ids == ([active] if active else [])
        if active == left["cargo"]["cargoId"]:
            observed_full_case_ctu_owner = True
        if left.get("partialCase") and active == left["partialCase"]["cargoId"]:
            observed_partial_case_ctu_owner = True
        if left["station"]["occupiedCargoIds"]:
            observed_station_owner = True
            assert not active or active not in left["station"]["occupiedCargoIds"]
    assert observed_full_case_ctu_owner is True
    assert observed_station_owner is True
    assert observed_partial_case_ctu_owner is True
    assert left["partialCase"]["status"] == "stored_partial_case"

    applied_before = list(runtime.state["simulationTest"]["appliedEventIds"])
    returned_before = left["ledger"]["returnedQuantityBase"]
    for _ in range(3):
        service._step_runtime(runtime, persist=False)
    assert left["ledger"]["returnedQuantityBase"] == returned_before == 10
    assert runtime.state["simulationTest"]["appliedEventIds"] == applied_before


def test_legacy_f4_right_only_snapshot_keeps_its_original_completion_semantics(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
) -> None:
    service, runtime = build_persisted_runtime(isolated_repo, audited_scenario)
    runtime.state["simulationTest"].pop("leftFlow")
    while not runtime.stopped:
        service._step_runtime(runtime, persist=True)
    assert runtime.state["stopReason"] == "simulation_test_merge_arrived"
    assert runtime.state["simulationTest"]["mergePoint"]["acceptedCount"] == 5
    assert runtime.state["simulationTest"]["order"]["status"] == "right_merge_arrived"
    assert runtime.state["orders"][0]["status"] == "executing"


def test_shuttle_playback_rejects_diagonal_and_lift_drift_frames() -> None:
    base = {
        "cellId": "cell-r100-c39", "level": 1, "stage": "track",
        "nodeId": "L1-r100-c39", "index": 0,
        "position": {"nodeId": "L1-r100-c39", "row": 100, "col": 39, "level": 1,
                     "worldX": 0.0, "worldY": 0.6, "worldZ": 0.0},
    }
    diagonal = deepcopy(base)
    diagonal.update({"nodeId": "L1-r102-c41", "index": 1})
    diagonal["position"].update({"nodeId": "L1-r102-c41", "row": 102, "col": 41,
                                 "worldX": 2.0, "worldZ": -2.0})
    with pytest.raises(RightHandoffValidationError, match="diagonal"):
        validate_shuttle_route_frames([base, diagonal])

    lift_drift = deepcopy(diagonal)
    lift_drift.update({"nodeId": "L2-r102-c41", "stage": "lift_moving"})
    lift_drift["position"].update({"nodeId": "L2-r102-c41", "level": 2, "worldY": 2.0})
    with pytest.raises(RightHandoffValidationError, match="drifts horizontally"):
        validate_shuttle_route_frames([base, lift_drift])


def test_events_are_complete_unique_and_atomically_idempotent(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
) -> None:
    service, runtime = build_persisted_runtime(isolated_repo, audited_scenario)
    while not runtime.stopped:
        service._step_runtime(runtime, persist=True)
    events = runtime.state["simulationTest"]["events"]
    event_ids = [item["eventId"] for item in events]
    assert len(event_ids) == len(set(event_ids))
    assert all(REQUIRED_EVENT_FIELDS <= item.keys() for item in events)
    with db.get_conn() as conn:
        stored = conn.execute(
            "SELECT payload FROM simulation_events WHERE run_id = ? AND json_extract(payload, '$.eventId') IS NOT NULL",
            (runtime.run_id,),
        ).fetchall()
    stored_events = [json.loads(row["payload"]) for row in stored]
    stored_ids = [item["eventId"] for item in stored_events]
    assert len(stored_ids) == len(set(stored_ids)) == len(runtime.state["simulationTest"]["appliedEventIds"])
    # The websocket snapshot intentionally retains only the 100 most recent
    # events. The durable event log remains the audit source for the complete
    # five-cargo lifecycle.
    assert {item["eventType"] for item in stored_events} >= {
        "cargo_pick_started", "cargo_pick_completed", "lift_entered", "lift_exited",
        "handoff_accepted", "handoff_completed", "conveyor_entered",
        "conveyor_advanced", "arm_infeed_arrived", "arm_pick_started",
        "arm_phase_grip", "arm_phase_lift", "arm_phase_rotate", "arm_phase_place",
        "arm_phase_release", "arm_release_completed", "arm_cycle_completed",
        "merge_conveyor_advanced", "merge_point_arrived",
        "left_ctu_pick_started", "left_ctu_pick_completed",
        "break_pack_handoff_started", "break_pack_case_received",
        "break_pack_case_opened", "break_pack_units_picked",
        "partial_case_created", "partial_case_loaded",
        "partial_case_return_started", "partial_case_returned",
    }
    arm_sequence = [
        "arm_pick_started", "arm_phase_grip", "arm_phase_lift", "arm_phase_rotate",
        "arm_phase_place", "arm_phase_release", "arm_release_completed",
        "arm_cycle_completed", "merge_point_arrived",
    ]
    for cargo in runtime.state["simulationTest"]["cargo"]:
        cargo_events = [
            item["eventType"] for item in stored_events
            if item.get("cargoId") == cargo["cargoId"]
        ]
        positions = [cargo_events.index(event_type) for event_type in arm_sequence]
        assert positions == sorted(positions)
        assert all(cargo_events.count(event_type) == 1 for event_type in arm_sequence)

    duplicate = deepcopy(events[-1])
    isolated_repo.update_simulation_test_tick(
        runtime.run_id, runtime.state["status"], runtime.state, runtime.metrics, [duplicate],
    )
    with db.get_conn() as conn:
        count = conn.execute(
            "SELECT count(*) FROM simulation_events WHERE run_id = ? AND json_extract(payload, '$.eventId') = ?",
            (runtime.run_id, duplicate["eventId"]),
        ).fetchone()[0]
    assert count == 1


def test_atomic_tick_rolls_back_snapshot_and_events_together(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
) -> None:
    service, runtime = build_persisted_runtime(isolated_repo, audited_scenario)
    before = isolated_repo.get_run(runtime.run_id)
    changed = deepcopy(runtime.state)
    changed["tick"] = 999
    valid = {
        "eventId": "valid-before-error", "tick": 999, "eventType": "handoff_accepted",
        "runId": runtime.run_id, "orderId": "o", "taskId": "t", "cargoId": "c",
        "fromNodeId": "a", "toNodeId": "b", "deviceId": "d", "quantityBase": 20,
        "dataSource": "simulation_test",
    }
    with pytest.raises(ValueError, match="eventId"):
        isolated_repo.update_simulation_test_tick(
            runtime.run_id, "running", changed, runtime.metrics, [valid, {"tick": 999}],
        )
    after = isolated_repo.get_run(runtime.run_id)
    assert after["payload"]["tick"] == before["payload"]["tick"]
    with sqlite3.connect(db.DB_PATH) as conn:
        count = conn.execute(
            "SELECT count(*) FROM simulation_events WHERE run_id = ? AND payload LIKE ?",
            (runtime.run_id, '%valid-before-error%'),
        ).fetchone()[0]
    assert count == 0


def test_pause_resume_speed_stop_and_reset_use_existing_run_lifecycle(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
) -> None:
    async def scenario() -> None:
        topology, audit = audited_scenario
        isolated_repo.upsert_topology(topology["id"], topology["name"], topology)
        service = SimulationService(isolated_repo, instance_id="lifecycle-owner", control_lease_seconds=30)
        result = await service.start_simulation_test(topology, audit)
        runtime = service.runtimes[result["runId"]]
        initial_ids = [item["cargoId"] for item in runtime.state["simulationTest"]["cargo"]]

        assert service.pause(runtime.run_id)["status"] == "paused"
        paused_tick = runtime.state["tick"]
        await asyncio.sleep(0.35)
        assert runtime.state["tick"] == paused_tick
        assert service.set_speed(runtime.run_id, 4)["speedMultiplier"] == 4
        assert service.resume(runtime.run_id)["status"] == "running"
        await asyncio.sleep(0.35)
        assert runtime.state["tick"] > paused_tick
        service.pause(runtime.run_id)

        await service.reset(runtime.run_id)
        assert runtime.state["status"] == "paused"
        assert runtime.state["tick"] == 0
        assert runtime.state["simulationTest"]["resetGeneration"] == 1
        assert [item["cargoId"] for item in runtime.state["simulationTest"]["cargo"]] == initial_ids
        assert runtime.state["simulationTest"]["inventory"]["right"]["availableCases"] == 5
        assert runtime.state["simulationTest"]["inventory"]["left"]["sourceQuantityBase"] == 20
        assert runtime.state["simulationTest"]["leftFlow"]["status"] == "waiting_pick"
        assert runtime.state["simulationTest"]["leftFlow"]["ledger"]["sourceQuantityBase"] == 20
        assert runtime.state["simulationTest"]["leftFlow"]["ledger"]["returnedQuantityBase"] == 0
        assert runtime.state["simulationTest"]["leftFlow"]["partialCase"] is None
        assert await service.stop(runtime.run_id) == {"runId": runtime.run_id, "status": "stopped"}
        assert isolated_repo.get_run(runtime.run_id)["status"] == "stopped"

    asyncio.run(scenario())


def test_persisted_test_run_recovers_without_replaying_or_bypassing_lease(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
) -> None:
    async def scenario() -> None:
        owner, runtime = build_persisted_runtime(
            isolated_repo, audited_scenario, run_id="run-test-recovery", instance_id="owner-a",
        )
        for _ in range(15):
            owner._step_runtime(runtime, persist=True)
        owner.pause(runtime.run_id)
        saved_tick = runtime.state["tick"]
        saved_events = list(runtime.state["simulationTest"]["appliedEventIds"])

        competing = SimulationService(isolated_repo, instance_id="owner-b", control_lease_seconds=30)
        with pytest.raises(RunControlConflict, match="another live backend"):
            await competing.recover_simulation_test(runtime.run_id)

        assert isolated_repo.release_run_control(runtime.run_id, "owner-a") is True
        recovered = await competing.recover_simulation_test(runtime.run_id)
        assert recovered.paused is True
        assert recovered.state["tick"] == saved_tick
        assert recovered.state["simulationTest"]["appliedEventIds"] == saved_events
        assert recovered.state["simulationTest"]["inventory"]["left"]["sourceQuantityBase"] == 20
        await competing.reset(runtime.run_id)
        assert recovered.state["tick"] == 0
        assert recovered.state["simulationTest"]["resetGeneration"] == 1
        await competing.stop(runtime.run_id)

    asyncio.run(scenario())


def test_test_run_does_not_mutate_topology_catalog_or_another_run(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
) -> None:
    topology, _ = audited_scenario
    topology_before = deepcopy(topology)
    catalog_before = deepcopy(load_catalog())
    isolated_repo.create_run(
        "run-unrelated", "paused", topology["id"],
        {"id": "run-unrelated", "status": "paused", "tick": 17, "formalInventory": ["unchanged"]},
        {"completedTasks": 3},
    )
    unrelated_before = isolated_repo.get_run("run-unrelated")
    service, runtime = build_persisted_runtime(isolated_repo, audited_scenario)
    for _ in range(25):
        service._step_runtime(runtime, persist=True)
    assert topology == topology_before
    assert load_catalog() == catalog_before
    assert isolated_repo.get_run("run-unrelated") == unrelated_before


def test_start_api_rejects_stale_topology_revision_before_creating_run(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topology, _ = audited_scenario
    isolated_repo.upsert_topology(topology["id"], topology["name"], topology)

    class StartRecorder:
        called = False

        async def start_simulation_test(self, topology_payload, scenario_audit):
            self.called = True
            raise AssertionError("stale revision must be rejected before service start")

    recorder = StartRecorder()
    monkeypatch.setattr(routes, "repo", isolated_repo)
    monkeypatch.setattr(routes, "simulation_service", recorder)
    app = FastAPI()
    app.include_router(routes.router)

    response = TestClient(app).post(
        f"/api/v1/simulations/test-scenarios/{SCENARIO_ID}/start",
        json={
            "topologyId": topology["id"],
            "topologyRevision": "2.0.0:stale",
            "scenarioId": SCENARIO_ID,
        },
    )
    assert response.status_code == 409
    assert "current topology" in response.json()["detail"]
    assert recorder.called is False
    assert isolated_repo.list_runs(topology["id"]) == []


def test_start_api_accepts_only_the_audited_simulation_test_contract(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topology, _ = audited_scenario
    isolated_repo.upsert_topology(topology["id"], topology["name"], topology)

    class StartRecorder:
        audit = None

        async def start_simulation_test(self, topology_payload, scenario_audit):
            self.audit = scenario_audit
            return {
                "runId": "run-test-api-contract",
                "status": "running",
                "runMode": "simulation_test",
                "scenarioId": SCENARIO_ID,
            }

    recorder = StartRecorder()
    monkeypatch.setattr(routes, "repo", isolated_repo)
    monkeypatch.setattr(routes, "simulation_service", recorder)
    app = FastAPI()
    app.include_router(routes.router)

    response = TestClient(app).post(
        f"/api/v1/simulations/test-scenarios/{SCENARIO_ID}/start",
        json={
            "topologyId": topology["id"],
            "topologyRevision": EXPECTED_REVISION,
            "scenarioId": SCENARIO_ID,
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "runId": "run-test-api-contract",
        "status": "running",
        "runMode": "simulation_test",
        "scenarioId": SCENARIO_ID,
    }
    assert recorder.audit["dataSource"] == "simulation_test"
    assert recorder.audit["rightHandoff"]["formalRouteStatus"] == "BLOCKED"
    assert recorder.audit["rightHandoff"]["simulationRouteStatus"] == "PASS_SIMULATION_ONLY"
    # The recorder proves the route contract without creating a DB Run.
    assert isolated_repo.list_runs(topology["id"]) == []


def test_general_start_api_validates_profile_and_passes_only_audited_plan_to_service(
    isolated_repo: Repository,
    audited_scenario: tuple[dict, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topology, _ = audited_scenario
    isolated_repo.upsert_topology(topology["id"], topology["name"], topology)

    class StartRecorder:
        audit = None

        async def start_general_simulation(self, topology_payload, scenario_audit):
            self.audit = scenario_audit
            return {
                "runId": "run-general-api-contract",
                "status": "running",
                "runMode": "simulation",
                "profileId": "general-order-simulation",
            }

    recorder = StartRecorder()
    monkeypatch.setattr(routes, "repo", isolated_repo)
    monkeypatch.setattr(routes, "simulation_service", recorder)
    app = FastAPI()
    app.include_router(routes.router)
    request = {
        "topologyId": topology["id"],
        "topologyRevision": EXPECTED_REVISION,
        "profileId": "general-order-simulation",
        "orders": [_general_order(40)],
        "inventoryOverrides": [],
    }

    response = TestClient(app).post("/api/v1/simulations/general/start", json=request)

    assert response.status_code == 200
    assert response.json() == {
        "runId": "run-general-api-contract",
        "status": "running",
        "runMode": "simulation",
        "profileId": "general-order-simulation",
    }
    assert recorder.audit["status"] == "PASS"
    assert recorder.audit["formalInventoryMutated"] is False
    assert recorder.audit["plan"]["inventoryMutated"] is False
    assert recorder.audit["plans"][0]["allocations"][0]["rightQuantityCases"] == 2
    assert isolated_repo.list_runs(topology["id"]) == []

    stale = TestClient(app).post(
        "/api/v1/simulations/general/start",
        json={**request, "topologyRevision": "2.0.0:stale"},
    )
    assert stale.status_code == 409

