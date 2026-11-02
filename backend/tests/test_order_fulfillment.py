from __future__ import annotations

from copy import deepcopy
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import routes
from app.schemas import TopologyPayload
from app.services import simulation
from app.services.fulfillment_data import (
    FulfillmentDataValidationError,
    catalog_with_packaging,
    load_packaging_registry,
    validate_general_simulation_orders,
    validate_simulation_test_scenario,
)
from app.services.goods import load_catalog
from app.services.order_fulfillment import (
    PackagingSpecificationError,
    plan_order_fulfillment,
    plan_orders_fulfillment,
    topology_inventory_balances,
)
from app.services.simulation import RunRuntime, SimulationService
from app.services.shuttle_topology import build_shuttle_track_graph


TOPOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "topology_666.json"


CATALOG = {
    "version": 1,
    "skus": [
        {"skuId": "SKU-A", "name": "A", "unitsPerCase": 20, "packagingSource": "verified-test-spec", "verificationStatus": "verified"},
        {"skuId": "SKU-B", "name": "B", "unitsPerCase": 12, "packagingSource": "verified-test-spec", "verificationStatus": "verified"},
        {"skuId": "SKU-NO-PACK", "name": "No pack"},
    ],
}

BALANCES = [
    {"sourceWarehouse": "right", "sourceLocationId": "right-a", "skuId": "SKU-A", "quantityCases": 20, "dataSource": "formal_inventory", "verificationStatus": "verified"},
    {"sourceWarehouse": "left", "sourceLocationId": "left-a", "skuId": "SKU-A", "quantityCases": 10, "dataSource": "formal_inventory", "verificationStatus": "verified"},
    {"sourceWarehouse": "right", "sourceLocationId": "right-b", "skuId": "SKU-B", "quantityCases": 20, "dataSource": "formal_inventory", "verificationStatus": "verified"},
    {"sourceWarehouse": "left", "sourceLocationId": "left-b", "skuId": "SKU-B", "quantityCases": 10, "dataSource": "formal_inventory", "verificationStatus": "verified"},
]


def order(quantity: int, sku_id: str = "SKU-A", order_id: str = "ORD-1") -> dict:
    return {
        "id": order_id,
        "priority": "normal",
        "dispatchMode": "balanced",
        "maxParallelCtu": 7,
        "targetStation": "出库口 01",
        "createdAt": "2026-10-29T00:00:00Z",
        "lines": [{
            "id": "line-1",
            "skuId": sku_id,
            "skuName": sku_id,
            "quantityBase": quantity,
        }],
    }


def allocation(quantity: int, sku_id: str = "SKU-A") -> dict:
    return plan_order_fulfillment(order(quantity, sku_id), CATALOG, BALANCES)["allocations"][0]


@pytest.mark.parametrize(
    ("quantity", "right_cases", "right_base", "left_pick", "left_return"),
    [
        (110, 5, 100, 10, 10),
        (20, 1, 20, 0, 0),
        (10, 0, 0, 10, 10),
        (40, 2, 40, 0, 0),
        (41, 2, 40, 1, 19),
    ],
)
def test_general_case_and_break_pack_rules(quantity, right_cases, right_base, left_pick, left_return) -> None:
    item = allocation(quantity)
    assert item["rightQuantityCases"] == right_cases
    assert item["rightQuantityBase"] == right_base
    assert item["leftPickQuantityBase"] == left_pick
    assert item["leftReturnQuantityBase"] == left_return
    assert item["rightQuantityBase"] + item["leftPickQuantityBase"] == quantity
    assert item["leftSourceQuantityBase"] == item["leftPickQuantityBase"] + item["leftReturnQuantityBase"]


def test_different_units_per_case_are_loaded_from_catalog() -> None:
    item = allocation(25, "SKU-B")
    assert item["unitsPerCase"] == 12
    assert item["rightQuantityCases"] == 2
    assert item["rightQuantityBase"] == 24
    assert item["leftPickQuantityBase"] == 1
    assert item["leftReturnQuantityBase"] == 11


