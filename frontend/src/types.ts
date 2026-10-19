export type ZoneType = 'ctu' | 'shuttle' | 'port'
export type CellType = 'road' | 'blocked' | 'conveyor'
export type ConveyorDirection = 'right' | 'left' | 'up' | 'down'
export type NodeType = 'storage' | 'ctu_robot' | 'shuttle_robot' | 'lift_transfer' | 'turn_transfer'
export type EditorObjectType =
  | 'select'
  | 'batch'
  | 'erase'
  | 'road'
  | 'blocked'
  | 'conveyor'
  | 'ctu_robot'
  | 'shuttle_robot'
  | 'lift_transfer'
  | 'turn_transfer'
export type RobotType = 'ctu' | 'shuttle'
export type DispatchPolicy = 'greedy' | 'hungarian'
export type PathPolicy = 'astar' | 'dijkstra'

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

export interface RobotConfig {
  id: string
  type: RobotType
  startNode: string
  speed: number
  battery: number
  enabled: boolean
}

export interface ScenarioPayload {
  id: string
  name: string
  topologyId: string
  robots: RobotConfig[]
  orderGenerator: {
    ratePerMinute: number
    maxOrders: number
    linesPerOrderMin: number
    linesPerOrderMax: number
    urgentInsertionTick: number | null
  }
  dispatchPolicy: DispatchPolicy
  pathPolicy: PathPolicy
  conveyorPolicy: {
    releaseThreshold: number
    fifo: boolean
  }
  faultInjection: {
    robotFailureTick: number | null
    robotFailureId: string | null
    conveyorJammedTick: number | null
    conveyorCellId: string | null
  }
  seed: number
  durationTicks: number
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
}

export interface SimulationTask {
  id: string
  robotType: RobotType
  mode: 'direct' | 'cross_zone'
  status: string
  phase: string
  phaseLabel: string
  sourceCellId: string
  sourceLayer: number
  targetCellId: string
  targetLayer: number
  /** 交付点（输送线出入口入口格），等于任务最终 targetCellId */
  deliveryCellId: string
  lines: SimulationTaskLine[]
  currentLineIndex: number
  picked: number
  priority: number
  etaTicks: number
  urgent: boolean
  assignedRobotId: string | null
  remainingTicks: number
  history: SimulationTaskHistory[]
  createdTick: number
  startedTick: number | null
  completedTick: number | null
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

export interface GoodsSku {
  skuId?: string
  name?: string
  brand?: string
  thousandWeight?: number | string
  unit?: string
  colorType?: string
  standard?: string
  spec?: string
  grade?: string
  surfaceTreatment?: string
  material?: string
  strength?: number | string
  largeCategory?: string
  subclass?: string
}

export interface GoodsCatalog {
  version: number
  skus: GoodsSku[]
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
  tick: number
  topologyId: string
  scenarioId: string
  robots: SimulationRobot[]
  tasks: SimulationTask[]
  inventory?: GoodsSlot[]
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

export interface SimulationRun {
  id: string
  status: string
  topology_id: string
  scenario_id: string
  payload: SimulationState
  metrics: SimulationMetrics
  created_at?: string
  updated_at?: string
}

export interface AlgorithmEvaluationResult {
  category: 'dispatch' | 'path' | 'conveyor'
  label: string
  dispatchPolicy: DispatchPolicy
  pathPolicy: PathPolicy
  releaseThreshold: number
  throughput: number
  avgCompletionTicks: number
  conveyorUtilization: number
  congestionEvents: number
  deadlocksDetected: number
  urgentOrdersHandled: number
  summary: string
}
