from __future__ import annotations

import random
import sqlite3
from collections import deque
from pathlib import Path
from typing import Any

from ..core.db import get_conn, utc_now
from ..core.repository import Repository


SYNTHETIC_DEMO = "synthetic_demo"

DEMO_SKUS: tuple[dict[str, Any], ...] = (
    {"sku_id": "SKU-DEMO-001", "sku_name": "标准紧固件套装", "category": "fastener", "specification": "M6 assorted", "unit": "box", "weight_kg": 2.4, "material": "steel"},
    {"sku_id": "SKU-DEMO-002", "sku_name": "工业轴承", "category": "bearing", "specification": "6204-2RS", "unit": "piece", "weight_kg": 0.11, "material": "bearing steel"},
    {"sku_id": "SKU-DEMO-003", "sku_name": "液压接头", "category": "hydraulic", "specification": "G1/2", "unit": "piece", "weight_kg": 0.18, "material": "carbon steel"},
    {"sku_id": "SKU-DEMO-004", "sku_name": "接近传感器", "category": "electrical", "specification": "M18 PNP", "unit": "piece", "weight_kg": 0.09, "material": None},
    {"sku_id": "SKU-DEMO-005", "sku_name": "控制面板组件", "category": "panel", "specification": "24V compact", "unit": "set", "weight_kg": 1.8, "material": None},
    {"sku_id": "SKU-DEMO-006", "sku_name": "同步带", "category": "drive", "specification": "HTD-5M-600", "unit": "piece", "weight_kg": 0.32, "material": "rubber"},
    {"sku_id": "SKU-DEMO-007", "sku_name": "导轨滑块", "category": "linear_motion", "specification": "HGH20", "unit": "piece", "weight_kg": 0.65, "material": "steel"},
    {"sku_id": "SKU-DEMO-008", "sku_name": "电缆组件", "category": "electrical", "specification": "5m shielded", "unit": "piece", "weight_kg": 0.72, "material": "copper/PVC"},
)


class InventoryError(ValueError):
    pass


class InventoryNotFoundError(InventoryError):
    pass


class InventoryConflictError(InventoryError):
    pass


class AccessMappingValidationError(InventoryError):
    pass


