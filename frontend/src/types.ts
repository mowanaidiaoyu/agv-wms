export type ZoneType = 'ctu' | 'shuttle' | 'port' | 'pick_station' | 'drop_zone'
export type CellType = 'road' | 'blocked' | 'conveyor'
export type ConveyorDirection = 'right' | 'left' | 'up' | 'down'
export type ConveyorSide = 'north' | 'east' | 'south' | 'west'
export type ConveyorDeckKey = 'lower' | 'upper'
export type ConveyorDeckRole = 'outbound' | 'return' | 'custom'
export type ConveyorRunMode = 'single' | 'round_trip' | 'independent'

export interface ConveyorDeckConfig {
  enabled: boolean
  inputSide: ConveyorSide
  outputSide: ConveyorSide
  role: ConveyorDeckRole
  speed?: number
  capacity?: number
}

export interface ConveyorDecks {
  lower: ConveyorDeckConfig
  upper?: ConveyorDeckConfig
}
export type NodeType = 'storage' | 'ctu_robot' | 'shuttle_robot' | 'lift_transfer' | 'turn_transfer' | 'shuttle_dock'
export type EditorObjectType =
  | 'select'
  | 'batch'
  | 'erase'
  | 'road'
  | 'blocked'
  | 'conveyor'
  | 'ground_conveyor'
  | 'ctu_robot'
  | 'shuttle_robot'
  | 'lift_transfer'
  | 'turn_transfer'
  | 'shuttle_dock'
export type RobotType = 'ctu' | 'shuttle'

export interface Zone {
  id: string
  label: string
  type: ZoneType
  floor?: string
  x: number
  y: number
  width: number
  height: number
  /** 业务区域与输送线端点的固定连接；避免运行时再按最近距离猜测。 */
  conveyorBinding?: {
    blockId: string
    side: ConveyorSide
  } | null
  /** 四向车库每层巷道位掩码：`"行,列"` → bit0..bit3 对应货架层 1..4 是否为巷道 */
  slotAisles?: Record<string, number>
  /** 四向车库被删除的 2×2 槽位（`"行,列"` 列表）：该位置不再渲染货架/巷道，视为空位 */
  removedSlots?: string[]
  /** 四向车库「升降通道（电梯）」2×2 槽位（`"行,列"` 列表）：四向车跨层必须经过该竖井 */
  slotLifts?: string[]
}

export interface GridConfig {
  rows: number
  cols: number
  cellSize: number
  originX: number
  originY: number
}

export interface TopologyCell {
  id: string
  floor?: string
  row: number
  col: number
  type: CellType
  zoneId?: string | null
  cost: number
  walkable: boolean
  conveyorDirection?: ConveyorDirection | null
  /** 上下层独立的物料入口/出口。只在 2×2 块锚点格保存，其余格通过 conveyorAnchor 解析。 */
  conveyorDecks?: ConveyorDecks | null
  conveyorMode?: ConveyorRunMode | null
  /** 物理输送线路标识。相邻但不同线路的块不会因为坐标接触而被误连接。 */
  conveyorLineId?: string | null
  levels?: number
  /** 所属 2×2 输送带块的左上锚点（奇数起点支持）。缺失时回退到 floor-到-偶。 */
  conveyorAnchor?: { row: number; col: number } | null
  /** 地面输送线（整箱，四向车出货用）：贴地单层带面；缺省为架空输送线（零散件，CTU 用） */
  conveyorGround?: boolean
}

export interface TopologyNode {
  id: string
  type: NodeType
  floor?: string
  row: number
  col: number
  zoneId?: string | null
  capacity: number
  label: string
  /** 转向移载机只安装在一个输送层；下层输送带不会因上层设备而被覆盖。 */
  transferDeck?: ConveyorDeckKey | null
  /** 转向移载机在所选层的手动入口和出口方向。 */
  transferInputSide?: ConveyorSide | null
  transferOutputSide?: ConveyorSide | null
  /** 四向车出货位（shuttle_dock）：取什么货物（商品目录 skuId） */
  skuId?: string | null
  skuName?: string | null
  largeCategory?: string | null
  /** 四向车出货位：取多少（箱，每箱一趟搬运任务） */
  quantity?: number | null
}

export interface TopologyPayload {
  id: string
  name: string
  meta: {
    version: string
    viewport: { x: number; y: number; zoom: number }
    tags: string[]
    floors?: string[]
  }
  grid: GridConfig
  zones: Zone[]
  cells: TopologyCell[]
  nodes: TopologyNode[]
}

export type ArmPreviewStatus = 'PASS' | 'PARTIAL' | 'BLOCKED'

