from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.analytics.algorithm_experiment_metrics import (
    build_run_performance_metrics,
    compare_algorithm_experiment_runs,
)
from app.services.motion_speed import edge_distance_meters
from app.services.assignment import assign_tasks
from app.schemas import AlgorithmExperimentContractRequest, MotionSpeedContractRequest
from app.services.motion_speed import freeze_motion_speed_contract
from app.services.simulation import RunRuntime, SimulationService
from app.services.unified_transfer import build_playback_route


PHYSICS = {
    "physicsContractVersion": "motion-physics-v1",
    "coordinateContractVersion": "topology-grid-v1",
    "metersPerCell": 0.8,
    "shuttleLevelHeightMeters": 3.0,
    "floorHeightMeters": 4.0,
    "source": "USER_DEFINED_SIMULATION",
    "calibrationStatus": "UNVERIFIED",
}


def speed_contract(speed: float = 0.4) -> dict:
    return {
        "speedContractVersion": "motion-speed-v1",
        "simTickSeconds": 0.5,
        "ctuSpeedMetersPerSecond": speed,
        "shuttleSpeedMetersPerSecond": speed,
        "liftSpeedMetersPerSecond": 0.5,
        "source": "USER_DEFINED_SIMULATION",
        "calibrationStatus": "UNVERIFIED",
    }


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, "1.0"])
def test_speed_contract_rejects_non_strict_values(bad: object) -> None:
    payload = speed_contract()
    payload["ctuSpeedMetersPerSecond"] = bad
    with pytest.raises(ValidationError):
        MotionSpeedContractRequest.model_validate(payload)


def test_speed_contract_rejects_extra_and_requires_physics() -> None:
    with pytest.raises(ValidationError):
        MotionSpeedContractRequest.model_validate({**speed_contract(), "unknown": 1})
    with pytest.raises(ValueError, match="requires motionPhysicsContract"):
        freeze_motion_speed_contract(speed_contract(), None)


def test_speed_contract_enforces_discrete_scheduler_limit() -> None:
    with pytest.raises(ValueError, match="safe discrete scheduler limit"):
        freeze_motion_speed_contract(speed_contract(2.0), PHYSICS)
    frozen = freeze_motion_speed_contract(speed_contract(1.0), PHYSICS)
    assert frozen and frozen["schedulerLimits"]["maxCommittedEdgesPerRobotPerTick"] == 1


def make_runtime(speed: float) -> tuple[SimulationService, RunRuntime, dict]:
    config = {
        "motionPhysicsContract": deepcopy(PHYSICS),
        "motionSpeedContract": freeze_motion_speed_contract(speed_contract(speed), PHYSICS),
    }
    state = {
        "tick": 0,
        "performanceCounters": {},
        "robots": [],
        "tasks": [],
    }
    robot = {
        "id": "CTU-1",
        "type": "ctu",
        "currentCellId": "cell-r0-c0",
        "currentRow": 0,
        "currentCol": 0,
        "currentLayer": 1,
        "motionCreditMeters": 0.0,
        "motionEdgeKey": None,
        "motionCreditTick": -1,
        "physicalTravelWaitTicks": 0,
        "status": "moving",
    }
    runtime = RunRuntime("run-speed", {"id": "topology-test"}, config, state, {})
    return SimulationService(object()), runtime, robot


def ticks_until_edge_ready(speed: float, playback: int) -> tuple[int, int]:
    service, runtime, robot = make_runtime(speed)
    runtime.speed_multiplier = playback
    for tick in range(1, 20):
        runtime.state["tick"] = tick
        if service._physical_movement_ready(runtime, robot, "cell-r0-c1", 1.0):
            service._consume_motion_credit(robot)
            return tick, int(robot["physicalTravelWaitTicks"])
    raise AssertionError("edge never became ready")


def test_low_speed_uses_more_simulated_ticks_without_fake_blocking() -> None:
    slow_tick, slow_wait = ticks_until_edge_ready(0.4, 1)
    fast_tick, fast_wait = ticks_until_edge_ready(0.8, 1)
    assert (slow_tick, fast_tick) == (4, 2)
    assert slow_wait > fast_wait


def test_playback_multiplier_does_not_change_physical_edge_tick() -> None:
    assert [ticks_until_edge_ready(0.8, value)[0] for value in (1, 2, 4)] == [2, 2, 2]


