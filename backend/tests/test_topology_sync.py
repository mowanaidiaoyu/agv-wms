from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.core import db
from app.core.repository import Repository
from app.schemas import TopologyPayload
from app.services.conveyor import compile_conveyor_graph
from app.services.shuttle_topology import topology_revision
from app.services.topology_sync import (
    PYDANTIC_NULL_EQUIVALENT_FIELDS,
    PYC_F1_ROUTE_ANCHORS,
    TopologySyncBlocked,
    TopologySyncConflict,
    audit_pyc_f1_topology_sync,
    prepare_pyc_f1_topology_merge,
)


DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def _desired() -> dict:
    return json.loads((DATA_ROOT / "topology_666.json").read_text(encoding="utf-8"))


def _legacy_record() -> dict:
    topology = deepcopy(_desired())
    cells = {cell["id"]: cell for cell in topology["cells"]}
    for cell_id in PYC_F1_ROUTE_ANCHORS:
        cells[cell_id].pop("conveyorDecks", None)
        cells[cell_id].pop("conveyorMode", None)
    nodes = {node["id"]: node for node in topology["nodes"]}
    for node_id in ("turn_transfer-15", "turn_transfer-13"):
        nodes[node_id].pop("transferDeck", None)
        nodes[node_id].pop("transferInputSide", None)
        nodes[node_id].pop("transferOutputSide", None)
    return topology


def _expanded_legacy_record() -> dict:
    return TopologyPayload.model_validate(_legacy_record()).model_dump(by_alias=True)


def test_sync_audit_allows_only_the_pyc_f1_route_delta() -> None:
    report = audit_pyc_f1_topology_sync(_legacy_record(), _desired())

    assert report["counts"] == {"cells": 2474, "nodes": 2538, "zones": 182}
    assert len(report["changes"]["cells"]) == 20
    assert {item["id"] for item in report["changes"]["nodes"]} == {
        "turn_transfer-15",
        "turn_transfer-13",
    }
    assert report["changes"]["zones"] == []


def test_missing_and_null_are_equivalent_only_for_verified_pydantic_defaults() -> None:
    report = audit_pyc_f1_topology_sync(_expanded_legacy_record(), _desired())

    assert len(report["changes"]["cells"]) == 20
    assert len(report["changes"]["nodes"]) == 2
    equivalents = report["semanticEquivalentDefaults"]
    assert equivalents["cells"]["itemCount"] == 2474
    assert equivalents["nodes"]["itemCount"] == 2538
    assert equivalents["zones"]["itemCount"] == 182
    assert set(equivalents["cells"]["fieldCounts"]) <= PYDANTIC_NULL_EQUIVALENT_FIELDS["cells"]
    assert set(equivalents["nodes"]["fieldCounts"]) <= PYDANTIC_NULL_EQUIVALENT_FIELDS["nodes"]
    assert set(equivalents["zones"]["fieldCounts"]) <= PYDANTIC_NULL_EQUIVALENT_FIELDS["zones"]


def test_non_null_default_field_change_is_not_ignored() -> None:
    current = _expanded_legacy_record()
    cell = next(item for item in current["cells"] if item["id"] == "cell-r0-c17")
    cell["conveyorLineId"] = "unexpected-line"

    with pytest.raises(TopologySyncConflict, match="conveyorLineId"):
        audit_pyc_f1_topology_sync(current, _desired())


def test_unknown_missing_vs_null_field_is_not_ignored() -> None:
    current = _expanded_legacy_record()
    current["cells"][0]["futureBusinessField"] = None

    with pytest.raises(TopologySyncConflict, match="futureBusinessField"):
        audit_pyc_f1_topology_sync(current, _desired())


def test_sync_blocks_when_the_target_topology_has_an_active_run() -> None:
    with pytest.raises(TopologySyncBlocked, match="run-paused"):
        prepare_pyc_f1_topology_merge(
            _legacy_record(),
            _desired(),
            runs=[{
                "id": "run-paused",
                "status": "paused",
                "topology_id": "topology-6ceb4920",
            }],
        )


def test_sync_rejects_non_whitelisted_topology_conflicts() -> None:
    current = _legacy_record()
    current["grid"]["rows"] += 1

    with pytest.raises(TopologySyncConflict, match="grid differs"):
        prepare_pyc_f1_topology_merge(current, _desired())


