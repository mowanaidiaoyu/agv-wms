from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import shuttle as shuttle_api
from app.main import app
from app.services.shuttle_multi_planner import (
    ShuttleMultiPlanningError,
    _candidate_conflict,
    _track_action,
    _validate_inputs,
    build_shuttle_multi_scenarios,
    plan_shuttle_multi,
    validate_shuttle_multi_plan,
)
from app.services.shuttle_pathfinding import plan_shuttle_path
from app.services.shuttle_topology import (
    ShuttleConfigStore,
    build_shuttle_track_graph,
    validate_shuttle_configuration,
)
from app.shuttle_multi_schemas import (
    ShuttleMultiPlanRequest,
    ShuttleMultiPlanResponse,
    ShuttleMultiPlanningOptions,
)


ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY_PATH = ROOT / "docs" / "topologies" / "666.json"


@pytest.fixture(scope="module")
def topology() -> dict:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8-sig"))


@pytest.fixture(scope="module")
def graph(topology: dict) -> dict:
    return build_shuttle_track_graph(topology)


@pytest.fixture(scope="module")
def validation(topology: dict, graph: dict) -> dict:
    return validate_shuttle_configuration(
        topology, ShuttleConfigStore().load(topology["id"]), graph
    )


@pytest.fixture(scope="module")
def scenarios(graph: dict, validation: dict) -> list[dict]:
    return build_shuttle_multi_scenarios(graph, validation["lifts"])


def options(**overrides) -> dict:
    payload = ShuttleMultiPlanningOptions().model_dump()
    payload.update(overrides)
    return payload


def execute(
    topology: dict,
    graph: dict,
    validation: dict,
    vehicles: list[dict],
    **option_overrides,
) -> dict:
    return plan_shuttle_multi(
        graph,
        validation["lifts"],
        vehicles,
        options(**option_overrides),
        topology_id=topology["id"],
        topology_version=topology["meta"]["version"],
        topology_revision=validation["topology_revision"],
        config_revision=validation["config_revision"],
    )


def scenario(scenarios: list[dict], scenario_id: str) -> dict:
    return next(item for item in scenarios if item["scenario_id"] == scenario_id)


def custom_vehicle(
    vehicle_id: str, start: str, goal: str, priority: int, algorithm: str = "astar"
) -> dict:
    return {
        "vehicle_id": vehicle_id,
        "start_track_node_id": start,
        "target_track_node_id": goal,
        "priority": priority,
        "algorithm": algorithm,
    }


def test_duplicate_persistent_terminal_is_rejected_before_space_time_search(
    monkeypatch: pytest.MonkeyPatch, topology: dict, graph: dict, validation: dict
):
    vehicles = [
        custom_vehicle("SHUTTLE-01", "L1-r100-c7", "L4-r100-c51", 1),
        custom_vehicle("SHUTTLE-02", "L1-r100-c35", "L4-r100-c51", 2),
    ]

    def must_not_search(*_args, **_kwargs):
        raise AssertionError("space-time search must not run for duplicate persistent terminals")

    monkeypatch.setattr("app.services.shuttle_multi_planner._search_vehicle", must_not_search)
    with pytest.raises(ShuttleMultiPlanningError) as caught:
        execute(topology, graph, validation, vehicles)
    assert caught.value.failure_code == "duplicate_terminal_destination"
    assert caught.value.details == {
        "track_node_id": "L4-r100-c51",
        "vehicle_ids": ["SHUTTLE-01", "SHUTTLE-02"],
        "goal_occupancy": "persistent",
    }


def test_three_vehicle_duplicate_terminal_names_all_involved_vehicles(
    topology: dict, graph: dict, validation: dict
):
    vehicles = [
        custom_vehicle("SHUTTLE-01", "L1-r100-c7", "L4-r100-c51", 1),
        custom_vehicle("SHUTTLE-02", "L1-r100-c35", "L4-r100-c51", 2),
        custom_vehicle("SHUTTLE-03", "L4-r100-c7", "L1-r100-c51", 3, "dijkstra"),
    ]
    with pytest.raises(ShuttleMultiPlanningError) as caught:
        execute(topology, graph, validation, vehicles)
    assert caught.value.failure_code == "duplicate_terminal_destination"
    assert caught.value.expanded_states is None
    assert caught.value.details["vehicle_ids"] == ["SHUTTLE-01", "SHUTTLE-02"]