class InventoryService:
    """Inventory application service backed by the project's SQLite database.

    Reads are side-effect free. Demo data is inserted only through the explicit
    ``seed_synthetic_demo`` operation and never during application startup.
    """

    def __init__(self, repository: Repository):
        self.repository = repository
        self.db_path = Path(repository.db_path)

    def _topology_record(self, topology_id: str) -> dict[str, Any]:
        record = self.repository.get_topology(topology_id)
        if not record:
            raise InventoryNotFoundError(f"Topology {topology_id} not found")
        return record

    @staticmethod
    def _topology_payload(record: dict[str, Any]) -> dict[str, Any]:
        return record["payload"]

    @staticmethod
    def _storage_nodes(topology: dict[str, Any]) -> list[dict[str, Any]]:
        return [node for node in topology.get("nodes", []) if node.get("type") == "storage"]

    @staticmethod
    def _zone_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {zone["id"]: zone for zone in topology.get("zones", [])}

    def seed_synthetic_demo(self, topology_id: str, seed: int = 42) -> dict[str, Any]:
        topology = self._topology_payload(self._topology_record(topology_id))
        storage_nodes = sorted(self._storage_nodes(topology), key=lambda item: item["id"])
        if not storage_nodes:
            raise InventoryConflictError("Topology has no storage nodes")

        rng = random.Random(seed)
        selected = list(storage_nodes)
        rng.shuffle(selected)
        selected = selected[: min(32, len(selected))]
        now = utc_now()
        inserted_skus = 0
        inserted_balances = 0
        with get_conn(self.db_path) as conn:
            for sku in DEMO_SKUS:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO sku_catalog (
                      sku_id, sku_name, category, specification, unit,
                      weight_kg, material, source, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sku["sku_id"], sku["sku_name"], sku["category"], sku["specification"],
                        sku["unit"], sku["weight_kg"], sku["material"], SYNTHETIC_DEMO, now,
                    ),
                )
                inserted_skus += cursor.rowcount

            for index, node in enumerate(selected):
                sku = DEMO_SKUS[index % len(DEMO_SKUS)]
                on_hand = 12 + rng.randrange(0, 89)
                reserved = rng.randrange(0, min(8, on_hand) + 1)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO warehouse_inventory (
                      topology_id, zone_id, storage_node_id, storage_layer, sku_id,
                      quantity_on_hand, quantity_reserved, source, updated_at
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        topology_id, node.get("zoneId"), node["id"], sku["sku_id"],
                        on_hand, reserved, SYNTHETIC_DEMO, now,
                    ),
                )
                inserted_balances += cursor.rowcount

        return {
            "topology_id": topology_id,
            "seed": seed,
            "inserted_skus": inserted_skus,
            "inserted_balances": inserted_balances,
            "source": SYNTHETIC_DEMO,
        }

    def list_locations(
        self,
        topology_id: str,
        *,
        zone_id: str | None = None,
        sku_id: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        topology = self._topology_payload(self._topology_record(topology_id))
        nodes = self._storage_nodes(topology)
        if zone_id:
            nodes = [node for node in nodes if node.get("zoneId") == zone_id]
        if search:
            term = search.casefold()
            nodes = [node for node in nodes if term in node["id"].casefold() or term in str(node.get("label", "")).casefold()]

        rows_by_node = self._inventory_rows(topology_id, sku_id=sku_id)
        if sku_id:
            nodes = [node for node in nodes if node["id"] in rows_by_node]
        mappings = self._mapping_rows(topology_id)
        zones = self._zone_lookup(topology)
        return [self._location_payload(topology, node, zones, rows_by_node.get(node["id"], []), mappings.get(node["id"])) for node in nodes]

    def get_location(self, topology_id: str, storage_node_id: str) -> dict[str, Any]:
        topology = self._topology_payload(self._topology_record(topology_id))
        node = next((item for item in self._storage_nodes(topology) if item["id"] == storage_node_id), None)
        if not node:
            raise InventoryNotFoundError(f"Storage node {storage_node_id} not found in topology {topology_id}")
        rows = self._inventory_rows(topology_id, storage_node_id=storage_node_id).get(storage_node_id, [])
        mapping = self._mapping_rows(topology_id).get(storage_node_id)
        return self._location_payload(topology, node, self._zone_lookup(topology), rows, mapping)

    def get_sku_distribution(self, topology_id: str, sku_id: str) -> dict[str, Any]:
        self._topology_record(topology_id)
        with get_conn(self.db_path) as conn:
            sku_row = conn.execute("SELECT * FROM sku_catalog WHERE sku_id = ?", (sku_id,)).fetchone()
        if not sku_row:
            raise InventoryNotFoundError(f"SKU {sku_id} not found")
        locations = self.list_locations(topology_id, sku_id=sku_id)
        totals = self._sum_balances(balance for location in locations for balance in location["balances"])
        return {"topology_id": topology_id, "sku": self._sku_payload(dict(sku_row)), "locations": locations, **totals}

    def summary(self, topology_id: str) -> dict[str, Any]:
        topology = self._topology_payload(self._topology_record(topology_id))
        rows = [row for values in self._inventory_rows(topology_id).values() for row in values]
        totals = self._sum_balances(rows)
        return {
            "topology_id": topology_id,
            "storage_location_count": len(self._storage_nodes(topology)),
            "stocked_location_count": len({row["storage_node_id"] for row in rows}),
            "sku_count": len({row["sku_id"] for row in rows}),
            **totals,
            "data_sources": sorted({row["source"] for row in rows}),
        }

    def zone_summaries(self, topology_id: str) -> list[dict[str, Any]]:
        topology = self._topology_payload(self._topology_record(topology_id))
        locations = self.list_locations(topology_id)
        results: list[dict[str, Any]] = []
        for zone in topology.get("zones", []):
            zone_locations = [location for location in locations if location["zone_id"] == zone["id"]]
            balances = [balance for location in zone_locations for balance in location["balances"]]
            totals = self._sum_balances(balances)
            results.append({
                "topology_id": topology_id,
                "zone_id": zone["id"],
                "zone_label": zone.get("label", zone["id"]),
                "zone_type": zone.get("type", "mixed"),
                "storage_location_count": len(zone_locations),
                "stocked_location_count": sum(bool(location["balances"]) for location in zone_locations),
                "sku_count": len({balance["sku_id"] for balance in balances}),
                **totals,
                "data_sources": sorted({balance["source"] for balance in balances}),
            })
        return results

    def configure_access_mapping(
        self,
        topology_id: str,
        storage_node_id: str,
        access_cell_id: str,
        source: str,
    ) -> dict[str, Any]:
        topology = self._topology_payload(self._topology_record(topology_id))
        self._validate_access_mapping(topology, storage_node_id, access_cell_id)
        version = str(topology.get("meta", {}).get("version", "unknown"))
        now = utc_now()
        with get_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO storage_access_mappings (
                  topology_id, storage_node_id, access_cell_id, topology_version, source, validated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(topology_id, storage_node_id) DO UPDATE SET
                  access_cell_id=excluded.access_cell_id,
                  topology_version=excluded.topology_version,
                  source=excluded.source,
                  validated_at=excluded.validated_at
                """,
                (topology_id, storage_node_id, access_cell_id, version, source, now),
            )
        return self.get_location(topology_id, storage_node_id)["access_mapping"]

    def reserve_inventory(self, topology_id: str, storage_node_id: str, storage_layer: int, sku_id: str, quantity: int) -> None:
        if quantity <= 0:
            raise InventoryConflictError("Reservation quantity must be positive")
        with get_conn(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE warehouse_inventory
                SET quantity_reserved = quantity_reserved + ?, updated_at = ?
                WHERE topology_id = ? AND storage_node_id = ? AND storage_layer = ? AND sku_id = ?
                  AND quantity_on_hand - quantity_reserved >= ?
                """,
                (quantity, utc_now(), topology_id, storage_node_id, storage_layer, sku_id, quantity),
            )
            if cursor.rowcount != 1:
                raise InventoryConflictError("Insufficient available inventory or balance not found")

    def release_reservation(self, topology_id: str, storage_node_id: str, storage_layer: int, sku_id: str, quantity: int) -> None:
        if quantity <= 0:
            raise InventoryConflictError("Release quantity must be positive")
        with get_conn(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE warehouse_inventory
                SET quantity_reserved = quantity_reserved - ?, updated_at = ?
                WHERE topology_id = ? AND storage_node_id = ? AND storage_layer = ? AND sku_id = ?
                  AND quantity_reserved >= ?
                """,
                (quantity, utc_now(), topology_id, storage_node_id, storage_layer, sku_id, quantity),
            )
            if cursor.rowcount != 1:
                raise InventoryConflictError("Reservation is insufficient or balance not found")

    def confirm_pick(
        self,
        event_id: str,
        topology_id: str,
        storage_node_id: str,
        storage_layer: int,
        sku_id: str,
        quantity: int,
        source: str = "simulation_runtime",
    ) -> bool:
        """Confirm a pick exactly once. Returns False for a duplicate event id."""
        if quantity <= 0:
            raise InventoryConflictError("Pick quantity must be positive")
        with get_conn(self.db_path) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO inventory_movements (
                      event_id, topology_id, storage_node_id, storage_layer, sku_id,
                      movement_type, quantity, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'pick_confirmed', ?, ?, ?)
                    """,
                    (event_id, topology_id, storage_node_id, storage_layer, sku_id, quantity, source, utc_now()),
                )
            except sqlite3.IntegrityError:
                return False
            cursor = conn.execute(
                """
                UPDATE warehouse_inventory
                SET quantity_on_hand = quantity_on_hand - ?,
                    quantity_reserved = quantity_reserved - ?, updated_at = ?
                WHERE topology_id = ? AND storage_node_id = ? AND storage_layer = ? AND sku_id = ?
                  AND quantity_reserved >= ? AND quantity_on_hand >= ?
                """,
                (quantity, quantity, utc_now(), topology_id, storage_node_id, storage_layer, sku_id, quantity, quantity),
            )
            if cursor.rowcount != 1:
                raise InventoryConflictError("Confirmed pick does not have a matching reservation")
        return True

    def _validate_access_mapping(self, topology: dict[str, Any], storage_node_id: str, access_cell_id: str) -> None:
        node = next((item for item in self._storage_nodes(topology) if item["id"] == storage_node_id), None)
        if not node:
            raise AccessMappingValidationError(f"Storage node {storage_node_id} does not exist")
        cell = next((item for item in topology.get("cells", []) if item["id"] == access_cell_id), None)
        if not cell:
            raise AccessMappingValidationError(f"Access cell {access_cell_id} does not exist")
        zones = self._zone_lookup(topology)
        cell_zone = zones.get(cell.get("zoneId"))
        node_zone = zones.get(node.get("zoneId"))
        if cell.get("type") != "road" or not cell.get("walkable", True):
            raise AccessMappingValidationError("Access cell must be a walkable road cell")
        if not cell_zone or cell_zone.get("type") != "ctu":
            raise AccessMappingValidationError("Access cell is not in a CTU zone")
        if not node_zone or node_zone.get("type") != "ctu":
            raise AccessMappingValidationError("Storage node is not in a CTU zone")
        if (cell.get("floor") or "1F") != (node.get("floor") or "1F"):
            raise AccessMappingValidationError("Access cell and storage node must be on the same floor")
        distance = abs(int(cell["row"]) - int(node["row"])) + abs(int(cell["col"]) - int(node["col"]))
        if distance != 1:
            raise AccessMappingValidationError("Access cell must be explicitly adjacent to the storage node")

        road_positions = {
            (item.get("floor") or "1F", int(item["row"]), int(item["col"])): item
            for item in topology.get("cells", [])
            if item.get("type") == "road"
            and item.get("walkable", True)
            and zones.get(item.get("zoneId"), {}).get("type") == "ctu"
        }
        start = (cell.get("floor") or "1F", int(cell["row"]), int(cell["col"]))
        visited = {start}
        queue = deque([start])
        while queue:
            floor, row, col = queue.popleft()
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (floor, row + dr, col + dc)
                if nxt in road_positions and nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        if len(visited) < 2:
            raise AccessMappingValidationError("Access cell is not connected to the CTU road network")

    def _inventory_rows(
        self,
        topology_id: str,
        *,
        storage_node_id: str | None = None,
        sku_id: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        clauses = ["i.topology_id = ?"]
        params: list[Any] = [topology_id]
        if storage_node_id:
            clauses.append("i.storage_node_id = ?")
            params.append(storage_node_id)
        if sku_id:
            clauses.append("i.sku_id = ?")
            params.append(sku_id)
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT i.*, s.sku_name, s.category, s.specification, s.unit,
                       s.weight_kg, s.material, s.source AS sku_source
                FROM warehouse_inventory i
                JOIN sku_catalog s ON s.sku_id = i.sku_id
                WHERE {' AND '.join(clauses)}
                ORDER BY i.storage_node_id, i.storage_layer, i.sku_id
                """,
                params,
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            item["quantity_available"] = item["quantity_on_hand"] - item["quantity_reserved"]
            item["sku"] = self._sku_payload(item)
            grouped.setdefault(item["storage_node_id"], []).append(item)
        return grouped

    def _mapping_rows(self, topology_id: str) -> dict[str, dict[str, Any]]:
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM storage_access_mappings WHERE topology_id = ?",
                (topology_id,),
            ).fetchall()
        return {row["storage_node_id"]: dict(row) for row in rows}

    @staticmethod
    def _sku_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "sku_id": row["sku_id"],
            "sku_name": row["sku_name"],
            "category": row["category"],
            "specification": row["specification"],
            "unit": row["unit"],
            "weight_kg": row.get("weight_kg"),
            "material": row.get("material"),
            "source": row.get("sku_source", row.get("source")),
        }

    @staticmethod
    def _sum_balances(rows: Any) -> dict[str, int]:
        rows = list(rows)
        on_hand = sum(int(row["quantity_on_hand"]) for row in rows)
        reserved = sum(int(row["quantity_reserved"]) for row in rows)
        return {"quantity_on_hand": on_hand, "quantity_reserved": reserved, "quantity_available": on_hand - reserved}

    def _location_payload(
        self,
        topology: dict[str, Any],
        node: dict[str, Any],
        zones: dict[str, dict[str, Any]],
        balances: list[dict[str, Any]],
        mapping: dict[str, Any] | None,
    ) -> dict[str, Any]:
        topology_version = str(topology.get("meta", {}).get("version", "unknown"))
        if mapping is None:
            access = {
                "topology_id": topology["id"], "storage_node_id": node["id"],
                "access_cell_id": None, "status": "unconfigured", "source": None, "validated_at": None,
            }
        else:
            status = "configured" if mapping["topology_version"] == topology_version else "topology_version_mismatch"
            access = {
                "topology_id": topology["id"], "storage_node_id": node["id"],
                "access_cell_id": mapping["access_cell_id"], "status": status,
                "source": mapping["source"], "validated_at": mapping["validated_at"],
            }
        zone = zones.get(node.get("zoneId"))
        return {
            "topology_id": topology["id"],
            "topology_version": topology_version,
            "storage_node_id": node["id"],
            "label": node.get("label", node["id"]),
            "zone_id": node.get("zoneId"),
            "zone_label": zone.get("label") if zone else None,
            "zone_type": zone.get("type") if zone else None,
            "floor": node.get("floor") or "1F",
            "row": int(node["row"]),
            "col": int(node["col"]),
            "rack_level": None,
            "balances": balances,
            "access_mapping": access,
            "data_sources": sorted({balance["source"] for balance in balances}),
        }
