from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .shuttle_lift_schemas import ShuttlePlanResponse


ShuttleTransferStatus = Literal[
    "created",
    "reserved",
    "planned",
    "moving_to_pickup",
    "picked",
    "moving_to_dropoff",
    "delivered",
    "failed",
    "cancelled",
]


class ShuttleDemoInventoryInitializeRequest(BaseModel):
    topology_id: str = Field(min_length=1)


class ShuttleDemoInventoryInitializeResponse(BaseModel):
    topology_id: str
    topology_revision: str
    seed: Literal["shuttle-stock-demo-v1"]
    candidate_location_count: int = Field(ge=0)
    eligible_location_count: int = Field(ge=0)
    inserted_sku_count: int = Field(ge=0)
    inserted_balance_count: int = Field(ge=0)
    existing_balance_count: int = Field(ge=0)
    data_source: Literal["synthetic_demo"]


class ShuttleDemoSkuSummary(BaseModel):
    sku_id: str
    sku_name: str
    unit: str
    quantity_on_hand: int = Field(ge=0)
    quantity_reserved: int = Field(ge=0)
    quantity_available: int = Field(ge=0)
    stocked_location_count: int = Field(ge=0)


class ShuttleDemoInventoryBalance(BaseModel):
    sku_id: str
    sku_name: str
    unit: str
    quantity_on_hand: int = Field(ge=0)
    quantity_reserved: int = Field(ge=0)
    quantity_available: int = Field(ge=0)


class ShuttleDemoLocationInventory(BaseModel):
    topology_id: str
    location_id: str
    zone_id: str
    level: Literal[1, 2, 3, 4]
    row: int
    col: int
    world_x: float
    world_y: float
    world_z: float
    transfer_eligible: bool
    access_track_node_id: str | None
    eligibility_reason_codes: list[str]
    mapping_source: Literal["synthetic_demo_adjacency_assumption"]
    physical_review_status: Literal["unverified"]
    balances: list[ShuttleDemoInventoryBalance]
    data_source: Literal["synthetic_demo"]


class ShuttleDemoLocationPage(BaseModel):
    topology_id: str
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    locations: list[ShuttleDemoLocationInventory]


class ShuttleTransferCreateRequest(BaseModel):
    topology_id: str = Field(min_length=1)
    source_location_id: str = Field(min_length=1)
    destination_location_id: str = Field(min_length=1)
    sku_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    algorithm: Literal["astar", "dijkstra"] = "astar"
    vehicle_id: str = Field(default="SHUTTLE-01", min_length=1)
    current_track_node_id: str | None = None


class ShuttleTransferAdvanceRequest(BaseModel):
    elapsed_seconds: float = Field(ge=0)


class ShuttleTransferFailRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class ShuttleDemoInventoryLedgerEntry(BaseModel):
    event_id: str
    task_id: str
    topology_id: str
    location_id: str | None
    sku_id: str
    event_type: Literal["reserve", "release", "pick", "drop", "failure_in_transit"]
    quantity: int = Field(gt=0)
    on_hand_delta: int
    reserved_delta: int
    vehicle_load_delta: int
    quantity_on_hand_after: int | None
    quantity_reserved_after: int | None
    vehicle_load_after: int = Field(ge=0)
    data_source: Literal["synthetic_demo"]
    created_at: str


class ShuttleTransferTask(BaseModel):
    task_id: str
    topology_id: str
    topology_revision: str
    shuttle_config_revision: str
    coordinate_contract_revision: Literal["shuttle-internal-levels-v1"]
    vehicle_id: str
    current_track_node_id: str
    source_location_id: str
    source_access_track_node_id: str
    destination_location_id: str
    destination_access_track_node_id: str
    sku_id: str
    quantity: int = Field(gt=0)
    vehicle_capacity_units: int = Field(gt=0)
    vehicle_load_quantity: int = Field(ge=0)
    algorithm: Literal["astar", "dijkstra"]
    status: ShuttleTransferStatus
    plan: ShuttlePlanResponse | None
    pickup_at_seconds: float | None = Field(default=None, ge=0)
    dropoff_at_seconds: float | None = Field(default=None, ge=0)
    simulation_elapsed_seconds: float = Field(ge=0)
    pick_event_id: str
    drop_event_id: str
    failure_reason: str | None
    data_source: Literal["synthetic_demo"]
    source_inventory: ShuttleDemoInventoryBalance
    destination_inventory: ShuttleDemoInventoryBalance
    ledger: list[ShuttleDemoInventoryLedgerEntry]
    created_at: str
    updated_at: str
