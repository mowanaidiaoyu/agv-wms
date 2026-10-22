from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import shuttle as shuttle_api
from app.core.db import get_conn
from app.core.repository import Repository
from app.main import app
from app.services.shuttle_demo_transfer import (
    DEMO_SEED,
    ShuttleDemoTransferConflict,
    ShuttleDemoTransferService,
)


ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY_PATH = ROOT / "docs" / "topologies" / "666.json"


@pytest.fixture(scope="module")
def transfer_context(tmp_path_factory: pytest.TempPathFactory):
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8-sig"))
    repository = Repository(tmp_path_factory.mktemp("shuttle-transfer") / "transfer.db")
    repository.upsert_topology(topology["id"], topology["name"], topology)
    service = ShuttleDemoTransferService(repository)
    initialized = service.initialize_inventory(topology["id"])
    return topology, repository, service, initialized


def _locations(service: ShuttleDemoTransferService, topology_id: str, level=None, sku_id=None):
    return service.list_locations(
        topology_id, level=level, sku_id=sku_id, transfer_eligible=True, limit=5000
    )["locations"]


def _pair(service: ShuttleDemoTransferService, topology_id: str, source_level=1, destination_level=4):
    sources = _locations(service, topology_id, source_level, "SKU-DEMO-001")
    destinations = _locations(service, topology_id, destination_level)
    assert sources and destinations
    source = sources[0]
    destination = next(item for item in destinations if item["location_id"] != source["location_id"])
    return source, destination


def _task(service: ShuttleDemoTransferService, topology_id: str, *, source_level=1, destination_level=4, algorithm="astar", quantity=1):
    source, destination = _pair(service, topology_id, source_level, destination_level)
    task = service.create_task(
        topology_id, source["location_id"], destination["location_id"], "SKU-DEMO-001",
        quantity, algorithm, "SHUTTLE-01", source["access_track_node_id"],
    )
    return source, destination, task


def test_real_666_initializes_all_stable_candidate_identities(transfer_context):
    _, _, _, initialized = transfer_context
    assert initialized["seed"] == DEMO_SEED
    assert initialized["candidate_location_count"] == 4238
    assert initialized["eligible_location_count"] == 2204


def test_initialization_creates_one_to_three_skus_per_location(transfer_context):
    topology, repository, _, _ = transfer_context
    with get_conn(repository.db_path) as conn:
        rows = conn.execute(
            "SELECT location_id, COUNT(*) AS n FROM shuttle_demo_inventory WHERE topology_id=? GROUP BY location_id",
            (topology["id"],),
        ).fetchall()
    assert len(rows) == 4238
    assert {row["n"] for row in rows}.issubset({1, 2, 3})


def test_initial_quantities_are_in_contract_range(transfer_context):
    topology, repository, _, _ = transfer_context
    with get_conn(repository.db_path) as conn:
        minimum, maximum = conn.execute(
            "SELECT MIN(quantity_on_hand), MAX(quantity_on_hand) FROM shuttle_demo_inventory WHERE topology_id=?",
            (topology["id"],),
        ).fetchone()
    assert 10 <= minimum <= maximum <= 100


@pytest.mark.parametrize("location_id", [
    "shuttle-location:zone-shuttle-10:L1:r114:c17",
    "shuttle-location:zone-shuttle-10:L2:r114:c17",
    "shuttle-location:zone-shuttle-10:L4:r114:c17",
])
def test_stable_sha_inventory_generation_is_deterministic(location_id):
    first = ShuttleDemoTransferService._stable_stock(location_id)
    second = ShuttleDemoTransferService._stable_stock(location_id)
    assert first == second
    assert 1 <= len(first) <= 3


def test_initialization_is_idempotent(transfer_context):
    topology, _, service, initialized = transfer_context
    repeated = service.initialize_inventory(topology["id"])
    assert repeated["inserted_balance_count"] == 0
    assert repeated["existing_balance_count"] == initialized["inserted_balance_count"]


