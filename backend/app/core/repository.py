from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import DB_PATH
from .db import decode_json, encode_json, get_conn, row_to_dict, utc_now


class EvaluationSnapshotConflictError(ValueError):
    pass


class StoredEvaluationCorruptError(ValueError):
    pass


class Repository:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)

    def _get_conn(self):
        return get_conn(self.db_path)

    def upsert_topology(self, topology_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self._get_conn() as conn:
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
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM topologies ORDER BY updated_at DESC").fetchall()
        return [self._decode_payload(row_to_dict(row)) for row in rows]

    def get_topology(self, topology_id: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM topologies WHERE id = ?", (topology_id,)).fetchone()
        return self._decode_payload(row_to_dict(row)) if row else None

    def delete_topology(self, topology_id: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM topologies WHERE id = ?", (topology_id,)).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM topologies WHERE id = ?", (topology_id,))
        return self._decode_payload(row_to_dict(row))

    def upsert_scenario(self, scenario_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self._get_conn() as conn:
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
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
        return self._decode_payload(row_to_dict(row)) if row else None

    def list_scenarios(self) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
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
        with self._get_conn() as conn:
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
        with self._get_conn() as conn:
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
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode_run(row_to_dict(row)) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM simulation_runs ORDER BY updated_at DESC").fetchall()
        return [self._decode_run(row_to_dict(row)) for row in rows]

    def add_event(self, run_id: str, tick: int, event_type: str, payload: dict[str, Any]) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO simulation_events (run_id, tick, event_type, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, tick, event_type, encode_json(payload), utc_now()),
            )

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
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

    def save_multi_path_evaluation(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        input_snapshot = evaluation["input_snapshot"]
        results = evaluation["results"]
        summary = {
            "results": [
                {
                    "algorithm": result["algorithm"],
                    "planning_status": result["planning_status"],
                    "error_reason": result["error_reason"],
                    "planning_time_ms": result["planning_time_ms"],
                    "metrics": result["metrics"],
                }
                for result in results
            ]
        }
        values = {
            "experiment_id": evaluation["experiment_id"],
            "schema_version": evaluation["schema_version"],
            "experiment_type": evaluation["experiment_type"],
            "status": evaluation["status"],
            "topology_id": input_snapshot["topology_id"],
            "topology_name": input_snapshot["topology_name"],
            "topology_version": input_snapshot["topology_version"],
            "vehicle_count": len(input_snapshot["vehicles"]),
            "algorithms": input_snapshot["algorithms"],
            "input_snapshot": input_snapshot,
            "result_snapshot": results,
            "summary_snapshot": summary,
            "created_at": evaluation["created_at"],
        }
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO multi_path_evaluations (
                  experiment_id, schema_version, experiment_type, status,
                  topology_id, topology_name, topology_version, vehicle_count,
                  algorithms, input_snapshot, result_snapshot, summary_snapshot,
                  created_at, persisted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["experiment_id"], values["schema_version"], values["experiment_type"],
                    values["status"], values["topology_id"], values["topology_name"],
                    values["topology_version"], values["vehicle_count"], encode_json(values["algorithms"]),
                    encode_json(values["input_snapshot"]), encode_json(values["result_snapshot"]),
                    encode_json(values["summary_snapshot"]), values["created_at"], utc_now(),
                ),
            )
            stored = conn.execute(
                "SELECT * FROM multi_path_evaluations WHERE experiment_id = ?",
                (values["experiment_id"],),
            ).fetchone()
        decoded = self._decode_evaluation(row_to_dict(stored))
        if decoded != evaluation:
            raise EvaluationSnapshotConflictError(
                f"Experiment id {values['experiment_id']} already contains a different snapshot"
            )
        return decoded

    def get_multi_path_evaluation(self, experiment_id: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM multi_path_evaluations WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
        return self._decode_evaluation(row_to_dict(row)) if row else None

    def list_multi_path_evaluations(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        topology_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if topology_id:
            clauses.append("topology_id = ?")
            params.append(topology_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._get_conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM multi_path_evaluations{where}",
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT experiment_id, schema_version, experiment_type, status,
                       topology_id, topology_name, topology_version, vehicle_count,
                       algorithms, summary_snapshot, created_at
                FROM multi_path_evaluations{where}
                ORDER BY created_at DESC, experiment_id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        return {
            "items": [self._decode_evaluation_summary(row_to_dict(row)) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

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

    @staticmethod
    def _decode_evaluation(item: dict[str, Any]) -> dict[str, Any]:
        try:
            return {
                "experiment_id": item["experiment_id"],
                "schema_version": item["schema_version"],
                "experiment_type": item["experiment_type"],
                "status": item["status"],
                "created_at": item["created_at"],
                "input_snapshot": decode_json(item["input_snapshot"]),
                "results": decode_json(item["result_snapshot"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise StoredEvaluationCorruptError("Stored multi-path evaluation is corrupted") from exc

    @staticmethod
    def _decode_evaluation_summary(item: dict[str, Any]) -> dict[str, Any]:
        try:
            summary = decode_json(item["summary_snapshot"])
            return {
                "experiment_id": item["experiment_id"],
                "schema_version": item["schema_version"],
                "experiment_type": item["experiment_type"],
                "status": item["status"],
                "topology_id": item["topology_id"],
                "topology_name": item["topology_name"],
                "topology_version": item["topology_version"],
                "vehicle_count": item["vehicle_count"],
                "algorithms": decode_json(item["algorithms"]),
                "results": summary["results"],
                "created_at": item["created_at"],
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise StoredEvaluationCorruptError("Stored multi-path evaluation summary is corrupted") from exc
