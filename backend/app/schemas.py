from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ZoneType = Literal["ctu", "shuttle", "port", "pick_station", "drop_zone"]
CellType = Literal["road", "blocked", "conveyor"]
ConveyorDirection = Literal["right", "left", "up", "down"]
ConveyorSide = Literal["north", "east", "south", "west"]
NodeType = Literal["storage", "ctu_robot", "shuttle_robot", "lift_transfer", "turn_transfer", "shuttle_dock"]
SimulationStatus = Literal["idle", "running", "paused", "stopped", "completed"]
ArmPreviewStatus = Literal["PASS", "PARTIAL", "BLOCKED"]


class ConveyorBinding(BaseModel):
    blockId: str
    side: ConveyorSide


class Zone(BaseModel):
    id: str
    label: str
    type: ZoneType
    x: int
    y: int
    width: int
    height: int
    conveyorBinding: ConveyorBinding | None = None
    slotAisles: dict[str, int] | None = None
    removedSlots: list[str] | None = None
    slotLifts: list[str] | None = None


class GridConfig(BaseModel):
    rows: int = 12
    cols: int = 20
    cellSize: int = 32
    originX: int = 0
    originY: int = 0


class ConveyorDeckConfig(BaseModel):
    enabled: bool = True
    inputSide: ConveyorSide
    outputSide: ConveyorSide
    role: Literal["outbound", "return", "custom"] = "custom"
    speed: float = Field(default=1.0, gt=0)
    capacity: int = Field(default=1, ge=1)


class ConveyorDecks(BaseModel):
    lower: ConveyorDeckConfig
    upper: ConveyorDeckConfig | None = None


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
    conveyorDecks: ConveyorDecks | None = None
    conveyorMode: Literal["single", "round_trip", "independent"] | None = None
    conveyorLineId: str | None = None
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
    # 转向移载机只作用于指定输送层，并独立保存手动入口/出口。
    transferDeck: Literal["lower", "upper"] | None = None
    transferInputSide: ConveyorSide | None = None
    transferOutputSide: ConveyorSide | None = None
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


class LayoutPosition(BaseModel):
    floor: str = "1F"
    row: int
    col: int
    level: int = Field(default=1, ge=1)


class PickingArmEndpoint(BaseModel):
    nodeId: str
    topologyCellId: str
    position: LayoutPosition
    beltHeight: float = Field(ge=0)
    deckIndex: int = Field(ge=1)
    direction: ConveyorDirection
    provenance: Literal["topology", "proposed"]
    validationStatus: ArmPreviewStatus
    evidence: list[str] = Field(default_factory=list)


class PickingArmConveyorStep(BaseModel):
    cellId: str
    position: LayoutPosition
    direction: ConveyorDirection
    beltHeight: float = Field(ge=0)
    deviceIds: list[str] = Field(default_factory=list)
    transitionToNext: Literal["conveyor", "device_transfer", "endpoint"] = "conveyor"
    transferDeviceId: str | None = None
    directionSource: Literal["topology"] = "topology"


class RightWarehouseMergePoint(BaseModel):
    mergePointId: str
    topologyNodeId: str
    row: int
    col: int
    level: int = Field(ge=1)
    capacity: int = Field(ge=1)
    occupiedCargoIds: list[str] = Field(default_factory=list)
    acceptedCargoIds: list[str] = Field(default_factory=list)
    status: Literal["available", "waiting", "full", "blocked"] = "available"
    acceptedWarehouse: Literal["right"] = "right"
    cargoOrderIds: dict[str, str] = Field(default_factory=dict)


class PickingArmTransportCargo(BaseModel):
    orderId: str
    cargoId: str
    skuId: str
    quantityCases: int = Field(gt=0)
    unitsPerCase: int = Field(gt=0)
    quantityBase: int = Field(gt=0)
    sourceLocationId: str
    currentNodeId: str
    targetMergePointId: str
    transportStatus: Literal[
        "queued",
        "arm_transfer",
        "conveyor_moving",
        "waiting_at_merge",
        "arrived",
        "rejected",
    ]
    sourceWarehouse: Literal["right"] = "right"

    @model_validator(mode="after")
    def validate_quantity_contract(self) -> "PickingArmTransportCargo":
        if self.quantityBase != self.quantityCases * self.unitsPerCase:
            raise ValueError("quantityBase must equal quantityCases * unitsPerCase")
        return self


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
    validationStatus: ArmPreviewStatus
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
    downstreamStatus: ArmPreviewStatus
    previewConveyorPath: list[PickingArmConveyorStep] = Field(default_factory=list)
    previewEndpointId: str
    previewPathStatus: ArmPreviewStatus
    previewStopReason: str
    mergePoint: RightWarehouseMergePoint
    actionSequence: list[str]
    requiredAuthoritativeSignals: list[str]
    canDeclareDelivery: bool = False
    evidence: list[str] = Field(default_factory=list)


class TransportLayoutPlanResponse(BaseModel):
    planId: str
    schemaVersion: str
    topologyId: str
    topologyRevision: str
    overallStatus: ArmPreviewStatus
    pickingArm: PickingArmLayout
    summary: dict[str, Any]


