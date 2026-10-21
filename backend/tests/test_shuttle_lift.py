from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import path_demo as path_demo_api
from app.api import routes as core_routes
from app.api import shuttle as shuttle_api
from app.main import app
from app.services.shuttle_pathfinding import plan_shuttle_path
from app.services.shuttle_topology import (
    ShuttleConfigStore,
    add_lift_neighbors,
    build_shuttle_track_graph,
    connected_components,
    level_summaries,
    topology_revision,
    track_node_id,
    scan_lift_candidates,
    validate_shuttle_configuration,
)


ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY_PATH = ROOT / "docs" / "topologies" / "666.json"
CONFIG_PATH = ROOT / "data" / "shuttle_configs" / "topology-6ceb4920.shuttle-v1.json"


@pytest.fixture(scope="module")
def topology() -> dict:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8-sig"))


@pytest.fixture(scope="module")
def graph(topology: dict) -> dict:
    return build_shuttle_track_graph(topology)


@pytest.fixture(scope="module")
def validation(topology: dict, graph: dict) -> dict:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return validate_shuttle_configuration(topology, config, graph)


def test_real_666_three_level_track_counts_and_unique_ids(graph: dict):
    assert level_summaries(graph) == [
        {"shuttle_level": 1, "node_count": 350, "component_count": 1, "component_sizes": [350]},
        {"shuttle_level": 2, "node_count": 348, "component_count": 1, "component_sizes": [348]},
        {"shuttle_level": 3, "node_count": 348, "component_count": 1, "component_sizes": [348]},
    ]
    assert len(graph) == len(set(graph)) == 1046
    assert graph["L1-r100-c19"]["world_y"] != graph["L2-r100-c19"]["world_y"]


@pytest.mark.parametrize("level", [1, 2, 3])
def test_each_real_track_level_is_connected(graph: dict, level: int):
    components = connected_components(graph, level)
    assert len(components) == 1
    assert all(graph[node_id]["shuttle_level"] == level for node_id in components[0])


def test_tracks_come_only_from_shuttle_slot_aisles(topology: dict, graph: dict):
    shuttle_zone_ids = {zone["id"] for zone in topology["zones"] if zone["type"] == "shuttle"}
    assert graph
    assert all(node["zone_id"] in shuttle_zone_ids for node in graph.values())
    assert not any(node["zone_id"].startswith("zone-ctu") for node in graph.values())


def test_two_lifts_are_fixed_distinct_and_program_verified(validation: dict, graph: dict):
    assert validation["planning_enabled"] is True
    lifts = {item["lift_id"]: item for item in validation["lifts"]}
    assert (lifts["shuttle-lift-a"]["row"], lifts["shuttle-lift-a"]["col"]) == (100, 19)
    assert (lifts["shuttle-lift-b"]["row"], lifts["shuttle-lift-b"]["col"]) == (188, 39)
    assert lifts["shuttle-lift-a"]["zone_id"] == "zone-shuttle-3"
    assert lifts["shuttle-lift-b"]["zone_id"] == "zone-shuttle-30"
    assert {(item["row"], item["col"]) for item in lifts.values()} == {(100, 19), (188, 39)}
    for lift in lifts.values():
        assert lift["capacity"] == 1
        assert lift["timing_source"] == "synthetic_demo"
        assert lift["verification_status"] == "program_verified_pending_human"
        for level in (1, 2, 3):
            node_id = track_node_id(level, lift["row"], lift["col"])
            assert lift["level_entrances"][f"L{level}"] == node_id
            assert lift["level_exits"][f"L{level}"] == node_id
            assert len(graph[node_id]["neighbors"]) >= 2


def test_candidate_scan_uses_only_three_level_real_aisles(topology: dict, graph: dict):
    candidates = scan_lift_candidates(topology, graph)
    keys = {(item["row"], item["col"], item["zone_id"]) for item in candidates}
    assert (100, 19, "zone-shuttle-3") in keys
    assert (188, 39, "zone-shuttle-30") in keys
    assert len(candidates) == len(keys) > 2
    assert all(item["minimum_same_level_degree"] >= 2 for item in candidates)


def test_invalid_overlapping_lifts_are_rejected(topology: dict, graph: dict):
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config["lifts"][1]["row"] = config["lifts"][0]["row"]
    config["lifts"][1]["col"] = config["lifts"][0]["col"]
    result = validate_shuttle_configuration(topology, config, deepcopy(graph))
    assert result["planning_enabled"] is False
    assert any("overlaps" in warning for warning in result["warnings"])


def test_lift_inside_rack_or_without_three_level_aisle_is_rejected(topology: dict, graph: dict):
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config["lifts"][0].update({"zone_id": "zone-shuttle-3", "row": 100, "col": 3})
    result = validate_shuttle_configuration(topology, config, deepcopy(graph))
    assert result["planning_enabled"] is False
    assert not any(item["lift_id"] == "shuttle-lift-a" for item in result["lifts"])


