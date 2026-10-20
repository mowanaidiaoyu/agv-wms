from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import inventory as inventory_api
from app.core.db import get_conn, init_db, utc_now
from app.core.repository import Repository
from app.main import app
from app.services.inventory import AccessMappingValidationError, InventoryConflictError, InventoryService
from app.services.storage_access import audit_access_candidates, evaluate_access_candidate


def inventory_topology() -> dict:
    return {
        "id": "topology-inventory-test",
        "name": "Inventory Test",
        "meta": {"version": "2.0.0", "viewport": {"x": 0, "y": 0, "zoom": 1}, "tags": [], "floors": ["1F"]},
        "grid": {"rows": 6, "cols": 6, "cellSize": 32, "originX": 0, "originY": 0},
        "zones": [
            {"id": "zone-ctu", "label": "CTU 库区", "type": "ctu", "x": 2, "y": 0, "width": 1, "height": 4},
            {"id": "zone-shuttle", "label": "四向车库区", "type": "shuttle", "x": 4, "y": 0, "width": 2, "height": 4},
        ],
        "cells": [
            {"id": "cell-r1-c0", "floor": "1F", "row": 1, "col": 0, "type": "road", "zoneId": "zone-ctu", "cost": 1, "walkable": True},
            {"id": "cell-r1-c1", "floor": "1F", "row": 1, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1, "walkable": True},
            {"id": "cell-r3-c1", "floor": "1F", "row": 3, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1, "walkable": True},
            {"id": "cell-r1-c4", "floor": "1F", "row": 1, "col": 4, "type": "road", "zoneId": "zone-shuttle", "cost": 1, "walkable": True},
            {"id": "cell-r2-c1", "floor": "1F", "row": 2, "col": 1, "type": "blocked", "zoneId": "zone-ctu", "cost": 1, "walkable": False},
        ],
        "nodes": [
            {"id": "storage-a", "type": "storage", "floor": "1F", "row": 1, "col": 2, "zoneId": "zone-ctu", "capacity": 1, "label": "A"},
            {"id": "storage-b", "type": "storage", "floor": "1F", "row": 2, "col": 2, "zoneId": "zone-ctu", "capacity": 1, "label": "B"},
            {"id": "storage-c", "type": "storage", "floor": "1F", "row": 3, "col": 2, "zoneId": "zone-ctu", "capacity": 1, "label": "C"},
            {"id": "storage-shuttle", "type": "storage", "floor": "1F", "row": 1, "col": 5, "zoneId": "zone-shuttle", "capacity": 1, "label": "S"},
        ],
        "resources": [],
    }


@pytest.fixture()
def repository(tmp_path: Path) -> Repository:
    repo = Repository(tmp_path / "inventory.db")
    topology = inventory_topology()
    repo.upsert_topology(topology["id"], topology["name"], topology)
    return repo


@pytest.fixture()
def service(repository: Repository) -> InventoryService:
    return InventoryService(repository)


