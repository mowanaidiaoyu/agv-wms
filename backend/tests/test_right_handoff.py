from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.schemas import TopologyPayload
from app.services.fulfillment_data import (
    load_simulation_test_scenario,
    validate_simulation_test_scenario,
)
from app.services.goods import load_catalog
from app.services.right_handoff import (
    RightHandoffConflict,
    RunLocalHandoffState,
    can_authoritative_arm_grip,
)
from app.services.shuttle_topology import build_shuttle_track_graph, topology_revision


TOPOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "topology_666.json"
EXPECTED_REVISION = "2.0.0:01845f1f27d1f457"
SCENARIO_ID = "right-left-110-case-test"


def topology() -> dict:
    return TopologyPayload.model_validate(
        json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    ).model_dump(by_alias=True)


def audit() -> tuple[dict, dict]:
    payload = topology()
    return payload, validate_simulation_test_scenario(payload, load_catalog(), SCENARIO_ID)


def test_five_right_cases_have_unique_cargo_ids_and_conserve_100_units() -> None:
    _, result = audit()
    cargo = result["rightHandoff"]["cargo"]
    assert len(cargo) == 5
    assert len({item["cargoId"] for item in cargo}) == 5
    assert sum(item["quantityCases"] for item in cargo) == 5
    assert sum(item["quantityBase"] for item in cargo) == 100
    assert all(item["status"] == "waiting_pick" for item in cargo)


def test_left_break_pack_contract_conserves_twenty_units() -> None:
    _, result = audit()
    allocation = result["plan"]["allocations"][0]
    assert allocation["leftSourceQuantityBase"] == 20
    assert allocation["leftPickQuantityBase"] == 10
    assert allocation["leftReturnQuantityBase"] == 10
    assert allocation["leftSourceQuantityBase"] == (
        allocation["leftPickQuantityBase"] + allocation["leftReturnQuantityBase"]
    )


def test_shuttle_graph_path_is_legal_uses_lift_a_and_never_enters_conveyor() -> None:
    payload, result = audit()
    handoff = result["rightHandoff"]
    graph = build_shuttle_track_graph(payload)
    assert handoff["chosenLift"] == "shuttle-lift-a"
    assert handoff["shuttlePath"][0] == "L2-r96-c31"
    assert handoff["shuttlePath"][-1] == "L1-r96-c21"
    assert handoff["egressPath"][-1] == "L1-r96-c27"
    assert all(node_id in graph for node_id in handoff["shuttlePath"] + handoff["egressPath"])
    assert all(not node_id.startswith("cell-") for node_id in handoff["shuttlePath"])


def test_missing_authoritative_handoff_keeps_formal_route_blocked() -> None:
    _, result = audit()
    handoff = result["rightHandoff"]
    assert handoff["formalRouteStatus"] == "BLOCKED"
    assert handoff["simulationRouteStatus"] == "PASS_SIMULATION_ONLY"
    assert handoff["existingDockAssessment"]["validationStatus"] == "blocked"
    assert handoff["segments"][-1]["validationStatus"] == "blocked"


def test_simulation_only_facilities_are_explicit_and_do_not_mutate_topology() -> None:
    payload = topology()
    before = deepcopy(payload)
    result = validate_simulation_test_scenario(payload, load_catalog(), SCENARIO_ID)
    facilities = result["rightHandoff"]["facilities"]
    assert len(facilities) == 3
    assert all(item["dataSource"] == "simulation_test" for item in facilities)
    assert all(item["validationStatus"] == "simulation_only" for item in facilities)
    assert all(item["capacity"] == 1 for item in facilities)
    assert payload == before
    assert not any(node["id"].startswith("simtest-") for node in payload["nodes"])


