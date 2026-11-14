from app.services.conveyor import (
    compile_conveyor_graph,
    normalize_conveyor_anchors,
    route_from_block,
    route_from_cell,
)
from app.services.runtime import repo
from app.services.simulation import RunRuntime, SimulationService


def _deck(input_side: str, output_side: str, role: str) -> dict:
    return {
        "enabled": True,
        "inputSide": input_side,
        "outputSide": output_side,
        "role": role,
        "capacity": 1,
        "speed": 1,
    }


def _dual_line_topology() -> dict:
    return {
        "cells": [
            {
                "id": "cell-r0-c0",
                "floor": "1F",
                "row": 0,
                "col": 0,
                "type": "conveyor",
                "levels": 2,
                "conveyorLineId": "line-a",
                "conveyorDecks": {
                    "lower": _deck("east", "west", "outbound"),
                    "upper": _deck("west", "east", "return"),
                },
            },
            {
                "id": "cell-r0-c2",
                "floor": "1F",
                "row": 0,
                "col": 2,
                "type": "conveyor",
                "levels": 2,
                "conveyorLineId": "line-a",
                "conveyorDecks": {
                    "lower": _deck("east", "south", "outbound"),
                    "upper": _deck("south", "east", "return"),
                },
            },
            {
                "id": "cell-r2-c2",
                "floor": "1F",
                "row": 2,
                "col": 2,
                "type": "conveyor",
                "levels": 2,
                "conveyorLineId": "line-a",
                "conveyorDecks": {
                    "lower": _deck("north", "south", "outbound"),
                    "upper": _deck("south", "north", "return"),
                },
            },
        ],
        "nodes": [],
    }


def test_normalize_legacy_conveyor_cells_share_one_explicit_anchor():
    topology = {
        "cells": [
            {
                "id": "cell-r0-c0",
                "floor": "1F",
                "row": 0,
                "col": 0,
                "type": "conveyor",
                "conveyorDecks": {"lower": _deck("south", "east", "outbound")},
            },
            {"id": "cell-r0-c1", "floor": "1F", "row": 0, "col": 1, "type": "conveyor"},
            {"id": "cell-r1-c0", "floor": "1F", "row": 1, "col": 0, "type": "conveyor"},
            {"id": "cell-r1-c1", "floor": "1F", "row": 1, "col": 1, "type": "conveyor"},
        ],
        "nodes": [],
    }

    normalized = normalize_conveyor_anchors(topology)

    assert all(cell["conveyorAnchor"] == {"row": 0, "col": 0} for cell in normalized["cells"])
    assert len([cell for cell in normalized["cells"] if cell.get("conveyorDecks")]) == 1
    assert "conveyorAnchor" not in topology["cells"][0]


def test_compile_dual_deck_line_follows_real_turns_in_opposite_directions():
    graph = compile_conveyor_graph(_dual_line_topology())

    assert route_from_cell(graph, "cell-r0-c0", "lower", ground=False) == [
        "1F:0:0:lower",
        "1F:0:2:lower",
        "1F:2:2:lower",
    ]
    assert route_from_cell(graph, "cell-r2-c2", "upper", ground=False) == [
        "1F:2:2:upper",
        "1F:0:2:upper",
        "1F:0:0:upper",
    ]


def test_touching_conveyor_lines_are_not_implicitly_connected():
    topology = _dual_line_topology()
    topology["cells"][1]["conveyorLineId"] = "line-b"
    topology["cells"][2]["conveyorLineId"] = "line-b"
    graph = compile_conveyor_graph(topology)

    assert graph["nodes"]["1F:0:0:lower"]["next"] is None
    assert graph["nodes"]["1F:0:2:lower"]["next"] == "1F:2:2:lower"


def test_merge_module_accepts_west_and_south_inputs_with_one_east_output():
    topology = {
        "cells": [
            {
                "id": "cell-r0-c0", "floor": "1F", "row": 0, "col": 0,
                "type": "conveyor", "levels": 2,
                "conveyorDecks": {"lower": _deck("west", "east", "outbound"), "upper": _deck("west", "east", "return")},
            },
            {
                "id": "cell-r0-c2", "floor": "1F", "row": 0, "col": 2,
                "type": "conveyor", "levels": 2,
                "conveyorDecks": {"lower": _deck("west", "east", "outbound"), "upper": {**_deck("west", "east", "return"), "inputSides": ["west", "south"]}},
            },
            {
                "id": "cell-r0-c4", "floor": "1F", "row": 0, "col": 4,
                "type": "conveyor", "levels": 2,
                "conveyorDecks": {"lower": _deck("west", "east", "outbound"), "upper": _deck("west", "east", "return")},
            },
            {
                "id": "cell-r2-c2", "floor": "1F", "row": 2, "col": 2,
                "type": "conveyor", "levels": 2,
                "conveyorDecks": {"lower": {**_deck("north", "south", "outbound"), "enabled": False}, "upper": _deck("south", "north", "return")},
            },
        ],
        "nodes": [],
        "zones": [],
    }

    graph = compile_conveyor_graph(topology)

    assert graph["issues"] == []
    assert graph["nodes"]["1F:0:2:upper"]["inputSides"] == ["west", "south"]
    assert route_from_block(graph, "1F:0:4", "upper") == [
        "1F:0:4:upper", "1F:0:2:upper", "1F:0:0:upper",
    ]
    assert route_from_block(graph, "1F:2:2", "upper") == [
        "1F:2:2:upper", "1F:0:2:upper", "1F:0:0:upper",
    ]


def test_partial_manual_configuration_does_not_treat_legacy_neighbor_as_broken():
    topology = _dual_line_topology()
    topology["cells"][1].pop("conveyorDecks")
    topology["cells"][1]["conveyorDirection"] = "down"

    assert compile_conveyor_graph(topology)["issues"] == []