def test_same_row_column_on_different_levels_is_not_a_duplicate_terminal(
    graph: dict, validation: dict
):
    vehicles = [
        custom_vehicle("SHUTTLE-01", "L1-r100-c7", "L1-r100-c51", 1),
        custom_vehicle("SHUTTLE-02", "L2-r100-c7", "L2-r100-c51", 2),
    ]
    _validate_inputs(graph, validation["lifts"], vehicles, options())


def test_distinct_terminal_nodes_with_overlapping_vehicle_boxes_are_rejected(
    graph: dict, validation: dict
):
    overlapping = deepcopy(graph)
    first_goal = overlapping["L1-r100-c51"]
    second_goal = overlapping["L2-r100-c51"]
    second_goal["world_x"] = first_goal["world_x"] + 0.25
    second_goal["world_y"] = first_goal["world_y"]
    second_goal["world_z"] = first_goal["world_z"]
    vehicles = [
        custom_vehicle("SHUTTLE-01", "L1-r100-c7", "L1-r100-c51", 1),
        custom_vehicle("SHUTTLE-02", "L2-r100-c7", "L2-r100-c51", 2),
    ]
    with pytest.raises(ShuttleMultiPlanningError) as caught:
        _validate_inputs(overlapping, validation["lifts"], vehicles, options())
    assert caught.value.failure_code == "terminal_geometry_conflict"
    assert caught.value.details["track_node_ids"] == ["L1-r100-c51", "L2-r100-c51"]


@pytest.mark.parametrize("vehicle_count", [2, 3, 4])
def test_two_to_four_shuttles_run_without_conflict_on_real_separate_levels(
    topology: dict, graph: dict, validation: dict, scenarios: list[dict], vehicle_count: int
):
    base = scenario(scenarios, "parallel")["vehicle_requests"][0]
    vehicles = []
    for level in range(1, vehicle_count + 1):
        vehicles.append(
            {
                **base,
                "vehicle_id": f"SHUTTLE-0{level}",
                "start_track_node_id": base["start_track_node_id"].replace("L1-", f"L{level}-"),
                "target_track_node_id": base["target_track_node_id"].replace("L1-", f"L{level}-"),
                "priority": level,
            }
        )
    result = execute(topology, graph, validation, vehicles)
    assert result["status"] == "planning_success"
    assert result["metrics"]["planned_vehicle_count"] == vehicle_count
    assert result["safety_validation"] == {
        "node_conflicts": 0,
        "edge_conflicts": 0,
        "geometric_conflicts": 0,
        "lift_resource_conflicts": 0,
        "timing_violations": 0,
        "safe": True,
    }


def test_real_scenario_b_detects_node_competition_and_resolves_with_legal_wait(
    topology: dict, graph: dict, validation: dict, scenarios: list[dict]
):
    selected = scenario(scenarios, "track-conflict")
    result = execute(topology, graph, validation, selected["vehicle_requests"])
    assert selected["evidence"]["competition_track_node_id"] == "L1-r100-c31"
    assert result["metrics"]["detected_track_node_conflicts"] > 0
    assert result["metrics"]["total_wait_time_seconds"] > 0
    yielding = next(vehicle for vehicle in result["vehicles"] if vehicle["vehicle_id"] == "SHUTTLE-02")
    assert yielding["wait_intervals"]
    assert all(wait["track_node_id"] in graph for wait in yielding["wait_intervals"])
    assert result["safety_validation"]["safe"] is True
    assert any(event["event_type"] == "track_node_conflict_detected" for event in result["events"])


def test_reverse_edge_swap_is_detected_before_continuous_geometry(graph: dict):
    source = graph["L1-r98-c31"]
    target = graph["L1-r100-c31"]
    forward = _track_action(source, target, 0, None, 0.25)
    reverse = _track_action(target, source, 0, None, 0.25)
    conflict = _candidate_conflict(
        reverse["samples"],
        [{"vehicle_id": "SHUTTLE-01", "trajectory": forward["samples"]}],
        1.0,
    )
    assert conflict is not None
    assert conflict[0] == "edge"