def test_non_lift_positions_never_receive_vertical_edges(graph: dict, validation: dict):
    adjacency = add_lift_neighbors(graph, validation["lifts"])
    assert all(meta["edge_type"] == "track" for _, meta in adjacency["L1-r100-c7"])
    lift_edges = [meta for _, meta in adjacency["L1-r100-c19"] if meta["edge_type"] == "lift"]
    assert {meta["lift"]["lift_id"] for meta in lift_edges} == {"shuttle-lift-a"}


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("L1-r100-c7", "L2-r100-c51"),
        ("L2-r100-c7", "L3-r100-c51"),
        ("L1-r100-c7", "L3-r100-c51"),
        ("L3-r188-c7", "L2-r188-c51"),
        ("L2-r188-c7", "L1-r188-c51"),
        ("L3-r188-c7", "L1-r188-c51"),
    ],
)
def test_all_six_cross_level_directions_are_plannable(graph: dict, validation: dict, start: str, end: str):
    result = plan_shuttle_path(graph, validation["lifts"], start, end, "astar")
    assert result["result_status"] == "success"
    assert result["chosen_lift"] in {"shuttle-lift-a", "shuttle-lift-b"}
    assert len(result["lift_transitions"]) == 1
    assert result["lift_transitions"][0]["events"] == [
        "enter_lift",
        "lift_started",
        "arrive_level",
        "exit_lift",
    ]


def test_astar_and_dijkstra_return_same_optimal_cost(graph: dict, validation: dict):
    args = (graph, validation["lifts"], "L1-r100-c7", "L3-r100-c51")
    astar = plan_shuttle_path(*args, "astar")
    dijkstra = plan_shuttle_path(*args, "dijkstra")
    assert astar["estimated_duration_seconds"] == pytest.approx(dijkstra["estimated_duration_seconds"])
    assert astar["horizontal_distance_cells"] == pytest.approx(dijkstra["horizontal_distance_cells"])
    assert astar["chosen_lift"] == dijkstra["chosen_lift"] == "shuttle-lift-a"


def test_lift_cost_uses_seconds_and_timeline_is_monotonic(graph: dict, validation: dict):
    result = plan_shuttle_path(graph, validation["lifts"], "L1-r100-c7", "L3-r100-c51", "astar")
    transition = result["lift_transitions"][0]
    assert transition["lift_start_seconds"] - transition["enter_at_seconds"] == pytest.approx(1.0)
    assert transition["arrive_level_seconds"] - transition["lift_start_seconds"] == pytest.approx(6.0)
    assert transition["exit_at_seconds"] - transition["arrive_level_seconds"] == pytest.approx(1.0)
    arrivals = [node["arrival_seconds"] for node in result["path_nodes"]]
    assert arrivals == sorted(arrivals)
    assert result["estimated_duration_seconds"] == arrivals[-1]


def test_same_input_is_deterministic_except_runtime_metric(graph: dict, validation: dict):
    first = plan_shuttle_path(graph, validation["lifts"], "L3-r188-c7", "L1-r188-c51", "astar")
    second = plan_shuttle_path(graph, validation["lifts"], "L3-r188-c7", "L1-r188-c51", "astar")
    for result in (first, second):
        result.pop("planning_time_ms")
    assert first == second


def test_missing_or_unavailable_lifts_fail_cross_level_planning(graph: dict, validation: dict):
    missing = plan_shuttle_path(graph, [], "L1-r100-c7", "L3-r100-c51", "astar")
    assert missing["result_status"] == "failed"
    disabled_lifts = [item for item in validation["lifts"] if item["lift_id"] != "shuttle-lift-a"]
    rerouted = plan_shuttle_path(graph, disabled_lifts, "L1-r100-c7", "L3-r100-c51", "astar")
    assert rerouted["result_status"] == "success"
    assert rerouted["chosen_lift"] == "shuttle-lift-b"
    unavailable = deepcopy(validation["lifts"])
    unavailable[0]["state"] = "unavailable"
    rerouted_by_state = plan_shuttle_path(graph, unavailable, "L1-r100-c7", "L3-r100-c51", "astar")
    assert rerouted_by_state["result_status"] == "success"
    assert rerouted_by_state["chosen_lift"] == "shuttle-lift-b"
    for lift in unavailable:
        lift["state"] = "unavailable"
    blocked = plan_shuttle_path(graph, unavailable, "L1-r100-c7", "L3-r100-c51", "astar")
    assert blocked["result_status"] == "failed"


def test_invalid_track_is_not_traversable(graph: dict, validation: dict):
    result = plan_shuttle_path(graph, validation["lifts"], "L1-r0-c0", "L3-r100-c51", "astar")
    assert result["result_status"] == "failed"
    assert "does not exist" in result["failure_reason"]


