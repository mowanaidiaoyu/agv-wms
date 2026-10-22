from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .shuttle_lift_schemas import ShuttleAlgorithm, ShuttleTrackNode


ShuttleLevel = Literal[1, 2, 3, 4]
ShuttleMultiStatus = Literal["planning_success", "failed", "unsafe"]
ShuttleMultiFailureCode = Literal[
    "invalid_vehicle_count",
    "duplicate_vehicle_id",
    "invalid_priority",
    "invalid_planning_request",
    "initial_occupancy_conflict",
    "duplicate_terminal_destination",
    "terminal_geometry_conflict",
    "search_limit_exceeded",
    "unreachable_track",
    "reservation_conflict_unresolved",
    "configuration_mismatch",
]
ShuttleEventType = Literal[
    "track_node_conflict_detected",
    "edge_swap_conflict_detected",
    "geometric_conflict_detected",
    "lift_requested",
    "lift_reservation_denied",
    "lift_reserved",
    "lift_reposition_started",
    "lift_reposition_completed",
    "vehicle_wait_started",
    "vehicle_wait_completed",
    "lift_entered",
    "lift_transfer_started",
    "lift_transfer_completed",
    "lift_exited",
]


class ShuttleVehicleRequest(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=64)
    start_track_node_id: str = Field(min_length=1)
    target_track_node_id: str = Field(min_length=1)
    priority: int = Field(default=1, ge=1, le=100)
    algorithm: ShuttleAlgorithm = "astar"

    @model_validator(mode="after")
    def validate_distinct_endpoints(self) -> "ShuttleVehicleRequest":
        if self.start_track_node_id == self.target_track_node_id:
            raise ValueError("start_track_node_id and target_track_node_id must differ")
        return self


class ShuttleMultiPlanningOptions(BaseModel):
    time_step_seconds: Literal[0.25] = 0.25
    max_vehicles: int = Field(default=4, ge=2, le=4)
    max_time_steps: int = Field(default=1600, ge=20, le=10000)
    max_expanded_states: int = Field(default=400000, ge=100, le=2_000_000)
    max_lift_candidates: int = Field(default=2, ge=1, le=2)
    max_replans: int = Field(default=1, ge=0, le=4)
    vehicle_length_cells: float = Field(default=0.92, gt=0, le=2)
    vehicle_width_cells: float = Field(default=0.82, gt=0, le=2)
    safety_margin_cells: float = Field(default=0.05, ge=0, le=1)
    lift_initial_levels: dict[str, ShuttleLevel] = Field(
        default_factory=lambda: {"shuttle-lift-a": 1, "shuttle-lift-b": 1}
    )


class ShuttleMultiPlanRequest(BaseModel):
    topology_id: str = Field(min_length=1)
    topology_revision: str = Field(min_length=1)
    shuttle_config_revision: str = Field(min_length=1)
    coordinate_contract_version: Literal["shuttle-internal-levels-v1"] = (
        "shuttle-internal-levels-v1"
    )
    vehicle_requests: list[ShuttleVehicleRequest] = Field(min_length=2, max_length=4)
    planning_options: ShuttleMultiPlanningOptions = Field(
        default_factory=ShuttleMultiPlanningOptions
    )

    @model_validator(mode="after")
    def validate_vehicle_ids(self) -> "ShuttleMultiPlanRequest":
        vehicle_ids = [vehicle.vehicle_id for vehicle in self.vehicle_requests]
        if len(vehicle_ids) != len(set(vehicle_ids)):
            raise ValueError("vehicle_id values must be unique")
        return self


class ShuttleTimedTrajectorySample(BaseModel):
    time_step: int = Field(ge=0)
    time_seconds: float = Field(ge=0)
    world_x: float
    world_y: float
    world_z: float
    track_node_id: str | None = None
    action: Literal[
        "start", "wait", "turn", "move", "lift_enter", "lift", "lift_exit",
        "pick_service", "drop_service",
    ]
    lift_id: str | None = None
    edge_from: str | None = None
    edge_to: str | None = None


class ShuttleWaitInterval(BaseModel):
    track_node_id: str
    start_time_seconds: float = Field(ge=0)
    end_time_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    reason: Literal["track_reservation", "lift_resource", "turn"]


class ShuttleLiftUsage(BaseModel):
    lift_id: str
    vehicle_id: str
    entry_level: ShuttleLevel
    exit_level: ShuttleLevel
    request_time_seconds: float = Field(ge=0)
    reposition_start_seconds: float = Field(ge=0)
    reposition_end_seconds: float = Field(ge=0)
    enter_start_seconds: float = Field(ge=0)
    transfer_start_seconds: float = Field(ge=0)
    transfer_end_seconds: float = Field(ge=0)
    exit_end_seconds: float = Field(ge=0)
    initial_cabin_level: ShuttleLevel
    final_cabin_level: ShuttleLevel
    used_as_alternate: bool = False
    reservation_status: Literal["reserved"] = "reserved"