def test_continuous_perpendicular_motion_detects_between_tick_geometry():
    def pose(tick: int, x: float, z: float, edge_from: str, edge_to: str) -> dict:
        return {
            "time_step": tick,
            "world_x": x,
            "world_y": 0.35,
            "world_z": z,
            "track_node_id": None,
            "action": "move",
            "lift_id": None,
            "edge_from": edge_from,
            "edge_to": edge_to,
        }

    first = [pose(0, -1, 0, "west", "east"), pose(1, 1, 0, "west", "east")]
    second = [pose(0, 0, -1, "south", "north"), pose(1, 0, 1, "south", "north")]
    conflict = _candidate_conflict(
        first, [{"vehicle_id": "SHUTTLE-02", "trajectory": second}], 1.0
    )
    assert conflict is not None
    assert conflict[0] == "geometry"
    assert conflict[4]["minimum_distance_cells"] == pytest.approx(0)


def test_waiting_vehicle_keeps_occupying_its_real_track_node(
    topology: dict, graph: dict, validation: dict
):
    tiny = {
        node_id: deepcopy(graph[node_id])
        for node_id in ("L1-r98-c31", "L1-r100-c31", "L1-r102-c31")
    }
    tiny["L1-r98-c31"]["neighbors"] = ["L1-r100-c31"]
    tiny["L1-r100-c31"]["neighbors"] = ["L1-r98-c31", "L1-r102-c31"]
    tiny["L1-r102-c31"]["neighbors"] = ["L1-r100-c31"]
    vehicles = [
        {"vehicle_id": "SHUTTLE-01", "start_track_node_id": "L1-r98-c31", "target_track_node_id": "L1-r100-c31", "priority": 1, "algorithm": "astar"},
        {"vehicle_id": "SHUTTLE-02", "start_track_node_id": "L1-r102-c31", "target_track_node_id": "L1-r98-c31", "priority": 2, "algorithm": "astar"},
    ]
    with pytest.raises(ShuttleMultiPlanningError, match="no safe plan"):
        plan_shuttle_multi(
            tiny,
            [],
            vehicles,
            options(max_time_steps=40, max_expanded_states=5000),
            topology_id=topology["id"],
            topology_version=topology["meta"]["version"],
            topology_revision=validation["topology_revision"],
            config_revision=validation["config_revision"],
        )


def test_real_scenario_c_serializes_lift_a_and_models_empty_reposition(
    topology: dict, graph: dict, validation: dict, scenarios: list[dict]
):
    result = execute(
        topology, graph, validation, scenario(scenarios, "lift-conflict")["vehicle_requests"]
    )
    assert result["status"] == "planning_success"
    assert result["metrics"]["lift_resource_contentions"] == 1
    assert result["metrics"]["lift_a_usage_count"] == 2
    usages = [usage for vehicle in result["vehicles"] for usage in vehicle["lift_usage"]]
    first, second = usages
    assert first["exit_end_seconds"] <= second["reposition_start_seconds"]
    assert second["initial_cabin_level"] == first["final_cabin_level"] == 4
    assert second["reposition_end_seconds"] - second["reposition_start_seconds"] == pytest.approx(6.0)
    assert second["enter_start_seconds"] >= second["reposition_end_seconds"]
    assert any(event["event_type"] == "lift_reservation_denied" for event in result["events"])
    assert any(event["event_type"] == "lift_reposition_started" for event in result["events"])
    assert result["safety_validation"]["lift_resource_conflicts"] == 0


def test_two_lifts_keep_independent_capacity_one_timelines(
    topology: dict, graph: dict, validation: dict
):
    vehicles = [
        {"vehicle_id": "SHUTTLE-A", "start_track_node_id": "L1-r100-c21", "target_track_node_id": "L4-r98-c19", "priority": 1, "algorithm": "astar"},
        {"vehicle_id": "SHUTTLE-B", "start_track_node_id": "L1-r188-c41", "target_track_node_id": "L4-r190-c39", "priority": 2, "algorithm": "astar"},
    ]
    result = execute(topology, graph, validation, vehicles)
    usages = [usage for vehicle in result["vehicles"] for usage in vehicle["lift_usage"]]
    assert {usage["lift_id"] for usage in usages} == {"shuttle-lift-a", "shuttle-lift-b"}
    assert result["metrics"]["lift_a_usage_count"] == 1
    assert result["metrics"]["lift_b_usage_count"] == 1
    assert result["safety_validation"]["lift_resource_conflicts"] == 0


