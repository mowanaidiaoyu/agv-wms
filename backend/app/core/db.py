from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import APP_DATA_DIR, DB_PATH, DEFAULT_DB_PATH, TEST_MODE_ENV


_DEFAULT_DB_NORMALIZED = os.path.normcase(os.path.abspath(os.fspath(DEFAULT_DB_PATH)))
_NORMALIZED_TARGET_CACHE: tuple[str, Path, str] | None = None


class UnsafeTestDatabaseError(RuntimeError):
    """Raised before SQLite is opened when a test targets the business DB."""


def _test_mode_enabled() -> bool:
    return os.environ.get(TEST_MODE_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def assert_database_target_safe(path: Path | None = None) -> Path:
    """Reject the default business database before any test-mode connection.

    Production behavior is unchanged when ``AGV_WMS_TEST_MODE`` is absent.
    The guard deliberately lives next to every SQLite connection entry point,
    so collection-order mistakes and child processes fail closed before table
    creation, migration, seeding, reads, or WAL/checkpoint activity.
    """

    global _NORMALIZED_TARGET_CACHE

    raw_target = os.fspath(path or DB_PATH)
    cached = _NORMALIZED_TARGET_CACHE
    if cached is not None and cached[0] == raw_target:
        target, normalized = cached[1], cached[2]
    else:
        target = Path(os.path.abspath(os.fspath(Path(raw_target).expanduser())))
        normalized = os.path.normcase(os.fspath(target))
        _NORMALIZED_TARGET_CACHE = (raw_target, target, normalized)
    if _test_mode_enabled() and normalized == _DEFAULT_DB_NORMALIZED:
        raise UnsafeTestDatabaseError(
            "TEST_DATABASE_DEFAULT_PATH_REJECTED: configure AGV_WMS_DB_PATH "
            "to a unique isolated SQLite file before importing app.main or "
            "app.services.runtime"
        )
    return target


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def init_db() -> Path:
    target = assert_database_target_safe()
    _ensure_parent(target)
    with closing(sqlite3.connect(target)) as conn:
        with conn:
            conn.executescript(
                """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS topologies (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS simulation_runs (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              topology_id TEXT NOT NULL,
              payload TEXT NOT NULL,
              metrics TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS simulation_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              tick INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
                """
            )
            run_columns = {row[1] for row in conn.execute("PRAGMA table_info(simulation_runs)")}
            if "scenario_id" in run_columns:
                conn.executescript(
                    """
                ALTER TABLE simulation_runs RENAME TO simulation_runs_legacy;
                CREATE TABLE simulation_runs (
                  id TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  topology_id TEXT NOT NULL,
                  payload TEXT NOT NULL,
                  metrics TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                INSERT INTO simulation_runs (id, status, topology_id, payload, metrics, created_at, updated_at)
                SELECT id, status, topology_id, payload, metrics, created_at, updated_at
                FROM simulation_runs_legacy;
                DROP TABLE simulation_runs_legacy;
                    """
                )
            conn.execute("DROP TABLE IF EXISTS scenarios")
    return target


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    target = init_db()
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_read_conn() -> Iterator[sqlite3.Connection]:
    """Open the existing database in SQLite read-only mode.

    Diagnostic GET endpoints use this path so they cannot initialize tables,
    commit a transaction, create a run, or mutate inventory/event state.
    """

    target = assert_database_target_safe()
    uri = f"{target.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def decode_json(value: str) -> Any:
    return json.loads(value)
