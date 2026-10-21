from copy import deepcopy

from fastapi.testclient import TestClient

from app.main import app
from app.services.assignment import assign_tasks
from app.services.pathfinding import node_to_cell_id, parse_cell_id, shortest_path, shortest_path_shuttle
from app.services.runtime import repo
from app.services.simulation import CTU_RACK_LAYERS, ROBOT_CARRY_CAPACITY, RunRuntime, SimulationService


client = TestClient(app)


TOPOLOGY = {
    "id": "test-topology",
    "name": "Test Grid Topology",
    "meta": {"version": "2.0.0", "viewport": {"x": 0, "y": 0, "zoom": 1}, "tags": []},
    "grid": {"rows": 6, "cols": 8, "cellSize": 32, "originX": 0, "originY": 0},
    "zones": [
        {"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 4, "height": 6},
        {"id": "zone-port", "label": "Port", "type": "port", "x": 5, "y": 4, "width": 3, "height": 2},
    ],
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
        {"id": "node-storage-2", "type": "storage", "row": 3, "col": 3, "zoneId": "zone-ctu", "capacity": 1, "label": "Storage 2"},
    ],
}

CONFIG = {
    "id": "test-config",
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
    goal_cell = "cell-r5-c6"
    path = shortest_path(TOPOLOGY, start_cell, goal_cell, "astar")
    assert path
    ids = [cell["id"] for cell in path]
    assert ids[0] == start_cell
    assert ids[-1] == goal_cell
    assert "cell-r2-c2" not in ids


def test_shortest_path_ctu_avoids_storage_cells():
    # CTU 规则：放了物品（库位/输送带）的格子不能穿行，只能作为取货终点进入。
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
        ],
    }
    # 库位作为取货终点，可以进入
    path = shortest_path(topology, "cell-r0-c0", "cell-r0-c1", "astar", avoid_storage=True)
    assert [cell["id"] for cell in path] == ["cell-r0-c0", "cell-r0-c1"]
    # 中间库位 r0c1 不能穿行：从 r0c0 到 r0c2 会绕行，不经过 r0c1
    path = shortest_path(topology, "cell-r0-c0", "cell-r0-c2", "astar", avoid_storage=True)
    ids = [cell["id"] for cell in path]
    assert ids
    assert ids[-1] == "cell-r0-c2"
    assert "cell-r0-c1" not in ids[1:-1]