def test_distance_credit_is_tick_scoped_bounded_and_reset_on_route_change() -> None:
    service, runtime, robot = make_runtime(1.0)
    runtime.state["tick"] = 1
    assert service._physical_movement_ready(runtime, robot, "cell-r0-c1", 1.0) is False
    first_credit = robot["motionCreditMeters"]

    # Rechecking during a pause/no-new-tick cannot manufacture physical time.
    for _ in range(5):
        assert service._physical_movement_ready(runtime, robot, "cell-r0-c1", 1.0) is False
    assert robot["motionCreditMeters"] == pytest.approx(first_credit)

    runtime.state["tick"] = 2
    assert service._physical_movement_ready(runtime, robot, "cell-r0-c1", 1.0) is True
    ready_credit = robot["motionCreditMeters"]
    for tick in range(3, 9):
        runtime.state["tick"] = tick
        assert service._physical_movement_ready(runtime, robot, "cell-r0-c1", 1.0) is True
    assert robot["motionCreditMeters"] == pytest.approx(ready_credit)

    # A reroute names a different edge and discards credit for the old target.
    runtime.state["tick"] = 9
    assert service._physical_movement_ready(runtime, robot, "cell-r0-c2", 1.0) is False
    assert robot["motionCreditMeters"] == pytest.approx(0.5)
    assert robot["status"] == "moving"
    assert int(robot.get("blockedWaitTicks") or 0) == 0


def test_greedy_and_hungarian_accept_the_same_minimal_multi_task_scope() -> None:
    topology = {
        "id": "assignment-grid",
        "grid": {"rows": 2, "cols": 3},
        "zones": [],
        "nodes": [],
        "cells": [
            {
                "id": f"cell-r0-c{col}",
                "floor": "1F",
                "row": 0,
                "col": col,
                "type": "road",
                "walkable": True,
                "cost": 1.0,
            }
            for col in range(3)
        ],
    }
    robots = [
        {"id": "ctu-left", "type": "ctu", "status": "idle", "currentCellId": "cell-r0-c0", "battery": 100},
        {"id": "ctu-right", "type": "ctu", "status": "idle", "currentCellId": "cell-r0-c2", "battery": 100},
    ]
    tasks = [
        {"id": "task-left", "robotType": "ctu", "status": "pending", "sourceCellId": "cell-r0-c0"},
        {"id": "task-right", "robotType": "ctu", "status": "pending", "sourceCellId": "cell-r0-c2"},
    ]
    expected = {("ctu-left", "task-left"), ("ctu-right", "task-right")}
    assert set(assign_tasks("greedy", robots, tasks, topology, "astar")) == expected
    assert set(assign_tasks("hungarian", robots, tasks, topology, "astar")) == expected


def test_shuttle_track_edge_uses_real_grid_span() -> None:
    assert edge_distance_meters(PHYSICS, 100, 7, 1, 100, 9, 1) == pytest.approx(1.6)

    observed = []
    for speed in (0.4, 0.8):
        service, runtime, robot = make_runtime(speed)
        robot["type"] = "shuttle"
        for tick in range(1, 12):
            runtime.state["tick"] = tick
            if service._physical_movement_ready(runtime, robot, "cell-r0-c2", 1.0):
                observed.append(tick)
                break
    assert observed == [8, 4]


def test_lift_edge_uses_level_height_and_speed_credit() -> None:
    service, runtime, robot = make_runtime(0.8)
    robot["type"] = "shuttle"
    robot["currentLayer"] = 1
    runtime.config["motionSpeedContract"]["liftSpeedMetersPerSecond"] = 1.0
    for tick in range(1, 6):
        runtime.state["tick"] = tick
        assert service._physical_movement_ready(
            runtime, robot, "cell-r0-c0", 2.0
        ) is False
    runtime.state["tick"] = 6
    assert service._physical_movement_ready(runtime, robot, "cell-r0-c0", 2.0) is True


def test_lift_route_uses_frozen_vertical_speed() -> None:
    plan = {
        "path_nodes": [
            {"row": 1, "col": 1, "shuttle_level": 1},
            {"row": 1, "col": 1, "shuttle_level": 2},
        ],
        "path_edges": [{
            "edge_type": "lift",
            "start_seconds": 0.0,
            "motion_start_seconds": 0.0,
            "motion_end_seconds": 1.0,
            "end_seconds": 1.0,
        }],
    }
    _paths, slow_layers, _stages = build_playback_route(
        plan,
        0.5,
        lift_speed_meters_per_second=0.5,
        shuttle_level_height_meters=3.0,
    )
    _paths, fast_layers, _stages = build_playback_route(
        plan,
        0.5,
        lift_speed_meters_per_second=1.0,
        shuttle_level_height_meters=3.0,
    )
    assert len(slow_layers) == 15
    assert len(fast_layers) == 9


