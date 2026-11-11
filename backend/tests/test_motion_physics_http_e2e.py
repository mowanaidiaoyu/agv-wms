"""Isolated HTTP proof for start contract -> real moves -> event DB -> mileage GET."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import time


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = BACKEND_ROOT / "app_data" / "agv_platform.db"
DEFAULT_DB_SHA = hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest() if DEFAULT_DB.exists() else None
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

ISOLATED_ROOT = Path(tempfile.mkdtemp(prefix="agv-p3-all-http-"))
ISOLATED_DB = ISOLATED_ROOT / "agv_platform.db"

from app.core import config as app_config  # noqa: E402
from app.core import db as app_db  # noqa: E402

app_config.DB_PATH = ISOLATED_DB
app_db.DB_PATH = ISOLATED_DB
assert app_config.DB_PATH.resolve() == app_db.DB_PATH.resolve()
assert app_config.DB_PATH.resolve() != DEFAULT_DB.resolve()

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


def test_isolated_http_motion_distance_chain() -> None:
    contract = {
        "physicsContractVersion": "motion-physics-v1",
        "coordinateContractVersion": "topology-grid-v1",
        "metersPerCell": 0.8,
        "shuttleLevelHeightMeters": 3.0,
        "floorHeightMeters": 4.0,
    }
    run_id = ""
    with TestClient(app) as client:
        topology_response = client.get("/api/v1/topologies/topology-6ceb4920")
        assert topology_response.status_code == 200
        topology = topology_response.json()["payload"]
        assert len(topology["nodes"]) == 2520
        assert len([node for node in topology["nodes"] if node["type"] == "picking_arm"]) == 3
        shuttle_contract = client.get(f"/api/v1/shuttle/topologies/{topology['id']}/contract")
        assert shuttle_contract.status_code == 200
        assert shuttle_contract.json()["topologyRevision"] == "2.0.0:16e70cca04bee0ef"

        invalid = client.post(
            "/api/v1/simulations/start",
            json={
                "topologyId": topology["id"],
                "orders": [],
                "transfers": [],
                "motionPhysicsContract": {**contract, "metersPerCell": "0.8"},
            },
        )
        assert invalid.status_code == 422
        with sqlite3.connect(f"file:{ISOLATED_DB.as_posix()}?mode=ro", uri=True) as connection:
            assert connection.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0] == 0

        start = client.post(
            "/api/v1/simulations/start",
            json={"topologyId": topology["id"], "orders": [], "transfers": [], "motionPhysicsContract": contract},
        )
        assert start.status_code == 200, start.text
        run_id = start.json()["runId"]
        speed = client.post(f"/api/v1/simulations/{run_id}/speed", json={"multiplier": 4})
        assert speed.status_code == 200

        deadline = time.monotonic() + 12
        state = None
        while time.monotonic() < deadline:
            state_response = client.get(f"/api/v1/simulations/{run_id}/state")
            assert state_response.status_code == 200
            state = state_response.json()
            diagnostics = state.get("motionDistanceDiagnosticsV1") or {}
            if diagnostics.get("totalMovementCount", 0) > 0:
                break
            time.sleep(0.1)
        assert state is not None
        assert state["motionPhysicsContract"] == {
            **contract,
            "source": "USER_DEFINED_SIMULATION",
            "calibrationStatus": "UNVERIFIED",
        }
        assert state["motionDistanceDiagnosticsV1"]["totalMovementCount"] > 0

        stop = client.post(f"/api/v1/simulations/{run_id}/stop")
        assert stop.status_code == 200
        query_started = time.perf_counter()
        metrics_response = client.get(f"/api/v1/simulations/{run_id}/metrics/motion-distance")
        query_ms = (time.perf_counter() - query_started) * 1000
        assert metrics_response.status_code == 200, metrics_response.text
        metrics = metrics_response.json()
        assert metrics["runId"] == run_id
        assert metrics["metersPerCell"] == 0.8
        assert metrics["physicsContractSource"] == "USER_DEFINED_SIMULATION"
        assert metrics["calibrationStatus"] == "UNVERIFIED"
        assert metrics["totalMetrics"]["totalHorizontalMeters"]["value"] > 0
        assert metrics["evidenceSummary"]["uniqueMovementCount"] > 0
        deadlock_response = client.get(f"/api/v1/simulations/{run_id}/metrics/deadlock-lifecycle")
        assert deadlock_response.status_code == 200
        assert deadlock_response.json()["runId"] == run_id
        print(f"E2E_QUERY_MS={query_ms:.3f}")

    with sqlite3.connect(f"file:{ISOLATED_DB.as_posix()}?mode=ro", uri=True) as connection:
        event_count = connection.execute(
            "SELECT COUNT(*) FROM simulation_events WHERE run_id = ? AND event_type = 'robot_movement_batch_v1'",
            (run_id,),
        ).fetchone()[0]
    assert event_count > 0
    assert hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest() == DEFAULT_DB_SHA
