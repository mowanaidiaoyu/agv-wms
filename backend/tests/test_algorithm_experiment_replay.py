from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from copy import deepcopy
from pathlib import Path

import pytest

from app.analytics.algorithm_experiment_metrics import compare_algorithm_experiment_runs
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


def _order(topology: dict, index: int, *, created_at: str = "2026-11-17T08:00:00Z") -> dict:
    slots = []
    seen: set[str] = set()
    for slot in build_inventory(topology):
        if slot["skuId"] in seen or int(slot.get("caseCnt") or 0) < 1:
            continue
        slots.append(slot)
        seen.add(slot["skuId"])
        if len(slots) > index:
            break
    slot = slots[index]
    return {
        "id": f"ORD-REPLAY-{index}",
        "priority": "normal",
        "dispatchMode": "parallel",
        "maxParallelCtu": 2,
        "targetStation": "自动选择出库口",
        "createdAt": created_at,
        "lines": [{
            "id": f"LINE-REPLAY-{index}",
            "skuId": slot["skuId"],
            "skuName": slot["skuName"],
            "spec": slot.get("spec"),
            "quantity": 1,
            "qtyMode": "pieces",
        }],
    }


def _contract(topology: dict, assignment: str = "greedy") -> dict:
    return {
        "experimentContractVersion": "algorithm-experiment-v1",
        "scenarioId": "final-ux-replay",
        "scenarioVersion": "1",
        "seed": 42,
        "topologyRevision": topology_revision(topology),
        "safetyLimitTicks": 600,
        "pathPolicy": "astar",
        "assignmentPolicy": assignment,
        "variableDimension": "assignmentPolicy",
        "algorithmVersion": "agv-routing-assignment-v1",
    }


async def _cancel_loop(service: SimulationService, run_id: str) -> None:
    runtime = service.runtimes[run_id]
    runtime.resetting = True
    if runtime.task_handle is not None:
        runtime.task_handle.cancel()
        with suppress(asyncio.CancelledError):
            await runtime.task_handle
    runtime.resetting = False


@pytest.fixture
def isolated_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "final-ux-replay.db"
    monkeypatch.setattr(app_config, "DB_PATH", database)
    monkeypatch.setattr(app_db, "DB_PATH", database)
    app_db.init_db()
    return SimulationService(Repository())


def test_candidate_replays_authoritative_inputs_and_only_changes_assignment_policy(
    isolated_service: SimulationService,
) -> None:
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    original_order = _order(topology, 0)

    async def scenario() -> tuple[str, str]:
        baseline = await isolated_service.start(
            topology,
            orders=[deepcopy(original_order)],
            motion_physics_contract=PHYSICS,
            motion_speed_contract=SPEED,
            algorithm_experiment_contract=_contract(topology),
        )
        await _cancel_loop(isolated_service, baseline["runId"])
        isolated_service.stop(baseline["runId"])
        candidate = await isolated_service.create_assignment_candidate(
            baseline["runId"], "hungarian", topology
        )
        await _cancel_loop(isolated_service, candidate["runId"])
        return baseline["runId"], candidate["runId"]

    baseline_id, candidate_id = asyncio.run(scenario())
    baseline = isolated_service.repo.get_run_read_only(baseline_id)["payload"]
    candidate = isolated_service.repo.get_run_read_only(candidate_id)["payload"]
    assert baseline["experimentReplayContract"]["orders"] == [original_order]
    assert candidate["experimentReplayContract"]["orders"] == [original_order]
    assert candidate["id"] != baseline["id"]
    assert baseline["algorithmExperimentContract"]["assignmentPolicy"] == "greedy"
    assert candidate["algorithmExperimentContract"]["assignmentPolicy"] == "hungarian"
    for key in ("scenarioSignature", "initialInventoryHash", "initialVehicleSnapshotHash", "orderSnapshotHash"):
        assert baseline["algorithmExperimentContract"][key] == candidate["algorithmExperimentContract"][key]


