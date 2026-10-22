from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from ..core.db import decode_json, encode_json, get_conn, utc_now
from ..core.repository import Repository
from .inventory import DEMO_SKUS, SYNTHETIC_DEMO
from .shuttle_pathfinding import plan_shuttle_path
from .shuttle_storage_audit import build_shuttle_storage_candidates
from .shuttle_topology import (
    SHUTTLE_COORDINATE_CONTRACT_REVISION,
    ShuttleConfigStore,
    build_shuttle_track_graph,
    topology_revision,
    validate_shuttle_configuration,
)


DEMO_SEED = "shuttle-stock-demo-v1"
DEMO_MAPPING_SOURCE = "synthetic_demo_adjacency_assumption"
DEMO_VEHICLE_CAPACITY_UNITS = 100


class ShuttleDemoTransferError(ValueError):
    pass


class ShuttleDemoTransferNotFound(ShuttleDemoTransferError):
    pass


class ShuttleDemoTransferConflict(ShuttleDemoTransferError):
    pass


class ShuttleDemoTransferService:
    """Synthetic inventory and controlled single-Shuttle transfer runtime.

    The service intentionally keeps demo inventory separate from CTU/WMS inventory. Rack
    candidates retain their unverified physical-review status; an eligible demo mapping only
    means that a real same-level adjacent ShuttleGraph node passed deterministic geometry and
    graph checks for the offline demonstration.
    """

    def __init__(self, repository: Repository):
        self.repository = repository
        self.db_path = Path(repository.db_path)

    def _record(self, topology_id: str) -> dict[str, Any]:
        record = self.repository.get_topology(topology_id)
        if record is None:
            raise ShuttleDemoTransferNotFound(f"Topology {topology_id} not found")
        return record

    def _context(self, topology_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        topology = self._record(topology_id)["payload"]
        graph = build_shuttle_track_graph(topology)
        validation = validate_shuttle_configuration(
            topology, ShuttleConfigStore().load(topology_id), graph
        )
        if not validation["planning_enabled"]:
            reason = "; ".join(validation["warnings"]) or "ShuttleGraph planning is not enabled"
            raise ShuttleDemoTransferConflict(reason)
        return topology, graph, validation

    @staticmethod
    def _stable_stock(location_id: str) -> list[tuple[dict[str, Any], int]]:
        digest = hashlib.sha256(f"{DEMO_SEED}:{location_id}".encode("utf-8")).digest()
        count = 1 + digest[0] % 3
        start = digest[1] % len(DEMO_SKUS)
        step = (1, 3, 5, 7)[digest[2] % 4]
        selected: list[tuple[dict[str, Any], int]] = []
        used: set[int] = set()
        cursor = start
        for index in range(count):
            while cursor in used:
                cursor = (cursor + 1) % len(DEMO_SKUS)
            used.add(cursor)
            quantity_digest = hashlib.sha256(
                f"{DEMO_SEED}:{location_id}:{DEMO_SKUS[cursor]['sku_id']}".encode("utf-8")
            ).digest()
            quantity = 10 + int.from_bytes(quantity_digest[:2], "big") % 91
            selected.append((DEMO_SKUS[cursor], quantity))
            cursor = (cursor + step + index) % len(DEMO_SKUS)
        return selected

    @staticmethod
    def _eligible_option(candidate: dict[str, Any]) -> dict[str, Any] | None:
        options = sorted(
            candidate.get("access_options", []), key=lambda item: item["track_node_id"]
        )
        return next(
            (
                option
                for option in options
                if option.get("geometry_clear")
                and option.get("connected_to_lift_network")
                and {"real_shuttle_track", "same_level", "orthogonally_adjacent"}.issubset(
                    set(option.get("validation_codes", []))
                )
            ),
            None,
        )

    def initialize_inventory(self, topology_id: str) -> dict[str, Any]:
        topology, _, validation = self._context(topology_id)
        candidates = build_shuttle_storage_candidates(topology)
        revision = topology_revision(topology)
        now = utc_now()
        inserted_skus = 0
        eligible_count = 0
        eligibility_rows: list[tuple[Any, ...]] = []
        balance_rows: list[tuple[Any, ...]] = []
        for candidate in candidates:
            option = self._eligible_option(candidate)
            status = "eligible" if option else "inventory_only"
            if option:
                eligible_count += 1
            reason_codes = (
                list(option["validation_codes"])
                if option
                else list(candidate.get("blocker_codes", []))
            )
            eligibility = {
                "reason_codes": reason_codes,
                "geometry_clear": bool(option and option["geometry_clear"]),
                "connected_to_lift_network": bool(
                    option and option["connected_to_lift_network"]
                ),
                "assumption": (
                    "Offline synthetic demo uses real orthogonal rack/track adjacency; "
                    "the physical pickup mechanism remains unverified."
                ),
            }
            eligibility_rows.append(
                (
                    topology_id, candidate["location_id"], revision,
                    validation["config_revision"],
                    option["track_node_id"] if option else None, status,
                    DEMO_MAPPING_SOURCE, encode_json(eligibility), now,
                )
            )
            balance_rows.extend(
                (
                    topology_id, candidate["location_id"], sku["sku_id"], quantity, now,
                )
                for sku, quantity in self._stable_stock(candidate["location_id"])
            )
        with get_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
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
            existing_balances = conn.execute(
                "SELECT COUNT(*) FROM shuttle_demo_inventory WHERE topology_id=?",
                (topology_id,),
            ).fetchone()[0]
            conn.executemany(
                """
                INSERT INTO shuttle_demo_access_eligibility (
                  topology_id, location_id, topology_revision, shuttle_config_revision,
                  access_track_node_id, eligibility_status, mapping_source,
                  physical_review_status, validation_payload, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'unverified', ?, ?)
                ON CONFLICT(topology_id, location_id) DO UPDATE SET
                  topology_revision=excluded.topology_revision,
                  shuttle_config_revision=excluded.shuttle_config_revision,
                  access_track_node_id=excluded.access_track_node_id,
                  eligibility_status=excluded.eligibility_status,
                  mapping_source=excluded.mapping_source,
                  physical_review_status='unverified',
                  validation_payload=excluded.validation_payload,
                  updated_at=excluded.updated_at
                """,
                eligibility_rows,
            )
            conn.executemany(
                """
                INSERT OR IGNORE INTO shuttle_demo_inventory (
                  topology_id, location_id, sku_id, quantity_on_hand,
                  quantity_reserved, data_source, updated_at
                ) VALUES (?, ?, ?, ?, 0, 'synthetic_demo', ?)
                """,
                balance_rows,
            )
            balance_count = conn.execute(
                "SELECT COUNT(*) FROM shuttle_demo_inventory WHERE topology_id=?",
                (topology_id,),
            ).fetchone()[0]
            inserted_balances = balance_count - existing_balances
        return {
            "topology_id": topology_id,
            "topology_revision": revision,
            "seed": DEMO_SEED,
            "candidate_location_count": len(candidates),
            "eligible_location_count": eligible_count,
            "inserted_sku_count": inserted_skus,
            "inserted_balance_count": inserted_balances,
            "existing_balance_count": existing_balances,
            "data_source": SYNTHETIC_DEMO,
        }

    def list_skus(self, topology_id: str, search: str | None = None) -> list[dict[str, Any]]:
        self._record(topology_id)
        params: list[Any] = [topology_id]
        search_sql = ""
        if search:
            search_sql = " AND (LOWER(s.sku_id) LIKE ? OR LOWER(s.sku_name) LIKE ?)"
            term = f"%{search.casefold()}%"
            params.extend([term, term])
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT s.sku_id, s.sku_name, s.unit,
                       SUM(i.quantity_on_hand) AS quantity_on_hand,
                       SUM(i.quantity_reserved) AS quantity_reserved,
                       COUNT(DISTINCT i.location_id) AS stocked_location_count
                FROM shuttle_demo_inventory i
                JOIN sku_catalog s ON s.sku_id=i.sku_id
                WHERE i.topology_id=? {search_sql}
                GROUP BY s.sku_id, s.sku_name, s.unit
                ORDER BY s.sku_id
                """,
                params,
            ).fetchall()
        return [
            {
                **dict(row),
                "quantity_available": int(row["quantity_on_hand"]) - int(row["quantity_reserved"]),
            }
            for row in rows
        ]

    def list_locations(
        self,
        topology_id: str,
        *,
        level: int | None = None,
        sku_id: str | None = None,
        search: str | None = None,
        transfer_eligible: bool | None = None,
        offset: int = 0,
        limit: int = 1000,
    ) -> dict[str, Any]:
        topology = self._record(topology_id)["payload"]
        candidates = {item["location_id"]: item for item in build_shuttle_storage_candidates(topology)}
        clauses = ["e.topology_id=?"]
        params: list[Any] = [topology_id]
        if level is not None:
            clauses.append("e.location_id LIKE ?")
            params.append(f"%:L{level}:%")
        if sku_id:
            clauses.append(
                "EXISTS (SELECT 1 FROM shuttle_demo_inventory f WHERE "
                "f.topology_id=e.topology_id AND f.location_id=e.location_id "
                "AND f.sku_id=? AND f.quantity_on_hand-f.quantity_reserved>0)"
            )
            params.append(sku_id)
        if search:
            clauses.append("LOWER(e.location_id) LIKE ?")
            params.append(f"%{search.casefold()}%")
        if transfer_eligible is not None:
            clauses.append("e.eligibility_status=?")
            params.append("eligible" if transfer_eligible else "inventory_only")
        where = " AND ".join(clauses)
        with get_conn(self.db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM shuttle_demo_access_eligibility e WHERE {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT e.* FROM shuttle_demo_access_eligibility e
                WHERE {where} ORDER BY e.location_id LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
            location_ids = [row["location_id"] for row in rows]
            balances = self._balance_rows(conn, topology_id, location_ids)
        locations = [
            self._location_payload(candidates[row["location_id"]], dict(row), balances.get(row["location_id"], []))
            for row in rows
            if row["location_id"] in candidates
        ]
        return {
            "topology_id": topology_id,
            "total": total,
            "offset": offset,
            "limit": limit,
            "locations": locations,
        }

    def get_location(self, topology_id: str, location_id: str) -> dict[str, Any]:
        topology = self._record(topology_id)["payload"]
        candidate = next(
            (item for item in build_shuttle_storage_candidates(topology) if item["location_id"] == location_id),
            None,
        )
        if candidate is None:
            raise ShuttleDemoTransferNotFound(f"Shuttle demo location {location_id} not found")
        with get_conn(self.db_path) as conn:
            eligibility = conn.execute(
                "SELECT * FROM shuttle_demo_access_eligibility WHERE topology_id=? AND location_id=?",
                (topology_id, location_id),
            ).fetchone()
            if eligibility is None:
                raise ShuttleDemoTransferConflict("Initialize Shuttle demo inventory first")
            balances = self._balance_rows(conn, topology_id, [location_id]).get(location_id, [])
        return self._location_payload(candidate, dict(eligibility), balances)

    def create_task(
        self,
        topology_id: str,
        source_location_id: str,
        destination_location_id: str,
        sku_id: str,
        quantity: int,
        algorithm: str,
        vehicle_id: str,
        current_track_node_id: str | None,
    ) -> dict[str, Any]:
        if source_location_id == destination_location_id:
            raise ShuttleDemoTransferConflict("Source and destination locations must be different")
        if quantity <= 0:
            raise ShuttleDemoTransferConflict("Transfer quantity must be a positive integer")
        if quantity > DEMO_VEHICLE_CAPACITY_UNITS:
            raise ShuttleDemoTransferConflict(
                f"Synthetic demo Shuttle capacity is {DEMO_VEHICLE_CAPACITY_UNITS} units"
            )
        if algorithm not in {"astar", "dijkstra"}:
            raise ShuttleDemoTransferConflict(f"Unsupported algorithm: {algorithm}")
        topology, graph, validation = self._context(topology_id)
        current_node = current_track_node_id or (
            "L1-r100-c7" if "L1-r100-c7" in graph else sorted(graph)[0]
        )
        if current_node not in graph:
            raise ShuttleDemoTransferConflict("Vehicle current position is not a real ShuttleGraph node")
        source = self._eligibility(topology_id, source_location_id, topology, validation)
        destination = self._eligibility(topology_id, destination_location_id, topology, validation)
        task_id = f"shuttle-transfer-{uuid.uuid4().hex}"
        pick_event_id = f"{task_id}:pick"
        drop_event_id = f"{task_id}:drop"
        now = utc_now()
        with get_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            stock = conn.execute(
                """
                SELECT quantity_on_hand, quantity_reserved FROM shuttle_demo_inventory
                WHERE topology_id=? AND location_id=? AND sku_id=?
                """,
                (topology_id, source_location_id, sku_id),
            ).fetchone()
            if stock is None or int(stock["quantity_on_hand"]) - int(stock["quantity_reserved"]) < quantity:
                raise ShuttleDemoTransferConflict("Source location has insufficient available inventory")
            conn.execute(
                """
                INSERT INTO shuttle_demo_transfer_tasks (
                  task_id, topology_id, topology_revision, shuttle_config_revision,
                  coordinate_contract_revision, vehicle_id, current_track_node_id,
                  source_location_id, source_access_track_node_id,
                  destination_location_id, destination_access_track_node_id,
                  sku_id, quantity, vehicle_capacity_units, vehicle_load_quantity,
                  algorithm, status, pick_event_id, drop_event_id, data_source,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'created', ?, ?,
                          'synthetic_demo', ?, ?)
                """,
                (
                    task_id, topology_id, topology_revision(topology), validation["config_revision"],
                    SHUTTLE_COORDINATE_CONTRACT_REVISION, vehicle_id, current_node,
                    source_location_id, source["access_track_node_id"],
                    destination_location_id, destination["access_track_node_id"],
                    sku_id, quantity, DEMO_VEHICLE_CAPACITY_UNITS, algorithm,
                    pick_event_id, drop_event_id, now, now,
                ),
            )
            cursor = conn.execute(
                """
                UPDATE shuttle_demo_inventory
                SET quantity_reserved=quantity_reserved+?, updated_at=?
                WHERE topology_id=? AND location_id=? AND sku_id=?
                  AND quantity_on_hand-quantity_reserved>=?
                """,
                (quantity, now, topology_id, source_location_id, sku_id, quantity),
            )
            if cursor.rowcount != 1:
                raise ShuttleDemoTransferConflict("Source inventory changed before reservation")
            after = self._raw_balance(conn, topology_id, source_location_id, sku_id)
            self._insert_ledger(
                conn, f"{task_id}:reserve", task_id, topology_id, source_location_id,
                sku_id, "reserve", quantity, 0, quantity, 0, after, 0, now,
            )
            conn.execute(
                "UPDATE shuttle_demo_transfer_tasks SET status='reserved', updated_at=? WHERE task_id=?",
                (now, task_id),
            )
        return self.get_task(task_id)

    def plan_and_start(self, task_id: str) -> dict[str, Any]:
        task = self._task_row(task_id)
        if task["status"] not in {"reserved", "planned", "moving_to_pickup"}:
            if task["status"] in {"picked", "moving_to_dropoff", "delivered"}:
                return self.get_task(task_id)
            raise ShuttleDemoTransferConflict(f"Task in state {task['status']} cannot be planned")
        topology, graph, validation = self._context(task["topology_id"])
        if (
            task["topology_revision"] != topology_revision(topology)
            or task["shuttle_config_revision"] != validation["config_revision"]
        ):
            self._fail_before_pick(task_id, "Topology or Shuttle configuration revision changed")
            raise ShuttleDemoTransferConflict("Task topology contract is no longer current")
        first = plan_shuttle_path(
            graph, validation["lifts"], task["current_track_node_id"],
            task["source_access_track_node_id"], task["algorithm"],
        )
        second = plan_shuttle_path(
            graph, validation["lifts"], task["source_access_track_node_id"],
            task["destination_access_track_node_id"], task["algorithm"],
        )
        if first["result_status"] != "success" or second["result_status"] != "success":
            reason = first.get("failure_reason") or second.get("failure_reason") or "No legal transfer path"
            self._fail_before_pick(task_id, reason)
            raise ShuttleDemoTransferConflict(reason)
        plan = self._combine_plans(task["topology_id"], validation, first, second)
        pickup_at = float(first["estimated_duration_seconds"])
        dropoff_at = float(plan["estimated_duration_seconds"])
        now = utc_now()
        with get_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT status FROM shuttle_demo_transfer_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if current is None:
                raise ShuttleDemoTransferNotFound(f"Transfer task {task_id} not found")
            if current["status"] == "reserved":
                conn.execute(
                    """
                    UPDATE shuttle_demo_transfer_tasks SET status='planned', plan_payload=?,
                      pickup_at_seconds=?, dropoff_at_seconds=?, updated_at=? WHERE task_id=?
                    """,
                    (encode_json(plan), pickup_at, dropoff_at, now, task_id),
                )
                conn.execute(
                    "UPDATE shuttle_demo_transfer_tasks SET status='moving_to_pickup', updated_at=? WHERE task_id=?",
                    (now, task_id),
                )
        return self.get_task(task_id)

    def advance(self, task_id: str, elapsed_seconds: float) -> dict[str, Any]:
        if elapsed_seconds < 0:
            raise ShuttleDemoTransferConflict("Simulation elapsed time cannot be negative")
        now = utc_now()
        with get_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM shuttle_demo_transfer_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise ShuttleDemoTransferNotFound(f"Transfer task {task_id} not found")
            task = dict(row)
            if task["plan_payload"] is None:
                raise ShuttleDemoTransferConflict("Task has not been planned")
            if task["status"] in {"failed", "cancelled"}:
                return self._task_payload(conn, task)
            target_elapsed = max(float(task["simulation_elapsed_seconds"]), elapsed_seconds)
            dropoff_at = float(task["dropoff_at_seconds"])
            target_elapsed = min(target_elapsed, dropoff_at)
            status = task["status"]
            vehicle_load = int(task["vehicle_load_quantity"])
            if status in {"moving_to_pickup", "planned", "reserved"} and target_elapsed >= float(task["pickup_at_seconds"]):
                source_after = self._apply_pick(conn, task, now)
                vehicle_load = int(task["quantity"])
                status = "picked"
                conn.execute(
                    """
                    UPDATE shuttle_demo_transfer_tasks SET status='picked',
                      vehicle_load_quantity=?, simulation_elapsed_seconds=?, updated_at=?
                    WHERE task_id=?
                    """,
                    (vehicle_load, target_elapsed, now, task_id),
                )
                task["vehicle_load_quantity"] = vehicle_load
                task["status"] = status
                _ = source_after
            if status == "picked" and target_elapsed < dropoff_at:
                status = "moving_to_dropoff"
                conn.execute(
                    "UPDATE shuttle_demo_transfer_tasks SET status='moving_to_dropoff', updated_at=? WHERE task_id=?",
                    (now, task_id),
                )
            if status in {"picked", "moving_to_dropoff"} and target_elapsed >= dropoff_at:
                self._apply_drop(conn, task, now)
                vehicle_load = 0
                status = "delivered"
                conn.execute(
                    """
                    UPDATE shuttle_demo_transfer_tasks SET status='delivered',
                      vehicle_load_quantity=0, simulation_elapsed_seconds=?, updated_at=?
                    WHERE task_id=?
                    """,
                    (target_elapsed, now, task_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE shuttle_demo_transfer_tasks SET simulation_elapsed_seconds=?, updated_at=?
                    WHERE task_id=?
                    """,
                    (target_elapsed, now, task_id),
                )
            refreshed = dict(conn.execute(
                "SELECT * FROM shuttle_demo_transfer_tasks WHERE task_id=?", (task_id,)
            ).fetchone())
            return self._task_payload(conn, refreshed)

    def cancel(self, task_id: str) -> dict[str, Any]:
        return self._terminate(task_id, "Cancelled by user", cancelled=True)

    def fail(self, task_id: str, reason: str) -> dict[str, Any]:
        return self._terminate(task_id, reason, cancelled=False)

    def get_task(self, task_id: str) -> dict[str, Any]:
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM shuttle_demo_transfer_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise ShuttleDemoTransferNotFound(f"Transfer task {task_id} not found")
            return self._task_payload(conn, dict(row))

    def list_ledger(self, task_id: str) -> list[dict[str, Any]]:
        with get_conn(self.db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM shuttle_demo_transfer_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not exists:
                raise ShuttleDemoTransferNotFound(f"Transfer task {task_id} not found")
            rows = conn.execute(
                """
                SELECT * FROM shuttle_demo_inventory_ledger
                WHERE task_id=? ORDER BY created_at,
                  CASE event_type
                    WHEN 'reserve' THEN 1 WHEN 'release' THEN 2 WHEN 'pick' THEN 3
                    WHEN 'drop' THEN 4 WHEN 'failure_in_transit' THEN 5 ELSE 9 END,
                  event_id
                """,
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _eligibility(
        self,
        topology_id: str,
        location_id: str,
        topology: dict[str, Any],
        validation: dict[str, Any],
    ) -> dict[str, Any]:
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM shuttle_demo_access_eligibility WHERE topology_id=? AND location_id=?",
                (topology_id, location_id),
            ).fetchone()
        if row is None:
            raise ShuttleDemoTransferConflict("Initialize Shuttle demo inventory first")
        item = dict(row)
        if item["eligibility_status"] != "eligible" or not item["access_track_node_id"]:
            raise ShuttleDemoTransferConflict(
                f"Location {location_id} is inventory-only and has no eligible demo access track"
            )
        if (
            item["topology_revision"] != topology_revision(topology)
            or item["shuttle_config_revision"] != validation["config_revision"]
        ):
            raise ShuttleDemoTransferConflict(
                f"Location {location_id} eligibility belongs to an obsolete topology contract"
            )
        return item

    @staticmethod
    def _combine_plans(
        topology_id: str,
        validation: dict[str, Any],
        first: dict[str, Any],
        second: dict[str, Any],
    ) -> dict[str, Any]:
        offset = float(first["estimated_duration_seconds"])
        shifted_nodes = [
            {**node, "arrival_seconds": float(node["arrival_seconds"]) + offset}
            for node in second["path_nodes"][1:]
        ]
        shifted_edges = [
            {
                **edge,
                "start_seconds": float(edge["start_seconds"]) + offset,
                "motion_start_seconds": float(edge["motion_start_seconds"]) + offset,
                "motion_end_seconds": float(edge["motion_end_seconds"]) + offset,
                "end_seconds": float(edge["end_seconds"]) + offset,
            }
            for edge in second["path_edges"]
        ]
        shifted_transitions = [
            {
                **transition,
                "enter_at_seconds": float(transition["enter_at_seconds"]) + offset,
                "lift_start_seconds": float(transition["lift_start_seconds"]) + offset,
                "arrive_level_seconds": float(transition["arrive_level_seconds"]) + offset,
                "exit_at_seconds": float(transition["exit_at_seconds"]) + offset,
            }
            for transition in second["lift_transitions"]
        ]
        lifts = [
            transition["lift_id"]
            for transition in [*first["lift_transitions"], *second["lift_transitions"]]
        ]
        return {
            "topology_id": topology_id,
            "topology_revision": validation["topology_revision"],
            "config_revision": validation["config_revision"],
            "coordinate_contract_revision": SHUTTLE_COORDINATE_CONTRACT_REVISION,
            "algorithm": first["algorithm"],
            "start": first["start"],
            "destination": second["destination"],
            "chosen_lift": lifts[0] if lifts else None,
            "path_nodes": [*first["path_nodes"], *shifted_nodes],
            "path_edges": [*first["path_edges"], *shifted_edges],
            "lift_transitions": [*first["lift_transitions"], *shifted_transitions],
            "horizontal_distance_cells": float(first["horizontal_distance_cells"])
            + float(second["horizontal_distance_cells"]),
            "estimated_duration_seconds": offset + float(second["estimated_duration_seconds"]),
            "visited_states": int(first["visited_states"]) + int(second["visited_states"]),
            "planning_time_ms": float(first["planning_time_ms"]) + float(second["planning_time_ms"]),
            "result_status": "success",
            "failure_reason": None,
            "timing_source": SYNTHETIC_DEMO,
            "cost_contract": first["cost_contract"],
        }

    def _fail_before_pick(self, task_id: str, reason: str) -> None:
        self._terminate(task_id, reason, cancelled=False)

    def _terminate(self, task_id: str, reason: str, *, cancelled: bool) -> dict[str, Any]:
        now = utc_now()
        with get_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM shuttle_demo_transfer_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise ShuttleDemoTransferNotFound(f"Transfer task {task_id} not found")
            task = dict(row)
            if task["status"] in {"delivered", "failed", "cancelled"}:
                return self._task_payload(conn, task)
            pre_pick = task["status"] in {
                "created", "reserved", "planned", "moving_to_pickup"
            }
            next_status = "cancelled" if cancelled and pre_pick else "failed"
            if pre_pick:
                cursor = conn.execute(
                    """
                    UPDATE shuttle_demo_inventory SET quantity_reserved=quantity_reserved-?, updated_at=?
                    WHERE topology_id=? AND location_id=? AND sku_id=? AND quantity_reserved>=?
                    """,
                    (
                        task["quantity"], now, task["topology_id"], task["source_location_id"],
                        task["sku_id"], task["quantity"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ShuttleDemoTransferConflict("Reserved inventory cannot be released safely")
                after = self._raw_balance(
                    conn, task["topology_id"], task["source_location_id"], task["sku_id"]
                )
                self._insert_ledger(
                    conn, f"{task_id}:release", task_id, task["topology_id"],
                    task["source_location_id"], task["sku_id"], "release", task["quantity"],
                    0, -int(task["quantity"]), 0, after, 0, now,
                )
            elif int(task["vehicle_load_quantity"]) > 0:
                self._insert_ledger(
                    conn, f"{task_id}:failure-in-transit", task_id, task["topology_id"], None,
                    task["sku_id"], "failure_in_transit", task["quantity"], 0, 0, 0,
                    None, int(task["vehicle_load_quantity"]), now,
                )
            conn.execute(
                """
                UPDATE shuttle_demo_transfer_tasks SET status=?, failure_reason=?, updated_at=?
                WHERE task_id=?
                """,
                (next_status, reason, now, task_id),
            )
            refreshed = dict(conn.execute(
                "SELECT * FROM shuttle_demo_transfer_tasks WHERE task_id=?", (task_id,)
            ).fetchone())
            return self._task_payload(conn, refreshed)

    def _apply_pick(self, conn: sqlite3.Connection, task: dict[str, Any], now: str) -> dict[str, int]:
        existing = conn.execute(
            "SELECT 1 FROM shuttle_demo_inventory_ledger WHERE event_id=?",
            (task["pick_event_id"],),
        ).fetchone()
        if existing:
            return self._raw_balance(
                conn, task["topology_id"], task["source_location_id"], task["sku_id"]
            )
        cursor = conn.execute(
            """
            UPDATE shuttle_demo_inventory
            SET quantity_on_hand=quantity_on_hand-?, quantity_reserved=quantity_reserved-?, updated_at=?
            WHERE topology_id=? AND location_id=? AND sku_id=?
              AND quantity_on_hand>=? AND quantity_reserved>=?
            """,
            (
                task["quantity"], task["quantity"], now, task["topology_id"],
                task["source_location_id"], task["sku_id"], task["quantity"], task["quantity"],
            ),
        )
        if cursor.rowcount != 1:
            raise ShuttleDemoTransferConflict("Pick has no matching reserved source inventory")
        after = self._raw_balance(
            conn, task["topology_id"], task["source_location_id"], task["sku_id"]
        )
        self._insert_ledger(
            conn, task["pick_event_id"], task["task_id"], task["topology_id"],
            task["source_location_id"], task["sku_id"], "pick", task["quantity"],
            -int(task["quantity"]), -int(task["quantity"]), int(task["quantity"]),
            after, int(task["quantity"]), now,
        )
        return after

    def _apply_drop(self, conn: sqlite3.Connection, task: dict[str, Any], now: str) -> dict[str, int]:
        existing = conn.execute(
            "SELECT 1 FROM shuttle_demo_inventory_ledger WHERE event_id=?",
            (task["drop_event_id"],),
        ).fetchone()
        if existing:
            return self._raw_balance(
                conn, task["topology_id"], task["destination_location_id"], task["sku_id"]
            )
        sku_exists = conn.execute(
            "SELECT 1 FROM sku_catalog WHERE sku_id=?", (task["sku_id"],)
        ).fetchone()
        if not sku_exists:
            raise ShuttleDemoTransferConflict("SKU catalog record is missing")
        conn.execute(
            """
            INSERT INTO shuttle_demo_inventory (
              topology_id, location_id, sku_id, quantity_on_hand,
              quantity_reserved, data_source, updated_at
            ) VALUES (?, ?, ?, ?, 0, 'synthetic_demo', ?)
            ON CONFLICT(topology_id, location_id, sku_id) DO UPDATE SET
              quantity_on_hand=quantity_on_hand+excluded.quantity_on_hand,
              updated_at=excluded.updated_at
            """,
            (
                task["topology_id"], task["destination_location_id"], task["sku_id"],
                task["quantity"], now,
            ),
        )
        after = self._raw_balance(
            conn, task["topology_id"], task["destination_location_id"], task["sku_id"]
        )
        self._insert_ledger(
            conn, task["drop_event_id"], task["task_id"], task["topology_id"],
            task["destination_location_id"], task["sku_id"], "drop", task["quantity"],
            int(task["quantity"]), 0, -int(task["quantity"]), after, 0, now,
        )
        return after

    @staticmethod
    def _insert_ledger(
        conn: sqlite3.Connection,
        event_id: str,
        task_id: str,
        topology_id: str,
        location_id: str | None,
        sku_id: str,
        event_type: str,
        quantity: int,
        on_hand_delta: int,
        reserved_delta: int,
        vehicle_load_delta: int,
        balance_after: dict[str, int] | None,
        vehicle_load_after: int,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO shuttle_demo_inventory_ledger (
              event_id, task_id, topology_id, location_id, sku_id, event_type,
              quantity, on_hand_delta, reserved_delta, vehicle_load_delta,
              quantity_on_hand_after, quantity_reserved_after, vehicle_load_after,
              data_source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synthetic_demo', ?)
            """,
            (
                event_id, task_id, topology_id, location_id, sku_id, event_type, quantity,
                on_hand_delta, reserved_delta, vehicle_load_delta,
                balance_after["quantity_on_hand"] if balance_after else None,
                balance_after["quantity_reserved"] if balance_after else None,
                vehicle_load_after, created_at,
            ),
        )

    @staticmethod
    def _raw_balance(
        conn: sqlite3.Connection, topology_id: str, location_id: str, sku_id: str
    ) -> dict[str, int]:
        row = conn.execute(
            """
            SELECT quantity_on_hand, quantity_reserved FROM shuttle_demo_inventory
            WHERE topology_id=? AND location_id=? AND sku_id=?
            """,
            (topology_id, location_id, sku_id),
        ).fetchone()
        if row is None:
            return {"quantity_on_hand": 0, "quantity_reserved": 0, "quantity_available": 0}
        on_hand = int(row["quantity_on_hand"])
        reserved = int(row["quantity_reserved"])
        return {
            "quantity_on_hand": on_hand,
            "quantity_reserved": reserved,
            "quantity_available": on_hand - reserved,
        }

    @staticmethod
    def _balance_rows(
        conn: sqlite3.Connection, topology_id: str, location_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        if not location_ids:
            return {}
        placeholders = ",".join("?" for _ in location_ids)
        rows = conn.execute(
            f"""
            SELECT i.*, s.sku_name, s.unit FROM shuttle_demo_inventory i
            JOIN sku_catalog s ON s.sku_id=i.sku_id
            WHERE i.topology_id=? AND i.location_id IN ({placeholders})
            ORDER BY i.location_id, i.sku_id
            """,
            [topology_id, *location_ids],
        ).fetchall()
        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            item["quantity_available"] = int(item["quantity_on_hand"]) - int(item["quantity_reserved"])
            result.setdefault(item["location_id"], []).append(item)
        return result

    @staticmethod
    def _location_payload(
        candidate: dict[str, Any], eligibility: dict[str, Any], balances: list[dict[str, Any]]
    ) -> dict[str, Any]:
        validation = decode_json(eligibility["validation_payload"])
        return {
            "topology_id": candidate["topology_id"],
            "location_id": candidate["location_id"],
            "zone_id": candidate["zone_id"],
            "level": candidate["storage_level"],
            "row": candidate["row"],
            "col": candidate["col"],
            "world_x": candidate["world_x"],
            "world_y": candidate["world_y"],
            "world_z": candidate["world_z"],
            "transfer_eligible": eligibility["eligibility_status"] == "eligible",
            "access_track_node_id": eligibility["access_track_node_id"],
            "eligibility_reason_codes": list(validation.get("reason_codes", [])),
            "mapping_source": DEMO_MAPPING_SOURCE,
            "physical_review_status": "unverified",
            "balances": [
                {
                    "sku_id": item["sku_id"],
                    "sku_name": item["sku_name"],
                    "unit": item["unit"],
                    "quantity_on_hand": item["quantity_on_hand"],
                    "quantity_reserved": item["quantity_reserved"],
                    "quantity_available": item["quantity_available"],
                }
                for item in balances
            ],
            "data_source": SYNTHETIC_DEMO,
        }

    def _task_row(self, task_id: str) -> dict[str, Any]:
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM shuttle_demo_transfer_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            raise ShuttleDemoTransferNotFound(f"Transfer task {task_id} not found")
        return dict(row)

    def _task_payload(self, conn: sqlite3.Connection, task: dict[str, Any]) -> dict[str, Any]:
        sku = conn.execute(
            "SELECT sku_name, unit FROM sku_catalog WHERE sku_id=?", (task["sku_id"],)
        ).fetchone()
        if sku is None:
            raise ShuttleDemoTransferConflict("Task SKU catalog record is missing")
        source = self._raw_balance(
            conn, task["topology_id"], task["source_location_id"], task["sku_id"]
        )
        destination = self._raw_balance(
            conn, task["topology_id"], task["destination_location_id"], task["sku_id"]
        )
        ledger = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM shuttle_demo_inventory_ledger
                WHERE task_id=? ORDER BY created_at,
                  CASE event_type
                    WHEN 'reserve' THEN 1 WHEN 'release' THEN 2 WHEN 'pick' THEN 3
                    WHEN 'drop' THEN 4 WHEN 'failure_in_transit' THEN 5 ELSE 9 END,
                  event_id
                """,
                (task["task_id"],),
            ).fetchall()
        ]
        balance_common = {"sku_id": task["sku_id"], "sku_name": sku["sku_name"], "unit": sku["unit"]}
        return {
            **task,
            "plan": decode_json(task["plan_payload"]) if task["plan_payload"] else None,
            "source_inventory": {**balance_common, **source},
            "destination_inventory": {**balance_common, **destination},
            "ledger": ledger,
        }
