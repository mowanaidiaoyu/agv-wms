"""Fail-closed database isolation installed before test-module collection."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_DB = (BACKEND_ROOT / "app_data" / "agv_platform.db").resolve()
SESSION_ROOT = Path(tempfile.mkdtemp(prefix="agv-pytest-session-"))
SESSION_DB = (SESSION_ROOT / "pytest-session.db").resolve()

# These variables are set before any test module can import app.main/runtime.
# Child processes inherit the same fail-closed mode and isolated path.
os.environ["AGV_WMS_TEST_MODE"] = "1"
os.environ["AGV_WMS_DB_PATH"] = str(SESSION_DB)

from app.core import config as app_config  # noqa: E402
from app.core import db as app_db  # noqa: E402

app_config.DB_PATH = SESSION_DB
app_db.DB_PATH = SESSION_DB
app_db.assert_database_target_safe()


def _fingerprint(path: Path) -> tuple[bool, int | None, int | None, str | None]:
    if not path.exists():
        return False, None, None, None
    stat = path.stat()
    return (
        True,
        stat.st_size,
        stat.st_mtime_ns,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


_DEFAULT_DB_FINGERPRINT = _fingerprint(DEFAULT_DB)


def _assert_default_database_unchanged(stage: str) -> None:
    current = _fingerprint(DEFAULT_DB)
    if current != _DEFAULT_DB_FINGERPRINT:
        raise pytest.UsageError(
            f"DEFAULT_DATABASE_CHANGED_DURING_TESTS at {stage}: "
            f"before={_DEFAULT_DB_FINGERPRINT!r}, after={current!r}"
        )


def _assert_active_target_safe(stage: str) -> None:
    for name, value in (
        ("config.DB_PATH", app_config.DB_PATH),
        ("db.DB_PATH", app_db.DB_PATH),
    ):
        if Path(value).resolve() == DEFAULT_DB:
            raise pytest.UsageError(
                f"TEST_DATABASE_DEFAULT_PATH_REJECTED at {stage}: {name}"
            )


def pytest_report_header(config: pytest.Config) -> str:
    return f"database isolation: test-mode, session DB={SESSION_DB}"


def pytest_collection_finish(session: pytest.Session) -> None:
    _assert_default_database_unchanged("collection_finish")
    _assert_active_target_safe("collection_finish")


def pytest_runtest_setup(item: pytest.Item) -> None:
    _assert_default_database_unchanged(f"setup:{item.nodeid}")
    _assert_active_target_safe(f"setup:{item.nodeid}")


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None):
    yield
    _assert_default_database_unchanged(f"teardown:{item.nodeid}")
    _assert_active_target_safe(f"teardown:{item.nodeid}")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _assert_default_database_unchanged("session_finish")
    _assert_active_target_safe("session_finish")


@pytest.fixture(scope="session")
def isolated_session_database_path() -> Path:
    """Expose the collection-safe session target to isolation tests."""

    return SESSION_DB
