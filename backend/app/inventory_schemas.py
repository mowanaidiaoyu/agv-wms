from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


InventorySource = Literal["synthetic_demo", "manual", "wms_import"]


class SKURecord(BaseModel):
    sku_id: str = Field(min_length=1)
    sku_name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    specification: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    weight_kg: float | None = Field(default=None, ge=0)
    material: str | None = None
    source: str


class InventoryBalance(BaseModel):
    topology_id: str
    zone_id: str | None
    storage_node_id: str
    storage_layer: int = Field(ge=1)
    sku: SKURecord
    quantity_on_hand: int = Field(ge=0)
    quantity_reserved: int = Field(ge=0)
    quantity_available: int = Field(ge=0)
    source: str
    updated_at: str


class AccessMapping(BaseModel):
    topology_id: str
    storage_node_id: str
    access_cell_id: str | None = None
    status: Literal["configured", "unconfigured", "topology_version_mismatch", "invalid"]
    source: str | None = None
    validated_at: str | None = None
    validation_message: str | None = None


class StorageLocationInventory(BaseModel):
    topology_id: str
    topology_version: str
    storage_node_id: str
    label: str
    zone_id: str | None
    zone_label: str | None
    zone_type: str | None
    floor: str
    row: int
    col: int
    rack_level: int | None = None
    balances: list[InventoryBalance]
    access_mapping: AccessMapping
    data_sources: list[str]


class InventorySummary(BaseModel):
    topology_id: str
    storage_location_count: int
    stocked_location_count: int
    positive_inventory_location_count: int
    unconfigured_location_count: int
    zero_inventory_location_count: int
    sku_count: int
    quantity_on_hand: int
    quantity_reserved: int
    quantity_available: int
    data_sources: list[str]


class ZoneInventorySummary(BaseModel):
    topology_id: str
    zone_id: str
    zone_label: str
    zone_type: str
    storage_location_count: int
    stocked_location_count: int
    positive_inventory_location_count: int
    unconfigured_location_count: int
    zero_inventory_location_count: int
    sku_count: int
    quantity_on_hand: int
    quantity_reserved: int
    quantity_available: int
    data_sources: list[str]


class SyntheticInventorySeedRequest(BaseModel):
    topology_id: str = Field(min_length=1)
    seed: int = 42


class SyntheticInventorySeedResult(BaseModel):
    topology_id: str
    seed: int
    inserted_skus: int
    inserted_balances: int
    source: Literal["synthetic_demo"] = "synthetic_demo"


class SyntheticInventorySupplementRequest(BaseModel):
    topology_id: str = Field(min_length=1)
    count: int | None = Field(default=None, gt=0)
    fill_all: bool = False
    seed: int = 42


class SyntheticInventorySupplementPreview(BaseModel):
    topology_id: str
    eligible_location_count: int
    requested_location_count: int
    estimated_insert_count: int
    fill_all: bool
    before: InventorySummary


class SyntheticInventorySupplementResult(BaseModel):
    topology_id: str
    seed: int
    fill_all: bool
    requested_location_count: int
    eligible_before: int
    inserted_locations: int
    inserted_balances: int
    skipped_existing_locations: int
    skipped_due_to_limit: int
    source: Literal["synthetic_demo"] = "synthetic_demo"
    before: InventorySummary
    after: InventorySummary


class AccessMappingRequest(BaseModel):
    topology_id: str = Field(min_length=1)
    access_cell_id: str = Field(min_length=1)
    source: str = Field(default="manual", min_length=1)
    reviewed: bool = False


class AccessCandidate(BaseModel):
    topology_id: str
    topology_version: str
    storage_node_id: str
    access_cell_id: str
    floor: str
    row: int | None
    col: int | None
    zone_id: str | None
    distance_cells: int | None
    status: Literal["eligible", "review_required", "rejected"]
    reason_codes: list[str]
    reasons: list[str]


class AccessCandidateList(BaseModel):
    topology_id: str
    topology_version: str
    storage_node_id: str
    eligible_count: int = Field(ge=0)
    review_required_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    candidates: list[AccessCandidate]


class AccessCandidateAudit(BaseModel):
    topology_id: str
    topology_version: str
    storage_location_count: int = Field(ge=0)
    eligible_location_count: int = Field(ge=0)
    review_required_location_count: int = Field(ge=0)
    unmappable_location_count: int = Field(ge=0)
    configured_mapping_count: int = Field(ge=0)
    invalid_mapping_count: int = Field(ge=0)
    reason_counts: dict[str, int]


class StorageAccessPathRequest(BaseModel):
    topology_id: str = Field(min_length=1)
    storage_node_id: str = Field(min_length=1)
    start_cell_id: str = Field(min_length=1)
    algorithm: Literal["astar", "dijkstra"] = "astar"


class StorageAccessPathResult(BaseModel):
    topology_id: str
    topology_version: str
    storage_node_id: str
    access_cell_id: str
    algorithm: Literal["astar", "dijkstra"]
    path_cells: list[dict]
    total_distance: int = Field(ge=0)
    total_cost: float = Field(ge=0)
    visited_nodes: int = Field(ge=0)
    execution_status: Literal["planning_preview_only"]


# Reserved contracts for C-INV-2/C-INV-3. They are intentionally not exposed as
# inventory mutation endpoints in this Gate.
class OrderLineContract(BaseModel):
    sku_id: str
    requested_quantity: int = Field(gt=0)
    allocated_quantity: int = Field(default=0, ge=0)
    picked_quantity: int = Field(default=0, ge=0)


class PickingTaskContract(BaseModel):
    task_id: str
    robot_id: str
    source_storage_node_id: str
    access_cell_id: str
    sku_id: str
    quantity: int = Field(gt=0)
    status: Literal["pending", "reserved", "en_route", "picked", "failed", "cancelled"]
