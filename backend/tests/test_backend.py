from fastapi.testclient import TestClient

from app.main import app
from app.services.assignment import assign_tasks
from app.services.pathfinding import shortest_path


client = TestClient(app)


TOPOLOGY = {
    "id": "test-topology",
    "name": "Test Topology",
    "meta": {"version": "1.0.0", "viewport": {"x": 0, "y": 0, "zoom": 1}, "tags": []},
    "zones": [{"id": "zone-ctu", "label": "CTU", "type": "ctu", "x": 0, "y": 0, "width": 100, "height": 100}],
    "nodes": [
        {"id": "a", "type": "storage", "x": 0, "y": 0, "zoneId": "zone-ctu", "capacity": 1, "label": "A"},
        {"id": "b", "type": "storage", "x": 100, "y": 0, "zoneId": "zone-ctu", "capacity": 1, "label": "B"},
        {"id": "c", "type": "station", "x": 200, "y": 0, "zoneId": "zone-ctu", "capacity": 1, "label": "C"},
    ],
    "edges": [
        {"id": "ab", "from": "a", "to": "b", "type": "aisle", "length": 1, "bidirectional": True, "capacity": 1, "speedLimit": 1},
        {"id": "bc", "from": "b", "to": "c", "type": "aisle", "length": 1, "bidirectional": True, "capacity": 1, "speedLimit": 1},
    ],
    "resources": [{"id": "station-1", "type": "station", "label": "Station 1", "nodeIds": ["c"], "edgeIds": [], "capacity": 1, "meta": {}}],
}

SCENARIO = {
    "id": "test-scenario",
    "name": "Test Scenario",
    "topologyId": "test-topology",
    "robots": [{"id": "r1", "type": "ctu", "startNode": "a", "speed": 1.0, "battery": 100, "enabled": True}],
    "orderGenerator": {"ratePerMinute": 2, "maxOrders": 4, "linesPerOrderMin": 1, "linesPerOrderMax": 2, "urgentInsertionTick": 4},
    "dispatchPolicy": "greedy",
    "pathPolicy": "astar",
    "conveyorPolicy": {"releaseThreshold": 0.8, "fifo": True},
    "faultInjection": {"robotFailureTick": None, "robotFailureId": None, "conveyorJammedTick": None, "conveyorEdgeId": None},
    "seed": 7,
    "durationTicks": 10,
}


def test_shortest_path():
    assert shortest_path(TOPOLOGY, "a", "c", "astar") == ["a", "b", "c"]


def test_assignment():
    robots = [{"id": "r1", "type": "ctu", "status": "idle", "currentNode": "a", "battery": 100}]
    tasks = [{"id": "t1", "robotType": "ctu", "status": "pending", "sourceNode": "b", "targetNode": "c"}]
    assert assign_tasks("greedy", robots, tasks, TOPOLOGY, "astar") == [("r1", "t1")]


def test_topology_roundtrip():
    response = client.post("/api/v1/topologies", json=TOPOLOGY)
    assert response.status_code == 200
    result = client.get("/api/v1/topologies/test-topology")
    assert result.status_code == 200
    assert result.json()["payload"]["name"] == "Test Topology"


def test_topology_validate():
    response = client.post("/api/v1/topologies/validate", json=TOPOLOGY)
    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["errors"] == []


def test_algorithm_evaluation():
    client.post("/api/v1/topologies", json=TOPOLOGY)
    scenario_response = client.post("/api/v1/scenarios", json=SCENARIO)
    assert scenario_response.status_code == 200
    response = client.post(
        "/api/v1/algorithms/evaluate",
        json={"topologyId": TOPOLOGY["id"], "scenarioId": SCENARIO["id"]},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 6
    assert {item["category"] for item in results} == {"dispatch", "path", "conveyor"}
