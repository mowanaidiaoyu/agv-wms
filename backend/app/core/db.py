from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import DB_PATH


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def init_db(db_path: Path = DB_PATH) -> None:
    _ensure_parent(db_path)
    conn = sqlite3.connect(db_path)
    try:
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

            CREATE TABLE IF NOT EXISTS scenarios (
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
              scenario_id TEXT NOT NULL,
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

            CREATE TABLE IF NOT EXISTS multi_path_evaluations (
              experiment_id TEXT PRIMARY KEY,
              schema_version TEXT NOT NULL,
              experiment_type TEXT NOT NULL,
              status TEXT NOT NULL,
              topology_id TEXT NOT NULL,
              topology_name TEXT NOT NULL,
              topology_version TEXT NOT NULL,
              vehicle_count INTEGER NOT NULL,
              algorithms TEXT NOT NULL,
              input_snapshot TEXT NOT NULL,
              result_snapshot TEXT NOT NULL,
              summary_snapshot TEXT NOT NULL,
              created_at TEXT NOT NULL,
              persisted_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_multi_path_evaluations_created_at
              ON multi_path_evaluations (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_multi_path_evaluations_topology_status
              ON multi_path_evaluations (topology_id, status);

            CREATE TABLE IF NOT EXISTS sku_catalog (
              sku_id TEXT PRIMARY KEY,
              sku_name TEXT NOT NULL,
              category TEXT NOT NULL,
              specification TEXT NOT NULL,
              unit TEXT NOT NULL,
              weight_kg REAL,
              material TEXT,
              source TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS warehouse_inventory (
              topology_id TEXT NOT NULL,
              zone_id TEXT,
              storage_node_id TEXT NOT NULL,
              storage_layer INTEGER NOT NULL DEFAULT 1 CHECK (storage_layer >= 1),
              sku_id TEXT NOT NULL,
              quantity_on_hand INTEGER NOT NULL CHECK (quantity_on_hand >= 0),
              quantity_reserved INTEGER NOT NULL DEFAULT 0 CHECK (
                quantity_reserved >= 0 AND quantity_reserved <= quantity_on_hand
              ),
              source TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (topology_id, storage_node_id, storage_layer, sku_id),
              FOREIGN KEY (sku_id) REFERENCES sku_catalog (sku_id)
            );

            CREATE TABLE IF NOT EXISTS storage_access_mappings (
              topology_id TEXT NOT NULL,
              storage_node_id TEXT NOT NULL,
              access_cell_id TEXT NOT NULL,
              topology_version TEXT NOT NULL,
              source TEXT NOT NULL,
              validated_at TEXT NOT NULL,
              PRIMARY KEY (topology_id, storage_node_id)
            );

            CREATE TABLE IF NOT EXISTS inventory_movements (
              event_id TEXT PRIMARY KEY,
              topology_id TEXT NOT NULL,
              storage_node_id TEXT NOT NULL,
              storage_layer INTEGER NOT NULL,
              sku_id TEXT NOT NULL,
              movement_type TEXT NOT NULL,
              quantity INTEGER NOT NULL CHECK (quantity > 0),
              source TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_inventory_topology_zone
              ON warehouse_inventory (topology_id, zone_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_topology_sku
              ON warehouse_inventory (topology_id, sku_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_storage_node
              ON warehouse_inventory (topology_id, storage_node_id);
            """
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def decode_json(value: str) -> Any:
    return json.loads(value)
