from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.schemas import PickingArmTransportCargo, TopologyPayload
from app.services.merge_point import (
    DuplicateCargoError,
    MergePointCapacityError,
    MergePointError,
    accept_right_warehouse_cargo,
    release_right_warehouse_cargo,
)
from app.services.transport_layout import (
    LayoutRevisionMismatch,
    LayoutValidationError,
    build_transport_layout_plan,
    load_transport_layout_plan,
)


DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def _topology() -> dict:
    raw = json.loads((DATA_ROOT / "topology_666.json").read_text(encoding="utf-8"))
    return TopologyPayload.model_validate(raw).model_dump(by_alias=True)


def _plan() -> dict:
    return json.loads(
        (
            DATA_ROOT
            / "transport_layout_plans"
            / "topology-6ceb4920.arm-preview.json"
        ).read_text(encoding="utf-8")
    )


def test_arm_preview_uses_verified_coordinates_and_real_conveyor_nodes() -> None:
    plan = load_transport_layout_plan(_topology())
    arm = plan.pickingArm

    assert arm.installation.model_dump() == {
        "floor": "1F", "row": 91, "col": 54, "level": 1
    }
    assert arm.pickup.topologyCellId == "cell-r92-c48"
    assert arm.dropoff.topologyCellId == "cell-r86-c50"
    assert arm.previewConveyorPath[0].cellId == "cell-r86-c50"
    assert arm.previewConveyorPath[-1].cellId == "cell-r108-c68"
    expected_positions = (
        [(86, col) for col in range(50, 61)]
        + [(row, 60) for row in range(87, 109)]
        + [(108, col) for col in range(61, 69)]
    )
    assert [
        (step.position.row, step.position.col) for step in arm.previewConveyorPath
    ] == expected_positions
    assert all(step.direction == "right" for step in arm.previewConveyorPath[:10])
    assert all(step.direction == "down" for step in arm.previewConveyorPath[10:32])
    assert all(step.direction == "right" for step in arm.previewConveyorPath[32:])
    assert all(step.directionSource == "topology" for step in arm.previewConveyorPath)
    first_turn = next(
        step for step in arm.previewConveyorPath
        if (step.position.row, step.position.col) == (86, 60)
    )
    second_turn = next(
        step for step in arm.previewConveyorPath
        if (step.position.row, step.position.col) == (108, 60)
    )
    assert first_turn.transferDeviceId == "turn_transfer-15"
    assert first_turn.transitionToNext == "device_transfer"
    assert second_turn.transferDeviceId == "turn_transfer-13"
    assert second_turn.transitionToNext == "device_transfer"
    assert arm.previewConveyorPath[-1].deviceIds == ["lift_transfer-09"]
    assert arm.mergePoint.model_dump() == {
        "mergePointId": "right-order-merge-r108-c68",
        "topologyNodeId": "lift_transfer-09",
        "row": 108,
        "col": 68,
        "level": 2,
        "capacity": 1,
        "occupiedCargoIds": [],
        "acceptedCargoIds": [],
        "status": "available",
        "acceptedWarehouse": "right",
        "cargoOrderIds": {},
    }


def test_preview_generation_does_not_mutate_topology_inventory_or_orders() -> None:
    topology = _topology()
    snapshot = deepcopy(topology)
    plan = build_transport_layout_plan(topology, _plan())

    assert topology == snapshot
    assert plan.summary == {
        "previewOnly": True,
        "inventoryMutated": False,
        "ordersCompleted": False,
        "formalTopologyMutated": False,
        "conveyorNodeCount": 41,
    }
    assert plan.pickingArm.canDeclareDelivery is False


def test_preview_rejects_topology_revision_mismatch(monkeypatch) -> None:
    raw = _plan()
    raw["topologyRevision"] = "2.0.0:stale"
    monkeypatch.setattr(
        "app.services.transport_layout._load_raw_plan",
        lambda _topology_id: raw,
    )

    with pytest.raises(LayoutRevisionMismatch, match="topologyRevision"):
        load_transport_layout_plan(_topology())


def test_preview_rejects_an_occupied_arm_installation_cell() -> None:
    topology = _topology()
    topology["cells"].append({
        "id": "cell-r91-c54",
        "floor": "1F",
        "row": 91,
        "col": 54,
        "type": "blocked",
        "cost": 1,
        "walkable": False,
    })

    with pytest.raises(LayoutValidationError, match="installation collides"):
        build_transport_layout_plan(topology, _plan())


def test_preview_rejects_illegal_dropoff_direction() -> None:
    topology = _topology()
    dropoff = next(cell for cell in topology["cells"] if cell["id"] == "cell-r86-c50")
    dropoff["conveyorDirection"] = "left"

    with pytest.raises(LayoutValidationError, match="direction does not match"):
        build_transport_layout_plan(topology, _plan())


