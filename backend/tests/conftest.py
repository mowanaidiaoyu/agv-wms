from __future__ import annotations

import atexit
import gc
from pathlib import Path
import time
import uuid

from app.core import db


# Redirect the database before test modules import app.main/app.services.runtime.
# This prevents API tests that use the process-global Repository from ever
# opening backend/app_data/agv_platform.db. Tests that need a bespoke database
# may still monkeypatch db.DB_PATH and will restore to this isolated session DB.
_TEST_DATABASE_ROOT = Path(__file__).resolve().parent / ".isolated_sqlite"
_TEST_DATABASE_ROOT.mkdir(parents=True, exist_ok=True)
_TEST_DATABASE = _TEST_DATABASE_ROOT / f"pytest-{uuid.uuid4().hex}.db"
db.DB_PATH = _TEST_DATABASE


def _remove_session_database() -> None:
    gc.collect()
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{_TEST_DATABASE}{suffix}")
        for _ in range(10):
            if not path.exists():
                break
            try:
                path.unlink()
                break
            except PermissionError:
                gc.collect()
                time.sleep(0.01)
        if path.exists():
            atexit.register(path.unlink, missing_ok=True)


def pytest_sessionfinish() -> None:
    _remove_session_database()