export interface LayoutPosition {
  floor: string
  row: number
  col: number
  level: number
}

export interface PickingArmEndpoint {
  nodeId: string
  topologyCellId: string
  position: LayoutPosition
  beltHeight: number
  deckIndex: number
  direction: ConveyorDirection
  provenance: 'topology' | 'proposed'
  validationStatus: ArmPreviewStatus
  evidence: string[]
}

export interface PickingArmConveyorStep {
  cellId: string
  position: LayoutPosition
  direction: ConveyorDirection
  beltHeight: number
  deviceIds: string[]
  transitionToNext: 'conveyor' | 'device_transfer' | 'endpoint'
  transferDeviceId?: string | null
  directionSource: 'topology'
}

export interface RightWarehouseMergePoint {
  mergePointId: string
  topologyNodeId: string
  row: number
  col: number
  level: number
  capacity: number
  occupiedCargoIds: string[]
  acceptedCargoIds: string[]
  status: 'available' | 'waiting' | 'full' | 'blocked'
  acceptedWarehouse: 'right'
  cargoOrderIds: Record<string, string>
}

export interface PickingArmTransportCargo {
  orderId: string
  cargoId: string
  skuId: string
  quantityCases: number
  unitsPerCase: number
  quantityBase: number
  sourceLocationId: string
  currentNodeId: string
  targetMergePointId: string
  transportStatus: 'queued' | 'arm_transfer' | 'conveyor_moving' | 'waiting_at_merge' | 'arrived' | 'rejected'
  sourceWarehouse: 'right'
}

export interface PickingArmKinematics {
  upperArmLength: number
  forearmLength: number
  shoulderHeight: number
  baseRadius: number
  upperArmThickness: number
  forearmThickness: number
  jointRadius: number
  gripperScale: number
  safeTravelHeight: number
  linkCollisionRadius: number
}

export interface PickingArmLayout {
  armId: string
  status: 'proposed'
  validationStatus: ArmPreviewStatus
  installation: LayoutPosition
  mountHeight: number
  workRadius: number
  kinematics: PickingArmKinematics
  pickup: PickingArmEndpoint
  dropoff: PickingArmEndpoint
  liftBId: string
  liftBPosition: LayoutPosition
  liftBClearanceCells: number
  downstreamCellId: string
  downstreamStatus: ArmPreviewStatus
  previewConveyorPath: PickingArmConveyorStep[]
  previewEndpointId: string
  previewPathStatus: ArmPreviewStatus
  previewStopReason: string
  mergePoint: RightWarehouseMergePoint
  actionSequence: string[]
  requiredAuthoritativeSignals: string[]
  canDeclareDelivery: boolean
  evidence: string[]
}

export interface TransportLayoutPlan {
  planId: string
  schemaVersion: string
  topologyId: string
  topologyRevision: string
  overallStatus: ArmPreviewStatus
  pickingArm: PickingArmLayout
  summary: {
    previewOnly: boolean
    inventoryMutated: boolean
    ordersCompleted: boolean
    formalTopologyMutated: boolean
    conveyorNodeCount: number
  }
}

export interface StoredRecord<T> {
  id: string
  name: string
  payload: T
  created_at?: string
  updated_at?: string
}

export interface SimulationMetrics {
  throughput: number
  completedTasks: number
  pendingTasks: number
  activeRobots: number
  blockedRobots: number
  conveyorUtilization: number
  deadlocksDetected: number
  congestionEvents: number
  urgentOrdersHandled: number
  avgEtaTicks: number
  avgCompletionTicks: number
  stationQueueDepth: number
  conveyorQueueDepth: number
  bufferOccupancy: number
}

export interface SimulationRobot {
  id: string
  type: RobotType
  currentNode: string
  currentCellId: string
  currentFloor?: string
  currentRow: number
  currentCol: number
  /** 四向车当前所在货架层（1..4），CTU 恒为 1 */
  currentLayer?: number
  status: string
  battery: number
  path: string[]
  /** 与 path 等长的每格所在货架层 */
  pathLayers?: number[]
  pathStages?: string[]
  targetCellId: string
  targetLayer?: number
  currentTaskId: string | null
  waitTicks: number
  lastCellChangeTick?: number
  blockedByRobotId?: string | null
  deadlockGroupId?: string | null
  recoveryCount?: number
  activePortId?: string | null
  failed: boolean
  phaseLabel: string
  load: number
  capacity: number
  /** CTU 当前装载的货物箱（取货后从库位移到车上，交付后清空） */
  carrying?: GoodsSlot[]
  /** CTU 到达库位/出入口后的升降、伸叉和装箱动作；仅动作期间存在。 */
  handlingAction?: {
    type: 'pickup' | 'deliver'
    stage: 'lifting' | 'extending' | 'stowing'
    targetLayer: number
    slotIndex: number
    nodeId?: string
    cellId?: string
    approachDirection?: 'north' | 'south' | 'east' | 'west'
    skuId?: string
    skuName?: string
    verified?: boolean
  } | null
}

