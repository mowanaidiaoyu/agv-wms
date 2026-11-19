from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import APP_DATA_DIR, DB_PATH, DEFAULT_DB_PATH, TEST_MODE_ENV


_DEFAULT_DB_NORMALIZED = os.path.normcase(os.path.abspath(os.fspath(DEFAULT_DB_PATH)))
_NORMALIZED_TARGET_CACHE: tuple[str, Path, str] | None = None
# 每次写入都执行 DDL 会在 Windows 上反复争抢文件锁、拖慢仿真 tick；同一进程内
# 每个数据库文件只初始化一次，文件被删除后（测试重建库）会再次初始化。
_INITIALIZED_TARGETS: set[str] = set()
_INIT_LOCK = threading.Lock()
# 每次 _initialize_schema 重建表结构时自增；get_conn 用它判断线程缓存的连接是否过期
# （例如测试删除并重建了同一个数据库文件）。
_SCHEMA_COUNTER = 0
_SCHEMA_COUNTER_LOCK = threading.Lock()
# 每个线程复用同一个 SQLite 连接，避免 Windows 上 WAL 模式反复 open/close -shm
# 共享内存文件带来的 ~25ms/次开销。按 (target_key, schema_counter) 失效。
_THREAD_LOCAL = threading.local()


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
    global _SCHEMA_COUNTER
    target = assert_database_target_safe()
    _ensure_parent(target)
    key = os.path.normcase(os.fspath(target))
    if target.exists() and key in _INITIALIZED_TARGETS:
        return target
    with _INIT_LOCK:
        if target.exists() and key in _INITIALIZED_TARGETS:
            return target
        # 重建表结构前先关闭当前线程缓存的连接，避免它持有旧文件句柄
        # （例如测试删除并重建了同一个数据库文件）。
        close_cached_connections()
        _initialize_schema(target)
        _INITIALIZED_TARGETS.add(key)
        with _SCHEMA_COUNTER_LOCK:
            _SCHEMA_COUNTER += 1
    return target


def close_cached_connections() -> None:
    """关闭当前线程缓存的 SQLite 连接（若有）。

    测试隔离工具在切换 DB_PATH 后需要释放旧数据库文件句柄，以便重命名/删除。
    其他线程的缓存连接会在下次 get_conn 时按 schema_counter 自动重建。
    """

    conn = getattr(_THREAD_LOCAL, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _THREAD_LOCAL.conn = None
        _THREAD_LOCAL.conn_key = None
        _THREAD_LOCAL.conn_schema = -1


def _initialize_schema(target: Path) -> None:
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


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    target = init_db()
    target_key = os.path.normcase(os.fspath(target))
    conn = getattr(_THREAD_LOCAL, "conn", None)
    conn_key = getattr(_THREAD_LOCAL, "conn_key", None)
    conn_schema = getattr(_THREAD_LOCAL, "conn_schema", -1)
    if conn is None or conn_key != target_key or conn_schema != _SCHEMA_COUNTER:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        conn = sqlite3.connect(target)
        conn.row_factory = sqlite3.Row
        # WAL 模式是持久化的（建表时设置），这里再声明一次是廉价的防御性操作。
        # synchronous 是连接级（不持久化）；WAL 模式下 NORMAL 是 SQLite 官方推荐值，
        # 只在 checkpoint 时同步 WAL，而非每次 commit 都 fsync，
        # 这对 Windows 上的写入吞吐至关重要，且在 WAL 模式下是安全的。
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _THREAD_LOCAL.conn = conn
        _THREAD_LOCAL.conn_key = target_key
        _THREAD_LOCAL.conn_schema = _SCHEMA_COUNTER
    try:
        yield conn
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        # 出错时丢弃缓存连接，下次调用重新建立
        # （例如测试删除并重建了数据库文件）。
        _THREAD_LOCAL.conn = None
        _THREAD_LOCAL.conn_key = None
        _THREAD_LOCAL.conn_schema = -1
        raise


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
