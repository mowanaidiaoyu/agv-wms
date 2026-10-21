from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


ShuttleAlgorithm = Literal["astar", "dijkstra"]
ShuttleLiftState = Literal["idle", "moving_up", "moving_down", "occupied", "unavailable"]
ShuttleVerificationStatus = Literal[
    "program_verified_pending_human",
    "unverified",
    "rejected",
    "topology_version_mismatch",
]


class ShuttleTrackNode(BaseModel):
    topology_id: str
    zone_id: str
    track_node_id: str
    shuttle_level: Literal[1, 2, 3, 4]
    row: int
    col: int
    world_x: float
    world_y: float
    world_z: float
    track_type: Literal["straight", "intersection", "turn", "lift_entry", "lift_exit"]
    traversable: bool
    neighbors: list[str] = Field(default_factory=list)


class ShuttleLevelSummary(BaseModel):
    shuttle_level: Literal[1, 2, 3, 4]
    node_count: int = Field(ge=0)
    component_count: int = Field(ge=0)
    component_sizes: list[int] = Field(default_factory=list)


class ShuttleLift(BaseModel):
    lift_id: Literal["shuttle-lift-a", "shuttle-lift-b"]
    label: str
    topology_id: str
    topology_revision: str
    config_revision: str
    zone_id: str
    row: int
    col: int
    supported_levels: list[Literal[1, 2, 3, 4]]
    level_entrances: dict[str, str]
    level_exits: dict[str, str]
    capacity: Literal[1] = 1
    enter_time_seconds: float = Field(gt=0)
    travel_time_per_level_seconds: float = Field(gt=0)
    exit_time_seconds: float = Field(gt=0)
    timing_source: Literal["synthetic_demo"] = "synthetic_demo"
    vehicle_type: Literal["shuttle"] = "shuttle"
    state: ShuttleLiftState = "idle"
    verification_status: ShuttleVerificationStatus
    validation_evidence: list[str] = Field(default_factory=list)


class ShuttleTopologyLevelsResponse(BaseModel):
    topology_id: str
    topology_name: str
    topology_revision: str
    topology_version: str
    config_revision: str
    coordinate_contract_revision: Literal["shuttle-internal-levels-v1"]
    data_source: Literal["topology_slot_aisles"] = "topology_slot_aisles"
    verification_status: ShuttleVerificationStatus
    planning_enabled: bool
    lift_candidate_count: int = Field(ge=0)
    lift_selection_basis: list[str] = Field(default_factory=list)
    levels: list[ShuttleLevelSummary]
    warnings: list[str] = Field(default_factory=list)


class ShuttleTracksResponse(BaseModel):
    topology_id: str
    topology_revision: str
    config_revision: str
    coordinate_contract_revision: Literal["shuttle-internal-levels-v1"]
    nodes: list[ShuttleTrackNode]


class ShuttleLiftsResponse(BaseModel):
    topology_id: str
    topology_revision: str
    config_revision: str
    coordinate_contract_revision: Literal["shuttle-internal-levels-v1"]
    planning_enabled: bool
    lifts: list[ShuttleLift]
    warnings: list[str] = Field(default_factory=list)


class ShuttlePlanRequest(BaseModel):
    topology_id: str = Field(min_length=1)
    start_track_node: str = Field(min_length=1)
    start_level: Literal[1, 2, 3, 4]
    end_track_node: str = Field(min_length=1)
    end_level: Literal[1, 2, 3, 4]
    algorithm: ShuttleAlgorithm = "astar"

    @model_validator(mode="after")
    def validate_explicit_levels(self) -> "ShuttlePlanRequest":
        expected_start = f"L{self.start_level}-"
        expected_end = f"L{self.end_level}-"
        if not self.start_track_node.startswith(expected_start):
            raise ValueError("start_track_node does not match start_level")
        if not self.end_track_node.startswith(expected_end):
            raise ValueError("end_track_node does not match end_level")
        return self


class ShuttlePathNodeStep(ShuttleTrackNode):
    arrival_seconds: float = Field(ge=0)
    phase: Literal["start", "track", "lift"]


class ShuttlePathEdge(BaseModel):
    from_track_node: str
    to_track_node: str
    edge_type: Literal["track", "lift"]
    lift_id: str | None = None
    duration_seconds: float = Field(gt=0)
    start_seconds: float = Field(ge=0)
    motion_start_seconds: float = Field(ge=0)
    motion_end_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    distance_cells: float = Field(ge=0)
    turn_time_seconds: float = Field(ge=0)


class ShuttleLiftTransition(BaseModel):
    lift_id: str
    from_level: Literal[1, 2, 3, 4]
    to_level: Literal[1, 2, 3, 4]
    entrance_track_node: str
    exit_track_node: str
    enter_at_seconds: float = Field(ge=0)
    lift_start_seconds: float = Field(ge=0)
    arrive_level_seconds: float = Field(ge=0)
    exit_at_seconds: float = Field(ge=0)
    events: list[Literal["enter_lift", "lift_started", "arrive_level", "exit_lift"]]


class ShuttlePlanResponse(BaseModel):
    topology_id: str
    topology_revision: str
    config_revision: str
    coordinate_contract_revision: Literal["shuttle-internal-levels-v1"]
    algorithm: ShuttleAlgorithm
    start: ShuttleTrackNode | None = None
    destination: ShuttleTrackNode | None = None
    chosen_lift: str | None = None
    path_nodes: list[ShuttlePathNodeStep] = Field(default_factory=list)
    path_edges: list[ShuttlePathEdge] = Field(default_factory=list)
    lift_transitions: list[ShuttleLiftTransition] = Field(default_factory=list)
    horizontal_distance_cells: float = Field(ge=0, default=0)
    estimated_duration_seconds: float = Field(ge=0, default=0)
    visited_states: int = Field(ge=0, default=0)
    planning_time_ms: float = Field(ge=0, default=0)
    result_status: Literal["success", "failed"]
    failure_reason: str | None = None
    timing_source: Literal["synthetic_demo"] = "synthetic_demo"
    cost_contract: dict[str, float | str] = Field(default_factory=dict)
