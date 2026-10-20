from __future__ import annotations

import random
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from ..core.db import decode_json, encode_json, get_conn, utc_now
from ..core.repository import Repository
from .ctu_topology_audit import audit_ctu_topology
from .pathfinding import shortest_path_result
from .storage_access import access_candidates, audit_access_candidates, evaluate_access_candidate


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

    def preview_synthetic_supplement(
        self,
        topology_id: str,
        *,
        count: int | None = None,
        fill_all: bool = False,
    ) -> dict[str, Any]:
        self._validate_supplement_request(count, fill_all)
        topology = self._topology_payload(self._topology_record(topology_id))
        candidates, existing_count = self._supplement_candidates(topology_id, topology, seed=0)
        requested = len(candidates) if fill_all else int(count or 0)
        return {
            "topology_id": topology_id,
            "eligible_location_count": len(candidates),
            "requested_location_count": requested,
            "estimated_insert_count": min(requested, len(candidates)),
            "fill_all": fill_all,
            "before": self.summary(topology_id),
            "_existing_count": existing_count,
        }

    def supplement_synthetic_demo(
        self,
        topology_id: str,
        *,
        count: int | None = None,
        fill_all: bool = False,
        seed: int = 42,
    ) -> dict[str, Any]:
        self._validate_supplement_request(count, fill_all)
        topology = self._topology_payload(self._topology_record(topology_id))
        before = self.summary(topology_id)
        candidates, existing_count = self._supplement_candidates(topology_id, topology, seed=seed)
        requested = len(candidates) if fill_all else int(count or 0)
        selected = candidates[:requested]
        inserted_locations = 0
        inserted_balances = 0
        now = utc_now()

        with get_conn(self.db_path) as conn:
            self._insert_demo_skus(conn, now)
            for node in selected:
                sku, on_hand, reserved = self._stable_demo_balance(node["id"], seed)
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
                if cursor.rowcount:
                    inserted_locations += 1
                    inserted_balances += cursor.rowcount

        return {
            "topology_id": topology_id,
            "seed": seed,
            "fill_all": fill_all,
            "requested_location_count": requested,
            "eligible_before": len(candidates),
            "inserted_locations": inserted_locations,
            "inserted_balances": inserted_balances,
            "skipped_existing_locations": existing_count,
            "skipped_due_to_limit": max(0, len(candidates) - len(selected)),
            "source": SYNTHETIC_DEMO,
            "before": before,
            "after": self.summary(topology_id),
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
        recorded_ids = {row["storage_node_id"] for row in rows}
        positive_ids = {row["storage_node_id"] for row in rows if int(row["quantity_on_hand"]) > 0}
        zero_ids = recorded_ids - positive_ids
        total_locations = len(self._storage_nodes(topology))
        return {
            "topology_id": topology_id,
            "storage_location_count": total_locations,
            "stocked_location_count": len(recorded_ids),
            "positive_inventory_location_count": len(positive_ids),
            "unconfigured_location_count": total_locations - len(recorded_ids),
            "zero_inventory_location_count": len(zero_ids),
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
            recorded_ids = {balance["storage_node_id"] for balance in balances}
            positive_ids = {balance["storage_node_id"] for balance in balances if int(balance["quantity_on_hand"]) > 0}
            totals = self._sum_balances(balances)
            results.append({
                "topology_id": topology_id,
                "zone_id": zone["id"],
                "zone_label": zone.get("label", zone["id"]),
                "zone_type": zone.get("type", "mixed"),
                "storage_location_count": len(zone_locations),
                "stocked_location_count": sum(bool(location["balances"]) for location in zone_locations),
                "positive_inventory_location_count": len(positive_ids),
                "unconfigured_location_count": len(zone_locations) - len(recorded_ids),
                "zero_inventory_location_count": len(recorded_ids - positive_ids),
                "sku_count": len({balance["sku_id"] for balance in balances}),
                **totals,
                "data_sources": sorted({balance["source"] for balance in balances}),
            })
        return results

    @staticmethod
    def _validate_supplement_request(count: int | None, fill_all: bool) -> None:
        if fill_all and count is not None:
            raise InventoryConflictError("count must be omitted when fill_all is true")
        if not fill_all and (count is None or count <= 0):
            raise InventoryConflictError("count must be positive when fill_all is false")

    def _supplement_candidates(
        self,
        topology_id: str,
        topology: dict[str, Any],
        *,
        seed: int,
    ) -> tuple[list[dict[str, Any]], int]:
        zones = self._zone_lookup(topology)
        ctu_nodes = [
            node for node in self._storage_nodes(topology)
            if zones.get(node.get("zoneId"), {}).get("type") == "ctu"
        ]
        with get_conn(self.db_path) as conn:
            existing_rows = conn.execute(
                "SELECT DISTINCT storage_node_id FROM warehouse_inventory WHERE topology_id = ?",
                (topology_id,),
            ).fetchall()
        ctu_node_ids = {node["id"] for node in ctu_nodes}
        existing_ids = {row["storage_node_id"] for row in existing_rows if row["storage_node_id"] in ctu_node_ids}
        candidates = [node for node in ctu_nodes if node["id"] not in existing_ids]
        candidates.sort(key=lambda node: hashlib.sha256(f"{seed}:{node['id']}".encode()).hexdigest())
        return candidates, len(existing_ids)

    @staticmethod
    def _insert_demo_skus(conn: sqlite3.Connection, now: str) -> int:
        inserted = 0
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
            inserted += cursor.rowcount
        return inserted

    @staticmethod
    def _stable_demo_balance(storage_node_id: str, seed: int) -> tuple[dict[str, Any], int, int]:
        digest = hashlib.sha256(f"{seed}:{storage_node_id}".encode()).digest()
        sku = DEMO_SKUS[int.from_bytes(digest[:2], "big") % len(DEMO_SKUS)]
        on_hand = 12 + int.from_bytes(digest[2:4], "big") % 89
        reserved = int.from_bytes(digest[4:6], "big") % (min(8, on_hand) + 1)
        return sku, on_hand, reserved

    def configure_access_mapping(
        self,
        topology_id: str,
        storage_node_id: str,
        access_cell_id: str,
        source: str,
        *,
        reviewed: bool = False,
    ) -> dict[str, Any]:
        topology = self._topology_payload(self._topology_record(topology_id))
        evaluation = self._validate_access_mapping(topology, storage_node_id, access_cell_id)
        if not reviewed:
            raise AccessMappingValidationError("Access mapping requires explicit human review before saving")
        if evaluation["status"] == "review_required" and source != "manual_verified":
            raise AccessMappingValidationError("A review-required candidate must be saved as manual_verified")
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

    def get_access_candidates(self, topology_id: str, storage_node_id: str, *, limit: int = 12) -> dict[str, Any]:
        topology = self._topology_payload(self._topology_record(topology_id))
        try:
            return access_candidates(topology, storage_node_id, limit=limit)
        except ValueError as exc:
            raise InventoryNotFoundError(str(exc)) from exc

    def audit_access_candidates(self, topology_id: str) -> dict[str, Any]:
        topology = self._topology_payload(self._topology_record(topology_id))
        audit = audit_access_candidates(topology)
        mappings = self._mapping_rows(topology_id)
        configured = 0
        invalid = 0
        for storage_node_id, mapping in mappings.items():
            if mapping.get("topology_version") != audit["topology_version"]:
                invalid += 1
                continue
            evaluation = evaluate_access_candidate(topology, storage_node_id, str(mapping.get("access_cell_id") or ""))
            if evaluation["status"] == "eligible" or (
                evaluation["status"] == "review_required" and mapping.get("source") == "manual_verified"
            ):
                configured += 1
            else:
                invalid += 1
        return {**audit, "configured_mapping_count": configured, "invalid_mapping_count": invalid}

    def audit_ctu_road_semantics(self, topology_id: str) -> dict[str, Any]:
        topology = self._topology_payload(self._topology_record(topology_id))
        audit = audit_ctu_topology(topology)
        access_audit = self.audit_access_candidates(topology_id)
        audit["formal_access_cell_count"] = access_audit["configured_mapping_count"]
        audit["saved_draft_count"] = len(self.list_ctu_topology_drafts(topology_id))
        return audit

    def create_ctu_trial_draft(self, topology_id: str, note: str | None = None) -> dict[str, Any]:
        """Persist the deterministic audit proposal without changing topology or access mappings."""

        topology = self._topology_payload(self._topology_record(topology_id))
        audit = audit_ctu_topology(topology)
        trial_area = audit.get("trial_area")
        if not trial_area:
            raise InventoryConflictError("Topology has no real road component suitable for an audit-only CTU trial draft")
        version = str(audit["topology_revision"])
        digest_source = ":".join(
            [
                topology_id,
                version,
                *trial_area["road_cell_ids"],
                *trial_area["storage_node_ids"],
            ]
        )
        draft_id = f"ctu-audit-{hashlib.sha256(digest_source.encode()).hexdigest()[:16]}"
        now = utc_now()
        payload = {
            "contract_version": "ctu-road-pick-contract/v1",
            "draft_id": draft_id,
            "topology_id": topology_id,
            "topology_revision": version,
            "zone_id": trial_area["zone_ids"][0] if len(trial_area["zone_ids"]) == 1 else None,
            "zone_ids": trial_area["zone_ids"],
            "road_cell_ids": trial_area["road_cell_ids"],
            "vehicle_type": "ctu",
            "floor": trial_area["floor"],
            "traversability": "unverified",
            "road_connectivity": {
                "component_id": trial_area["road_component_id"],
                "cell_count": len(trial_area["road_cell_ids"]),
            },
            "storage_node_ids": trial_area["storage_node_ids"],
            "pick_face": "unknown",
            "access_cell_id": None,
            "vehicle_start_cell_id": trial_area["vehicle_start_cell_id"],
            "mapping_source": "topology_audit_draft",
            "review_status": "draft",
            "eligible_for_planning": False,
            "reviewer_required": "researcher_b",
            "note": note,
            "reasons": trial_area["reasons"],
            "created_at": now,
            "updated_at": now,
        }
        with get_conn(self.db_path) as conn:
            existing = conn.execute(
                "SELECT payload FROM ctu_topology_drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
            if existing:
                return self._draft_payload(topology, decode_json(existing["payload"]))
            conn.execute(
                """
                INSERT INTO ctu_topology_drafts (
                  draft_id, topology_id, topology_version, review_status, source, payload, created_at, updated_at
                ) VALUES (?, ?, ?, 'draft', 'topology_audit_draft', ?, ?, ?)
                """,
                (draft_id, topology_id, version, encode_json(payload), now, now),
            )
        return self._draft_payload(topology, payload)

    def list_ctu_topology_drafts(self, topology_id: str) -> list[dict[str, Any]]:
        topology = self._topology_payload(self._topology_record(topology_id))
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT payload FROM ctu_topology_drafts WHERE topology_id = ? ORDER BY updated_at DESC, draft_id",
                (topology_id,),
            ).fetchall()
        return [self._draft_payload(topology, decode_json(row["payload"])) for row in rows]

    @staticmethod
    def _draft_payload(topology: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        current_version = str(topology.get("meta", {}).get("version", "unknown"))
        return {
            **payload,
            "version_status": (
                "current" if payload.get("topology_revision") == current_version else "topology_version_mismatch"
            ),
        }

    def plan_storage_access_path(
        self,
        topology_id: str,
        storage_node_id: str,
        start_cell_id: str,
        algorithm: str,
    ) -> dict[str, Any]:
        if algorithm not in {"astar", "dijkstra"}:
            raise InventoryConflictError(f"Unsupported algorithm: {algorithm}")
        topology = self._topology_payload(self._topology_record(topology_id))
        mapping = self._mapping_rows(topology_id).get(storage_node_id)
        if not mapping:
            raise InventoryConflictError("Storage location has no reviewed access mapping")
        topology_version = str(topology.get("meta", {}).get("version", "unknown"))
        if mapping.get("topology_version") != topology_version:
            raise InventoryConflictError("Storage access mapping belongs to a different topology version")
        evaluation = self._validate_access_mapping(topology, storage_node_id, str(mapping["access_cell_id"]))
        if evaluation["status"] == "review_required" and mapping.get("source") != "manual_verified":
            raise InventoryConflictError("Storage access mapping has not completed manual review")

        zones = self._zone_lookup(topology)
        road_cells = [
            cell for cell in topology.get("cells", [])
            if cell.get("type") == "road"
            and cell.get("walkable", True)
            and zones.get(cell.get("zoneId"), {}).get("type") == "ctu"
        ]
        cells_by_id = {cell["id"]: cell for cell in road_cells}
        if start_cell_id not in cells_by_id:
            raise InventoryConflictError("Start cell must be a real walkable CTU road Cell")
        access_cell_id = str(mapping["access_cell_id"])
        road_only_topology = {**topology, "cells": road_cells}
        result = shortest_path_result(road_only_topology, start_cell_id, access_cell_id, algorithm)
        if not result.path:
            raise InventoryConflictError("No road-only CTU path reaches the reviewed storage access Cell")
        return {
            "topology_id": topology_id,
            "topology_version": topology_version,
            "storage_node_id": storage_node_id,
            "access_cell_id": access_cell_id,
            "algorithm": algorithm,
            "path_cells": result.path,
            "total_distance": max(0, len(result.path) - 1),
            "total_cost": result.total_cost,
            "visited_nodes": result.visited_nodes,
            "execution_status": "planning_preview_only",
        }

    def list_access_mappings(self, topology_id: str) -> list[dict[str, Any]]:
        self._topology_record(topology_id)
        return list(self._mapping_rows(topology_id).values())

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

    def _validate_access_mapping(
        self,
        topology: dict[str, Any],
        storage_node_id: str,
        access_cell_id: str,
    ) -> dict[str, Any]:
        evaluation = evaluate_access_candidate(topology, storage_node_id, access_cell_id)
        if evaluation["status"] == "rejected":
            raise AccessMappingValidationError("; ".join(evaluation["reasons"]))
        return evaluation

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
            status = "topology_version_mismatch"
            validation_message = None
            if mapping["topology_version"] == topology_version:
                evaluation = evaluate_access_candidate(topology, node["id"], mapping["access_cell_id"])
                manually_verified = mapping.get("source") == "manual_verified"
                if evaluation["status"] == "eligible" or (evaluation["status"] == "review_required" and manually_verified):
                    status = "configured"
                else:
                    status = "invalid"
                    validation_message = "; ".join(evaluation["reasons"])
            access = {
                "topology_id": topology["id"], "storage_node_id": node["id"],
                "access_cell_id": mapping["access_cell_id"], "status": status,
                "source": mapping["source"], "validated_at": mapping["validated_at"],
                "validation_message": validation_message,
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
