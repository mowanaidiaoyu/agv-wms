from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ZoneType = Literal["ctu", "shuttle", "port"]
CellType = Literal["road", "blocked", "conveyor"]
ConveyorDirection = Literal["right", "left", "up", "down"]
NodeType = Literal["storage", "ctu_robot", "shuttle_robot", "lift_transfer", "turn_transfer"]
SimulationStatus = Literal["idle", "running", "paused", "stopped", "completed"]


class Zone(BaseModel):
    id: str
    label: str
    type: ZoneType
    x: int
    y: int
    width: int
    height: int
    slotAisles: dict[str, int] | None = None
    removedSlots: list[str] | None = None
    slotLifts: list[str] | None = None


class GridConfig(BaseModel):
    rows: int = 12
    cols: int = 20
    cellSize: int = 32
    originX: int = 0
    originY: int = 0


class TopologyCell(BaseModel):
    id: str
    floor: str = "1F"
    row: int
    col: int
    type: CellType
    zoneId: str | None = None
    cost: float = 1.0
    walkable: bool = True
    conveyorDirection: ConveyorDirection | None = None
    levels: int = 1
    conveyorAnchor: dict[str, int] | None = None


class TopologyNode(BaseModel):
    id: str
    type: NodeType
    floor: str = "1F"
    row: int
    col: int
    zoneId: str | None = None
    capacity: int = 1
    label: str


class TopologyMeta(BaseModel):
    version: str = "2.0.0"
    viewport: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0, "zoom": 1})
    tags: list[str] = Field(default_factory=list)
    floors: list[str] = Field(default_factory=lambda: ["1F"])


class TopologyPayload(BaseModel):
    id: str
    name: str
    meta: TopologyMeta = Field(default_factory=TopologyMeta)
    grid: GridConfig = Field(default_factory=GridConfig)
    zones: list[Zone] = Field(default_factory=list)
    cells: list[TopologyCell] = Field(default_factory=list)
    nodes: list[TopologyNode] = Field(default_factory=list)


class SimulationControlResponse(BaseModel):
    runId: str
    status: SimulationStatus
