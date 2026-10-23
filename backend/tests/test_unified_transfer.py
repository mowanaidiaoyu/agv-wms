from __future__ import annotations

import json
from pathlib import Path

from app.schemas import SimulationStartRequest
from app.services.unified_transfer import (
    apply_drop,
    apply_pick,
    build_playback_route,
    build_transfer_runtime,
    cancel_transfer,
    shuttle_contract,
)


TOPOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "topology_666.json"


def _topology() -> dict:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


def _request(**overrides) -> dict:
    request = {
        "id": "transfer-sku-001-l1-l4",
        "vehicleId": "SHUTTLE-UNIFIED-01",
        "skuId": "SKU-001",
        "skuName": "SKU-001",
        "sourceLocationId": "SIM-BIN-L1-r100-c7",
        "destinationLocationId": "SIM-BIN-L4-r100-c51",
        "sourceTrackNode": "L1-r100-c7",
        "destinationTrackNode": "L4-r100-c51",
        "sourceLevel": 1,
        "destinationLevel": 4,
        "quantity": 2,
        "algorithm": "astar",
        "initialSourceQuantity": 10,
        "initialDestinationQuantity": 5,
    }
    request.update(overrides)
    return request


def test_unified_start_request_accepts_orders_and_transfers_together() -> None:
    payload = SimulationStartRequest.model_validate({
        "topologyId": "topology-6ceb4920",
        "orders": [{
            "id": "ORD-DEMO-001",
            "priority": "normal",
            "targetStation": "出库口 01",
            "createdAt": "2026-10-23T00:00:00Z",
            "lines": [{
                "id": "line-demo-001",
                "skuId": "SKU-001",
                "skuName": "SKU-001",
                "quantity": 5,
            }],
        }],
        "transfers": [_request()],
    })

    assert payload.orders[0].lines[0].quantity == 5
    assert payload.transfers[0].quantity == 2


def test_pyc_topology_uses_validated_c_shuttle_graph_contract() -> None:
    contract = shuttle_contract(_topology())

    assert contract["topologyId"] == "topology-6ceb4920"
    assert contract["topologyRevision"] == "2.0.0:7fdde35b3ef6f522"
    assert contract["planningEnabled"] is True
    assert {lift["lift_id"] for lift in contract["lifts"]} == {
        "shuttle-lift-a",
        "shuttle-lift-b",
    }
    assert all(lift["timing_source"] == "synthetic_demo" for lift in contract["lifts"])


def test_l1_to_l4_transfer_conserves_inventory_and_is_idempotent() -> None:
    tasks, robots, balances, events = build_transfer_runtime(_topology(), [_request()])
    task = tasks[0]
    state = {"transferInventory": balances, "transferEvents": events}
    source, destination = balances

    assert task["plan"]["result_status"] == "success"
    assert task["plan"]["chosen_lift"] in {"shuttle-lift-a", "shuttle-lift-b"}
    assert robots[0]["currentLayer"] == 1
    assert (source["quantityOnHand"], source["quantityReserved"]) == (10, 2)
    assert destination["quantityOnHand"] == 5

    assert apply_pick(state, task, tick=2) is True
    assert apply_pick(state, task, tick=3) is False
    assert (source["quantityOnHand"], source["quantityReserved"]) == (8, 0)
    assert task["inTransitQuantity"] == 2
    assert source["quantityOnHand"] + destination["quantityOnHand"] + task["inTransitQuantity"] == 15

    assert apply_drop(state, task, tick=20) is True
    assert apply_drop(state, task, tick=21) is False
    assert destination["quantityOnHand"] == 7
    assert task["inTransitQuantity"] == 0
    assert source["quantityOnHand"] + destination["quantityOnHand"] == 15
    assert [event["eventType"] for event in state["transferEvents"]] == ["reserve", "pick", "drop"]


def test_c_path_is_adapted_to_explicit_lift_playback_stages() -> None:
    tasks, _, _, _ = build_transfer_runtime(_topology(), [_request()])
    paths, layers, stages = build_playback_route(tasks[0]["plan"], tick_seconds=0.6)

    assert len(paths) == len(layers) == len(stages)
    assert {"enter_lift", "lift_moving", "exit_lift"}.issubset(stages)
    lift_layers = [layer for layer, stage in zip(layers, stages) if stage == "lift_moving"]
    assert lift_layers[0] > 1
    assert lift_layers[-1] == 4


def test_cancel_releases_reservation_or_returns_in_transit_once() -> None:
    tasks, _, balances, events = build_transfer_runtime(_topology(), [_request()])
    task = tasks[0]
    state = {"transferInventory": balances, "transferEvents": events}
    assert cancel_transfer(state, task, tick=1) is True
    assert cancel_transfer(state, task, tick=2) is False
    assert balances[0]["quantityOnHand"] == 10
    assert balances[0]["quantityReserved"] == 0

    tasks, _, balances, events = build_transfer_runtime(_topology(), [_request(id="transfer-after-pick")])
    task = tasks[0]
    state = {"transferInventory": balances, "transferEvents": events}
    apply_pick(state, task, tick=2)
    assert cancel_transfer(state, task, tick=3) is True
    assert balances[0]["quantityOnHand"] == 10
    assert task["inTransitQuantity"] == 0