def test_lift_b_simultaneous_requests_are_serialized_at_capacity_one(
    topology: dict, graph: dict, validation: dict
):
    vehicles = [
        {"vehicle_id": "SHUTTLE-01", "start_track_node_id": "L1-r188-c41", "target_track_node_id": "L4-r190-c39", "priority": 1, "algorithm": "astar"},
        {"vehicle_id": "SHUTTLE-02", "start_track_node_id": "L2-r190-c39", "target_track_node_id": "L3-r188-c41", "priority": 2, "algorithm": "astar"},
    ]
    result = execute(topology, graph, validation, vehicles)
    usages = [usage for vehicle in result["vehicles"] for usage in vehicle["lift_usage"]]
    assert [usage["lift_id"] for usage in usages] == ["shuttle-lift-b", "shuttle-lift-b"]
    assert usages[0]["exit_end_seconds"] <= usages[1]["reposition_start_seconds"]
    assert result["metrics"]["lift_resource_contentions"] == 1
    assert result["safety_validation"]["lift_resource_conflicts"] == 0


def test_real_graph_can_select_an_alternate_lift_when_it_finishes_earlier(
    topology: dict, graph: dict, validation: dict
):
    vehicles = [
        {"vehicle_id": "SHUTTLE-01", "start_track_node_id": "L1-r142-c31", "target_track_node_id": "L4-r142-c31", "priority": 1, "algorithm": "astar"},
        {"vehicle_id": "SHUTTLE-02", "start_track_node_id": "L2-r142-c31", "target_track_node_id": "L3-r142-c31", "priority": 2, "algorithm": "astar"},
    ]
    baseline = plan_shuttle_path(
        graph, validation["lifts"], "L2-r142-c31", "L3-r142-c31", "astar"
    )
    result = execute(topology, graph, validation, vehicles)
    second = next(vehicle for vehicle in result["vehicles"] if vehicle["vehicle_id"] == "SHUTTLE-02")
    assert baseline["chosen_lift"] == "shuttle-lift-b"
    assert second["lift_usage"][0]["lift_id"] == "shuttle-lift-a"
    assert second["lift_usage"][0]["used_as_alternate"] is True
    assert result["metrics"]["alternate_lift_selections"] == 1


@pytest.mark.parametrize(
    ("start", "goal"),
    [
        ("L1-r100-c21", "L4-r98-c19"),
        ("L4-r98-c19", "L1-r100-c21"),
    ],
)
def test_bidirectional_l1_l4_plans_use_real_lift_edges(
    topology: dict, graph: dict, validation: dict, start: str, goal: str
):
    companion_start = start.replace("r100-c21", "r102-c19").replace("r98-c19", "r100-c21")
    companion_goal = goal.replace("r98-c19", "r102-c19").replace("r100-c21", "r98-c19")
    if companion_start not in graph or companion_goal not in graph:
        companion_start = "L2-r100-c21"
        companion_goal = "L3-r98-c19"
    vehicles = [
        {"vehicle_id": "SHUTTLE-01", "start_track_node_id": start, "target_track_node_id": goal, "priority": 1, "algorithm": "astar"},
        {"vehicle_id": "SHUTTLE-02", "start_track_node_id": companion_start, "target_track_node_id": companion_goal, "priority": 2, "algorithm": "astar"},
    ]
    result = execute(topology, graph, validation, vehicles)
    assert result["status"] == "planning_success"
    assert all(vehicle["lift_usage"] for vehicle in result["vehicles"])


def test_different_levels_at_same_projection_are_geometrically_independent(
    topology: dict, graph: dict, validation: dict, scenarios: list[dict]
):
    result = execute(
        topology, graph, validation, scenario(scenarios, "parallel")["vehicle_requests"]
    )
    assert result["metrics"]["detected_geometric_conflicts"] == 0
    assert result["safety_validation"]["geometric_conflicts"] == 0


