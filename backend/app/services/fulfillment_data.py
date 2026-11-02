from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..core.config import DATA_DIR
from .order_fulfillment import plan_order_fulfillment, plan_orders_fulfillment
from .right_handoff import audit_right_handoff
from .pathfinding import node_to_cell_id, port_entry_cell, shortest_path
from .shuttle_topology import build_shuttle_track_graph, topology_revision
from .unified_transfer import plan_transfer


PACKAGING_SPECS_PATH = DATA_DIR / "sku_packaging_specs.json"
SCENARIO_ROOT = DATA_DIR / "simulation_scenarios"
SIMULATION_PROFILE_ROOT = DATA_DIR / "simulation_inventory_profiles"
TEST_PACKAGING_STATUSES = frozenset({"simulation_test_confirmed"})
TEST_INVENTORY_SOURCES = frozenset({"simulation_test"})
SIMULATION_PACKAGING_STATUSES = frozenset({"simulation_configured"})
SIMULATION_INVENTORY_SOURCES = frozenset({"simulation"})


class FulfillmentDataValidationError(ValueError):
    pass


def load_packaging_registry(path: Path = PACKAGING_SPECS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("records"), list):
        raise FulfillmentDataValidationError("Packaging registry records must be a list")
    return payload


def catalog_with_packaging(
    catalog: dict[str, Any],
    *,
    scopes: frozenset[str] = frozenset({"formal"}),
    scenario_id: str | None = None,
    profile_id: str | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Overlay traceable packaging records without editing sku_catalog.json."""
    result = deepcopy(catalog)
    by_sku = {
        str(item.get("skuId")): item
        for item in result.get("skus", [])
        if isinstance(item, dict) and item.get("skuId")
    }
    registry = registry or load_packaging_registry()
    for record in registry["records"]:
        if record.get("scope") not in scopes:
            continue
        if scenario_id is not None and record.get("scenarioId") != scenario_id:
            continue
        if profile_id is not None and record.get("profileId") != profile_id:
            continue
        sku = by_sku.get(str(record.get("skuId")))
        if sku is None:
            raise FulfillmentDataValidationError(
                f"Packaging record references unknown catalog SKU {record.get('skuId')}"
            )
        sku.update({
            "unitsPerCase": record.get("unitsPerCase"),
            "packagingSource": record.get("packagingSource"),
            "verificationStatus": record.get("verificationStatus"),
            "packagingScope": record.get("scope"),
            "dataScope": record.get("dataScope") or record.get("scope"),
            "baseUom": record.get("baseUom") or "piece",
            "caseUom": record.get("caseUom") or "case",
        })
    return result


def load_simulation_test_scenario(scenario_id: str) -> dict[str, Any]:
    if not scenario_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in scenario_id):
        raise FulfillmentDataValidationError("Invalid scenario id")
    path = SCENARIO_ROOT / f"{scenario_id}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("scenarioId") != scenario_id:
        raise FulfillmentDataValidationError("Scenario id does not match its file name")
    return payload


def load_simulation_inventory_profile(profile_id: str) -> dict[str, Any]:
    if not profile_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in profile_id):
        raise FulfillmentDataValidationError("Invalid simulation inventory profile id")
    path = SIMULATION_PROFILE_ROOT / f"{profile_id}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("profileId") != profile_id:
        raise FulfillmentDataValidationError("Simulation inventory profile id does not match its file name")
    return payload


def _apply_inventory_overrides(
    inventory: list[dict[str, Any]],
    overrides: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    result = deepcopy(inventory)
    by_id = {str(item.get("inventoryRecordId")): item for item in result}
    seen: set[str] = set()
    for override in overrides or []:
        record_id = str(override.get("inventoryRecordId") or "")
        if record_id not in by_id:
            raise FulfillmentDataValidationError(
                f"Inventory override references an unsupported record: {record_id or 'missing'}"
            )
        if record_id in seen:
            raise FulfillmentDataValidationError(f"Duplicate inventory override: {record_id}")
        quantity = override.get("quantityCases")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            raise FulfillmentDataValidationError("Simulation quantityCases must be a non-negative integer")
        by_id[record_id]["quantityCases"] = quantity
        seen.add(record_id)
    return result


def _cell_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(cell["id"]): cell for cell in topology.get("cells", [])}


def _validate_right_location(
    topology: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    if record.get("locationType") != "shuttle_storage":
        raise FulfillmentDataValidationError("Right inventory must map to shuttle_storage")
    mapping = record.get("topologyMapping") or {}
    track_node_id = str(mapping.get("trackNodeId") or "")
    graph = build_shuttle_track_graph(topology)
    track = graph.get(track_node_id)
    if track is None:
        raise FulfillmentDataValidationError(
            f"Right inventory track node is missing or not traversable: {track_node_id}"
        )
    expected = (int(mapping["row"]), int(mapping["col"]), int(mapping["level"]))
    actual = (int(track["row"]), int(track["col"]), int(track["shuttle_level"]))
    if actual != expected:
        raise FulfillmentDataValidationError("Right inventory track coordinates do not match ShuttleGraph")
    expected_cell_id = f"cell-r{expected[0]}-c{expected[1]}"
    if mapping.get("cellId") != expected_cell_id:
        raise FulfillmentDataValidationError("Right inventory cell id does not match its coordinates")
    cell = _cell_lookup(topology).get(expected_cell_id)
    # Sparse topology payloads omit ordinary road cells. An absent explicit
    # record is therefore the grid's default walkable road; an explicit cell
    # must still be road and walkable.
    if cell is not None and (cell.get("type") != "road" or not cell.get("walkable", True)):
        raise FulfillmentDataValidationError("Right inventory location is not backed by a walkable road cell")
    exit_track = str(mapping.get("pickupExitTrackNodeId") or "")
    route = plan_transfer(topology, track_node_id, exit_track, "astar")
    if route["result_status"] != "success":
        raise FulfillmentDataValidationError(
            f"Right inventory is not shuttle-reachable: {route.get('failure_reason')}"
        )
    return {
        "trackNodeId": track_node_id,
        "pickupExitTrackNodeId": exit_track,
        "pathNodeCount": len(route["path_nodes"]),
        "chosenLift": route.get("chosen_lift"),
    }


def _validate_left_location(
    topology: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    if record.get("locationType") != "ctu_storage":
        raise FulfillmentDataValidationError("Left inventory must map to ctu_storage")
    mapping = record.get("topologyMapping") or {}
    node_id = str(mapping.get("topologyNodeId") or "")
    node = next((item for item in topology.get("nodes", []) if item.get("id") == node_id), None)
    if node is None or node.get("type") != "storage":
        raise FulfillmentDataValidationError(f"Left inventory storage node is invalid: {node_id}")
    if node_to_cell_id(topology, node_id) != mapping.get("cellId"):
        raise FulfillmentDataValidationError("Left inventory cell does not match the topology storage node")
    layer = int(mapping.get("layer") or 0)
    if layer < 1 or layer > int(node.get("capacity") or 0):
        raise FulfillmentDataValidationError("Left inventory layer exceeds storage capacity")
    reachable_ports: list[str] = []
    for zone in topology.get("zones", []):
        if zone.get("type") != "port":
            continue
        target = port_entry_cell(topology, zone)
        route = shortest_path(
            topology,
            str(mapping["cellId"]),
            target,
            "astar",
            avoid_storage=True,
        )
        if route:
            reachable_ports.append(str(zone["id"]))
    if not reachable_ports:
        raise FulfillmentDataValidationError("Left inventory storage has no legal CTU route to a port")
    return {"topologyNodeId": node_id, "layer": layer, "reachablePortIds": reachable_ports}


def validate_simulation_test_scenario(
    topology: dict[str, Any],
    catalog: dict[str, Any],
    scenario_id: str,
) -> dict[str, Any]:
    scenario = load_simulation_test_scenario(scenario_id)
    if scenario.get("dataSource") != "simulation_test":
        raise FulfillmentDataValidationError("Scenario inventory must be marked simulation_test")
    if scenario.get("isolationMode") != "run_local_snapshot":
        raise FulfillmentDataValidationError("Scenario must use run_local_snapshot isolation")
    if scenario.get("topologyId") != topology.get("id"):
        raise FulfillmentDataValidationError("Scenario topology id mismatch")
    actual_revision = topology_revision(topology)
    if scenario.get("topologyRevision") != actual_revision:
        raise FulfillmentDataValidationError(
            f"Scenario expects {scenario.get('topologyRevision')}; current topology is {actual_revision}"
        )

    scenario_catalog = catalog_with_packaging(
        catalog,
        scopes=frozenset({"simulation_test"}),
        scenario_id=scenario_id,
    )
    sku = next(
        (item for item in scenario_catalog.get("skus", []) if item.get("skuId") == scenario.get("skuId")),
        None,
    )
    if sku is None or sku.get("verificationStatus") != "simulation_test_confirmed":
        raise FulfillmentDataValidationError("Scenario SKU packaging is not simulation-test confirmed")
    if int(sku.get("unitsPerCase") or 0) != int((scenario.get("packaging") or {}).get("unitsPerCase") or 0):
        raise FulfillmentDataValidationError("Scenario packaging does not match the packaging registry")

    inventory = deepcopy(scenario.get("inventory") or [])
    if len({item.get("inventoryRecordId") for item in inventory}) != len(inventory):
        raise FulfillmentDataValidationError("Scenario inventoryRecordId values must be unique")
    if any(item.get("dataSource") != "simulation_test" for item in inventory):
        raise FulfillmentDataValidationError("Every scenario inventory record must be simulation_test")
    right = [item for item in inventory if item.get("sourceWarehouse") == "right"]
    left = [item for item in inventory if item.get("sourceWarehouse") == "left"]
    if len(right) != 1 or len(left) != 1:
        raise FulfillmentDataValidationError("110-case scenario requires one right and one left inventory record")
    right_evidence = _validate_right_location(topology, right[0])
    left_evidence = _validate_left_location(topology, left[0])

    plan = plan_order_fulfillment(
        scenario["order"],
        scenario_catalog,
        inventory,
        allowed_packaging_statuses=TEST_PACKAGING_STATUSES,
        allowed_inventory_sources=TEST_INVENTORY_SOURCES,
    )
    expected = scenario.get("expectedAllocation") or {}
    allocation = plan["allocations"][0]
    mismatches = {
        key: {"expected": value, "actual": allocation.get(key)}
        for key, value in expected.items()
        if allocation.get(key) != value
    }
    if mismatches:
        raise FulfillmentDataValidationError(f"Scenario allocation mismatch: {mismatches}")
    guards = scenario.get("businessGuards") or {}
    if any(guards.get(key) is not False for key in (
        "mutateFormalInventory", "mutateTopology", "mutateOtherOrders",
        "previewCargoAccepted", "canDeclareCompleted", "repeatRunsShareInventory",
    )):
        raise FulfillmentDataValidationError("Scenario business isolation guards are incomplete")
    handoff = audit_right_handoff(topology, scenario, plan)
    return {
        "scenarioId": scenario_id,
        "status": "PASS",
        "dataSource": "simulation_test",
        "isolationMode": "run_local_snapshot",
        "topologyId": topology["id"],
        "topologyRevision": actual_revision,
        "sku": {
            "skuId": sku["skuId"],
            "unitsPerCase": sku["unitsPerCase"],
            "packagingSource": sku["packagingSource"],
            "verificationStatus": sku["verificationStatus"],
            "scope": sku["packagingScope"],
        },
        "inventory": inventory,
        "locationEvidence": {"right": right_evidence, "left": left_evidence},
        "plan": plan,
        "order": deepcopy(scenario["order"]),
        "rightHandoff": handoff,
        "businessGuards": guards,
        "knownPhysicalGaps": scenario.get("knownPhysicalGaps") or [],
    }


def validate_general_simulation_orders(
    topology: dict[str, Any],
    catalog: dict[str, Any],
    profile_id: str,
    orders: list[dict[str, Any]],
    inventory_overrides: list[dict[str, Any]] | None = None,
    *,
    require_ready: bool = False,
) -> dict[str, Any]:
    """Validate a user-authored simulation order against an isolated profile.

    The profile supplies only explicit simulation packaging and location mappings.
    The request may change case counts, but cannot change a SKU, location, mapping,
    source type, or authority flag.  No database or formal inventory is mutated.
    """
    profile = load_simulation_inventory_profile(profile_id)
    if profile.get("dataSource") != "simulation":
        raise FulfillmentDataValidationError("General profile dataSource must be simulation")
    if profile.get("isolationMode") != "run_local_snapshot":
        raise FulfillmentDataValidationError("General profile must use run_local_snapshot isolation")
    if profile.get("topologyId") != topology.get("id"):
        raise FulfillmentDataValidationError("General profile topology id mismatch")
    actual_revision = topology_revision(topology)
    if profile.get("topologyRevision") != actual_revision:
        raise FulfillmentDataValidationError(
            f"Profile expects {profile.get('topologyRevision')}; current topology is {actual_revision}"
        )
    if not orders:
        raise FulfillmentDataValidationError("At least one simulation order is required")
    order_ids = [str(order.get("id") or "") for order in orders]
    if not all(order_ids) or len(set(order_ids)) != len(order_ids):
        raise FulfillmentDataValidationError("Simulation order IDs must be non-empty and unique")
    if any(not order.get("lines") for order in orders):
        raise FulfillmentDataValidationError("Every simulation order needs at least one line")
    if any(line.get("quantityBase") is None for order in orders for line in order.get("lines", [])):
        raise FulfillmentDataValidationError("General simulation accepts quantityBase order lines only")

    simulation_catalog = catalog_with_packaging(
        catalog,
        scopes=frozenset({"simulation"}),
        profile_id=profile_id,
    )
    inventory = _apply_inventory_overrides(
        deepcopy(profile.get("inventory") or []),
        inventory_overrides,
    )
    if not inventory or len({item.get("inventoryRecordId") for item in inventory}) != len(inventory):
        raise FulfillmentDataValidationError("Simulation inventory records must be present and unique")
    if any(item.get("dataSource") != "simulation" for item in inventory):
        raise FulfillmentDataValidationError("General inventory may contain simulation records only")

    location_evidence: dict[str, dict[str, Any]] = {}
    for record in inventory:
        record_id = str(record["inventoryRecordId"])
        if record.get("sourceWarehouse") == "right":
            location_evidence[record_id] = _validate_right_location(topology, record)
        elif record.get("sourceWarehouse") == "left":
            location_evidence[record_id] = _validate_left_location(topology, record)
        else:
            raise FulfillmentDataValidationError(f"Unknown warehouse side for {record_id}")

    plans = plan_orders_fulfillment(
        orders,
        simulation_catalog,
        inventory,
        allowed_packaging_statuses=SIMULATION_PACKAGING_STATUSES,
        allowed_inventory_sources=SIMULATION_INVENTORY_SOURCES,
    )
    allocation_status = "ready" if all(plan["allocationStatus"] == "ready" for plan in plans) else "shortage"
    if require_ready and allocation_status != "ready":
        shortages = [
            {
                "orderId": plan["orderId"],
                "lines": [
                    {"skuId": item["skuId"], "shortageQuantityBase": item["shortageQuantityBase"]}
                    for item in plan["allocations"] if item["allocationStatus"] == "shortage"
                ],
            }
            for plan in plans if plan["allocationStatus"] == "shortage"
        ]
        raise FulfillmentDataValidationError(f"Simulation inventory shortage: {shortages}")

    combined_plan = {
        "orderId": "multiple" if len(orders) > 1 else orders[0]["id"],
        "allocationStatus": allocation_status,
        "allocations": [item for plan in plans for item in plan["allocations"]],
        "tasks": [item for plan in plans for item in plan["tasks"]],
        "inventoryMutated": False,
        "inventoryReserved": False,
        "previewCargoAccepted": False,
        "canDeclareCompleted": False,
        "requestFingerprint": ":".join(plan["requestFingerprint"] for plan in plans),
    }
    template_id = str(profile.get("rightHandoffTemplateScenarioId") or "")
    template = load_simulation_test_scenario(template_id)
    scenario = {
        "scenarioId": profile_id,
        "dataSource": "simulation",
        "inventory": inventory,
        "rightHandoff": deepcopy(template.get("rightHandoff") or {}),
    }
    handoff = audit_right_handoff(topology, scenario, combined_plan)
    supported_skus = []
    sku_by_id = {
        str(item.get("skuId")): item
        for item in simulation_catalog.get("skus", [])
        if item.get("verificationStatus") == "simulation_configured"
    }
    for sku_id in sorted({str(line["skuId"]) for order in orders for line in order["lines"]}):
        sku = sku_by_id.get(sku_id)
        if sku is None:
            raise FulfillmentDataValidationError(
                f"SKU {sku_id} has no explicit packaging configuration in profile {profile_id}"
            )
        supported_skus.append({
            "skuId": sku_id,
            "unitsPerCase": sku["unitsPerCase"],
            "baseUom": sku.get("baseUom", "piece"),
            "caseUom": sku.get("caseUom", "case"),
            "packagingSource": sku["packagingSource"],
            "verificationStatus": sku["verificationStatus"],
            "dataScope": sku.get("dataScope", "simulation"),
        })
    guards = deepcopy(profile.get("businessGuards") or {})
    if any(guards.get(key) is not False for key in (
        "mutateFormalInventory", "mutateTopology", "mutateOtherOrders",
        "previewCargoAccepted", "canDeclareCompleted", "repeatRunsShareInventory",
    )):
        raise FulfillmentDataValidationError("General simulation business isolation guards are incomplete")
    return {
        "profileId": profile_id,
        "scenarioId": profile_id,
        "status": "PASS" if allocation_status == "ready" else "SHORTAGE",
        "allocationStatus": allocation_status,
        "runMode": "simulation",
        "dataSource": "simulation",
        "isolationMode": "run_local_snapshot",
        "topologyId": topology["id"],
        "topologyRevision": actual_revision,
        "supportedSkus": supported_skus,
        "inventory": inventory,
        "locationEvidence": location_evidence,
        "orders": deepcopy(orders),
        "plans": plans,
        "order": deepcopy(orders[0]),
        "plan": combined_plan,
        "rightHandoff": handoff,
        "businessGuards": guards,
        "knownPhysicalGaps": template.get("knownPhysicalGaps") or [],
        "formalInventoryMutated": False,
    }
