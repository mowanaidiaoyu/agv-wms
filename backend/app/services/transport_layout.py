from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from ..core.config import DATA_DIR
from ..schemas import (
    LayoutOption,
    LayoutPosition,
    LayoutReferenceNode,
    LayoutRoute,
    NetworkIssue,
    PickingArmConveyorStep,
    PickingArmEndpoint,
    PickingArmKinematics,
    PickingArmLayout,
    ProposedLayoutConnection,
    ProposedLayoutFacility,
    TransportLayoutPlanResponse,
)
from .shuttle_topology import topology_revision
from .unified_transfer import shuttle_context


PLAN_ROOT = DATA_DIR / "transport_layout_plans"


class LayoutRevisionMismatch(ValueError):
    pass


def _position(row: int, col: int, level: int = 1) -> LayoutPosition:
    return LayoutPosition(floor="1F", row=int(row), col=int(col), level=int(level))


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


def _reference(
    reference_id: str,
    kind: str,
    network: str,
    row: int,
    col: int,
    level: int,
    source_id: str,
    evidence: list[str] | None = None,
) -> LayoutReferenceNode:
    return LayoutReferenceNode(
        referenceId=reference_id,
        kind=kind,
        networkType=network,
        position=_position(row, col, level),
        sourceId=source_id,
        validationStatus="PASS",
        evidence=evidence or [],
    )


def _facility(
    facility_id: str,
    kind: str,
    network: str,
    row: int,
    col: int,
    level: int,
    purpose: str,
    *,
    capacity: int = 1,
    spans_levels: list[int] | None = None,
    validation: str = "PARTIAL",
    refs: list[str] | None = None,
    occupancy: str = "exclusive",
    conflicts: list[str] | None = None,
    evidence: list[str] | None = None,
) -> ProposedLayoutFacility:
    return ProposedLayoutFacility(
        facilityId=facility_id,
        kind=kind,
        networkType=network,
        position=_position(row, col, level),
        spansLevels=spans_levels or [level],
        validationStatus=validation,
        capacity=capacity,
        purpose=purpose,
        existingRefIds=refs or [],
        occupancyPolicy=occupancy,
        conflicts=conflicts or [],
        evidence=evidence or [],
    )


def _connection(
    connection_id: str,
    from_id: str,
    to_id: str,
    network: str,
    edge_type: str,
    direction: str,
    points: list[LayoutPosition],
    *,
    status: str = "PASS",
    capacity: int = 1,
    handoff: str | None = None,
    height: dict[str, Any] | None = None,
    required_cells: list[str] | None = None,
    conflicts: list[str] | None = None,
    evidence: list[str] | None = None,
) -> ProposedLayoutConnection:
    return ProposedLayoutConnection(
        connectionId=connection_id,
        fromId=from_id,
        toId=to_id,
        networkType=network,
        edgeType=edge_type,
        direction=direction,
        points=points,
        status=status,
        capacity=capacity,
        handoffAction=handoff,
        heightTransition=height,
        requiredCellIds=required_cells or [],
        conflicts=conflicts or [],
        evidence=evidence or [],
    )


def _cell_range(row: int, start: int, end: int) -> list[str]:
    return [f"cell-r{row}-c{col}" for col in range(start, end + 1)]