export interface SimulationTaskHistory {
  tick: number
  phase: string
  status: string
}

export interface SimulationTaskLine {
  nodeId: string
  cellId: string
  layer: number
  skuId?: string
  skuName?: string
  largeCategory?: string
  subclass?: string
  spec?: string
  grade?: string
  material?: string
  surfaceTreatment?: string
  quantity?: number
}

/** 四向车出货位运行时设置（可在「四向车搬运」工作台调整，存在 Run 状态里，不改拓扑）。 */
export interface ShuttleDockState {
  dockId: string
  label: string
  row: number
  col: number
  floor: string
  skuId?: string | null
  skuName?: string | null
  largeCategory?: string | null
  quantity: number
  priority: 'normal' | 'urgent'
  vehicleId?: string | null
  loop: boolean
  enabled: boolean
  cursor?: number
}

export interface ShuttleTrackNode {
  topology_id: string
  zone_id: string
  track_node_id: string
  shuttle_level: 1 | 2 | 3 | 4
  row: number
  col: number
  world_x: number
  world_y: number
  world_z: number
  track_type: 'straight' | 'intersection' | 'turn' | 'lift_entry' | 'lift_exit'
  traversable: boolean
  neighbors: string[]
}

export interface ShuttleLiftTransition {
  lift_id: string
  from_level: number
  to_level: number
  entrance_track_node: string
  exit_track_node: string
  enter_at_seconds: number
  lift_start_seconds: number
  arrive_level_seconds: number
  exit_at_seconds: number
  events: string[]
}

export interface ShuttlePlan {
  topologyId: string
  topologyRevision: string
  configRevision: string
  coordinateContractRevision: string
  algorithm: 'astar' | 'dijkstra'
  chosen_lift: string | null
  path_nodes: Array<ShuttleTrackNode & { arrival_seconds: number; phase: string }>
  path_edges: Array<{
    from_track_node: string
    to_track_node: string
    edge_type: 'track' | 'lift'
    lift_id?: string | null
    duration_seconds: number
  }>
  lift_transitions: ShuttleLiftTransition[]
  estimated_duration_seconds: number
  result_status: 'success' | 'failed'
  failure_reason?: string | null
  timing_source: 'synthetic_demo'
}

export interface ShuttleContract {
  topologyId: string
  topologyRevision: string
  configRevision: string
  coordinateContractRevision: string
  verificationStatus: string
  planningEnabled: boolean
  tracks: ShuttleTrackNode[]
  lifts: Array<{
    lift_id: string
    label: string
    row: number
    col: number
    supported_levels?: number[]
    verification_status: string
    timing_source: 'synthetic_demo'
  }>
  warnings: string[]
}

export interface ShuttleTransferInput {
  id: string
  vehicleId: string
  skuId: string
  skuName: string
  sourceLocationId: string
  destinationLocationId: string
  sourceTrackNode: string
  destinationTrackNode: string
  sourceLevel: 1 | 2 | 3 | 4
  destinationLevel: 1 | 2 | 3 | 4
  quantity: number
  algorithm: 'astar' | 'dijkstra'
  initialSourceQuantity: number
  initialDestinationQuantity: number
}

export interface TransferInventoryBalance {
  locationId: string
  trackNodeId: string
  level: number
  row: number
  col: number
  worldX: number
  worldY: number
  worldZ: number
  skuId: string
  skuName: string
  quantityOnHand: number
  quantityReserved: number
  quantityAvailable: number
  dataSource: 'run_isolated_simulation_snapshot'
}

export interface TransferInventoryEvent {
  eventId: string
  taskId: string
  tick: number
  eventType: 'reserve' | 'pick' | 'drop' | 'cancel_release' | 'cancel_return'
  locationId: string | null
  skuId: string
  quantity: number
  onHandDelta: number
  reservedDelta: number
  inTransitDelta: number
  quantityOnHandAfter?: number | null
  quantityReservedAfter?: number | null
  inTransitAfter?: number
}