def test_unreviewed_or_revision_mismatched_config_cannot_plan(topology: dict, graph: dict):
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config["source_topology_revision"] = "2.0.0:stale"
    mismatch = validate_shuttle_configuration(topology, config, deepcopy(graph))
    assert mismatch["verification_status"] == "topology_version_mismatch"
    assert mismatch["planning_enabled"] is False
    missing = validate_shuttle_configuration(topology, None, deepcopy(graph))
    assert missing["verification_status"] == "unverified"
    assert missing["planning_enabled"] is False
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config["review_status"] = "unverified"
    draft = validate_shuttle_configuration(topology, config, deepcopy(graph))
    assert draft["verification_status"] == "unverified"
    assert draft["planning_enabled"] is False


def test_versioned_configuration_reloads_without_mutating_666(tmp_path: Path, topology: dict):
    before = hashlib.sha256(TOPOLOGY_PATH.read_bytes()).hexdigest()
    root = tmp_path / "shuttle_configs"
    root.mkdir()
    target = root / "topology-6ceb4920.shuttle-v1.json"
    target.write_bytes(CONFIG_PATH.read_bytes())
    first = ShuttleConfigStore(root).load(topology["id"])
    second = ShuttleConfigStore(root).load(topology["id"])
    assert first == second
    assert first["source_topology_revision"] == topology_revision(topology)
    assert hashlib.sha256(TOPOLOGY_PATH.read_bytes()).hexdigest() == before


def test_shuttle_api_exposes_real_tracks_lifts_and_planning(monkeypatch: pytest.MonkeyPatch, topology: dict):
    class FakeRepository:
        def get_topology(self, topology_id: str):
            if topology_id != topology["id"]:
                return None
            return {"id": topology["id"], "name": topology["name"], "payload": topology}

    monkeypatch.setattr(shuttle_api, "repo", FakeRepository())
    client = TestClient(app)
    levels = client.get(f"/api/v1/shuttle/topologies/{topology['id']}/levels")
    assert levels.status_code == 200
    assert [item["node_count"] for item in levels.json()["levels"]] == [350, 348, 348]
    lifts = client.get(f"/api/v1/shuttle/topologies/{topology['id']}/lifts")
    assert lifts.status_code == 200
    assert [item["lift_id"] for item in lifts.json()["lifts"]] == ["shuttle-lift-a", "shuttle-lift-b"]
    tracks = client.get(f"/api/v1/shuttle/topologies/{topology['id']}/tracks?level=2")
    assert tracks.status_code == 200
    assert len(tracks.json()["nodes"]) == 348
    planned = client.post(
        "/api/v1/shuttle/plan",
        json={
            "topology_id": topology["id"],
            "start_track_node": "L1-r100-c7",
            "start_level": 1,
            "end_track_node": "L3-r100-c51",
            "end_level": 3,
            "algorithm": "astar",
        },
    )
    assert planned.status_code == 200
    assert planned.json()["result_status"] == "success"
    assert planned.json()["chosen_lift"] == "shuttle-lift-a"


def test_shuttle_router_keeps_original_topology_and_path_demo_routes_available(
    monkeypatch: pytest.MonkeyPatch,
    topology: dict,
):
    record = {"id": topology["id"], "name": topology["name"], "payload": topology}

    class FakeRepository:
        def list_topologies(self):
            return [record]

        def get_topology(self, topology_id: str):
            return record if topology_id == topology["id"] else None

    repository = FakeRepository()
    monkeypatch.setattr(core_routes, "repo", repository)
    monkeypatch.setattr(path_demo_api, "repo", repository)
    monkeypatch.setattr(shuttle_api, "repo", repository)

    client = TestClient(app)
    listed = client.get("/api/v1/topologies")
    assert listed.status_code == 200
    assert [(item["id"], item["name"]) for item in listed.json()] == [(topology["id"], "666")]

    detail = client.get(f"/api/v1/topologies/{topology['id']}")
    assert detail.status_code == 200
    assert len(detail.json()["payload"]["cells"]) == 2484

    options = client.get(f"/api/v1/path-demo/topologies/{topology['id']}/options")
    assert options.status_code == 200
    assert options.json()["default_start"]
    assert options.json()["default_goal"]

    shuttle = client.get(f"/api/v1/shuttle/topologies/{topology['id']}/levels")
    assert shuttle.status_code == 200
    assert [level["shuttle_level"] for level in shuttle.json()["levels"]] == [1, 2, 3]


def test_api_rejects_track_level_mismatch(monkeypatch: pytest.MonkeyPatch, topology: dict):
    class FakeRepository:
        def get_topology(self, topology_id: str):
            return {"id": topology["id"], "name": topology["name"], "payload": topology}

    monkeypatch.setattr(shuttle_api, "repo", FakeRepository())
    response = TestClient(app).post(
        "/api/v1/shuttle/plan",
        json={
            "topology_id": topology["id"],
            "start_track_node": "L2-r100-c7",
            "start_level": 1,
            "end_track_node": "L3-r100-c51",
            "end_level": 3,
            "algorithm": "dijkstra",
        },
    )
    assert response.status_code == 422
