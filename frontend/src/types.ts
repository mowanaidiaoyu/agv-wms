export type ZoneType = 'ctu' | 'shuttle' | 'conveyor' | 'buffer' | 'station' | 'mixed'
export type NodeType = 'intersection' | 'storage' | 'buffer' | 'station' | 'lift' | 'entry' | 'conveyor_gate'
export type EdgeType = 'aisle' | 'conveyor' | 'lift' | 'cross_zone'
export type ResourceType = 'conveyor_segment' | 'buffer_slot' | 'lift' | 'station'
export type RobotType = 'ctu' | 'shuttle'
export type DispatchPolicy = 'greedy' | 'hungarian'
export type PathPolicy = 'astar' | 'dijkstra'

export interface Zone {
  id: string
  label: string
  type: ZoneType
  x: number
  y: number
  width: number
  height: number
}

export interface TopologyNode {
  id: string
  type: NodeType
  x: number
  y: number
  zoneId?: string | null
  capacity: number
  label: string
}

export interface TopologyEdge {
  id: string
  from: string
  to: string
  type: EdgeType
  length: number
  bidirectional: boolean
  capacity: number
  speedLimit: number
}

export interface Resource {
  id: string
  type: ResourceType
  label: string
  nodeIds: string[]
  edgeIds: string[]
  capacity: number
  meta: Record<string, unknown>
}

export interface TopologyPayload {
  id: string
  name: string
  meta: {
    version: string
    viewport: { x: number; y: number; zoom: number }
    tags: string[]
  }
  zones: Zone[]
  nodes: TopologyNode[]
  edges: TopologyEdge[]
  resources: Resource[]
}

export interface TopologyValidationResult {
  valid: boolean
  errors: string[]
  warnings: string[]
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
    conveyorEdgeId: string | null
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
  status: string
  battery: number
  path: string[]
  targetNode: string
  currentTaskId: string | null
  waitTicks: number
  failed: boolean
  phaseLabel: string
}

export interface SimulationTaskHistory {
  tick: number
  phase: string
  status: string
}

export interface SimulationTask {
  id: string
  robotType: RobotType
  mode: 'direct' | 'cross_zone'
  status: string
  phase: string
  phaseLabel: string
  sourceNode: string
  targetNode: string
  stationNode: string
  entryNode: string
  handoffNode: string | null
  conveyorExitNode: string | null
  stationResourceId: string | null
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

export interface SimulationResourceState {
  id: string
  label: string
  type: ResourceType
  capacity: number
  nodeIds: string[]
  edgeIds: string[]
  occupiedBy: string[]
  queue: string[]
  meta: Record<string, unknown>
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

export interface SimulationState {
  id: string
  status: 'idle' | 'running' | 'paused' | 'stopped' | 'completed'
  tick: number
  topologyId: string
  scenarioId: string
  robots: SimulationRobot[]
  tasks: SimulationTask[]
  resources: SimulationResourceState[]
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
