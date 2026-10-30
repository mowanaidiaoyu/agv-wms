import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.services.transport_layout import LayoutRevisionMismatch, load_transport_layout_plan


TOPOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "topology_666.json"


@pytest.fixture(scope="module")
def topology() -> dict:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def plan(topology: dict):
    return load_transport_layout_plan(topology)


def test_plan_is_bound_to_real_topology_and_revision(plan) -> None:
    assert plan.topologyId == "topology-6ceb4920"
    assert plan.topologyRevision == "2.0.0:dc95637ce2811b8c"
    assert plan.overallStatus == "PARTIAL"
    assert plan.summary["formalTopologyMutated"] is False


def test_candidate_ids_are_globally_unique(plan) -> None:
    ids = (
        [item.referenceId for item in plan.references]
        + [item.facilityId for item in plan.facilities]
        + [item.connectionId for item in plan.connections]
        + [item.routeId for item in plan.routes]
        + [item.optionId for item in plan.options]
    )
    assert len(ids) == len(set(ids))
    assert "LAYOUT_DUPLICATE_ID" not in {issue.code for issue in plan.issues}


def test_all_candidate_coordinates_are_in_bounds(topology: dict, plan) -> None:
    positions = [item.position for item in plan.references]
    positions += [item.position for item in plan.facilities]
    positions += [point for connection in plan.connections for point in connection.points]
    assert all(0 <= position.row < topology["grid"]["rows"] for position in positions)
    assert all(0 <= position.col < topology["grid"]["cols"] for position in positions)
    assert "LAYOUT_OUT_OF_BOUNDS" not in {issue.code for issue in plan.issues}


def test_exclusive_candidates_do_not_cover_racks_or_vehicle_devices(plan) -> None:
    codes = {issue.code for issue in plan.issues}
    assert "LAYOUT_FIXED_NODE_COLLISION" not in codes
    assert "LAYOUT_BLOCKED_CELL_COLLISION" not in codes
    co_located = [
        item for item in plan.facilities if item.occupancyPolicy == "co_located_with_reference"
    ]
    assert co_located
    assert all(item.existingRefIds for item in co_located)


def test_every_non_blocked_level_change_has_a_device(plan) -> None:
    for connection in plan.connections:
        changes_level = any(
            left.level != right.level
            for left, right in zip(connection.points, connection.points[1:])
        )
        if changes_level and connection.status != "BLOCKED":
            assert connection.heightTransition
            assert connection.heightTransition.get("deviceId")
    assert "LAYOUT_UNDECLARED_LEVEL_CHANGE" not in {issue.code for issue in plan.issues}


def test_every_port_has_a_directed_candidate_path_to_pick_station(plan) -> None:
    for port_id in ("zone-port", "zone-port-2", "zone-port-3", "zone-port-4"):
        stop = next(item for item in plan.facilities if item.facilityId == f"proposed-ctu-stop-{port_id}")
        station = next(item for item in plan.facilities if item.facilityId == f"proposed-pick-station-{port_id}")
        assert stop.position.col == station.position.col
        assert stop.position.row < station.position.row
        path = [
            item for item in plan.connections
            if item.connectionId in {
                f"left-{port_id}-stop-to-input",
                f"left-{port_id}-input-to-pick",
            }
        ]
        assert len(path) == 2
        assert all(item.status == "PASS" and item.direction == "down" for item in path)


def test_shuttle_sources_have_explicit_pickup_and_dock_approach(plan) -> None:
    source_27 = next(item for item in plan.connections if item.connectionId == "right-source27-fork-pick")
    source_29 = next(item for item in plan.connections if item.connectionId == "right-source29-fork-pick")
    approach = next(item for item in plan.connections if item.connectionId == "right-overhead-track-to-dock-transfer")
    descent = next(item for item in plan.connections if item.connectionId == "right-dock-transfer-descent")
    dock = next(item for item in plan.connections if item.connectionId == "right-dock-to-ground-conveyor")
    assert source_27.status == source_29.status == "PASS"
    assert approach.networkType == "shuttle"
    assert approach.status == "PARTIAL"
    assert descent.heightTransition
    assert dock.edgeType == "handoff"
    assert all("conveyor" not in item.networkType for item in (source_27, source_29, approach))