def test_minimal_merge_preserves_lower_decks_and_builds_the_upper_route() -> None:
    merged, report = prepare_pyc_f1_topology_merge(_legacy_record(), _desired())
    graph = compile_conveyor_graph(merged)

    assert report["mergedRevision"] == topology_revision(merged)
    assert report["mergedRevision"] == topology_revision(_desired())
    assert graph["nodes"]["1F:86:60:lower"]["outputSide"] == "east"
    assert graph["nodes"]["1F:86:60:upper"]["inputSide"] == "west"
    assert graph["nodes"]["1F:86:60:upper"]["outputSide"] == "south"
    assert graph["nodes"]["1F:86:60:upper"]["next"] == "1F:88:60:upper"
    assert graph["nodes"]["1F:108:60:upper"]["inputSide"] == "north"
    assert graph["nodes"]["1F:108:60:upper"]["outputSide"] == "east"
    assert graph["nodes"]["1F:108:60:upper"]["next"] == "1F:108:62:upper"
    assert graph["nodes"]["1F:108:66:upper"]["next"] is None

    cells = {cell["id"]: cell for cell in merged["cells"]}
    nodes = {node["id"]: node for node in merged["nodes"]}
    assert cells["cell-r108-c68"]["type"] == "road"
    assert nodes["lift_transfer-09"]["type"] == "lift_transfer"
    assert (nodes["lift_transfer-09"]["row"], nodes["lift_transfer-09"]["col"]) == (108, 68)


def test_expanded_database_merge_preserves_defaults_and_has_stable_revision() -> None:
    current = _expanded_legacy_record()
    current_before = deepcopy(current)
    current_graph = compile_conveyor_graph(current)

    merged, report = prepare_pyc_f1_topology_merge(current, _desired())
    merged_graph = compile_conveyor_graph(merged)

    assert report["mergedRevision"] == topology_revision(merged)
    assert report["mergedRevision"] != topology_revision(_desired())
    for cell_id in PYC_F1_ROUTE_ANCHORS:
        row_col = cell_id.removeprefix("cell-r").split("-c")
        block_id = f"1F:{row_col[0]}:{row_col[1]}:lower"
        assert merged_graph["nodes"][block_id]["inputSide"] == current_graph["nodes"][block_id]["inputSide"]
        assert merged_graph["nodes"][block_id]["outputSide"] == current_graph["nodes"][block_id]["outputSide"]

    changed_cell_ids = set(PYC_F1_ROUTE_ANCHORS)
    before_cells = {item["id"]: item for item in current_before["cells"]}
    after_cells = {item["id"]: item for item in merged["cells"]}
    assert all(
        before_cells[item_id] == after_cells[item_id]
        for item_id in before_cells
        if item_id not in changed_cell_ids
    )
    before_nodes = {item["id"]: item for item in current_before["nodes"]}
    after_nodes = {item["id"]: item for item in merged["nodes"]}
    assert all(
        before_nodes[item_id] == after_nodes[item_id]
        for item_id in before_nodes
        if item_id not in {"turn_transfer-15", "turn_transfer-13"}
    )
    assert merged["zones"] == current_before["zones"]
    assert merged["grid"] == current_before["grid"]
    assert merged["meta"] == current_before["meta"]


def test_expected_revision_survives_isolated_repository_roundtrip(tmp_path, monkeypatch) -> None:
    isolated_database = tmp_path / "topology-sync.db"
    monkeypatch.setattr(db, "DB_PATH", isolated_database)
    db.init_db()
    repository = Repository()
    merged, report = prepare_pyc_f1_topology_merge(_expanded_legacy_record(), _desired())
    api_payload = TopologyPayload.model_validate(merged).model_dump(by_alias=True)

    repository.upsert_topology(api_payload["id"], api_payload["name"], api_payload)
    fetched = repository.get_topology(api_payload["id"])

    assert fetched is not None
    assert topology_revision(fetched["payload"]) == report["mergedRevision"]
    assert fetched["payload"] == api_payload


def test_merge_does_not_mutate_run_order_or_inventory_inputs() -> None:
    stopped_runs = [{
        "id": "run-stopped",
        "topologyId": "topology-6ceb4920",
        "status": "stopped",
        "orders": [{"id": "order-1", "status": "waiting_dispatch"}],
        "tasks": [{"id": "task-1", "status": "pending"}],
        "inventory": [{"cargoId": "cargo-1", "quantity": 1}],
    }]
    before = deepcopy(stopped_runs)

    prepare_pyc_f1_topology_merge(_expanded_legacy_record(), _desired(), stopped_runs)

    assert stopped_runs == before


def test_sync_keeps_utf8_labels_visible_to_the_api_serializer() -> None:
    merged, _ = prepare_pyc_f1_topology_merge(_legacy_record(), _desired())
    nodes = {node["id"]: node for node in merged["nodes"]}

    assert nodes["turn_transfer-15"]["label"] == "转向移载机-15"
    assert nodes["turn_transfer-13"]["label"] == "转向移载机-13"
    assert nodes["lift_transfer-09"]["label"] == "升降移载机-09"
    encoded = json.dumps(merged, ensure_ascii=False).encode("utf-8")
    assert "转向移载机-15".encode("utf-8") in encoded

