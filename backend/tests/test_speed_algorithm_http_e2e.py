"""Isolated HTTP proof for P4 speed freezing and P5 experiment comparison."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import time


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = BACKEND_ROOT / "app_data" / "agv_platform.db"
DEFAULT_DB_SHA = hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest() if DEFAULT_DB.exists() else None
ISOLATED_ROOT = Path(tempfile.mkdtemp(prefix="agv-p4-p5-http-"))
ISOLATED_DB = ISOLATED_ROOT / "agv_platform.db"

from fastapi.testclient import TestClient  # noqa: E402
from pytest_database_isolation import isolated_seeded_database  # noqa: E402


PHYSICS = {
    "physicsContractVersion": "motion-physics-v1",
    "coordinateContractVersion": "topology-grid-v1",
    "metersPerCell": 0.8,
    "shuttleLevelHeightMeters": 3.0,
    "floorHeightMeters": 4.0,
    "source": "USER_DEFINED_SIMULATION",
    "calibrationStatus": "UNVERIFIED",
}

SPEED = {
    "speedContractVersion": "motion-speed-v1",
    "simTickSeconds": 0.6,
    "ctuSpeedMetersPerSecond": 1.0,
    "shuttleSpeedMetersPerSecond": 1.2,
    "liftSpeedMetersPerSecond": 0.5,
    "source": "USER_DEFINED_SIMULATION",
    "calibrationStatus": "UNVERIFIED",
}


def _transfer() -> dict:
    return {
        "id": "transfer-p5-http",
        "vehicleId": "SHUTTLE-P5-HTTP",
        "skuId": "SKU-P5-HTTP",
        "skuName": "SKU-P5-HTTP",
        "sourceLocationId": "SIM-BIN-L1-r100-c7",
        "destinationLocationId": "SIM-BIN-L4-r100-c51",
        "sourceTrackNode": "L1-r100-c7",
        "destinationTrackNode": "L4-r100-c51",
        "sourceLevel": 1,
        "destinationLevel": 4,
        "quantity": 2,
        "algorithm": "astar",
        "initialSourceQuantity": 10,
        "initialDestinationQuantity": 5,
    }


def _start(client: TestClient, policy: str) -> str:
    response = client.post(
        "/api/v1/simulations/start",
        json={
            "topologyId": "topology-6ceb4920",
            "orders": [],
            "transfers": [_transfer()],
            "motionPhysicsContract": PHYSICS,
            "motionSpeedContract": SPEED,
            "algorithmExperimentContract": {
                "experimentContractVersion": "algorithm-experiment-v1",
                "scenarioId": "p5-http-small-transfer",
                "scenarioVersion": "1",
                "seed": 42,
                "topologyRevision": "2.0.0:16e70cca04bee0ef",
                "safetyLimitTicks": 400,
                "pathPolicy": policy,
                "assignmentPolicy": "greedy",
                "variableDimension": "pathPolicy",
                "algorithmVersion": f"{policy}-http-v1",
            },
        },
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["runId"]
    speed = client.post(f"/api/v1/simulations/{run_id}/speed", json={"multiplier": 4})
    assert speed.status_code == 200, speed.text
    return run_id


def _wait_for_terminal(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/simulations/{run_id}/state")
        assert response.status_code == 200, response.text
        state = response.json()
        if state["status"] in {"completed", "stopped"}:
            return state
        time.sleep(0.1)
    raise AssertionError(f"Run {run_id} did not terminate before the HTTP test deadline")


def _run_isolated_speed_and_algorithm_http_chain(app: object) -> None:
    with TestClient(app) as client:
        openapi = client.get("/openapi.json")
        assert openapi.status_code == 200
        document = openapi.json()
        start_schema = document["components"]["schemas"]["SimulationStartRequest"]
        assert "motionSpeedContract" in start_schema["properties"]
        assert "algorithmExperimentContract" in start_schema["properties"]
        assert "/api/v1/simulations/{run_id}/metrics/run-performance" in document["paths"]
        assert "/api/v1/algorithm-experiments/compare" in document["paths"]

        topology = client.get("/api/v1/topologies/topology-6ceb4920")
        assert topology.status_code == 200
        payload = topology.json()["payload"]
        assert len(payload["nodes"]) == 2520
        assert len([node for node in payload["nodes"] if node["type"] == "picking_arm"]) == 3

        astar_id = _start(client, "astar")
        astar_state = _wait_for_terminal(client, astar_id)
        dijkstra_id = _start(client, "dijkstra")
        dijkstra_state = _wait_for_terminal(client, dijkstra_id)

        for run_id, state, policy in (
            (astar_id, astar_state, "astar"),
            (dijkstra_id, dijkstra_state, "dijkstra"),
        ):
            assert state["status"] == "completed"
            assert state["stopReason"] is None
            assert state["motionSpeedContract"]["simTickSeconds"] == 0.6
            assert state["motionSpeedContract"]["shuttleSpeedMetersPerSecond"] == 1.2
            assert state["algorithmExperimentContract"]["pathPolicy"] == policy
            performance = client.get(f"/api/v1/simulations/{run_id}/metrics/run-performance")
            assert performance.status_code == 200, performance.text
            result = performance.json()
            assert result["dataQuality"] == "COMPLETE"
            assert result["taskCompletionRatePercent"] == 100.0
            assert result["movementRobotSeconds"] > 0

        experiments = client.get("/api/v1/algorithm-experiments")
        assert experiments.status_code == 200
        assert {item["runId"] for item in experiments.json()["runs"]} == {
            astar_id,
            dijkstra_id,
        }
        comparison = client.get(
            "/api/v1/algorithm-experiments/compare",
            params={"baselineRunId": astar_id, "candidateRunId": dijkstra_id},
        )
        assert comparison.status_code == 200, comparison.text
        result = comparison.json()
        assert result["comparable"] is True
        assert result["baseline"]["taskCompletionRatePercent"] == 100.0
        assert result["candidate"]["taskCompletionRatePercent"] == 100.0
        assert result["distanceImprovementPercent"] == 0.0
        assert result["semantics"]["syntheticWaveDistanceUsed"] is False

    assert ISOLATED_DB.exists()
    if DEFAULT_DB.exists():
        assert hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest() == DEFAULT_DB_SHA


def test_isolated_speed_and_algorithm_http_chain(monkeypatch) -> None:
    with isolated_seeded_database(ISOLATED_DB):
        # The contract under test is simulated-time/algorithm behavior, not a
        # 35-second wall-clock race.  Keep every logical tick and event-loop
        # yield, but remove the playback delay so loaded CI hosts are deterministic.
        from app.services import simulation as simulation_module

        original_sleep = simulation_module.asyncio.sleep

        async def yield_without_playback_delay(_seconds: float) -> None:
            await original_sleep(0)

        monkeypatch.setattr(
            simulation_module.asyncio,
            "sleep",
            yield_without_playback_delay,
        )
        from app.main import app

        _run_isolated_speed_and_algorithm_http_chain(app)