def test_same_direction_track_spacing_is_checked_continuously(graph: dict):
    rear = _track_action(graph["L1-r98-c31"], graph["L1-r100-c31"], 0, None, 0.25)
    front = _track_action(graph["L1-r100-c31"], graph["L1-r102-c31"], 0, None, 0.25)
    safe = _candidate_conflict(
        rear["samples"],
        [{"vehicle_id": "SHUTTLE-FRONT", "trajectory": front["samples"]}],
        1.282,
    )
    assert safe is None

    delayed_front = [
        deepcopy(front["samples"][0]),
        deepcopy(front["samples"][0]),
        deepcopy(front["samples"][0]),
        *deepcopy(front["samples"][1:]),
    ]
    for tick, sample in enumerate(delayed_front):
        sample["time_step"] = tick
    unsafe = _candidate_conflict(
        rear["samples"],
        [{"vehicle_id": "SHUTTLE-FRONT", "trajectory": delayed_front}],
        1.282,
    )
    assert unsafe is not None
    assert unsafe[0] in {"node", "geometry"}


def test_time_contract_records_discretization_geometry_and_all_search_limits(
    topology: dict, graph: dict, validation: dict, scenarios: list[dict]
):
    result = execute(
        topology, graph, validation, scenario(scenarios, "parallel")["vehicle_requests"],
        max_vehicles=3, max_time_steps=900, max_expanded_states=200000,
        max_lift_candidates=1, max_replans=2,
    )
    contract = result["time_contract"]
    assert contract["time_step_seconds"] == 0.25
    assert contract["interval_semantics"] == "half_open_[start,end)"
    assert contract["duration_rounding"] == "ceiling_never_round_down"
    assert contract["timing_source"] == "synthetic_demo"
    assert contract["search_limits"] == {
        "max_vehicles": 3,
        "max_time_steps": 900,
        "max_expanded_states": 200000,
        "max_lift_candidates": 1,
        "max_replans": 2,
    }
    assert result["metrics"]["replans_used"] == 0


def test_configured_offline_initial_cabin_level_drives_empty_reposition(
    topology: dict, graph: dict, validation: dict
):
    lifts = deepcopy(validation["lifts"])
    next(lift for lift in lifts if lift["lift_id"] == "shuttle-lift-b")["state"] = "unavailable"
    vehicles = [
        {"vehicle_id": "SHUTTLE-01", "start_track_node_id": "L1-r100-c21", "target_track_node_id": "L4-r98-c19", "priority": 1, "algorithm": "astar"},
        {"vehicle_id": "SHUTTLE-02", "start_track_node_id": "L3-r98-c31", "target_track_node_id": "L3-r102-c31", "priority": 2, "algorithm": "astar"},
    ]
    result = plan_shuttle_multi(
        graph, lifts, vehicles,
        options(lift_initial_levels={"shuttle-lift-a": 4, "shuttle-lift-b": 1}),
        topology_id=topology["id"], topology_version=topology["meta"]["version"],
        topology_revision=validation["topology_revision"], config_revision=validation["config_revision"],
    )
    usage = result["vehicles"][0]["lift_usage"][0]
    assert usage["initial_cabin_level"] == 4
    assert usage["reposition_end_seconds"] - usage["reposition_start_seconds"] == pytest.approx(9.0)
    assert result["lift_initial_state"]["shuttle-lift-a"] == 4


def test_lift_entrance_transfer_and_exit_samples_match_reserved_timeline(
    topology: dict, graph: dict, validation: dict
):
    vehicles = [
        {"vehicle_id": "SHUTTLE-01", "start_track_node_id": "L1-r100-c21", "target_track_node_id": "L4-r98-c19", "priority": 1, "algorithm": "astar"},
        {"vehicle_id": "SHUTTLE-02", "start_track_node_id": "L3-r98-c31", "target_track_node_id": "L3-r102-c31", "priority": 2, "algorithm": "astar"},
    ]
    result = execute(topology, graph, validation, vehicles)
    vehicle = result["vehicles"][0]
    usage = vehicle["lift_usage"][0]
    by_time = {sample["time_seconds"]: sample for sample in vehicle["timed_trajectory"]}
    assert by_time[usage["transfer_start_seconds"]]["action"] == "lift_enter"
    assert by_time[usage["transfer_end_seconds"]]["action"] == "lift"
    assert by_time[usage["exit_end_seconds"]]["action"] == "lift_exit"
    lift = next(item for item in validation["lifts"] if item["lift_id"] == usage["lift_id"])
    assert by_time[usage["exit_end_seconds"]]["track_node_id"] == lift["level_exits"]["L4"]


