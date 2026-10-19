from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


ZoneType = Literal["ctu", "shuttle", "port"]
CellType = Literal["road", "blocked", "conveyor"]
ConveyorDirection = Literal["right", "left", "up", "down"]
NodeType = Literal["storage", "ctu_robot", "shuttle_robot", "lift_transfer", "turn_transfer"]
RobotType = Literal["ctu", "shuttle"]
DispatchPolicy = Literal["greedy", "hungarian"]
PathPolicy = Literal["astar", "dijkstra"]
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


class RobotConfig(BaseModel):
    id: str
    type: RobotType
    startNode: str
    speed: float = 1.0
    battery: float = 100.0
    enabled: bool = True


class OrderGeneratorConfig(BaseModel):
    ratePerMinute: float = 4.0
    maxOrders: int = 18
    linesPerOrderMin: int = 1
    linesPerOrderMax: int = 3
    urgentInsertionTick: int | None = 20


class ConveyorPolicyConfig(BaseModel):
    releaseThreshold: float = 0.8
    fifo: bool = True


class FaultInjectionConfig(BaseModel):
    robotFailureTick: int | None = 35
    robotFailureId: str | None = None
    conveyorJammedTick: int | None = 45
    conveyorCellId: str | None = None


class ScenarioPayload(BaseModel):
    id: str
    name: str
    topologyId: str
    robots: list[RobotConfig]
    orderGenerator: OrderGeneratorConfig = Field(default_factory=OrderGeneratorConfig)
    dispatchPolicy: DispatchPolicy = "greedy"
    pathPolicy: PathPolicy = "astar"
    conveyorPolicy: ConveyorPolicyConfig = Field(default_factory=ConveyorPolicyConfig)
    faultInjection: FaultInjectionConfig = Field(default_factory=FaultInjectionConfig)
    seed: int = 7
    durationTicks: int = 90


class SimulationControlResponse(BaseModel):
    runId: str
    status: SimulationStatus


class RunState(BaseModel):
    id: str
    status: SimulationStatus
    tick: int
    topologyId: str
    scenarioId: str
    robots: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    queues: dict[str, list[str]]
    createdAt: datetime
    updatedAt: datetime


class MetricSnapshot(BaseModel):
    throughput: int
    completedTasks: int
    pendingTasks: int
    activeRobots: int
    blockedRobots: int
    conveyorUtilization: float
    deadlocksDetected: int
    congestionEvents: int
    urgentOrdersHandled: int
    avgEtaTicks: float
    avgCompletionTicks: float
    stationQueueDepth: int
    conveyorQueueDepth: int
    bufferOccupancy: int


class EventMessage(BaseModel):
    type: str
    tick: int
    payload: dict[str, Any]


class AlgorithmEvaluationRequest(BaseModel):
    topologyId: str
    scenarioId: str


class AlgorithmEvaluationResponse(BaseModel):
    topologyId: str
    scenarioId: str
    results: list[dict[str, Any]]
