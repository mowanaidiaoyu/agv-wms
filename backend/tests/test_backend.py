from copy import deepcopy

from fastapi.testclient import TestClient

from app.main import app
from app.services.assignment import assign_tasks
from app.services.pathfinding import node_to_cell_id, shortest_path
from app.services.runtime import repo
from app.services.simulation import CTU_RACK_LAYERS, ROBOT_CARRY_CAPACITY, SimulationService


client = TestClient(app)


TOPOLOGY = {
    "id": "test-topology",
    "name": "Test Grid Topology",
    "meta": {"version": "2.0.0", "viewport": {"x": 0, "y": 0, "zoom": 1}, "tags": []},
    "grid": {"rows": 6, "cols": 8, "cellSize": 32, "originX": 0, "originY": 0},
    "zones": [{"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 4, "height": 6}],
    "cells": [
        {"id": "cell-r1-c1", "row": 1, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        {"id": "cell-r1-c2", "row": 1, "col": 2, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        {"id": "cell-r1-c3", "row": 1, "col": 3, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        {"id": "cell-r1-c4", "row": 1, "col": 4, "type": "conveyor", "zoneId": "zone-ctu", "cost": 0.6, "walkable": True},
        {"id": "cell-r1-c5", "row": 1, "col": 5, "type": "conveyor", "zoneId": "zone-ctu", "cost": 0.6, "walkable": True},
        {"id": "cell-r2-c1", "row": 2, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        {"id": "cell-r2-c2", "row": 2, "col": 2, "type": "blocked", "zoneId": "zone-ctu", "cost": 1.0, "walkable": False},
        {"id": "cell-r2-c3", "row": 2, "col": 3, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        {"id": "cell-r2-c4", "row": 2, "col": 4, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        {"id": "cell-r2-c5", "row": 2, "col": 5, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        {"id": "cell-r2-c6", "row": 2, "col": 6, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
    ],
    "nodes": [
        {"id": "r1", "type": "ctu_robot", "row": 1, "col": 2, "zoneId": "zone-ctu", "capacity": 1, "label": "CTU-01"},
        {"id": "node-storage", "type": "storage", "row": 1, "col": 1, "zoneId": "zone-ctu", "capacity": 1, "label": "Storage"},
        {"id": "node-entry", "type": "entry", "row": 1, "col": 3, "zoneId": "zone-ctu", "capacity": 1, "label": "Entry"},
        {"id": "node-gate", "type": "conveyor_gate", "row": 1, "col": 4, "zoneId": "zone-ctu", "capacity": 1, "label": "Gate"},
        {"id": "node-buffer", "type": "buffer", "row": 2, "col": 4, "zoneId": "zone-ctu", "capacity": 1, "label": "Buffer"},
        {"id": "node-station", "type": "station", "row": 2, "col": 6, "zoneId": "zone-ctu", "capacity": 1, "label": "Station"},
    ],
    "resources": [
        {"id": "res-conveyor", "type": "conveyor_segment", "label": "Conveyor", "cellIds": ["cell-r1-c4", "cell-r1-c5"], "nodeIds": ["node-gate"], "capacity": 1, "meta": {}},
        {"id": "res-buffer", "type": "buffer_slot", "label": "Buffer", "cellIds": ["cell-r2-c4"], "nodeIds": ["node-buffer"], "capacity": 1, "meta": {}},
        {"id": "res-station", "type": "station", "label": "Station", "cellIds": ["cell-r2-c6"], "nodeIds": ["node-station"], "capacity": 1, "meta": {}},
    ],
}

SCENARIO = {
    "id": "test-scenario",
    "name": "Test Scenario",
    "topologyId": "test-topology",
    "robots": [{"id": "r1", "type": "ctu", "startNode": "r1", "speed": 1.0, "battery": 100, "enabled": True}],
    "orderGenerator": {"ratePerMinute": 2, "maxOrders": 4, "linesPerOrderMin": 1, "linesPerOrderMax": 2, "urgentInsertionTick": 4},
    "dispatchPolicy": "greedy",
    "pathPolicy": "astar",
    "conveyorPolicy": {"releaseThreshold": 0.8, "fifo": True},
    "faultInjection": {"robotFailureTick": None, "robotFailureId": None, "conveyorJammedTick": None, "conveyorCellId": None},
    "seed": 7,
    "durationTicks": 10,
}


def test_shortest_path_avoids_blocked_cell():
    start_cell = node_to_cell_id(TOPOLOGY, "node-storage")
    goal_cell = node_to_cell_id(TOPOLOGY, "node-station")
    path = shortest_path(TOPOLOGY, start_cell, goal_cell, "astar")
    assert [cell["id"] for cell in path] == ["cell-r1-c1", "cell-r1-c2", "cell-r1-c3", "cell-r1-c4", "cell-r1-c5", "cell-r2-c5", "cell-r2-c6"]


def test_shortest_path_crosses_floors_with_lift_resource():
    topology = {
        **TOPOLOGY,
        "id": "multi-floor-topology",
        "meta": {**TOPOLOGY["meta"], "floors": ["1F", "2F"]},
        "cells": [
            {"id": "cell-r1-c1", "floor": "1F", "row": 1, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r1-c2", "floor": "1F", "row": 1, "col": 2, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-2F-r1-c2", "floor": "2F", "row": 1, "col": 2, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-2F-r1-c3", "floor": "2F", "row": 1, "col": 3, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        ],
        "nodes": [
            {"id": "node-storage-1f", "type": "storage", "floor": "1F", "row": 1, "col": 1, "zoneId": "zone-ctu", "capacity": 1, "label": "Storage 1F"},
            {"id": "node-lift-1f", "type": "lift", "floor": "1F", "row": 1, "col": 2, "zoneId": "zone-ctu", "capacity": 1, "label": "Lift 1F"},
            {"id": "node-lift-2f", "type": "lift", "floor": "2F", "row": 1, "col": 2, "zoneId": "zone-ctu", "capacity": 1, "label": "Lift 2F"},
            {"id": "node-station-2f", "type": "station", "floor": "2F", "row": 1, "col": 3, "zoneId": "zone-ctu", "capacity": 1, "label": "Station 2F"},
        ],
        "resources": [
            {"id": "res-lift", "type": "lift", "label": "Lift A", "cellIds": [], "nodeIds": ["node-lift-1f", "node-lift-2f"], "capacity": 1, "meta": {"verticalCost": 4}},
        ],
    }
    path = shortest_path(topology, node_to_cell_id(topology, "node-storage-1f"), node_to_cell_id(topology, "node-station-2f"), "astar")
    assert [cell["id"] for cell in path] == ["cell-r1-c1", "cell-r1-c2", "cell-2F-r1-c2", "cell-2F-r1-c3"]


def test_shortest_path_ctu_avoids_storage_cells():
    # CTU 规则：放了物品（库位/缓存位）的格子不能穿行，只能作为取货终点进入。
    topology = {
        **TOPOLOGY,
        "cells": [
            {"id": "cell-r0-c0", "floor": "1F", "row": 0, "col": 0, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r0-c1", "floor": "1F", "row": 0, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r0-c2", "floor": "1F", "row": 0, "col": 2, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        ],
        "nodes": [
            {"id": "rack", "type": "storage", "floor": "1F", "row": 0, "col": 1, "zoneId": "zone-ctu", "capacity": 1, "label": "Rack"},
            {"id": "start", "type": "ctu_robot", "floor": "1F", "row": 0, "col": 0, "zoneId": "zone-ctu", "capacity": 1, "label": "CTU"},
            {"id": "goal", "type": "station", "floor": "1F", "row": 0, "col": 2, "zoneId": "zone-ctu", "capacity": 1, "label": "Station"},
        ],
    }
    # 中间 r0c1 是库位，不能穿行 → 无路
    assert shortest_path(topology, "cell-r0-c0", "cell-r0-c2", "astar", avoid_storage=True) == []
    # 库位作为取货终点，可以进入
    path = shortest_path(topology, "cell-r0-c0", "cell-r0-c1", "astar", avoid_storage=True)
    assert [cell["id"] for cell in path] == ["cell-r0-c0", "cell-r0-c1"]
    # 不启用该规则（默认）仍可穿行
    path = shortest_path(topology, "cell-r0-c0", "cell-r0-c2", "astar")
    assert [cell["id"] for cell in path] == ["cell-r0-c0", "cell-r0-c1", "cell-r0-c2"]


def test_assignment():
    robots = [{"id": "r1", "type": "ctu", "status": "idle", "currentCellId": "cell-r1-c1", "battery": 100}]
    tasks = [{"id": "t1", "robotType": "ctu", "status": "pending", "sourceNode": "node-storage", "targetNode": "node-station"}]
    assert assign_tasks("greedy", robots, tasks, TOPOLOGY, "astar") == [("r1", "t1")]


def test_topology_roundtrip():
    response = client.post("/api/v1/topologies", json=TOPOLOGY)
    assert response.status_code == 200
    result = client.get("/api/v1/topologies/test-topology")
    assert result.status_code == 200
    assert result.json()["payload"]["name"] == "Test Grid Topology"


def test_topology_delete():
    topology = {**TOPOLOGY, "id": "delete-topology", "name": "Delete Grid Topology"}
    response = client.post("/api/v1/topologies", json=topology)
    assert response.status_code == 200

    delete_response = client.delete("/api/v1/topologies/delete-topology")
    assert delete_response.status_code == 200
    assert delete_response.json()["id"] == "delete-topology"

    missing_response = client.get("/api/v1/topologies/delete-topology")
    assert missing_response.status_code == 404


def test_topology_delete_blocked_when_referenced():
    topology = {**TOPOLOGY, "id": "blocked-topology", "name": "Blocked Grid Topology"}
    scenario = {**SCENARIO, "id": "blocked-scenario", "topologyId": "blocked-topology"}
    client.post("/api/v1/topologies", json=topology)
    scenario_response = client.post("/api/v1/scenarios", json=scenario)
    assert scenario_response.status_code == 200

    delete_response = client.delete("/api/v1/topologies/blocked-topology")
    assert delete_response.status_code == 409


def test_get_scenario_auto_syncs_stale_robot_start_node():
    topology = {**TOPOLOGY, "id": "sync-topology", "name": "Sync Topology"}
    client.post("/api/v1/topologies", json=topology)

    stale_scenario = {
        **SCENARIO,
        "id": "sync-scenario",
        "name": "Sync Scenario",
        "topologyId": topology["id"],
        "robots": [{"id": "r1", "type": "ctu", "startNode": "legacy-start-node", "speed": 1.8, "battery": 88, "enabled": False}],
    }
    repo.upsert_scenario(stale_scenario["id"], stale_scenario["name"], stale_scenario)

    response = client.get("/api/v1/scenarios/sync-scenario")
    assert response.status_code == 200

    robots = response.json()["payload"]["robots"]
    assert robots == [{"id": "r1", "type": "ctu", "startNode": "r1", "speed": 1.8, "battery": 88, "enabled": False}]


def test_start_simulation_auto_syncs_missing_topology_devices():
    topology = deepcopy(TOPOLOGY)
    topology["id"] = "auto-sync-topology"
    topology["name"] = "Auto Sync Topology"
    topology["nodes"].append(
        {
            "id": "shuttle-01",
            "type": "shuttle_robot",
            "row": 2,
            "col": 5,
            "zoneId": "zone-ctu",
            "capacity": 1,
            "label": "Shuttle-01",
        }
    )
    client.post("/api/v1/topologies", json=topology)

    stale_scenario = {
        **SCENARIO,
        "id": "auto-sync-scenario",
        "name": "Auto Sync Scenario",
        "topologyId": topology["id"],
        "robots": [{"id": "r1", "type": "ctu", "startNode": "legacy-r1", "speed": 1.6, "battery": 77, "enabled": True}],
    }
    repo.upsert_scenario(stale_scenario["id"], stale_scenario["name"], stale_scenario)

    response = client.post(
        "/api/v1/simulations/start",
        json={"topologyId": topology["id"], "scenarioId": stale_scenario["id"]},
    )
    assert response.status_code == 200

    persisted = repo.get_scenario(stale_scenario["id"])
    assert persisted is not None
    robots = persisted["payload"]["robots"]
    assert robots == [
        {"id": "r1", "type": "ctu", "startNode": "r1", "speed": 1.6, "battery": 77, "enabled": True},
        {"id": "shuttle-01", "type": "shuttle", "startNode": "shuttle-01", "speed": 1.1, "battery": 100, "enabled": True},
    ]


def test_algorithm_evaluation():
    client.post("/api/v1/topologies", json=TOPOLOGY)
    scenario_response = client.post("/api/v1/scenarios", json=SCENARIO)
    assert scenario_response.status_code == 200
    response = client.post("/api/v1/algorithms/evaluate", json={"topologyId": TOPOLOGY["id"], "scenarioId": SCENARIO["id"]})
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 6
    assert {item["category"] for item in results} == {"dispatch", "path", "conveyor"}


def _service() -> SimulationService:
    return SimulationService(repo)


def test_seed_tasks_multiline_orders():
    service = _service()
    tasks = service._seed_tasks(TOPOLOGY, SCENARIO)
    ctu_tasks = [task for task in tasks if task["robotType"] == "ctu"]
    assert len(ctu_tasks) == SCENARIO["orderGenerator"]["maxOrders"]

    for task in ctu_tasks:
        lines = task["lines"]
        assert SCENARIO["orderGenerator"]["linesPerOrderMin"] <= len(lines) <= SCENARIO["orderGenerator"]["linesPerOrderMax"]
        # sourceNode/sourceCellId 始终等于首行（兼容 assignment.task_cost）
        assert task["sourceNode"] == lines[0]["nodeId"]
        assert task["sourceCellId"] == lines[0]["cellId"]
        assert all(1 <= line["layer"] <= CTU_RACK_LAYERS for line in lines)
        assert task["picked"] == 0
        assert task["currentLineIndex"] == 0


def test_robot_carry_capacity():
    service = _service()
    runtime = service._create_runtime("test-capacity", TOPOLOGY, SCENARIO, record_events=False)
    robot = runtime.state["robots"][0]
    assert robot["type"] == "ctu"
    assert robot["load"] == 0
    assert robot["capacity"] == ROBOT_CARRY_CAPACITY["ctu"]


def test_ctu_pickup_increments_load_and_advances_to_next_line():
    service = _service()
    runtime = service._create_runtime("test-pickup", TOPOLOGY, SCENARIO, record_events=False)
    robot = runtime.state["robots"][0]
    task = service._build_task(
        TOPOLOGY,
        "task-ctu-1",
        "ctu",
        "direct",
        [{"nodeId": "node-storage", "layer": 3}, {"nodeId": "node-buffer", "layer": 12}],
        "node-station",
        "node-entry",
        None,
        None,
        False,
        2,
    )
    task["phase"] = "pickup_from_ctu"
    task["status"] = "assigned"
    robot["currentTaskId"] = task["id"]
    robot["status"] = "moving"
    robot["load"] = 0
    robot["capacity"] = ROBOT_CARRY_CAPACITY["ctu"]
    robot["currentCellId"] = node_to_cell_id(TOPOLOGY, "node-storage")
    runtime.state["tasks"] = [task]

    service._advance_robot_phase(runtime, robot, task)

    assert robot["load"] == 1
    assert task["picked"] == 1
    assert task["currentLineIndex"] == 1
    assert robot["targetCellId"] == node_to_cell_id(TOPOLOGY, "node-buffer")
    assert task["phase"] == "pickup_from_ctu"


def test_ctu_pickup_switches_to_delivery_when_all_lines_picked():
    service = _service()
    runtime = service._create_runtime("test-delivery", TOPOLOGY, SCENARIO, record_events=False)
    robot = runtime.state["robots"][0]
    task = service._build_task(
        TOPOLOGY,
        "task-ctu-1",
        "ctu",
        "direct",
        [{"nodeId": "node-storage", "layer": 3}],
        "node-station",
        "node-entry",
        None,
        None,
        False,
        2,
    )
    task["phase"] = "pickup_from_ctu"
    task["status"] = "assigned"
    robot["currentTaskId"] = task["id"]
    robot["status"] = "moving"
    robot["load"] = 0
    robot["capacity"] = ROBOT_CARRY_CAPACITY["ctu"]
    robot["currentCellId"] = node_to_cell_id(TOPOLOGY, "node-storage")
    runtime.state["tasks"] = [task]

    service._advance_robot_phase(runtime, robot, task)

    assert robot["load"] == 1
    assert task["picked"] == 1
    assert task["phase"] == "delivery_to_station"
    assert robot["targetCellId"] == node_to_cell_id(TOPOLOGY, "node-station")
