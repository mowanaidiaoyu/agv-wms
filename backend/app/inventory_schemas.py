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


class CtuRoadSemanticCell(BaseModel):
    road_cell_id: str
    floor: str
    row: int
    col: int
    zone_id: str | None
    road_class: Literal["ctu", "shuttle", "unassigned", "other", "invalid"]
    traversability: Literal["walkable", "blocked"]
    component_id: str
    component_size: int = Field(ge=1)
    eligible_for_ctu: bool


class CtuTrialArea(BaseModel):
    draft_key: str
    status: Literal["UNVERIFIED"]
    review_status: Literal["draft"]
    vehicle_type: Literal["ctu"]
    floor: str
    zone_ids: list[str]
    road_class: str
    road_cell_ids: list[str]
    road_component_id: str
    vehicle_start_cell_id: str
    storage_node_ids: list[str]
    pick_face: Literal["unknown"]
    access_cell_id: None = None
    mapping_source: Literal["topology_audit_draft"]
    eligible_for_planning: Literal[False]
    reasons: list[str]


class CtuRoadSemanticAudit(BaseModel):
    topology_id: str
    topology_revision: str
    vehicle_type: Literal["ctu"]
    render_contract: dict[str, str | bool]
    road_counts: dict[str, int]
    road_components: dict[str, int]
    road_cells: list[CtuRoadSemanticCell]
    ctu_road_overlap_storage_count: int = Field(ge=0)
    ctu_road_eligible_count: int = Field(ge=0)
    storage_node_count: int = Field(ge=0)
    ctu_storage_node_count: int = Field(ge=0)
    stable_ctu_storage_geometry_count: int = Field(ge=0)
    pick_face_declared_count: int = Field(ge=0)
    ctu_vehicle_footprint_declared: bool
    formal_access_cell_count: int = Field(ge=0)
    trial_area: CtuTrialArea | None
    warnings: list[str]
    saved_draft_count: int = Field(ge=0)


class CtuTrialDraftRequest(BaseModel):
    topology_id: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=1000)


class CtuTrialDraft(BaseModel):
    contract_version: Literal["ctu-road-pick-contract/v1"]
    draft_id: str
    topology_id: str
    topology_revision: str
    zone_id: str | None
    zone_ids: list[str]
    road_cell_ids: list[str]
    vehicle_type: Literal["ctu"]
    floor: str
    traversability: Literal["unverified"]
    road_connectivity: dict[str, str | int]
    storage_node_ids: list[str]
    pick_face: Literal["unknown"]
    access_cell_id: None = None
    vehicle_start_cell_id: str
    mapping_source: Literal["topology_audit_draft"]
    review_status: Literal["draft", "verified", "rejected"]
    eligible_for_planning: Literal[False]
    reviewer_required: Literal["researcher_b"]
    note: str | None
    reasons: list[str]
    created_at: str
    updated_at: str
    version_status: Literal["current", "topology_version_mismatch"]


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