def test_conveyor_directions_are_explicit_and_invalid_reverse_is_blocked(plan) -> None:
    option_a = next(item for item in plan.options if item.optionId == "scheme-a-height-transfer")
    option_a_connections = [
        item for item in plan.connections if item.connectionId in option_a.connectionIds
    ]
    conveyor_edges = [item for item in option_a_connections if item.edgeType == "conveyor"]
    assert conveyor_edges
    assert all(item.direction == "right" for item in conveyor_edges)
    reverse = next(item for item in plan.connections if item.connectionId == "scheme-b-left-input-to-turn-transfer-14")
    assert reverse.direction == "left"
    assert reverse.status == "BLOCKED"


def test_c48_to_c52_uses_an_explicit_height_transfer(plan) -> None:
    bridge = next(item for item in plan.connections if item.connectionId == "scheme-a-c48-to-c52-height-transfer")
    assert [(point.col, point.level) for point in bridge.points] == [
        (48, 1),
        (49, 1),
        (50, 1),
        (51, 2),
        (52, 2),
    ]
    assert bridge.heightTransition is not None
    assert bridge.heightTransition["fromLevel"] == 1
    assert bridge.heightTransition["toLevel"] == 2
    assert bridge.requiredCellIds == ["cell-r74-c49", "cell-r74-c50", "cell-r74-c51"]


def test_recommended_merge_has_two_inputs_and_one_output(plan) -> None:
    merge_id = "proposed-merge-a-r74-c67"
    inputs = [item for item in plan.connections if item.toId == merge_id and item.status != "BLOCKED"]
    outputs = [item for item in plan.connections if item.fromId == merge_id and item.status != "BLOCKED"]
    assert {item.connectionId for item in inputs} == {
        "scheme-a-right-input-to-merge",
        "scheme-a-left-input-to-merge",
    }
    assert any(item.connectionId == "scheme-a-merge-to-outbound" for item in outputs)
    assert "LAYOUT_MERGE_INPUTS_INSUFFICIENT" not in {issue.code for issue in plan.issues}


def test_recommended_outbound_has_a_directed_arrival(plan) -> None:
    outbound_id = "proposed-outbound-a-r74-c96"
    arrivals = [
        item for item in plan.connections
        if item.toId == outbound_id and item.status != "BLOCKED"
    ]
    assert [item.connectionId for item in arrivals] == ["scheme-a-merge-to-outbound"]
    assert arrivals[0].direction == "right"
    assert any(item.fromId == outbound_id for item in plan.connections)


def test_plan_generation_does_not_mutate_formal_topology(topology: dict) -> None:
    snapshot = deepcopy(topology)
    load_transport_layout_plan(topology)
    assert topology == snapshot
    assert "transportNetwork" not in topology


def test_picking_arm_uses_user_confirmed_installation_and_row86_dropoff(
    topology: dict, plan
) -> None:
    arm = plan.pickingArm
    assert arm is not None
    assert (arm.installation.row, arm.installation.col, arm.installation.level) == (91, 54, 1)
    assert not any(
        int(cell["row"]) == 91 and int(cell["col"]) == 54
        for cell in topology["cells"]
    )
    assert not any(
        int(node["row"]) == 91 and int(node["col"]) == 54
        for node in topology["nodes"]
    )
    drop_cell = next(cell for cell in topology["cells"] if cell["id"] == arm.dropoff.topologyCellId)
    pickup_cell = next(cell for cell in topology["cells"] if cell["id"] == arm.pickup.topologyCellId)
    assert pickup_cell["id"] == "cell-r92-c48"
    assert pickup_cell["type"] == "conveyor"
    assert pickup_cell["conveyorGround"] is True
    assert pickup_cell["conveyorDirection"] == "right"
    assert drop_cell["id"] == "cell-r86-c50"
    assert drop_cell["type"] == "conveyor"
    assert drop_cell["conveyorDirection"] == "right"
    assert drop_cell["levels"] == 2