def test_non_lift_track_node_never_allows_a_cross_level_transition(
    topology: dict, graph: dict, validation: dict
):
    lifts = deepcopy(validation["lifts"])
    for lift in lifts:
        lift["state"] = "unavailable"
    vehicles = [
        {"vehicle_id": "SHUTTLE-01", "start_track_node_id": "L1-r100-c31", "target_track_node_id": "L4-r100-c31", "priority": 1, "algorithm": "astar"},
        {"vehicle_id": "SHUTTLE-02", "start_track_node_id": "L2-r98-c31", "target_track_node_id": "L3-r102-c31", "priority": 2, "algorithm": "astar"},
    ]
    with pytest.raises(ShuttleMultiPlanningError, match="no legal ShuttleGraph route") as unreachable:
        plan_shuttle_multi(
            graph, lifts, vehicles, options(max_time_steps=80),
            topology_id=topology["id"], topology_version=topology["meta"]["version"],
            topology_revision=validation["topology_revision"], config_revision=validation["config_revision"],
        )
    assert unreachable.value.failure_code == "unreachable_track"


def test_one_unavailable_lift_reroutes_all_cross_level_work_to_the_other(
    topology: dict, graph: dict, validation: dict, scenarios: list[dict]
):
    lifts = deepcopy(validation["lifts"])
    next(lift for lift in lifts if lift["lift_id"] == "shuttle-lift-a")["state"] = "unavailable"
    vehicles = scenario(scenarios, "lift-conflict")["vehicle_requests"]
    result = plan_shuttle_multi(
        graph, lifts, vehicles, options(max_time_steps=2400),
        topology_id=topology["id"], topology_version=topology["meta"]["version"],
        topology_revision=validation["topology_revision"], config_revision=validation["config_revision"],
    )
    assert result["status"] == "planning_success"
    assert all(
        usage["lift_id"] == "shuttle-lift-b"
        for vehicle in result["vehicles"] for usage in vehicle["lift_usage"]
    )


def test_search_limits_and_invalid_nodes_fail_without_an_unsafe_partial_plan(
    topology: dict, graph: dict, validation: dict, scenarios: list[dict]
):
    vehicles = scenario(scenarios, "lift-conflict")["vehicle_requests"]
    with pytest.raises(ShuttleMultiPlanningError, match="max_expanded_states") as limited:
        execute(topology, graph, validation, vehicles, max_expanded_states=100)
    assert limited.value.failure_code == "search_limit_exceeded"
    assert limited.value.vehicle_id
    assert limited.value.expanded_states == 101
    assert limited.value.max_expanded_states == 100
    invalid = deepcopy(vehicles)
    invalid[0]["start_track_node_id"] = "L1-r0-c0"
    with pytest.raises(ShuttleMultiPlanningError, match="non-traversable ShuttleGraph node") as unreachable:
        execute(topology, graph, validation, invalid)
    assert unreachable.value.failure_code == "unreachable_track"


def test_user_candidate_three_vehicle_configuration_plans_within_existing_limit(
    topology: dict, graph: dict, validation: dict
):
    vehicles = [
        custom_vehicle("SHUTTLE-01", "L1-r100-c7", "L4-r100-c51", 1),
        custom_vehicle("SHUTTLE-02", "L1-r100-c35", "L4-r100-c43", 2),
        custom_vehicle("SHUTTLE-03", "L4-r100-c7", "L1-r100-c51", 3, "dijkstra"),
    ]
    result = execute(topology, graph, validation, vehicles)
    assert result["status"] == "planning_success"
    assert [vehicle["visited_states"] for vehicle in result["vehicles"]] == [430, 15011, 74050]
    assert max(vehicle["visited_states"] for vehicle in result["vehicles"]) < options()["max_expanded_states"]
    assert result["safety_validation"]["safe"] is True
    assert result["metrics"]["unresolved_conflict_count"] == 0