export interface SimulationTask {
  id: string
  orderId?: string | null
  dockId?: string | null
  requestedVehicleId?: string | null
  robotType: RobotType
  mode: 'direct' | 'cross_zone' | 'shuttle_transfer'
  status: string
  phase: string
  phaseLabel: string
  sourceCellId: string
  sourceLayer: number
  /** 原始库位节点（货物拣出前的 nodeId，回库时放回同一库位） */
  sourceNodeId?: string | null
  targetCellId: string
  targetLayer: number
  /** 交付点（输送线出入口入口格），等于任务最终 targetCellId */
  deliveryCellId: string
  lines: SimulationTaskLine[]
  /** 实际交付到输送线上的货箱（用于按实载箱数渲染） */
  deliveredGoods?: SimulationTaskLine[]
  /** 转运中的货箱（人工拣货流程） */
  transitGoods?: SimulationTaskLine[]
  currentLineIndex: number
  picked: number
  priority: number
  etaTicks: number
  urgent: boolean
  assignedRobotId: string | null
  taskIndex?: number
  splitStrategy?: SimulationOrderDispatchMode
  sourceGroupLabel?: string
  assignedPortId?: string
  assignedPortLabel?: string
  portQueuePosition?: number
  portSelectionCost?: number
  portSelectionReason?: string
  lastProgressTick?: number
  deadlockGroupId?: string | null
  recoveryCount?: number
  remainingTicks: number
  /** 人工拣货位入口格（拓扑有 pick_station 时才有） */
  pickStationCellId?: string | null
  pickStationZoneId?: string | null
  /** 放置区入口格（拣货结束后把拣出货物放到 drop_zone 时才有） */
  dropZoneCellId?: string | null
  dropZoneZoneId?: string | null
  /** 拣出并放到放置区的货物快照（place_goods 结束后生成，供前端渲染放置区货物） */
  placedGoods?: SimulationTaskLine[]
  /** 人工拣货闭环各计时阶段的统一剩余 tick（下层输送/升降/上层输送/拣货/放置/回程共用） */
  remainingStageTicks?: number
  conveyorTransport?: {
    phase: string
    deck: ConveyorDeckKey
    route: string[]
    index: number
    currentBlockId?: string | null
    nextBlockId?: string | null
    waitingTicks: number
    movedTicks: number
    startedTick: number
  } | null
  /** 回库时正在放置的订单行序号 */
  returnLineIndex?: number
  history: SimulationTaskHistory[]
  createdTick: number
  startedTick: number | null
  completedTick: number | null
  skuId?: string
  skuName?: string
  quantity?: number
  sourceLocationId?: string
  destinationLocationId?: string
  sourceTrackNode?: string
  destinationTrackNode?: string
  inTransitQuantity?: number
  plan?: ShuttlePlan
  failureReason?: string | null
}

export type SimulationOrderPriority = 'normal' | 'urgent'
export type SimulationOrderDispatchMode = 'compact' | 'balanced' | 'parallel'
export type SimulationOrderStatus =
  | 'ready'
  | 'waiting_dispatch'
  | 'executing'
  | 'returning'
  | 'cancelled'
  | 'completed'
  | 'shortage'
  | 'exception'

export interface SimulationOrderLineInput {
  id: string
  skuId: string
  skuName: string
  spec?: string | null
  quantity?: number
  quantityBase?: number
  unitsPerCase?: number
}

export interface RightCasePreparationTask {
  orderId: string
  taskId: string
  cargoId: string
  skuId: string
  sourceWarehouse: 'right'
  sourceLocationId: string
  destinationHandoffId: string
  quantityCases: number
  quantityBase: number
  status: 'waiting_pick' | 'shuttle_picking' | 'shuttle_transporting' | 'lift_waiting' | 'lift_moving' | 'shuttle_to_handoff' | 'waiting_dock' | 'dock_transferring' | 'conveyor_transporting' | 'waiting_arm' | 'arm_processing' | 'merge_conveyor_transporting' | 'merge_arrived' | 'cancelled'
  authoritative: false
  currentNodeId?: string
  currentDeviceId?: string | null
  simulationStateAuthoritative?: boolean
  preview?: false
}

export interface LeftBreakPackPreparationTask {
  orderId: string
  taskId: string
  skuId: string
  sourceWarehouse: 'left'
  sourceLocationId: string
  sourceQuantityBase: number
  pickQuantityBase: number
  returnQuantityBase: number
  status: 'waiting_split' | 'waiting_pick' | 'ctu_picking' | 'ctu_transporting' | 'station_receiving' | 'break_pack_opening' | 'break_pack_picking' | 'break_pack_confirming' | 'waiting_return' | 'ctu_returning' | 'returning_to_storage' | 'returned' | 'cancelled'
  authoritative: false
}

