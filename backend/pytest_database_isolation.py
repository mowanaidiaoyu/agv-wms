"""Test-only database binding that is safe when application modules are cached.

Pytest imports every selected test module during collection.  Several HTTP
tests need ``app.main``/``app.services.runtime``, whose global repository is
created and seeded at import time.  Reassigning DB_PATH later is not enough:
the cached runtime will not repeat its import-time seed for the newly selected
database.  This context manager binds both path holders, initializes and seeds
the selected database explicitly, then restores the exact prior pointers.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator

# Establish a fail-closed test baseline before importing either path holder.
# backend/tests/conftest.py normally supplies the session path.  The fallback
# also protects standalone execution of an individual HTTP test module.
os.environ.setdefault("AGV_WMS_TEST_MODE", "1")
if not os.environ.get("AGV_WMS_DB_PATH"):
    _fallback_root = Path(tempfile.mkdtemp(prefix="agv-pytest-safe-baseline-"))
    os.environ["AGV_WMS_DB_PATH"] = str(_fallback_root / "pytest-session.db")

from app.core import config as app_config
from app.core import db as app_db


_SAFE_BASELINE_DB = Path(os.environ["AGV_WMS_DB_PATH"]).expanduser().resolve()
if app_config.DB_PATH.resolve() == app_config.DEFAULT_DB_PATH.resolve():
    app_config.DB_PATH = _SAFE_BASELINE_DB
if app_db.DB_PATH.resolve() == app_config.DEFAULT_DB_PATH.resolve():
    app_db.DB_PATH = _SAFE_BASELINE_DB
app_db.assert_database_target_safe()


@contextmanager
def isolated_seeded_database(database: Path) -> Iterator[object]:
    previous_config_path = app_config.DB_PATH
    previous_db_path = app_db.DB_PATH
    previous_environment_path = os.environ.get(app_config.DB_PATH_ENV)
    database = database.resolve()
    assert database != app_config.DEFAULT_DB_PATH.resolve()

    app_config.DB_PATH = database
    app_db.DB_PATH = database
    os.environ[app_config.DB_PATH_ENV] = str(database)
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
        app_db.assert_database_target_safe()
        assert runtime.repo.get_topology_read_only("topology-6ceb4920") is not None
        yield runtime
    finally:
        # Repository connections are context-managed per operation.  Reopening
        # the file read-only after the caller exits is the closure proof.
        if database.exists():
            with closing(
                sqlite3.connect(
                    f"{database.as_uri()}?mode=ro",
                    uri=True,
                )
            ) as connection:
                connection.execute("SELECT 1").fetchone()
        app_config.DB_PATH = previous_config_path
        app_db.DB_PATH = previous_db_path
        if previous_environment_path is None:
            os.environ.pop(app_config.DB_PATH_ENV, None)
        else:
            os.environ[app_config.DB_PATH_ENV] = previous_environment_path
        assert app_config.DB_PATH == previous_config_path
        assert app_db.DB_PATH == previous_db_path
        app_db.assert_database_target_safe()
