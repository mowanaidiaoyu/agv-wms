import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.runtime import repo
from app.services.shuttle_topology import topology_revision
from app.services.transport_network import (
    audit_directed_path,
    audit_path,
    audit_transport_network,
    build_transport_network,
)


TOPOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "topology_666.json"
client = TestClient(app)


@pytest.fixture(scope="module")
def topology() -> dict:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def graph(topology: dict):
    return build_transport_network(topology)


@pytest.fixture(scope="module")
def audit(topology: dict):
    return audit_transport_network(topology)


def _issue_codes(audit) -> set[str]:
    return {issue.code for issue in audit.issues}


def _minimal_handoff_topology() -> dict:
    return {
        "id": "minimal-transport",
        "name": "Minimal transport contract",
        "meta": {"version": "1.0.0", "floors": ["1F"]},
        "grid": {"rows": 2, "cols": 4},
        "zones": [],
        "nodes": [],
        "cells": [
            {
                "id": "cell-r0-c0",
                "floor": "1F",
                "row": 0,
                "col": 0,
                "type": "road",
                "walkable": True,
            },
            {
                "id": "cell-r0-c1",
                "floor": "1F",
                "row": 0,
                "col": 1,
                "type": "conveyor",
                "walkable": True,
                "conveyorGround": True,
                "conveyorDirection": "right",
                "levels": 1,
            },
            {
                "id": "cell-r0-c2",
                "floor": "1F",
                "row": 0,
                "col": 2,
                "type": "conveyor",
                "walkable": True,
                "conveyorGround": True,
                "conveyorDirection": "right",
                "levels": 1,
            },
        ],
        "transportNetwork": {
            "handoffs": [
                {
                    "handoffId": "handoff-minimal",
                    "fromNetwork": "ctu",
                    "toNetwork": "ground_conveyor",
                    "deviceId": "device-minimal",
                    "inputNodeId": "ctu-cell:cell-r0-c0",
                    "outputNodeId": "conveyor-cell:cell-r0-c1",
                    "capacity": 1,
                    "validationStatus": "VALID",
                    "source": "test.transportNetwork.handoffs",
                    "provenance": "declared",
                    "evidence": ["Synthetic contract fixture"],
                }
            ]
        },
    }


def test_real_topology_id_and_revision(topology: dict, audit) -> None:
    assert audit.topologyId == "topology-6ceb4920"
    assert audit.topologyRevision == "2.0.0:760d47b9c5c88579"
    assert audit.topologyRevision == topology_revision(topology)


def test_left_ports_are_recognized_but_pick_station_is_missing(audit) -> None:
    assert audit.summary["portIds"] == [
        "zone-port",
        "zone-port-2",
        "zone-port-3",
        "zone-port-4",
    ]
    assert audit.summary["pickStationCount"] == 0
    assert "MISSING_PICK_STATION" in _issue_codes(audit)
    assert audit.leftRoute.status == "BLOCKED"


def test_dock_is_recognized_without_a_legal_conveyor_handoff(audit, graph) -> None:
    # 拓扑恢复后包含两个出货位（dock-02/03，dock-01 已随错误输送线删除）；audit 只对第一个 dock 生成衔接议题
    assert audit.summary["dockIds"] == ["dock-02", "dock-03"]
    assert "facility:dock-02" in graph.nodes
    assert "conveyor-cell:cell-r74-c17" in graph.nodes
    assert "MISSING_SHUTTLE_DOCK_HANDOFF" in _issue_codes(audit)
    result = audit_directed_path(
        graph,
        "facility:dock-02",
        "conveyor-cell:cell-r74-c17",
        "dock-to-conveyor",
    )
    assert result.status == "BLOCKED"


def test_c48_to_c52_gap_is_not_auto_connected(audit, graph) -> None:
    assert "CONVEYOR_GAP_R74_C49_C51" in _issue_codes(audit)
    result = audit_directed_path(
        graph,
        "conveyor-cell:cell-r74-c48",
        "conveyor-cell:cell-r74-c52",
        "ground-to-elevated-gap",
    )
    assert result.status == "BLOCKED"
    assert result.reachable is False


def test_directed_conveyor_does_not_allow_reverse_travel(graph) -> None:
    forward = audit_directed_path(
        graph,
        "conveyor-cell:cell-r74-c17",
        "conveyor-cell:cell-r74-c18",
        "forward",
    )
    reverse = audit_directed_path(
        graph,
        "conveyor-cell:cell-r74-c18",
        "conveyor-cell:cell-r74-c17",
        "reverse",
    )
    assert forward.status == "PASS"
    assert reverse.status == "BLOCKED"