def test_two_manual_neighbors_with_mismatched_sides_are_rejected():
    topology = _dual_line_topology()
    topology["cells"][1]["conveyorDecks"]["lower"] = _deck("north", "south", "outbound")

    assert any(
        issue.get("code") == "CONVEYOR_MANUAL_NEIGHBOR_DIRECTION_MISMATCH"
        for issue in compile_conveyor_graph(topology)["issues"]
    )


def test_turn_transfer_overrides_only_its_selected_deck():
    topology = _dual_line_topology()
    topology["nodes"] = [
        {
            "id": "turn-1",
            "type": "turn_transfer",
            "label": "上层转向移载机",
            "floor": "1F",
            "row": 0,
            "col": 2,
            "transferDeck": "upper",
            "transferInputSide": "south",
            "transferOutputSide": "east",
        }
    ]

    graph = compile_conveyor_graph(topology)

    assert graph["nodes"]["1F:0:2:upper"]["inputSide"] == "south"
    assert graph["nodes"]["1F:0:2:upper"]["outputSide"] == "east"
    assert graph["nodes"]["1F:0:2:lower"]["inputSide"] == "east"
    assert graph["nodes"]["1F:0:2:lower"]["outputSide"] == "south"


def test_turn_transfer_rejects_an_unavailable_upper_deck():
    topology = _dual_line_topology()
    topology["cells"][0]["levels"] = 1
    topology["cells"][0]["conveyorDecks"].pop("upper")
    topology["nodes"] = [
        {
            "id": "turn-1",
            "type": "turn_transfer",
            "label": "错误转向移载机",
            "floor": "1F",
            "row": 0,
            "col": 0,
            "transferDeck": "upper",
            "transferInputSide": "west",
            "transferOutputSide": "north",
        }
    ]

    assert any(
        "没有可用上层" in issue["message"]
        for issue in compile_conveyor_graph(topology)["issues"]
    )


def test_zone_binding_selects_exact_line_endpoint_and_validates_side():
    topology = _dual_line_topology()
    topology["zones"] = [
        {
            "id": "port-1",
            "label": "出库口 01",
            "type": "port",
            "x": 0,
            "y": 0,
            "width": 1,
            "height": 1,
            "conveyorBinding": {"blockId": "1F:0:0", "side": "east"},
        },
        {
            "id": "pick-1",
            "label": "拣货位 01",
            "type": "pick_station",
            "x": 2,
            "y": 2,
            "width": 1,
            "height": 1,
            "conveyorBinding": {"blockId": "1F:2:2", "side": "south"},
        },
    ]
    graph = compile_conveyor_graph(topology)

    assert graph["issues"] == []
    assert route_from_block(graph, "1F:0:0", "lower")[0] == "1F:0:0:lower"
    assert route_from_block(graph, "1F:2:2", "upper")[0] == "1F:2:2:upper"

    topology["zones"][0]["conveyorBinding"]["side"] = "north"
    assert any("下层入口侧" in issue["message"] for issue in compile_conveyor_graph(topology)["issues"])


def test_conveyor_capacity_blocks_then_releases_waiting_item(monkeypatch):
    graph = compile_conveyor_graph(_dual_line_topology())
    route = ["1F:0:0:lower", "1F:0:2:lower", "1F:2:2:lower"]
    first = {
        "id": "task-1",
        "orderId": "order-1",
        "status": "assigned",
        "phase": "conveyor_to_lift",
        "phaseLabel": "下层输送转运",
        "remainingStageTicks": 3,
        "lastProgressTick": 0,
        "startedTick": 0,
        "completedTick": None,
        "etaTicks": 3,
        "lines": [],
        "conveyorTransport": {
            "phase": "conveyor_to_lift",
            "deck": "lower",
            "route": route,
            "index": 0,
            "currentBlockId": route[0],
            "nextBlockId": route[1],
            "waitingTicks": 0,
            "movedTicks": 0,
            "startedTick": 0,
        },
    }
    second = {
        "id": "task-2",
        "orderId": "order-2",
        "status": "assigned",
        "phase": "conveyor_to_lift",
        "phaseLabel": "下层输送转运",
        "remainingStageTicks": 3,
        "lastProgressTick": 0,
        "startedTick": 0,
        "completedTick": None,
        "etaTicks": 3,
        "lines": [],
        "conveyorTransport": {
            "phase": "conveyor_to_lift",
            "deck": "lower",
            "route": route,
            "index": -1,
            "currentBlockId": None,
            "nextBlockId": route[0],
            "waitingTicks": 0,
            "movedTicks": 0,
            "startedTick": 0,
        },
    }
    runtime = RunRuntime(
        run_id="conveyor-capacity",
        topology=_dual_line_topology(),
        config={},
        state={"tick": 1, "tasks": [first, second], "robots": [], "queues": {}, "eventCount": 0},
        metrics={},
        conveyor_graph=graph,
    )
    service = SimulationService(repo)
    monkeypatch.setattr(service, "_record_event", lambda *_args, **_kwargs: None)

    assert service._advance_conveyor_transport(runtime, None, second) is False
    assert second["conveyorTransport"]["waitingTicks"] == 1
    assert second["conveyorTransport"]["currentBlockId"] is None

    assert service._advance_conveyor_transport(runtime, None, first) is False
    assert first["conveyorTransport"]["currentBlockId"] == route[1]
    assert service._advance_conveyor_transport(runtime, None, second) is False
    assert second["conveyorTransport"]["currentBlockId"] == route[0]
    assert second["conveyorTransport"]["waitingTicks"] == 0

    service._refresh_metrics(runtime)
    assert runtime.metrics["conveyorUtilization"] > 0
    assert len(runtime.state["conveyorItems"]) == 2
