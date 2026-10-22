from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import defaultdict
from typing import Any

from ..core.db import decode_json, encode_json, get_conn, utc_now
from ..core.repository import Repository
from .inventory import SYNTHETIC_DEMO
from .shuttle_demo_transfer import (
    DEMO_VEHICLE_CAPACITY_UNITS,
    ShuttleDemoTransferConflict,
    ShuttleDemoTransferNotFound,
    ShuttleDemoTransferService,
)
from .shuttle_multi_planner import ShuttleMultiPlanningError, plan_shuttle_multi_transfer
from .shuttle_topology import SHUTTLE_COORDINATE_CONTRACT_REVISION, topology_revision


SERVICE_CONTRACT_REVISION = "shuttle-multi-transfer-service-v1"
PICKUP_SERVICE_SECONDS = 1.0
DROP_SERVICE_SECONDS = 1.0


class ShuttleMultiTransferService(ShuttleDemoTransferService):
    """Persistent 2-4 Shuttle inventory transfer batches.

    Inventory reservation is one SQLite transaction.  Planning is performed by
    the shared prioritized reservation kernel and includes both service dwells.
    Advancing is monotonic and applies each persisted Pick/Drop event once.
    """

    def __init__(self, repository: Repository):
        super().__init__(repository)

    @staticmethod
    def _request_hash(topology_id: str, vehicles: list[dict[str, Any]], options: dict[str, Any]) -> str:
        payload = json.dumps(
            {"topology_id": topology_id, "vehicles": vehicles, "planning_options": options},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def create_and_plan(
        self,
        topology_id: str,
        idempotency_key: str,
        vehicles: list[dict[str, Any]],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        if not 2 <= len(vehicles) <= 4:
            raise ShuttleDemoTransferConflict("Multi-transfer requires between 2 and 4 vehicles")
        vehicle_ids = [str(item["vehicle_id"]) for item in vehicles]
        if len(vehicle_ids) != len(set(vehicle_ids)):
            raise ShuttleDemoTransferConflict("vehicle_id values must be unique")
        request_hash = self._request_hash(topology_id, vehicles, options)
        with get_conn(self.db_path) as conn:
            existing = conn.execute(
                "SELECT batch_id, request_hash FROM shuttle_demo_transfer_batches WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise ShuttleDemoTransferConflict(
                    "idempotency_key already belongs to a different multi-transfer request"
                )
            return self.get_batch(existing["batch_id"])

        topology, graph, validation = self._context(topology_id)
        prepared: list[dict[str, Any]] = []
        for item in vehicles:
            quantity = int(item["quantity"])
            if quantity <= 0 or quantity > DEMO_VEHICLE_CAPACITY_UNITS:
                raise ShuttleDemoTransferConflict(
                    f"Each Shuttle quantity must be 1-{DEMO_VEHICLE_CAPACITY_UNITS} units"
                )
            if item["algorithm"] not in {"astar", "dijkstra"}:
                raise ShuttleDemoTransferConflict(f"Unsupported algorithm: {item['algorithm']}")
            if item["source_location_id"] == item["destination_location_id"]:
                raise ShuttleDemoTransferConflict("Source and destination locations must be different")
            current_node = str(item["current_track_node_id"])
            if current_node not in graph:
                raise ShuttleDemoTransferConflict(
                    f"Vehicle {item['vehicle_id']} current position is not a real ShuttleGraph node"
                )
            source = self._eligibility(
                topology_id, item["source_location_id"], topology, validation
            )
            destination = self._eligibility(
                topology_id, item["destination_location_id"], topology, validation
            )
            prepared.append(
                {
                    **item,
                    "quantity": quantity,
                    "current_track_node_id": current_node,
                    "source_access_track_node_id": source["access_track_node_id"],
                    "destination_access_track_node_id": destination["access_track_node_id"],
                }
            )

        batch_id = f"shuttle-transfer-batch-{uuid.uuid4().hex}"
        now = utc_now()
        aggregate: dict[tuple[str, str], int] = defaultdict(int)
        for item in prepared:
            aggregate[(item["source_location_id"], item["sku_id"])] += item["quantity"]

        with get_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                "SELECT batch_id, request_hash FROM shuttle_demo_transfer_batches WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if duplicate:
                if duplicate["request_hash"] != request_hash:
                    raise ShuttleDemoTransferConflict(
                        "idempotency_key already belongs to a different multi-transfer request"
                    )
                return self._batch_payload(conn, dict(conn.execute(
                    "SELECT * FROM shuttle_demo_transfer_batches WHERE batch_id=?",
                    (duplicate["batch_id"],),
                ).fetchone()))
            for (location_id, sku_id), required in aggregate.items():
                stock = conn.execute(
                    """
                    SELECT quantity_on_hand, quantity_reserved FROM shuttle_demo_inventory
                    WHERE topology_id=? AND location_id=? AND sku_id=?
                    """,
                    (topology_id, location_id, sku_id),
                ).fetchone()
                if stock is None or int(stock["quantity_on_hand"]) - int(stock["quantity_reserved"]) < required:
                    raise ShuttleDemoTransferConflict(
                        f"Insufficient shared inventory for {sku_id} at {location_id}: required {required}"
                    )
            conn.execute(
                """
                INSERT INTO shuttle_demo_transfer_batches (
                  batch_id, idempotency_key, request_hash, topology_id, topology_revision,
                  shuttle_config_revision, coordinate_contract_revision, status,
                  pickup_service_seconds, drop_service_seconds, service_contract_revision,
                  data_source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?, 'synthetic_demo', ?, ?)
                """,
                (
                    batch_id, idempotency_key, request_hash, topology_id,
                    topology_revision(topology), validation["config_revision"],
                    SHUTTLE_COORDINATE_CONTRACT_REVISION, PICKUP_SERVICE_SECONDS,
                    DROP_SERVICE_SECONDS, SERVICE_CONTRACT_REVISION, now, now,
                ),
            )
            task_rows: list[dict[str, Any]] = []
            for item in prepared:
                task_id = f"shuttle-transfer-{uuid.uuid4().hex}"
                task = {**item, "task_id": task_id}
                task_rows.append(task)
                conn.execute(
                    """
                    INSERT INTO shuttle_demo_transfer_tasks (
                      task_id, topology_id, topology_revision, shuttle_config_revision,
                      coordinate_contract_revision, vehicle_id, current_track_node_id,
                      source_location_id, source_access_track_node_id,
                      destination_location_id, destination_access_track_node_id,
                      sku_id, quantity, vehicle_capacity_units, vehicle_load_quantity,
                      algorithm, status, pick_event_id, drop_event_id, data_source,
                      created_at, updated_at, batch_id, priority
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'reserved', ?, ?,
                              'synthetic_demo', ?, ?, ?, ?)
                    """,
                    (
                        task_id, topology_id, topology_revision(topology),
                        validation["config_revision"], SHUTTLE_COORDINATE_CONTRACT_REVISION,
                        item["vehicle_id"], item["current_track_node_id"],
                        item["source_location_id"], item["source_access_track_node_id"],
                        item["destination_location_id"], item["destination_access_track_node_id"],
                        item["sku_id"], item["quantity"], DEMO_VEHICLE_CAPACITY_UNITS,
                        item["algorithm"], f"{task_id}:pick", f"{task_id}:drop",
                        now, now, batch_id, int(item["priority"]),
                    ),
                )
            for (location_id, sku_id), required in aggregate.items():
                cursor = conn.execute(
                    """
                    UPDATE shuttle_demo_inventory SET quantity_reserved=quantity_reserved+?, updated_at=?
                    WHERE topology_id=? AND location_id=? AND sku_id=?
                      AND quantity_on_hand-quantity_reserved>=?
                    """,
                    (required, now, topology_id, location_id, sku_id, required),
                )
                if cursor.rowcount != 1:
                    raise ShuttleDemoTransferConflict("Shared source inventory changed before reservation")
            for task in task_rows:
                after = self._raw_balance(
                    conn, topology_id, task["source_location_id"], task["sku_id"]
                )
                self._insert_ledger(
                    conn, f"{task['task_id']}:reserve", task["task_id"], topology_id,
                    task["source_location_id"], task["sku_id"], "reserve", task["quantity"],
                    0, task["quantity"], 0, after, 0, now,
                )

        try:
            return self.plan_and_start(batch_id, options)
        except ShuttleMultiPlanningError as exc:
            self._fail_before_pick_batch(batch_id, str(exc))
            return self.get_batch(batch_id)
        except Exception:
            self._fail_before_pick_batch(batch_id, "Multi-transfer planning failed")
            raise

    def plan_and_start(self, batch_id: str, options: dict[str, Any]) -> dict[str, Any]:
        with get_conn(self.db_path) as conn:
            batch = conn.execute(
                "SELECT * FROM shuttle_demo_transfer_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            tasks = conn.execute(
                "SELECT * FROM shuttle_demo_transfer_tasks WHERE batch_id=? ORDER BY priority, vehicle_id",
                (batch_id,),
            ).fetchall()
        if batch is None:
            raise ShuttleDemoTransferNotFound(f"Transfer batch {batch_id} not found")
        if batch["status"] in {"running", "completed"}:
            return self.get_batch(batch_id)
        topology, graph, validation = self._context(batch["topology_id"])
        if (
            batch["topology_revision"] != topology_revision(topology)
            or batch["shuttle_config_revision"] != validation["config_revision"]
        ):
            raise ShuttleMultiPlanningError("Batch topology contract is no longer current")
        planning_vehicles = [
            {
                "vehicle_id": task["vehicle_id"],
                "priority": task["priority"],
                "algorithm": task["algorithm"],
                "start_track_node_id": task["current_track_node_id"],
                "pickup_track_node_id": task["source_access_track_node_id"],
                "target_track_node_id": task["destination_access_track_node_id"],
                "source_location_id": task["source_location_id"],
                "destination_location_id": task["destination_location_id"],
                "sku_id": task["sku_id"],
                "quantity": task["quantity"],
            }
            for task in tasks
        ]
        plan = plan_shuttle_multi_transfer(
            graph,
            validation["lifts"],
            planning_vehicles,
            options,
            topology_id=batch["topology_id"],
            topology_version=str(topology.get("meta", {}).get("version", "unknown")),
            topology_revision=batch["topology_revision"],
            config_revision=batch["shuttle_config_revision"],
            pickup_service_seconds=float(batch["pickup_service_seconds"]),
            drop_service_seconds=float(batch["drop_service_seconds"]),
        )
        if plan["status"] != "planning_success" or not plan["safety_validation"]["safe"]:
            raise ShuttleMultiPlanningError(
                plan.get("failure_reason") or "Multi-transfer plan failed final safety validation"
            )
        vehicle_results = {item["vehicle_id"]: item for item in plan["vehicles"]}
        now = utc_now()
        with get_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE shuttle_demo_transfer_batches SET status='running', plan_payload=?,
                  updated_at=? WHERE batch_id=? AND status IN ('reserved', 'planned')
                """,
                (encode_json(plan), now, batch_id),
            )
            for task in tasks:
                result = vehicle_results[task["vehicle_id"]]
                conn.execute(
                    """
                    UPDATE shuttle_demo_transfer_tasks SET status='moving_to_pickup',
                      pickup_at_seconds=?, dropoff_at_seconds=?, updated_at=? WHERE task_id=?
                    """,
                    (
                        result["pickup_service_end_seconds"],
                        result["drop_service_end_seconds"],
                        now,
                        task["task_id"],
                    ),
                )
        return self.get_batch(batch_id)

    def advance(self, batch_id: str, elapsed_seconds: float) -> dict[str, Any]:
        if elapsed_seconds < 0:
            raise ShuttleDemoTransferConflict("Simulation elapsed time cannot be negative")
        now = utc_now()
        with get_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            batch_row = conn.execute(
                "SELECT * FROM shuttle_demo_transfer_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if batch_row is None:
                raise ShuttleDemoTransferNotFound(f"Transfer batch {batch_id} not found")
            batch = dict(batch_row)
            if batch["plan_payload"] is None:
                raise ShuttleDemoTransferConflict("Transfer batch has not been planned")
            if batch["status"] in {"failed", "cancelled"}:
                return self._batch_payload(conn, batch)
            plan = decode_json(batch["plan_payload"])
            target_elapsed = max(float(batch["simulation_elapsed_seconds"]), elapsed_seconds)
            target_elapsed = min(target_elapsed, float(plan["metrics"]["makespan_seconds"]))
            tasks = [dict(row) for row in conn.execute(
                "SELECT * FROM shuttle_demo_transfer_tasks WHERE batch_id=? ORDER BY priority, vehicle_id",
                (batch_id,),
            ).fetchall()]
            for task in tasks:
                status = task["status"]
                if status in {"failed", "cancelled", "delivered"}:
                    continue
                if target_elapsed >= float(task["pickup_at_seconds"]):
                    self._apply_pick(conn, task, now)
                    conn.execute(
                        "UPDATE shuttle_demo_inventory_ledger SET simulation_time_seconds=? WHERE event_id=?",
                        (float(task["pickup_at_seconds"]), task["pick_event_id"]),
                    )
                    task["vehicle_load_quantity"] = int(task["quantity"])
                    status = "picked"
                    conn.execute(
                        "UPDATE shuttle_demo_transfer_tasks SET status='picked', vehicle_load_quantity=?, updated_at=? WHERE task_id=?",
                        (task["quantity"], now, task["task_id"]),
                    )
                if status == "picked" and target_elapsed < float(task["dropoff_at_seconds"]):
                    status = "moving_to_dropoff"
                    conn.execute(
                        "UPDATE shuttle_demo_transfer_tasks SET status='moving_to_dropoff', updated_at=? WHERE task_id=?",
                        (now, task["task_id"]),
                    )
                if status in {"picked", "moving_to_dropoff"} and target_elapsed >= float(task["dropoff_at_seconds"]):
                    self._apply_drop(conn, task, now)
                    conn.execute(
                        "UPDATE shuttle_demo_inventory_ledger SET simulation_time_seconds=? WHERE event_id=?",
                        (float(task["dropoff_at_seconds"]), task["drop_event_id"]),
                    )
                    status = "delivered"
                    conn.execute(
                        "UPDATE shuttle_demo_transfer_tasks SET status='delivered', vehicle_load_quantity=0, updated_at=? WHERE task_id=?",
                        (now, task["task_id"]),
                    )
                conn.execute(
                    "UPDATE shuttle_demo_transfer_tasks SET simulation_elapsed_seconds=?, updated_at=? WHERE task_id=?",
                    (min(target_elapsed, float(task["dropoff_at_seconds"])), now, task["task_id"]),
                )
            remaining = conn.execute(
                "SELECT COUNT(*) FROM shuttle_demo_transfer_tasks WHERE batch_id=? AND status!='delivered'",
                (batch_id,),
            ).fetchone()[0]
            batch_status = "completed" if remaining == 0 else "running"
            conn.execute(
                """
                UPDATE shuttle_demo_transfer_batches SET status=?, simulation_elapsed_seconds=?,
                  completed_at=CASE WHEN ?='completed' THEN COALESCE(completed_at, ?) ELSE completed_at END,
                  updated_at=? WHERE batch_id=?
                """,
                (batch_status, target_elapsed, batch_status, now, now, batch_id),
            )
            refreshed = dict(conn.execute(
                "SELECT * FROM shuttle_demo_transfer_batches WHERE batch_id=?", (batch_id,)
            ).fetchone())
            return self._batch_payload(conn, refreshed)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM shuttle_demo_transfer_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if row is None:
                raise ShuttleDemoTransferNotFound(f"Transfer batch {batch_id} not found")
            return self._batch_payload(conn, dict(row))

    def cancel(self, batch_id: str) -> dict[str, Any]:
        return self._terminate_batch(batch_id, "Cancelled by user", cancelled=True)

    def fail(self, batch_id: str, reason: str) -> dict[str, Any]:
        return self._terminate_batch(batch_id, reason, cancelled=False)

    def _terminate_batch(
        self, batch_id: str, reason: str, *, cancelled: bool
    ) -> dict[str, Any]:
        now = utc_now()
        with get_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            batch_row = conn.execute(
                "SELECT * FROM shuttle_demo_transfer_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if batch_row is None:
                raise ShuttleDemoTransferNotFound(f"Transfer batch {batch_id} not found")
            batch = dict(batch_row)
            if batch["status"] in {"completed", "failed", "cancelled"}:
                return self._batch_payload(conn, batch)
            has_in_transit = False
            for row in conn.execute(
                "SELECT * FROM shuttle_demo_transfer_tasks WHERE batch_id=? ORDER BY priority, vehicle_id",
                (batch_id,),
            ).fetchall():
                task = dict(row)
                if task["status"] in {"delivered", "failed", "cancelled"}:
                    continue
                pre_pick = task["status"] in {
                    "created", "reserved", "planned", "moving_to_pickup"
                }
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
                        raise ShuttleDemoTransferConflict("Batch reservation cannot be released safely")
                    after = self._raw_balance(
                        conn, task["topology_id"], task["source_location_id"], task["sku_id"]
                    )
                    self._insert_ledger(
                        conn, f"{task['task_id']}:release", task["task_id"], task["topology_id"],
                        task["source_location_id"], task["sku_id"], "release", task["quantity"],
                        0, -int(task["quantity"]), 0, after, 0, now,
                    )
                    next_status = "cancelled" if cancelled else "failed"
                else:
                    task_has_in_transit = int(task["vehicle_load_quantity"]) > 0
                    has_in_transit = has_in_transit or task_has_in_transit
                    if task_has_in_transit:
                        self._insert_ledger(
                            conn, f"{task['task_id']}:failure-in-transit", task["task_id"],
                            task["topology_id"], None, task["sku_id"], "failure_in_transit",
                            task["quantity"], 0, 0, 0, None,
                            int(task["vehicle_load_quantity"]), now,
                        )
                    next_status = "failed"
                conn.execute(
                    "UPDATE shuttle_demo_transfer_tasks SET status=?, failure_reason=?, updated_at=? WHERE task_id=?",
                    (next_status, reason, now, task["task_id"]),
                )
            batch_status = "failed" if has_in_transit or not cancelled else "cancelled"
            conn.execute(
                """
                UPDATE shuttle_demo_transfer_batches SET status=?, failure_reason=?,
                  completed_at=COALESCE(completed_at, ?), updated_at=? WHERE batch_id=?
                """,
                (batch_status, reason, now, now, batch_id),
            )
            refreshed = dict(conn.execute(
                "SELECT * FROM shuttle_demo_transfer_batches WHERE batch_id=?", (batch_id,)
            ).fetchone())
            return self._batch_payload(conn, refreshed)

    def _fail_before_pick_batch(self, batch_id: str, reason: str) -> None:
        now = utc_now()
        with get_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            tasks = [dict(row) for row in conn.execute(
                "SELECT * FROM shuttle_demo_transfer_tasks WHERE batch_id=?", (batch_id,)
            ).fetchall()]
            for task in tasks:
                if task["status"] not in {"created", "reserved", "planned", "moving_to_pickup"}:
                    continue
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
                    raise ShuttleDemoTransferConflict("Batch reservation cannot be released safely")
                after = self._raw_balance(
                    conn, task["topology_id"], task["source_location_id"], task["sku_id"]
                )
                self._insert_ledger(
                    conn, f"{task['task_id']}:release", task["task_id"], task["topology_id"],
                    task["source_location_id"], task["sku_id"], "release", task["quantity"],
                    0, -int(task["quantity"]), 0, after, 0, now,
                )
                conn.execute(
                    "UPDATE shuttle_demo_transfer_tasks SET status='failed', failure_reason=?, updated_at=? WHERE task_id=?",
                    (reason, now, task["task_id"]),
                )
            conn.execute(
                "UPDATE shuttle_demo_transfer_batches SET status='failed', failure_reason=?, updated_at=? WHERE batch_id=?",
                (reason, now, batch_id),
            )

    def _batch_payload(self, conn: sqlite3.Connection, batch: dict[str, Any]) -> dict[str, Any]:
        plan = decode_json(batch["plan_payload"]) if batch["plan_payload"] else None
        plan_by_vehicle = {
            item["vehicle_id"]: item for item in (plan or {}).get("vehicles", [])
        }
        tasks: list[dict[str, Any]] = []
        for row in conn.execute(
            "SELECT * FROM shuttle_demo_transfer_tasks WHERE batch_id=? ORDER BY priority, vehicle_id",
            (batch["batch_id"],),
        ).fetchall():
            task = self._task_payload(conn, dict(row))
            vehicle_plan = plan_by_vehicle.get(task["vehicle_id"])
            task["runtime_phase"] = self._runtime_phase(
                task, vehicle_plan, float(batch["simulation_elapsed_seconds"])
            )
            tasks.append(task)
        return {
            **batch,
            "plan": plan,
            "tasks": tasks,
        }

    @staticmethod
    def _runtime_phase(
        task: dict[str, Any], vehicle_plan: dict[str, Any] | None, elapsed: float
    ) -> str:
        if task["status"] in {"delivered", "failed", "cancelled"}:
            return task["status"]
        if not vehicle_plan:
            return "reserved"
        if elapsed < float(vehicle_plan["pickup_service_start_seconds"]):
            return "moving_to_pickup"
        if elapsed < float(vehicle_plan["pickup_service_end_seconds"]):
            return "picking"
        if elapsed < float(vehicle_plan["drop_service_start_seconds"]):
            return "moving_to_dropoff"
        if elapsed < float(vehicle_plan["drop_service_end_seconds"]):
            return "dropping"
        return "delivered"
