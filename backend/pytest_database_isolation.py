"""Test-only database binding that is safe when application modules are cached.

Pytest imports every selected test module during collection.  Several HTTP
tests need ``app.main``/``app.services.runtime``, whose global repository is
created and seeded at import time.  Reassigning DB_PATH later is not enough:
the cached runtime will not repeat its import-time seed for the newly selected
database.  This context manager binds both path holders, initializes and seeds
the selected database explicitly, then restores the exact prior pointers.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from app.core import config as app_config
from app.core import db as app_db


@contextmanager
def isolated_seeded_database(database: Path) -> Iterator[object]:
    previous_config_path = app_config.DB_PATH
    previous_db_path = app_db.DB_PATH
    database = database.resolve()
    assert database != (app_config.APP_DATA_DIR / "agv_platform.db").resolve()

    app_config.DB_PATH = database
    app_db.DB_PATH = database
    try:
        # Import only after both pointers are bound.  If another test already
        # cached runtime, the explicit idempotent seed below repairs that
        # collection-order case without replacing its stateless Repository.
        from app.services import runtime
        from app.services.seeder import seed_defaults

        app_db.init_db()
        seed_defaults(runtime.repo)
        assert app_config.DB_PATH == database
        assert app_db.DB_PATH == database
        assert runtime.repo.get_topology_read_only("topology-6ceb4920") is not None
        yield runtime
    finally:
        # Repository connections are context-managed per operation.  Reopening
        # the file read-only after the caller exits is the closure proof.
        if database.exists():
            with sqlite3.connect(
                f"{database.as_uri()}?mode=ro",
                uri=True,
            ) as connection:
                connection.execute("SELECT 1").fetchone()
        app_config.DB_PATH = previous_config_path
        app_db.DB_PATH = previous_db_path
        assert app_config.DB_PATH == previous_config_path
        assert app_db.DB_PATH == previous_db_path
