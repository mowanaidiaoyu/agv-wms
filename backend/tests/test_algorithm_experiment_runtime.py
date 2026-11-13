from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.analytics.algorithm_experiment_metrics import compare_algorithm_experiment_runs
from app.core import config as app_config
from app.core import db as app_db
from app.core.repository import Repository
from app.services.simulation import SimulationService
from app.services.shuttle_topology import topology_revision


TOPOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "topology_666.json"


def _transfer() -> dict:
    return {
        "id": "transfer-p5-small",
        "vehicleId": "SHUTTLE-P5-01",
        "skuId": "SKU-P5",
        "skuName": "SKU-P5",
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


async def _run_to_terminal(
    service: SimulationService,
    topology: dict,
    policy: str,
    playback_multiplier: int = 2,
) -> str:
    experiment = {
        "experimentContractVersion": "algorithm-experiment-v1",
        "scenarioId": "p5-small-transfer",
        "scenarioVersion": "1",
        "seed": 42,
        "topologyRevision": topology_revision(topology),
        "safetyLimitTicks": 400,
        "pathPolicy": policy,
        "assignmentPolicy": "greedy",
        "variableDimension": "pathPolicy",
        "algorithmVersion": f"{policy}-runtime-v1",
    }
    started = await service.start(
        topology,
        orders=[],
        transfers=[_transfer()],
        motion_physics_contract=PHYSICS,
        motion_speed_contract=SPEED,
        algorithm_experiment_contract=experiment,
    )
    runtime = service.runtimes[started["runId"]]
    runtime.speed_multiplier = playback_multiplier
    runtime.state["speedMultiplier"] = playback_multiplier
    runtime.state["playbackMultiplier"] = playback_multiplier

    # The production loop is intentionally replaced by deterministic logical
    # stepping in this test.  It exercises the same scheduler and observers,
    # without introducing wall-clock playback timing into an A/B comparison.
    runtime.resetting = True
    assert runtime.task_handle is not None
    runtime.task_handle.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runtime.task_handle
    runtime.resetting = False

    while runtime.state["tick"] < experiment["safetyLimitTicks"]:
        if service._all_tasks_completed(runtime):
            break
        service._step_runtime(runtime, persist=False)

    assert service._all_tasks_completed(runtime), runtime.state["tasks"]
    assert all(task["status"] == "completed" for task in runtime.state["tasks"])
    runtime.state["status"] = "completed"
    runtime.state["stopReason"] = None
    service._finalize_deadlock_observation(runtime, "completed")
    service._finalize_motion_observation(runtime, "completed")
    service._persist(runtime)
    return started["runId"]


def test_real_astar_and_dijkstra_runs_share_one_reproducible_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "p5-runtime.db"
    monkeypatch.setattr(app_config, "DB_PATH", database)
    monkeypatch.setattr(app_db, "DB_PATH", database)
    app_db.init_db()
    repo = Repository()
    service = SimulationService(repo)
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))

    astar_id, dijkstra_id = asyncio.run(
        _run_pair(service, topology)
    )
    comparison = compare_algorithm_experiment_runs(repo, astar_id, dijkstra_id)

    assert comparison is not None
    assert comparison["comparable"] is True
    assert comparison["comparisonReasons"] == []
    assert comparison["baseline"]["status"] == "completed"
    assert comparison["candidate"]["status"] == "completed"
    assert comparison["baseline"]["completedTaskCount"] == 1
    assert comparison["candidate"]["completedTaskCount"] == 1
    assert comparison["baseline"]["totalActualHorizontalMeters"] > 0
    assert comparison["candidate"]["totalActualHorizontalMeters"] > 0
    assert comparison["baseline"]["totalActualHorizontalMeters"] == pytest.approx(54.4)
    assert comparison["candidate"]["totalActualHorizontalMeters"] == pytest.approx(54.4)
    assert comparison["baseline"]["liftVerticalMeters"] == pytest.approx(9.0)
    assert comparison["candidate"]["liftVerticalMeters"] == pytest.approx(9.0)
    assert comparison["baseline"]["simulatedSeconds"] == pytest.approx(67.8)
    assert comparison["candidate"]["simulatedSeconds"] == pytest.approx(67.8)
    assert comparison["baseline"]["motionDataQuality"] == "COMPLETE"
    assert comparison["candidate"]["motionDataQuality"] == "COMPLETE"
    assert comparison["baseline"]["planningCallCount"] >= 1
    assert comparison["candidate"]["planningCallCount"] >= 1
    assert {
        item["algorithm"] for item in comparison["baseline"]["planningEvidence"]
    } == {"astar"}
    assert {
        item["algorithm"] for item in comparison["candidate"]["planningEvidence"]
    } == {"dijkstra"}
    assert comparison["baseline"]["planningEvidence"][0]["visitedStateCount"] == 118
    assert comparison["candidate"]["planningEvidence"][0]["visitedStateCount"] == 669

    astar_contract = comparison["baseline"]["experimentContract"]
    dijkstra_contract = comparison["candidate"]["experimentContract"]
    assert astar_contract["scenarioSignature"] == dijkstra_contract["scenarioSignature"]
    assert astar_contract["initialInventoryHash"] == dijkstra_contract["initialInventoryHash"]
    assert astar_contract["initialVehicleSnapshotHash"] == dijkstra_contract["initialVehicleSnapshotHash"]
    assert comparison["distanceImprovementPercent"] == pytest.approx(0.0)

    saved_states = []
    for run_id in (astar_id, dijkstra_id):
        saved = repo.get_run_read_only(run_id)
        assert saved is not None
        saved_states.append(saved["payload"])
        balances = saved["payload"]["transferInventory"]
        assert sum(int(item["quantityOnHand"]) for item in balances) == 15
    assert saved_states[0]["motionPhysicsContract"] == saved_states[1]["motionPhysicsContract"]
    assert saved_states[0]["motionSpeedContract"] == saved_states[1]["motionSpeedContract"]