def test_priority_order_and_repeated_plans_are_deterministic(
    topology: dict, graph: dict, validation: dict, scenarios: list[dict]
):
    vehicles = scenario(scenarios, "track-conflict")["vehicle_requests"]
    first = execute(topology, graph, validation, list(reversed(vehicles)))
    second = execute(topology, graph, validation, vehicles)
    assert first["planning_id"] == second["planning_id"]
    assert [vehicle["vehicle_id"] for vehicle in first["vehicles"]] == ["SHUTTLE-01", "SHUTTLE-02"]
    assert [vehicle["timed_trajectory"] for vehicle in first["vehicles"]] == [
        vehicle["timed_trajectory"] for vehicle in second["vehicles"]
    ]
    assert first["events"] == second["events"]


@pytest.mark.parametrize("scenario_id", ["parallel", "track-conflict", "lift-conflict"])
def test_real_scenarios_have_traceable_nodes_and_final_safe_plans(
    topology: dict, graph: dict, validation: dict, scenarios: list[dict], scenario_id: str
):
    selected = scenario(scenarios, scenario_id)
    assert selected["evidence"]["source"] == "real_shuttle_graph"
    assert all(
        vehicle[endpoint] in graph
        for vehicle in selected["vehicle_requests"]
        for endpoint in ("start_track_node_id", "target_track_node_id")
    )
    result = execute(topology, graph, validation, selected["vehicle_requests"])
    assert result["status"] == "planning_success"
    assert result["metrics"]["unresolved_conflict_count"] == 0
    assert result["safety_validation"]["safe"] is True


def test_event_times_match_wait_and_lift_reservations(
    topology: dict, graph: dict, validation: dict, scenarios: list[dict]
):
    result = execute(
        topology, graph, validation, scenario(scenarios, "lift-conflict")["vehicle_requests"]
    )
    usage = result["vehicles"][1]["lift_usage"][0]
    requested = next(
        event for event in result["events"]
        if event["event_type"] == "lift_requested" and event["vehicle_ids"] == ["SHUTTLE-02"]
    )
    exited = next(
        event for event in result["events"]
        if event["event_type"] == "lift_exited" and event["vehicle_ids"] == ["SHUTTLE-02"]
    )
    assert requested["simulation_time_seconds"] == usage["request_time_seconds"]
    assert exited["simulation_time_seconds"] == usage["exit_end_seconds"]


def test_final_validator_rejects_overlapping_lift_reservations():
    reservation = {
        "lift_id": "shuttle-lift-a", "reposition_start_tick": 0, "reposition_end_tick": 0,
        "enter_start_tick": 0, "transfer_start_tick": 4, "transfer_end_tick": 16,
        "exit_end_tick": 20,
    }
    safety = validate_shuttle_multi_plan([], [reservation, {**reservation}], 1.0)
    assert safety["lift_resource_conflicts"] == 1
    assert safety["safe"] is False


def test_multi_schema_enforces_two_to_four_unique_shuttles(validation: dict, scenarios: list[dict]):
    vehicle = scenario(scenarios, "parallel")["vehicle_requests"][0]
    base = {
        "topology_id": "topology-6ceb4920",
        "topology_revision": validation["topology_revision"],
        "shuttle_config_revision": validation["config_revision"],
        "coordinate_contract_version": "shuttle-internal-levels-v1",
    }
    with pytest.raises(ValueError):
        ShuttleMultiPlanRequest.model_validate({**base, "vehicle_requests": [vehicle]})
    with pytest.raises(ValueError):
        ShuttleMultiPlanRequest.model_validate({**base, "vehicle_requests": [vehicle, vehicle]})
    five = [{**vehicle, "vehicle_id": f"S-{index}"} for index in range(5)]
    with pytest.raises(ValueError):
        ShuttleMultiPlanRequest.model_validate({**base, "vehicle_requests": five})


