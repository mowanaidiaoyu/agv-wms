from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ZoneType = Literal["ctu", "shuttle", "port", "pick_station", "drop_zone"]
CellType = Literal["road", "blocked", "conveyor"]
ConveyorDirection = Literal["right", "left", "up", "down"]
NodeType = Literal["storage", "ctu_robot", "shuttle_robot", "lift_transfer", "turn_transfer", "shuttle_dock"]
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
    # 地面输送线（整箱，四向车出货用）：贴地单层带面；架空输送线（零散件，CTU 用）保持默认 False
    conveyorGround: bool = False


class TopologyNode(BaseModel):
    id: str
    type: NodeType
    floor: str = "1F"
    row: int
    col: int
    zoneId: str | None = None
    capacity: int = 1
    label: str
    # 四向车出货位（shuttle_dock）专用：设置取什么货物、取多少。
    # 仿真启动时按出货位生成搬运任务：四向车取货 → 经巷道出库 → 在地面输送线上交付移动。
    skuId: str | None = None
    skuName: str | None = None
    largeCategory: str | None = None
    quantity: int | None = None


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
    transportNetwork: TransportNetworkContract | None = None


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


class ShuttleDockSettingsRequest(BaseModel):
    """四向车出货位运行时设置（四向车搬运工作台）。未传字段保持不变。"""

    skuId: str | None = None
    skuName: str | None = None
    largeCategory: str | None = None
    quantity: int | None = Field(default=None, ge=1)
    priority: Literal["normal", "urgent"] | None = None
    vehicleId: str | None = None
    loop: bool | None = None
    enabled: bool | None = None


class ShuttlePlanRequest(BaseModel):
    topologyId: str = Field(min_length=1)
    startTrackNode: str = Field(min_length=1)
    endTrackNode: str = Field(min_length=1)
    algorithm: Literal["astar", "dijkstra"] = "astar"


class ShuttleTransferFailRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


TransportNetworkType = Literal[
    "ctu",
    "shuttle",
    "ground_conveyor",
    "elevated_conveyor",
    "lift",
    "human_station",
    "port",
    "outbound",
    "transfer_device",
]
AuditStatus = Literal["PASS", "PARTIAL", "BLOCKED"]
ValidationStatus = Literal["VALID", "MISSING", "PROPOSED", "INVALID"]


class TransportNode(BaseModel):
    """A typed location in one transport network.

    ``source`` identifies the concrete file/object that supplied the fact, while
    ``provenance`` distinguishes topology facts from derived or proposed data.
    """

    nodeId: str
    kind: str
    networkType: TransportNetworkType
    topologyCellId: str | None = None
    trackNodeId: str | None = None
    level: int = Field(default=1, ge=1)
    floor: str = "1F"
    row: int | None = None
    col: int | None = None
    physical: bool
    simulated: bool
    source: str
    provenance: Literal["topology", "derived", "declared", "proposed"]


class TransportEdge(BaseModel):
    edgeId: str
    fromNodeId: str
    toNodeId: str
    edgeType: str
    direction: str
    distance: float = Field(ge=0)
    source: str
    provenance: Literal["topology", "derived", "declared", "proposed"]
    traversable: bool


class HandoffPoint(BaseModel):
    handoffId: str
    fromNetwork: TransportNetworkType
    toNetwork: TransportNetworkType
    deviceId: str | None = None
    inputNodeId: str
    outputNodeId: str
    capacity: int = Field(default=1, ge=0)
    validationStatus: ValidationStatus
    source: str
    provenance: Literal["topology", "derived", "declared", "proposed"]
    evidence: list[str] = Field(default_factory=list)


class TransportNetworkContract(BaseModel):
    nodes: list[TransportNode] = Field(default_factory=list)
    edges: list[TransportEdge] = Field(default_factory=list)
    handoffs: list[HandoffPoint] = Field(default_factory=list)


class NetworkIssue(BaseModel):
    code: str
    severity: Literal["INFO", "WARNING", "ERROR"]
    sourceId: str | None = None
    targetId: str | None = None
    reason: str
    evidence: list[str] = Field(default_factory=list)


class PathAuditSegment(BaseModel):
    segmentId: str
    fromNodeId: str
    toNodeId: str
    networkType: str
    status: AuditStatus
    nodeIds: list[str] = Field(default_factory=list)
    edgeIds: list[str] = Field(default_factory=list)
    direction: str
    reachable: bool
    hasLegalHandoff: bool
    blockingReasons: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class PathAudit(BaseModel):
    routeId: str
    status: AuditStatus
    segments: list[PathAuditSegment]
    nodeIds: list[str] = Field(default_factory=list)
    edgeIds: list[str] = Field(default_factory=list)
    reachable: bool
    hasLegalHandoffs: bool
    blockingReasons: list[str] = Field(default_factory=list)


class CandidateFacility(BaseModel):
    candidateId: str
    kind: str
    networkType: str
    topologyCellId: str | None = None
    row: int | None = None
    col: int | None = None
    level: int | None = None
    physicalStatus: Literal["proposed"] = "proposed"
    relativeToIds: list[str] = Field(default_factory=list)
    requiredEdges: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    candidateArea: dict[str, Any] | None = None
    confirmationRequired: bool = True


