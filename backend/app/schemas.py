from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


class SimulationSpeedRequest(BaseModel):
    multiplier: Literal[1, 2, 4]


class SimulationSpeedResponse(SimulationControlResponse):
    speedMultiplier: Literal[1, 2, 4]


class SimulationOrderLineInput(BaseModel):
    id: str
    skuId: str
    skuName: str
    spec: str | None = None
    quantity: int = Field(default=1, ge=1, le=20)


class SimulationOrderInput(BaseModel):
    id: str
    priority: Literal["normal", "urgent"] = "normal"
    targetStation: str = "出库口"
    createdAt: str
    lines: list[SimulationOrderLineInput] = Field(min_length=1)


class ShuttleTransferInput(BaseModel):
    id: str = Field(min_length=1)
    vehicleId: str = Field(default="SHUTTLE-UNIFIED-01", min_length=1)
    skuId: str = Field(min_length=1)
    skuName: str = Field(default="", min_length=0)
    sourceLocationId: str = Field(min_length=1)
    destinationLocationId: str = Field(min_length=1)
    sourceTrackNode: str = Field(min_length=1)
    destinationTrackNode: str = Field(min_length=1)
    sourceLevel: Literal[1, 2, 3, 4]
    destinationLevel: Literal[1, 2, 3, 4]
    quantity: int = Field(gt=0, le=100)
    algorithm: Literal["astar", "dijkstra"] = "astar"
    initialSourceQuantity: int = Field(default=10, ge=0)
    initialDestinationQuantity: int = Field(default=5, ge=0)

    @model_validator(mode="after")
    def validate_transfer(self) -> "ShuttleTransferInput":
        if self.sourceLocationId == self.destinationLocationId:
            raise ValueError("sourceLocationId and destinationLocationId must differ")
        if self.initialSourceQuantity < self.quantity:
            raise ValueError("initialSourceQuantity is smaller than quantity")
        if not self.sourceTrackNode.startswith(f"L{self.sourceLevel}-"):
            raise ValueError("sourceTrackNode does not match sourceLevel")
        if not self.destinationTrackNode.startswith(f"L{self.destinationLevel}-"):
            raise ValueError("destinationTrackNode does not match destinationLevel")
        return self


class SimulationStartRequest(BaseModel):
    topologyId: str
    orders: list[SimulationOrderInput] = Field(default_factory=list)
    transfers: list[ShuttleTransferInput] = Field(default_factory=list)


class ShuttlePlanRequest(BaseModel):
    topologyId: str = Field(min_length=1)
    startTrackNode: str = Field(min_length=1)
    endTrackNode: str = Field(min_length=1)
    algorithm: Literal["astar", "dijkstra"] = "astar"


class ShuttleTransferFailRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