def _load_raw_plan(topology_id: str) -> dict[str, Any]:
    path = PLAN_ROOT / f"{topology_id}.g1b1.json"
    if not path.exists():
        raise FileNotFoundError(topology_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _build_left_layout(
    raw: dict[str, Any],
    references: list[LayoutReferenceNode],
    facilities: list[ProposedLayoutFacility],
    connections: list[ProposedLayoutConnection],
) -> tuple[list[str], list[str]]:
    layout = raw["leftLayout"]
    workcells = layout["workcells"]
    facility_ids: list[str] = []
    connection_ids: list[str] = []
    junction_ids: list[str] = []
    for workcell in workcells:
        port_id = str(workcell["portId"])
        col = int(workcell["col"])
        suffix = port_id
        port_ref = f"existing-port-{port_id}"
        conveyor_ref = f"existing-conveyor-{workcell['conveyorCellId']}"
        references.extend(
            [
                _reference(
                    port_ref,
                    "port",
                    "port",
                    int(workcell["verticalTransferRow"]),
                    col,
                    1,
                    port_id,
                    [f"Topology Port entry: {workcell['entryCellId']}"],
                ),
                _reference(
                    conveyor_ref,
                    "conveyor_cell",
                    "elevated_conveyor",
                    74,
                    col,
                    2,
                    str(workcell["conveyorCellId"]),
                    ["Existing conveyor cell, direction=right, levels=2."],
                ),
            ]
        )

        junction_id = f"proposed-ctu-junction-{suffix}"
        stop_id = f"proposed-ctu-stop-{suffix}"
        input_id = f"proposed-input-buffer-{suffix}"
        station_id = f"proposed-pick-station-{suffix}"
        output_id = f"proposed-output-buffer-{suffix}"
        lift_id = f"proposed-port-lift-{suffix}"
        junction_ids.append(junction_id)
        workcell_facilities = [junction_id, stop_id, input_id, station_id, output_id, lift_id]
        facility_ids.extend(workcell_facilities)
        facilities.extend(
            [
                _facility(
                    junction_id,
                    "ctu_road_junction",
                    "ctu",
                    int(workcell["roadJunctionRow"]),
                    col,
                    1,
                    "Connect the proposed north-side CTU road corridor to this Port spur.",
                    validation="PASS",
                    evidence=["Rows 64..71 contain no explicit cell or fixed node at this coordinate."],
                ),
                _facility(
                    stop_id,
                    "ctu_stop",
                    "ctu",
                    int(workcell["ctuStopRow"]),
                    col,
                    1,
                    "Dedicated CTU stopping position before the manual workcell.",
                    validation="PASS",
                ),
                _facility(
                    input_id,
                    "input_buffer",
                    "human_station",
                    int(workcell["inputBufferRow"]),
                    col,
                    1,
                    "Hold one delivered tote while the station is occupied.",
                    capacity=2,
                    validation="PASS",
                ),
                _facility(
                    station_id,
                    "pick_station",
                    "human_station",
                    int(workcell["pickStationRow"]),
                    col,
                    1,
                    "Manual each-pick workstation for breakpack demand.",
                    validation="PASS",
                ),
                _facility(
                    output_id,
                    "output_buffer",
                    "port",
                    int(workcell["outputBufferRow"]),
                    col,
                    1,
                    "Hold picked units before vertical conveyor transfer.",
                    capacity=2,
                    validation="PARTIAL",
                    refs=[port_ref],
                    occupancy="co_located_with_reference",
                    conflicts=["Uses the existing Port footprint; exact equipment envelope needs approval."],
                ),
                _facility(
                    lift_id,
                    "port_vertical_transfer",
                    "transfer_device",
                    int(workcell["verticalTransferRow"]),
                    col,
                    1,
                    "Raise picked goods from the L1 workcell to the L2 elevated conveyor.",
                    spans_levels=[1, 2],
                    validation="PARTIAL",
                    refs=[port_ref, conveyor_ref],
                    occupancy="co_located_with_reference",
                    conflicts=["Physical Port height and guarding have not been surveyed."],
                ),
            ]
        )

        path_specs = [
            (
                f"left-{suffix}-junction-to-stop",
                junction_id,
                stop_id,
                "ctu",
                "road",
                "down",
                int(workcell["roadJunctionRow"]),
                int(workcell["ctuStopRow"]),
                None,
                None,
            ),
            (
                f"left-{suffix}-stop-to-input",
                stop_id,
                input_id,
                "ctu->human_station",
                "handoff",
                "down",
                int(workcell["ctuStopRow"]),
                int(workcell["inputBufferRow"]),
                "CTU places tote into the guarded input buffer and receives completion acknowledgement.",
                None,
            ),
            (
                f"left-{suffix}-input-to-pick",
                input_id,
                station_id,
                "human_station",
                "buffer_release",
                "down",
                int(workcell["inputBufferRow"]),
                int(workcell["pickStationRow"]),
                "Operator accepts the tote from the input buffer.",
                None,
            ),
            (
                f"left-{suffix}-pick-to-output",
                station_id,
                output_id,
                "human_station",
                "pick_complete",
                "down",
                int(workcell["pickStationRow"]),
                int(workcell["outputBufferRow"]),
                "Picked units enter the output buffer only after pick confirmation.",
                None,
            ),
            (
                f"left-{suffix}-output-to-port-lift",
                output_id,
                lift_id,
                "human_station->transfer_device",
                "handoff",
                "down",
                int(workcell["outputBufferRow"]),
                int(workcell["verticalTransferRow"]),
                "Output buffer releases one load to the vertical transfer.",
                None,
            ),
            (
                f"left-{suffix}-lift-to-conveyor",
                lift_id,
                conveyor_ref,
                "transfer_device->elevated_conveyor",
                "height_transfer",
                "down",
                int(workcell["verticalTransferRow"]),
                74,
                "Vertical transfer discharges only after conveyor acceptance.",
                {
                    "fromLevel": 1,
                    "toLevel": 2,
                    "deviceId": lift_id,
                    "method": "vertical transfer",
                },
            ),
        ]
        for (
            connection_id,
            from_id,
            to_id,
            network,
            edge_type,
            direction,
            start_row,
            end_row,
            handoff,
            height,
        ) in path_specs:
            connection_ids.append(connection_id)
            connections.append(
                _connection(
                    connection_id,
                    from_id,
                    to_id,
                    network,
                    edge_type,
                    direction,
                    [_position(start_row, col, 1), _position(end_row, col, 2 if height else 1)],
                    handoff=handoff,
                    height=height,
                    required_cells=(
                        [f"cell-r{row}-c{col}" for row in range(start_row, end_row + 1)]
                        if edge_type == "road"
                        else []
                    ),
                )
            )

    corridor = layout["roadCorridor"]
    corridor_id = "proposed-left-ctu-road-corridor"
    connection_ids.append(corridor_id)
    connections.append(
        _connection(
            corridor_id,
            junction_ids[-1],
            junction_ids[0],
            "ctu",
            "road",
            "bidirectional",
            [
                _position(int(corridor["row"]), int(corridor["colStart"]), 1),
                _position(int(corridor["row"]), int(corridor["colEnd"]), 1),
            ],
            required_cells=_cell_range(
                int(corridor["row"]), int(corridor["colStart"]), int(corridor["colEnd"])
            ),
            evidence=[str(corridor["note"])],
        )
    )
    return facility_ids, connection_ids


def _build_right_layout(
    raw: dict[str, Any],
    references: list[LayoutReferenceNode],
    facilities: list[ProposedLayoutFacility],
    connections: list[ProposedLayoutConnection],
) -> tuple[list[str], list[str]]:
    layout = raw["rightLayout"]
    references.extend(
        [
            _reference("existing-dock-01", "shuttle_dock", "port", 74, 17, 1, "dock-01"),
            _reference(
                "existing-dock-conveyor-cell-r74-c17",
                "conveyor_cell",
                "ground_conveyor",
                74,
                17,
                1,
                "cell-r74-c17",
                ["Existing direction=right, conveyorGround=true."],
            ),
            _reference("existing-source-cell-r96-c27-L2", "shuttle_storage", "shuttle", 96, 27, 2, "cell-r96-c27/L2"),
            _reference("existing-source-cell-r96-c29-L2", "shuttle_storage", "shuttle", 96, 29, 2, "cell-r96-c29/L2"),
            _reference("existing-track-L2-r96-c19", "shuttle_track", "shuttle", 96, 19, 2, "L2-r96-c19"),
            _reference("existing-track-L2-r96-c23", "shuttle_track", "shuttle", 96, 23, 2, "L2-r96-c23"),
            _reference("existing-track-L2-r96-c31", "shuttle_track", "shuttle", 96, 31, 2, "L2-r96-c31"),
        ]
    )
    for lift in layout["verifiedLifts"]:
        references.append(
            _reference(
                f"existing-{lift['liftId']}",
                "shuttle_lift",
                "lift",
                int(lift["row"]),
                int(lift["col"]),
                1,
                str(lift["liftId"]),
                [f"Verified ShuttleGraph levels: {lift['levels']}"],
            )
        )

    source_27 = layout["sourceRepairs"][0]
    source_29 = layout["sourceRepairs"][1]
    pickup_27_id = "proposed-shuttle-pick-stop-cell-r96-c27-L2"
    pickup_29_id = "proposed-shuttle-pick-interface-cell-r96-c29-L2"
    transfer_id = "proposed-shuttle-dock-descent-r76-c17"
    dock_handoff_id = "proposed-shuttle-dock-handoff-dock-01"
    facility_ids = [pickup_27_id, pickup_29_id, transfer_id, dock_handoff_id]
    facilities.extend(
        [
            _facility(
                pickup_27_id,
                "shuttle_pick_stop",
                "shuttle",
                int(source_27["pickupRow"]),
                int(source_27["pickupCol"]),
                2,
                "L2 stopping track for fork pickup from cell-r96-c27 without removing the source.",
                validation="PARTIAL",
                conflicts=["Requires physical approval of a new L2 rail on the existing removed slot 96,25."],
                evidence=[str(source_27["basis"])],
            ),
            _facility(
                pickup_29_id,
                "shuttle_pick_interface",
                "shuttle",
                int(source_29["pickupRow"]),
                int(source_29["pickupCol"]),
                2,
                "Fork pickup interface from the real L2-r96-c31 track.",
                validation="PASS",
                refs=["existing-track-L2-r96-c31"],
                occupancy="co_located_with_reference",
                evidence=[str(source_29["basis"])],
            ),
            _facility(
                transfer_id,
                "shuttle_dock_vertical_transfer",
                "transfer_device",
                int(layout["dockTransfer"]["approachRow"]),
                int(layout["dockTransfer"]["approachCol"]),
                2,
                "Receive a case from the overhead Shuttle approach and lower it to dock level.",
                spans_levels=[1, 2],
                validation="PARTIAL",
                conflicts=["Structural clearance and guarding at r76,c17 require survey."],
            ),
            _facility(
                dock_handoff_id,
                "shuttle_dock_handoff",
                "port",
                74,
                17,
                1,
                "Transfer a case from dock-01 into the ground conveyor without vehicle travel on the belt.",
                validation="PARTIAL",
                refs=["existing-dock-01", "existing-dock-conveyor-cell-r74-c17"],
                occupancy="co_located_with_reference",
                conflicts=["Dock device envelope and acceptance signal are not documented."],
            ),
        ]
    )

    approach_points = [
        _position(int(point["row"]), int(point["col"]), int(point["level"]))
        for point in layout["overheadApproach"]["points"]
    ]
    connections.extend(
        [
            _connection(
                "right-source27-fork-pick",
                "existing-source-cell-r96-c27-L2",
                pickup_27_id,
                "shuttle",
                "fork_pick",
                "west",
                [_position(96, 27, 2), _position(96, 25, 2)],
                handoff="Shuttle stops at c25, extends fork east, verifies load, and retracts before travel.",
            ),
            _connection(
                "right-source27-stop-to-track",
                pickup_27_id,
                "existing-track-L2-r96-c23",
                "shuttle",
                "shuttle_track",
                "west",
                [_position(96, 25, 2), _position(96, 23, 2)],
                required_cells=["proposed-track-L2-r96-c25"],
            ),
            _connection(
                "right-source29-fork-pick",
                "existing-source-cell-r96-c29-L2",
                pickup_29_id,
                "shuttle",
                "fork_pick",
                "east",
                [_position(96, 29, 2), _position(96, 31, 2)],
                handoff="Shuttle remains on L2-r96-c31 and extends fork west into the source slot.",
            ),
            _connection(
                "right-source27-graph-to-approach",
                "existing-track-L2-r96-c23",
                "existing-track-L2-r96-c19",
                "shuttle",
                "shuttle_graph",
                "graph_path",
                [_position(96, 23, 2), _position(96, 19, 2)],
                evidence=["Both endpoints are real nodes in the connected L2 ShuttleGraph."],
            ),
            _connection(
                "right-source29-graph-to-approach",
                "existing-track-L2-r96-c31",
                "existing-track-L2-r96-c19",
                "shuttle",
                "shuttle_graph",
                "graph_path",
                [_position(96, 31, 2), _position(96, 19, 2)],
                evidence=["Both endpoints are real nodes in the connected L2 ShuttleGraph."],
            ),
            _connection(
                "right-overhead-track-to-dock-transfer",
                "existing-track-L2-r96-c19",
                transfer_id,
                "shuttle",
                "proposed_shuttle_track",
                "north_then_west",
                approach_points,
                status="PARTIAL",
                required_cells=[
                    f"proposed-track-L2-r{point.row}-c{point.col}" for point in approach_points[1:]
                ],
                conflicts=[str(layout["overheadApproach"]["note"])],
            ),
            _connection(
                "right-dock-transfer-descent",
                transfer_id,
                dock_handoff_id,
                "transfer_device",
                "height_transfer",
                "north_and_down",
                [_position(76, 17, 2), _position(74, 17, 1)],
                handoff=str(layout["dockTransfer"]["method"]),
                height={
                    "fromLevel": 2,
                    "toLevel": 1,
                    "deviceId": transfer_id,
                    "method": "enclosed vertical case transfer",
                },
                status="PARTIAL",
            ),
            _connection(
                "right-dock-to-ground-conveyor",
                dock_handoff_id,
                "existing-dock-conveyor-cell-r74-c17",
                "port->ground_conveyor",
                "handoff",
                "right",
                [_position(74, 17, 1), _position(74, 18, 1)],
                handoff="Dock clamps the case, confirms conveyor space, then releases it onto the right-running belt.",
                status="PARTIAL",
            ),
        ]
    )
    return facility_ids, [
        "right-source27-fork-pick",
        "right-source27-stop-to-track",
        "right-source29-fork-pick",
        "right-source27-graph-to-approach",
        "right-source29-graph-to-approach",
        "right-overhead-track-to-dock-transfer",
        "right-dock-transfer-descent",
        "right-dock-to-ground-conveyor",
    ]


def _build_options(
    raw: dict[str, Any],
    references: list[LayoutReferenceNode],
    facilities: list[ProposedLayoutFacility],
    connections: list[ProposedLayoutConnection],
    common_facilities: list[str],
    common_connections: list[str],
) -> tuple[list[LayoutOption], list[LayoutRoute]]:
    references.extend(
        [
            _reference("existing-ground-cell-r74-c48", "conveyor_cell", "ground_conveyor", 74, 48, 1, "cell-r74-c48"),
            _reference("existing-elevated-cell-r74-c52", "conveyor_cell", "elevated_conveyor", 74, 52, 2, "cell-r74-c52"),
            _reference("existing-elevated-cell-r74-c66", "conveyor_cell", "elevated_conveyor", 74, 66, 2, "cell-r74-c66"),
            _reference("existing-elevated-cell-r74-c67", "conveyor_cell", "elevated_conveyor", 74, 67, 2, "cell-r74-c67"),
            _reference("existing-turn-transfer-14", "turn_transfer", "transfer_device", 74, 43, 1, "turn_transfer-14"),
            _reference("existing-ground-cell-r74-c48-b", "conveyor_cell", "ground_conveyor", 74, 48, 1, "cell-r74-c48"),
        ]
    )
    options: list[LayoutOption] = []
    routes: list[LayoutRoute] = []

    option_a = next(item for item in raw["connectionOptions"] if item["optionId"] == "scheme-a-height-transfer")
    converter = option_a["heightTransfer"]
    converter_id = str(converter["deviceId"])
    merge_a = option_a["merge"]
    outbound_a = option_a["outbound"]
    merge_a_id = str(merge_a["facilityId"])
    outbound_a_id = str(outbound_a["facilityId"])
    facilities.extend(
        [
            _facility(
                converter_id,
                "conveyor_height_transfer",
                "transfer_device",
                74,
                50,
                1,
                "Bridge c48 to c52 and convert the belt from L1 to L2.",
                capacity=int(converter["capacity"]),
                spans_levels=[1, 2],
                validation="PARTIAL",
                conflicts=["Physical incline/lift envelope inside c49..c51 requires confirmation."],
            ),
            _facility(
                merge_a_id,
                "conveyor_merge",
                "elevated_conveyor",
                int(merge_a["row"]),
                int(merge_a["col"]),
                int(merge_a["level"]),
                "Merge the right full-case line with the left zone-port-4 breakpack output.",
                capacity=int(merge_a["capacity"]),
                validation="PARTIAL",
                refs=["existing-elevated-cell-r74-c67"],
                occupancy="co_located_with_reference",
                conflicts=["Requires a two-input merge device; the current cell is conveyor only."],
            ),
            _facility(
                outbound_a_id,
                "outbound",
                "outbound",
                int(outbound_a["row"]),
                int(outbound_a["col"]),
                int(outbound_a["level"]),
                "Physical outbound acceptance point with completion sensor.",
                capacity=int(outbound_a["capacity"]),
                validation="PARTIAL",
                conflicts=["Discharge equipment beyond c96 is not represented in the topology."],
            ),
        ]
    )
    bridge_points = [
        _position(int(point["row"]), int(point["col"]), int(point["level"]))
        for point in converter["points"]
    ]
    missing_cells = [
        cell
        for item in option_a["missingConveyorRanges"]
        for cell in _cell_range(int(item["row"]), int(item["colStart"]), int(item["colEnd"]))
    ]
    option_a_connections = [
        "scheme-a-c48-to-c52-height-transfer",
        "scheme-a-right-input-to-merge",
        "scheme-a-left-input-to-merge",
        "scheme-a-merge-to-outbound",
        "scheme-a-outbound-acceptance",
    ]
    connections.extend(
        [
            _connection(
                option_a_connections[0],
                "existing-ground-cell-r74-c48",
                "existing-elevated-cell-r74-c52",
                "ground_conveyor->elevated_conveyor",
                "height_transfer",
                "right",
                bridge_points,
                handoff="Converter accepts one case at c48 and releases it to c52 only after level and space confirmation.",
                height={
                    "fromLevel": 1,
                    "toLevel": 2,
                    "deviceId": converter_id,
                    "method": str(converter["method"]),
                },
                required_cells=["cell-r74-c49", "cell-r74-c50", "cell-r74-c51"],
            ),
            _connection(
                option_a_connections[1],
                "existing-elevated-cell-r74-c52",
                merge_a_id,
                "elevated_conveyor",
                "conveyor",
                "right",
                [_position(74, 52, 2), _position(74, 67, 2)],
                required_cells=_cell_range(74, 52, 67),
                evidence=["Missing cells are explicitly listed as proposed, never inferred from blank grid."],
            ),
            _connection(
                option_a_connections[2],
                "proposed-port-lift-zone-port-4",
                merge_a_id,
                "transfer_device->elevated_conveyor",
                "merge_input",
                "down",
                [_position(73, 67, 1), _position(74, 67, 2)],
                handoff="Port lift requests merge capacity, raises the picked load, and discharges from the north input.",
                height={
                    "fromLevel": 1,
                    "toLevel": 2,
                    "deviceId": "proposed-port-lift-zone-port-4",
                    "method": "vertical transfer",
                },
            ),
            _connection(
                option_a_connections[3],
                merge_a_id,
                outbound_a_id,
                "elevated_conveyor",
                "conveyor",
                "right",
                [_position(74, 67, 2), _position(74, 96, 2)],
                capacity=1,
                required_cells=_cell_range(74, 68, 96),
                evidence=[f"Proposed missing cells: {missing_cells}"],
            ),
            _connection(
                option_a_connections[4],
                outbound_a_id,
                "MISSING:external-outfeed",
                "outbound",
                "acceptance",
                "right",
                [_position(74, 95, 2), _position(74, 96, 2)],
                capacity=int(outbound_a["capacity"]),
                handoff="Outbound sensor confirms physical receipt before order delivery can complete.",
                status="PARTIAL",
                conflicts=["External discharge destination remains outside the approved topology contract."],
            ),
        ]
    )
    a_facilities = common_facilities + [converter_id, merge_a_id, outbound_a_id]
    a_connections = common_connections + option_a_connections
    options.append(
        LayoutOption(
            optionId=str(option_a["optionId"]),
            label=str(option_a["label"]),
            status=str(option_a["status"]),
            description="Recommended static layout: complete the eastbound line and use explicit height-transfer, merge, and outbound devices.",
            facilityIds=a_facilities,
            connectionIds=a_connections,
            assumptions=[str(value) for value in option_a["assumptions"]],
        )
    )
    routes.extend(
        [
            LayoutRoute(
                routeId="left-zone-port-4-via-scheme-a",
                side="left",
                optionId=str(option_a["optionId"]),
                status="PARTIAL",
                facilityIds=[
                    "proposed-ctu-stop-zone-port-4",
                    "proposed-pick-station-zone-port-4",
                    "proposed-port-lift-zone-port-4",
                    merge_a_id,
                    outbound_a_id,
                ],
                connectionIds=[
                    "left-zone-port-4-stop-to-input",
                    "left-zone-port-4-input-to-pick",
                    "left-zone-port-4-pick-to-output",
                    "left-zone-port-4-output-to-port-lift",
                    option_a_connections[2],
                    option_a_connections[3],
                    option_a_connections[4],
                ],
                blockingReasons=["CTU storage aisles still lack an approved explicit road connection to the north corridor."],
            ),
            LayoutRoute(
                routeId="right-source27-via-scheme-a",
                side="right",
                optionId=str(option_a["optionId"]),
                status="PARTIAL",
                facilityIds=[pickup for pickup in common_facilities if "shuttle" in pickup] + [converter_id, merge_a_id, outbound_a_id],
                connectionIds=[
                    "right-source27-fork-pick",
                    "right-source27-stop-to-track",
                    "right-source27-graph-to-approach",
                    "right-overhead-track-to-dock-transfer",
                    "right-dock-transfer-descent",
                    "right-dock-to-ground-conveyor",
                    option_a_connections[0],
                    option_a_connections[1],
                    option_a_connections[3],
                    option_a_connections[4],
                ],
                blockingReasons=["Overhead guideway clearance above cell-r92-c19 requires structural approval."],
            ),
            LayoutRoute(
                routeId="right-source29-via-scheme-a",
                side="right",
                optionId=str(option_a["optionId"]),
                status="PARTIAL",
                facilityIds=[pickup for pickup in common_facilities if "shuttle" in pickup] + [converter_id, merge_a_id, outbound_a_id],
                connectionIds=[
                    "right-source29-fork-pick",
                    "right-source29-graph-to-approach",
                    "right-overhead-track-to-dock-transfer",
                    "right-dock-transfer-descent",
                    "right-dock-to-ground-conveyor",
                    option_a_connections[0],
                    option_a_connections[1],
                    option_a_connections[3],
                    option_a_connections[4],
                ],
                blockingReasons=["Overhead guideway clearance above cell-r92-c19 requires structural approval."],
            ),
        ]
    )

    option_b = next(item for item in raw["connectionOptions"] if item["optionId"] == "scheme-b-existing-transfer-reroute")
    merge_b = option_b["merge"]
    outbound_b = option_b["outbound"]
    merge_b_id = str(merge_b["facilityId"])
    outbound_b_id = str(outbound_b["facilityId"])
    facilities.extend(
        [
            _facility(
                merge_b_id,
                "conveyor_merge",
                "ground_conveyor",
                int(merge_b["row"]),
                int(merge_b["col"]),
                int(merge_b["level"]),
                "Investigate reuse of turn_transfer-14 as a two-input merge.",
                validation="BLOCKED",
                refs=["existing-turn-transfer-14"],
                occupancy="co_located_with_reference",
                conflicts=[str(reason) for reason in option_b["blockingReasons"][:2]],
            ),
            _facility(
                outbound_b_id,
                "outbound",
                "outbound",
                int(outbound_b["row"]),
                int(outbound_b["col"]),
                int(outbound_b["level"]),
                "Candidate discharge at the current end of the ground conveyor.",
                validation="BLOCKED",
                refs=["existing-ground-cell-r74-c48-b"],
                occupancy="co_located_with_reference",
                conflicts=[str(option_b["blockingReasons"][2])],
            ),
        ]
    )
    b_connections = [
        "scheme-b-right-input-to-turn-transfer-14",
        "scheme-b-left-input-to-turn-transfer-14",
        "scheme-b-merge-to-outbound",
    ]
    connections.extend(
        [
            _connection(
                b_connections[0],
                "existing-dock-conveyor-cell-r74-c17",
                merge_b_id,
                "ground_conveyor",
                "conveyor",
                "right",
                [_position(74, 17, 1), _position(74, 43, 1)],
                evidence=["The current ground conveyor is continuous and right-running from c17 through c43."],
            ),
            _connection(
                b_connections[1],
                "existing-elevated-cell-r74-c67",
                merge_b_id,
                "elevated_conveyor->ground_conveyor",
                "missing_transfer",
                "left",
                [_position(74, 67, 2), _position(74, 43, 1)],
                status="BLOCKED",
                conflicts=[str(reason) for reason in option_b["blockingReasons"][:2]],
            ),
            _connection(
                b_connections[2],
                merge_b_id,
                outbound_b_id,
                "ground_conveyor",
                "conveyor",
                "right",
                [_position(74, 43, 1), _position(74, 48, 1)],
                status="BLOCKED",
                conflicts=[str(option_b["blockingReasons"][2])],
            ),
        ]
    )
    options.append(
        LayoutOption(
            optionId=str(option_b["optionId"]),
            label=str(option_b["label"]),
            status="BLOCKED",
            description="Reuse turn_transfer-14; rejected until direction, height transfer, and downstream discharge are redesigned.",
            facilityIds=common_facilities + [merge_b_id, outbound_b_id],
            connectionIds=common_connections + b_connections,
            blockingReasons=[str(reason) for reason in option_b["blockingReasons"]],
        )
    )
    routes.append(
        LayoutRoute(
            routeId="shared-via-scheme-b",
            side="shared",
            optionId=str(option_b["optionId"]),
            status="BLOCKED",
            facilityIds=[merge_b_id, outbound_b_id],
            connectionIds=b_connections,
            blockingReasons=[str(reason) for reason in option_b["blockingReasons"]],
        )
    )
    return options, routes


def validate_transport_layout_plan(
    topology: dict[str, Any], plan: TransportLayoutPlanResponse
) -> list[NetworkIssue]:
    issues: list[NetworkIssue] = []
    all_ids = (
        [item.referenceId for item in plan.references]
        + [item.facilityId for item in plan.facilities]
        + [item.connectionId for item in plan.connections]
        + [item.routeId for item in plan.routes]
        + [item.optionId for item in plan.options]
    )
    duplicates = sorted({value for value in all_ids if all_ids.count(value) > 1})
    if duplicates:
        issues.append(
            _issue(
                "LAYOUT_DUPLICATE_ID",
                "Layout IDs must be globally unique.",
                evidence=duplicates,
            )
        )

    rows = int(topology.get("grid", {}).get("rows") or 0)
    cols = int(topology.get("grid", {}).get("cols") or 0)
    positions = [
        (item.referenceId, item.position) for item in plan.references
    ] + [
        (item.facilityId, item.position) for item in plan.facilities
    ] + [
        (item.connectionId, point)
        for item in plan.connections
        for point in item.points
    ]
    for source_id, position in positions:
        if not (0 <= position.row < rows and 0 <= position.col < cols):
            issues.append(
                _issue(
                    "LAYOUT_OUT_OF_BOUNDS",
                    "Candidate position is outside the topology grid.",
                    source_id,
                    evidence=[f"row={position.row}, col={position.col}, grid={rows}x{cols}"],
                )
            )

    occupied_nodes = {
        (str(node.get("floor") or "1F"), int(node["row"]), int(node["col"])): node
        for node in topology.get("nodes", [])
        if node.get("type") in {"storage", "ctu_robot", "shuttle_robot", "lift_transfer", "turn_transfer", "shuttle_dock"}
    }
    blocked_cells = {
        (str(cell.get("floor") or "1F"), int(cell["row"]), int(cell["col"]))
        for cell in topology.get("cells", [])
        if cell.get("type") == "blocked" or not cell.get("walkable", True)
    }
    for facility in plan.facilities:
        key = (
            facility.position.floor,
            facility.position.row,
            facility.position.col,
        )
        if facility.occupancyPolicy == "exclusive" and key in occupied_nodes:
            node = occupied_nodes[key]
            issues.append(
                _issue(
                    "LAYOUT_FIXED_NODE_COLLISION",
                    "Exclusive proposed facility overlaps an existing rack or device node.",
                    facility.facilityId,
                    str(node["id"]),
                )
            )
        if facility.occupancyPolicy == "exclusive" and key in blocked_cells:
            issues.append(
                _issue(
                    "LAYOUT_BLOCKED_CELL_COLLISION",
                    "Exclusive proposed facility overlaps a blocked cell.",
                    facility.facilityId,
                )
            )
        if (
            facility.occupancyPolicy == "co_located_with_reference"
            and not facility.existingRefIds
        ):
            issues.append(
                _issue(
                    "LAYOUT_COLOCATION_REFERENCE_MISSING",
                    "Co-located candidate must name the existing reference it shares.",
                    facility.facilityId,
                )
            )

    for connection in plan.connections:
        level_changes = any(
            left.level != right.level
            for left, right in zip(connection.points, connection.points[1:])
        )
        if connection.status != "BLOCKED" and level_changes and not connection.heightTransition:
            issues.append(
                _issue(
                    "LAYOUT_UNDECLARED_LEVEL_CHANGE",
                    "Connection changes level without a height-transition device.",
                    connection.connectionId,
                )
            )

    try:
        graph, validation = shuttle_context(topology)
        real_tracks = set(graph)
        real_lifts = {str(lift["lift_id"]) for lift in validation.get("lifts") or []}
        for reference in plan.references:
            if reference.kind == "shuttle_track" and reference.sourceId not in real_tracks:
                issues.append(
                    _issue(
                        "LAYOUT_SHUTTLE_TRACK_REFERENCE_MISSING",
                        "Layout references a ShuttleGraph node that does not exist.",
                        reference.referenceId,
                        reference.sourceId,
                    )
                )
            if reference.kind == "shuttle_lift" and reference.sourceId not in real_lifts:
                issues.append(
                    _issue(
                        "LAYOUT_SHUTTLE_LIFT_REFERENCE_MISSING",
                        "Layout references an unverified Shuttle lift.",
                        reference.referenceId,
                        reference.sourceId,
                    )
                )
    except ValueError as error:
        issues.append(
            _issue(
                "LAYOUT_SHUTTLE_GRAPH_UNAVAILABLE",
                "The ShuttleGraph contract could not be validated.",
                evidence=[str(error)],
            )
        )

    merge = next(
        (item for item in plan.facilities if item.facilityId == "proposed-merge-a-r74-c67"),
        None,
    )
    if merge:
        inputs = [
            connection
            for connection in plan.connections
            if connection.toId == merge.facilityId and connection.status != "BLOCKED"
        ]
        outputs = [
            connection
            for connection in plan.connections
            if connection.fromId == merge.facilityId and connection.status != "BLOCKED"
        ]
        if len(inputs) < 2:
            issues.append(
                _issue(
                    "LAYOUT_MERGE_INPUTS_INSUFFICIENT",
                    "Recommended merge requires two legal upstream inputs.",
                    merge.facilityId,
                    evidence=[connection.connectionId for connection in inputs],
                )
            )
        if not outputs:
            issues.append(
                _issue(
                    "LAYOUT_MERGE_OUTPUT_MISSING",
                    "Recommended merge requires a legal downstream output.",
                    merge.facilityId,
                )
            )
    return issues


def _validate_picking_arm(
    topology: dict[str, Any], arm: PickingArmLayout
) -> list[NetworkIssue]:
    issues: list[NetworkIssue] = []
    rows = int(topology["grid"]["rows"])
    cols = int(topology["grid"]["cols"])
    base = arm.installation
    if not (0 <= base.row < rows and 0 <= base.col < cols):
        issues.append(
            _issue(
                "PICKING_ARM_OUT_OF_BOUNDS",
                "The proposed arm base lies outside the topology grid.",
                arm.armId,
                evidence=[f"r{base.row},c{base.col}; grid={rows}x{cols}"],
            )
        )
    occupied = [
        str(item["id"])
        for item in [*topology.get("cells", []), *topology.get("nodes", [])]
        if int(item.get("row", -1)) == base.row
        and int(item.get("col", -1)) == base.col
    ]
    occupied.extend(
        str(zone["id"])
        for zone in topology.get("zones", [])
        if int(zone.get("y", -1)) <= base.row
        < int(zone.get("y", -1)) + int(zone.get("height", 0))
        and int(zone.get("x", -1)) <= base.col
        < int(zone.get("x", -1)) + int(zone.get("width", 0))
    )
    if occupied:
        issues.append(
            _issue(
                "PICKING_ARM_BASE_OCCUPIED",
                "The proposed arm base overlaps formal topology occupancy.",
                arm.armId,
                evidence=occupied,
            )
        )
    conveyor_clearances = [
        (
            math.hypot(int(cell["row"]) - base.row, int(cell["col"]) - base.col),
            str(cell["id"]),
        )
        for cell in topology.get("cells", [])
        if cell.get("type") == "conveyor"
    ]
    if conveyor_clearances:
        nearest_distance, nearest_id = min(conveyor_clearances)
        required_clearance = arm.kinematics.baseRadius + 0.5
        if nearest_distance < required_clearance - 1e-9:
            issues.append(
                _issue(
                    "PICKING_ARM_BASE_CLEARANCE_INSUFFICIENT",
                    "The arm base overlaps the clearance envelope of a neighbouring conveyor.",
                    arm.armId,
                    nearest_id,
                    evidence=[
                        f"centerDistance={nearest_distance:.3f}",
                        f"requiredClearance={required_clearance:.3f}",
                    ],
                )
            )
    for endpoint_name, endpoint in (("pickup", arm.pickup), ("dropoff", arm.dropoff)):
        planar_reach = math.hypot(
            endpoint.position.row - base.row,
            endpoint.position.col - base.col,
        )
        if planar_reach > arm.workRadius + 1e-9:
            issues.append(
                _issue(
                    "PICKING_ARM_ENDPOINT_OUT_OF_REACH",
                    f"The {endpoint_name} endpoint exceeds the declared work radius.",
                    arm.armId,
                    str(endpoint.topologyCellId),
                    evidence=[
                        f"planarReach={planar_reach:.3f}",
                        f"workRadius={arm.workRadius:.3f}",
                    ],
                )
            )
    link_reach = arm.kinematics.upperArmLength + arm.kinematics.forearmLength
    if link_reach + 1e-9 < arm.workRadius:
        issues.append(
            _issue(
                "PICKING_ARM_KINEMATICS_UNDERSIZED",
                "The configured links cannot cover the declared work radius.",
                arm.armId,
                evidence=[f"linkReach={link_reach:.3f}", f"workRadius={arm.workRadius:.3f}"],
            )
        )
    pickup_cell = next(
        (
            cell
            for cell in topology.get("cells", [])
            if str(cell.get("id")) == str(arm.pickup.topologyCellId)
        ),
        None,
    )
    if not pickup_cell or pickup_cell.get("type") != "conveyor":
        issues.append(
            _issue(
                "PICKING_ARM_PICKUP_NOT_CONVEYOR",
                "The arm pickup must reference a real conveyor cell.",
                arm.armId,
                str(arm.pickup.topologyCellId),
            )
        )
    elif str(pickup_cell.get("conveyorDirection")) != str(arm.pickup.direction):
        issues.append(
            _issue(
                "PICKING_ARM_PICKUP_DIRECTION_MISMATCH",
                "The arm plan pickup direction differs from the formal conveyor direction.",
                arm.armId,
                str(arm.pickup.topologyCellId),
            )
        )
    drop_cell = next(
        (
            cell
            for cell in topology.get("cells", [])
            if str(cell.get("id")) == str(arm.dropoff.topologyCellId)
        ),
        None,
    )
    if not drop_cell or drop_cell.get("type") != "conveyor":
        issues.append(
            _issue(
                "PICKING_ARM_DROPOFF_NOT_CONVEYOR",
                "The arm dropoff must reference a real conveyor cell.",
                arm.armId,
                str(arm.dropoff.topologyCellId),
            )
        )
    elif str(drop_cell.get("conveyorDirection")) != str(arm.dropoff.direction):
        issues.append(
            _issue(
                "PICKING_ARM_DROPOFF_DIRECTION_MISMATCH",
                "The arm plan direction differs from the formal conveyor direction.",
                arm.armId,
                str(arm.dropoff.topologyCellId),
                evidence=[
                    f"plan={arm.dropoff.direction}",
                    f"topology={drop_cell.get('conveyorDirection')}",
                ],
            )
        )
    downstream = next(
        (
            cell
            for cell in topology.get("cells", [])
            if str(cell.get("id")) == arm.downstreamCellId
        ),
        None,
    )
    if arm.downstreamStatus == "PASS" and (
        not downstream
        or downstream.get("type") != "conveyor"
        or str(downstream.get("conveyorDirection")) != str(arm.dropoff.direction)
    ):
        issues.append(
            _issue(
                "PICKING_ARM_DOWNSTREAM_INVALID",
                "A PASS downstream must be a real conveyor cell with matching direction.",
                str(arm.dropoff.topologyCellId),
                arm.downstreamCellId,
            )
        )
    path_ids = [step.cellId for step in arm.previewConveyorPath]
    if not path_ids or path_ids[0] != str(arm.dropoff.topologyCellId):
        issues.append(
            _issue(
                "PICKING_ARM_PREVIEW_PATH_START_INVALID",
                "The conveyor preview path must start at the arm dropoff cell.",
                str(arm.dropoff.topologyCellId),
                path_ids[0] if path_ids else None,
            )
        )
    if arm.downstreamStatus == "PASS" and arm.downstreamCellId not in path_ids:
        issues.append(
            _issue(
                "PICKING_ARM_PREVIEW_PATH_DOWNSTREAM_MISSING",
                "The declared downstream cell is absent from the directed preview path.",
                str(arm.dropoff.topologyCellId),
                arm.downstreamCellId,
            )
        )
    return issues


def _build_picking_arm_conveyor_path(
    topology: dict[str, Any], start_cell_id: str, level: int, stop_cell_id: str | None = None
) -> tuple[list[PickingArmConveyorStep], str]:
    cells = {
        str(cell["id"]): cell
        for cell in topology.get("cells", [])
        if cell.get("type") == "conveyor"
    }
    by_position = {
        (str(cell.get("floor") or "1F"), int(cell["row"]), int(cell["col"])): cell
        for cell in cells.values()
    }
    nodes_by_position: dict[tuple[str, int, int], list[str]] = {}
    for node in topology.get("nodes", []):
        key = (
            str(node.get("floor") or "1F"),
            int(node.get("row", -1)),
            int(node.get("col", -1)),
        )
        nodes_by_position.setdefault(key, []).append(str(node["id"]))
    direction_delta = {
        "right": (0, 1),
        "left": (0, -1),
        "up": (-1, 0),
        "down": (1, 0),
    }

    def belt_height(cell: dict[str, Any]) -> float | None:
        if bool(cell.get("conveyorGround")):
            return 0.6 if level == 1 else None
        if int(cell.get("levels") or 1) < level:
            return None
        return 1.5 + (level - 1) * 1.5

    current = cells.get(start_cell_id)
    if current is None or belt_height(current) is None:
        return [], f"invalid start cell or level: {start_cell_id}/L{level}"
    steps: list[PickingArmConveyorStep] = []
    visited: set[str] = set()
    while current is not None and len(steps) < 500:
        cell_id = str(current["id"])
        if cell_id in visited:
            return steps, f"cycle detected at {cell_id}"
        visited.add(cell_id)
        floor = str(current.get("floor") or "1F")
        row = int(current["row"])
        col = int(current["col"])
        direction = str(current.get("conveyorDirection") or "right")
        height = belt_height(current)
        if height is None:
            return steps, f"level L{level} is unavailable at {cell_id}"
        key = (floor, row, col)
        steps.append(
            PickingArmConveyorStep(
                cellId=cell_id,
                position=LayoutPosition(floor=floor, row=row, col=col, level=level),
                direction=direction,
                beltHeight=height,
                deviceIds=nodes_by_position.get(key, []),
            )
        )
        if stop_cell_id and cell_id == stop_cell_id:
            return steps, f"requested stop reached at {cell_id}"
        delta = direction_delta.get(direction)
        if delta is None:
            return steps, f"unsupported direction {direction} at {cell_id}"
        next_key = (floor, row + delta[0], col + delta[1])
        next_cell = by_position.get(next_key)
        if next_cell is None:
            return steps, f"next conveyor cell missing at r{next_key[1]},c{next_key[2]}"
        next_height = belt_height(next_cell)
        if next_height is None or abs(next_height - height) > 1e-9:
            return steps, f"undeclared level change from {cell_id} to {next_cell['id']}"
        next_direction = str(next_cell.get("conveyorDirection") or "right")
        if next_direction != direction:
            turn_devices = [
                node_id
                for position in (key, next_key)
                for node_id in nodes_by_position.get(position, [])
                if next(
                    (
                        node.get("type") == "turn_transfer"
                        for node in topology.get("nodes", [])
                        if str(node.get("id")) == node_id
                    ),
                    False,
                )
            ]
            if not turn_devices:
                return steps, f"direction changes without turn_transfer before {next_cell['id']}"
        current = next_cell
    return steps, "path guard reached"


def _build_picking_arm_transfer_path(
    topology: dict[str, Any], start_cell_id: str, level: int, transfer: dict[str, Any]
) -> tuple[list[PickingArmConveyorStep], str, str, list[str]]:
    device_id = str(transfer["deviceId"])
    entry_id = str(transfer["entryCellId"])
    via_id = str(transfer["viaCellId"])
    exit_id = str(transfer["exitCellId"])
    source, source_stop = _build_picking_arm_conveyor_path(
        topology, start_cell_id, level, entry_id
    )
    via, via_stop = _build_picking_arm_conveyor_path(topology, via_id, level, via_id)
    target, target_stop = _build_picking_arm_conveyor_path(topology, exit_id, level)
    nodes = {str(node["id"]): node for node in topology.get("nodes", [])}
    device = nodes.get(device_id)
    evidence = [str(transfer.get("note") or "")]
    if (
        not source
        or source[-1].cellId != entry_id
        or not via
        or not target
        or device is None
        or device.get("type") != "turn_transfer"
    ):
        reason = "; ".join((source_stop, via_stop, target_stop))
        return source, "BLOCKED", reason, evidence
    entry = source[-1]
    source[-1] = entry.model_copy(
        update={"transitionToNext": "device_transfer", "transferDeviceId": device_id}
    )
    via[0] = via[0].model_copy(
        update={"transitionToNext": "device_transfer", "transferDeviceId": device_id}
    )
    target[-1] = target[-1].model_copy(update={"transitionToNext": "endpoint"})
    evidence.extend(
        [
            f"{device_id} is a real turn_transfer node at r{device['row']},c{device['col']}.",
            f"The preview device edge is proposed: {entry_id}→{via_id}→{exit_id}.",
            "Every visual waypoint is a real L2 conveyor cell; the device edge is not a formal topology edge.",
        ]
    )
    return [*source, via[0], *target], str(transfer["validationStatus"]), target_stop, evidence


def build_transport_layout_plan(
    topology: dict[str, Any], raw: dict[str, Any]
) -> TransportLayoutPlanResponse:
    references: list[LayoutReferenceNode] = []
    facilities: list[ProposedLayoutFacility] = []
    connections: list[ProposedLayoutConnection] = []
    left_facilities, left_connections = _build_left_layout(
        raw, references, facilities, connections
    )
    right_facilities, right_connections = _build_right_layout(
        raw, references, facilities, connections
    )
    common_facilities = left_facilities + right_facilities
    common_connections = left_connections + right_connections
    options, routes = _build_options(
        raw,
        references,
        facilities,
        connections,
        common_facilities,
        common_connections,
    )
    design_issues = [
        _issue(
            "LEFT_STORAGE_ROAD_CONNECTION_UNCONFIRMED",
            "The proposed Port corridor is geometrically clear, but no approved explicit road connects it to CTU storage aisles.",
            "proposed-left-ctu-road-corridor",
            "CTU storage network",
            severity="WARNING",
        ),
        _issue(
            "RIGHT_OVERHEAD_CLEARANCE_UNCONFIRMED",
            "The L2 Shuttle approach crosses above the ground conveyor at r92,c19; structural clearance is not proven.",
            "right-overhead-track-to-dock-transfer",
            "cell-r92-c19",
            severity="WARNING",
        ),
        _issue(
            "SCHEME_B_DIRECTION_AND_LEVEL_BLOCKED",
            "Scheme B cannot supply a westbound L2 left input or a legal L2-to-L1 transfer at turn_transfer-14.",
            "cell-r74-c67",
            "turn_transfer-14",
        ),
        _issue(
            "PLAN_PREVIEW_ONLY",
            "The plan is an independent proposed data layer and does not modify formal topology connectivity.",
            str(raw["planId"]),
            severity="INFO",
        ),
        _issue(
            "PICKING_ARM_HANDOFF_PENDING_G3",
            "The picking arm is a proposed visual handoff. No authoritative picked-goods, output-space or completion event exists yet.",
            "proposed-picking-arm-r89-c63",
            "cell-r84-c60",
            evidence=["Animation completion must not mutate inventory, delivered quantity or order status."],
            severity="WARNING",
        ),
        _issue(
            "PICKING_ARM_PICK_SOURCE_UNCONFIRMED",
            "The pickup cell is real, but no formal pick_station or breakpack event routes picked order goods to it.",
            "MISSING:breakpack_exit",
            "cell-r92-c48",
            severity="WARNING",
        ),
        _issue(
            "PICKING_ARM_TARGET_LINE_TRANSFER_UNAPPROVED",
            "The preview uses turn_transfer-15 to move down from row84 to the row86 right-running line, but the formal topology has no declared device edge for this handoff.",
            "turn_transfer-15",
            "cell-r86-c60",
            evidence=["Preview edge: cell-r84-c60 -> cell-r85-c60 -> cell-r86-c60."],
            severity="WARNING",
        ),
    ]
    arm_raw = raw.get("pickingArmPreview") or {}
    pickup_raw = arm_raw.get("pickup") or {}
    dropoff_raw = arm_raw.get("dropoff") or {}
    install_raw = arm_raw.get("installation") or {}
    lift_raw = arm_raw.get("liftClearance") or {}
    downstream_raw = arm_raw.get("downstream") or {}
    transfer_raw = arm_raw.get("previewTransfer") or {}
    kinematics_raw = arm_raw.get("kinematics") or {}
    arm_path, arm_path_status, arm_path_stop, arm_transfer_evidence = _build_picking_arm_transfer_path(
        topology,
        str(dropoff_raw["topologyCellId"]),
        int(dropoff_raw["level"]),
        transfer_raw,
    )
    picking_arm = PickingArmLayout(
        armId=str(arm_raw["armId"]),
        validationStatus=str(arm_raw["validationStatus"]),
        installation=_position(
            int(install_raw["row"]), int(install_raw["col"]), int(install_raw["level"])
        ),
        mountHeight=float(install_raw["mountHeight"]),
        workRadius=float(install_raw["workRadius"]),
        kinematics=PickingArmKinematics(**kinematics_raw),
        pickup=PickingArmEndpoint(
            nodeId=str(pickup_raw["nodeId"]),
            topologyCellId=str(pickup_raw["topologyCellId"]),
            position=_position(
                int(pickup_raw["row"]), int(pickup_raw["col"]), int(pickup_raw["level"])
            ),
            beltHeight=float(pickup_raw["beltHeight"]),
            deckIndex=int(pickup_raw["deckIndex"]),
            direction=str(pickup_raw["direction"]),
            provenance=str(pickup_raw["provenance"]),
            validationStatus=str(pickup_raw["validationStatus"]),
            evidence=[str(pickup_raw["note"])],
        ),
        dropoff=PickingArmEndpoint(
            nodeId=str(dropoff_raw["nodeId"]),
            topologyCellId=str(dropoff_raw["topologyCellId"]),
            position=_position(
                int(dropoff_raw["row"]), int(dropoff_raw["col"]), int(dropoff_raw["level"])
            ),
            beltHeight=float(dropoff_raw["beltHeight"]),
            deckIndex=int(dropoff_raw["deckIndex"]),
            direction=str(dropoff_raw["direction"]),
            provenance=str(dropoff_raw["provenance"]),
            validationStatus=str(dropoff_raw["validationStatus"]),
            evidence=[str(dropoff_raw["note"])],
        ),
        liftBId=str(lift_raw["liftId"]),
        liftBPosition=_position(int(lift_raw["row"]), int(lift_raw["col"]), 1),
        liftBClearanceCells=float(lift_raw["minimumPlanarDistanceCells"]),
        downstreamCellId=str(downstream_raw["nextRequiredCellId"]),
        downstreamStatus=str(downstream_raw["validationStatus"]),
        previewConveyorPath=arm_path,
        previewEndpointId=arm_path[-1].cellId if arm_path else str(dropoff_raw["topologyCellId"]),
        previewPathStatus=arm_path_status,
        previewStopReason=arm_path_stop,
        previewTransferDeviceId=str(transfer_raw["deviceId"]),
        previewTransferStatus=str(transfer_raw["validationStatus"]),
        previewTransferEvidence=arm_transfer_evidence,
        actionSequence=[str(value) for value in arm_raw["actionSequence"]],
        requiredAuthoritativeSignals=[
            str(value) for value in arm_raw["requiredAuthoritativeSignals"]
        ],
        canDeclareDelivery=False,
        evidence=[str(value) for value in arm_raw.get("evidence") or []],
    )
    response = TransportLayoutPlanResponse(
        planId=str(raw["planId"]),
        schemaVersion=str(raw["schemaVersion"]),
        topologyId=str(raw["topologyId"]),
        topologyRevision=str(raw["topologyRevision"]),
        overallStatus=str(raw["overallStatus"]),
        recommendedOptionId=str(raw["recommendedOptionId"]),
        pickingArm=picking_arm,
        references=references,
        facilities=facilities,
        connections=connections,
        routes=routes,
        options=options,
        issues=design_issues,
        summary={},
        assumptions=[str(value) for value in raw.get("assumptions") or []],
    )
    validation_issues = validate_transport_layout_plan(topology, response)
    validation_issues.extend(_validate_picking_arm(topology, picking_arm))
    issue_list = design_issues + validation_issues
    overall_status = "BLOCKED" if validation_issues else str(raw["overallStatus"])
    return response.model_copy(
        update={
            "overallStatus": overall_status,
            "issues": issue_list,
            "summary": {
                "referenceCount": len(references),
                "proposedFacilityCount": len(facilities),
                "connectionCount": len(connections),
                "routeCount": len(routes),
                "optionCount": len(options),
                "validationErrorCount": len(validation_issues),
                "formalTopologyMutated": False,
                "recommendedStaticConnectivity": "PASS",
                "recommendedPhysicalApproval": "PARTIAL",
                "pickingArmPreviewOnly": True,
                "pickingArmCanDeclareDelivery": False,
                "pickingArmPreviewPathNodeCount": len(arm_path),
            },
        }
    )


def load_transport_layout_plan(topology: dict[str, Any]) -> TransportLayoutPlanResponse:
    raw = _load_raw_plan(str(topology["id"]))
    actual_revision = topology_revision(topology)
    if str(raw.get("topologyId")) != str(topology["id"]):
        raise LayoutRevisionMismatch(
            f"Layout topologyId={raw.get('topologyId')} does not match {topology['id']}"
        )
    if str(raw.get("topologyRevision")) != actual_revision:
        raise LayoutRevisionMismatch(
            f"Layout topologyRevision={raw.get('topologyRevision')} does not match {actual_revision}"
        )
    snapshot = deepcopy(topology)
    plan = build_transport_layout_plan(topology, raw)
    if topology != snapshot:
        raise RuntimeError("Transport layout generation mutated the formal topology")
    return plan


def plan_path_for(topology_id: str) -> Path:
    return PLAN_ROOT / f"{topology_id}.g1b1.json"
