from __future__ import annotations

from copy import deepcopy
from typing import Any


DEVICE_NODE_TO_ROBOT_TYPE = {
    "ctu_robot": "ctu",
    "shuttle_robot": "shuttle",
}


def _default_speed(robot_type: str) -> float:
    return 1.4 if robot_type == "ctu" else 1.1


def sync_scenario_with_topology_payload(topology: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    synced = deepcopy(scenario)
    device_nodes = [node for node in topology.get("nodes", []) if node.get("type") in DEVICE_NODE_TO_ROBOT_TYPE]
    existing_robots = {
        robot.get("id"): robot
        for robot in scenario.get("robots", [])
        if robot.get("id")
    }

    robots: list[dict[str, Any]] = []
    for node in device_nodes:
        robot_id = node["id"]
        robot_type = DEVICE_NODE_TO_ROBOT_TYPE[node["type"]]
        existing = existing_robots.get(robot_id, {})
        robots.append(
            {
                "id": robot_id,
                "type": robot_type,
                "startNode": robot_id,
                "speed": existing.get("speed", _default_speed(robot_type)),
                "battery": existing.get("battery", 100),
                "enabled": existing.get("enabled", True),
            }
        )

    robot_ids = {robot["id"] for robot in robots}
    fault_injection = deepcopy(synced.get("faultInjection", {}))
    robot_failure_id = fault_injection.get("robotFailureId")
    if robot_failure_id not in robot_ids:
        fault_injection["robotFailureId"] = robots[0]["id"] if robots else None

    synced["topologyId"] = topology["id"]
    synced["robots"] = robots
    synced["faultInjection"] = fault_injection
    return synced
