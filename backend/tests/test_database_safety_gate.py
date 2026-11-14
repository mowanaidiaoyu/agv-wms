from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.core import config as app_config
from app.core import db as app_db
from pytest_database_isolation import isolated_seeded_database


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = (BACKEND_ROOT / "app_data" / "agv_platform.db").resolve()


def _fingerprint(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


def _subprocess_environment(database: Path | None) -> dict[str, str]:
    environment = os.environ.copy()
    environment[app_config.TEST_MODE_ENV] = "1"
    if database is None:
        environment.pop(app_config.DB_PATH_ENV, None)
    else:
        environment[app_config.DB_PATH_ENV] = str(database.resolve())
    environment["PYTHONPATH"] = str(BACKEND_ROOT)
    return environment


def _run_python(code: str, database: Path | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        env=_subprocess_environment(database),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_runtime_first_import_uses_preconfigured_isolated_database(tmp_path: Path) -> None:
    before = _fingerprint(DEFAULT_DB)
    database = tmp_path / "runtime-first.db"
    result = _run_python(
        "from app.services import runtime; "
        "from app.core import config, db; "
        "assert config.DB_PATH.resolve() == db.DB_PATH.resolve(); "
        "assert runtime.repo.get_topology_read_only('topology-6ceb4920'); "
        "print(db.DB_PATH.resolve())",
        database,
    )
    assert result.returncode == 0, result.stderr
    assert str(database.resolve()) in result.stdout
    assert database.exists()
    assert _fingerprint(DEFAULT_DB) == before


@pytest.mark.parametrize(
    "imports",
    (
        "import app.main; import pytest_database_isolation",
        "import pytest_database_isolation; import app.main",
    ),
)
def test_collection_order_variants_remain_isolated(
    tmp_path: Path,
    imports: str,
) -> None:
    before = _fingerprint(DEFAULT_DB)
    suffix = "main-first" if imports.startswith("import app.main") else "helper-first"
    database = tmp_path / f"{suffix}.db"
    result = _run_python(
        f"{imports}; from app.services import runtime; "
        "assert runtime.repo.get_topology_read_only('topology-6ceb4920')",
        database,
    )
    assert result.returncode == 0, result.stderr
    assert database.exists()
    assert _fingerprint(DEFAULT_DB) == before


def test_repeated_testclient_startup_stays_on_selected_database(tmp_path: Path) -> None:
    database = tmp_path / "repeated-testclient.db"
    with isolated_seeded_database(database):
        from app.main import app

        for _ in range(2):
            with TestClient(app) as client:
                response = client.get("/api/v1/topologies/topology-6ceb4920")
                assert response.status_code == 200
                assert len(response.json()["payload"]["nodes"]) == 2520
    assert database.exists()


def test_exception_exit_restores_safe_pointers_and_environment(tmp_path: Path) -> None:
    before_config = app_config.DB_PATH
    before_db = app_db.DB_PATH
    before_environment = os.environ.get(app_config.DB_PATH_ENV)

    with pytest.raises(RuntimeError, match="intentional isolation failure"):
        with isolated_seeded_database(tmp_path / "exception.db"):
            raise RuntimeError("intentional isolation failure")

    assert app_config.DB_PATH == before_config
    assert app_db.DB_PATH == before_db
    assert os.environ.get(app_config.DB_PATH_ENV) == before_environment
    app_db.assert_database_target_safe()


def test_child_process_without_database_path_fails_closed() -> None:
    before = _fingerprint(DEFAULT_DB)
    result = _run_python("import app.services.runtime", None)
    assert result.returncode != 0
    assert "TEST_DATABASE_DEFAULT_PATH_REJECTED" in result.stderr
    assert _fingerprint(DEFAULT_DB) == before


def test_test_mode_rejects_default_path_before_sqlite_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _fingerprint(DEFAULT_DB)
    monkeypatch.setattr(app_config, "DB_PATH", DEFAULT_DB)
    monkeypatch.setattr(app_db, "DB_PATH", DEFAULT_DB)
    monkeypatch.setenv(app_config.TEST_MODE_ENV, "1")
    monkeypatch.setenv(app_config.DB_PATH_ENV, str(DEFAULT_DB))

    with pytest.raises(app_db.UnsafeTestDatabaseError, match="DEFAULT_PATH_REJECTED"):
        app_db.init_db()
    with pytest.raises(app_db.UnsafeTestDatabaseError, match="DEFAULT_PATH_REJECTED"):
        with app_db.get_read_conn():
            pass
    assert _fingerprint(DEFAULT_DB) == before


def test_seed_defaults_only_populates_selected_isolated_database(tmp_path: Path) -> None:
    database = tmp_path / "seed-only-here.db"
    with isolated_seeded_database(database):
        from app.services import runtime

        assert runtime.repo.get_topology_read_only("topology-6ceb4920") is not None
        assert app_config.DB_PATH == database.resolve()
        assert app_db.DB_PATH == database.resolve()
        assert os.environ[app_config.DB_PATH_ENV] == str(database.resolve())

    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM topologies").fetchone()[0] == 1


def test_multiple_temporary_databases_do_not_share_rows(tmp_path: Path) -> None:
    database_a = tmp_path / "a.db"
    database_b = tmp_path / "b.db"
    marker = {"id": "test-isolation-marker", "name": "marker", "nodes": []}

    with isolated_seeded_database(database_a) as runtime_a:
        runtime_a.repo.upsert_topology(marker["id"], marker["name"], marker)
        assert runtime_a.repo.get_topology_read_only(marker["id"]) is not None

    with isolated_seeded_database(database_b) as runtime_b:
        assert runtime_b.repo.get_topology_read_only(marker["id"]) is None

    with isolated_seeded_database(database_a) as runtime_a_again:
        assert runtime_a_again.repo.get_topology_read_only(marker["id"]) is not None


def test_child_process_inherits_active_isolated_database(tmp_path: Path) -> None:
    database = tmp_path / "child-inherits.db"
    with isolated_seeded_database(database):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.services import runtime; from app.core import db; "
                "assert runtime.repo.get_topology_read_only('topology-6ceb4920'); "
                "print(db.DB_PATH.resolve())",
            ],
            cwd=BACKEND_ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    assert result.returncode == 0, result.stderr
    assert str(database.resolve()) in result.stdout


def test_isolation_context_leaves_no_open_database_handle(tmp_path: Path) -> None:
    database = tmp_path / "closed-after-context.db"
    with isolated_seeded_database(database):
        assert database.exists()

    renamed = database.with_name("closed-after-context-renamed.db")
    database.rename(renamed)
    renamed.rename(database)
    assert database.exists()
