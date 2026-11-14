"""Isolated HTTP proof for P6 authoritative throughput metrics."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = BACKEND_ROOT / "app_data" / "agv_platform.db"
DEFAULT_DB_SHA = hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest() if DEFAULT_DB.exists() else None
ISOLATED_ROOT = Path(tempfile.mkdtemp(prefix="agv-p6-throughput-http-"))
ISOLATED_DB = ISOLATED_ROOT / "agv_platform.db"

from fastapi.testclient import TestClient  # noqa: E402
from pytest_database_isolation import isolated_seeded_database  # noqa: E402


def _state(path_policy: str) -> dict:
    return {
        "id": f"run-{path_policy}",
        "tick": 300,
        "status": "stopped",
        "stopReason": "observation_window_complete",
        "throughputContractVersion": "throughput-metrics-v1",
        "throughputGeneration": 1,
        "motionSpeedContract": {"simTickSeconds": 0.6},
        "algorithmExperimentContract": {
            "scenarioSignature": "same-throughput-scenario",
            "observationTicks": 300,
            "pathPolicy": path_policy,
            "assignmentPolicy": "greedy",
            "variableDimension": "pathPolicy",
            "topologyRevision": "2.0.0:16e70cca04bee0ef",
        },
        "orders": [{"id": "order-1", "status": "executing", "completedTick": None}],
        "tasks": [{"id": "task-1", "status": "running", "createdTick": 0, "completedTick": None}],
        "performanceCounters": {"blockedRobotTicks": 2, "physicalTravelWaitRobotTicks": 3},
    }


def _delivery(run_id: str, index: int) -> dict:
    return {
        "contractVersion": "throughput-delivery-event-v1",
        "deliveryId": f"{run_id}:g1:order-1:line-1:task-{index}:SKU-1",
        "runId": run_id,
        "generation": 1,
        "orderId": "order-1",
        "orderLineId": "line-1",
        "taskId": f"task-{index}",
        "skuId": "SKU-1",
        "quantityPieces": 10,
        "quantityBoxes": 1,
        "tick": 100 + index,
        "flowKind": "outbound_order",
        "deliveryStage": "drop_zone_received",
        "evidenceSource": "secondary_arm_dropzone_placed",
    }


def test_isolated_throughput_read_api_and_comparison() -> None:
    with isolated_seeded_database(ISOLATED_DB) as runtime:
        from app.main import app

        for run_id, policy, deliveries in (
            ("run-throughput-a", "astar", 1),
            ("run-throughput-b", "dijkstra", 2),
        ):
            state = _state(policy)
            state["id"] = run_id
            runtime.repo.create_run(
                run_id,
                "stopped",
                "topology-6ceb4920",
                state,
                {"throughput": 999},
            )
            for index in range(deliveries):
                runtime.repo.add_event(run_id, 100 + index, "outbound_delivery_v1", _delivery(run_id, index))

        with TestClient(app) as client:
            document = client.get("/openapi.json").json()
            assert "/api/v1/simulations/{run_id}/metrics/throughput" in document["paths"]
            assert "/api/v1/algorithm-experiments/throughput-compare" in document["paths"]

            single = client.get("/api/v1/simulations/run-throughput-a/metrics/throughput")
            assert single.status_code == 200, single.text
            assert single.json()["deliveredPieces"] == 10
            assert single.json()["throughputPiecesPerSimulatedHour"] == 200.0
            # The legacy mixed counter is deliberately ignored.
            assert single.json()["deliveredPieces"] != 999

            comparison = client.get(
                "/api/v1/algorithm-experiments/throughput-compare",
                params={"baselineRunId": "run-throughput-a", "candidateRunId": "run-throughput-b"},
            )
            assert comparison.status_code == 200, comparison.text
            result = comparison.json()
            assert result["comparisonType"] == "THROUGHPUT_COMPARISON"
            assert result["comparable"] is True
            assert result["deliveredPiecesIncrease"] == 10
            assert result["throughputImprovementPercent"] == 100.0

        # The read endpoints must not mutate the stored Run payload.
        saved = runtime.repo.get_run_read_only("run-throughput-a")
        assert saved is not None
        assert saved["payload"] == {**_state("astar"), "id": "run-throughput-a"}

    assert ISOLATED_DB.exists()
    if DEFAULT_DB.exists():
        assert hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest() == DEFAULT_DB_SHA