def test_different_levels_require_a_lift_or_handoff() -> None:
    topology = {
        "id": "level-isolation",
        "name": "Level isolation",
        "meta": {"version": "1", "floors": ["1F"]},
        "grid": {"rows": 1, "cols": 1},
        "zones": [],
        "cells": [],
        "nodes": [],
        "transportNetwork": {
            "nodes": [
                {
                    "nodeId": "level-1",
                    "kind": "test",
                    "networkType": "shuttle",
                    "trackNodeId": "L1-test",
                    "level": 1,
                    "floor": "1F",
                    "row": 0,
                    "col": 0,
                    "physical": True,
                    "simulated": True,
                    "source": "test",
                    "provenance": "declared",
                },
                {
                    "nodeId": "level-2",
                    "kind": "test",
                    "networkType": "shuttle",
                    "trackNodeId": "L2-test",
                    "level": 2,
                    "floor": "1F",
                    "row": 0,
                    "col": 0,
                    "physical": True,
                    "simulated": True,
                    "source": "test",
                    "provenance": "declared",
                },
            ],
            "edges": [
                {
                    "edgeId": "illegal-level-edge",
                    "fromNodeId": "level-1",
                    "toNodeId": "level-2",
                    "edgeType": "track",
                    "direction": "up",
                    "distance": 0,
                    "source": "test",
                    "provenance": "declared",
                    "traversable": True,
                }
            ],
        },
    }
    strict_graph = build_transport_network(topology)
    assert strict_graph.edges["illegal-level-edge"].traversable is False
    assert audit_directed_path(strict_graph, "level-1", "level-2").status == "BLOCKED"


def test_ctu_and_shuttle_cannot_use_conveyor_as_vehicle_road(graph) -> None:
    for source_id, targets in graph.adjacency.items():
        source = graph.nodes[source_id]
        for target_id, _, is_handoff in targets:
            target = graph.nodes[target_id]
            if is_handoff:
                continue
            assert not (
                source.networkType in {"ctu", "shuttle"}
                and target.networkType in {"ground_conveyor", "elevated_conveyor"}
            )
    assert audit_directed_path(
        graph,
        "facility:ctu-01",
        "conveyor-cell:cell-r74-c17",
        "ctu-must-not-drive-on-conveyor",
    ).status == "BLOCKED"


def test_first_two_default_dock_sources_remain_unreachable(audit) -> None:
    sources = audit.summary["defaultDockSourcesAudited"]
    assert sources == [
        {"cellId": "cell-r96-c27", "level": 2},
        {"cellId": "cell-r96-c29", "level": 2},
    ]
    issues = [
        issue for issue in audit.issues if issue.code == "UNREACHABLE_DEFAULT_DOCK_SOURCE"
    ]
    assert [issue.sourceId for issue in issues] == [
        "cell-r96-c27/L2",
        "cell-r96-c29/L2",
    ]
    assert all("robotToSource=false" in issue.evidence for issue in issues)
    assert all("sourceToDock=false" in issue.evidence for issue in issues)


def test_missing_complete_routes_return_blocked_with_specific_issues(audit) -> None:
    assert audit.status == "BLOCKED"
    assert audit.leftRoute.status == "BLOCKED"
    assert audit.rightRoute.status == "BLOCKED"
    assert {
        "CTU_EXPLICIT_STOP_MISSING",
        "MISSING_CTU_PORT_HANDOFF",
        "MISSING_PICK_STATION",
        "MISSING_SHUTTLE_DOCK_HANDOFF",
        "CONVEYOR_GAP_R74_C49_C51",
        "MISSING_CONVEYOR_MERGE",
        "MISSING_OUTBOUND",
    }.issubset(_issue_codes(audit))


def test_minimal_topology_with_valid_handoff_can_pass() -> None:
    strict_graph = build_transport_network(_minimal_handoff_topology())
    result = audit_path(
        strict_graph,
        "minimal-valid-route",
        ["ctu-cell:cell-r0-c0", "conveyor-cell:cell-r0-c2"],
    )
    assert result.status == "PASS"
    assert result.hasLegalHandoffs is True
    assert "handoff:handoff-minimal" in result.edgeIds


def test_removing_a_critical_edge_blocks_the_minimal_route() -> None:
    topology = _minimal_handoff_topology()
    topology["cells"] = [
        cell for cell in topology["cells"] if cell["id"] != "cell-r0-c2"
    ]
    strict_graph = build_transport_network(topology)
    result = audit_path(
        strict_graph,
        "minimal-broken-route",
        ["ctu-cell:cell-r0-c0", "conveyor-cell:cell-r0-c2"],
    )
    assert result.status == "BLOCKED"


def test_read_only_api_has_no_repository_write_side_effects(
    monkeypatch: pytest.MonkeyPatch, topology: dict
) -> None:
    payload_before = deepcopy(topology)
    monkeypatch.setattr(
        repo,
        "get_topology_read_only",
        lambda topology_id: {
            "id": topology_id,
            "name": topology["name"],
            "payload": topology,
        }
        if topology_id == topology["id"]
        else None,
    )

    def fail_on_write(*_args, **_kwargs):
        raise AssertionError("The audit endpoint attempted a repository write")

    for method_name in ("upsert_topology", "delete_topology", "create_run", "update_run", "add_event"):
        monkeypatch.setattr(repo, method_name, fail_on_write)

    response = client.get(
        "/api/v1/topologies/topology-6ceb4920/transport-network/audit"
    )
    assert response.status_code == 200
    assert response.json()["status"] == "BLOCKED"
    assert topology == payload_before

    missing = client.get("/api/v1/topologies/missing/transport-network/audit")
    assert missing.status_code == 404