def test_shortest_path_extra_blocked_reroutes_ctu_around_occupied_cell():
    # 回归：CTU 头对头死锁时的绕行。extra_blocked 把其它设备当前占用的格临时视为障碍，
    # 从 r0c0 到 r0c2 的直线被 r0c1 挡住时，应绕行下方一行而不是穿行被占格。
    topology = {
        **TOPOLOGY,
        "zones": [
            {"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 3, "height": 3},
        ],
        "cells": [
            {"id": "cell-r0-c0", "row": 0, "col": 0, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r0-c1", "row": 0, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r0-c2", "row": 0, "col": 2, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r1-c0", "row": 1, "col": 0, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r1-c1", "row": 1, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r1-c2", "row": 1, "col": 2, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        ],
        "nodes": [],
    }
    # 无占用：直行 r0c0 -> r0c1 -> r0c2
    direct = shortest_path(topology, "cell-r0-c0", "cell-r0-c2", "astar", avoid_storage=True)
    assert [cell["id"] for cell in direct] == ["cell-r0-c0", "cell-r0-c1", "cell-r0-c2"]

    # r0c1 被另一台 CTU 占用：绕行，不经过 r0c1
    blocked = {parse_cell_id("cell-r0-c1")}
    rerouted = shortest_path(
        topology, "cell-r0-c0", "cell-r0-c2", "astar", avoid_storage=True, extra_blocked=blocked
    )
    assert rerouted
    ids = [cell["id"] for cell in rerouted]
    assert ids[0] == "cell-r0-c0"
    assert ids[-1] == "cell-r0-c2"
    assert "cell-r0-c1" not in ids


def test_shortest_path_extra_blocked_still_reaches_blocked_goal():
    # extra_blocked 不应把终点也挡掉（机器人的停靠位/取货终点可以是被占格）。
    topology = {
        **TOPOLOGY,
        "zones": [{"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 2, "height": 2}],
        "cells": [
            {"id": "cell-r0-c0", "row": 0, "col": 0, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r0-c1", "row": 0, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        ],
        "nodes": [],
    }
    path = shortest_path(
        topology, "cell-r0-c0", "cell-r0-c1", "astar", avoid_storage=True, extra_blocked={parse_cell_id("cell-r0-c1")}
    )
    assert [cell["id"] for cell in path] == ["cell-r0-c0", "cell-r0-c1"]


def test_shortest_path_shuttle_uses_aisle_and_lift():
    # 四向车库：两个 2×2 槽并排，槽 A 是第 1 层巷道，槽 B 是电梯竖井 + 第 2 层巷道。
    topology = {
        **TOPOLOGY,
        "zones": [
            {"id": "zone-shuttle", "label": "Shuttle", "type": "shuttle", "x": 0, "y": 0, "width": 4, "height": 2},
            {"id": "zone-port", "label": "Port", "type": "port", "x": 4, "y": 0, "width": 2, "height": 2},
        ],
        "cells": [
            {"id": "cell-r0-c0", "row": 0, "col": 0, "type": "road", "zoneId": "zone-shuttle", "cost": 1.0, "walkable": True},
            {"id": "cell-r0-c1", "row": 0, "col": 1, "type": "road", "zoneId": "zone-shuttle", "cost": 1.0, "walkable": True},
            {"id": "cell-r1-c0", "row": 1, "col": 0, "type": "road", "zoneId": "zone-shuttle", "cost": 1.0, "walkable": True},
            {"id": "cell-r1-c1", "row": 1, "col": 1, "type": "road", "zoneId": "zone-shuttle", "cost": 1.0, "walkable": True},
            {"id": "cell-r0-c2", "row": 0, "col": 2, "type": "road", "zoneId": "zone-shuttle", "cost": 1.0, "walkable": True},
            {"id": "cell-r0-c3", "row": 0, "col": 3, "type": "road", "zoneId": "zone-shuttle", "cost": 1.0, "walkable": True},
            {"id": "cell-r1-c2", "row": 1, "col": 2, "type": "road", "zoneId": "zone-shuttle", "cost": 1.0, "walkable": True},
            {"id": "cell-r1-c3", "row": 1, "col": 3, "type": "road", "zoneId": "zone-shuttle", "cost": 1.0, "walkable": True},
        ],
        "nodes": [
            {"id": "shuttle", "type": "shuttle_robot", "row": 0, "col": 0, "zoneId": "zone-shuttle", "capacity": 1, "label": "Shuttle-01"},
        ],
    }
    # 槽 A (0,0)：第 1 层巷道（bit0=1）；槽 B (0,2)：电梯竖井 + 第 2 层巷道
    topology["zones"][0]["slotAisles"] = {"0,0": 0b0001, "0,2": 0b0010}
    topology["zones"][0]["slotLifts"] = ["0,2"]

    path = shortest_path_shuttle(topology, "cell-r0-c0", 1, "cell-r0-c3", 2)
    assert path
    assert path[0] == (0, 0, 1)
    assert path[-1] == (0, 3, 2)
    assert any((row, col) == (0, 2) for row, col, _ in path)

    # 没有电梯竖井时无法跨层
    no_lift = {**topology, "zones": [{**topology["zones"][0], "slotLifts": None, "slotAisles": {"0,0": 0b0001}}]}
    assert shortest_path_shuttle(no_lift, "cell-r0-c0", 1, "cell-r0-c3", 2) == []


def test_assignment():
    robots = [{"id": "r1", "type": "ctu", "status": "idle", "currentCellId": "cell-r1-c1", "battery": 100}]
    tasks = [{"id": "t1", "robotType": "ctu", "status": "pending", "sourceCellId": "cell-r1-c1", "sourceLayer": 1}]
    assert assign_tasks(robots, tasks, TOPOLOGY) == [("r1", "t1")]


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


def test_start_simulation_builds_devices_from_topology():
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

    response = client.post(
        "/api/v1/simulations/start",
        json={"topologyId": topology["id"]},
    )
    assert response.status_code == 200
    state = client.get(f"/api/v1/simulations/{response.json()['runId']}/state").json()
    assert {(robot["id"], robot["type"]) for robot in state["robots"]} == {
        ("r1", "ctu"),
        ("shuttle-01", "shuttle"),
    }


def _service() -> SimulationService:
    return SimulationService(repo)


def test_seed_tasks_multiline_orders():
    service = _service()
    tasks = service._seed_tasks(TOPOLOGY, CONFIG)
    ctu_tasks = [task for task in tasks if task["robotType"] == "ctu"]
    assert len(ctu_tasks) == CONFIG["orderGenerator"]["maxOrders"]

    for task in ctu_tasks:
        lines = task["lines"]
        assert CONFIG["orderGenerator"]["linesPerOrderMin"] <= len(lines) <= CONFIG["orderGenerator"]["linesPerOrderMax"]
        assert task["sourceCellId"] == lines[0]["cellId"]
        assert task["deliveryCellId"]
        assert all(1 <= line["layer"] <= CTU_RACK_LAYERS for line in lines)
        assert task["picked"] == 0
        assert task["currentLineIndex"] == 0


def test_robot_carry_capacity():
    service = _service()
    runtime = service._create_runtime("test-capacity", TOPOLOGY, CONFIG)
    robot = runtime.state["robots"][0]
    assert robot["type"] == "ctu"
    assert robot["load"] == 0
    assert robot["capacity"] == ROBOT_CARRY_CAPACITY["ctu"]


def test_ctu_pickup_increments_load_and_advances_to_next_line():
    service = _service()
    runtime = service._create_runtime("test-pickup", TOPOLOGY, CONFIG)
    robot = runtime.state["robots"][0]
    task = service._build_task(
        TOPOLOGY,
        "task-ctu-1",
        "ctu",
        "direct",
        [{"nodeId": "node-storage", "layer": 3}, {"nodeId": "node-storage-2", "layer": 12}],
        "cell-r5-c6",
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
    assert robot["targetCellId"] == node_to_cell_id(TOPOLOGY, "node-storage-2")
    assert task["phase"] == "pickup_from_ctu"


def test_ctu_pickup_switches_to_delivery_when_all_lines_picked():
    service = _service()
    runtime = service._create_runtime("test-delivery", TOPOLOGY, CONFIG)
    robot = runtime.state["robots"][0]
    task = service._build_task(
        TOPOLOGY,
        "task-ctu-1",
        "ctu",
        "direct",
        [{"nodeId": "node-storage", "layer": 3}],
        "cell-r5-c6",
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
    assert task["phase"] == "delivery_to_port"
    assert robot["targetCellId"] == "cell-r5-c6"


def test_reroute_ctu_avoids_occupied_cell_and_keeps_target():
    # 回归：头对头死锁时 CTU 绕行。直行 r0c0 -> r0c2 被 r0c1 占住，_reroute_ctu 应换一条
    # 不经过 r0c1 的路径，且保持原目标不变。
    topology = {
        "id": "reroute-topology",
        "name": "Reroute",
        "meta": {"version": "2.0.0", "viewport": {"x": 0, "y": 0, "zoom": 1}, "tags": []},
        "grid": {"rows": 3, "cols": 3, "cellSize": 32, "originX": 0, "originY": 0},
        "zones": [{"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 3, "height": 3}],
        "cells": [
            {"id": "cell-r0-c0", "row": 0, "col": 0, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r0-c1", "row": 0, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r0-c2", "row": 0, "col": 2, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r1-c0", "row": 1, "col": 0, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r1-c1", "row": 1, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
            {"id": "cell-r1-c2", "row": 1, "col": 2, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        ],
        "nodes": [
            {"id": "r1", "type": "ctu_robot", "row": 0, "col": 0, "zoneId": "zone-ctu", "capacity": 1, "label": "CTU-01"},
        ],
    }
    config = {
        "id": "reroute-config",
        "topologyId": topology["id"],
        "robots": [{"id": "r1", "type": "ctu", "startNode": "r1", "speed": 1.0, "battery": 100, "enabled": True}],
        "orderGenerator": {"ratePerMinute": 1, "maxOrders": 0, "linesPerOrderMin": 1, "linesPerOrderMax": 1},
        "dispatchPolicy": "greedy",
        "pathPolicy": "astar",
        "conveyorPolicy": {"releaseThreshold": 0.8, "fifo": True},
        "faultInjection": {"robotFailureTick": None, "robotFailureId": None, "conveyorJammedTick": None, "conveyorCellId": None},
        "seed": 1,
        "durationTicks": 10,
    }
    service = _service()
    runtime = service._create_runtime("test-reroute", topology, config)
    robot = runtime.state["robots"][0]
    robot["currentCellId"] = "cell-r0-c0"
    robot["targetCellId"] = "cell-r0-c2"

    assert service._reroute_ctu(runtime, robot, {"cell-r0-c1"}) is True
    assert robot["path"][0] == "cell-r0-c0"
    assert robot["path"][-1] == "cell-r0-c2"
    assert "cell-r0-c1" not in robot["path"]
    assert robot["targetCellId"] == "cell-r0-c2"


def test_tick_payload_only_includes_inventory_when_inventory_changes():
    service = _service()
    runtime = RunRuntime(
        run_id="payload-test",
        topology={},
        config={},
        state={"tick": 1, "status": "running", "inventory": [{"nodeId": "slot-a"}, {"nodeId": "slot-b"}]},
        metrics={},
    )

    first = service._tick_payload(runtime)
    assert first["inventory"] == runtime.state["inventory"]

    runtime.state["tick"] = 2
    second = service._tick_payload(runtime)
    assert second["tick"] == 2
    assert "inventory" not in second

    runtime.state["inventory"].pop()
    runtime.state["tick"] = 3
    third = service._tick_payload(runtime)
    assert third["inventory"] == runtime.state["inventory"]
