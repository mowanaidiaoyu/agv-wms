from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR.parent / "data"
APP_DATA_DIR = BASE_DIR / "app_data"
DB_PATH = APP_DATA_DIR / "agv_platform.db"

API_PREFIX = "/api/v1"
WS_PREFIX = "/ws"
