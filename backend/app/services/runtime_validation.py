from __future__ import annotations

from typing import Any

from .pathfinding import normalize_floor


ROBOT_NODE_TYPES = {"ctu": "ctu_robot", "shuttle": "shuttle_robot"}


def validate_runtime_inputs(topology: dict[str, Any], scenario: dict[str, Any]) -> list[str]:
    """Validate only invariants required to start the current simulation runtime."""
    errors: list[str] = []
    if scenario.get("topologyId") != topology.get("id"):
        errors.append("Scenario topologyId does not match the selected topology")

    cells = {cell.get("id"): cell for cell in topology.get("cells", []) if cell.get("id")}
    nodes = {node.get("id"): node for node in topology.get("nodes", []) if node.get("id")}
    positions = {
        (normalize_floor(cell.get("floor")), cell.get("row"), cell.get("col")): cell
        for cell in cells.values()
    }

    def traversable_cell_for(node: dict[str, Any]) -> dict[str, Any] | None:
        cell = positions.get((normalize_floor(node.get("floor")), node.get("row"), node.get("col")))
        if not cell or not cell.get("walkable", True) or cell.get("type") == "blocked":
            return None
        return cell

    for resource in topology.get("resources", []):
        resource_id = resource.get("id", "<unnamed>")
        for cell_id in resource.get("cellIds", []):
            if cell_id not in cells:
                errors.append(f"Resource {resource_id} references missing cell {cell_id}")
        for node_id in resource.get("nodeIds", []):
            if node_id not in nodes:
                errors.append(f"Resource {resource_id} references missing node {node_id}")

    enabled_robots = [robot for robot in scenario.get("robots", []) if robot.get("enabled", True)]
    if not enabled_robots:
        errors.append("Scenario has no enabled robots")
    for robot in enabled_robots:
        robot_id = robot.get("id", "<unnamed>")
        start_node_id = robot.get("startNode")
        start_node = nodes.get(start_node_id)
        if start_node is None:
            errors.append(f"Robot {robot_id} references missing start node {start_node_id}")
            continue
        expected_type = ROBOT_NODE_TYPES.get(robot.get("type"))
        if expected_type and start_node.get("type") != expected_type:
            errors.append(
                f"Robot {robot_id} type {robot.get('type')} requires a {expected_type} start node"
            )
        if traversable_cell_for(start_node) is None:
            errors.append(f"Robot {robot_id} start node {start_node_id} is not on a traversable cell")

    task_nodes = [
        node for node in nodes.values()
        if node.get("type") in {"storage", "buffer", "station", "entry", "conveyor_gate"}
    ]
    for node in task_nodes:
        if traversable_cell_for(node) is None:
            errors.append(f"Task node {node.get('id')} is not on a traversable cell")

    if enabled_robots and not any(node.get("type") in {"storage", "buffer"} for node in task_nodes):
        errors.append("Topology has no storage or buffer node for generated tasks")
    if enabled_robots and not any(node.get("type") == "station" for node in task_nodes):
        errors.append("Topology has no station node for generated tasks")

    return errors