def test_right_and_left_inventory_shortages_are_explicit_and_create_no_tasks() -> None:
    right_short = plan_order_fulfillment(
        order(110),
        CATALOG,
        [{**BALANCES[0], "quantityCases": 4}, BALANCES[1]],
    )
    left_short = plan_order_fulfillment(order(10), CATALOG, [BALANCES[0]])
    assert right_short["allocationStatus"] == "shortage"
    assert right_short["allocations"][0]["shortageQuantityBase"] == 20
    assert right_short["tasks"] == []
    assert left_short["allocationStatus"] == "shortage"
    assert left_short["allocations"][0]["shortageQuantityBase"] == 10
    assert left_short["tasks"] == []


def test_invalid_or_unverified_packaging_is_rejected() -> None:
    with pytest.raises(PackagingSpecificationError, match="no valid unitsPerCase"):
        plan_order_fulfillment(order(10, "SKU-NO-PACK"), CATALOG, BALANCES)
    bad_catalog = {"skus": [{"skuId": "SKU-A", "name": "A", "unitsPerCase": 20}]}
    with pytest.raises(PackagingSpecificationError, match="packagingSource"):
        plan_order_fulfillment(order(10), bad_catalog, BALANCES)


def test_client_cannot_override_catalog_packaging() -> None:
    request = order(10)
    request["lines"][0]["unitsPerCase"] = 10
    with pytest.raises(PackagingSpecificationError, match="does not match"):
        plan_order_fulfillment(request, CATALOG, BALANCES)


def test_planning_is_deterministic_idempotent_and_does_not_mutate_inventory() -> None:
    before = deepcopy(BALANCES)
    first = plan_order_fulfillment(order(110), CATALOG, BALANCES)
    second = plan_order_fulfillment(order(110), CATALOG, BALANCES)
    assert first == second
    assert first["requestFingerprint"] == second["requestFingerprint"]
    assert BALANCES == before


def test_several_orders_and_skus_cannot_consume_each_others_stock() -> None:
    balances = [
        {**BALANCES[0], "quantityCases": 1},
        {**BALANCES[2], "quantityCases": 1},
    ]
    plans = plan_orders_fulfillment(
        [order(20, "SKU-A", "ORD-A1"), order(20, "SKU-A", "ORD-A2"), order(12, "SKU-B", "ORD-B1")],
        CATALOG,
        balances,
    )
    assert [plan["allocationStatus"] for plan in plans] == ["ready", "shortage", "ready"]
    assert plans[2]["tasks"][0]["skuId"] == "SKU-B"


def test_prepared_task_contract_contains_order_cargo_sku_and_quantities() -> None:
    plan = plan_order_fulfillment(order(110), CATALOG, BALANCES)
    right = plan["allocations"][0]["rightTasks"]
    left = plan["allocations"][0]["leftTasks"]
    assert len(right) == 5
    assert len({task["cargoId"] for task in right}) == 5
    assert all(task["orderId"] == "ORD-1" and task["quantityBase"] == 20 for task in right)
    assert len(left) == 1
    assert left[0]["sourceQuantityBase"] == 20
    assert left[0]["pickQuantityBase"] == 10
    assert left[0]["returnQuantityBase"] == 10


def test_preview_and_authoritative_business_flow_are_explicitly_isolated() -> None:
    plan = plan_order_fulfillment(order(110), CATALOG, BALANCES)
    assert plan["inventoryMutated"] is False
    assert plan["inventoryReserved"] is False
    assert plan["previewCargoAccepted"] is False
    assert plan["canDeclareCompleted"] is False
    assert all(task["authoritative"] is False for task in plan["tasks"])