def test_experiment_contract_is_strict_and_declares_one_dimension() -> None:
    result = AlgorithmExperimentContractRequest.model_validate({
        "experimentContractVersion": "algorithm-experiment-v1",
        "scenarioId": "scenario-1",
        "scenarioVersion": "1",
        "seed": 42,
        "topologyRevision": "2.0.0:test",
        "safetyLimitTicks": 200,
        "pathPolicy": "astar",
        "assignmentPolicy": "greedy",
        "variableDimension": "pathPolicy",
        "algorithmVersion": "routing-v1",
    })
    assert result.variableDimension == "pathPolicy"


class FakeComparisonRepo:
    def __init__(self, states: dict[str, dict]):
        self.states = states

    def get_run_read_only(self, run_id: str):
        state = self.states.get(run_id)
        return {"id": run_id, "status": state["status"], "payload": state} if state else None

    def get_motion_distance_metrics(self, run_id: str):
        state = self.states[run_id]
        return {
            "dataQuality": "COMPLETE",
            "totalMetrics": {
                "totalHorizontalMeters": {"value": state["distance"]},
                "ctuHorizontalMeters": {"value": state["distance"]},
                "shuttleHorizontalMeters": {"value": 0.0},
                "shuttleLiftVerticalMeters": {"value": 0.0},
            },
        }

    def get_deadlock_lifecycle_metrics(self, _run_id: str):
        return {"dataQuality": "COMPLETE", "metrics": [
            {"metricId": "PERSISTENT_WAIT_CYCLE_COUNT", "value": 0},
            {"metricId": "CONFIRMED_RESOLVED_EPISODE_COUNT", "value": 0},
        ]}


def test_effective_speed_uses_full_physical_travel_time() -> None:
    state = experiment_state("astar", 0.8)
    state["motionSpeedContract"] = {
        "simTickSeconds": 0.5,
        "ctuSpeedMetersPerSecond": 0.4,
    }
    state["performanceCounters"] = {
        "movementRobotTicks": 1,
        "physicalTravelWaitRobotTicks": 3,
        "blockedRobotTicks": 2,
    }
    repo = FakeComparisonRepo({"a": state})
    result = build_run_performance_metrics(repo, "a")
    assert result is not None
    assert result["movementRobotSeconds"] == pytest.approx(2.0)
    assert result["waitingRobotSeconds"] == pytest.approx(1.0)
    assert result["averageEffectiveHorizontalSpeedMetersPerSecond"] == pytest.approx(0.4)


def experiment_state(policy: str, distance: float, signature: str = "same") -> dict:
    return {
        "status": "completed",
        "stopReason": None,
        "simulatedSeconds": 10.0,
        "motionSpeedContract": {"simTickSeconds": 0.5},
        "algorithmExperimentContract": {
            "scenarioSignature": signature,
            "variableDimension": "pathPolicy",
            "pathPolicy": policy,
            "assignmentPolicy": "greedy",
        },
        "tasks": [{"id": "task-1", "status": "completed", "createdTick": 0, "completedTick": 20}],
        "planningEvidence": [{"planId": f"plan-{policy}", "planningDurationMs": 1.0}],
        "performanceCounters": {"blockedRobotTicks": 0},
        "distance": distance,
    }


def test_comparison_calculates_only_for_same_complete_scenario() -> None:
    repo = FakeComparisonRepo({
        "a": experiment_state("astar", 10.0),
        "b": experiment_state("dijkstra", 8.0),
    })
    result = compare_algorithm_experiment_runs(repo, "a", "b")
    assert result and result["comparable"] is True
    assert result["distanceSavedMeters"] == pytest.approx(2.0)
    assert result["distanceImprovementPercent"] == pytest.approx(20.0)


def test_comparison_hides_rates_when_scenario_differs_or_baseline_is_zero() -> None:
    repo = FakeComparisonRepo({
        "a": experiment_state("astar", 10.0, "one"),
        "b": experiment_state("dijkstra", 8.0, "two"),
    })
    result = compare_algorithm_experiment_runs(repo, "a", "b")
    assert result and result["comparable"] is False
    assert result["distanceImprovementPercent"] is None

    zero_repo = FakeComparisonRepo({
        "a": experiment_state("astar", 0.0),
        "b": experiment_state("dijkstra", 0.0),
    })
    zero = compare_algorithm_experiment_runs(zero_repo, "a", "b")
    assert zero and zero["comparable"] is True
    assert zero["distanceImprovementPercent"] is None
