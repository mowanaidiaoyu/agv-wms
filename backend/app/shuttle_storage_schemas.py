from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ShuttleStorageCandidate(BaseModel):
    location_id: str
    topology_id: str
    topology_revision: str
    zone_id: str
    floor: str
    storage_level: Literal[1, 2, 3, 4]
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    pick_face: Literal["unknown"]
    access_track_node_id: None = None
    potential_access_track_node_ids: list[str]
    access_mapping_status: Literal["unverified"]
    review_status: Literal["draft"]
    data_source: Literal["topology_shuttle_slot_candidate"]
    execution_allowed: Literal[False]
    blocker_codes: list[str]


class ShuttleStorageCandidatePage(BaseModel):
    topology_id: str
    topology_revision: str
    contract_version: Literal["shuttle-storage-contract/v1"]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    candidates: list[ShuttleStorageCandidate]


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