def test_topology_inventory_rejects_docks_capacity_and_unlinked_storage() -> None:
    topology = {
        "nodes": [
            {"id": "right", "type": "shuttle_dock", "skuId": "SKU-A", "quantity": 3},
            {"id": "left", "type": "storage", "skuId": "SKU-A", "quantity": 2},
            {"id": "generated-looking", "type": "storage", "capacity": 17},
            {
                "id": "formal-left", "type": "storage", "skuId": "SKU-A", "quantity": 2,
                "sourceWarehouse": "left", "inventorySource": "formal_inventory",
                "inventoryRecordId": "inventory-left-a",
            },
        ]
    }
    assert topology_inventory_balances(topology) == [
        {
            "sourceWarehouse": "left", "sourceLocationId": "formal-left", "skuId": "SKU-A",
            "quantityCases": 2, "dataSource": "formal_inventory",
            "inventoryRecordId": "inventory-left-a", "verificationStatus": "verified",
        },
    ]


def test_prepared_order_never_becomes_completed_without_authoritative_events(monkeypatch) -> None:
    monkeypatch.setattr(simulation, "load_catalog", lambda: CATALOG)
    service = SimulationService(object())
    states, tasks = service._prepare_base_quantity_orders({}, [order(110)], BALANCES)
    assert states[0]["status"] == "waiting_dispatch"
    assert states[0]["decomposition"]["stage"] == "tasks_prepared"
    assert states[0]["completedTasks"] == 0
    assert states[0]["fulfillmentPlan"]["canDeclareCompleted"] is False
    assert len(tasks) == 6


