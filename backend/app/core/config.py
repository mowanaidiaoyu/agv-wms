import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR.parent / "data"
APP_DATA_DIR = BASE_DIR / "app_data"
DEFAULT_DB_PATH = APP_DATA_DIR / "agv_platform.db"
DB_PATH_ENV = "AGV_WMS_DB_PATH"
TEST_MODE_ENV = "AGV_WMS_TEST_MODE"


def _configured_database_path() -> Path:
    configured = os.environ.get(DB_PATH_ENV)
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DB_PATH


DB_PATH = _configured_database_path()

API_PREFIX = "/api/v1"
WS_PREFIX = "/ws"
