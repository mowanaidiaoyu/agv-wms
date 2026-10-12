from __future__ import annotations

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

    def upsert_scenario(self, scenario_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO scenarios (id, name, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,
                  payload=excluded.payload,
                  updated_at=excluded.updated_at
                """,
                (scenario_id, name, encode_json(payload), now, now),
            )
            row = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
        return self._decode_payload(row_to_dict(row))

    def get_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
        return self._decode_payload(row_to_dict(row)) if row else None

    def list_scenarios(self) -> list[dict[str, Any]]:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM scenarios ORDER BY updated_at DESC").fetchall()
        return [self._decode_payload(row_to_dict(row)) for row in rows]

    def topology_has_scenarios(self, topology_id: str) -> bool:
        return any(item["payload"].get("topologyId") == topology_id for item in self.list_scenarios())

    def create_run(
        self,
        run_id: str,
        status: str,
        topology_id: str,
        scenario_id: str,
        payload: dict[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO simulation_runs (id, status, topology_id, scenario_id, payload, metrics, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, status, topology_id, scenario_id, encode_json(payload), encode_json(metrics), now, now),
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
            row = conn.execute("SELECT * FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode_run(row_to_dict(row))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode_run(row_to_dict(row)) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM simulation_runs ORDER BY updated_at DESC").fetchall()
        return [self._decode_run(row_to_dict(row)) for row in rows]

    def add_event(self, run_id: str, tick: int, event_type: str, payload: dict[str, Any]) -> None:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO simulation_events (run_id, tick, event_type, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, tick, event_type, encode_json(payload), utc_now()),
            )

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT run_id, tick, event_type, payload, created_at FROM simulation_events WHERE run_id = ? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        results = []
        for row in rows:
            item = row_to_dict(row)
            item["payload"] = decode_json(item["payload"])
            results.append(item)
        return results

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
