from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


NodeType = Literal["intersection", "storage", "buffer", "station", "lift", "entry", "conveyor_gate"]
EdgeType = Literal["aisle", "conveyor", "lift", "cross_zone"]
ResourceType = Literal["conveyor_segment", "buffer_slot", "lift", "station"]
RobotType = Literal["ctu", "shuttle"]
DispatchPolicy = Literal["greedy", "hungarian"]
PathPolicy = Literal["astar", "dijkstra"]
SimulationStatus = Literal["idle", "running", "paused", "stopped", "completed"]


class Zone(BaseModel):
    id: str
    label: str
    type: Literal["ctu", "shuttle", "conveyor", "buffer", "station", "mixed"]
    x: float
    y: float
    width: float
    height: float


class TopologyNode(BaseModel):
    id: str
    type: NodeType
    x: float
    y: float
    zoneId: str | None = None
    capacity: int = 1
    label: str


class TopologyEdge(BaseModel):
    id: str
    from_node: str = Field(alias="from")
    to: str
    type: EdgeType
    length: float = 1.0
    bidirectional: bool = True
    capacity: int = 1
    speedLimit: float = 1.0

    model_config = {"populate_by_name": True}


class Resource(BaseModel):
    id: str
    type: ResourceType
    label: str
    nodeIds: list[str] = Field(default_factory=list)
    edgeIds: list[str] = Field(default_factory=list)
    capacity: int = 1
    meta: dict[str, Any] = Field(default_factory=dict)


class TopologyMeta(BaseModel):
    version: str = "1.0.0"
    viewport: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0, "zoom": 1})
    tags: list[str] = Field(default_factory=list)


class TopologyPayload(BaseModel):
    id: str
    name: str
    meta: TopologyMeta = Field(default_factory=TopologyMeta)
    zones: list[Zone] = Field(default_factory=list)
    nodes: list[TopologyNode] = Field(default_factory=list)
    edges: list[TopologyEdge] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> "TopologyPayload":
        node_ids = {node.id for node in self.nodes}
        zone_ids = {zone.id for zone in self.zones}
        edge_ids = {edge.id for edge in self.edges}
        for node in self.nodes:
            if node.zoneId and node.zoneId not in zone_ids:
                raise ValueError(f"Node {node.id} references missing zone {node.zoneId}")
        for edge in self.edges:
            if edge.from_node not in node_ids or edge.to not in node_ids:
                raise ValueError(f"Edge {edge.id} references missing node")
        for resource in self.resources:
            for node_id in resource.nodeIds:
                if node_id not in node_ids:
                    raise ValueError(f"Resource {resource.id} references missing node {node_id}")
            for edge_id in resource.edgeIds:
                if edge_id not in edge_ids:
                    raise ValueError(f"Resource {resource.id} references missing edge {edge_id}")
        return self


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
    conveyorEdgeId: str | None = None


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
    resources: list[dict[str, Any]]
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


class TopologyValidationResult(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]