export interface OrderLineAllocation {
  orderId: string
  lineId: string
  skuId: string
  quantityBase: number
  unitsPerCase: number
  rightQuantityCases: number
  rightQuantityBase: number
  leftSourceCases: number
  leftSourceQuantityBase: number
  leftPickQuantityBase: number
  leftReturnQuantityBase: number
  allocationStatus: 'ready' | 'shortage'
  shortageQuantityBase: number
  inventorySufficient: boolean
  packagingSource: string
  packagingVerificationStatus: string
  rightTasks: RightCasePreparationTask[]
  leftTasks: LeftBreakPackPreparationTask[]
}

export interface OrderFulfillmentPlan {
  orderId: string
  allocationStatus: 'ready' | 'shortage'
  allocations: OrderLineAllocation[]
  tasks: Array<RightCasePreparationTask | LeftBreakPackPreparationTask>
  inventoryMutated: false
  inventoryReserved: false
  previewCargoAccepted: false
  canDeclareCompleted: false
  requestFingerprint: string
}

export interface SimulationOrderInput {
  id: string
  priority: SimulationOrderPriority
  dispatchMode?: SimulationOrderDispatchMode
  maxParallelCtu?: number
  targetStation: string
  createdAt: string
  lines: SimulationOrderLineInput[]
  fulfillmentPlan?: OrderFulfillmentPlan
}

export interface SimulationOrder extends SimulationOrderInput {
  status: SimulationOrderStatus
  statusLabel: string
  taskIds: string[]
  totalTasks: number
  completedTasks: number
  progress: number
  missingQuantity?: number
  startedTick?: number | null
  completedTick?: number | null
  cancelRequested?: boolean
  cancelRequestedTick?: number | null
  cancelledTick?: number | null
  cancelReason?: string | null
  decomposition?: {
    stage: 'inventory_matched' | 'tasks_generated' | 'tasks_prepared' | 'shortage' | 'dispatching' | 'executing' | 'waiting_handoff' | 'returning' | 'cancelled' | 'completed' | 'exception'
    requestedQuantity: number
    allocatedQuantity: number
    missingQuantity: number
    taskCount: number
    assignedTaskCount: number
    activeVehicleIds: string[]
    vehicleCapacity: number
    strategy: SimulationOrderDispatchMode
    maxParallelCtu: number
  }
  lines: Array<SimulationOrderLineInput & { allocated?: number; picked?: number }>
}

export interface GoodsCatalogSku {
  skuId: string
  name: string
  spec?: string | null
  grade?: string | null
  material?: string | null
  largeCategory?: string | null
  subclass?: string | null
  unitsPerCase?: number | null
  packagingSource?: string | null
  verificationStatus?: 'verified' | 'human_verified' | 'simulation_test_confirmed' | string | null
  packagingScope?: 'formal' | 'simulation_test' | string | null
}

export interface GoodsCatalog {
  version: number
  skus: GoodsCatalogSku[]
}

export interface PackagingSpecificationRecord {
  skuId: string
  unitsPerCase: number
  packagingSource: string
  verificationStatus: string
  scope: 'formal' | 'simulation_test' | 'simulation' | string
  dataScope?: 'formal' | 'simulation_test' | 'simulation' | string
  baseUom?: string
  caseUom?: string
  scenarioId?: string
  profileId?: string
  verifiedAt?: string
  notes?: string
}

export interface SimulationInventoryRecord {
  inventoryRecordId: string
  sourceWarehouse: 'left' | 'right'
  sourceLocationId: string
  locationType: 'ctu_storage' | 'shuttle_storage'
  skuId: string
  quantityCases: number
  dataSource: 'simulation'
  verificationStatus: string
  topologyMapping: Record<string, string | number>
}

export interface SimulationInventoryProfile {
  schemaVersion: string
  profileId: string
  name: string
  dataSource: 'simulation'
  isolationMode: 'run_local_snapshot'
  topologyId: string
  topologyRevision: string
  inventory: SimulationInventoryRecord[]
  packaging: PackagingSpecificationRecord[]
  formalInventoryMutated: false
}

export interface GeneralSimulationAudit {
  profileId: string
  scenarioId: string
  status: 'PASS' | 'SHORTAGE'
  allocationStatus: 'ready' | 'shortage'
  runMode: 'simulation'
  dataSource: 'simulation'
  isolationMode: 'run_local_snapshot'
  topologyId: string
  topologyRevision: string
  supportedSkus: PackagingSpecificationRecord[]
  inventory: SimulationInventoryRecord[]
  orders: SimulationOrderInput[]
  plans: OrderFulfillmentPlan[]
  plan: OrderFulfillmentPlan
  formalInventoryMutated: false
}

export interface PackagingSpecificationRegistry {
  schemaVersion: string
  records: PackagingSpecificationRecord[]
}

