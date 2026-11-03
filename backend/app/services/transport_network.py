from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..schemas import (
    CandidateFacility,
    HandoffPoint,
    NetworkIssue,
    PathAudit,
    PathAuditSegment,
    TransportEdge,
    TransportNetworkAuditResponse,
    TransportNode,
)
from .pathfinding import (
    DEFAULT_FLOOR,
    cell_id,
    node_position,
    parse_cell_id,
    port_entry_cell,
    shuttle_rack_slots,
    shortest_path_shuttle,
    storage_entry_dirs,
)
from .shuttle_topology import build_shuttle_track_graph, topology_revision
from .unified_transfer import shuttle_context


DIRECTION_DELTAS: dict[str, tuple[int, int]] = {
    "right": (0, 1),
    "left": (0, -1),
    "up": (-1, 0),
    "down": (1, 0),
}


@dataclass
class TransportGraph:
    """Read-only directed graph built from topology facts and declared contracts."""

    nodes: dict[str, TransportNode] = field(default_factory=dict)
    edges: dict[str, TransportEdge] = field(default_factory=dict)
    handoffs: dict[str, HandoffPoint] = field(default_factory=dict)
    adjacency: dict[str, list[tuple[str, str, bool]]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_node(self, node: TransportNode) -> None:
        self.nodes[node.nodeId] = node
        self.adjacency.setdefault(node.nodeId, [])

    def add_edge(self, edge: TransportEdge) -> None:
        source = self.nodes.get(edge.fromNodeId)
        target = self.nodes.get(edge.toNodeId)
        traversable = bool(edge.traversable and source is not None and target is not None)
        if source is not None and target is not None:
            if source.networkType != target.networkType:
                traversable = False
            if source.floor != target.floor:
                traversable = False
            if source.level != target.level and edge.edgeType != "lift":
                traversable = False
        if traversable != edge.traversable:
            edge = edge.model_copy(update={"traversable": traversable})
        self.edges[edge.edgeId] = edge
        if traversable:
            self.adjacency.setdefault(edge.fromNodeId, []).append(
                (edge.toNodeId, edge.edgeId, False)
            )

    def add_handoff(self, handoff: HandoffPoint) -> None:
        self.handoffs[handoff.handoffId] = handoff
        source = self.nodes.get(handoff.inputNodeId)
        target = self.nodes.get(handoff.outputNodeId)
        valid = (
            handoff.validationStatus == "VALID"
            and handoff.capacity > 0
            and bool(handoff.deviceId)
            and source is not None
            and target is not None
            and source.networkType == handoff.fromNetwork
            and target.networkType == handoff.toNetwork
        )
        if valid:
            self.adjacency.setdefault(handoff.inputNodeId, []).append(
                (handoff.outputNodeId, f"handoff:{handoff.handoffId}", True)
            )


def _floor(value: Any) -> str:
    return str(value or DEFAULT_FLOOR)


def _cell_level(cell: dict[str, Any]) -> int:
    return 1 if cell.get("conveyorGround") else max(1, int(cell.get("levels") or 1))


def _road_node_id(cell_value: str) -> str:
    return f"ctu-cell:{cell_value}"


def _conveyor_node_id(cell_value: str) -> str:
    return f"conveyor-cell:{cell_value}"


def _shuttle_track_node_id(track_value: str) -> str:
    return f"shuttle-track:{track_value}"


def _storage_node_id(storage_value: str) -> str:
    return f"storage:{storage_value}"


def _shuttle_storage_node_id(cell_value: str, level: int) -> str:
    return f"shuttle-storage:{cell_value}:L{level}"


def _add_declared_contract(graph: TransportGraph, topology: dict[str, Any]) -> None:
    """Load an optional future G1-B contract without mutating the topology.

    Current topology 666 has no such section. Supporting it here keeps the G1-A
    validator testable and gives G1-B one explicit, reviewable data contract.
    """

    contract = topology.get("transportNetwork") or {}
    for raw in contract.get("nodes") or []:
        graph.add_node(TransportNode.model_validate(raw))
    for raw in contract.get("edges") or []:
        graph.add_edge(TransportEdge.model_validate(raw))
    for raw in contract.get("handoffs") or []:
        graph.add_handoff(HandoffPoint.model_validate(raw))


def build_transport_network(topology: dict[str, Any]) -> TransportGraph:
    graph = TransportGraph()
    cells = {
        (_floor(cell.get("floor")), int(cell["row"]), int(cell["col"])): cell
        for cell in topology.get("cells", [])
    }

    # CTUs may only use explicit road cells in this audit graph. Blank grid
    # positions accepted by the legacy planner are deliberately absent.
    road_positions: dict[tuple[str, int, int], str] = {}
    conveyor_positions: dict[tuple[str, int, int], str] = {}
    for position, cell in cells.items():
        if cell.get("type") == "road" and cell.get("walkable", True):
            node_id = _road_node_id(str(cell["id"]))
            road_positions[position] = node_id
            graph.add_node(
                TransportNode(
                    nodeId=node_id,
                    kind="ctu_road",
                    networkType="ctu",
                    topologyCellId=str(cell["id"]),
                    level=1,
                    floor=position[0],
                    row=position[1],
                    col=position[2],
                    physical=True,
                    simulated=True,
                    source="topology.cells",
                    provenance="topology",
                )
            )
        elif cell.get("type") == "conveyor":
            node_id = _conveyor_node_id(str(cell["id"]))
            conveyor_positions[position] = node_id
            is_ground = bool(cell.get("conveyorGround"))
            graph.add_node(
                TransportNode(
                    nodeId=node_id,
                    kind="conveyor_cell",
                    networkType="ground_conveyor" if is_ground else "elevated_conveyor",
                    topologyCellId=str(cell["id"]),
                    level=_cell_level(cell),
                    floor=position[0],
                    row=position[1],
                    col=position[2],
                    physical=True,
                    simulated=True,
                    source="topology.cells",
                    provenance="topology",
                )
            )

    for position, source_id in road_positions.items():
        floor, row, col = position
        for d_row, d_col in DIRECTION_DELTAS.values():
            target_id = road_positions.get((floor, row + d_row, col + d_col))
            if target_id is None:
                continue
            graph.add_edge(
                TransportEdge(
                    edgeId=f"ctu-road:{source_id}->{target_id}",
                    fromNodeId=source_id,
                    toNodeId=target_id,
                    edgeType="road",
                    direction="directed_record_of_bidirectional_road",
                    distance=1.0,
                    source="derived:adjacent_explicit_road_cells",
                    provenance="derived",
                    traversable=True,
                )
            )

    for position, source_id in conveyor_positions.items():
        cell = cells[position]
        direction = str(cell.get("conveyorDirection") or "")
        delta = DIRECTION_DELTAS.get(direction)
        if delta is None:
            continue
        target_position = (position[0], position[1] + delta[0], position[2] + delta[1])
        target_id = conveyor_positions.get(target_position)
        if target_id is None:
            continue
        source_node = graph.nodes[source_id]
        target_node = graph.nodes[target_id]
        same_conveyor = (
            source_node.networkType == target_node.networkType
            and source_node.level == target_node.level
        )
        graph.add_edge(
            TransportEdge(
                edgeId=f"conveyor:{source_id}->{target_id}",
                fromNodeId=source_id,
                toNodeId=target_id,
                edgeType="conveyor",
                direction=direction,
                distance=1.0,
                source="derived:topology.cells.conveyorDirection",
                provenance="derived",
                traversable=same_conveyor,
            )
        )

    entry_directions = storage_entry_dirs(topology)
    for node in topology.get("nodes", []):
        node_type = str(node.get("type") or "")
        position = node_position(node)
        topology_cell_id = cell_id(position[1], position[2], position[0])
        if node_type == "storage":
            storage_id = _storage_node_id(str(node["id"]))
            graph.add_node(
                TransportNode(
                    nodeId=storage_id,
                    kind="ctu_storage",
                    networkType="ctu",
                    topologyCellId=topology_cell_id,
                    level=1,
                    floor=position[0],
                    row=position[1],
                    col=position[2],
                    physical=True,
                    simulated=True,
                    source="topology.nodes",
                    provenance="topology",
                )
            )
            allowed = entry_directions.get(
                position, {(-1, 0), (1, 0), (0, -1), (0, 1)}
            )
            for d_row, d_col in allowed:
                stop_id = road_positions.get(
                    (position[0], position[1] + d_row, position[2] + d_col)
                )
                if stop_id is None:
                    continue
                for source_id, target_id, action in (
                    (storage_id, stop_id, "leave_storage"),
                    (stop_id, storage_id, "pickup_at_storage"),
                ):
                    graph.add_edge(
                        TransportEdge(
                            edgeId=f"ctu-storage:{source_id}->{target_id}",
                            fromNodeId=source_id,
                            toNodeId=target_id,
                            edgeType=action,
                            direction="storage_long_side",
                            distance=1.0,
                            source="derived:topology.nodes+zone_geometry+explicit_road",
                            provenance="derived",
                            traversable=True,
                        )
                    )
        elif node_type == "ctu_robot":
            robot_id = f"facility:{node['id']}"
            graph.add_node(
                TransportNode(
                    nodeId=robot_id,
                    kind="ctu_robot",
                    networkType="ctu",
                    topologyCellId=topology_cell_id,
                    level=1,
                    floor=position[0],
                    row=position[1],
                    col=position[2],
                    physical=True,
                    simulated=True,
                    source="topology.nodes",
                    provenance="topology",
                )
            )
            road_id = road_positions.get(position)
            if road_id:
                for source_id, target_id in ((robot_id, road_id), (road_id, robot_id)):
                    graph.add_edge(
                        TransportEdge(
                            edgeId=f"ctu-robot:{source_id}->{target_id}",
                            fromNodeId=source_id,
                            toNodeId=target_id,
                            edgeType="robot_position",
                            direction="bidirectional",
                            distance=0,
                            source="derived:coincident_explicit_road",
                            provenance="derived",
                            traversable=True,
                        )
                    )
        elif node_type in {"shuttle_dock", "lift_transfer", "turn_transfer"}:
            network_type = "port" if node_type == "shuttle_dock" else "transfer_device"
            graph.add_node(
                TransportNode(
                    nodeId=f"facility:{node['id']}",
                    kind=node_type,
                    networkType=network_type,
                    topologyCellId=topology_cell_id,
                    level=1,
                    floor=position[0],
                    row=position[1],
                    col=position[2],
                    physical=True,
                    simulated=node_type == "shuttle_dock",
                    source="topology.nodes",
                    provenance="topology",
                )
            )

    for zone in topology.get("zones", []):
        if zone.get("type") not in {"port", "pick_station", "drop_zone"}:
            continue
        entry = port_entry_cell(topology, zone)
        row = int(zone["y"]) + int(zone["height"]) - 1
        col = int(zone["x"]) + int(zone["width"]) // 2
        kind = str(zone["type"])
        network_type = "human_station" if kind in {"pick_station", "drop_zone"} else "port"
        graph.add_node(
            TransportNode(
                nodeId=f"facility:{zone['id']}",
                kind=kind,
                networkType=network_type,
                topologyCellId=entry,
                level=1,
                floor=DEFAULT_FLOOR,
                row=row,
                col=col,
                physical=True,
                simulated=kind != "port",
                source="topology.zones",
                provenance="topology",
            )
        )

    # ShuttleGraph is the only accepted source of shuttle track edges. Its
    # verified lift edges are separate from topology lift_transfer visuals.
    try:
        shuttle_graph, validation = shuttle_context(topology)
        lifts = validation.get("lifts") or []
        graph.metadata["shuttleValidation"] = {
            "status": validation.get("verification_status"),
            "lifts": [lift.get("lift_id") for lift in lifts],
        }
    except ValueError:
        shuttle_graph = build_shuttle_track_graph(topology)
        lifts = []
        graph.metadata["shuttleValidation"] = {"status": "unverified", "lifts": []}

    for track_id, track in shuttle_graph.items():
        graph.add_node(
            TransportNode(
                nodeId=_shuttle_track_node_id(track_id),
                kind="shuttle_track",
                networkType="shuttle",
                trackNodeId=track_id,
                level=int(track["shuttle_level"]),
                floor=DEFAULT_FLOOR,
                row=int(track["row"]),
                col=int(track["col"]),
                physical=True,
                simulated=True,
                source="shuttle_topology.build_shuttle_track_graph",
                provenance="derived",
            )
        )
    for track_id, track in shuttle_graph.items():
        for neighbor in track.get("neighbors") or []:
            graph.add_edge(
                TransportEdge(
                    edgeId=f"shuttle-track:{track_id}->{neighbor}",
                    fromNodeId=_shuttle_track_node_id(track_id),
                    toNodeId=_shuttle_track_node_id(str(neighbor)),
                    edgeType="shuttle_track",
                    direction="declared_neighbor",
                    distance=2.0,
                    source="shuttle_topology.build_shuttle_track_graph",
                    provenance="derived",
                    traversable=True,
                )
            )
    for lift in lifts:
        entrances = lift.get("level_entrances") or {}
        exits = lift.get("level_exits") or {}
        for source_key, source_track in entrances.items():
            for target_key, target_track in exits.items():
                if source_key == target_key:
                    continue
                graph.add_edge(
                    TransportEdge(
                        edgeId=f"shuttle-lift:{lift['lift_id']}:{source_track}->{target_track}",
                        fromNodeId=_shuttle_track_node_id(str(source_track)),
                        toNodeId=_shuttle_track_node_id(str(target_track)),
                        edgeType="lift",
                        direction=f"{source_key}_to_{target_key}",
                        distance=abs(int(source_key[1:]) - int(target_key[1:])),
                        source=f"shuttle_config:{lift['lift_id']}",
                        provenance="declared",
                        traversable=True,
                    )
                )

    for source_cell, source_level in shuttle_rack_slots(topology):
        source_floor, row_value, col_value = parse_cell_id(source_cell)
        graph.add_node(
            TransportNode(
                nodeId=_shuttle_storage_node_id(source_cell, source_level),
                kind="shuttle_storage",
                networkType="shuttle",
                topologyCellId=source_cell,
                level=int(source_level),
                floor=source_floor,
                row=row_value,
                col=col_value,
                physical=True,
                simulated=True,
                source="derived:pathfinding.shuttle_rack_slots",
                provenance="derived",
            )
        )

    _add_declared_contract(graph, topology)
    for node_id in graph.adjacency:
        graph.adjacency[node_id].sort()
    return graph


def audit_directed_path(
    graph: TransportGraph,
    start_node_id: str,
    end_node_id: str,
    segment_id: str = "directed_path",
) -> PathAuditSegment:
    if start_node_id not in graph.nodes or end_node_id not in graph.nodes:
        missing = [
            node_id
            for node_id in (start_node_id, end_node_id)
            if node_id not in graph.nodes
        ]
        return _blocked_segment(
            segment_id,
            start_node_id,
            end_node_id,
            "unknown",
            [f"Missing transport node: {node_id}" for node_id in missing],
        )

    queue = deque([start_node_id])
    previous: dict[str, tuple[str, str, bool]] = {}
    visited = {start_node_id}
    while queue:
        current = queue.popleft()
        if current == end_node_id:
            break
        for target, edge_id, is_handoff in graph.adjacency.get(current, []):
            if target in visited:
                continue
            visited.add(target)
            previous[target] = (current, edge_id, is_handoff)
            queue.append(target)

    if end_node_id not in visited:
        start = graph.nodes[start_node_id]
        end = graph.nodes[end_node_id]
        return _blocked_segment(
            segment_id,
            start_node_id,
            end_node_id,
            f"{start.networkType}->{end.networkType}",
            ["No directed path exists in the strict transport graph."],
            evidence=[
                "Only explicit road cells, directed conveyor cells, ShuttleGraph tracks, "
                "verified lifts, and VALID declared handoffs are traversable."
            ],
        )

    node_ids = [end_node_id]
    edge_ids: list[str] = []
    handoff_flags: list[bool] = []
    cursor = end_node_id
    while cursor != start_node_id:
        parent, edge_id, is_handoff = previous[cursor]
        node_ids.append(parent)
        edge_ids.append(edge_id)
        handoff_flags.append(is_handoff)
        cursor = parent
    node_ids.reverse()
    edge_ids.reverse()
    handoff_flags.reverse()
    networks = [graph.nodes[node_id].networkType for node_id in node_ids]
    legal_handoffs = all(
        left == right or is_handoff
        for left, right, is_handoff in zip(networks, networks[1:], handoff_flags)
    )
    return PathAuditSegment(
        segmentId=segment_id,
        fromNodeId=start_node_id,
        toNodeId=end_node_id,
        networkType="->".join(dict.fromkeys(networks)),
        status="PASS" if legal_handoffs else "BLOCKED",
        nodeIds=node_ids,
        edgeIds=edge_ids,
        direction="directed",
        reachable=legal_handoffs,
        hasLegalHandoff=legal_handoffs,
        blockingReasons=[] if legal_handoffs else ["A network transition lacks a VALID handoff."],
        evidence=["Directed breadth-first validation over the strict transport graph."],
    )


def _blocked_segment(
    segment_id: str,
    start: str,
    end: str,
    network: str,
    reasons: list[str],
    *,
    evidence: list[str] | None = None,
    status: str = "BLOCKED",
) -> PathAuditSegment:
    return PathAuditSegment(
        segmentId=segment_id,
        fromNodeId=start,
        toNodeId=end,
        networkType=network,
        status=status,
        direction="directed",
        reachable=False,
        hasLegalHandoff=False,
        blockingReasons=reasons,
        evidence=evidence or [],
    )


def _route(route_id: str, segments: list[PathAuditSegment]) -> PathAudit:
    node_ids: list[str] = []
    edge_ids: list[str] = []
    reasons: list[str] = []
    for segment in segments:
        for value in segment.nodeIds:
            if not node_ids or node_ids[-1] != value:
                node_ids.append(value)
        edge_ids.extend(segment.edgeIds)
        reasons.extend(segment.blockingReasons)
    status = (
        "PASS"
        if all(segment.status == "PASS" for segment in segments)
        else "BLOCKED"
        if any(segment.status == "BLOCKED" for segment in segments)
        else "PARTIAL"
    )
    return PathAudit(
        routeId=route_id,
        status=status,
        segments=segments,
        nodeIds=node_ids,
        edgeIds=edge_ids,
        reachable=status == "PASS",
        hasLegalHandoffs=status == "PASS" and all(
            segment.hasLegalHandoff for segment in segments
        ),
        blockingReasons=list(dict.fromkeys(reasons)),
    )


def audit_path(
    graph: TransportGraph,
    route_id: str,
    required_node_ids: list[str],
) -> PathAudit:
    """Validate an ordered, directed route and every network transition.

    This is the reusable G1-B entry point for an approved sequence of business
    facilities. It does not infer missing facilities or handoffs.
    """

    if len(required_node_ids) < 2:
        raise ValueError("A path audit requires at least two ordered node IDs")
    segments = [
        audit_directed_path(
            graph,
            source,
            target,
            f"{route_id}:{index + 1}",
        )
        for index, (source, target) in enumerate(
            zip(required_node_ids, required_node_ids[1:])
        )
    ]
    return _route(route_id, segments)


def _issue(
    code: str,
    reason: str,
    source: str | None = None,
    target: str | None = None,
    evidence: Iterable[str] = (),
    severity: str = "ERROR",
) -> NetworkIssue:
    return NetworkIssue(
        code=code,
        severity=severity,
        sourceId=source,
        targetId=target,
        reason=reason,
        evidence=list(evidence),
    )


def _candidate_facilities(topology: dict[str, Any]) -> list[CandidateFacility]:
    candidates: list[CandidateFacility] = []
    for zone in topology.get("zones", []):
        if zone.get("type") != "port":
            continue
        row = int(zone["y"]) + int(zone["height"]) - 1
        col = int(zone["x"]) + int(zone["width"]) // 2
        entry = cell_id(row, col)
        candidates.append(
            CandidateFacility(
                candidateId=f"proposed-handoff-{zone['id']}",
                kind="ctu_port_handoff",
                networkType="ctu->elevated_conveyor",
                topologyCellId=entry,
                row=row,
                col=col,
                level=1,
                relativeToIds=[str(zone["id"]), entry, cell_id(row + 1, col)],
                requiredEdges=[
                    "explicit CTU approach road -> handoff input",
                    "handoff output -> adjacent conveyor cell",
                ],
                conflicts=[
                    f"{entry} is not an explicit topology cell",
                    "CTU-side stopping cell and level transition are not declared",
                ],
                evidence=[
                    "Port zone geometry supplies the entry coordinate.",
                    "The nearest conveyor is one row below, not connected by a declared device.",
                ],
            )
        )
    candidates.extend(
        [
            CandidateFacility(
                candidateId="proposed-pick-station-port-region",
                kind="pick_station",
                networkType="human_station",
                relativeToIds=[
                    str(zone["id"])
                    for zone in topology.get("zones", [])
                    if zone.get("type") == "port"
                ],
                requiredEdges=[
                    "CTU/Port handoff -> pick station input",
                    "pick station output -> breakpack conveyor input",
                ],
                conflicts=["No pick_station zone or surveyed workstation coordinate exists."],
                evidence=["Candidate area is constrained only by the four existing Port zones."],
                candidateArea={"basis": "human survey around existing Port zones"},
            ),
            CandidateFacility(
                candidateId="proposed-breakpack-exit-port-region",
                kind="breakpack_exit",
                networkType="human_station->elevated_conveyor",
                relativeToIds=["proposed-pick-station-port-region"],
                requiredEdges=["pick station output -> conveyor input"],
                conflicts=["No drop_zone or breakpack output device exists."],
                evidence=["No exact coordinate can be justified from the topology."],
                candidateArea={"basis": "same surveyed workcell as proposed pick station"},
            ),
            CandidateFacility(
                candidateId="proposed-handoff-dock-01-ground-conveyor",
                kind="shuttle_dock_handoff",
                networkType="shuttle->ground_conveyor",
                topologyCellId="cell-r74-c17",
                row=74,
                col=17,
                level=1,
                relativeToIds=["dock-01", "cell-r74-c17"],
                requiredEdges=[
                    "ShuttleGraph approach track -> dock input",
                    "dock output -> conveyor-cell:cell-r74-c17",
                ],
                conflicts=["ShuttleGraph has no declared track/dock connection."],
                evidence=[
                    "dock-01 and the first ground conveyor cell share the topology coordinate.",
                    "Coordinate coincidence is evidence for a candidate, not a valid handoff.",
                ],
            ),
            CandidateFacility(
                candidateId="proposed-conveyor-bridge-r74-c49-c51",
                kind="conveyor_bridge_or_transfer",
                networkType="ground_conveyor->elevated_conveyor",
                row=74,
                col=49,
                level=1,
                relativeToIds=["cell-r74-c48", "cell-r74-c52"],
                requiredEdges=[
                    "cell-r74-c48 -> approved bridge/transfer input",
                    "approved bridge/transfer output -> cell-r74-c52",
                ],
                conflicts=[
                    "c49..c51 have no conveyor cells",
                    "c48 is ground level 1 while c52 is elevated level 2",
                    "No lift/transfer device spans the level change",
                ],
                evidence=["The candidate area is the exact three-cell gap on row 74."],
                candidateArea={"row": 74, "colStart": 49, "colEnd": 51},
            ),
            CandidateFacility(
                candidateId="proposed-merge-turn-transfer-14-region",
                kind="conveyor_merge",
                networkType="ground_conveyor+elevated_conveyor",
                topologyCellId="cell-r74-c43",
                row=74,
                col=43,
                level=1,
                relativeToIds=["turn_transfer-14", "cell-r74-c43"],
                requiredEdges=[
                    "both inbound conveyor edges -> capacity-controlled merge",
                    "merge output -> downstream conveyor",
                ],
                conflicts=[
                    "turn_transfer-14 is on the ground line only",
                    "No elevated conveyor edge reaches this coordinate",
                ],
                evidence=[
                    "A real turn_transfer node exists here, so this is a survey candidate only."
                ],
            ),
            CandidateFacility(
                candidateId="proposed-outbound-downstream-region",
                kind="outbound",
                networkType="outbound",
                relativeToIds=["proposed-merge-turn-transfer-14-region"],
                requiredEdges=["verified merge output -> outbound acceptance point"],
                conflicts=["No outbound node or zone exists."],
                evidence=["Downstream direction must be surveyed after the merge design is selected."],
                candidateArea={"basis": "directed conveyor sinks after approved merge"},
            ),
        ]
    )
    return candidates


def audit_transport_network(topology: dict[str, Any]) -> TransportNetworkAuditResponse:
    graph = build_transport_network(topology)
    zones = list(topology.get("zones", []))
    nodes = list(topology.get("nodes", []))
    storage_nodes = sorted(
        (node for node in nodes if node.get("type") == "storage"),
        key=lambda item: str(item["id"]),
    )
    sample_storage = str(storage_nodes[0]["id"]) if storage_nodes else "MISSING:ctu_storage"
    sample_storage_graph_id = _storage_node_id(sample_storage)
    storage_stops = [
        target
        for target, edge_id, _ in graph.adjacency.get(sample_storage_graph_id, [])
        if edge_id.startswith("ctu-storage:")
    ]
    storage_stop = storage_stops[0] if storage_stops else "MISSING:explicit_ctu_stop"
    ports = [zone for zone in zones if zone.get("type") == "port"]
    first_port = f"facility:{ports[0]['id']}" if ports else "MISSING:port"
    pick_stations = [zone for zone in zones if zone.get("type") == "pick_station"]
    drop_zones = [zone for zone in zones if zone.get("type") == "drop_zone"]
    pick_station = (
        f"facility:{pick_stations[0]['id']}" if pick_stations else "MISSING:pick_station"
    )
    breakpack_exit = (
        f"facility:{drop_zones[0]['id']}" if drop_zones else "MISSING:breakpack_exit"
    )

    issues: list[NetworkIssue] = []
    if not storage_stops:
        issues.append(
            _issue(
                "CTU_EXPLICIT_STOP_MISSING",
                "The sampled storage has no long-side adjacent explicit road cell; legacy blank-grid travel is insufficient physical evidence.",
                sample_storage,
                storage_stop,
                ["Strict CTU edges are created only from topology.cells[type=road]."],
            )
        )
    issues.append(
        _issue(
            "MISSING_CTU_PORT_HANDOFF",
            "Port zones exist, but no device contract connects a CTU stop to a Port or conveyor input.",
            storage_stop,
            first_port,
            [
                f"{zone['id']} entry={port_entry_cell(topology, zone)}"
                for zone in ports
            ],
        )
    )
    if not pick_stations:
        issues.append(
            _issue(
                "MISSING_PICK_STATION",
                "No pick_station zone exists in the topology.",
                first_port,
                pick_station,
                ["topology.zones contains zero type=pick_station records."],
            )
        )
    if not drop_zones:
        issues.append(
            _issue(
                "MISSING_BREAKPACK_EXIT",
                "No drop_zone or explicit breakpack exit exists.",
                pick_station,
                breakpack_exit,
                ["topology.zones contains zero type=drop_zone records."],
            )
        )

    left_segments = [
        audit_directed_path(
            graph, sample_storage_graph_id, storage_stop, "left-storage-to-ctu-stop"
        ),
        _blocked_segment(
            "left-ctu-stop-to-port",
            storage_stop,
            first_port,
            "ctu->port",
            ["A declared CTU-to-Port handoff device is missing."],
            evidence=[f"Recognized Port zones: {[zone['id'] for zone in ports]}"],
        ),
        _blocked_segment(
            "left-port-to-pick-station",
            first_port,
            pick_station,
            "port->human_station",
            ["The pick_station entity and handoff are missing."],
        ),
        _blocked_segment(
            "left-pick-to-breakpack-exit",
            pick_station,
            breakpack_exit,
            "human_station",
            ["A modeled breakpack output/drop zone is missing."],
        ),
        _blocked_segment(
            "left-breakpack-exit-to-conveyor",
            breakpack_exit,
            "MISSING:left-conveyor-input",
            "human_station->elevated_conveyor",
            ["No declared output handoff connects picking to an elevated conveyor input."],
        ),
        _blocked_segment(
            "left-conveyor-to-merge",
            "MISSING:left-conveyor-input",
            "MISSING:conveyor-merge",
            "elevated_conveyor",
            ["No formal merge node or complete directed path is declared."],
        ),
        _blocked_segment(
            "left-merge-to-outbound",
            "MISSING:conveyor-merge",
            "MISSING:outbound",
            "conveyor->outbound",
            ["No outbound node, acceptance edge, or completion handoff exists."],
        ),
    ]

    docks = [node for node in nodes if node.get("type") == "shuttle_dock"]
    dock = docks[0] if docks else None
    dock_id = f"facility:{dock['id']}" if dock else "MISSING:shuttle_dock"
    sources = shuttle_rack_slots(topology)
    default_sources = sources[1:3]
    shuttle_robots = [node for node in nodes if node.get("type") == "shuttle_robot"]
    robot = shuttle_robots[0] if shuttle_robots else None
    for source_cell, source_level in default_sources:
        robot_reachable = False
        dock_reachable = False
        if robot and dock:
            robot_cell = cell_id(int(robot["row"]), int(robot["col"]), _floor(robot.get("floor")))
            dock_cell = cell_id(int(dock["row"]), int(dock["col"]), _floor(dock.get("floor")))
            robot_reachable = bool(
                shortest_path_shuttle(
                    topology, robot_cell, 1, source_cell, int(source_level)
                )
            )
            dock_reachable = bool(
                shortest_path_shuttle(
                    topology, source_cell, int(source_level), dock_cell, 1
                )
            )
        if not robot_reachable or not dock_reachable:
            issues.append(
                _issue(
                    "UNREACHABLE_DEFAULT_DOCK_SOURCE",
                    "A default dock source selected by the current cursor order is not reachable by the existing runtime planner.",
                    f"{source_cell}/L{source_level}",
                    str(dock.get("id")) if dock else dock_id,
                    [
                        f"robotToSource={str(robot_reachable).lower()}",
                        f"sourceToDock={str(dock_reachable).lower()}",
                        "The strict graph also has no declared rack pickup handoff edge.",
                    ],
                )
            )

    source_cell, source_level = default_sources[0] if default_sources else ("MISSING:shuttle_source", 1)
    source_node = (
        _shuttle_storage_node_id(source_cell, source_level)
        if default_sources
        else "MISSING:shuttle_source"
    )
    issues.extend(
        [
            _issue(
                "MISSING_SHUTTLE_PICKUP_INTERFACE",
                "Rack locations are not connected to a ShuttleGraph stopping track by a pickup action.",
                source_node,
                "MISSING:shuttle-pickup-track",
            ),
            _issue(
                "SHUTTLE_GRAPH_NOT_CONNECTED_TO_DOCK",
                "Verified internal shuttle tracks/lifts do not declare an edge or device to dock-01.",
                "ShuttleGraph",
                str(dock.get("id")) if dock else dock_id,
                [f"validatedLifts={graph.metadata.get('shuttleValidation', {}).get('lifts', [])}"],
            ),
            _issue(
                "MISSING_SHUTTLE_DOCK_HANDOFF",
                "Coordinate coincidence between dock-01 and cell-r74-c17 is not a legal equipment handoff.",
                str(dock.get("id")) if dock else dock_id,
                "cell-r74-c17",
            ),
        ]
    )

    cells_by_id = {str(cell["id"]): cell for cell in topology.get("cells", [])}
    gap_ids = [f"cell-r74-c{col}" for col in range(49, 52)]
    if (
        "cell-r74-c48" in cells_by_id
        and "cell-r74-c52" in cells_by_id
        and not any(value in cells_by_id for value in gap_ids)
    ):
        issues.append(
            _issue(
                "CONVEYOR_GAP_R74_C49_C51",
                "The directed ground conveyor ends at c48; c49..c51 are undefined and c52 is a different elevated network/level.",
                "cell-r74-c48",
                "cell-r74-c52",
                [
                    "missing=cell-r74-c49,cell-r74-c50,cell-r74-c51",
                    f"c48Ground={bool(cells_by_id['cell-r74-c48'].get('conveyorGround'))}",
                    f"c52Ground={bool(cells_by_id['cell-r74-c52'].get('conveyorGround'))}",
                    f"c52Level={_cell_level(cells_by_id['cell-r74-c52'])}",
                ],
            )
        )
    issues.extend(
        [
            _issue(
                "MISSING_CONVEYOR_MERGE",
                "No formal merge resource joins the two business routes.",
                "turn_transfer-14",
                "MISSING:conveyor-merge",
            ),
            _issue(
                "MISSING_OUTBOUND",
                "No outbound node/zone or final acceptance handoff exists.",
                "MISSING:conveyor-merge",
                "MISSING:outbound",
            ),
        ]
    )

    right_segments = [
        _blocked_segment(
            "right-storage-to-shuttle-track",
            source_node,
            "MISSING:shuttle-pickup-track",
            "shuttle",
            ["The rack pickup stopping track/fork action is not declared."],
            evidence=[f"Default source: {source_cell}/L{source_level}"],
        ),
        _blocked_segment(
            "right-shuttle-track-via-lift-to-dock",
            "MISSING:shuttle-pickup-track",
            dock_id,
            "shuttle->port",
            ["ShuttleGraph internal lift connectivity does not extend to dock-01."],
            evidence=[
                f"Validated internal lifts: {graph.metadata.get('shuttleValidation', {}).get('lifts', [])}"
            ],
            status="PARTIAL",
        ),
        _blocked_segment(
            "right-dock-to-ground-conveyor",
            dock_id,
            "conveyor-cell:cell-r74-c17",
            "port->ground_conveyor",
            ["A declared dock transfer device/handoff is missing."],
            evidence=["dock-01 and cell-r74-c17 share row/col but remain separate networks."],
        ),
        audit_directed_path(
            graph,
            "conveyor-cell:cell-r74-c17",
            "conveyor-cell:cell-r74-c48",
            "right-ground-conveyor-directed-prefix",
        ),
        audit_directed_path(
            graph,
            "conveyor-cell:cell-r74-c48",
            "conveyor-cell:cell-r74-c52",
            "right-ground-to-elevated-gap",
        ),
        _blocked_segment(
            "right-elevated-conveyor-to-merge",
            "conveyor-cell:cell-r74-c52",
            "MISSING:conveyor-merge",
            "elevated_conveyor",
            ["No approved merge node is declared downstream."],
        ),
        _blocked_segment(
            "right-merge-to-outbound",
            "MISSING:conveyor-merge",
            "MISSING:outbound",
            "conveyor->outbound",
            ["No outbound node, edge, or acceptance handoff exists."],
        ),
    ]

    left_route = _route("left-breakpack-outbound", left_segments)
    right_route = _route("right-fullcase-outbound", right_segments)
    status = "PASS" if left_route.status == right_route.status == "PASS" else "BLOCKED"
    edge_counts: dict[str, int] = {}
    for edge in graph.edges.values():
        edge_counts[edge.edgeType] = edge_counts.get(edge.edgeType, 0) + 1
    network_counts: dict[str, int] = {}
    for node in graph.nodes.values():
        network_counts[node.networkType] = network_counts.get(node.networkType, 0) + 1
    return TransportNetworkAuditResponse(
        topologyId=str(topology["id"]),
        topologyRevision=topology_revision(topology),
        status=status,
        leftRoute=left_route,
        rightRoute=right_route,
        issues=issues,
        candidateHandoffs=_candidate_facilities(topology),
        summary={
            "strictRules": {
                "ctuUsesExplicitRoadOnly": True,
                "shuttleUsesShuttleGraphOnly": True,
                "conveyorUsesDirectedAdjacentCellsOnly": True,
                "crossNetworkRequiresValidHandoff": True,
                "crossLevelRequiresLiftOrDevice": True,
            },
            "nodeCountsByNetwork": dict(sorted(network_counts.items())),
            "edgeCountsByType": dict(sorted(edge_counts.items())),
            "validHandoffCount": sum(
                1 for handoff in graph.handoffs.values() if handoff.validationStatus == "VALID"
            ),
            "portIds": [str(zone["id"]) for zone in ports],
            "pickStationCount": len(pick_stations),
            "dropZoneCount": len(drop_zones),
            "dockIds": [str(item["id"]) for item in docks],
            "defaultDockSourcesAudited": [
                {"cellId": value, "level": level} for value, level in default_sources
            ],
            "issueCount": len(issues),
        },
    )