def test_planning_api_returns_the_explicit_contract(monkeypatch) -> None:
    topology = {
        "id": "topology-f2",
        "nodes": [
            {"id": "right-a", "type": "storage", "skuId": "SKU-A", "quantity": 5, "sourceWarehouse": "right", "inventorySource": "formal_inventory", "inventoryRecordId": "right-inventory"},
            {"id": "left-a", "type": "storage", "skuId": "SKU-A", "quantity": 1, "sourceWarehouse": "left", "inventorySource": "formal_inventory", "inventoryRecordId": "left-inventory"},
        ],
    }
    monkeypatch.setattr(routes.repo, "get_topology", lambda topology_id: {
        "id": topology_id,
        "payload": topology,
    })
    monkeypatch.setattr(routes, "load_catalog", lambda: CATALOG)
    response = TestClient(app).post(
        "/api/v1/topologies/topology-f2/order-fulfillment-plan",
        json=order(110),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["allocationStatus"] == "ready"
    assert payload["allocations"][0]["rightQuantityBase"] == 100
    assert payload["allocations"][0]["leftPickQuantityBase"] == 10
    assert payload["canDeclareCompleted"] is False


class _MemoryRepository:
    def __init__(self) -> None:
        self.events: list[tuple[str, int, str, dict]] = []
        self.states: list[dict] = []

    def add_event(self, run_id, tick, event_type, payload) -> None:
        self.events.append((run_id, tick, event_type, deepcopy(payload)))

    def update_run(self, _run_id, _status, payload, _metrics) -> dict:
        self.states.append(deepcopy(payload))
        return {}


def test_runtime_duplicate_request_is_idempotent_and_cancel_has_no_stock_side_effect(monkeypatch) -> None:
    monkeypatch.setattr(simulation, "load_catalog", lambda: CATALOG)
    repository = _MemoryRepository()
    service = SimulationService(repository)  # type: ignore[arg-type]
    topology = {
        "id": "topology-f2",
        "nodes": [
            {"id": "right-a", "type": "storage", "skuId": "SKU-A", "quantity": 5, "sourceWarehouse": "right", "inventorySource": "formal_inventory", "inventoryRecordId": "right-inventory"},
            {"id": "left-a", "type": "storage", "skuId": "SKU-A", "quantity": 1, "sourceWarehouse": "left", "inventorySource": "formal_inventory", "inventoryRecordId": "left-inventory"},
        ],
    }
    initial_inventory = [{"nodeId": "unrelated", "skuId": "SKU-Z", "qty": 9}]
    runtime = RunRuntime(
        run_id="run-f2",
        topology=topology,
        config={},
        state={
            "id": "run-f2", "status": "paused", "tick": 0, "topologyId": topology["id"],
            "orders": [], "tasks": [], "fulfillmentTasks": [], "inventory": deepcopy(initial_inventory),
            "robots": [], "alerts": [], "queues": {"conveyor": [], "station": [], "buffer": []},
        },
        metrics={},
        paused=True,
    )
    service.runtimes[runtime.run_id] = runtime
    monkeypatch.setattr(service, "_refresh_metrics", lambda _runtime: None)

    async def scenario() -> None:
        first = await service.add_order(runtime.run_id, order(110))
        second = await service.add_order(runtime.run_id, order(110))
        assert first is second
        assert len(first["orders"]) == 1
        assert len(first["fulfillmentTasks"]) == 6
        assert first["inventory"] == initial_inventory
        cancelled = await service.cancel_order(runtime.run_id, "ORD-1", "test cancellation")
        assert cancelled["orders"][0]["status"] == "cancelled"
        assert all(task["status"] == "cancelled" for task in cancelled["fulfillmentTasks"])
        assert cancelled["inventory"] == initial_inventory

    asyncio.run(scenario())


def test_packaging_registry_keeps_simulation_test_spec_out_of_formal_catalog() -> None:
    catalog = load_catalog()
    registry = load_packaging_registry()
    formal = catalog_with_packaging(catalog, registry=registry)
    test_catalog = catalog_with_packaging(
        catalog,
        scopes=frozenset({"simulation_test"}),
        scenario_id="right-left-110-case-test",
        registry=registry,
    )
    sku_id = "13448419-4.0-4.0"
    formal_sku = next(item for item in formal["skus"] if item["skuId"] == sku_id)
    test_sku = next(item for item in test_catalog["skus"] if item["skuId"] == sku_id)
    assert "unitsPerCase" not in formal_sku
    assert test_sku["unitsPerCase"] == 20
    assert test_sku["verificationStatus"] == "simulation_test_confirmed"
    assert test_sku["packagingScope"] == "simulation_test"


def test_real_110_scenario_uses_valid_shuttle_and_ctu_locations() -> None:
    topology = TopologyPayload.model_validate(
        json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    ).model_dump(by_alias=True)
    result = validate_simulation_test_scenario(
        topology,
        load_catalog(),
        "right-left-110-case-test",
    )
    assert result["status"] == "PASS"
    assert result["dataSource"] == "simulation_test"
    assert result["locationEvidence"]["right"] == {
        "trackNodeId": "L2-r96-c31",
        "pickupExitTrackNodeId": "L1-r96-c21",
        "pathNodeCount": 11,
        "chosenLift": "shuttle-lift-a",
    }
    assert set(result["locationEvidence"]["left"]["reachablePortIds"]) == {
        "zone-port", "zone-port-2", "zone-port-3", "zone-port-4",
    }
    allocation = result["plan"]["allocations"][0]
    assert allocation["rightQuantityBase"] == 100
    assert allocation["leftPickQuantityBase"] == 10
    assert allocation["leftReturnQuantityBase"] == 10
    assert len(result["plan"]["tasks"]) == 6
    assert result["plan"]["inventoryMutated"] is False
    assert result["plan"]["canDeclareCompleted"] is False


def test_suggested_l2_r96_c29_is_not_a_valid_shuttle_location() -> None:
    topology = TopologyPayload.model_validate(
        json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    ).model_dump(by_alias=True)
    graph = build_shuttle_track_graph(topology)
    assert "L1-r96-c29" in graph
    assert "L2-r96-c29" not in graph


def test_scenario_endpoint_is_read_only_and_repeatable(monkeypatch) -> None:
    topology = TopologyPayload.model_validate(
        json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    ).model_dump(by_alias=True)
    before = deepcopy(topology)
    monkeypatch.setattr(routes.repo, "get_topology", lambda topology_id: {
        "id": topology_id,
        "payload": topology,
    })
    client = TestClient(app)
    first = client.get(
        "/api/v1/topologies/topology-6ceb4920/fulfillment-test-scenarios/right-left-110-case-test"
    )
    second = client.get(
        "/api/v1/topologies/topology-6ceb4920/fulfillment-test-scenarios/right-left-110-case-test"
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert topology == before
    assert first.json()["businessGuards"]["repeatRunsShareInventory"] is False


def _general_order(order_id: str, sku_id: str, quantity_base: int, units_per_case: int) -> dict:
    return {
        "id": order_id,
        "priority": "normal",
        "dispatchMode": "balanced",
        "maxParallelCtu": 7,
        "targetStation": "simulation-merge",
        "createdAt": "2026-10-31T00:00:00+08:00",
        "lines": [{
            "id": f"line-{order_id}",
            "skuId": sku_id,
            "skuName": sku_id,
            "quantityBase": quantity_base,
            "unitsPerCase": units_per_case,
        }],
    }


def test_general_simulation_plans_dynamic_quantities_and_two_pack_sizes() -> None:
    topology = TopologyPayload.model_validate(
        json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    ).model_dump(by_alias=True)
    result = validate_general_simulation_orders(
        topology,
        load_catalog(),
        "general-order-simulation",
        [
            _general_order("ORD-40", "13448419-4.0-4.0", 40, 20),
            _general_order("ORD-35", "13448419-4.0-4.0", 35, 20),
            _general_order("ORD-25-U12", "13495233-15.0-15.0", 25, 12),
        ],
        require_ready=True,
    )
    assert result["status"] == "PASS"
    assert result["formalInventoryMutated"] is False
    allocations = {
        (item["orderId"], item["skuId"]): item
        for item in result["plan"]["allocations"]
    }
    assert allocations[("ORD-40", "13448419-4.0-4.0")]["rightQuantityBase"] == 40
    assert allocations[("ORD-40", "13448419-4.0-4.0")]["leftPickQuantityBase"] == 0
    assert allocations[("ORD-35", "13448419-4.0-4.0")]["rightQuantityBase"] == 20
    assert allocations[("ORD-35", "13448419-4.0-4.0")]["leftPickQuantityBase"] == 15
    assert allocations[("ORD-25-U12", "13495233-15.0-15.0")]["rightQuantityBase"] == 24
    assert allocations[("ORD-25-U12", "13495233-15.0-15.0")]["leftPickQuantityBase"] == 1
    assert len({task["taskId"] for task in result["plan"]["tasks"]}) == len(result["plan"]["tasks"])


def test_general_simulation_shortage_and_override_whitelist_are_explicit() -> None:
    topology = TopologyPayload.model_validate(
        json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    ).model_dump(by_alias=True)
    result = validate_general_simulation_orders(
        topology,
        load_catalog(),
        "general-order-simulation",
        [_general_order("ORD-SHORT", "13448419-4.0-4.0", 41, 20)],
        [
            {"inventoryRecordId": "sim-right-13448419-l2-r96-c31", "quantityCases": 1},
            {"inventoryRecordId": "sim-left-13448419-storage-01-l1", "quantityCases": 0},
        ],
    )
    assert result["status"] == "SHORTAGE"
    assert result["plans"][0]["allocationStatus"] == "shortage"
    assert result["plans"][0]["tasks"] == []
    with pytest.raises(FulfillmentDataValidationError, match="unsupported record"):
        validate_general_simulation_orders(
            topology,
            load_catalog(),
            "general-order-simulation",
            [_general_order("ORD-BAD", "13448419-4.0-4.0", 20, 20)],
            [{"inventoryRecordId": "invented-location", "quantityCases": 99}],
        )
