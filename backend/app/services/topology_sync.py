from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Iterable

from .conveyor import compile_conveyor_graph
from .shuttle_topology import topology_revision


PYC_F1_ROUTE_ANCHORS = (
    "cell-r86-c50",
    "cell-r86-c52",
    "cell-r86-c54",
    "cell-r86-c56",
    "cell-r86-c58",
    "cell-r86-c60",
    "cell-r88-c60",
    "cell-r90-c60",
    "cell-r92-c60",
    "cell-r94-c60",
    "cell-r96-c60",
    "cell-r98-c60",
    "cell-r100-c60",
    "cell-r102-c60",
    "cell-r104-c60",
    "cell-r106-c60",
    "cell-r108-c60",
    "cell-r108-c62",
    "cell-r108-c64",
    "cell-r108-c66",
)
PYC_F1_TURN_NODES = ("turn_transfer-15", "turn_transfer-13")
ACTIVE_RUN_STATUSES = frozenset({"idle", "running", "paused"})

# TopologyPayload.model_dump() materializes these optional Pydantic fields as
# explicit nulls. Every production reader treats null and a missing key alike
# for these fields (dict.get/or in Python, optional chaining/nullish fallback in
# TypeScript). This list is intentionally closed: unknown fields and all
# non-null values remain strict business differences.
PYDANTIC_NULL_EQUIVALENT_FIELDS: dict[str, frozenset[str]] = {
    "cells": frozenset({"conveyorDecks", "conveyorLineId", "conveyorMode"}),
    "nodes": frozenset({
        "transferDeck",
        "transferInputSide",
        "transferOutputSide",
        "skuId",
        "skuName",
        "largeCategory",
        "quantity",
    }),
    "zones": frozenset({"conveyorBinding"}),
}


class TopologySyncError(ValueError):
    pass


class TopologySyncConflict(TopologySyncError):
    pass


class TopologySyncBlocked(TopologySyncError):
    pass


def _by_id(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in items}


def _field_differences(
    collection: str,
    current: dict[str, Any],
    desired: dict[str, Any],
) -> tuple[list[str], list[str]]:
    differences: list[str] = []
    equivalent_defaults: list[str] = []
    null_equivalent = PYDANTIC_NULL_EQUIVALENT_FIELDS.get(collection, frozenset())
    for field in sorted(set(current) | set(desired)):
        current_has = field in current
        desired_has = field in desired
        if current_has != desired_has:
            present_value = current[field] if current_has else desired[field]
            if field in null_equivalent and present_value is None:
                equivalent_defaults.append(field)
            else:
                differences.append(field)
            continue
        if current[field] != desired[field]:
            differences.append(field)
    return differences, equivalent_defaults


def audit_pyc_f1_topology_sync(
    current: dict[str, Any], desired: dict[str, Any]
) -> dict[str, Any]:
    if current.get("id") != "topology-6ceb4920" or desired.get("id") != current.get("id"):
        raise TopologySyncConflict("PYC-F1 sync only supports topology-6ceb4920")
    if current.get("name") != desired.get("name"):
        raise TopologySyncConflict("Topology name differs")
    for field in ("grid", "meta"):
        if current.get(field) != desired.get(field):
            raise TopologySyncConflict(f"Topology {field} differs")

    allowed = {
        "cells": {
            item_id: {"conveyorDecks", "conveyorMode"}
            for item_id in PYC_F1_ROUTE_ANCHORS
        },
        "nodes": {
            item_id: {"transferDeck", "transferInputSide", "transferOutputSide"}
            for item_id in PYC_F1_TURN_NODES
        },
        "zones": {},
    }
    changes: dict[str, list[dict[str, Any]]] = {
        "cells": [],
        "nodes": [],
        "zones": [],
    }
    equivalent_default_counts: dict[str, Counter[str]] = {
        collection: Counter() for collection in ("cells", "nodes", "zones")
    }
    equivalent_default_items: dict[str, set[str]] = {
        collection: set() for collection in ("cells", "nodes", "zones")
    }
    for collection in ("cells", "nodes", "zones"):
        current_items = _by_id(current.get(collection, []))
        desired_items = _by_id(desired.get(collection, []))
        if current_items.keys() != desired_items.keys():
            raise TopologySyncConflict(f"Topology {collection} membership differs")
        for item_id in sorted(current_items):
            fields, equivalent_defaults = _field_differences(
                collection,
                current_items[item_id],
                desired_items[item_id],
            )
            if equivalent_defaults:
                equivalent_default_items[collection].add(item_id)
                equivalent_default_counts[collection].update(equivalent_defaults)
            if not fields:
                continue
            permitted = allowed[collection].get(item_id, set())
            unexpected = set(fields) - permitted
            if unexpected:
                raise TopologySyncConflict(
                    f"Unexpected {collection} difference at {item_id}: {sorted(unexpected)}"
                )
            for field in fields:
                current_value = current_items[item_id].get(field)
                desired_value = desired_items[item_id].get(field)
                if current_value not in (None, desired_value):
                    raise TopologySyncConflict(
                        f"Conflicting non-empty value at {collection}.{item_id}.{field}"
                    )
            changes[collection].append({"id": item_id, "fields": fields})

    return {
        "topologyId": str(current["id"]),
        "currentRevision": topology_revision(current),
        "desiredRevision": topology_revision(desired),
        "counts": {
            collection: len(current.get(collection, []))
            for collection in ("cells", "nodes", "zones")
        },
        "changes": changes,
        "semanticEquivalentDefaults": {
            collection: {
                "itemCount": len(equivalent_default_items[collection]),
                "fieldCounts": dict(sorted(equivalent_default_counts[collection].items())),
            }
            for collection in ("cells", "nodes", "zones")
        },
    }