def test_multi_api_exposes_real_scenarios_and_safe_plan(
    monkeypatch: pytest.MonkeyPatch,
    topology: dict,
    validation: dict,
):
    class FakeRepository:
        def get_topology(self, topology_id: str):
            if topology_id != topology["id"]:
                return None
            return {"id": topology["id"], "name": topology["name"], "payload": topology}

    monkeypatch.setattr(shuttle_api, "repo", FakeRepository())
    client = TestClient(app)
    listed = client.get(f"/api/v1/shuttle/multi-scenarios?topology_id={topology['id']}")
    assert listed.status_code == 200
    payload = listed.json()
    assert [item["scenario_id"] for item in payload["scenarios"]] == [
        "parallel", "track-conflict", "lift-conflict"
    ]
    planned = client.post(
        "/api/v1/shuttle/plan-multi",
        json={
            "topology_id": topology["id"],
            "topology_revision": validation["topology_revision"],
            "shuttle_config_revision": validation["config_revision"],
            "coordinate_contract_version": "shuttle-internal-levels-v1",
            "vehicle_requests": payload["scenarios"][1]["vehicle_requests"],
        },
    )
    assert planned.status_code == 200
    result = ShuttleMultiPlanResponse.model_validate(planned.json())
    assert result.status == "planning_success"
    assert result.safety_validation.safe is True
    assert result.metrics.detected_track_node_conflicts > 0


def test_api_rejects_stale_topology_or_config_without_reusing_a_plan(
    monkeypatch: pytest.MonkeyPatch, topology: dict, validation: dict, scenarios: list[dict]
):
    class FakeRepository:
        def get_topology(self, topology_id: str):
            return {"id": topology["id"], "name": topology["name"], "payload": topology}

    monkeypatch.setattr(shuttle_api, "repo", FakeRepository())
    response = TestClient(app).post(
        "/api/v1/shuttle/plan-multi",
        json={
            "topology_id": topology["id"],
            "topology_revision": "stale",
            "shuttle_config_revision": validation["config_revision"],
            "coordinate_contract_version": "shuttle-internal-levels-v1",
            "vehicle_requests": scenario(scenarios, "parallel")["vehicle_requests"],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "Topology revision mismatch" in response.json()["failure_reason"]
    assert response.json()["failure"]["failure_code"] == "configuration_mismatch"
    assert response.json()["vehicles"] == []


def test_multi_api_returns_structured_duplicate_terminal_failure_without_partial_plan(
    monkeypatch: pytest.MonkeyPatch, topology: dict, validation: dict
):
    class FakeRepository:
        def get_topology(self, topology_id: str):
            return {"id": topology["id"], "name": topology["name"], "payload": topology}

    monkeypatch.setattr(shuttle_api, "repo", FakeRepository())
    response = TestClient(app).post(
        "/api/v1/shuttle/plan-multi",
        json={
            "topology_id": topology["id"],
            "topology_revision": validation["topology_revision"],
            "shuttle_config_revision": validation["config_revision"],
            "coordinate_contract_version": "shuttle-internal-levels-v1",
            "vehicle_requests": [
                custom_vehicle("SHUTTLE-01", "L1-r100-c7", "L4-r100-c51", 1),
                custom_vehicle("SHUTTLE-02", "L1-r100-c35", "L4-r100-c51", 2),
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["vehicles"] == []
    assert body["failure"]["failure_code"] == "duplicate_terminal_destination"
    assert body["failure"]["expanded_states"] is None
    assert body["failure"]["details"]["track_node_id"] == "L4-r100-c51"
    assert body["failure"]["planning_time_ms"] >= 0


def test_original_single_shuttle_astar_and_dijkstra_still_match(graph: dict, validation: dict):
    args = (graph, validation["lifts"], "L1-r100-c7", "L4-r100-c51")
    astar = plan_shuttle_path(*args, "astar")
    dijkstra = plan_shuttle_path(*args, "dijkstra")
    assert astar["result_status"] == dijkstra["result_status"] == "success"
    assert astar["estimated_duration_seconds"] == pytest.approx(dijkstra["estimated_duration_seconds"])
    assert astar["chosen_lift"] == dijkstra["chosen_lift"] == "shuttle-lift-a"