def test_capacity_one_rejects_two_simultaneous_cargoes() -> None:
    _, result = audit()
    handoff = result["rightHandoff"]
    state = RunLocalHandoffState("run-local-a", handoff["cargo"], handoff["facilities"])
    cargo_ids = [item["cargoId"] for item in state.cargo]
    device_id = handoff["facilities"][0]["deviceId"]
    state.begin_transfer(cargo_ids[0], device_id)
    with pytest.raises(RightHandoffConflict, match="capacity"):
        state.begin_transfer(cargo_ids[1], device_id)


def test_same_cargo_cannot_repeat_the_same_handoff() -> None:
    _, result = audit()
    handoff = result["rightHandoff"]
    state = RunLocalHandoffState("run-local-a", handoff["cargo"], handoff["facilities"])
    cargo_id = state.cargo[0]["cargoId"]
    device_id = handoff["facilities"][0]["deviceId"]
    state.begin_transfer(cargo_id, device_id)
    state.complete_transfer(cargo_id, device_id)
    with pytest.raises(RightHandoffConflict, match="Duplicate"):
        state.begin_transfer(cargo_id, device_id)


def test_arm_requires_matching_authoritative_waiting_arm_signal() -> None:
    assert can_authoritative_arm_grip(None, None) is False
    cargo = {"cargoId": "cargo-1", "status": "waiting_arm", "preview": False}
    assert can_authoritative_arm_grip(cargo, {"cargoId": "cargo-1", "authoritative": False}) is False
    assert can_authoritative_arm_grip({**cargo, "preview": True}, {"cargoId": "cargo-1", "authoritative": True}) is False
    assert can_authoritative_arm_grip(cargo, {"cargoId": "cargo-1", "authoritative": True}) is True


def test_reset_rebuilds_one_run_local_snapshot_without_duplicate_cargo() -> None:
    _, result = audit()
    handoff = result["rightHandoff"]
    state = RunLocalHandoffState("run-local-a", handoff["cargo"], handoff["facilities"])
    before = [item["cargoId"] for item in state.cargo]
    state.begin_transfer(before[0], handoff["facilities"][0]["deviceId"])
    state.reset()
    after = [item["cargoId"] for item in state.cargo]
    assert after == before
    assert len(after) == len(set(after)) == 5
    assert all(item["status"] == "waiting_pick" for item in state.cargo)
    assert state.active_by_device == {}


def test_preview_signal_does_not_change_test_order_or_cargo_state() -> None:
    _, result = audit()
    handoff = result["rightHandoff"]
    state = RunLocalHandoffState("run-local-a", handoff["cargo"], handoff["facilities"])
    before = deepcopy(state.cargo)
    assert can_authoritative_arm_grip(state.cargo[0], {"cargoId": state.cargo[0]["cargoId"], "authoritative": False}) is False
    assert state.cargo == before
    assert result["plan"]["canDeclareCompleted"] is False


def test_scenario_validation_does_not_touch_formal_catalog_inventory_or_other_runs() -> None:
    payload = topology()
    scenario = load_simulation_test_scenario(SCENARIO_ID)
    before_scenario = deepcopy(scenario)
    first = validate_simulation_test_scenario(payload, load_catalog(), SCENARIO_ID)
    second = validate_simulation_test_scenario(payload, load_catalog(), SCENARIO_ID)
    assert first == second
    assert scenario == before_scenario
    assert first["businessGuards"]["mutateFormalInventory"] is False
    assert first["businessGuards"]["mutateOtherOrders"] is False
    assert first["isolationMode"] == "run_local_snapshot"


def test_topology_revision_and_real_conveyor_endpoint_are_enforced() -> None:
    payload, result = audit()
    assert topology_revision(payload) == EXPECTED_REVISION
    assert result["topologyRevision"] == EXPECTED_REVISION
    assert result["rightHandoff"]["conveyorPath"][-1] == "1F:92:47:lower"
    arm = next(cell for cell in payload["cells"] if cell["id"] == "cell-r92-c48")
    assert arm["type"] == "conveyor"
    assert arm["conveyorGround"] is True