def test_sku_catalog_has_eight_demo_skus_and_totals(transfer_context):
    topology, _, service, _ = transfer_context
    skus = service.list_skus(topology["id"])
    assert len(skus) == 8
    assert all(item["quantity_available"] == item["quantity_on_hand"] - item["quantity_reserved"] for item in skus)


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_level_filter_returns_only_requested_level(transfer_context, level):
    topology, _, service, _ = transfer_context
    page = service.list_locations(topology["id"], level=level, limit=5000)
    assert page["locations"]
    assert all(item["level"] == level for item in page["locations"])


def test_sku_filter_returns_positive_available_balances(transfer_context):
    topology, _, service, _ = transfer_context
    locations = _locations(service, topology["id"], sku_id="SKU-DEMO-003")
    assert locations
    assert all(
        next(item for item in location["balances"] if item["sku_id"] == "SKU-DEMO-003")["quantity_available"] > 0
        for location in locations
    )


def test_single_location_returns_all_skus(transfer_context):
    topology, _, service, _ = transfer_context
    location = service.list_locations(topology["id"], limit=1)["locations"][0]
    detail = service.get_location(topology["id"], location["location_id"])
    assert detail["balances"] == location["balances"]


def test_inventory_only_location_is_not_transfer_eligible(transfer_context):
    topology, _, service, _ = transfer_context
    item = service.list_locations(
        topology["id"], transfer_eligible=False, limit=1
    )["locations"][0]
    assert item["transfer_eligible"] is False
    assert item["access_track_node_id"] is None


def test_demo_eligibility_keeps_physical_review_unverified(transfer_context):
    topology, _, service, _ = transfer_context
    item = _locations(service, topology["id"])[0]
    assert item["mapping_source"] == "synthetic_demo_adjacency_assumption"
    assert item["physical_review_status"] == "unverified"
    assert "real_shuttle_track" in item["eligibility_reason_codes"]


def test_same_source_and_destination_is_rejected(transfer_context):
    topology, _, service, _ = transfer_context
    source, _ = _pair(service, topology["id"])
    with pytest.raises(ShuttleDemoTransferConflict, match="different"):
        service.create_task(topology["id"], source["location_id"], source["location_id"], "SKU-DEMO-001", 1, "astar", "S", None)


def test_insufficient_source_inventory_is_rejected(transfer_context):
    topology, _, service, _ = transfer_context
    source, destination = _pair(service, topology["id"])
    available = next(item for item in source["balances"] if item["sku_id"] == "SKU-DEMO-001")["quantity_available"]
    with pytest.raises(ShuttleDemoTransferConflict, match="insufficient"):
        service.create_task(topology["id"], source["location_id"], destination["location_id"], "SKU-DEMO-001", available + 1, "astar", "S", None)


def test_inventory_only_source_is_rejected(transfer_context):
    topology, _, service, _ = transfer_context
    source = service.list_locations(topology["id"], transfer_eligible=False, limit=1)["locations"][0]
    destination = _locations(service, topology["id"])[0]
    sku = source["balances"][0]["sku_id"]
    with pytest.raises(ShuttleDemoTransferConflict, match="inventory-only"):
        service.create_task(topology["id"], source["location_id"], destination["location_id"], sku, 1, "astar", "S", None)


@pytest.mark.parametrize("algorithm", ["astar", "dijkstra"])
def test_same_level_transfer_uses_real_shuttle_planner(transfer_context, algorithm):
    topology, _, service, _ = transfer_context
    _, _, task = _task(service, topology["id"], source_level=1, destination_level=1, algorithm=algorithm)
    planned = service.plan_and_start(task["task_id"])
    assert planned["plan"]["result_status"] == "success"
    assert planned["plan"]["algorithm"] == algorithm
    assert all(edge["edge_type"] == "track" for edge in planned["plan"]["path_edges"])