export interface FulfillmentTestScenarioAudit {
  scenarioId: string
  status: 'PASS' | 'BLOCKED'
  dataSource: 'simulation_test'
  isolationMode: 'run_local_snapshot'
  topologyId: string
  topologyRevision: string
  sku: PackagingSpecificationRecord
  inventory: Array<{
    inventoryRecordId: string
    sourceWarehouse: 'left' | 'right'
    sourceLocationId: string
    locationType: 'ctu_storage' | 'shuttle_storage'
    skuId: string
    quantityCases: number
    dataSource: 'simulation_test'
    verificationStatus: string
    topologyMapping: Record<string, string | number>
  }>
  locationEvidence: Record<string, unknown>
  order: SimulationOrderInput
  plan: OrderFulfillmentPlan
  rightHandoff: {
    formalRouteStatus: 'BLOCKED'
    simulationRouteStatus: 'PASS_SIMULATION_ONLY'
    formalBlockers: string[]
    existingDockAssessment: {
      deviceId: string
      validationStatus: 'blocked'
      reason: string
    }
    sourceTrackNodeId: string
    provenLiftExitTrackNodeId: string
    handoffTrackNodeId: string
    armReceiveNodeId: string
    shuttlePath: string[]
    egressPath: string[]
    chosenLift: string
    conveyorPath: string[]
    facilities: Array<{
      deviceId: string
      deviceType: string
      inputNodeId: string
      outputNodeId: string
      sourceLevel: number
      targetLevel: number
      capacity: number
      transferDuration: number
      dataSource: 'simulation_test'
      validationStatus: 'simulation_only'
      existingTopologyDeviceId?: string
      inputSide?: string
      outputSide?: string
      geometry: {
        floor: string
        beltHeight: number
        pathCells: Array<{ row: number; col: number }>
        worldPoints: Array<{ x: number; y: number; z: number }>
        visualStyle: string
      }
    }>
    authority: {
      stateOwner: 'SimulationService'
      requiresUnifiedTick: true
      previewSignalsAccepted: false
      animationCanCompleteBusinessTask: false
    }
    cargo: Array<{
      cargoId: string
      orderId: string
      taskId: string
      skuId: string
      quantityCases: number
      quantityBase: number
      sourceLocationId: string
      currentNodeId: string
      currentDeviceId: string | null
      destinationNodeId: string
      status: string
      dataSource: 'simulation_test'
      authoritative: false
      preview: false
    }>
    segments: Array<{
      segmentId: string
      validationStatus: 'physical_validated' | 'simulation_only' | 'blocked'
      reason: string
    }>
  }
  businessGuards: Record<string, boolean | string>
  knownPhysicalGaps: string[]
}

export interface SimulationAlert {
  category: string
  level: 'info' | 'warning' | 'error'
  message: string
  tick: number
  details: Record<string, unknown>
  suggestion: string
}

export interface SimulationQueues {
  conveyor: string[]
  station: string[]
  buffer: string[]
}

export interface GoodsSlot {
  nodeId: string
  cellId: string
  layer: number
  skuId?: string | null
  skuName?: string | null
  largeCategory?: string | null
  subclass?: string | null
  spec?: string | null
  grade?: string | null
  material?: string | null
  surfaceTreatment?: string | null
  qty?: number
  dataSource?: 'simulation_catalog_seed' | string
  authoritative?: boolean
}

export interface GoodsInventory {
  topologyId: string
  dataSource?: 'simulation_catalog_seed' | string
  authoritative?: boolean
  slots: GoodsSlot[]
}