class SimulationControlResponse(BaseModel):
    runId: str
    status: SimulationStatus


class SimulationTestStartRequest(BaseModel):
    topologyId: str = Field(min_length=1)
    topologyRevision: str = Field(min_length=1)
    scenarioId: str = Field(min_length=1)


class SimulationTestControlResponse(SimulationControlResponse):
    runMode: Literal["simulation_test"]
    scenarioId: str


class SimulationInventoryOverride(BaseModel):
    inventoryRecordId: str = Field(min_length=1)
    quantityCases: int = Field(ge=0, le=1_000_000)


class SimulationSpeedRequest(BaseModel):
    multiplier: Literal[1, 2, 4]


class SimulationSpeedResponse(SimulationControlResponse):
    speedMultiplier: Literal[1, 2, 4]


class SimulationOrderLineInput(BaseModel):
    id: str
    skuId: str
    skuName: str
    spec: str | None = None
    quantity: int | None = Field(default=None, ge=1, le=20)
    quantityBase: int | None = Field(default=None, ge=1, le=1_000_000)
    unitsPerCase: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_quantity_mode(self) -> "SimulationOrderLineInput":
        if self.quantity is None and self.quantityBase is None:
            self.quantity = 1
        elif self.quantity is not None and self.quantityBase is not None:
            raise ValueError("quantity and quantityBase cannot both be supplied")
        if self.quantity is not None and self.unitsPerCase is not None:
            raise ValueError("unitsPerCase is only valid for quantityBase order lines")
        return self


class SimulationOrderInput(BaseModel):
    id: str
    priority: Literal["normal", "urgent"] = "normal"
    dispatchMode: Literal["compact", "balanced", "parallel"] = "balanced"
    maxParallelCtu: int = Field(default=7, ge=1, le=7)
    targetStation: str = "出库口"
    createdAt: str
    lines: list[SimulationOrderLineInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_line_quantity_mode(self) -> "SimulationOrderInput":
        modes = {"base" if line.quantityBase is not None else "legacy" for line in self.lines}
        if len(modes) != 1:
            raise ValueError("an order cannot mix legacy case lines with quantityBase lines")
        return self


class GeneralSimulationPlanRequest(BaseModel):
    topologyId: str = Field(min_length=1)
    topologyRevision: str = Field(min_length=1)
    profileId: str = Field(min_length=1)
    orders: list[SimulationOrderInput] = Field(min_length=1)
    inventoryOverrides: list[SimulationInventoryOverride] = Field(default_factory=list)


class GeneralSimulationControlResponse(SimulationControlResponse):
    runMode: Literal["simulation"]
    profileId: str


class RightCasePreparationTask(BaseModel):
    orderId: str
    taskId: str
    cargoId: str
    skuId: str
    sourceWarehouse: Literal["right"]
    sourceLocationId: str
    destinationHandoffId: str
    quantityCases: int = Field(gt=0)
    quantityBase: int = Field(gt=0)
    status: Literal["waiting_pick"]
    authoritative: Literal[False] = False


class LeftBreakPackPreparationTask(BaseModel):
    orderId: str
    taskId: str
    skuId: str
    sourceWarehouse: Literal["left"]
    sourceLocationId: str
    sourceQuantityBase: int = Field(gt=0)
    pickQuantityBase: int = Field(gt=0)
    returnQuantityBase: int = Field(ge=0)
    status: Literal["waiting_split"]
    authoritative: Literal[False] = False


class OrderLineAllocation(BaseModel):
    orderId: str
    lineId: str
    skuId: str
    quantityBase: int = Field(gt=0)
    unitsPerCase: int = Field(gt=0)
    rightQuantityCases: int = Field(ge=0)
    rightQuantityBase: int = Field(ge=0)
    leftSourceCases: int = Field(ge=0)
    leftSourceQuantityBase: int = Field(ge=0)
    leftPickQuantityBase: int = Field(ge=0)
    leftReturnQuantityBase: int = Field(ge=0)
    allocationStatus: Literal["ready", "shortage"]
    shortageQuantityBase: int = Field(ge=0)
    inventorySufficient: bool
    packagingSource: str
    packagingVerificationStatus: str
    rightTasks: list[RightCasePreparationTask] = Field(default_factory=list)
    leftTasks: list[LeftBreakPackPreparationTask] = Field(default_factory=list)


class OrderFulfillmentPlanResponse(BaseModel):
    orderId: str
    allocationStatus: Literal["ready", "shortage"]
    allocations: list[OrderLineAllocation]
    tasks: list[dict[str, Any]]
    inventoryMutated: Literal[False] = False
    inventoryReserved: Literal[False] = False
    previewCargoAccepted: Literal[False] = False
    canDeclareCompleted: Literal[False] = False
    requestFingerprint: str


class SimulationOrderCancelRequest(BaseModel):
    reason: str = Field(default="用户取消", min_length=1, max_length=200)


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
