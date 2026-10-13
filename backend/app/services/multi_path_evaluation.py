from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from .multi_path_demo import plan_multi_paths
from .path_demo import PathDemoError


ALGORITHMS = ("astar", "dijkstra")


def _sum_conflicts(conflicts: dict[str, int], prefix: str) -> int:
    return sum(value for name, value in conflicts.items() if name.startswith(prefix))


def _metrics(plan: dict[str, Any]) -> dict[str, Any]:
    vehicles = plan["vehicles"]
    total_wait = sum(vehicle["wait_time_seconds"] for vehicle in vehicles)
    return {
        "total_path_distance": sum(vehicle["total_distance"] for vehicle in vehicles),
        "makespan_seconds": plan["makespan_seconds"],
        "total_wait_time_seconds": total_wait,
        "average_wait_time_seconds": total_wait / len(vehicles),
        "detected_conflicts": _sum_conflicts(plan["conflicts"], "detected_"),
        "resolved_conflicts": _sum_conflicts(plan["conflicts"], "resolved_"),
        "remaining_conflicts": _sum_conflicts(plan["conflicts"], "remaining_"),
        "avoidance_count": plan["avoidance_count"],
        "vehicles": [
            {
                "vehicle_id": vehicle["vehicle_id"],
                "total_distance": vehicle["total_distance"],
                "estimated_duration_seconds": vehicle["estimated_duration_seconds"],
                "wait_time_seconds": vehicle["wait_time_seconds"],
                "avoidance_count": vehicle["avoidance_count"],
                "visited_states": vehicle["visited_states"],
            }
            for vehicle in vehicles
        ],
    }


def evaluate_multi_path_algorithms(
    *,
    topology: dict[str, Any],
    topology_id: str,
    topology_name: str,
    topology_version: str,
    mode: str,
    scenario_id: str | None,
    vehicles: list[dict[str, Any]],
    planning_config: dict[str, Any],
) -> dict[str, Any]:
    frozen_topology = deepcopy(topology)
    frozen_vehicles = deepcopy(vehicles)
    frozen_config = deepcopy(planning_config)
    results: list[dict[str, Any]] = []

    for algorithm in ALGORITHMS:
        started_at = perf_counter()
        try:
            planned = plan_multi_paths(
                frozen_topology,
                deepcopy(frozen_vehicles),
                algorithm,
                deepcopy(frozen_config),
            )
            plan = {
                "topology_id": topology_id,
                "topology_name": topology_name,
                "topology_version": topology_version,
                **planned,
            }
            results.append(
                {
                    "algorithm": algorithm,
                    "planning_status": "success",
                    "error_reason": None,
                    "planning_time_ms": planned["planning_time_ms"],
                    "metrics": _metrics(planned),
                    "plan": plan,
                }
            )
        except PathDemoError as exc:
            results.append(
                {
                    "algorithm": algorithm,
                    "planning_status": "failed",
                    "error_reason": exc.detail,
                    "planning_time_ms": (perf_counter() - started_at) * 1000,
                    "metrics": None,
                    "plan": None,
                }
            )

    return {
        "experiment_id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_snapshot": {
            "topology_id": topology_id,
            "topology_name": topology_name,
            "topology_version": topology_version,
            "mode": mode,
            "scenario_id": scenario_id,
            "vehicles": frozen_vehicles,
            "planning_config": frozen_config,
            "algorithms": list(ALGORITHMS),
        },
        "results": results,
    }