def _assert_no_active_run(
    topology_id: str, runs: Iterable[dict[str, Any]]
) -> None:
    active = [
        str(run.get("id"))
        for run in runs
        if run.get("topology_id", run.get("topologyId")) == topology_id
        and str(run.get("status")) in ACTIVE_RUN_STATUSES
    ]
    if active:
        raise TopologySyncBlocked(
            f"Topology {topology_id} has active runs: {', '.join(active)}"
        )


def _validate_pyc_f1_route(topology: dict[str, Any]) -> None:
    graph = compile_conveyor_graph(topology)
    expected_blocks = [
        *[f"1F:86:{col}:upper" for col in range(50, 61, 2)],
        *[f"1F:{row}:60:upper" for row in range(88, 109, 2)],
        *[f"1F:108:{col}:upper" for col in range(62, 67, 2)],
    ]
    for current_id, next_id in zip(expected_blocks, expected_blocks[1:]):
        node = graph.get("nodes", {}).get(current_id)
        if node is None or node.get("next") != next_id:
            raise TopologySyncConflict(
                f"Directed upper-deck route is broken at {current_id}; expected {next_id}"
            )
    final_block = graph.get("nodes", {}).get("1F:108:66:upper")
    if final_block is None or final_block.get("next") is not None:
        raise TopologySyncConflict("Final Conveyor block must terminate at 1F:108:66:upper")
    cells = _by_id(topology.get("cells", []))
    endpoint = cells.get("cell-r108-c68")
    if endpoint is None or endpoint.get("type") != "road":
        raise TopologySyncConflict("cell-r108-c68 must remain a road endpoint")
    nodes = _by_id(topology.get("nodes", []))
    lift = nodes.get("lift_transfer-09")
    if (
        lift is None
        or lift.get("type") != "lift_transfer"
        or (int(lift.get("row", -1)), int(lift.get("col", -1))) != (108, 68)
    ):
        raise TopologySyncConflict("lift_transfer-09 is not the r108,c68 terminal device")
    route_issues = [
        issue
        for issue in graph.get("issues", [])
        if any(anchor.replace("cell-r", "").replace("-c", ":") in issue.get("blockId", "") for anchor in PYC_F1_ROUTE_ANCHORS)
    ]
    if route_issues:
        raise TopologySyncConflict(f"Conveyor graph reports route issues: {route_issues}")


def prepare_pyc_f1_topology_merge(
    current: dict[str, Any],
    desired: dict[str, Any],
    runs: Iterable[dict[str, Any]] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    _assert_no_active_run(str(current.get("id")), runs)
    report = audit_pyc_f1_topology_sync(current, desired)
    merged = deepcopy(current)
    merged_cells = _by_id(merged.get("cells", []))
    desired_cells = _by_id(desired.get("cells", []))
    for cell_id in PYC_F1_ROUTE_ANCHORS:
        for field in ("conveyorDecks", "conveyorMode"):
            merged_cells[cell_id][field] = deepcopy(desired_cells[cell_id].get(field))
    merged_nodes = _by_id(merged.get("nodes", []))
    desired_nodes = _by_id(desired.get("nodes", []))
    for node_id in PYC_F1_TURN_NODES:
        for field in ("transferDeck", "transferInputSide", "transferOutputSide"):
            merged_nodes[node_id][field] = deepcopy(desired_nodes[node_id].get(field))
    _validate_pyc_f1_route(merged)
    report["mergedRevision"] = topology_revision(merged)
    post_merge_report = audit_pyc_f1_topology_sync(merged, desired)
    if any(post_merge_report["changes"][collection] for collection in ("cells", "nodes", "zones")):
        raise TopologySyncConflict(
            "Minimal merge still contains non-equivalent business differences"
        )
    report["postMergeSemanticEquivalentDefaults"] = post_merge_report[
        "semanticEquivalentDefaults"
    ]
    return merged, report
