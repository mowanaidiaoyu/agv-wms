from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import AlgorithmExperimentContractRequest
from app.services.assignment import assign_tasks, greedy_assign
from app.services.throughput_aware_assignment import ALGORITHM_VERSION, frozen_weights, throughput_aware_assign


def _topology(rows: int = 3, cols: int = 7) -> dict:
    return {
        "id": "throughput-aware-unit",
        "grid": {"rows": rows, "cols": cols},
        "meta": {"floors": ["1F"]},
        "nodes": [],
        "cells": [],
        "zones": [],
        "edges": [],
    }


def _robot(robot_id: str, col: int, home_col: int | None = None) -> dict:
    return {
        "id": robot_id,
        "type": "ctu",
        "status": "idle",
        "failed": False,
        "battery": 100,
        "currentCellId": f"cell-r0-c{col}",
        "homeCellId": f"cell-r0-c{col if home_col is None else home_col}",
        "currentLayer": 1,
        "capacity": 5,
    }


def _task(task_id: str, source_col: int, delivery_col: int, **extra: object) -> dict:
    return {
        "id": task_id,
        "robotType": "ctu",
        "status": "pending",
        "sourceCellId": f"cell-r0-c{source_col}",
        "sourceLayer": 1,
        "deliveryCellId": f"cell-r0-c{delivery_col}",
        "lines": [{"cellId": f"cell-r0-c{source_col}"}],
        "priority": 2,
        "urgent": False,
        "createdTick": 0,
        **extra,
    }


def _config(**weights: float) -> dict:
    return {
        "motionPhysicsContract": {"metersPerCell": 0.8, "shuttleLevelHeightMeters": 3.0},
        "motionSpeedContract": {
            "simTickSeconds": 0.6,
            "ctuSpeedMetersPerSecond": 1.0,
            "shuttleSpeedMetersPerSecond": 1.2,
            "liftSpeedMetersPerSecond": 0.5,
        },
        "throughputAwareWeights": {
            "contractVersion": ALGORITHM_VERSION,
            "travelTimeWeight": 1.0,
            "congestionWeight": 0.65,
            "ageWeight": 0.2,
            "returnWeight": 0.25,
            "handlingTicks": 2.0,
            "maxCongestionPenaltyTicks": 30.0,
            "maxAgeBonusTicks": 60.0,
            **weights,
        },
    }


def test_throughput_aware_is_deterministic_legal_and_can_differ_from_greedy() -> None:
    topology = _topology()
    robots = [_robot("r1", 0, 6), _robot("r2", 6, 0)]
    tasks = [_task("t1", 1, 0), _task("t2", 5, 6)]
    state = {"tick": 10, "robots": robots, "tasks": tasks, "queues": {}}

    greedy = greedy_assign(robots, tasks, topology, "astar")
    first, evidence = throughput_aware_assign(
        robots,
        tasks,
        topology,
        "astar",
        state,
        _config(returnWeight=2.0),
    )
    second, _ = throughput_aware_assign(
        list(reversed(robots)),
        list(reversed(tasks)),
        topology,
        "astar",
        state,
        _config(returnWeight=2.0),
    )

    assert set(greedy) == {("r1", "t1"), ("r2", "t2")}
    assert set(first) == {("r1", "t2"), ("r2", "t1")}
    assert first == second
    assert len({robot_id for robot_id, _ in first}) == len(first)
    assert len({task_id for _, task_id in first}) == len(first)
    assert all(item["algorithmVersion"] == ALGORITHM_VERSION for item in evidence)


def test_throughput_aware_preserves_requested_vehicle_dependencies_capacity_and_reachability() -> None:
    topology = _topology()
    robots = [_robot("r1", 0), _robot("r2", 6)]
    dependency = _task("dependency", 2, 3)
    tasks = [
        dependency,
        _task("requested", 5, 4, requestedVehicleId="r2"),
        _task("blocked", 1, 2, blockedBy=["dependency"]),
        _task("unreachable", 99, 1),
        _task("over-capacity", 3, 4, lines=[{"cellId": "cell-r0-c3"}] * 6),
    ]
    state = {"tick": 1, "robots": robots, "tasks": tasks, "queues": {}}

    matches, _ = throughput_aware_assign(robots, tasks, topology, "astar", state, _config())

    assert ("r2", "requested") in matches
    assert all(task_id not in {"blocked", "unreachable", "over-capacity"} for _, task_id in matches)


def test_task_age_is_observable_bounded_and_prevents_indefinite_starvation() -> None:
    topology = _topology()
    robots = [_robot("r1", 0)]
    old = _task("old", 4, 5, createdTick=0)
    new = _task("new", 1, 2, createdTick=100)
    state = {"tick": 100, "robots": robots, "tasks": [old, new], "queues": {}}
    matches, evidence = throughput_aware_assign(
        robots,
        [old, new],
        topology,
        "astar",
        state,
        _config(ageWeight=1.0, maxAgeBonusTicks=60.0, returnWeight=0.0),
    )
    assert matches == [("r1", "old")]
    assert evidence[0]["taskAgeTicks"] == 100
    assert evidence[0]["ageBonusTicks"] == 60.0
    assert evidence[0]["observableAtTick"] == 100


def test_policy_is_explicit_and_unknown_values_do_not_fall_back_to_greedy() -> None:
    with pytest.raises(ValueError, match="Unsupported assignment policy"):
        assign_tasks("typo", [], [], _topology(), "astar")
    with pytest.raises(ValueError, match="requires frozen runtime"):
        assign_tasks("throughput_aware", [], [], _topology(), "astar")


def test_contract_accepts_throughput_aware_defaults_and_rejects_unbounded_weights() -> None:
    contract = AlgorithmExperimentContractRequest.model_validate(
        {
            "experimentContractVersion": "algorithm-experiment-v1",
            "scenarioId": "throughput-aware",
            "scenarioVersion": "1",
            "seed": 42,
            "topologyRevision": "revision",
            "safetyLimitTicks": 600,
            "observationTicks": 600,
            "pathPolicy": "astar",
            "assignmentPolicy": "throughput_aware",
            "variableDimension": "assignmentPolicy",
            "algorithmVersion": ALGORITHM_VERSION,
        }
    )
    assert contract.assignmentPolicy == "throughput_aware"
    with pytest.raises(ValidationError):
        AlgorithmExperimentContractRequest.model_validate(
            {
                **contract.model_dump(),
                "throughputAwareWeights": {
                    "contractVersion": ALGORITHM_VERSION,
                    "travelTimeWeight": 11.0,
                },
            }
        )
    with pytest.raises(ValueError, match="between 0 and 10"):
        frozen_weights({"travelTimeWeight": 11.0})
    with pytest.raises(ValueError, match="Unsupported throughput-aware weight fields"):
        frozen_weights({"futureInformationWeight": 1.0})