@pytest.mark.parametrize(("source_level", "destination_level"), [(1, 4), (4, 1)])
def test_cross_level_transfer_uses_verified_lift(transfer_context, source_level, destination_level):
    topology, _, service, _ = transfer_context
    _, _, task = _task(service, topology["id"], source_level=source_level, destination_level=destination_level)
    planned = service.plan_and_start(task["task_id"])
    assert any(edge["edge_type"] == "lift" for edge in planned["plan"]["path_edges"])
    assert planned["plan"]["chosen_lift"] in {"shuttle-lift-a", "shuttle-lift-b"}


def test_create_task_atomically_reserves_without_deducting_on_hand(transfer_context):
    topology, _, service, _ = transfer_context
    source, _, task = _task(service, topology["id"], quantity=2)
    before = next(item for item in source["balances"] if item["sku_id"] == "SKU-DEMO-001")
    assert task["source_inventory"]["quantity_on_hand"] == before["quantity_on_hand"]
    assert task["source_inventory"]["quantity_reserved"] == before["quantity_reserved"] + 2
    assert [item["event_type"] for item in task["ledger"]] == ["reserve"]


def test_planning_does_not_deduct_inventory(transfer_context):
    topology, _, service, _ = transfer_context
    _, _, task = _task(service, topology["id"])
    before = task["source_inventory"]["quantity_on_hand"]
    planned = service.plan_and_start(task["task_id"])
    assert planned["source_inventory"]["quantity_on_hand"] == before
    assert planned["status"] == "moving_to_pickup"


def test_pick_and_drop_are_each_exactly_once(transfer_context):
    topology, _, service, _ = transfer_context
    _, _, task = _task(service, topology["id"], quantity=3)
    planned = service.plan_and_start(task["task_id"])
    picked = service.advance(task["task_id"], planned["pickup_at_seconds"])
    picked_again = service.advance(task["task_id"], planned["pickup_at_seconds"])
    delivered = service.advance(task["task_id"], planned["dropoff_at_seconds"])
    delivered_again = service.advance(task["task_id"], planned["dropoff_at_seconds"])
    assert picked["source_inventory"] == picked_again["source_inventory"]
    assert delivered["destination_inventory"] == delivered_again["destination_inventory"]
    assert [item["event_type"] for item in delivered["ledger"]].count("pick") == 1
    assert [item["event_type"] for item in delivered["ledger"]].count("drop") == 1