export interface SimulationState {
  id: string
  status: 'idle' | 'running' | 'paused' | 'stopped' | 'completed'
  stopReason?: 'user' | 'safety_limit' | 'transfer_failed' | 'task_failed' | 'runtime_error' | 'simulation_test_waiting_arm' | 'simulation_test_merge_arrived' | 'simulation_test_quantity_accounted' | null
  runtimeError?: {
    type: string
    message: string
    tick: number
  } | null
  orderDriven?: boolean
  tick: number
  speedMultiplier?: 1 | 2 | 4
  tickIntervalSeconds?: number
  eventCount?: number
  topologyId: string
  topologyRevision?: string
  runMode?: 'simulation_test' | string
  scenarioId?: string
  dataSource?: 'simulation_test' | string
  simulationStateAuthoritative?: boolean
  robots: SimulationRobot[]
  tasks: SimulationTask[]
  fulfillmentTasks?: Array<RightCasePreparationTask | LeftBreakPackPreparationTask>
  orders?: SimulationOrder[]
  inventory?: GoodsSlot[]
  shuttleDocks?: ShuttleDockState[]
  transferInventory?: TransferInventoryBalance[]
  transferEvents?: TransferInventoryEvent[]
  alerts: SimulationAlert[]
  queues: SimulationQueues
  conveyorItems?: Array<{
    taskId: string
    orderId?: string | null
    deck: ConveyorDeckKey
    currentBlockId?: string | null
    nextBlockId?: string | null
    waitingTicks: number
    phase: string
    goodsCount: number
  }>
  simulationTest?: SimulationTestRunState
  trafficMonitor?: {
    waitForEdges: Array<{
      waiter: string
      owner: string
      resource: string
      kind: 'cell' | 'port'
    }>
    stalledTaskIds: string[]
    suspectedDeadlocks: Array<{
      id: string
      type: 'cyclic_wait' | 'livelock'
      robotIds: string[]
      taskIds: string[]
      firstTick: number
      lastTick: number
      consecutiveTicks: number
      level: 'suspected' | 'confirmed'
      reason: string
      recoveryStatus?: 'executing' | 'completed' | null
      priorityRobotId?: string
      actions?: Array<{
        robotId: string
        action: string
        cellId?: string
        taskId?: string
      }>
    }>
    activeRecoveries: Array<Record<string, unknown>>
  }
  createdAt: string
  updatedAt: string
}

export interface SimulationTestPosition {
  nodeId: string
  row?: number | null
  col?: number | null
  level: number
  worldX: number
  worldY: number
  worldZ: number
}

export interface SimulationTestShuttleFrame {
  cellId: string
  level: number
  stage: 'track' | 'enter_lift' | 'lift_moving' | 'exit_lift'
  nodeId: string
  position: SimulationTestPosition
  index: number
}

export interface SimulationTestCargoState {
  cargoId: string
  orderId: string
  taskId: string
  skuId: string
  quantityCases: number
  quantityBase: number
  sourceLocationId: string
  currentNodeId: string
  currentDeviceId: string | null
  destinationNodeId: string
  status: RightCasePreparationTask['status']
  dataSource: 'simulation_test'
  authoritative: false
  preview: false
  simulationStateAuthoritative: true
  previousPosition: SimulationTestPosition
  currentPosition: SimulationTestPosition
  positionTick: number
  conveyorIndex?: number | null
  mergeConveyorIndex?: number | null
  movementState?: {
    fromNodeId: string
    toNodeId: string
    segmentProgress: number
    movementDurationSeconds: number
    transportState: string
    tick: number
  }
  waitingTicks: number
  blockedReason?: string | null
}

export interface SimulationTestTransportEvent {
  runId: string
  tick: number
  eventId: string
  eventType: string
  orderId: string
  taskId: string
  cargoId: string
  fromNodeId: string | null
  toNodeId: string | null
  deviceId: string | null
  quantityBase: number
  dataSource: 'simulation_test'
}

export interface SimulationTestLeftCargoState {
  cargoId: string
  parentCargoId?: string
  orderId: string
  taskId: string
  skuId: string
  quantityCases: number
  quantityBase: number
  originalQuantityBase?: number
  pickedQuantityBase?: number
  remainingQuantityBase?: number
  sourceLocationId: string
  currentNodeId: string
  currentDeviceId: string | null
  status: string
  caseType: 'full_case' | 'partial_case' | 'empty_case'
  dataSource: 'simulation_test' | 'simulation'
  authoritative: false
  preview: false
  simulationStateAuthoritative: true
  currentPosition: SimulationTestPosition
  previousPosition: SimulationTestPosition
}

export interface SimulationTestLeftFlowState {
  status: string
  taskId: string
  pickQuantityBase?: number
  returnQuantityBase?: number
  sourceLocationId: string
  sourceCellId: string
  sourceStopCellId: string
  sourcePosition: SimulationTestPosition
  cargo: SimulationTestLeftCargoState
  partialCase: SimulationTestLeftCargoState | null
  ctu: {
    deviceId: string
    capacity: 1
    phase: string
    phaseTicks: number
    activeCargoId: string | null
    routeName: 'to_source' | 'to_station' | 'to_storage'
    routeIndex: number
    toSourceFrames: SimulationTestShuttleFrame[]
    toStationFrames: SimulationTestShuttleFrame[]
    toStorageFrames: SimulationTestShuttleFrame[]
    currentPosition: SimulationTestPosition
    dataSource: 'simulation_test' | 'simulation'
  }
  station: {
    deviceId: string
    deviceType: 'break_pack_station'
    positionCellId: string
    inputNodeId: string
    outputNodeId: string
    capacity: 1
    reservedCargoIds: string[]
    occupiedCargoIds: string[]
    phase: string
    phaseTicks: number
    position: SimulationTestPosition
    dataSource: 'simulation_test' | 'simulation'
    validationStatus: 'simulation_only'
  }
  ledger: {
    initialSourceQuantityBase: number
    sourceQuantityBase: number
    ctuQuantityBase: number
    stationQuantityBase: number
    pickedQuantityBase: number
    returnedQuantityBase: number
    returnApplied: boolean
  }
  quantityAudit: {
    sourceConserved: boolean
    orderQuantityAccounted: boolean
  }
}