def test_order_identity_and_created_time_are_part_of_frozen_signature(
    isolated_service: SimulationService,
) -> None:
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))

    async def scenario() -> tuple[str, str]:
        first = await isolated_service.start(
            topology,
            orders=[_order(topology, 0, created_at="2026-11-17T08:00:00Z")],
            motion_physics_contract=PHYSICS,
            motion_speed_contract=SPEED,
            algorithm_experiment_contract=_contract(topology),
        )
        second_order = _order(topology, 0, created_at="2026-11-17T08:00:01Z")
        second_order["id"] = "ORD-REPLAY-DIFFERENT"
        second = await isolated_service.start(
            topology,
            orders=[second_order],
            motion_physics_contract=PHYSICS,
            motion_speed_contract=SPEED,
            algorithm_experiment_contract=_contract(topology, "hungarian"),
        )
        await _cancel_loop(isolated_service, first["runId"])
        await _cancel_loop(isolated_service, second["runId"])
        return first["runId"], second["runId"]

    first_id, second_id = asyncio.run(scenario())
    first = isolated_service.repo.get_run_read_only(first_id)["payload"]["algorithmExperimentContract"]
    second = isolated_service.repo.get_run_read_only(second_id)["payload"]["algorithmExperimentContract"]
    assert first["orderSnapshotHash"] != second["orderSnapshotHash"]
    assert first["scenarioSignature"] != second["scenarioSignature"]


def test_unregistered_runtime_order_mutation_blocks_replay_and_comparison(
    isolated_service: SimulationService,
) -> None:
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))

    async def scenario() -> tuple[str, str]:
        baseline = await isolated_service.start(
            topology,
            orders=[_order(topology, 0)],
            motion_physics_contract=PHYSICS,
            motion_speed_contract=SPEED,
            algorithm_experiment_contract=_contract(topology),
        )
        candidate = await isolated_service.start(
            topology,
            orders=[_order(topology, 0)],
            motion_physics_contract=PHYSICS,
            motion_speed_contract=SPEED,
            algorithm_experiment_contract=_contract(topology, "hungarian"),
        )
        await _cancel_loop(isolated_service, baseline["runId"])
        await _cancel_loop(isolated_service, candidate["runId"])
        inserted = _order(topology, 1)
        await isolated_service.add_order(baseline["runId"], inserted)
        await isolated_service.cancel_order(baseline["runId"], inserted["id"])
        isolated_service.stop(baseline["runId"])
        isolated_service.stop(candidate["runId"])
        with pytest.raises(ValueError, match="unregistered order"):
            await isolated_service.create_assignment_candidate(
                baseline["runId"], "throughput_aware", topology
            )
        return baseline["runId"], candidate["runId"]

    baseline_id, candidate_id = asyncio.run(scenario())
    comparison = compare_algorithm_experiment_runs(
        isolated_service.repo, baseline_id, candidate_id
    )
    assert comparison is not None
    assert comparison["comparable"] is False
    assert "WORKLOAD_MUTATED_AFTER_START" in comparison["comparisonReasons"]
    saved = isolated_service.repo.get_run_read_only(baseline_id)["payload"]
    assert saved["experimentReplayContract"]["workloadMutationCount"] == 2
    assert saved["experimentReplayContract"]["workloadMutationReasons"] == [
        "ORDER_INSERTED:ORD-REPLAY-1",
        "ORDER_CANCELLED:ORD-REPLAY-1",
    ]


def test_historical_run_without_replay_contract_is_explicitly_ineligible(
    isolated_service: SimulationService,
) -> None:
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))

    async def scenario() -> str:
        started = await isolated_service.start(
            topology,
            orders=[_order(topology, 0)],
            motion_physics_contract=PHYSICS,
            motion_speed_contract=SPEED,
            algorithm_experiment_contract=_contract(topology),
        )
        await _cancel_loop(isolated_service, started["runId"])
        runtime = isolated_service.runtimes[started["runId"]]
        runtime.state.pop("experimentReplayContract")
        isolated_service.stop(started["runId"])
        with pytest.raises(ValueError, match="lacks authoritative frozen replay evidence"):
            await isolated_service.create_assignment_candidate(
                started["runId"], "hungarian", topology
            )
        return started["runId"]

    run_id = asyncio.run(scenario())
    summary = next(
        item for item in isolated_service.repo.list_algorithm_experiment_runs()
        if item["runId"] == run_id
    )
    assert summary["replayEligible"] is False
    assert summary["replayIneligibilityReason"] == "FROZEN_REPLAY_EVIDENCE_MISSING"