def insert_balance(
    repository: Repository,
    storage_node_id: str,
    sku_id: str,
    on_hand: int,
    reserved: int,
    zone_id: str = "zone-ctu",
    source: str = "synthetic_demo",
) -> None:
    now = utc_now()
    with get_conn(repository.db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sku_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sku_id, f"Name {sku_id}", "test", "spec", "piece", None, None, source, now),
        )
        conn.execute(
            """
            INSERT INTO warehouse_inventory VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            ("topology-inventory-test", zone_id, storage_node_id, sku_id, on_hand, reserved, source, now),
        )


def extended_inventory_repository(tmp_path: Path, storage_count: int = 12) -> Repository:
    topology = inventory_topology()
    topology["grid"]["rows"] = 20
    topology["grid"]["cols"] = 20
    topology["zones"][0]["width"] = 18
    topology["zones"][0]["height"] = 18
    topology["nodes"] = [
        {
            "id": f"storage-{index:02d}", "type": "storage", "floor": "1F",
            "row": 2 + index // 10, "col": 2 + index % 10,
            "zoneId": "zone-ctu", "capacity": 1, "label": f"Storage {index:02d}",
        }
        for index in range(storage_count)
    ]
    repository = Repository(tmp_path / "supplement.db")
    repository.upsert_topology(topology["id"], topology["name"], topology)
    return repository


def test_location_sku_association_and_same_sku_in_multiple_locations(repository: Repository, service: InventoryService):
    insert_balance(repository, "storage-a", "SKU-1", 20, 3)
    insert_balance(repository, "storage-b", "SKU-1", 10, 1)

    detail = service.get_location("topology-inventory-test", "storage-a")
    assert detail["balances"][0]["sku"]["sku_id"] == "SKU-1"
    assert detail["balances"][0]["quantity_available"] == 17

    distribution = service.get_sku_distribution("topology-inventory-test", "SKU-1")
    assert {item["storage_node_id"] for item in distribution["locations"]} == {"storage-a", "storage-b"}
    assert distribution["quantity_on_hand"] == 30
    assert distribution["quantity_reserved"] == 4
    assert distribution["quantity_available"] == 26


def test_zone_and_global_summary_include_empty_locations(repository: Repository, service: InventoryService):
    insert_balance(repository, "storage-a", "SKU-1", 20, 5)
    summary = service.summary("topology-inventory-test")
    assert summary == {
        "topology_id": "topology-inventory-test",
        "storage_location_count": 4,
        "stocked_location_count": 1,
        "positive_inventory_location_count": 1,
        "unconfigured_location_count": 3,
        "zero_inventory_location_count": 0,
        "sku_count": 1,
        "quantity_on_hand": 20,
        "quantity_reserved": 5,
        "quantity_available": 15,
        "data_sources": ["synthetic_demo"],
    }
    zones = {item["zone_id"]: item for item in service.zone_summaries("topology-inventory-test")}
    assert zones["zone-ctu"]["storage_location_count"] == 3
    assert zones["zone-ctu"]["quantity_available"] == 15
    assert zones["zone-ctu"]["unconfigured_location_count"] == 2
    assert zones["zone-shuttle"]["stocked_location_count"] == 0


def test_explicit_synthetic_initialization_is_deterministic_idempotent_and_persistent(repository: Repository):
    first = InventoryService(repository).seed_synthetic_demo("topology-inventory-test", seed=17)
    before = InventoryService(repository).list_locations("topology-inventory-test")
    second = InventoryService(Repository(repository.db_path)).seed_synthetic_demo("topology-inventory-test", seed=17)
    after = InventoryService(Repository(repository.db_path)).list_locations("topology-inventory-test")

    assert first["source"] == "synthetic_demo"
    assert first["inserted_balances"] == 4
    assert second["inserted_skus"] == 0
    assert second["inserted_balances"] == 0
    assert before == after
    assert all(balance["source"] == "synthetic_demo" for item in after for balance in item["balances"])


def test_database_rejects_negative_or_over_reserved_inventory(repository: Repository):
    now = utc_now()
    with get_conn(repository.db_path) as conn:
        conn.execute(
            "INSERT INTO sku_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("SKU-X", "X", "test", "spec", "piece", None, None, "synthetic_demo", now),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO warehouse_inventory VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)",
                ("topology-inventory-test", "zone-ctu", "storage-a", "SKU-X", -1, 0, "synthetic_demo", now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO warehouse_inventory VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)",
                ("topology-inventory-test", "zone-ctu", "storage-a", "SKU-X", 2, 3, "synthetic_demo", now),
            )


def test_reservation_and_idempotent_pick_contract(repository: Repository, service: InventoryService):
    insert_balance(repository, "storage-a", "SKU-1", 10, 0)
    service.reserve_inventory("topology-inventory-test", "storage-a", 1, "SKU-1", 4)
    assert service.confirm_pick("pick-event-1", "topology-inventory-test", "storage-a", 1, "SKU-1", 3)
    assert not service.confirm_pick("pick-event-1", "topology-inventory-test", "storage-a", 1, "SKU-1", 3)
    balance = service.get_location("topology-inventory-test", "storage-a")["balances"][0]
    assert (balance["quantity_on_hand"], balance["quantity_reserved"], balance["quantity_available"]) == (7, 1, 6)
    with pytest.raises(InventoryConflictError):
        service.reserve_inventory("topology-inventory-test", "storage-a", 1, "SKU-1", 7)


def test_empty_and_missing_storage_locations(service: InventoryService):
    assert service.get_location("topology-inventory-test", "storage-b")["balances"] == []
    with pytest.raises(Exception, match="not found"):
        service.get_location("topology-inventory-test", "missing-storage")


def test_access_mapping_requires_real_adjacent_connected_ctu_road(service: InventoryService):
    mapping = service.configure_access_mapping(
        "topology-inventory-test", "storage-a", "cell-r1-c1", "manual_verified", reviewed=True
    )
    assert mapping["status"] == "configured"
    assert mapping["access_cell_id"] == "cell-r1-c1"

    with pytest.raises(AccessMappingValidationError):
        service.configure_access_mapping("topology-inventory-test", "storage-b", "missing-cell", "manual")
    with pytest.raises(AccessMappingValidationError):
        service.configure_access_mapping("topology-inventory-test", "storage-b", "cell-r2-c1", "manual")
    with pytest.raises(AccessMappingValidationError):
        service.configure_access_mapping("topology-inventory-test", "storage-c", "cell-r3-c1", "manual")
    with pytest.raises(AccessMappingValidationError):
        service.configure_access_mapping("topology-inventory-test", "storage-shuttle", "cell-r1-c4", "manual")


def test_inventory_api_queries_use_isolated_database(monkeypatch: pytest.MonkeyPatch, repository: Repository):
    monkeypatch.setattr(inventory_api, "repo", repository)
    client = TestClient(app)

    seeded = client.post(
        "/api/v1/inventory/synthetic-demo",
        json={"topology_id": "topology-inventory-test", "seed": 23},
    )
    assert seeded.status_code == 200
    assert seeded.json()["source"] == "synthetic_demo"

    summary = client.get("/api/v1/inventory/summary", params={"topology_id": "topology-inventory-test"})
    assert summary.status_code == 200
    assert summary.json()["storage_location_count"] == 4

    zones = client.get("/api/v1/inventory/zones", params={"topology_id": "topology-inventory-test"})
    assert zones.status_code == 200
    assert {item["zone_id"] for item in zones.json()} == {"zone-ctu", "zone-shuttle"}

    locations = client.get("/api/v1/inventory/locations", params={"topology_id": "topology-inventory-test"})
    assert locations.status_code == 200
    assert len(locations.json()) == 4

    detail = client.get(
        "/api/v1/inventory/locations/storage-a",
        params={"topology_id": "topology-inventory-test"},
    )
    assert detail.status_code == 200
    assert detail.json()["storage_node_id"] == "storage-a"
    assert detail.json()["access_mapping"]["status"] == "unconfigured"

    candidates = client.get(
        "/api/v1/inventory/access-mappings/storage-a/candidates",
        params={"topology_id": "topology-inventory-test"},
    )
    assert candidates.status_code == 200
    assert candidates.json()["eligible_count"] == 1
    assert candidates.json()["candidates"][0]["access_cell_id"] == "cell-r1-c1"

    audit = client.get(
        "/api/v1/inventory/access-mappings/audit",
        params={"topology_id": "topology-inventory-test"},
    )
    assert audit.status_code == 200
    assert audit.json()["eligible_location_count"] == 1

    unreviewed = client.put(
        "/api/v1/inventory/access-mappings/storage-a",
        json={"topology_id": "topology-inventory-test", "access_cell_id": "cell-r1-c1", "source": "manual_verified"},
    )
    assert unreviewed.status_code == 409

    mapped = client.put(
        "/api/v1/inventory/access-mappings/storage-a",
        json={"topology_id": "topology-inventory-test", "access_cell_id": "cell-r1-c1", "source": "manual_verified", "reviewed": True},
    )
    assert mapped.status_code == 200
    assert mapped.json()["status"] == "configured"

    path_preview = client.post(
        "/api/v1/inventory/access-path/plan",
        json={
            "topology_id": "topology-inventory-test", "storage_node_id": "storage-a",
            "start_cell_id": "cell-r1-c0", "algorithm": "astar",
        },
    )
    assert path_preview.status_code == 200
    assert path_preview.json()["access_cell_id"] == "cell-r1-c1"
    assert all(cell["type"] == "road" for cell in path_preview.json()["path_cells"])

    missing = client.get(
        "/api/v1/inventory/locations/missing",
        params={"topology_id": "topology-inventory-test"},
    )
    assert missing.status_code == 404

    sku_id = next(item for item in locations.json() if item["balances"])["balances"][0]["sku"]["sku_id"]
    sku = client.get(f"/api/v1/inventory/sku/{sku_id}", params={"topology_id": "topology-inventory-test"})
    assert sku.status_code == 200
    assert sku.json()["sku"]["source"] == "synthetic_demo"


def test_init_db_does_not_seed_or_reset_inventory(tmp_path: Path):
    database = tmp_path / "no-auto-seed.db"
    init_db(database)
    with get_conn(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM warehouse_inventory").fetchone()[0] == 0
    init_db(database)
    with get_conn(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM warehouse_inventory").fetchone()[0] == 0


def test_real_666_demo_seed_only_references_existing_storage_nodes(tmp_path: Path):
    topology_path = Path(__file__).resolve().parents[2] / "docs" / "topologies" / "666.json"
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    repository = Repository(tmp_path / "real-666-inventory.db")
    repository.upsert_topology(topology["id"], topology["name"], topology)

    result = InventoryService(repository).seed_synthetic_demo(topology["id"], seed=42)
    locations = InventoryService(repository).list_locations(topology["id"])
    real_storage_ids = {node["id"] for node in topology["nodes"] if node["type"] == "storage"}
    seeded_ids = {item["storage_node_id"] for item in locations if item["balances"]}

    assert result["inserted_balances"] == 32
    assert seeded_ids
    assert seeded_ids <= real_storage_ids
    assert all(item["access_mapping"]["status"] == "unconfigured" for item in locations if item["balances"])


def test_supplement_adds_requested_missing_locations_without_overwriting_existing(tmp_path: Path):
    repository = extended_inventory_repository(tmp_path)
    service = InventoryService(repository)
    insert_balance(repository, "storage-00", "SKU-REAL", 50, 11, source="wms_import")

    preview = service.preview_synthetic_supplement("topology-inventory-test", count=3)
    assert preview["eligible_location_count"] == 11
    assert preview["estimated_insert_count"] == 3

    result = service.supplement_synthetic_demo("topology-inventory-test", count=3, seed=9)
    assert result["inserted_locations"] == 3
    assert result["after"]["stocked_location_count"] == 4
    original = service.get_location("topology-inventory-test", "storage-00")["balances"][0]
    assert (original["quantity_on_hand"], original["quantity_reserved"], original["source"]) == (50, 11, "wms_import")
    assert all(
        balance["source"] == "synthetic_demo"
        for location in service.list_locations("topology-inventory-test")
        if location["storage_node_id"] != "storage-00"
        for balance in location["balances"]
    )


def test_fill_all_supplement_is_repeatable_without_duplicate_records(tmp_path: Path):
    repository = extended_inventory_repository(tmp_path, storage_count=10)
    service = InventoryService(repository)
    first = service.supplement_synthetic_demo("topology-inventory-test", fill_all=True, seed=42)
    balances_after_first = service.list_locations("topology-inventory-test")
    second = service.supplement_synthetic_demo("topology-inventory-test", fill_all=True, seed=42)

    assert first["inserted_locations"] == 10
    assert first["after"]["unconfigured_location_count"] == 0
    assert second["inserted_locations"] == 0
    assert second["after"] == first["after"]
    assert service.list_locations("topology-inventory-test") == balances_after_first


def test_inventory_summary_distinguishes_unconfigured_zero_and_positive_locations(tmp_path: Path):
    repository = extended_inventory_repository(tmp_path, storage_count=3)
    service = InventoryService(repository)
    insert_balance(repository, "storage-00", "SKU-ZERO", 0, 0)
    insert_balance(repository, "storage-01", "SKU-POSITIVE", 5, 2)

    summary = service.summary("topology-inventory-test")
    assert summary["stocked_location_count"] == 2
    assert summary["positive_inventory_location_count"] == 1
    assert summary["zero_inventory_location_count"] == 1
    assert summary["unconfigured_location_count"] == 1


def test_supplement_api_previews_and_fills_only_real_ctu_storage_nodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    repository = extended_inventory_repository(tmp_path, storage_count=6)
    monkeypatch.setattr(inventory_api, "repo", repository)
    client = TestClient(app)

    preview = client.get(
        "/api/v1/inventory/synthetic-demo/supplement/preview",
        params={"topology_id": "topology-inventory-test", "count": 2},
    )
    assert preview.status_code == 200
    assert preview.json()["eligible_location_count"] == 6
    assert preview.json()["estimated_insert_count"] == 2

    first = client.post(
        "/api/v1/inventory/synthetic-demo/supplement",
        json={"topology_id": "topology-inventory-test", "count": 2, "fill_all": False, "seed": 5},
    )
    assert first.status_code == 200
    assert first.json()["inserted_locations"] == 2
    assert first.json()["after"]["stocked_location_count"] == 2

    remaining = client.post(
        "/api/v1/inventory/synthetic-demo/supplement",
        json={"topology_id": "topology-inventory-test", "fill_all": True, "seed": 5},
    )
    assert remaining.status_code == 200
    assert remaining.json()["inserted_locations"] == 4
    assert remaining.json()["after"]["unconfigured_location_count"] == 0
    assert all(
        location["storage_node_id"].startswith("storage-")
        for location in InventoryService(repository).list_locations("topology-inventory-test")
        if location["balances"]
    )

    invalid = client.post(
        "/api/v1/inventory/synthetic-demo/supplement",
        json={"topology_id": "topology-inventory-test", "count": 1, "fill_all": True},
    )
    assert invalid.status_code == 409


def test_access_candidate_review_is_deterministic_and_does_not_change_inventory(repository: Repository):
    service = InventoryService(repository)
    insert_balance(repository, "storage-a", "SKU-REAL", 20, 6, source="wms_import")
    before = service.get_location("topology-inventory-test", "storage-a")["balances"]

    first = service.get_access_candidates("topology-inventory-test", "storage-a")
    second = service.get_access_candidates("topology-inventory-test", "storage-a")
    assert first == second
    assert first["eligible_count"] == 1
    assert first["candidates"][0]["access_cell_id"] == "cell-r1-c1"
    assert first["candidates"][0]["status"] == "eligible"

    with pytest.raises(AccessMappingValidationError, match="explicit human review"):
        service.configure_access_mapping(
            "topology-inventory-test", "storage-a", "cell-r1-c1", "manual_verified"
        )
    service.configure_access_mapping(
        "topology-inventory-test", "storage-a", "cell-r1-c1", "manual_verified", reviewed=True
    )
    after = service.get_location("topology-inventory-test", "storage-a")["balances"]
    assert after == before


def test_access_candidate_rejects_blocked_cells_and_roads_across_racks():
    topology = inventory_topology()
    blocked = evaluate_access_candidate(topology, "storage-b", "cell-r2-c1")
    assert blocked["status"] == "rejected"
    assert "ACCESS_CELL_NOT_ROAD" in blocked["reason_codes"]

    topology["cells"].extend([
        {"id": "cell-r1-c3", "floor": "1F", "row": 1, "col": 3, "type": "road", "zoneId": "zone-ctu", "cost": 1, "walkable": True},
        {"id": "cell-r1-c4", "floor": "1F", "row": 1, "col": 4, "type": "road", "zoneId": "zone-ctu", "cost": 1, "walkable": True},
    ])
    across_rack = evaluate_access_candidate(topology, "storage-a", "cell-r1-c4")
    assert across_rack["status"] == "rejected"
    assert "ACCESS_NOT_ON_PICK_FACE" in across_rack["reason_codes"]


def test_unconfigured_storage_cannot_plan_pick_path_and_reviewed_mapping_uses_road_only(repository: Repository):
    service = InventoryService(repository)
    with pytest.raises(InventoryConflictError, match="no reviewed access mapping"):
        service.plan_storage_access_path(
            "topology-inventory-test", "storage-a", "cell-r1-c0", "astar"
        )

    service.configure_access_mapping(
        "topology-inventory-test", "storage-a", "cell-r1-c1", "manual_verified", reviewed=True
    )
    for algorithm in ("astar", "dijkstra"):
        result = service.plan_storage_access_path(
            "topology-inventory-test", "storage-a", "cell-r1-c0", algorithm
        )
        assert result["access_cell_id"] == "cell-r1-c1"
        assert result["path_cells"][-1]["id"] == "cell-r1-c1"
        assert all(cell["type"] == "road" for cell in result["path_cells"])
        assert all(cell["id"] != "storage-a" for cell in result["path_cells"])
        assert result["execution_status"] == "planning_preview_only"


def test_real_666_has_no_provable_or_reviewable_access_candidate():
    topology_path = Path(__file__).resolve().parents[2] / "docs" / "topologies" / "666.json"
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    audit = audit_access_candidates(topology)

    assert audit["storage_location_count"] == 2496
    assert audit["eligible_location_count"] == 0
    assert audit["review_required_location_count"] == 0
    assert audit["unmappable_location_count"] == 2496
    assert audit["reason_counts"]["NO_ADJACENT_REAL_ROAD"] == 2495
