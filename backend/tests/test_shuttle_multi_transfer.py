from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.core.db import get_conn
from app.core.repository import Repository
from app.services.shuttle_demo_transfer import ShuttleDemoTransferConflict, ShuttleDemoTransferService
from app.services.shuttle_multi_transfer import ShuttleMultiTransferService
from app.shuttle_multi_schemas import ShuttleMultiPlanningOptions
from app.shuttle_transfer_schemas import ShuttleMultiTransferBatch


ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY_PATH = ROOT / "docs" / "topologies" / "666.json"


@pytest.fixture()
def multi_transfer(tmp_path: Path):
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8-sig"))
    repository = Repository(tmp_path / "multi-transfer.db")
    repository.upsert_topology(topology["id"], topology["name"], topology)
    single = ShuttleDemoTransferService(repository)
    single.initialize_inventory(topology["id"])
    return topology, repository, single, ShuttleMultiTransferService(repository)


def _unique_locations(single: ShuttleDemoTransferService, topology_id: str, *, sku_id=None):
    locations = single.list_locations(
        topology_id, level=1, sku_id=sku_id, transfer_eligible=True, limit=5000
    )["locations"]
    selected = []
    used_access = set()
    for item in locations:
        access = item["access_track_node_id"]
        if access not in used_access:
            selected.append(item)
            used_access.add(access)
    return selected