def test_cancel_before_pick_releases_reservation(transfer_context):
    topology, _, service, _ = transfer_context
    _, _, task = _task(service, topology["id"], quantity=2)
    cancelled = service.cancel(task["task_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["source_inventory"]["quantity_reserved"] == task["source_inventory"]["quantity_reserved"] - 2
    assert [item["event_type"] for item in cancelled["ledger"]][-1] == "release"


def test_failure_before_pick_releases_reservation(transfer_context):
    topology, _, service, _ = transfer_context
    _, _, task = _task(service, topology["id"])
    failed = service.fail(task["task_id"], "test pre-pick failure")
    assert failed["status"] == "failed"
    assert failed["vehicle_load_quantity"] == 0
    assert failed["ledger"][-1]["event_type"] == "release"


def test_failure_after_pick_keeps_in_transit_goods(transfer_context):
    topology, _, service, _ = transfer_context
    _, _, task = _task(service, topology["id"], quantity=2)
    planned = service.plan_and_start(task["task_id"])
    service.advance(task["task_id"], planned["pickup_at_seconds"])
    failed = service.fail(task["task_id"], "test post-pick failure")
    assert failed["status"] == "failed"
    assert failed["vehicle_load_quantity"] == 2
    assert failed["ledger"][-1]["event_type"] == "failure_in_transit"


def test_transfer_conserves_source_destination_and_vehicle_load(transfer_context):
    topology, _, service, _ = transfer_context
    _, _, task = _task(service, topology["id"], quantity=4)
    initial_total = task["source_inventory"]["quantity_on_hand"] + task["destination_inventory"]["quantity_on_hand"]
    planned = service.plan_and_start(task["task_id"])
    picked = service.advance(task["task_id"], planned["pickup_at_seconds"])
    delivered = service.advance(task["task_id"], planned["dropoff_at_seconds"])
    assert picked["source_inventory"]["quantity_on_hand"] + picked["destination_inventory"]["quantity_on_hand"] + picked["vehicle_load_quantity"] == initial_total
    assert delivered["source_inventory"]["quantity_on_hand"] + delivered["destination_inventory"]["quantity_on_hand"] == initial_total


def test_reading_task_history_has_no_inventory_side_effect(transfer_context):
    topology, _, service, _ = transfer_context
    _, _, task = _task(service, topology["id"])
    first = service.get_task(task["task_id"])
    second = service.get_task(task["task_id"])
    assert first["source_inventory"] == second["source_inventory"]
    assert first["ledger"] == second["ledger"]


def test_reinitialize_does_not_overwrite_delivered_inventory(transfer_context):
    topology, _, service, _ = transfer_context
    _, _, task = _task(service, topology["id"], quantity=2)
    planned = service.plan_and_start(task["task_id"])
    delivered = service.advance(task["task_id"], planned["dropoff_at_seconds"])
    service.initialize_inventory(topology["id"])
    reread = service.get_task(task["task_id"])
    assert reread["source_inventory"] == delivered["source_inventory"]
    assert reread["destination_inventory"] == delivered["destination_inventory"]


def test_ctu_inventory_table_is_untouched(transfer_context):
    topology, repository, service, _ = transfer_context
    with get_conn(repository.db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM warehouse_inventory").fetchone()[0]
    service.initialize_inventory(topology["id"])
    with get_conn(repository.db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM warehouse_inventory").fetchone()[0]
    assert before == after == 0


def test_competing_reservations_are_serialized(transfer_context):
    topology, repository, service, _ = transfer_context
    source, destination = _pair(service, topology["id"])
    available = next(item for item in source["balances"] if item["sku_id"] == "SKU-DEMO-001")["quantity_available"]
    quantity = available // 2 + 1
    def reserve():
        local = ShuttleDemoTransferService(Repository(repository.db_path))
        try:
            return local.create_task(topology["id"], source["location_id"], destination["location_id"], "SKU-DEMO-001", quantity, "astar", "S", source["access_track_node_id"])["status"]
        except ShuttleDemoTransferConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(results) == ["conflict", "reserved"]


def test_api_exposes_inventory_task_and_ledger_on_isolated_database(transfer_context, monkeypatch):
    topology, repository, service, _ = transfer_context
    monkeypatch.setattr(shuttle_api, "repo", repository)
    client = TestClient(app)
    skus = client.get(f"/api/v1/shuttle/topologies/{topology['id']}/demo-inventory/skus")
    assert skus.status_code == 200
    source, destination = _pair(service, topology["id"])
    created = client.post("/api/v1/shuttle/demo-transfers", json={
        "topology_id": topology["id"], "source_location_id": source["location_id"],
        "destination_location_id": destination["location_id"], "sku_id": "SKU-DEMO-001",
        "quantity": 1, "algorithm": "astar", "vehicle_id": "SHUTTLE-01",
        "current_track_node_id": source["access_track_node_id"],
    })
    assert created.status_code == 200
    task_id = created.json()["task_id"]
    planned = client.post(f"/api/v1/shuttle/demo-transfers/{task_id}/plan-start")
    assert planned.status_code == 200
    advanced = client.post(f"/api/v1/shuttle/demo-transfers/{task_id}/advance", json={"elapsed_seconds": planned.json()["dropoff_at_seconds"]})
    assert advanced.json()["status"] == "delivered"
    ledger = client.get(f"/api/v1/shuttle/demo-transfers/{task_id}/ledger")
    assert [item["event_type"] for item in ledger.json()] == ["reserve", "pick", "drop"]
