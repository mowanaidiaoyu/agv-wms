import asyncio
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


def test_ctu_storage_entry_only_from_rack_long_side():
    # 竖向 1×3 的 CTU 货架：长边在左右，只能从左右进入，不能从上下端头（短边）进入。
    topology = {
        **TOPOLOGY,
        "grid": {"rows": 5, "cols": 5, "cellSize": 32, "originX": 0, "originY": 0},
        "zones": [{"id": "zone-rack", "label": "Rack", "type": "ctu", "x": 2, "y": 0, "width": 1, "height": 3}],
        "cells": [],
        "nodes": [
            {"id": "storage-a", "type": "storage", "floor": "1F", "row": 0, "col": 2, "zoneId": "zone-rack", "capacity": 17, "label": "A"},
            {"id": "storage-b", "type": "storage", "floor": "1F", "row": 1, "col": 2, "zoneId": "zone-rack", "capacity": 17, "label": "B"},
            {"id": "storage-c", "type": "storage", "floor": "1F", "row": 2, "col": 2, "zoneId": "zone-rack", "capacity": 17, "label": "C"},
        ],
    }
    # 从正下方（短边）出发取底层库位：必须绕到左右长边后再进入
    path = shortest_path(topology, "cell-r3-c2", "cell-r2-c2", "astar", avoid_storage=True)
    ids = [cell["id"] for cell in path]
    assert ids[-1] == "cell-r2-c2"
    assert ids[-2] in {"cell-r2-c1", "cell-r2-c3"}

    # 左右长边都被挡住时，禁止改从短边进入 => 无路可走
    blocked_sides = {
        **topology,
        "cells": [
            {"id": "cell-r2-c1", "row": 2, "col": 1, "type": "blocked", "walkable": False, "cost": 1.0},
            {"id": "cell-r2-c3", "row": 2, "col": 3, "type": "blocked", "walkable": False, "cost": 1.0},
        ],
    }
    assert shortest_path(blocked_sides, "cell-r3-c2", "cell-r2-c2", "astar", avoid_storage=True) == []


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


def test_seed_tasks_never_share_one_storage_slot():
    # 同一库位槽（nodeId + layer）只能派一单，否则两台车会抢同一格形成对头死锁。
    service = _service()
    tasks = service._seed_tasks(TOPOLOGY, CONFIG)
    keys = [(line["nodeId"], line["layer"]) for task in tasks for line in task["lines"]]
    assert keys
    assert len(keys) == len(set(keys))


def test_split_ports_by_conveyor_kind():
    # port 归属由入口格最近的输送块决定：地面输送块（conveyorGround）→ 地面线 port，
    # 其余 → 架空线 port；附近没有输送块时按架空处理。
    service = _service()
    topology = {
        "zones": [
            {"id": "p-elevated", "label": "Port E", "type": "port", "x": 5, "y": 4, "width": 3, "height": 2},
            {"id": "p-ground", "label": "Port G", "type": "port", "x": 0, "y": 6, "width": 3, "height": 2},
        ],
        "cells": [
            # p-elevated 入口格 cell-r5-c6 紧邻架空输送块
            {"id": "cell-r5-c6", "floor": "1F", "row": 5, "col": 6, "type": "conveyor"},
            # p-ground 入口格 cell-r7-c1 紧邻地面输送块
            {"id": "cell-r7-c1", "floor": "1F", "row": 7, "col": 1, "type": "conveyor", "conveyorGround": True},
        ],
    }
    ground_ports, elevated_ports = service._split_ports_by_conveyor_kind(topology)
    assert ground_ports == ["cell-r7-c1"]
    assert elevated_ports == ["cell-r5-c6"]