async def _run_pair(service: SimulationService, topology: dict) -> tuple[str, str]:
    return (
        await _run_to_terminal(service, topology, "astar"),
        await _run_to_terminal(service, topology, "dijkstra"),
    )


def test_experiment_rejects_pre_arm_r1_topology_revision() -> None:
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    experiment = {
        "experimentContractVersion": "algorithm-experiment-v1",
        "scenarioId": "wrong-revision",
        "scenarioVersion": "1",
        "seed": 42,
        "topologyRevision": "2.0.0:1ec8cf4490635105",
        "safetyLimitTicks": 100,
        "pathPolicy": "astar",
        "assignmentPolicy": "greedy",
        "variableDimension": "pathPolicy",
        "algorithmVersion": "astar-runtime-v1",
    }
    with pytest.raises(ValueError, match="topologyRevision does not match"):
        asyncio.run(
            SimulationService(object()).start(
                topology,
                orders=[],
                transfers=[_transfer()],
                motion_physics_contract=PHYSICS,
                motion_speed_contract=SPEED,
                algorithm_experiment_contract=experiment,
            )
        )


def test_playback_multipliers_preserve_full_physical_run_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "playback-equivalence.db"
    monkeypatch.setattr(app_config, "DB_PATH", database)
    monkeypatch.setattr(app_db, "DB_PATH", database)
    app_db.init_db()
    repo = Repository()
    service = SimulationService(repo)
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))

    run_ids = asyncio.run(_run_playback_variants(service, topology))
    snapshots = []
    for run_id in run_ids:
        saved = repo.get_run_read_only(run_id)
        assert saved is not None
        state = saved["payload"]
        motion = repo.get_motion_distance_metrics(run_id)
        snapshots.append({
            "tick": state["tick"],
            "simulatedSeconds": state["simulatedSeconds"],
            "taskCompletionTicks": [task["completedTick"] for task in state["tasks"]],
            "inventory": state["transferInventory"],
            "distance": motion["totalMetrics"],
        })
    assert snapshots[0] == snapshots[1] == snapshots[2]


async def _run_playback_variants(
    service: SimulationService,
    topology: dict,
) -> tuple[str, str, str]:
    return tuple(
        [await _run_to_terminal(service, topology, "astar", multiplier) for multiplier in (1, 2, 4)]
    )  # type: ignore[return-value]
