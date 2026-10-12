from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import path_demo as path_demo_api
from app.services.path_demo import PathDemoError, analyze_path_demo_topology, plan_path_demo


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def topology_666() -> dict:
    with (PROJECT_ROOT / "docs" / "topologies" / "666.json").open(encoding="utf-8") as handle:
        topology = json.load(handle)
    assert topology["name"] == "666"
    assert topology["id"] == "topology-6ceb4920"
    return topology


def test_reads_real_666_topology(topology_666: dict):
    assert len(topology_666["cells"]) == 2484
    assert any(cell["type"] == "blocked" for cell in topology_666["cells"])


def test_finds_largest_walkable_component_and_distant_defaults(topology_666: dict):
    analysis = analyze_path_demo_topology(topology_666)
    assert len(analysis["components"]) == 4
    assert analysis["components"][0]["cell_count"] == 1608
    assert analysis["default_start"]["component_id"] == analysis["largest_component_id"]
    assert analysis["default_goal"]["component_id"] == analysis["largest_component_id"]
    assert analysis["default_start"]["type"] == "road"
    assert analysis["default_goal"]["type"] == "road"
    distance = abs(analysis["default_start"]["row"] - analysis["default_goal"]["row"]) + abs(
        analysis["default_start"]["col"] - analysis["default_goal"]["col"]
    )
    assert distance >= 20


def test_astar_returns_stable_legal_path_on_666(topology_666: dict):
    analysis = analyze_path_demo_topology(topology_666)
    start = analysis["default_start"]["id"]
    goal = analysis["default_goal"]["id"]

    first = plan_path_demo(topology_666, start, goal, "astar")
    second = plan_path_demo(topology_666, start, goal, "astar")
    walkable_ids = {
        cell["id"]
        for cell in topology_666["cells"]
        if cell.get("walkable", True) and cell.get("type") != "blocked"
    }

    assert [cell["id"] for cell in first["path_cells"]] == [cell["id"] for cell in second["path_cells"]]
    assert first["visited_nodes"] == second["visited_nodes"]
    assert all(cell["id"] in walkable_ids for cell in first["path_cells"])
    for previous, current in zip(first["path_cells"], first["path_cells"][1:]):
        assert previous["floor"] == current["floor"]
        assert abs(previous["row"] - current["row"]) + abs(previous["col"] - current["col"]) == 1


def test_astar_and_dijkstra_have_equal_optimal_cost_on_666(topology_666: dict):
    analysis = analyze_path_demo_topology(topology_666)
    start = analysis["default_start"]["id"]
    goal = analysis["default_goal"]["id"]
    astar = plan_path_demo(topology_666, start, goal, "astar")
    dijkstra = plan_path_demo(topology_666, start, goal, "dijkstra")
    assert astar["total_cost"] == pytest.approx(dijkstra["total_cost"])
    assert astar["total_distance"] == dijkstra["total_distance"]


def test_rejects_blocked_cell(topology_666: dict):
    analysis = analyze_path_demo_topology(topology_666)
    blocked = next(cell for cell in topology_666["cells"] if cell["type"] == "blocked")
    with pytest.raises(PathDemoError, match="not a traversable road cell"):
        plan_path_demo(topology_666, blocked["id"], analysis["default_goal"]["id"], "astar")


def test_rejects_cells_in_different_components(topology_666: dict):
    analysis = analyze_path_demo_topology(topology_666)
    first = next(cell for cell in analysis["traversable_cells"] if cell["component_id"] == "component-1" and cell["type"] == "road")
    second = next(cell for cell in analysis["traversable_cells"] if cell["component_id"] == "component-2" and cell["type"] == "road")
    with pytest.raises(PathDemoError, match="different connected components") as exc_info:
        plan_path_demo(topology_666, first["id"], second["id"], "astar")
    assert exc_info.value.status_code == 409


def test_path_demo_api_uses_isolated_repository(monkeypatch: pytest.MonkeyPatch, topology_666: dict):
    class FakeRepository:
        def get_topology(self, topology_id: str):
            if topology_id != topology_666["id"]:
                return None
            return {"id": topology_666["id"], "name": topology_666["name"], "payload": topology_666}

    monkeypatch.setattr(path_demo_api, "repo", FakeRepository())
    app = FastAPI()
    app.include_router(path_demo_api.router, prefix="/api/v1")
    client = TestClient(app)

    options_response = client.get(f"/api/v1/path-demo/topologies/{topology_666['id']}/options")
    assert options_response.status_code == 200
    options = options_response.json()

    response = client.post(
        "/api/v1/path-demo/plan",
        json={
            "topology_id": topology_666["id"],
            "start_cell_id": options["default_start"]["id"],
            "goal_cell_id": options["default_goal"]["id"],
            "algorithm": "astar",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["topology_id"] == topology_666["id"]
    assert payload["topology_version"] == topology_666["meta"]["version"]
    assert payload["path_cells"][0]["id"] == options["default_start"]["id"]
    assert payload["path_cells"][-1]["id"] == options["default_goal"]["id"]

    invalid = client.post(
        "/api/v1/path-demo/plan",
        json={
            "topology_id": topology_666["id"],
            "start_cell_id": options["default_start"]["id"],
            "goal_cell_id": options["default_goal"]["id"],
            "algorithm": "random-walk",
        },
    )
    assert invalid.status_code == 422