class TransportNetworkAuditResponse(BaseModel):
    topologyId: str
    topologyRevision: str
    status: AuditStatus
    leftRoute: PathAudit
    rightRoute: PathAudit
    issues: list[NetworkIssue]
    candidateHandoffs: list[CandidateFacility]
    summary: dict[str, Any]


class LayoutPosition(BaseModel):
    floor: str = "1F"
    row: int
    col: int
    level: int = Field(default=1, ge=1)


class LayoutReferenceNode(BaseModel):
    referenceId: str
    kind: str
    networkType: str
    position: LayoutPosition
    sourceId: str
    validationStatus: AuditStatus
    evidence: list[str] = Field(default_factory=list)


class ProposedLayoutFacility(BaseModel):
    facilityId: str
    kind: str
    networkType: str
    position: LayoutPosition
    spansLevels: list[int] = Field(default_factory=list)
    status: Literal["proposed"] = "proposed"
    validationStatus: AuditStatus
    capacity: int = Field(default=1, ge=1)
    purpose: str
    existingRefIds: list[str] = Field(default_factory=list)
    occupancyPolicy: Literal["exclusive", "co_located_with_reference"] = "exclusive"
    conflicts: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class ProposedLayoutConnection(BaseModel):
    connectionId: str
    fromId: str
    toId: str
    networkType: str
    edgeType: str
    direction: str
    points: list[LayoutPosition] = Field(min_length=2)
    status: AuditStatus
    capacity: int = Field(default=1, ge=1)
    handoffAction: str | None = None
    heightTransition: dict[str, Any] | None = None
    requiredCellIds: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class LayoutRoute(BaseModel):
    routeId: str
    side: Literal["left", "right", "shared"]
    optionId: str
    status: AuditStatus
    facilityIds: list[str] = Field(default_factory=list)
    connectionIds: list[str] = Field(default_factory=list)
    blockingReasons: list[str] = Field(default_factory=list)


class LayoutOption(BaseModel):
    optionId: str
    label: str
    status: AuditStatus
    description: str
    facilityIds: list[str] = Field(default_factory=list)
    connectionIds: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    blockingReasons: list[str] = Field(default_factory=list)


class PickingArmEndpoint(BaseModel):
    nodeId: str
    topologyCellId: str | None = None
    position: LayoutPosition
    beltHeight: float = Field(ge=0)
    deckIndex: int | None = Field(default=None, ge=1)
    direction: str | None = None
    provenance: Literal["topology", "proposed"]
    validationStatus: AuditStatus
    evidence: list[str] = Field(default_factory=list)


class PickingArmConveyorStep(BaseModel):
    cellId: str
    position: LayoutPosition
    direction: str
    beltHeight: float = Field(ge=0)
    deviceIds: list[str] = Field(default_factory=list)
    transitionToNext: Literal["conveyor", "device_transfer", "endpoint"] = "conveyor"
    transferDeviceId: str | None = None


class PickingArmKinematics(BaseModel):
    upperArmLength: float = Field(gt=0)
    forearmLength: float = Field(gt=0)
    shoulderHeight: float = Field(gt=0)
    baseRadius: float = Field(gt=0)
    upperArmThickness: float = Field(gt=0)
    forearmThickness: float = Field(gt=0)
    jointRadius: float = Field(gt=0)
    gripperScale: float = Field(gt=0)
    safeTravelHeight: float = Field(gt=0)
    linkCollisionRadius: float = Field(gt=0)


class PickingArmLayout(BaseModel):
    armId: str
    status: Literal["proposed"] = "proposed"
    validationStatus: AuditStatus
    installation: LayoutPosition
    mountHeight: float = Field(default=0, ge=0)
    workRadius: float = Field(gt=0)
    kinematics: PickingArmKinematics
    pickup: PickingArmEndpoint
    dropoff: PickingArmEndpoint
    liftBId: str
    liftBPosition: LayoutPosition
    liftBClearanceCells: float = Field(gt=0)
    downstreamCellId: str
    downstreamStatus: AuditStatus
    previewConveyorPath: list[PickingArmConveyorStep] = Field(default_factory=list)
    previewEndpointId: str
    previewPathStatus: AuditStatus
    previewStopReason: str
    previewTransferDeviceId: str | None = None
    previewTransferStatus: AuditStatus | None = None
    previewTransferEvidence: list[str] = Field(default_factory=list)
    actionSequence: list[str]
    requiredAuthoritativeSignals: list[str]
    canDeclareDelivery: bool = False
    evidence: list[str] = Field(default_factory=list)


class TransportLayoutPlanResponse(BaseModel):
    planId: str
    schemaVersion: str
    topologyId: str
    topologyRevision: str
    overallStatus: AuditStatus
    recommendedOptionId: str
    pickingArm: PickingArmLayout | None = None
    references: list[LayoutReferenceNode]
    facilities: list[ProposedLayoutFacility]
    connections: list[ProposedLayoutConnection]
    routes: list[LayoutRoute]
    options: list[LayoutOption]
    issues: list[NetworkIssue]
    summary: dict[str, Any]
    assumptions: list[str] = Field(default_factory=list)


TopologyPayload.model_rebuild()
