from __future__ import annotations

import time
from typing import Any

from .db import decode_json, encode_json, get_conn, row_to_dict, utc_now


class Repository:
    def upsert_topology(self, topology_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO topologies (id, name, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,
                  payload=excluded.payload,
                  updated_at=excluded.updated_at
                """,
                (topology_id, name, encode_json(payload), now, now),
            )
            row = conn.execute("SELECT * FROM topologies WHERE id = ?", (topology_id,)).fetchone()
        return self._decode_payload(row_to_dict(row))

    def list_topologies(self) -> list[dict[str, Any]]:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM topologies ORDER BY updated_at DESC").fetchall()
        return [self._decode_payload(row_to_dict(row)) for row in rows]

    def get_topology(self, topology_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM topologies WHERE id = ?", (topology_id,)).fetchone()
        return self._decode_payload(row_to_dict(row)) if row else None

    def delete_topology(self, topology_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM topologies WHERE id = ?", (topology_id,)).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM topologies WHERE id = ?", (topology_id,))
        return self._decode_payload(row_to_dict(row))

    def create_run(
        self,
        run_id: str,
        status: str,
        topology_id: str,
        payload: dict[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO simulation_runs (id, status, topology_id, payload, metrics, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, status, topology_id, encode_json(payload), encode_json(metrics), now, now),
            )
            row = conn.execute("SELECT * FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode_run(row_to_dict(row))

    def update_run(self, run_id: str, status: str, payload: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE simulation_runs
                SET status = ?, payload = ?, metrics = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, encode_json(payload), encode_json(metrics), now, run_id),
            )
        # 直接用内存对象构造返回值；此前每 tick 把整份状态 JSON 回读解码一遍，
        # 而 _persist 并不使用返回值，纯属浪费。
        return {
            "id": run_id,
            "status": status,
            "payload": payload,
            "metrics": metrics,
            "updated_at": now,
        }

    def update_simulation_test_tick(
        self,
        run_id: str,
        status: str,
        payload: dict[str, Any],
        metrics: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Atomically persist one simulation-test snapshot and its idempotent events."""
        now = utc_now()
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM simulation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if not existing:
                raise KeyError(run_id)
            for event in events:
                event_id = str(event.get("eventId") or "")
                if not event_id:
                    raise ValueError("simulation_test eventId is required")
                duplicate = conn.execute(
                    """
                    SELECT 1 FROM simulation_events
                    WHERE run_id = ? AND json_extract(payload, '$.eventId') = ?
                    LIMIT 1
                    """,
                    (run_id, event_id),
                ).fetchone()
                if duplicate:
                    continue
                conn.execute(
                    """
                    INSERT INTO simulation_events (run_id, tick, event_type, payload, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        int(event.get("tick", payload.get("tick", 0))),
                        str(event.get("eventType") or "simulation_test_event"),
                        encode_json(event),
                        now,
                    ),
                )
            conn.execute(
                """
                UPDATE simulation_runs
                SET status = ?, payload = ?, metrics = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, encode_json(payload), encode_json(metrics), now, run_id),
            )
        return {
            "id": run_id,
            "status": status,
            "payload": payload,
            "metrics": metrics,
            "updated_at": now,
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode_run(row_to_dict(row)) if row else None

    def list_runs(self, topology_id: str | None = None) -> list[dict[str, Any]]:
        with get_conn() as conn:
            if topology_id:
                rows = conn.execute(
                    "SELECT * FROM simulation_runs WHERE topology_id = ? ORDER BY updated_at DESC",
                    (topology_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM simulation_runs ORDER BY updated_at DESC"
                ).fetchall()
        return [self._decode_run(row_to_dict(row)) for row in rows]

    def claim_run_control(
        self,
        run_id: str,
        owner_id: str,
        lease_seconds: float,
    ) -> bool:
        """Claim a Run unless another live backend instance owns its lease."""
        now = time.time()
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT id FROM simulation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if not run:
                return False
            lease = conn.execute(
                "SELECT owner_id, expires_at FROM simulation_run_leases WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if lease and lease["owner_id"] != owner_id and float(lease["expires_at"]) > now:
                return False
            conn.execute(
                """
                INSERT INTO simulation_run_leases (run_id, owner_id, heartbeat_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                  owner_id=excluded.owner_id,
                  heartbeat_at=excluded.heartbeat_at,
                  expires_at=excluded.expires_at
                """,
                (run_id, owner_id, now, now + max(0.0, lease_seconds)),
            )
        return True

    def renew_run_control(
        self,
        run_id: str,
        owner_id: str,
        lease_seconds: float,
    ) -> bool:
        now = time.time()
        with get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE simulation_run_leases
                SET heartbeat_at = ?, expires_at = ?
                WHERE run_id = ? AND owner_id = ?
                """,
                (now, now + max(0.0, lease_seconds), run_id, owner_id),
            )
        return cursor.rowcount == 1

    def release_run_control(self, run_id: str, owner_id: str) -> bool:
        with get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM simulation_run_leases WHERE run_id = ? AND owner_id = ?",
                (run_id, owner_id),
            )
        return cursor.rowcount == 1

    def stop_run_atomic(
        self,
        run_id: str,
        owner_id: str,
        expected_updated_at: str,
        payload: dict[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist one idempotent stop transition and event in one transaction.

        A persisted running Run may only be stopped by its current lease owner.
        A paused Run may be adopted when its old lease is absent or expired.
        """
        now_epoch = time.time()
        now = utc_now()
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM simulation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if not row:
                return {"outcome": "not_found"}

            current = row_to_dict(row)
            status = str(current["status"])
            if status == "stopped":
                return {"outcome": "already_stopped", "run": self._decode_run(current)}
            if status == "completed":
                return {"outcome": "completed", "run": self._decode_run(current)}
            if status not in {"running", "paused"}:
                return {"outcome": "invalid_status", "status": status}
            if current["updated_at"] != expected_updated_at:
                return {"outcome": "stale"}

            lease = conn.execute(
                "SELECT owner_id, expires_at FROM simulation_run_leases WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            live_owner = (
                str(lease["owner_id"])
                if lease and float(lease["expires_at"]) > now_epoch
                else None
            )
            if live_owner and live_owner != owner_id:
                return {"outcome": "owned", "ownerId": live_owner}
            if status == "running" and live_owner != owner_id:
                return {"outcome": "unowned_running"}

            conn.execute(
                """
                UPDATE simulation_runs
                SET status = 'stopped', payload = ?, metrics = ?, updated_at = ?
                WHERE id = ? AND updated_at = ?
                """,
                (encode_json(payload), encode_json(metrics), now, run_id, expected_updated_at),
            )
            conn.execute(
                """
                INSERT INTO simulation_events (run_id, tick, event_type, payload, created_at)
                VALUES (?, ?, 'simulation_stopped', ?, ?)
                """,
                (
                    run_id,
                    int(payload.get("tick", 0)),
                    encode_json({"runId": run_id}),
                    now,
                ),
            )
            conn.execute("DELETE FROM simulation_run_leases WHERE run_id = ?", (run_id,))
        return {
            "outcome": "stopped",
            "run": {
                "id": run_id,
                "status": "stopped",
                "payload": payload,
                "metrics": metrics,
                "updated_at": now,
            },
        }

    def add_event(self, run_id: str, tick: int, event_type: str, payload: dict[str, Any]) -> None:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO simulation_events (run_id, tick, event_type, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, tick, event_type, encode_json(payload), utc_now()),
            )

    @staticmethod
    def _decode_payload(item: dict[str, Any]) -> dict[str, Any]:
        if item:
            item["payload"] = decode_json(item["payload"])
        return item

    @staticmethod
    def _decode_run(item: dict[str, Any]) -> dict[str, Any]:
        if item:
            item["payload"] = decode_json(item["payload"])
            item["metrics"] = decode_json(item["metrics"])
        return item
