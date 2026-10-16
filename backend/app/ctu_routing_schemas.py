from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


CtuRoutingAlgorithm = Literal["astar", "dijkstra"]
CtuRoutingStatus = Literal["success", "partial_failure", "failed"]
CtuTaskState = Literal[
    "pending_pick",
    "picking_in_progress",
    "picking_complete",
    "invalid",
]


class CtuRoutingCell(BaseModel):
    id: str
    floor: str
    row: int
    col: int
    type: str


class CtuRoutingTimeModel(BaseModel):
    planning_basis: Literal["geometric_path_only"] = "geometric_path_only"
    live_simulation_tick_seconds: float
    reservation_time_step_seconds: None = None
    execution_eta_seconds: None = None


class CtuPathSegment(BaseModel):
    segment_index: int
    task_id: str
    vehicle_id: str
    line_index: int
    line_id: str
    line_id_source: Literal["task_line", "derived_from_task_order"]
    sku_id: str | None
    quantity: int
    quantity_source: Literal["task_line", "one_box_per_line_contract"]
    storage_node_id: str | None
    task_cell_id: str | None
    docking_cell_id: str | None
    docking_source: Literal[
        "co_located_traversable_cell",
        "explicit_resource_mapping",
    ] | None
    start_cell_id: str
    goal_cell_id: str | None
    algorithm: CtuRoutingAlgorithm
    avoid_storage: Literal[True] = True
    planning_status: Literal["success", "failed"]
    failure_code: str | None = None
    failure_reason: str | None = None
    path_cells: list[CtuRoutingCell]
    total_cost: float | None = None
    visited_nodes: int = 0
    pick_confirmation_status: Literal["not_executed"] = "not_executed"


class CtuRoutingPlan(BaseModel):
    schema_version: Literal["ctu-routing-adapter/v1"] = "ctu-routing-adapter/v1"
    source_kind: Literal["simulation_task"] = "simulation_task"
    planning_kind: Literal["single_vehicle_geometric_path"] = (
        "single_vehicle_geometric_path"
    )
    execution_status: Literal["not_executed"] = "not_executed"
    task_id: str | None
    vehicle_id: str | None
    algorithm: str
    avoid_storage: Literal[True] = True
    task_state: CtuTaskState
    planning_status: CtuRoutingStatus
    failure_code: str | None = None
    failure_reason: str | None = None
    vehicle_capacity: int | None
    vehicle_load: int | None
    remaining_capacity: int | None
    current_line_index: int | None
    picked_count: int | None
    segments: list[CtuPathSegment]
    combined_path_cell_ids: list[str]
    unplanned_line_ids: list[str]
    total_cost: float
    total_visited_nodes: int
    time_model: CtuRoutingTimeModel
