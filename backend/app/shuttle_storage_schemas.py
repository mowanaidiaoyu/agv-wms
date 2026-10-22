from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PickFace = Literal["north", "south", "east", "west"]
ReviewStatus = Literal["draft", "simulation_approved", "rejected", "physical_verified"]
EffectiveReviewStatus = Literal[
    "draft", "simulation_approved", "rejected", "physical_verified", "topology_version_mismatch"
]


class ShuttleAccessOption(BaseModel):
    track_node_id: str
    shuttle_level: Literal[1, 2, 3, 4]
    row: int
    col: int
    world_x: float
    world_y: float
    world_z: float
    required_pick_face: PickFace
    required_heading_radians: float
    connected_to_lift_network: bool
    vehicle_clearance_cells: float = Field(ge=0)
    geometry_clear: bool
    validation_codes: list[str]


class ShuttleStorageCandidate(BaseModel):
    location_id: str
    topology_id: str
    topology_revision: str
    zone_id: str
    floor: str
    storage_level: Literal[1, 2, 3, 4]
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    world_x: float
    world_y: float
    world_z: float
    pick_face: Literal["unknown"]
    access_track_node_id: None = None
    potential_access_track_node_ids: list[str]
    access_mapping_status: Literal["unverified"]
    review_status: Literal["draft"]
    data_source: Literal["topology_shuttle_slot_candidate"]
    execution_allowed: Literal[False]
    blocker_codes: list[str]
    access_options: list[ShuttleAccessOption]
    persisted_status: EffectiveReviewStatus | None = None


class ShuttleStorageCandidatePage(BaseModel):
    topology_id: str
    topology_revision: str
    contract_version: Literal["shuttle-storage-contract/v1"]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    candidates: list[ShuttleStorageCandidate]


class ShuttleTrialAreaResponse(BaseModel):
    topology_id: str
    topology_revision: str
    candidates: list[ShuttleStorageCandidate]
    selection_rule: str
    execution_allowed: Literal[False]


class ShuttleMappingValidation(BaseModel):
    valid: bool
    location_id: str
    access_track_node_id: str | None
    pick_face: PickFace | None
    topology_revision: str
    topology_content_sha256: str
    shuttle_config_revision: str
    same_level: bool
    adjacent_track: bool
    pick_face_matches: bool
    geometry_clear: bool
    operation_clearance_cells: float | None
    connected_to_lift_network: bool
    reachable: bool
    mapping_conflict: bool
    codes: list[str]
    reasons: list[str]


class ShuttleStorageDraftRequest(BaseModel):
    location_id: str = Field(min_length=1)
    pick_face: PickFace
    access_track_node_id: str = Field(min_length=1)
    reviewer: str = Field(min_length=1, max_length=120)
    review_note: str | None = Field(default=None, max_length=1000)


class ShuttleStorageReviewRequest(BaseModel):
    decision: Literal["simulation_approved", "rejected"]
    reviewer: str = Field(min_length=1, max_length=120)
    review_note: str | None = Field(default=None, max_length=1000)
    explicit_confirmation: bool = False


class ShuttleStorageLocationRecord(BaseModel):
    location_id: str
    topology_id: str
    topology_revision: str
    topology_content_sha256: str
    shuttle_config_revision: str
    zone_id: str
    level: Literal[1, 2, 3, 4]
    row: int
    col: int
    geometry_source: Literal["topology_shuttle_slot_candidate"]
    pick_face: PickFace | None
    access_track_node_id: str | None
    status: ReviewStatus
    effective_status: Literal[
        "draft", "simulation_approved", "rejected", "physical_verified", "topology_version_mismatch"
    ]
    execution_allowed: bool
    validation: ShuttleMappingValidation
    reviewer: str
    review_note: str | None
    created_at: str
    updated_at: str


class ShuttleStorageReviewHistory(BaseModel):
    review_id: int
    topology_id: str
    location_id: str
    decision: Literal["draft", "simulation_approved", "rejected"]
    reviewer: str
    review_note: str | None
    pick_face: PickFace | None
    access_track_node_id: str | None
    topology_revision: str
    shuttle_config_revision: str
    validation: ShuttleMappingValidation
    created_at: str


class ShuttleDemoDropoffDraftRequest(BaseModel):
    track_node_id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=120)
    reviewer: str = Field(min_length=1, max_length=120)
    review_note: str | None = Field(default=None, max_length=1000)


class ShuttleDemoDropoffReviewRequest(BaseModel):
    decision: Literal["simulation_approved", "rejected"]
    reviewer: str = Field(min_length=1, max_length=120)
    review_note: str | None = Field(default=None, max_length=1000)
    explicit_confirmation: bool = False


class ShuttleDemoDropoffRecord(BaseModel):
    dropoff_id: str
    topology_id: str
    topology_revision: str
    topology_content_sha256: str
    shuttle_config_revision: str
    track_node_id: str
    label: str
    status: Literal["draft", "simulation_approved", "rejected"]
    effective_status: Literal["draft", "simulation_approved", "rejected", "topology_version_mismatch"]
    execution_allowed: bool
    validation: dict
    reviewer: str
    review_note: str | None
    data_source: Literal["synthetic_demo"]
    created_at: str
    updated_at: str


class ShuttleStorageAuditResponse(BaseModel):
    topology_id: str
    topology_version: str
    topology_revision: str
    contract_version: Literal["shuttle-storage-contract/v1"]
    audit_status: Literal["blocked_unverified_mappings"]
    data_source: Literal["topology_shuttle_slot_candidate"]
    execution_allowed: Literal[False]
    ctu_storage_node_count: int = Field(ge=0)
    shuttle_storage_node_count: int = Field(ge=0)
    shuttle_zone_count: int = Field(ge=0)
    rack_level_candidate_count: int = Field(ge=0)
    candidates_with_adjacent_track_count: int = Field(ge=0)
    declared_pick_face_count: int = Field(ge=0)
    verified_access_mapping_count: int = Field(ge=0)
    formal_dropoff_count: int = Field(ge=0)
    blockers: list[str]
    required_human_review: list[str]
    candidate_preview: list[ShuttleStorageCandidate]
