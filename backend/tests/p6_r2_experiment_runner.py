"""Opt-in P6-R2 formal experiment runner.

This module is intentionally not collected by pytest. It requires an explicit
test-mode database path and writes only the requested isolated experiment DB
and JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core import config as app_config
from app.core import db as app_db
from app.core.repository import Repository
from app.services.goods import build_inventory
from app.services.simulation import SimulationService
import test_throughput_experiment_runtime as experiment


DEFAULT_POLICIES = ("greedy", "hungarian", "throughput_aware")


def _metric_value(payload: dict[str, Any] | None, metric_id: str) -> float | None:
    metric = ((payload or {}).get("totalMetrics") or {}).get(metric_id)
    value = metric.get("value") if isinstance(metric, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _deadlock_value(payload: dict[str, Any] | None, metric_id: str) -> float | None:
    for metric in (payload or {}).get("metrics") or []:
        if isinstance(metric, dict) and metric.get("metricId") == metric_id:
            value = metric.get("value")
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return None


async def _run(
    ticks: int,
    seed: int,
    repo: Repository,
    topology: dict[str, Any],
    policies: list[str],
    weights: dict[str, Any] | None,
    initial_orders: list[dict[str, Any]],
    scheduled_orders: list[tuple[int, dict[str, Any]]],
    arrival_hash: str,
) -> list[str]:
    experiment.OBSERVATION_TICKS = ticks
    service = SimulationService(repo)
    run_ids: list[str] = []
    for policy in policies:
        run_ids.append(
            await experiment._run_fixed_window(
                service,
                topology,
                path_policy="astar",
                assignment_policy=policy,
                variable_dimension="assignmentPolicy",
                seed=seed,
                throughput_aware_weights=weights if policy == "throughput_aware" else None,
                initial_orders=initial_orders,
                scheduled_orders=scheduled_orders,
                order_arrival_plan_hash=arrival_hash,
            )
        )
    return run_ids


def _summary(repo: Repository, run_id: str) -> dict[str, Any]:
    saved = repo.get_run_read_only(run_id)
    assert saved is not None
    state = saved["payload"]
    throughput = repo.get_throughput_metrics(run_id)
    motion = repo.get_motion_distance_metrics(run_id)
    deadlock = repo.get_deadlock_lifecycle_metrics(run_id)
    tasks = [task for task in state.get("tasks", []) if isinstance(task, dict) and not task.get("parentId")]
    assignment_waits = [
        int(task["startedTick"]) - int(task.get("createdTick") or 0)
        for task in tasks
        if isinstance(task.get("startedTick"), int)
    ]
    contract = state.get("algorithmExperimentContract") or {}
    counters = state.get("performanceCounters") or {}
    return {
        "runId": run_id,
        "assignmentPolicy": contract.get("assignmentPolicy"),
        "assignmentAlgorithmVersion": contract.get("assignmentAlgorithmVersion"),
        "scenarioSignature": contract.get("scenarioSignature"),
        "observationTick": throughput.get("observationTick"),
        "observationSeconds": throughput.get("observationSeconds"),
        "deliveredPieces": throughput.get("deliveredPieces"),
        "deliveredBoxes": throughput.get("deliveredBoxes"),
        "throughputPiecesPerSimulatedHour": throughput.get("throughputPiecesPerSimulatedHour"),
        "completedOrderCount": throughput.get("completedOrderCount"),
        "blockedRobotTicks": throughput.get("blockedRobotTicks"),
        "physicalTravelWaitRobotTicks": throughput.get("physicalTravelWaitRobotTicks"),
        "movementRobotTicks": counters.get("movementRobotTicks"),
        "averageFirstAssignmentWaitTicks": (
            sum(assignment_waits) / len(assignment_waits) if assignment_waits else None
        ),
        "maxFirstAssignmentWaitTicks": max(assignment_waits) if assignment_waits else None,
        "unassignedRootTaskCount": sum(1 for task in tasks if task.get("status") == "pending"),
        "emptyTravelMeters": None,
        "emptyTravelReason": "AUTHORITATIVE_LOADED_STATE_DISTANCE_NOT_COLLECTED",
        "totalHorizontalMeters": _metric_value(motion, "totalHorizontalMeters"),
        "persistentCycleEpisodeCount": _deadlock_value(deadlock, "PERSISTENT_WAIT_CYCLE_COUNT"),
        "throughputDataQuality": throughput.get("dataQuality"),
        "motionDataQuality": (motion or {}).get("dataQuality"),
        "acceptanceEligible": throughput.get("acceptanceEligible"),
        "deliveryEvidence": throughput.get("evidenceSummary"),
        "assignmentDecisionCount": len(state.get("throughputAwareAssignmentEvidence") or []),
        "inventoryNonNegative": all(int(item.get("qty") or 0) >= 0 for item in state.get("inventory", [])),
    }


def _clustered_orders(topology: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = {str(node.get("id")): node for node in topology.get("nodes", [])}
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    seen: set[str] = set()
    for slot in build_inventory(topology):
        sku_id = str(slot.get("skuId") or "")
        node = nodes.get(str(slot.get("nodeId") or ""))
        if not sku_id or sku_id in seen or node is None or int(slot.get("caseCnt") or 0) <= 1:
            continue
        seen.add(sku_id)
        distance = abs(int(node.get("row") or 0) - 106) + abs(int(node.get("col") or 0) - 60)
        candidates.append((distance, sku_id, slot))
    selected = [item[2] for item in sorted(candidates, key=lambda item: (item[0], item[1]))[:28]]
    if len(selected) != 28:
        raise RuntimeError("The port-competition scenario requires 28 unique eligible SKUs")
    orders: list[dict[str, Any]] = []
    for order_index in range(4):
        lines = [
            {
                "id": f"cluster-line-{order_index}-{line_index}",
                "skuId": slot["skuId"],
                "skuName": slot["skuName"],
                "spec": slot.get("spec"),
                "quantity": 1,
                "qtyMode": "pieces",
            }
            for line_index, slot in enumerate(selected[order_index * 7:(order_index + 1) * 7])
        ]
        orders.append(
            {
                "id": f"ORD-P6-CLUSTER-{order_index}",
                "priority": "normal",
                "dispatchMode": "parallel",
                "maxParallelCtu": 7,
                "targetStation": "AUTO",
                "createdAt": "2026-11-17T00:00:00Z",
                "lines": lines,
            }
        )
    return orders


def _arrival_plan(
    topology: dict[str, Any], scenario: str
) -> tuple[list[dict[str, Any]], list[tuple[int, dict[str, Any]]], str]:
    if scenario == "port_competition":
        orders = _clustered_orders(topology)
        schedule = [(0, order) for order in orders]
        initial, scheduled = orders, []
    elif scenario == "continuous_arrivals":
        orders = experiment._orders(topology)
        ticks = (0, 150, 300, 450)
        schedule = list(zip(ticks, orders))
        initial, scheduled = [orders[0]], list(zip(ticks[1:], orders[1:]))
    else:
        orders = experiment._orders(topology)
        schedule = [(0, order) for order in orders]
        initial, scheduled = orders, []
    encoded = json.dumps(
        schedule,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return initial, scheduled, hashlib.sha256(encoded).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policies", nargs="+", choices=DEFAULT_POLICIES, default=list(DEFAULT_POLICIES))
    parser.add_argument("--weights-json")
    parser.add_argument("--ablation", choices=("time_only", "time_congestion", "full"))
    parser.add_argument(
        "--scenario",
        choices=("dispersed", "port_competition", "continuous_arrivals"),
        default="dispersed",
    )
    args = parser.parse_args()
    if os.environ.get("AGV_WMS_TEST_MODE") != "1":
        raise RuntimeError("AGV_WMS_TEST_MODE=1 is required")
    configured = Path(os.environ.get("AGV_WMS_DB_PATH", "")).resolve()
    if not os.environ.get("AGV_WMS_DB_PATH") or configured == app_config.DEFAULT_DB_PATH.resolve():
        raise RuntimeError("A unique non-default AGV_WMS_DB_PATH is required")
    if app_config.DB_PATH.resolve() != configured or app_db.DB_PATH.resolve() != configured:
        raise RuntimeError("config.DB_PATH and db.DB_PATH must match the isolated environment path")
    app_db.init_db()
    repo = Repository()
    topology = json.loads(experiment.TOPOLOGY_PATH.read_text(encoding="utf-8"))
    ablation_weights = {
        "time_only": {"congestionWeight": 0.0, "ageWeight": 0.0, "returnWeight": 0.0},
        "time_congestion": {"congestionWeight": 0.65, "ageWeight": 0.0, "returnWeight": 0.0},
        "full": None,
    }
    if args.weights_json and args.ablation:
        raise ValueError("Use either --weights-json or --ablation, not both")
    weights = (
        json.loads(args.weights_json)
        if args.weights_json
        else ablation_weights.get(args.ablation)
    )
    if weights is not None and not isinstance(weights, dict):
        raise ValueError("--weights-json must decode to an object")
    initial_orders, scheduled_orders, arrival_hash = _arrival_plan(topology, args.scenario)
    run_ids = asyncio.run(
        _run(
            args.ticks,
            args.seed,
            repo,
            topology,
            args.policies,
            weights,
            initial_orders,
            scheduled_orders,
            arrival_hash,
        )
    )
    report = {
        "contractVersion": "p6-r2-experiment-report-v1",
        "databasePath": str(configured),
        "ticks": args.ticks,
        "seed": args.seed,
        "scenario": args.scenario,
        "orderArrivalPlanHash": arrival_hash,
        "weights": weights,
        "policies": [_summary(repo, run_id) for run_id in run_ids],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
