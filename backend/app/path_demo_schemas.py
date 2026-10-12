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