def test_seed_tasks_routes_shuttle_to_ground_conveyor_anchor():
    # 四向车（整箱）直接驶上地面输送线交付：目标取地面线 2×2 块锚点，
    # 不需要 port 定位（port 是 CTU 概念）；CTU（零散件）仍走架空线 port。
    service = _service()
    topology = {
        **TOPOLOGY,
        "grid": {**TOPOLOGY["grid"], "rows": 10, "cols": 10},
        "zones": [
            {"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 4, "height": 6},
            {"id": "zone-shuttle", "label": "Shuttle", "type": "shuttle", "x": 0, "y": 8, "width": 4, "height": 2, "slotAisles": {"8,0": 1}},
            {"id": "zone-port", "label": "Port", "type": "port", "x": 5, "y": 4, "width": 3, "height": 2},
        ],
        "cells": [
            *TOPOLOGY["cells"],
            # 地面输送线块（2×2，锚点 (9,8)）：无任何 port 靠近它
            {"id": "cell-r9-c8", "floor": "1F", "row": 9, "col": 8, "type": "conveyor", "conveyorGround": True,
             "conveyorAnchor": {"row": 9, "col": 8}},
            {"id": "cell-r9-c9", "floor": "1F", "row": 9, "col": 9, "type": "conveyor", "conveyorGround": True,
             "conveyorAnchor": {"row": 9, "col": 8}},
            # shuttle 区 4 格故意不建格 → 非 road → 该 2×2 槽为四向车取货位
        ],
    }
    tasks = service._seed_tasks(topology, CONFIG)
    ctu_tasks = [task for task in tasks if task["robotType"] == "ctu"]
    shuttle_tasks = [task for task in tasks if task["robotType"] == "shuttle"]
    assert ctu_tasks and shuttle_tasks
    # 四向车交付到地面线锚点（cell-r9-c8），CTU 走架空 port（cell-r5-c6）
    assert all(task["deliveryCellId"] == "cell-r9-c8" for task in shuttle_tasks)
    assert all(task["deliveryCellId"] == "cell-r5-c6" for task in ctu_tasks)


def test_seed_shuttle_dock_tasks():
    # 四向车出货位（shuttle_dock）：设置取什么货物（skuId）与取多少（quantity），
    # 生成对应数量的搬运任务（每箱一趟），交付到出货位所在地面输送线格；无需 port。
    service = _service()
    topology = {
        **TOPOLOGY,
        "grid": {**TOPOLOGY["grid"], "rows": 10, "cols": 10},
        "zones": [
            {"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 4, "height": 6},
            {"id": "zone-shuttle", "label": "Shuttle", "type": "shuttle", "x": 0, "y": 8, "width": 4, "height": 2, "slotAisles": {"8,0": 1}},
        ],
        "cells": [
            *TOPOLOGY["cells"],
            {"id": "cell-r9-c8", "floor": "1F", "row": 9, "col": 8, "type": "conveyor", "conveyorGround": True,
             "conveyorAnchor": {"row": 9, "col": 8}},
            {"id": "cell-r9-c9", "floor": "1F", "row": 9, "col": 9, "type": "conveyor", "conveyorGround": True,
             "conveyorAnchor": {"row": 9, "col": 8}},
        ],
        "nodes": [
            *TOPOLOGY["nodes"],
            {
                "id": "dock-01", "type": "shuttle_dock", "floor": "1F", "row": 9, "col": 8,
                "zoneId": None, "capacity": 1, "label": "出货位 1",
                "skuId": "SKU-DOCK", "skuName": "脚手架 1.7米", "largeCategory": "建筑配件", "quantity": 3,
            },
        ],
    }
    tasks = service._seed_shuttle_tasks(topology)
    assert [task["id"] for task in tasks] == [
        "task-dock-dock-01-1",
        "task-dock-dock-01-2",
        "task-dock-dock-01-3",
    ]
    assert all(task["robotType"] == "shuttle" and task["mode"] == "cross_zone" for task in tasks)
    assert all(task["deliveryCellId"] == "cell-r9-c8" for task in tasks)
    assert all(task["lines"][0]["skuId"] == "SKU-DOCK" for task in tasks)
    assert all(task["lines"][0]["skuName"] == "脚手架 1.7米" for task in tasks)
    assert all(task["sourceCellId"] == "cell-r8-c2" for task in tasks)  # 巷道块(8,0)邻接的唯一货位


def test_update_shuttle_dock_runtime_settings():
    # 四向车搬运工作台运行时改出货位设置：只重排「未分配」任务，执行中/已完成不受影响。
    topology = {
        **TOPOLOGY,
        "id": "dock-update-topology",
        "grid": {**TOPOLOGY["grid"], "rows": 10, "cols": 10},
        "zones": [
            {"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 4, "height": 6},
            {"id": "zone-shuttle", "label": "Shuttle", "type": "shuttle", "x": 0, "y": 8, "width": 4, "height": 2, "slotAisles": {"8,0": 1}},
        ],
        "cells": [
            *TOPOLOGY["cells"],
            {"id": "cell-r9-c8", "floor": "1F", "row": 9, "col": 8, "type": "conveyor", "conveyorGround": True,
             "conveyorAnchor": {"row": 9, "col": 8}},
            {"id": "cell-r9-c9", "floor": "1F", "row": 9, "col": 9, "type": "conveyor", "conveyorGround": True,
             "conveyorAnchor": {"row": 9, "col": 8}},
        ],
        "nodes": [
            *TOPOLOGY["nodes"],
            {
                "id": "dock-01", "type": "shuttle_dock", "floor": "1F", "row": 9, "col": 8,
                "zoneId": None, "capacity": 1, "label": "出货位 1",
                "skuId": "SKU-DOCK", "skuName": "脚手架 1.7米", "quantity": 1,
            },
        ],
    }
    assert client.post("/api/v1/topologies", json=topology).status_code == 200
    run = client.post("/api/v1/simulations/start", json={"topologyId": "dock-update-topology"}).json()
    run_id = run["runId"]
    try:
        state = client.get(f"/api/v1/simulations/{run_id}/state").json()
        assert len(state["shuttleDocks"]) == 1
        assert state["shuttleDocks"][0]["dockId"] == "dock-01"

        resp = client.put(
            f"/api/v1/simulations/{run_id}/shuttle-docks/dock-01",
            json={"skuId": "SKU-X", "skuName": "测试箱", "quantity": 2, "priority": "urgent", "loop": True},
        )
        assert resp.status_code == 200
        state = client.get(f"/api/v1/simulations/{run_id}/state").json()
        dock = state["shuttleDocks"][0]
        assert dock["quantity"] == 2 and dock["priority"] == "urgent" and dock["loop"] is True
        pending = [
            task for task in state["tasks"]
            if task.get("dockId") == "dock-01" and task["status"] == "pending"
        ]
        assert len(pending) == 2
        assert all(task["lines"][0]["skuId"] == "SKU-X" for task in pending)
        assert all(task["priority"] == 1 for task in pending)
        assert all(task.get("requestedVehicleId") is None for task in pending)
    finally:
        client.post(f"/api/v1/simulations/{run_id}/stop")


def test_update_shuttle_dock_rejects_unknown_vehicle():
    # 指定车辆必须是存在的四向车；否则 409。
    topology = {
        **TOPOLOGY,
        "id": "dock-vehicle-topology",
        "grid": {**TOPOLOGY["grid"], "rows": 10, "cols": 10},
        "zones": [
            {"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 4, "height": 6},
            {"id": "zone-shuttle", "label": "Shuttle", "type": "shuttle", "x": 0, "y": 8, "width": 4, "height": 2, "slotAisles": {"8,0": 1}},
        ],
        "cells": [
            *TOPOLOGY["cells"],
            {"id": "cell-r9-c8", "floor": "1F", "row": 9, "col": 8, "type": "conveyor", "conveyorGround": True,
             "conveyorAnchor": {"row": 9, "col": 8}},
        ],
        "nodes": [
            *TOPOLOGY["nodes"],
            {"id": "dock-01", "type": "shuttle_dock", "floor": "1F", "row": 9, "col": 8,
             "zoneId": None, "capacity": 1, "label": "出货位 1", "quantity": 1},
        ],
    }
    assert client.post("/api/v1/topologies", json=topology).status_code == 200
    run = client.post("/api/v1/simulations/start", json={"topologyId": "dock-vehicle-topology"}).json()
    run_id = run["runId"]
    try:
        resp = client.put(
            f"/api/v1/simulations/{run_id}/shuttle-docks/dock-01",
            json={"vehicleId": "SHUTTLE-NOT-EXIST"},
        )
        assert resp.status_code == 409
    finally:
        client.post(f"/api/v1/simulations/{run_id}/stop")


def test_update_shuttle_dock_autoprovisions_from_latest_topology():
    # Run 启动后才保存进拓扑的出货位：工作台保存设置时自动补建（无需重启仿真），并立即派任务。
    base_nodes = [node for node in TOPOLOGY["nodes"]]
    topology = {
        **TOPOLOGY,
        "id": "dock-late-topology",
        "grid": {**TOPOLOGY["grid"], "rows": 10, "cols": 10},
        "zones": [
            {"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 4, "height": 6},
            {"id": "zone-shuttle", "label": "Shuttle", "type": "shuttle", "x": 0, "y": 8, "width": 4, "height": 2, "slotAisles": {"8,0": 1}},
        ],
        "cells": [
            *TOPOLOGY["cells"],
            {"id": "cell-r9-c8", "floor": "1F", "row": 9, "col": 8, "type": "conveyor", "conveyorGround": True,
             "conveyorAnchor": {"row": 9, "col": 8}},
            {"id": "cell-r9-c9", "floor": "1F", "row": 9, "col": 9, "type": "conveyor", "conveyorGround": True,
             "conveyorAnchor": {"row": 9, "col": 8}},
        ],
        "nodes": base_nodes,  # 启动时拓扑里还没有出货位
    }
    assert client.post("/api/v1/topologies", json=topology).status_code == 200
    run = client.post("/api/v1/simulations/start", json={"topologyId": "dock-late-topology"}).json()
    run_id = run["runId"]
    try:
        state = client.get(f"/api/v1/simulations/{run_id}/state").json()
        assert state["shuttleDocks"] == []

        # 模拟用户随后在编辑器把出货位存进同一拓扑，再在工作台保存设置。
        topology["nodes"] = [
            *base_nodes,
            {"id": "dock-late", "type": "shuttle_dock", "floor": "1F", "row": 9, "col": 8,
             "zoneId": None, "capacity": 1, "label": "后加出货位", "skuId": "SKU-LATE", "quantity": 2},
        ]
        assert client.post("/api/v1/topologies", json=topology).status_code == 200

        resp = client.put(
            f"/api/v1/simulations/{run_id}/shuttle-docks/dock-late",
            json={"quantity": 2},
        )
        assert resp.status_code == 200
        state = client.get(f"/api/v1/simulations/{run_id}/state").json()
        assert [dock["dockId"] for dock in state["shuttleDocks"]] == ["dock-late"]
        pending = [
            task for task in state["tasks"]
            if task.get("dockId") == "dock-late" and task["status"] == "pending"
        ]
        assert len(pending) == 2
        assert all(task["lines"][0]["skuId"] == "SKU-LATE" for task in pending)

        # 拓扑里根本不存在的出货位仍然 404。
        resp = client.put(f"/api/v1/simulations/{run_id}/shuttle-docks/dock-ghost", json={})
        assert resp.status_code == 404
    finally:
        client.post(f"/api/v1/simulations/{run_id}/stop")


def _strict_shuttle_topology(dock_rows_cols: list[tuple[int, int]] | None) -> dict:
    """严格移动测试拓扑：车库 rows0-1 cols0-5（L1 全巷道）；正下方两条地面线块
    A=rows2-3 cols0-1、B=rows2-3 cols4-5（经 row3 cols2-3 地面线相连）；其余为普通道路。"""
    ground_cells: list[dict] = []
    for anchor_col, prefix in ((0, "g"), (4, "d")):
        for r in (2, 3):
            for c in (anchor_col, anchor_col + 1):
                ground_cells.append({
                    "id": f"cell-{prefix}{r}-{c}", "floor": "1F", "row": r, "col": c, "type": "conveyor",
                    "conveyorGround": True, "conveyorAnchor": {"row": 2, "col": anchor_col},
                })
    for c in (2, 3):
        ground_cells.append({
            "id": f"cell-r3-c{c}", "floor": "1F", "row": 3, "col": c, "type": "conveyor",
            "conveyorGround": True, "conveyorAnchor": {"row": 2, "col": 4},
        })
    return {
        "id": "shuttle-strict-topology",
        "name": "Shuttle Strict Topology",
        "meta": {"version": "2.0.0", "viewport": {"x": 0, "y": 0, "zoom": 1}, "tags": []},
        "grid": {"rows": 8, "cols": 8, "cellSize": 32, "originX": 0, "originY": 0},
        "zones": [
            {
                "id": "zone-shuttle", "label": "S", "type": "shuttle", "x": 0, "y": 0, "width": 6, "height": 2,
                "slotAisles": {"0,0": 1, "0,2": 1, "0,4": 1},
            },
        ],
        "cells": ground_cells,
        "nodes": [
            {
                "id": f"dock-{i}", "type": "shuttle_dock", "floor": "1F", "row": r, "col": c,
                "zoneId": None, "capacity": 1, "label": f"出货口 {i}",
            }
            for i, (r, c) in enumerate(dock_rows_cols or [], start=1)
        ],
    }


_GATE_A = {(2, 0), (2, 1), (3, 0), (3, 1)}
_GATE_B = {(2, 4), (2, 5), (3, 4), (3, 5)}
_ZONE_CELLS = {(0, c) for c in range(6)} | {(1, c) for c in range(6)}


def _first_out_zone_cell(path: list[tuple[int, int, int]]) -> tuple[int, int] | None:
    for row, col, _ in path:
        if (row, col) not in _ZONE_CELLS:
            return (row, col)
    return None


def test_shuttle_exits_rack_through_dock_gate():
    # 出货位在车库正下方块 A 上：四向车从巷道经闸口 A 驶上地面线。
    topology = _strict_shuttle_topology([(2, 0)])
    path = shortest_path_shuttle(topology, "cell-r0-c0", 1, "cell-r3-c0", 1)
    assert path, "经过闸口时四向车应可从巷道驶上地面线"
    assert _first_out_zone_cell(path) in _GATE_A


def test_shuttle_cannot_exit_rack_without_dock_gate():
    # 出货位只在块 B 时：车库正下方的块 A（无 dock）不可跨越，必须绕行从 B 出库。
    topology = _strict_shuttle_topology([(2, 4)])
    path = shortest_path_shuttle(topology, "cell-r0-c0", 1, "cell-r3-c0", 1)
    assert path, "存在连接地面线时应可从远端闸口出库再沿线交付"
    assert _first_out_zone_cell(path) in _GATE_B, "出库位置必须落在出货位闸口块上"
    # 目标在闸口对侧（B 块）同理。
    path = shortest_path_shuttle(topology, "cell-r0-c0", 1, "cell-r3-c5", 1)
    assert path and _first_out_zone_cell(path) in _GATE_B


def test_shuttle_never_walks_on_plain_road():
    # 车库外普通道路不可通行：目标在纯道路格上时不可达。
    topology = _strict_shuttle_topology([(2, 0)])
    assert shortest_path_shuttle(topology, "cell-r0-c0", 1, "cell-r5-c5", 1) == []


def test_shuttle_without_dock_falls_back_to_any_boundary_crossing():
    # 没有任何出货位时保持旧行为：车库与地面线的任意邻接都可跨越（兼容旧拓扑）。
    topology = _strict_shuttle_topology(None)
    path = shortest_path_shuttle(topology, "cell-r0-c0", 1, "cell-r3-c0", 1)
    assert path and _first_out_zone_cell(path) in _GATE_A


TWO_ROBOT_TOPOLOGY = {
    "id": "two-robot-topology",
    "name": "Two Robot Topology",
    "meta": {"version": "2.0.0", "viewport": {"x": 0, "y": 0, "zoom": 1}, "tags": []},
    "grid": {"rows": 3, "cols": 3, "cellSize": 32, "originX": 0, "originY": 0},
    "zones": [{"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 3, "height": 3}],
    "cells": [],
    "nodes": [
        {"id": "r1", "type": "ctu_robot", "row": 1, "col": 0, "zoneId": "zone-ctu", "capacity": 1, "label": "CTU-01"},
        {"id": "r2", "type": "ctu_robot", "row": 1, "col": 1, "zoneId": "zone-ctu", "capacity": 1, "label": "CTU-02"},
    ],
}

TWO_ROBOT_CONFIG = {
    "id": "two-robot-config",
    "topologyId": "two-robot-topology",
    "robots": [
        {"id": "r1", "type": "ctu", "startNode": "r1", "speed": 1.0, "battery": 100, "enabled": True},
        {"id": "r2", "type": "ctu", "startNode": "r2", "speed": 1.0, "battery": 100, "enabled": True},
    ],
    "orderGenerator": {"ratePerMinute": 1, "maxOrders": 0, "linesPerOrderMin": 1, "linesPerOrderMax": 1},
    "dispatchPolicy": "greedy",
    "pathPolicy": "astar",
    "conveyorPolicy": {"releaseThreshold": 0.8, "fifo": True},
    "faultInjection": {"robotFailureTick": None, "robotFailureId": None, "conveyorJammedTick": None, "conveyorCellId": None},
    "seed": 1,
    "durationTicks": 10,
}


def test_failed_robot_cell_is_treated_as_obstacle():
    # 故障设备停在原地后，占的格不能出现在其它设备的路径里。
    service = _service()
    runtime = service._create_runtime("test-failed", TWO_ROBOT_TOPOLOGY, TWO_ROBOT_CONFIG)
    r1, r2 = runtime.state["robots"]
    r2["failed"] = True
    r2["status"] = "failed"
    r2["currentCellId"] = "cell-r1-c1"
    r1["currentCellId"] = "cell-r1-c0"

    assert service._robot_path_to(runtime, r1, "cell-r1-c2", 1)
    assert "cell-r1-c1" not in r1["path"]
    assert r1["path"][-1] == "cell-r1-c2"


def test_head_on_deadlock_lets_one_robot_yield():
    # 两台车互相把对方当前格当作下一格时，其中一台应后退一格并重新规划。
    service = _service()
    runtime = service._create_runtime("test-head-on", TWO_ROBOT_TOPOLOGY, TWO_ROBOT_CONFIG)
    r1, r2 = runtime.state["robots"]
    r1.update(
        currentCellId="cell-r1-c1",
        lastCellId="cell-r1-c0",
        targetCellId="cell-r1-c2",
        path=["cell-r1-c1", "cell-r1-c2"],
        status="blocked",
    )
    r2.update(
        currentCellId="cell-r1-c2",
        lastCellId="cell-r1-c2",
        targetCellId="cell-r1-c0",
        path=["cell-r1-c2", "cell-r1-c1"],
        status="blocked",
    )
    occupied = {"cell-r1-c1", "cell-r1-c2"}

    assert service._clear_head_on(runtime, r1, r2, occupied) is True
    assert r1["currentCellId"] == "cell-r1-c0"
    assert r1["lastCellId"] == "cell-r1-c1"
    assert r1["path"][0] == "cell-r1-c0"
    assert r1["path"][-1] == "cell-r1-c2"


def test_step_off_port_moves_finished_robot_off_entry():
    service = _service()
    runtime = service._create_runtime("test-step-off", TOPOLOGY, CONFIG)
    robot = runtime.state["robots"][0]
    robot["currentCellId"] = "cell-r5-c6"
    robot["lastCellId"] = "cell-r4-c6"
    robot["path"] = ["cell-r5-c6"]
    service._step_off_port(runtime, robot, None)
    assert robot["currentCellId"] == "cell-r4-c6"


def test_robot_carry_capacity():
    service = _service()
    runtime = service._create_runtime("test-capacity", TOPOLOGY, CONFIG)
    robot = runtime.state["robots"][0]
    assert robot["type"] == "ctu"
    assert robot["load"] == 0
    assert robot["capacity"] == ROBOT_CARRY_CAPACITY["ctu"]


def test_ctu_pickup_path_stops_in_adjacent_aisle_cell():
    service = _service()
    runtime = service._create_runtime("test-pickup-approach", TOPOLOGY, CONFIG)
    robot = runtime.state["robots"][0]
    target = node_to_cell_id(TOPOLOGY, "node-storage")

    assert service._robot_path_to(runtime, robot, target, 17, stop_adjacent=True) is True
    assert robot["path"][-1] != target
    _, end_row, end_col = parse_cell_id(robot["path"][-1])
    _, target_row, target_col = parse_cell_id(target)
    assert abs(end_row - target_row) + abs(end_col - target_col) == 1
    assert robot["targetCellId"] == target
    assert robot["targetLayer"] == 17


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
    assert robot["handlingAction"]["stage"] == "lifting"
    assert robot["handlingAction"]["targetLayer"] == 3
    assert robot["load"] == 0
    service._advance_robot_phase(runtime, robot, task)
    assert robot["handlingAction"]["stage"] == "extending"
    service._advance_robot_phase(runtime, robot, task)
    assert robot["handlingAction"]["stage"] == "stowing"
    service._advance_robot_phase(runtime, robot, task)

    assert robot["load"] == 1
    assert robot["handlingAction"] is None
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

    for _ in range(4):
        service._advance_robot_phase(runtime, robot, task)

    assert robot["load"] == 1
    assert robot["handlingAction"] is None
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


def test_ctu_yields_to_adjacent_free_cell_when_no_reroute_is_available():
    topology = {
        "id": "yield-topology",
        "name": "Yield",
        "meta": {"version": "2.0.0", "viewport": {"x": 0, "y": 0, "zoom": 1}, "tags": []},
        "grid": {"rows": 2, "cols": 3, "cellSize": 32, "originX": 0, "originY": 0},
        "zones": [{"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 3, "height": 2}],
        "cells": [],
        "nodes": [{"id": "r1", "type": "ctu_robot", "row": 0, "col": 0, "zoneId": "zone-ctu", "capacity": 1, "label": "CTU"}],
    }
    config = deepcopy(CONFIG)
    config["topologyId"] = topology["id"]
    config["robots"] = [{"id": "r1", "type": "ctu", "startNode": "r1", "speed": 1.0, "battery": 100, "enabled": True}]
    service = _service()
    runtime = service._create_runtime("yield-test", topology, config)
    robot = runtime.state["robots"][0]
    robot["targetCellId"] = "cell-r0-c2"

    occupied = {"cell-r0-c0", "cell-r0-c1"}
    reserved = set()
    assert service._yield_ctu(runtime, robot, occupied, reserved) is True
    assert robot["currentCellId"] == "cell-r1-c0"
    assert robot["targetCellId"] == "cell-r0-c2"
    assert robot["yieldTicks"] == 2
    assert "cell-r1-c0" in occupied
    assert "cell-r0-c0" not in occupied


def test_completed_robot_returns_to_standby_while_other_tasks_are_unfinished(monkeypatch):
    service = _service()
    runtime = service._create_runtime("standby-return", TOPOLOGY, deepcopy(CONFIG))
    robot = runtime.state["robots"][0]
    task = runtime.state["tasks"][0]
    task.update({"status": "assigned", "phase": "delivery_to_port", "assignedRobotId": robot["id"]})
    runtime.state["tasks"].append({"id": "other-task", "status": "assigned"})
    robot.update(
        {
            "currentCellId": "cell-r1-c5",
            "currentNode": "cell-r1-c5",
            "currentRow": 1,
            "currentCol": 5,
            "currentTaskId": task["id"],
        }
    )
    monkeypatch.setattr(service, "_record_event", lambda *_args, **_kwargs: None)

    # 交付动作链：升降对位 → 伸叉送箱 → 箱入输送线 → 完成
    for _ in range(4):
        service._advance_robot_phase(runtime, robot, task)

    assert task["status"] == "completed"
    assert robot["returningToStandby"] is True
    assert robot["status"] == "returning"
    assert robot["targetCellId"] == robot["homeCellId"]
    assert len(robot["path"]) > 1


def test_tick_payload_sends_inventory_delta_only_when_inventory_changes():
    service = _service()
    runtime = RunRuntime(
        run_id="payload-test",
        topology={},
        config={},
        state={"tick": 1, "status": "running", "inventory": [{"nodeId": "slot-a"}, {"nodeId": "slot-b"}]},
        metrics={},
    )

    # 常规 tick 不携带全量库存（库存可能有数万条，避免重复编码），也没有增量。
    first = service._tick_payload(runtime)
    assert "inventory" not in first
    assert "inventoryDelta" not in first

    runtime.state["tick"] = 2
    runtime.inventory_delta.append({"op": "remove", "nodeId": "slot-a", "layer": 1})
    second = service._tick_payload(runtime)
    assert second["tick"] == 2
    assert "inventory" not in second
    assert second["inventoryDelta"] == [{"op": "remove", "nodeId": "slot-a", "layer": 1}]

    # 增量被消费（广播后清空）后，下一个 tick 不再携带。
    runtime.inventory_delta.clear()
    runtime.state["tick"] = 3
    third = service._tick_payload(runtime)
    assert "inventoryDelta" not in third


def test_runtime_continues_past_nominal_duration_until_tasks_finish(monkeypatch):
    service = _service()
    runtime = RunRuntime(
        run_id="duration-test",
        topology={},
        config={"durationTicks": 1, "maxDurationTicks": 10},
        state={
            "tick": 0,
            "status": "running",
            "stopReason": None,
            "tasks": [{"id": "task-long", "status": "assigned"}],
        },
        metrics={},
    )

    def step(current, *, persist):
        current.state["tick"] += 1
        if current.state["tick"] == 3:
            current.state["tasks"][0]["status"] = "completed"

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(service, "_step_runtime", step)
    monkeypatch.setattr(service, "_persist", lambda _runtime: None)
    monkeypatch.setattr("app.services.simulation.asyncio.sleep", no_wait)

    asyncio.run(service._loop(runtime))

    assert runtime.state["tick"] == 3
    assert runtime.state["status"] == "completed"
    assert runtime.state["stopReason"] is None


def test_order_driven_runtime_skips_automatic_faults_and_urgent_insertion():
    service = _service()
    config = deepcopy(CONFIG)
    config["orderGenerator"]["urgentInsertionTick"] = 1
    config["faultInjection"]["robotFailureTick"] = 1
    config["faultInjection"]["robotFailureId"] = "r1"
    runtime = service._create_runtime("order-no-injection", TOPOLOGY, config)
    runtime.state["orderDriven"] = True
    initial_task_ids = [task["id"] for task in runtime.state["tasks"]]

    service._inject_urgent_order(runtime, 1)
    service._inject_faults(runtime, 1)

    assert [task["id"] for task in runtime.state["tasks"]] == initial_task_ids
    assert runtime.state["robots"][0]["failed"] is False
    assert runtime.state["robots"][0]["status"] == "idle"


def test_order_driven_tasks_keep_order_identity_and_inventory_allocation():
    service = _service()
    inventory = [
        {
            "nodeId": "node-storage",
            "cellId": "cell-r1-c2",
            "layer": 3,
            "skuId": "sku-order-1",
            "skuName": "六角螺栓 M8",
            "spec": "M8",
            "qty": 3,
        },
        {
            "nodeId": "node-storage-2",
            "cellId": "cell-r2-c2",
            "layer": 5,
            "skuId": "sku-order-1",
            "skuName": "六角螺栓 M8",
            "spec": "M8",
            "qty": 2,
        },
    ]
    orders = [
        {
            "id": "ORD-TEST-001",
            "priority": "urgent",
            "targetStation": "出库口 01",
            "createdAt": "2026-10-21T00:00:00Z",
            "lines": [
                {
                    "id": "line-1",
                    "skuId": "sku-order-1",
                    "skuName": "六角螺栓 M8",
                    "spec": "M8",
                    "quantity": 2,
                }
            ],
        }
    ]

    tasks, order_states = service._seed_order_tasks(TOPOLOGY, orders, inventory)

    assert len(tasks) == 1
    assert tasks[0]["orderId"] == "ORD-TEST-001"
    assert tasks[0]["urgent"] is True
    assert len(tasks[0]["lines"]) == 2
    assert order_states[0]["taskIds"] == [tasks[0]["id"]]
    assert order_states[0]["status"] == "waiting_dispatch"
    assert order_states[0]["lines"][0]["allocated"] == 2


def test_ctu_order_over_five_boxes_is_split_by_vehicle_capacity():
    service = _service()
    inventory = [
        {
            "nodeId": "node-storage",
            "cellId": "cell-r1-c1",
            "layer": layer,
            "skuId": "sku-capacity",
            "skuName": "容量测试料箱",
            "qty": 1,
        }
        for layer in range(1, 7)
    ]
    orders = [
        {
            "id": "ORD-CAPACITY",
            "priority": "normal",
            "targetStation": "出库口",
            "createdAt": "2026-10-22T00:00:00Z",
            "lines": [
                {
                    "id": "line-capacity",
                    "skuId": "sku-capacity",
                    "skuName": "容量测试料箱",
                    "quantity": 6,
                }
            ],
        }
    ]

    tasks, order_states = service._seed_order_tasks(TOPOLOGY, orders, inventory)

    assert [len(task["lines"]) for task in tasks] == [5, 1]
    assert all(len(task["lines"]) <= ROBOT_CARRY_CAPACITY["ctu"] for task in tasks)
    assert order_states[0]["totalTasks"] == 2


PICK_STATION_TOPOLOGY = {
    **TOPOLOGY,
    "id": "pick-station-topology",
    "name": "Pick Station Topology",
    "zones": [
        *TOPOLOGY["zones"],
        {"id": "zone-pick", "label": "人工拣货位", "type": "pick_station", "x": 5, "y": 1, "width": 3, "height": 2},
        {"id": "zone-drop", "label": "放置区", "type": "drop_zone", "x": 0, "y": 3, "width": 2, "height": 2},
    ],
}

PICK_STATION_CONFIG = deepcopy(CONFIG)
PICK_STATION_CONFIG.update(
    {
        "topologyId": PICK_STATION_TOPOLOGY["id"],
        "pickTicks": 2,
        "conveyorTransferTicks": 2,
        "liftTicks": 2,
        "placeTicks": 2,
    }
)
PICK_STATION_CONFIG["orderGenerator"]["maxOrders"] = 0


# 只有拣货位、没有放置区的拓扑：picking 结束后应直接进入 conveyor_return。
PICK_STATION_NO_DROP_TOPOLOGY = {
    **PICK_STATION_TOPOLOGY,
    "id": "pick-station-no-drop-topology",
    "name": "Pick Station No Drop Topology",
    "zones": [
        *TOPOLOGY["zones"],
        {"id": "zone-pick", "label": "人工拣货位", "type": "pick_station", "x": 5, "y": 1, "width": 3, "height": 2},
    ],
}


def _picking_task(service: SimulationService, runtime: RunRuntime, *, phase: str = "delivery_to_port") -> tuple[dict, dict, dict]:
    """构造一个已完成 CTU 取货、正准备交付的单箱任务，并放在人工拣货位拓扑里。"""
    slot = {
        "nodeId": "node-storage",
        "cellId": "cell-r1-c1",
        "layer": 3,
        "skuId": "sku-pick-cycle",
        "skuName": "人工拣货测试料箱",
        "qty": 4,
    }
    task = service._build_task(
        PICK_STATION_TOPOLOGY,
        "task-pick-cycle",
        "ctu",
        "direct",
        [dict(slot)],
        "cell-r5-c6",
        False,
        2,
    )
    task.update(
        {
            "status": "assigned",
            "phase": phase,
            "assignedRobotId": "r1",
            "picked": 1,
            "currentLineIndex": 1,
        }
    )
    robot = runtime.state["robots"][0]
    robot.update(
        {
            "currentTaskId": task["id"],
            "currentCellId": "cell-r5-c6",
            "currentNode": "cell-r5-c6",
            "currentRow": 5,
            "currentCol": 6,
            "lastCellId": "cell-r4-c6",
            "path": ["cell-r5-c6"],
            "pathLayers": [1],
            "load": 1,
            "carrying": [dict(slot)],
            "status": "moving",
        }
    )
    runtime.state["tasks"] = [task]
    runtime.state["inventory"] = []
    return task, robot, slot


def test_delivery_without_pick_station_still_completes_immediately():
    # 回归：拓扑没有 pick_station 时，交付动作结束后任务直接完成（保持原行为）。
    service = _service()
    runtime = service._create_runtime("test-no-pick-station", TOPOLOGY, deepcopy(CONFIG))
    task, robot, _ = _picking_task(service, runtime)
    task["deliveryCellId"] = "cell-r5-c6"

    # 交付动作链：升降对位 → 伸叉送箱 → 箱入输送线 → 完成
    for _ in range(4):
        service._advance_robot_phase(runtime, robot, task)

    assert task["status"] == "completed"
    assert task["phase"] == "completed"
    assert robot["currentTaskId"] is None
    assert robot["load"] == 0
    assert robot["carrying"] == []


def test_pick_station_cycle_returns_goods_to_original_slot_and_layer():
    service = _service()
    runtime = service._create_runtime("test-pick-cycle", PICK_STATION_TOPOLOGY, deepcopy(PICK_STATION_CONFIG))
    task, robot, slot = _picking_task(service, runtime)

    # 1) CTU 到达出入口：播放与取货对称的交付动作（升降对位 → 伸叉送箱 → 箱入输送线）
    service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "delivery_to_port"
    assert robot["handlingAction"]["stage"] == "lifting"
    service._advance_robot_phase(runtime, robot, task)
    assert robot["handlingAction"]["stage"] == "extending"
    service._advance_robot_phase(runtime, robot, task)
    assert robot["handlingAction"]["stage"] == "stowing"

    # 2) 交付动作完成：进入下层输送转运（尚未完成）
    service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "conveyor_to_lift"
    assert task["status"] != "completed"
    assert task["pickStationCellId"] == "cell-r2-c6"
    assert task["pickStationZoneId"] == "zone-pick"
    assert task["remainingStageTicks"] == PICK_STATION_CONFIG["conveyorTransferTicks"]
    assert robot["load"] == 0
    assert robot["carrying"] == []
    assert len(task["transitGoods"]) == 1

    # 3) 下层输送计时结束进入升降移栽，升降结束进入上层输送
    for _ in range(PICK_STATION_CONFIG["conveyorTransferTicks"]):
        service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "lift_to_upper"
    assert task["remainingStageTicks"] == PICK_STATION_CONFIG["liftTicks"]
    for _ in range(PICK_STATION_CONFIG["liftTicks"]):
        service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "conveyor_to_pick"
    assert task["remainingStageTicks"] == PICK_STATION_CONFIG["conveyorTransferTicks"]

    # 4) 上层输送计时结束进入人工拣货
    for _ in range(PICK_STATION_CONFIG["conveyorTransferTicks"]):
        service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "picking"
    assert task["remainingStageTicks"] == PICK_STATION_CONFIG["pickTicks"]

    # 5) 拣货计时结束：拣出的货物进入放置阶段
    for _ in range(PICK_STATION_CONFIG["pickTicks"]):
        service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "place_goods"
    assert task["dropZoneZoneId"] == "zone-drop"
    assert task["dropZoneCellId"] == "cell-r4-c1"
    assert task["remainingStageTicks"] == PICK_STATION_CONFIG["placeTicks"]
    assert robot["status"] == "waiting"

    # 6) 放置计时结束：记录放置货物快照，空货箱进入上层输送回程
    for _ in range(PICK_STATION_CONFIG["placeTicks"]):
        service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "conveyor_return"
    assert task["placedGoods"] == task["lines"]
    assert task["remainingStageTicks"] == PICK_STATION_CONFIG["conveyorTransferTicks"]

    # 7) 回程计时结束：CTU 到出入口取回货箱
    for _ in range(PICK_STATION_CONFIG["conveyorTransferTicks"]):
        service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "pickup_from_port"
    assert robot["targetCellId"] == task["deliveryCellId"]

    # 8) CTU 到达出入口取回货箱，前往原库位回库
    service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "return_to_storage"
    assert robot["targetCellId"] == slot["cellId"]
    assert robot["targetLayer"] == slot["layer"]
    assert len(robot["carrying"]) == 1

    # 9) 回到原库位原层后完成，货箱放回原 nodeId + layer
    service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "completed"
    assert task["status"] == "completed"
    assert task["completedTick"] is not None
    assert robot["carrying"] == []
    assert robot["load"] == 0
    restored = runtime.state["inventory"]
    assert len(restored) == 1
    assert restored[0]["nodeId"] == slot["nodeId"]
    assert restored[0]["layer"] == slot["layer"]
    assert restored[0]["qty"] == slot["qty"]
    assert runtime.metrics["completedTasks"] == 1

    phases = [entry["phase"] for entry in task["history"]]
    for phase in (
        "conveyor_to_lift",
        "lift_to_upper",
        "conveyor_to_pick",
        "picking",
        "place_goods",
        "conveyor_return",
        "pickup_from_port",
        "return_to_storage",
        "completed",
    ):
        assert phase in phases


def test_pick_station_without_drop_zone_skips_place_goods():
    # 有拣货位、无放置区：picking 结束后直接进入 conveyor_return，不产生放置数据。
    service = _service()
    runtime = service._create_runtime(
        "test-pick-no-drop", PICK_STATION_NO_DROP_TOPOLOGY, deepcopy(PICK_STATION_CONFIG)
    )
    task, robot, _ = _picking_task(service, runtime)
    # 交付动作 3 tick + 进入下层输送
    for _ in range(4):
        service._advance_robot_phase(runtime, robot, task)
    # 下层输送 → 升降 → 上层输送 → 拣货
    for _ in range(
        PICK_STATION_CONFIG["conveyorTransferTicks"]
        + PICK_STATION_CONFIG["liftTicks"]
        + PICK_STATION_CONFIG["conveyorTransferTicks"]
    ):
        service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "picking"
    # 拣货计时结束：没有放置区，直接回程
    for _ in range(PICK_STATION_CONFIG["pickTicks"]):
        service._advance_robot_phase(runtime, robot, task)
    assert task["phase"] == "conveyor_return"
    assert task["dropZoneZoneId"] is None
    assert task["dropZoneCellId"] is None
    assert task["placedGoods"] == []


def test_complete_picking_finishes_picking_immediately():
    service = _service()
    runtime = service._create_runtime("test-complete-picking", PICK_STATION_TOPOLOGY, deepcopy(PICK_STATION_CONFIG))
    task, robot, _ = _picking_task(service, runtime, phase="picking")
    task["pickStationZoneId"] = "zone-pick"
    task["pickStationCellId"] = "cell-r2-c6"
    task["remainingStageTicks"] = 6
    service.runtimes[runtime.run_id] = runtime

    asyncio.run(service.complete_picking(runtime.run_id, task["id"]))

    # 拓扑有放置区：跳过拣货后进入拣货放置阶段
    assert task["phase"] == "place_goods"
    assert task["remainingStageTicks"] == PICK_STATION_CONFIG["placeTicks"]
    assert task["dropZoneZoneId"] == "zone-drop"
    assert task["dropZoneCellId"] == "cell-r4-c1"
    assert robot["status"] == "waiting"