def test_picking_arm_is_preview_only_and_has_real_directed_downstream(
    topology: dict, plan
) -> None:
    arm = plan.pickingArm
    assert arm is not None
    assert arm.pickup.provenance == "topology"
    assert arm.dropoff.provenance == "topology"
    assert arm.canDeclareDelivery is False
    # 用户在编辑器删除了错误的 row-86 段（c51..c59，含 turn_transfer-15）：
    # 放货点 c50 仍在，但下游衔接格缺失 → 下游如实 BLOCKED，等待重绘。
    assert arm.downstreamCellId == "cell-r86-c51"
    assert arm.downstreamStatus == "BLOCKED"
    assert not any(cell["id"] == arm.downstreamCellId for cell in topology["cells"])
    assert arm.liftBId == "shuttle-lift-b"
    assert arm.liftBClearanceCells == pytest.approx(8.60)
    assert arm.workRadius == pytest.approx(7.9)
    assert arm.kinematics.upperArmLength == pytest.approx(4.0)
    assert arm.kinematics.forearmLength == pytest.approx(3.9)
    assert arm.kinematics.shoulderHeight == pytest.approx(4.0)
    assert arm.kinematics.safeTravelHeight == pytest.approx(6.3)
    issue_codes = {issue.code for issue in plan.issues}
    assert "PICKING_ARM_HANDOFF_PENDING_G3" in issue_codes
    assert "PICKING_ARM_PICK_SOURCE_UNCONFIRMED" in issue_codes
    assert "PICKING_ARM_TARGET_LINE_TRANSFER_UNAPPROVED" not in issue_codes
    assert "PICKING_ARM_POST_TURN_DIRECTION_OVERRIDE" in issue_codes


def test_enlarged_arm_covers_both_endpoints_without_occupying_formal_equipment(plan) -> None:
    arm = plan.pickingArm
    assert arm is not None
    for endpoint in (arm.pickup, arm.dropoff):
        planar_reach = (
            (endpoint.position.row - arm.installation.row) ** 2
            + (endpoint.position.col - arm.installation.col) ** 2
        ) ** 0.5
        assert planar_reach <= arm.workRadius
    assert arm.kinematics.upperArmLength + arm.kinematics.forearmLength >= arm.workRadius
    issue_codes = {issue.code for issue in plan.issues}
    assert "PICKING_ARM_BASE_OCCUPIED" not in issue_codes
    assert "PICKING_ARM_BASE_CLEARANCE_INSUFFICIENT" not in issue_codes
    assert "PICKING_ARM_ENDPOINT_OUT_OF_REACH" not in issue_codes
    assert "PICKING_ARM_KINEMATICS_UNDERSIZED" not in issue_codes


def test_picking_arm_preview_path_stops_at_c50_until_segment_redrawn(plan) -> None:
    arm = plan.pickingArm
    assert arm is not None
    # 用户删除 c51..c59 后，预览路径从放货点 c50 出发立即受阻：只含起点一格。
    assert [step.cellId for step in arm.previewConveyorPath] == ["cell-r86-c50"]
    assert all(step.position.level == 2 for step in arm.previewConveyorPath)
    assert all(step.beltHeight == pytest.approx(3.0) for step in arm.previewConveyorPath)
    assert all(step.direction == "right" for step in arm.previewConveyorPath)
    assert arm.previewEndpointId == "cell-r86-c50"
    assert arm.previewPathStatus == "BLOCKED"
    assert "r86,c51" in arm.previewStopReason
    assert arm.previewTransferDeviceId == "turn_transfer-15"
    # 用户以 c60 的显式甲板（lower north→south outbound）替代原转向设备；
    # turn_transfer-15 节点已随错误线一并删除，路线声明保留为重绘参考。
    assert not any(node["id"] == "turn_transfer-15" for node in topology_fixture()["nodes"])
    assert arm.previewTransferStatus == "PARTIAL"


def topology_fixture():
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


def test_revision_mismatch_refuses_the_plan(topology: dict) -> None:
    mismatched = deepcopy(topology)
    mismatched["meta"]["version"] = "2.0.1"
    with pytest.raises(LayoutRevisionMismatch, match="topologyRevision"):
        load_transport_layout_plan(mismatched)


def test_all_candidates_remain_proposed_and_scheme_b_stays_blocked(plan) -> None:
    assert plan.facilities
    assert all(item.status == "proposed" for item in plan.facilities)
    scheme_b = next(item for item in plan.options if item.optionId == "scheme-b-existing-transfer-reroute")
    assert scheme_b.status == "BLOCKED"
    assert scheme_b.blockingReasons
