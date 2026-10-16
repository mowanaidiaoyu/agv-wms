from __future__ import annotations

from copy import deepcopy

from app.services.pathfinding import shortest_path, shortest_path_result
from app.services.runtime_validation import validate_runtime_inputs
from app.services.topology_fingerprint import topology_content_sha256


def _topology() -> dict:
    return {
        "id": "integration-safety",
        "name": "Integration safety",
        "meta": {"version": "1", "viewport": {"x": 0, "y": 0, "zoom": 1}},
        "cells": [
            {"id": "cell-r0-c0", "floor": "1F", "row": 0, "col": 0, "type": "road", "walkable": True, "cost": 1},
            {"id": "cell-r0-c1", "floor": "1F", "row": 0, "col": 1, "type": "road", "walkable": True, "cost": 2},
            {"id": "cell-r0-c2", "floor": "1F", "row": 0, "col": 2, "type": "road", "walkable": True, "cost": 1},
            {"id": "cell-r1-c0", "floor": "1F", "row": 1, "col": 0, "type": "road", "walkable": True, "cost": 1},
            {"id": "cell-r1-c1", "floor": "1F", "row": 1, "col": 1, "type": "road", "walkable": True, "cost": 1},
            {"id": "cell-r1-c2", "floor": "1F", "row": 1, "col": 2, "type": "road", "walkable": True, "cost": 1},
        ],
        "nodes": [
            {"id": "robot", "type": "ctu_robot", "floor": "1F", "row": 0, "col": 0},
            {"id": "storage", "type": "storage", "floor": "1F", "row": 0, "col": 1},
            {"id": "station", "type": "station", "floor": "1F", "row": 0, "col": 2},
        ],
        "resources": [{"id": "station-resource", "cellIds": ["cell-r0-c2"], "nodeIds": ["station"]}],
    }


def _scenario() -> dict:
    return {
        "id": "scenario",
        "topologyId": "integration-safety",
        "robots": [{"id": "CTU-01", "type": "ctu", "startNode": "robot", "enabled": True}],
    }


def test_topology_fingerprint_is_canonical_and_content_sensitive():
    topology = _topology()
    reordered = {key: topology[key] for key in reversed(topology)}
    assert topology_content_sha256(topology) == topology_content_sha256(reordered)

    changed = deepcopy(topology)
    changed["cells"][0]["cost"] = 9
    assert topology_content_sha256(topology) != topology_content_sha256(changed)


def test_integrated_pathfinding_preserves_result_metrics_and_storage_avoidance():
    topology = _topology()
    astar = shortest_path_result(topology, "cell-r0-c0", "cell-r0-c2", "astar", avoid_storage=True)
    dijkstra = shortest_path_result(topology, "cell-r0-c0", "cell-r0-c2", "dijkstra", avoid_storage=True)

    expected = ["cell-r0-c0", "cell-r1-c0", "cell-r1-c1", "cell-r1-c2", "cell-r0-c2"]
    assert [cell["id"] for cell in astar.path] == expected
    assert [cell["id"] for cell in dijkstra.path] == expected
    assert astar.total_cost == dijkstra.total_cost == 4
    assert shortest_path(topology, "cell-r0-c0", "cell-r0-c1", avoid_storage=True)[-1]["id"] == "cell-r0-c1"


def test_runtime_validation_accepts_valid_inputs_and_reports_broken_references():
    topology = _topology()
    scenario = _scenario()
    assert validate_runtime_inputs(topology, scenario) == []

    broken = deepcopy(topology)
    broken["resources"][0]["cellIds"] = ["missing-cell"]
    broken["cells"] = [cell for cell in broken["cells"] if cell["id"] != "cell-r0-c0"]
    errors = validate_runtime_inputs(broken, scenario)
    assert any("missing cell missing-cell" in error for error in errors)
    assert any("start node robot is not on a traversable cell" in error for error in errors)
