from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.analytics.throughput_metrics import compare_throughput_metrics
from app.core import config as app_config
from app.core import db as app_db
from app.core.repository import Repository
from app.services.goods import build_inventory
from app.services.shuttle_topology import topology_revision
from app.services.simulation import SimulationService


TOPOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "topology_666.json"
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
OBSERVATION_TICKS = 600


def _orders(topology: dict) -> list[dict]:
    eligible = [slot for slot in build_inventory(topology) if int(slot.get("caseCnt") or 0) > 1]
    selected: list[dict] = []
    seen: set[str] = set()
    for slot in eligible:
        if slot["skuId"] in seen:
            continue
        selected.append(slot)
        seen.add(slot["skuId"])
        if len(selected) == 28:
            break
    assert len(selected) == 28
    result: list[dict] = []
    for order_index in range(4):
        lines = [
            {
                "id": f"line-{order_index}-{line_index}",
                "skuId": slot["skuId"],
                "skuName": slot["skuName"],
                "spec": slot.get("spec"),
                "quantity": 1,
                "qtyMode": "pieces",
            }
            for line_index, slot in enumerate(selected[order_index * 7:(order_index + 1) * 7])
        ]
        result.append({
            "id": f"ORD-P6-{order_index}",
            "priority": "normal",
            "dispatchMode": "parallel",
            "maxParallelCtu": 7,
            "targetStation": "自动选择出库口",
            "createdAt": "2026-11-13T00:00:00Z",
            "lines": lines,
        })
    return result


async def _run_fixed_window(
    service: SimulationService,
    topology: dict,
    *,
    path_policy: str,
    assignment_policy: str,
    variable_dimension: str,
    seed: int = 42,
) -> str:
    started = await service.start(
        topology,
        _orders(topology),
        [],
        motion_physics_contract=PHYSICS,
        motion_speed_contract=SPEED,
        algorithm_experiment_contract={
            "experimentContractVersion": "algorithm-experiment-v1",
            "scenarioId": "p6-multi-ctu-fixed-window",
            "scenarioVersion": "1",
            "seed": seed,
            "topologyRevision": topology_revision(topology),
            "safetyLimitTicks": OBSERVATION_TICKS + 100,
            "observationTicks": OBSERVATION_TICKS,
            "pathPolicy": path_policy,
            "assignmentPolicy": assignment_policy,
            "variableDimension": variable_dimension,
            "algorithmVersion": "p6-throughput-runtime-v1",
        },
    )
    runtime = service.runtimes[started["runId"]]
    runtime.resetting = True
    assert runtime.task_handle is not None
    runtime.task_handle.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runtime.task_handle
    runtime.resetting = False

    while runtime.state["tick"] < OBSERVATION_TICKS:
        service._step_runtime(runtime, persist=False)
    runtime.state["status"] = "stopped"
    runtime.state["stopReason"] = "observation_window_complete"
    runtime.state["throughputObservation"] = {
        "contractVersion": "throughput-metrics-v1",
        "observationTick": OBSERVATION_TICKS,
        "observationSeconds": OBSERVATION_TICKS * SPEED["simTickSeconds"],
        "completedNaturally": False,
    }
    service._finalize_deadlock_observation(runtime, "observation_window_complete")
    service._finalize_motion_observation(runtime, "observation_window_complete")
    service._persist(runtime)
    return started["runId"]


async def _run_all(service: SimulationService, topology: dict) -> tuple[str, str, str, str]:
    return (
        await _run_fixed_window(service, topology, path_policy="astar", assignment_policy="greedy", variable_dimension="assignmentPolicy"),
        await _run_fixed_window(service, topology, path_policy="astar", assignment_policy="hungarian", variable_dimension="assignmentPolicy"),
        await _run_fixed_window(service, topology, path_policy="dijkstra", assignment_policy="greedy", variable_dimension="pathPolicy"),
        await _run_fixed_window(service, topology, path_policy="astar", assignment_policy="greedy", variable_dimension="pathPolicy"),
    )


def test_real_multi_ctu_fixed_window_throughput_pairs_are_fair_and_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "p6-throughput-runtime.db"
    monkeypatch.setattr(app_config, "DB_PATH", database)
    monkeypatch.setattr(app_db, "DB_PATH", database)
    app_db.init_db()
    repo = Repository()
    service = SimulationService(repo)
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))

    greedy_id, hungarian_id, dijkstra_id, astar_id = asyncio.run(_run_all(service, topology))
    assignment = compare_throughput_metrics(
        repo.get_throughput_metrics(greedy_id),
        repo.get_throughput_metrics(hungarian_id),
    )
    path = compare_throughput_metrics(
        repo.get_throughput_metrics(dijkstra_id),
        repo.get_throughput_metrics(astar_id),
    )

    for comparison in (assignment, path):
        assert comparison["comparable"] is True
        assert comparison["comparisonReasons"] == []
        assert comparison["baseline"]["acceptanceEligible"] is True
        assert comparison["candidate"]["acceptanceEligible"] is True
        assert comparison["baseline"]["observationTick"] == OBSERVATION_TICKS
        assert comparison["candidate"]["observationSeconds"] == pytest.approx(360.0)
        assert comparison["baseline"]["deliveredPieces"] > 0
        assert comparison["candidate"]["deliveredPieces"] > 0

    for run_id in (greedy_id, hungarian_id, dijkstra_id, astar_id):
        saved = repo.get_run_read_only(run_id)
        assert saved is not None
        state = saved["payload"]
        assert state["stopReason"] == "observation_window_complete"
        assert len(state["orders"]) == 4
        assert sum(len(order["lines"]) for order in state["orders"]) == 28
        active_ctus = {
            robot_id
            for order in state["orders"]
            for robot_id in order["decomposition"]["activeVehicleIds"]
        }
        assert len(active_ctus) >= 2
        allocated = sum(int(order["decomposition"]["allocatedPieces"]) for order in state["orders"])
        picked = sum(int(order["decomposition"]["pickedPieces"]) for order in state["orders"])
        delivered = repo.get_throughput_metrics(run_id)["deliveredPieces"]
        assert 0 <= delivered <= picked <= allocated == 28
        delivery_ids = state["throughputDeliveryIds"]
        assert len(delivery_ids) == delivered
        assert len(delivery_ids) == len(set(delivery_ids))
        assert all(":cargo-" in delivery_id for delivery_id in delivery_ids)
        assert int(state["throughputCargoSequence"]) >= delivered
        assert all(int(item.get("qty") or 0) >= 0 for item in state["inventory"])