def test_preview_reports_a_disconnected_path() -> None:
    topology = _topology()
    topology["cells"] = [
        cell for cell in topology["cells"] if cell["id"] != "cell-r86-c51"
    ]

    with pytest.raises(LayoutValidationError, match="disconnected"):
        build_transport_layout_plan(topology, _plan())


def test_required_route_segments_are_contiguous_and_end_at_the_merge_point() -> None:
    steps = load_transport_layout_plan(_topology()).pickingArm.previewConveyorPath
    positions = [(step.position.row, step.position.col) for step in steps]
    assert positions[:11] == [(86, col) for col in range(50, 61)]
    assert positions[10:33] == [(row, 60) for row in range(86, 109)]
    assert positions[32:39] == [(108, col) for col in range(60, 67)]
    assert positions[38:] == [(108, col) for col in range(66, 69)]
    assert all(
        abs(left[0] - right[0]) + abs(left[1] - right[1]) == 1
        for left, right in zip(positions, positions[1:])
    )
    assert steps[-1].transitionToNext == "endpoint"
    assert steps[-1].cellId == "cell-r108-c68"


def test_route_rejects_turn_transfer_with_illegal_input_output() -> None:
    topology = _topology()
    turn = next(node for node in topology["nodes"] if node["id"] == "turn_transfer-15")
    turn["transferInputSide"] = "east"

    with pytest.raises(LayoutValidationError, match="matching configured turn_transfer"):
        build_transport_layout_plan(topology, _plan())


def test_route_rejects_reverse_upper_deck_direction() -> None:
    topology = _topology()
    anchor = next(cell for cell in topology["cells"] if cell["id"] == "cell-r88-c60")
    anchor["conveyorDecks"]["upper"]["inputSide"] = "south"
    anchor["conveyorDecks"]["upper"]["outputSide"] = "north"

    with pytest.raises(LayoutValidationError, match="matching configured turn_transfer"):
        build_transport_layout_plan(topology, _plan())


def test_route_rejects_cross_level_gap() -> None:
    topology = _topology()
    anchor = next(cell for cell in topology["cells"] if cell["id"] == "cell-r88-c60")
    anchor["conveyorDecks"]["upper"]["enabled"] = False

    with pytest.raises(LayoutValidationError, match="upper deck is unavailable"):
        build_transport_layout_plan(topology, _plan())


def _cargo(cargo_id: str, order_id: str = "ORDER-RIGHT-1") -> PickingArmTransportCargo:
    return PickingArmTransportCargo(
        orderId=order_id,
        cargoId=cargo_id,
        skuId="SKU-A",
        quantityCases=2,
        unitsPerCase=6,
        quantityBase=12,
        sourceLocationId="right-warehouse-r92-c48",
        currentNodeId="cell-r108-c67",
        targetMergePointId="right-order-merge-r108-c68",
        transportStatus="waiting_at_merge",
    )


def test_merge_point_enforces_capacity_and_keeps_order_identity() -> None:
    merge_point = load_transport_layout_plan(_topology()).pickingArm.mergePoint
    accepted = accept_right_warehouse_cargo(merge_point, _cargo("cargo-1", "ORDER-1"))

    assert merge_point.occupiedCargoIds == []
    assert accepted.occupiedCargoIds == ["cargo-1"]
    assert accepted.acceptedCargoIds == ["cargo-1"]
    assert accepted.cargoOrderIds == {"cargo-1": "ORDER-1"}
    assert accepted.status == "full"
    with pytest.raises(MergePointCapacityError, match="is full"):
        accept_right_warehouse_cargo(accepted, _cargo("cargo-2", "ORDER-2"))

    released = release_right_warehouse_cargo(accepted, "cargo-1")
    second_order = accept_right_warehouse_cargo(released, _cargo("cargo-2", "ORDER-2"))
    assert second_order.cargoOrderIds == {"cargo-1": "ORDER-1", "cargo-2": "ORDER-2"}
    assert second_order.occupiedCargoIds == ["cargo-2"]


def test_merge_point_rejects_duplicate_cargo_idempotently() -> None:
    merge_point = load_transport_layout_plan(_topology()).pickingArm.mergePoint
    accepted = accept_right_warehouse_cargo(merge_point, _cargo("cargo-1"))
    released = release_right_warehouse_cargo(accepted, "cargo-1")

    with pytest.raises(DuplicateCargoError, match="already been accepted"):
        accept_right_warehouse_cargo(released, _cargo("cargo-1"))


def test_merge_point_rejects_left_warehouse_cargo() -> None:
    merge_point = load_transport_layout_plan(_topology()).pickingArm.mergePoint
    left_cargo = _cargo("left-cargo").model_copy(update={"sourceWarehouse": "left"})

    with pytest.raises(MergePointError, match="only accepts right-warehouse cargo"):
        accept_right_warehouse_cargo(merge_point, left_cargo)
