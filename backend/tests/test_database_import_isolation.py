from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

from app.core import config as app_config
from app.core import db as app_db
from pytest_database_isolation import isolated_seeded_database


DEFAULT_DB = Path(__file__).resolve().parents[1] / "app_data" / "agv_platform.db"


def _topology_count(database: Path) -> int:
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM topologies").fetchone()[0])


def test_cached_runtime_seed_is_rebound_for_each_isolated_database(
    tmp_path: Path,
) -> None:
    default_sha = hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest()
    previous_config_path = app_config.DB_PATH
    previous_db_path = app_db.DB_PATH
    database_a = tmp_path / "collection-a.db"
    database_b = tmp_path / "http-b.db"

    try:
        app_config.DB_PATH = database_a
        app_db.DB_PATH = database_a
        from app.services import runtime
        from app.services.seeder import seed_defaults

        app_db.init_db()
        seed_defaults(runtime.repo)
        assert runtime.repo.get_topology_read_only("topology-6ceb4920") is not None
        assert _topology_count(database_a) == 1

        # This is the former failing order: runtime is already cached and was
        # seeded against A before the HTTP test selects B.
        with isolated_seeded_database(database_b) as rebound_runtime:
            assert rebound_runtime is runtime
            assert app_config.DB_PATH == database_b.resolve()
            assert app_db.DB_PATH == database_b.resolve()
            assert runtime.repo.get_topology_read_only("topology-6ceb4920") is not None
            assert _topology_count(database_b) == 1
            assert _topology_count(database_a) == 1

        assert app_config.DB_PATH == database_a
        assert app_db.DB_PATH == database_a
        assert _topology_count(database_b) == 1
    finally:
        app_config.DB_PATH = previous_config_path
        app_db.DB_PATH = previous_db_path

    assert hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest() == default_sha
