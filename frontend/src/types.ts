export type ZoneType = 'ctu' | 'shuttle' | 'port' | 'pick_station' | 'drop_zone'
export type CellType = 'road' | 'blocked' | 'conveyor'
export type ConveyorDirection = 'right' | 'left' | 'up' | 'down'
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

export type LayoutAuditStatus = 'PASS' | 'PARTIAL' | 'BLOCKED'

export interface LayoutPosition {
  floor: string
  row: number
  col: number
  level: number
}

export interface LayoutReferenceNode {
  referenceId: string
  kind: string
  networkType: string
  position: LayoutPosition
  sourceId: string
  validationStatus: LayoutAuditStatus
  evidence: string[]
}

export interface ProposedLayoutFacility {
  facilityId: string
  kind: string
  networkType: string
  position: LayoutPosition
  spansLevels: number[]
  status: 'proposed'
  validationStatus: LayoutAuditStatus
  capacity: number
  purpose: string
  existingRefIds: string[]
  occupancyPolicy: 'exclusive' | 'co_located_with_reference'
  conflicts: string[]
  evidence: string[]
}

export interface ProposedLayoutConnection {
  connectionId: string
  fromId: string
  toId: string
  networkType: string
  edgeType: string
  direction: string
  points: LayoutPosition[]
  status: LayoutAuditStatus
  capacity: number
  handoffAction?: string | null
  heightTransition?: Record<string, unknown> | null
  requiredCellIds: string[]
  conflicts: string[]
  evidence: string[]
}

export interface LayoutRoute {
  routeId: string
  side: 'left' | 'right' | 'shared'
  optionId: string
  status: LayoutAuditStatus
  facilityIds: string[]
  connectionIds: string[]
  blockingReasons: string[]
}

export interface LayoutOption {
  optionId: string
  label: string
  status: LayoutAuditStatus
  description: string
  facilityIds: string[]
  connectionIds: string[]
  assumptions: string[]
  blockingReasons: string[]
}

export interface PickingArmEndpoint {
  nodeId: string
  topologyCellId?: string | null
  position: LayoutPosition
  beltHeight: number
  deckIndex?: number | null
  direction?: string | null
  provenance: 'topology' | 'proposed'
  validationStatus: LayoutAuditStatus
  evidence: string[]
}

export interface PickingArmConveyorStep {
  cellId: string
  position: LayoutPosition
  direction: string
  beltHeight: number
  deviceIds: string[]
  transitionToNext: 'conveyor' | 'device_transfer' | 'proposed_direction' | 'endpoint'
  transferDeviceId?: string | null
  directionSource?: 'topology' | 'layout_plan'
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
  validationStatus: LayoutAuditStatus
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
  downstreamStatus: LayoutAuditStatus
  previewConveyorPath: PickingArmConveyorStep[]
  previewEndpointId: string
  previewPathStatus: LayoutAuditStatus
  previewStopReason: string
  previewTransferDeviceId?: string | null
  previewTransferStatus?: LayoutAuditStatus | null
  previewTransferEvidence: string[]
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
  overallStatus: LayoutAuditStatus
  recommendedOptionId: string
  pickingArm?: PickingArmLayout | null
  references: LayoutReferenceNode[]
  facilities: ProposedLayoutFacility[]
  connections: ProposedLayoutConnection[]
  routes: LayoutRoute[]
  options: LayoutOption[]
  issues: Array<{
    code: string
    severity: 'INFO' | 'WARNING' | 'ERROR'
    sourceId?: string | null
    targetId?: string | null
    reason: string
    evidence: string[]
  }>
  summary: Record<string, unknown>
  assumptions: string[]
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
  /** 任务种类：relocate = 倒库（为取深处货物让位）；缺省为普通取货/交付 */
  taskKind?: string | null
  /** 前置任务（如倒库）未全部完成时不可派发 */
  blockedBy?: string[] | null
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
export type SimulationOrderStatus =
  | 'ready'
  | 'waiting_dispatch'
  | 'executing'
  | 'completed'
  | 'exception'

export interface SimulationOrderLineInput {
  id: string
  skuId: string
  skuName: string
  spec?: string | null
  quantity: number
}

export interface SimulationOrderInput {
  id: string
  priority: SimulationOrderPriority
  targetStation: string
  createdAt: string
  lines: SimulationOrderLineInput[]
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
}

export interface GoodsCatalog {
  version: number
  skus: GoodsCatalogSku[]
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
}

export interface GoodsInventory {
  topologyId: string
  slots: GoodsSlot[]
}

export interface SimulationState {
  id: string
  status: 'idle' | 'running' | 'paused' | 'stopped' | 'completed'
  stopReason?: 'user' | 'safety_limit' | 'transfer_failed' | 'task_failed' | 'runtime_error' | null
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
  robots: SimulationRobot[]
  tasks: SimulationTask[]
  orders?: SimulationOrder[]
  inventory?: GoodsSlot[]
  shuttleDocks?: ShuttleDockState[]
  transferInventory?: TransferInventoryBalance[]
  transferEvents?: TransferInventoryEvent[]
  alerts: SimulationAlert[]
  queues: SimulationQueues
  createdAt: string
  updatedAt: string
}

export interface SimulationEvent {
  run_id?: string
  tick: number
  event_type?: string
  type?: string
  payload: Record<string, unknown>
  created_at?: string
}
