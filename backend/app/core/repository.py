from __future__ import annotations

from typing import Any

from .db import decode_json, encode_json, get_conn, get_read_conn, row_to_dict, utc_now


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

    def get_topology_read_only(self, topology_id: str) -> dict[str, Any] | None:
        with get_read_conn() as conn:
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

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode_run(row_to_dict(row)) if row else None

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