class ShuttleMultiVehicleResult(BaseModel):
    vehicle_id: str
    priority: int
    algorithm: ShuttleAlgorithm
    start: ShuttleTrackNode
    destination: ShuttleTrackNode
    path: list[str]
    timed_trajectory: list[ShuttleTimedTrajectorySample]
    wait_intervals: list[ShuttleWaitInterval] = Field(default_factory=list)
    lift_usage: list[ShuttleLiftUsage] = Field(default_factory=list)
    arrival_time_seconds: float = Field(ge=0)
    wait_time_seconds: float = Field(ge=0)
    horizontal_distance_cells: float = Field(ge=0)
    visited_states: int = Field(ge=0)
    planning_status: Literal["planned"] = "planned"
    pickup: ShuttleTrackNode | None = None
    source_location_id: str | None = None
    destination_location_id: str | None = None
    sku_id: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    pickup_service_start_seconds: float | None = Field(default=None, ge=0)
    pickup_service_end_seconds: float | None = Field(default=None, ge=0)
    drop_service_start_seconds: float | None = Field(default=None, ge=0)
    drop_service_end_seconds: float | None = Field(default=None, ge=0)


class ShuttlePlanningEvent(BaseModel):
    event_id: str
    simulation_time_seconds: float = Field(ge=0)
    vehicle_ids: list[str]
    event_type: ShuttleEventType
    resource_id: str | None = None
    track_node_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    resolution_status: Literal[
        "candidate_rejected",
        "resolved_by_wait",
        "reserved",
        "completed",
    ]


class ShuttleMultiMetrics(BaseModel):
    requested_vehicle_count: int = Field(ge=0)
    planned_vehicle_count: int = Field(ge=0)
    makespan_seconds: float = Field(ge=0)
    total_wait_time_seconds: float = Field(ge=0)
    average_wait_time_seconds: float = Field(ge=0)
    detected_track_node_conflicts: int = Field(ge=0)
    detected_edge_swap_conflicts: int = Field(ge=0)
    detected_geometric_conflicts: int = Field(ge=0)
    lift_resource_contentions: int = Field(ge=0)
    lift_a_usage_count: int = Field(ge=0)
    lift_b_usage_count: int = Field(ge=0)
    lift_a_occupied_seconds: float = Field(ge=0)
    lift_b_occupied_seconds: float = Field(ge=0)
    alternate_lift_selections: int = Field(ge=0)
    replans_used: int = Field(ge=0)
    unresolved_conflict_count: int = Field(ge=0)
    planning_time_ms: float = Field(ge=0)


class ShuttleSafetyValidation(BaseModel):
    node_conflicts: int = Field(ge=0)
    edge_conflicts: int = Field(ge=0)
    geometric_conflicts: int = Field(ge=0)
    lift_resource_conflicts: int = Field(ge=0)
    timing_violations: int = Field(ge=0)
    safe: bool


class ShuttleMultiFailure(BaseModel):
    vehicle_id: str | None = None
    failure_code: ShuttleMultiFailureCode
    expanded_states: int | None = Field(default=None, ge=0)
    max_expanded_states: int | None = Field(default=None, ge=1)
    planning_time_ms: float = Field(ge=0)
    failure_reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class ShuttleMultiPlanResponse(BaseModel):
    planning_id: str
    topology_id: str
    topology_version: str
    topology_revision: str
    shuttle_config_revision: str
    coordinate_contract_version: Literal["shuttle-internal-levels-v1"]
    planning_algorithm_version: str
    status: ShuttleMultiStatus
    vehicles: list[ShuttleMultiVehicleResult] = Field(default_factory=list)
    events: list[ShuttlePlanningEvent] = Field(default_factory=list)
    metrics: ShuttleMultiMetrics
    safety_validation: ShuttleSafetyValidation
    unresolved_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    failure_reason: str | None = None
    failure: ShuttleMultiFailure | None = None
    vehicle_initial_config: list[ShuttleVehicleRequest]
    lift_initial_state: dict[str, ShuttleLevel]
    time_contract: dict[str, Any]
    transfer_contract: dict[str, Any] | None = None


class ShuttleMultiScenario(BaseModel):
    scenario_id: Literal["parallel", "track-conflict", "lift-conflict"]
    name: str
    description: str
    vehicle_requests: list[ShuttleVehicleRequest]
    evidence: dict[str, Any] = Field(default_factory=dict)


class ShuttleMultiScenariosResponse(BaseModel):
    topology_id: str
    topology_revision: str
    shuttle_config_revision: str
    coordinate_contract_version: Literal["shuttle-internal-levels-v1"]
    scenarios: list[ShuttleMultiScenario]
