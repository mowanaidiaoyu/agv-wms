from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PathDemoAlgorithm = Literal["astar", "dijkstra"]


class PathDemoPlanRequest(BaseModel):
    topology_id: str = Field(min_length=1)
    start_cell_id: str = Field(min_length=1)
    goal_cell_id: str = Field(min_length=1)
    algorithm: PathDemoAlgorithm = "astar"


class PathDemoCell(BaseModel):
    id: str
    floor: str
    row: int
    col: int
    type: str
    component_id: str | None = None


class PathDemoComponent(BaseModel):
    id: str
    floor: str
    cell_count: int


class PathDemoTopologyOptions(BaseModel):
    topology_id: str
    topology_name: str
    topology_version: str
    components: list[PathDemoComponent]
    largest_component_id: str
    traversable_cells: list[PathDemoCell]
    default_start: PathDemoCell
    default_goal: PathDemoCell


class PathDemoPlanResponse(BaseModel):
    topology_id: str
    topology_name: str
    topology_version: str
    algorithm: PathDemoAlgorithm
    start: PathDemoCell
    goal: PathDemoCell
    path_cells: list[PathDemoCell]
    total_distance: float
    total_cost: float
    visited_nodes: int
    planning_time_ms: float


class MultiPathVehicleRequest(BaseModel):
    vehicle_id: str = Field(min_length=1)
    vehicle_type: Literal["ctu"] = "ctu"
    start_cell_id: str = Field(min_length=1)
    goal_cell_id: str = Field(min_length=1)
    priority: int = Field(default=1, ge=1, le=100)
    display_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class MultiPathPlanningConfig(BaseModel):
    time_step_seconds: float = Field(default=0.25, gt=0)
    speed_cells_per_second: float = Field(default=4.0, gt=0)
    max_time_steps: int = Field(default=512, ge=1, le=4096)
    max_expanded_states: int = Field(default=200_000, ge=1, le=2_000_000)
    vehicle_width_cells: float = Field(default=0.58, gt=0)
    vehicle_length_cells: float = Field(default=0.72, gt=0)
    safety_margin_cells: float = Field(default=0.05, ge=0)


class MultiPathPlanRequest(BaseModel):
    topology_id: str = Field(min_length=1)
    mode: Literal["preset", "custom"] | None = None
    scenario_id: Literal["parallel", "node-conflict", "edge-swap"] | None = None
    vehicles: list[MultiPathVehicleRequest] = Field(default_factory=list, max_length=6)
    algorithm: PathDemoAlgorithm = "astar"
    planning_config: MultiPathPlanningConfig = Field(default_factory=MultiPathPlanningConfig)


class MultiPathStep(BaseModel):
    time_step: int
    time_seconds: float
    action: Literal["start", "move", "wait"]
    cell: PathDemoCell


class MultiPathVehiclePlan(BaseModel):
    vehicle_id: str
    vehicle_type: Literal["ctu"] = "ctu"
    priority: int
    display_color: str | None = None
    start: PathDemoCell
    goal: PathDemoCell
    steps: list[MultiPathStep]
    total_distance: float
    estimated_duration_seconds: float
    wait_time_seconds: float
    avoidance_count: int
    visited_states: int


class MultiPathConflictMetrics(BaseModel):
    detected_node_conflicts: int
    detected_edge_conflicts: int
    detected_geometry_conflicts: int
    resolved_node_conflicts: int
    resolved_edge_conflicts: int
    resolved_geometry_conflicts: int
    remaining_node_conflicts: int
    remaining_edge_conflicts: int
    remaining_geometry_conflicts: int


class MultiPathPlanResponse(BaseModel):
    topology_id: str
    topology_name: str
    topology_version: str
    algorithm: PathDemoAlgorithm
    planning_status: Literal["success"]
    error_reason: None = None
    time_step_seconds: float
    speed_cells_per_second: float
    vehicle_width_cells: float
    vehicle_length_cells: float
    safety_distance_cells: float
    makespan_steps: int
    makespan_seconds: float
    vehicles: list[MultiPathVehiclePlan]
    conflicts: MultiPathConflictMetrics
    avoidance_count: int
    planning_time_ms: float


class MultiPathDemoVehicle(BaseModel):
    vehicle_id: str
    start_cell_id: str
    goal_cell_id: str


class MultiPathDemoScenario(BaseModel):
    id: Literal["parallel", "node-conflict", "edge-swap"]
    name: str
    description: str
    vehicles: list[MultiPathDemoVehicle]


class MultiPathDemoScenariosResponse(BaseModel):
    topology_id: str
    topology_name: str
    topology_version: str
    largest_road_component_id: str
    road_cells: list[PathDemoCell]
    scenarios: list[MultiPathDemoScenario]


class MultiPathEvaluationRequest(BaseModel):
    topology_id: str = Field(min_length=1)
    mode: Literal["preset", "custom"]
    scenario_id: Literal["parallel", "node-conflict", "edge-swap"] | None = None
    vehicles: list[MultiPathVehicleRequest] = Field(default_factory=list, max_length=6)
    planning_config: MultiPathPlanningConfig = Field(default_factory=MultiPathPlanningConfig)


class MultiPathEvaluationInputSnapshot(BaseModel):
    topology_id: str
    topology_name: str
    topology_version: str
    mode: Literal["preset", "custom"]
    scenario_id: Literal["parallel", "node-conflict", "edge-swap"] | None = None
    vehicles: list[MultiPathVehicleRequest]
    planning_config: MultiPathPlanningConfig
    algorithms: list[PathDemoAlgorithm]


class MultiPathEvaluationVehicleMetrics(BaseModel):
    vehicle_id: str
    total_distance: float
    estimated_duration_seconds: float
    wait_time_seconds: float
    avoidance_count: int
    visited_states: int


class MultiPathEvaluationMetrics(BaseModel):
    total_path_distance: float
    makespan_seconds: float
    total_wait_time_seconds: float
    average_wait_time_seconds: float
    detected_conflicts: int
    resolved_conflicts: int
    remaining_conflicts: int
    avoidance_count: int
    vehicles: list[MultiPathEvaluationVehicleMetrics]


class MultiPathEvaluationAlgorithmResult(BaseModel):
    algorithm: PathDemoAlgorithm
    planning_status: Literal["success", "failed"]
    error_reason: str | None
    planning_time_ms: float
    metrics: MultiPathEvaluationMetrics | None
    plan: MultiPathPlanResponse | None


class MultiPathEvaluationResponse(BaseModel):
    experiment_id: str
    created_at: str
    input_snapshot: MultiPathEvaluationInputSnapshot
    results: list[MultiPathEvaluationAlgorithmResult]