def _request(single: ShuttleDemoTransferService, topology_id: str):
    sources = _unique_locations(single, topology_id, sku_id="SKU-DEMO-001")
    all_locations = _unique_locations(single, topology_id)
    assert len(sources) >= 2 and len(all_locations) >= 6
    source_a, source_b = sources[0], sources[-1]
    destinations = [
        item
        for item in all_locations
        if item["access_track_node_id"] not in {
            source_a["access_track_node_id"], source_b["access_track_node_id"]
        }
        and item["location_id"] not in {source_a["location_id"], source_b["location_id"]}
    ]
    destination_a, destination_b = destinations[len(destinations) // 3], destinations[-1]
    return [
        {
            "vehicle_id": "SHUTTLE-01",
            "priority": 1,
            "algorithm": "astar",
            "current_track_node_id": source_a["access_track_node_id"],
            "source_location_id": source_a["location_id"],
            "destination_location_id": destination_a["location_id"],
            "sku_id": "SKU-DEMO-001",
            "quantity": 2,
        },
        {
            "vehicle_id": "SHUTTLE-02",
            "priority": 2,
            "algorithm": "dijkstra",
            "current_track_node_id": source_b["access_track_node_id"],
            "source_location_id": source_b["location_id"],
            "destination_location_id": destination_b["location_id"],
            "sku_id": "SKU-DEMO-001",
            "quantity": 3,
        },
    ]


def _options():
    return ShuttleMultiPlanningOptions(max_time_steps=3000).model_dump()


def test_batch_plan_reserves_real_pick_and_drop_service_windows(multi_transfer):
    topology, _, single, service = multi_transfer
    batch = service.create_and_plan(
        topology["id"], f"test-{uuid.uuid4().hex}", _request(single, topology["id"]), _options()
    )
    assert batch["status"] == "running"
    assert batch["plan"]["safety_validation"]["safe"] is True
    assert batch["plan"]["transfer_contract"]["pickup_occupancy"] == "finite_service_window"
    for vehicle in batch["plan"]["vehicles"]:
        actions = [sample["action"] for sample in vehicle["timed_trajectory"]]
        assert "pick_service" in actions
        assert "drop_service" in actions
        pick_samples = [sample for sample in vehicle["timed_trajectory"] if sample["action"] == "pick_service"]
        assert len({(sample["world_x"], sample["world_y"], sample["world_z"]) for sample in pick_samples}) == 1
    assert all(task["runtime_phase"] in {"moving_to_pickup", "picking"} for task in batch["tasks"])
    assert ShuttleMultiTransferBatch.model_validate(batch).batch_id == batch["batch_id"]


def test_idempotency_key_returns_same_batch_without_extra_reservation(multi_transfer):
    topology, _, single, service = multi_transfer
    vehicles = _request(single, topology["id"])
    key = f"test-{uuid.uuid4().hex}"
    first = service.create_and_plan(topology["id"], key, vehicles, _options())
    second = service.create_and_plan(topology["id"], key, vehicles, _options())
    assert second["batch_id"] == first["batch_id"]
    assert [task["source_inventory"] for task in second["tasks"]] == [
        task["source_inventory"] for task in first["tasks"]
    ]


def test_shared_source_inventory_is_aggregated_before_any_reservation(multi_transfer):
    topology, repository, single, service = multi_transfer
    vehicles = _request(single, topology["id"])
    source = _unique_locations(single, topology["id"], sku_id="SKU-DEMO-001")[0]
    available = next(
        item["quantity_available"] for item in source["balances"] if item["sku_id"] == "SKU-DEMO-001"
    )
    quantity = min(100, available // 2 + 1)
    for vehicle in vehicles:
        vehicle["source_location_id"] = source["location_id"]
        vehicle["quantity"] = quantity
    before = single.get_location(topology["id"], source["location_id"])
    with pytest.raises(ShuttleDemoTransferConflict, match="Insufficient shared inventory"):
        service.create_and_plan(
            topology["id"], f"test-{uuid.uuid4().hex}", vehicles, _options()
        )
    after = single.get_location(topology["id"], source["location_id"])
    assert after["balances"] == before["balances"]
    with get_conn(repository.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM shuttle_demo_transfer_batches").fetchone()[0] == 0


def test_failed_joint_plan_releases_every_pre_pick_reservation(multi_transfer):
    topology, _, single, service = multi_transfer
    vehicles = _request(single, topology["id"])
    vehicles[1]["destination_location_id"] = vehicles[0]["destination_location_id"]
    before = {
        item["source_location_id"]: single.get_location(topology["id"], item["source_location_id"])
        for item in vehicles
    }
    batch = service.create_and_plan(
        topology["id"], f"test-{uuid.uuid4().hex}", vehicles, _options()
    )
    assert batch["status"] == "failed"
    assert all(task["status"] == "failed" for task in batch["tasks"])
    for item in vehicles:
        after = single.get_location(topology["id"], item["source_location_id"])
        assert after["balances"] == before[item["source_location_id"]]["balances"]


def test_shared_clock_applies_pick_and_drop_exactly_once(multi_transfer):
    topology, _, single, service = multi_transfer
    batch = service.create_and_plan(
        topology["id"], f"test-{uuid.uuid4().hex}", _request(single, topology["id"]), _options()
    )
    pick_end = max(
        vehicle["pickup_service_end_seconds"] for vehicle in batch["plan"]["vehicles"]
    )
    picked = service.advance(batch["batch_id"], pick_end)
    picked_again = service.advance(batch["batch_id"], pick_end)
    assert [task["ledger"] for task in picked_again["tasks"]] == [
        task["ledger"] for task in picked["tasks"]
    ]
    completed = service.advance(batch["batch_id"], batch["plan"]["metrics"]["makespan_seconds"])
    completed_again = service.advance(batch["batch_id"], batch["plan"]["metrics"]["makespan_seconds"])
    assert completed["status"] == completed_again["status"] == "completed"
    for task in completed_again["tasks"]:
        event_types = [item["event_type"] for item in task["ledger"]]
        assert event_types.count("pick") == 1
        assert event_types.count("drop") == 1
        assert task["vehicle_load_quantity"] == 0
        pick = next(item for item in task["ledger"] if item["event_type"] == "pick")
        drop = next(item for item in task["ledger"] if item["event_type"] == "drop")
        assert pick["simulation_time_seconds"] == task["pickup_at_seconds"]
        assert drop["simulation_time_seconds"] == task["dropoff_at_seconds"]


def test_cancel_before_pick_atomically_releases_all_batch_reservations(multi_transfer):
    topology, _, single, service = multi_transfer
    batch = service.create_and_plan(
        topology["id"], f"test-{uuid.uuid4().hex}", _request(single, topology["id"]), _options()
    )
    cancelled = service.cancel(batch["batch_id"])
    assert cancelled["status"] == "cancelled"
    assert all(task["status"] == "cancelled" for task in cancelled["tasks"])
    assert all(task["ledger"][-1]["event_type"] == "release" for task in cancelled["tasks"])


def test_failure_after_pick_preserves_in_transit_load(multi_transfer):
    topology, _, single, service = multi_transfer
    batch = service.create_and_plan(
        topology["id"], f"test-{uuid.uuid4().hex}", _request(single, topology["id"]), _options()
    )
    first_pick = min(
        vehicle["pickup_service_end_seconds"] for vehicle in batch["plan"]["vehicles"]
    )
    advanced = service.advance(batch["batch_id"], first_pick)
    assert any(task["vehicle_load_quantity"] > 0 for task in advanced["tasks"])
    failed = service.fail(batch["batch_id"], "simulated runtime interruption")
    assert failed["status"] == "failed"
    loaded = [task for task in failed["tasks"] if task["vehicle_load_quantity"] > 0]
    assert loaded
    assert all(task["ledger"][-1]["event_type"] == "failure_in_transit" for task in loaded)
