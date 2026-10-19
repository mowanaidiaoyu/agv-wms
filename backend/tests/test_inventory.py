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


def inventory_topology() -> dict:
    return {
        "id": "topology-inventory-test",
        "name": "Inventory Test",
        "meta": {"version": "2.0.0", "viewport": {"x": 0, "y": 0, "zoom": 1}, "tags": [], "floors": ["1F"]},
        "grid": {"rows": 6, "cols": 6, "cellSize": 32, "originX": 0, "originY": 0},
        "zones": [
            {"id": "zone-ctu", "label": "CTU 库区", "type": "ctu", "x": 0, "y": 0, "width": 4, "height": 4},
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
) -> None:
    now = utc_now()
    with get_conn(repository.db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sku_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sku_id, f"Name {sku_id}", "test", "spec", "piece", None, None, "synthetic_demo", now),
        )
        conn.execute(
            """
            INSERT INTO warehouse_inventory VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            ("topology-inventory-test", zone_id, storage_node_id, sku_id, on_hand, reserved, "synthetic_demo", now),
        )


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
        "sku_count": 1,
        "quantity_on_hand": 20,
        "quantity_reserved": 5,
        "quantity_available": 15,
        "data_sources": ["synthetic_demo"],
    }
    zones = {item["zone_id"]: item for item in service.zone_summaries("topology-inventory-test")}
    assert zones["zone-ctu"]["storage_location_count"] == 3
    assert zones["zone-ctu"]["quantity_available"] == 15
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
        "topology-inventory-test", "storage-a", "cell-r1-c1", "manual_verified"
    )
    assert mapping["status"] == "configured"
    assert mapping["access_cell_id"] == "cell-r1-c1"

    with pytest.raises(AccessMappingValidationError, match="does not exist"):
        service.configure_access_mapping("topology-inventory-test", "storage-b", "missing-cell", "manual")
    with pytest.raises(AccessMappingValidationError, match="walkable road"):
        service.configure_access_mapping("topology-inventory-test", "storage-b", "cell-r2-c1", "manual")
    with pytest.raises(AccessMappingValidationError, match="not connected"):
        service.configure_access_mapping("topology-inventory-test", "storage-c", "cell-r3-c1", "manual")
    with pytest.raises(AccessMappingValidationError, match="CTU zone"):
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

    mapped = client.put(
        "/api/v1/inventory/access-mappings/storage-a",
        json={"topology_id": "topology-inventory-test", "access_cell_id": "cell-r1-c1", "source": "manual_verified"},
    )
    assert mapped.status_code == 200
    assert mapped.json()["status"] == "configured"

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
