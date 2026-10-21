import random

from fastapi.testclient import TestClient

from app.main import app
from app.services import goods, simulation
from app.services.goods import build_inventory, load_catalog, sample_order_lines
from app.services.runtime import repo
from app.services.simulation import SimulationService


client = TestClient(app)


TOPOLOGY = {
    "id": "goods-topology",
    "name": "Goods Topology",
    "meta": {"version": "2.0.0"},
    "grid": {"rows": 2, "cols": 2, "cellSize": 32, "originX": 0, "originY": 0},
    "zones": [
        {"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 2, "height": 2},
        {"id": "zone-port", "label": "Port", "type": "port", "x": 1, "y": 0, "width": 1, "height": 1},
    ],
    "cells": [
        {"id": "cell-r0-c0", "row": 0, "col": 0, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
        {"id": "cell-r0-c1", "row": 0, "col": 1, "type": "road", "zoneId": "zone-ctu", "cost": 1.0, "walkable": True},
    ],
    "nodes": [
        {"id": "ctu-a", "type": "storage", "row": 0, "col": 0, "zoneId": "zone-ctu", "capacity": 1, "label": "A"},
        {"id": "ctu-b", "type": "storage", "row": 0, "col": 1, "zoneId": "zone-ctu", "capacity": 1, "label": "B"},
    ],
}

CONFIG = {
    "id": "goods-config",
    "topologyId": "goods-topology",
    "robots": [],
    "orderGenerator": {"ratePerMinute": 2, "maxOrders": 4, "linesPerOrderMin": 1, "linesPerOrderMax": 2, "urgentInsertionTick": 4},
    "dispatchPolicy": "greedy",
    "pathPolicy": "astar",
    "conveyorPolicy": {"releaseThreshold": 0.8, "fifo": True},
    "faultInjection": {},
    "seed": 7,
    "durationTicks": 10,
}

CATALOG = {
    "version": 1,
    "skus": [
        {"skuId": "sku-1", "name": "六角螺母", "largeCategory": "螺母", "subclass": "六角螺母", "spec": "M5", "grade": "8", "material": "碳钢", "surfaceTreatment": "蓝白锌"},
        {"skuId": "sku-2", "name": "六角螺栓", "largeCategory": "螺栓", "subclass": "六角螺栓", "spec": "M6", "grade": "8.8", "material": "合金钢", "surfaceTreatment": "镀锌"},
    ],
}


def _service() -> SimulationService:
    return SimulationService(repo)


def test_load_catalog_missing_returns_empty(monkeypatch):
    monkeypatch.setattr(goods, "CATALOG_PATH", goods.CATALOG_PATH.parent / "does-not-exist.json")
    assert load_catalog() == {"version": 1, "skus": []}


def test_build_inventory_deterministic():
    assert build_inventory(TOPOLOGY, CATALOG) == build_inventory(TOPOLOGY, CATALOG)


def test_build_inventory_slot_count_and_layers():
    inventory = build_inventory(TOPOLOGY, CATALOG)
    # 2 个 CTU 库位节点 × 17 层 = 34 槽
    assert len(inventory) == 34
    assert {(slot["nodeId"], slot["layer"]) for slot in inventory} == {(node_id, layer) for node_id in ("ctu-a", "ctu-b") for layer in range(1, 18)}
    assert all(slot["skuId"] in {"sku-1", "sku-2"} for slot in inventory)


def test_build_inventory_empty_catalog():
    assert build_inventory(TOPOLOGY, {"version": 1, "skus": []}) == []


def test_build_inventory_covers_all_ctu_zones():
    # 666 拓扑把 CTU 库拆成多个列区（zone-ctu、zone-ctu-2 …），
    # 精确匹配 zone-ctu 会漏掉其余列区，必须按 zone 类型全部纳入。
    topology = {
        **TOPOLOGY,
        "zones": [
            {"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 1, "height": 1},
            {"id": "zone-ctu-2", "label": "CTU", "type": "ctu", "x": 1, "y": 0, "width": 1, "height": 1},
        ],
        "nodes": [
            {"id": "ctu-a", "type": "storage", "row": 0, "col": 0, "zoneId": "zone-ctu", "capacity": 1, "label": "A"},
            {"id": "ctu-c", "type": "storage", "row": 0, "col": 1, "zoneId": "zone-ctu-2", "capacity": 1, "label": "C"},
        ],
    }
    inventory = build_inventory(topology, CATALOG)
    node_ids = {slot["nodeId"] for slot in inventory}
    assert node_ids == {"ctu-a", "ctu-c"}
    assert len(inventory) == 2 * 17


def test_build_inventory_no_ctu_nodes_empty():
    topology = {
        **TOPOLOGY,
        "nodes": [{"id": "robot", "type": "ctu_robot", "row": 0, "col": 0, "zoneId": "zone-ctu", "capacity": 1, "label": "S"}],
    }
    assert build_inventory(topology, CATALOG) == []


def test_sample_order_lines_carry_sku_and_stay_within_inventory():
    inventory = build_inventory(TOPOLOGY, CATALOG)
    rng = random.Random(7)
    lines = sample_order_lines(inventory, 3, rng)
    assert len(lines) == 3
    keys = {(line["nodeId"], line["layer"]) for line in lines}
    assert keys <= {(slot["nodeId"], slot["layer"]) for slot in inventory}
    assert all(line.get("skuId") and line.get("skuName") for line in lines)


def test_seed_tasks_lines_carry_sku(monkeypatch):
    monkeypatch.setattr(simulation, "build_inventory", lambda topology: build_inventory(TOPOLOGY, CATALOG))
    service = _service()
    tasks = service._seed_tasks(TOPOLOGY, CONFIG)
    ctu_tasks = [task for task in tasks if task["robotType"] == "ctu"]
    assert ctu_tasks
    for task in ctu_tasks:
        assert all(line.get("skuId") for line in task["lines"])


def test_build_task_legacy_line_keeps_three_fields():
    service = _service()
    task = service._build_task(
        TOPOLOGY,
        "task-legacy",
        "ctu",
        "direct",
        [{"nodeId": "ctu-a", "layer": 3}],
        "cell-r0-c0",
        False,
        2,
    )
    assert set(task["lines"][0].keys()) == {"nodeId", "cellId", "layer"}


def test_goods_inventory_endpoint():
    client.post("/api/v1/topologies", json=TOPOLOGY)
    response = client.get("/api/v1/goods/inventory/goods-topology")
    assert response.status_code == 200
    body = response.json()
    assert body["topologyId"] == "goods-topology"
    assert len(body["slots"]) == 34


def test_goods_inventory_endpoint_missing_topology():
    response = client.get("/api/v1/goods/inventory/no-such-topology")
    assert response.status_code == 404