export interface SimulationTestRunState {
  scenarioId: string
  runMode: 'simulation_test' | 'simulation'
  dataSource: 'simulation_test' | 'simulation'
  topologyRevision: string
  simulationStateAuthoritative: true
  formalRouteStatus: 'BLOCKED'
  simulationRouteStatus: 'PASS_SIMULATION_ONLY'
  handoffContract?: FulfillmentTestScenarioAudit['rightHandoff']
  syntheticTimingDisclosure: string
  order: {
    orderId: string
    quantityBase: number
    rightQuantityBase: number
    leftSourceQuantityBase: number
    leftPickQuantityBase: number
    leftReturnQuantityBase: number
    status: 'both_sides_transporting' | 'waiting_left_completion' | 'waiting_right_completion' | 'simulation_test_quantity_accounted' | 'right_transporting' | 'waiting_arm' | 'right_merge_arrived'
    canDeclareCompleted: false
    formalOrderCompleted?: false
    physicalDeliveryConfirmed?: false
    rightMergeAcceptedQuantityBase?: number
    leftPickedQuantityBase?: number
    leftReturnedQuantityBase?: number
    quantityAccounted?: number
  }
  inventory: {
    isolationMode: 'run_local_snapshot'
    initializedOnce: true
    right: {
      initialCases: number
      availableCases: number
      inTransitCases: number
      waitingArmCases: number
      mergeArrivedCases: number
    }
    left: { sourceLocationId: string | null; sourceQuantityBase: number; pickQuantityBase: number; returnQuantityBase: number; status: string; dataSource: 'simulation_test' | 'simulation' }
  }
  leftFlow?: SimulationTestLeftFlowState
  leftFlows?: SimulationTestLeftFlowState[]
  cargo: SimulationTestCargoState[]
  shuttle: {
    deviceId: string
    capacity: number
    occupiedCargoIds: string[]
    activeCargoId?: string | null
    phase: string
    routeIndex?: number
    outboundFrames?: SimulationTestShuttleFrame[]
    returnFrames?: SimulationTestShuttleFrame[]
    currentPosition?: SimulationTestPosition
  }
  lift: { deviceId: string; capacity: number; occupiedCargoIds: string[]; dataSource: string; validationStatus: 'simulation_only' }
  facilities: Array<FulfillmentTestScenarioAudit['rightHandoff']['facilities'][number] & {
    durationTicks: number
    occupiedCargoIds: string[]
    queueCargoIds: string[]
  }>
  conveyor: {
    path: string[]
    occupiedByBlock: Record<string, string | null>
    capacityPerBlock: 1
    beltHeight?: number
    gridRows: number
    gridCols: number
  }
  armInfeed: { nodeId: string; capacity: number; occupiedCargoIds: string[]; status: string }
  arm: {
    armId: string
    pickupNodeId: string
    dropoffNodeId: string
    capacity: 1
    phase: 'idle' | 'approach' | 'grip' | 'lift' | 'rotate' | 'place' | 'release' | 'return_home'
    phaseElapsedTicks: number
    phaseDurationTicks: number
    phaseProgress: number
    activeCargoId: string | null
    cargoPlacement: 'none' | 'pickup' | 'gripper'
    completedCargoIds: string[]
    dataSource: 'simulation_test'
    simulationStateAuthoritative: true
  }
  mergeConveyor: {
    path: Array<SimulationTestPosition & {
      deviceIds: string[]
      transferDeviceId?: string | null
    }>
    occupiedByNode: Record<string, string | null>
    capacityPerBlock: 1
  }
  mergePoint: {
    mergePointId: string
    topologyNodeId: string
    nodeId: string
    capacity: number
    acceptedCargoIds: string[]
    acceptedCount: number
    dataSource: 'simulation_test'
  }
  events: SimulationTestTransportEvent[]
  resetGeneration: number
  lastProgressTick: number
  blockedTicks: number
  diagnostics: Array<{ tick: number; reason: string; waitingCargoIds: string[] }>
}

export interface SimulationEvent {
  run_id?: string
  tick: number
  event_type?: string
  type?: string
  payload: Record<string, unknown>
  created_at?: string
}
