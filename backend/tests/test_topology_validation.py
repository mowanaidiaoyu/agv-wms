from __future__ import annotations

import copy
import json
from pathlib import Path

from app.services.simulation import validate_runtime_inputs
from app.services.topology_validation import validate_topology_business_rules


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def topology_666() -> dict:
    return json.loads((PROJECT_ROOT / "docs" / "topologies" / "666.json").read_text(encoding="utf-8"))


def category(result: dict, name: str) -> dict:
    return next(item for item in result["categories"] if item["category"] == name)


def test_real_666_validation_separates_storage_access_and_runtime_readiness():
    topology = topology_666()
    result = validate_topology_business_rules(topology, access_mappings=[])

    assert len([node for node in topology["nodes"] if node["type"] == "storage"]) == 2496
    assert result["errors"] == []
    assert category(result, "structure")["error_count"] == 0
    assert category(result, "accessibility")["warning_count"] == 2496
    assert {issue["code"] for issue in category(result, "accessibility")["issues"]} == {"ACCESS_UNCONFIGURED"}
    runtime_codes = {issue["code"] for issue in category(result, "runtime")["issues"]}
    assert "STATION_MISSING" in runtime_codes
    assert "CONVEYOR_CELLS_UNBOUND" in runtime_codes


def test_storage_node_does_not_require_a_cell_at_the_same_position():
    topology = topology_666()
    storage = next(node for node in topology["nodes"] if node["type"] == "storage")
    assert not any(
        cell["row"] == storage["row"]
        and cell["col"] == storage["col"]
        and (cell.get("floor") or "1F") == (storage.get("floor") or "1F")
        for cell in topology["cells"]
    )
    result = validate_topology_business_rules(topology)
    assert not any(issue["entity_id"] == storage["id"] for issue in category(result, "structure")["issues"])


def test_storage_structure_still_rejects_out_of_bounds_and_missing_zone():
    topology = topology_666()
    invalid = copy.deepcopy(topology)
    invalid["nodes"][0]["row"] = invalid["grid"]["rows"]
    invalid["nodes"][0]["zoneId"] = "missing-zone"

    result = validate_topology_business_rules(invalid)
    issue_codes = {issue["code"] for issue in category(result, "structure")["issues"]}
    assert "NODE_OUT_OF_BOUNDS" in issue_codes
    assert "NODE_ZONE_MISSING" in issue_codes
    assert not result["valid"]


def test_operational_node_without_underlying_cell_remains_invalid():
    topology = topology_666()
    topology["nodes"].append({
        "id": "station-invalid", "type": "station", "floor": "1F",
        "row": 0, "col": 0, "zoneId": None, "capacity": 1, "label": "Invalid station",
    })
    topology["cells"] = [cell for cell in topology["cells"] if not (cell["row"] == 0 and cell["col"] == 0)]

    result = validate_topology_business_rules(topology)
    assert any(issue["code"] == "OPERATIONAL_NODE_CELL_MISSING" for issue in category(result, "structure")["issues"])


def test_missing_resource_references_remain_structural_errors():
    topology = topology_666()
    topology["resources"].append({
        "id": "resource-invalid-reference", "type": "station",
        "cellIds": ["missing-cell"], "nodeIds": ["missing-node"], "metadata": {},
    })

    result = validate_topology_business_rules(topology)
    issue_codes = {issue["code"] for issue in category(result, "structure")["issues"]}
    assert "RESOURCE_CELL_REFERENCE_MISSING" in issue_codes
    assert "RESOURCE_NODE_REFERENCE_MISSING" in issue_codes
    assert not result["valid"]


def test_formal_runtime_keeps_station_requirement_but_path_and_inventory_validation_do_not():
    topology = topology_666()
    scenario = {"id": "scenario-no-station", "topologyId": topology["id"], "robots": []}
    runtime_errors = validate_runtime_inputs(topology, scenario)
    topology_result = validate_topology_business_rules(topology)

    assert any("no station node" in message for message in runtime_errors)
    assert not any("站台" in message for message in topology_result["errors"])
