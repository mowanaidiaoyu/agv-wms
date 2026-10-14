export type PathDemoAlgorithm = 'astar' | 'dijkstra'

export interface PathDemoCell {
  id: string
  floor: string
  row: number
  col: number
  type: string
  component_id: string | null
}
export interface PathDemoComponentInfo {
  id: string
  floor: string
  cell_count: number
}
export interface PathDemoTopologyOptions {
  topology_id: string
  topology_name: string
  topology_version: string
  components: PathDemoComponentInfo[]
  largest_component_id: string
  traversable_cells: PathDemoCell[]
  default_start: PathDemoCell
  default_goal: PathDemoCell
}

export interface PathDemoPlan {
  topology_id: string
  topology_name: string
  topology_version: string
  algorithm: PathDemoAlgorithm
  start: PathDemoCell
  goal: PathDemoCell
  path_cells: PathDemoCell[]
  total_distance: number
  total_cost: number
  visited_nodes: number
  planning_time_ms: number
}

export type PlaybackStatus = 'idle' | 'running' | 'paused' | 'completed'

export interface PlaybackState {
  status: PlaybackStatus
  elapsedSeconds: number
  rate: PlaybackRate
}

export type PlaybackRate = 0.25 | 0.5 | 1 | 2 | 4

export interface TrajectoryPoint {
  vehicleId: string
  topologyId: string
  floor: string
  row: number
  col: number
  pathIndex: number
  arrivalSeconds: number
  world: [number, number, number]
}

export interface TrajectorySample {
  vehicleId: string
  topologyId: string
  floor: string
  row: number
  col: number
  pathIndex: number
  elapsedSeconds: number
  world: [number, number, number]
  headingRadians: number
}

export interface MultiPathVehicleRequest {
  vehicle_id: string
  vehicle_type?: 'ctu'
  start_cell_id: string
  goal_cell_id: string
  priority?: number
  display_color?: string | null
}

export interface MultiPathPlanningConfig {
  time_step_seconds: number
  speed_cells_per_second: number
  max_time_steps: number
  max_expanded_states: number
  vehicle_width_cells: number
  vehicle_length_cells: number
  safety_margin_cells: number
}

export interface MultiPathDemoScenario {
  id: 'parallel' | 'node-conflict' | 'edge-swap'
  name: string
  description: string
  vehicles: MultiPathVehicleRequest[]
}

export interface MultiPathDemoScenariosResponse {
  topology_id: string
  topology_name: string
  topology_version: string
  largest_road_component_id: string
  road_cells: PathDemoCell[]
  scenarios: MultiPathDemoScenario[]
}

export interface MultiPathStep {
  time_step: number
  time_seconds: number
  action: 'start' | 'move' | 'wait'
  cell: PathDemoCell
}

export interface MultiPathVehiclePlan {
  vehicle_id: string
  vehicle_type?: 'ctu'
  priority?: number
  display_color?: string | null
  start: PathDemoCell
  goal: PathDemoCell
  steps: MultiPathStep[]
  total_distance: number
  estimated_duration_seconds: number
  wait_time_seconds: number
  avoidance_count: number
  visited_states: number
}

export interface MultiPathConflictMetrics {
  detected_node_conflicts: number
  detected_edge_conflicts: number
  detected_geometry_conflicts: number
  resolved_node_conflicts: number
  resolved_edge_conflicts: number
  resolved_geometry_conflicts: number
  remaining_node_conflicts: number
  remaining_edge_conflicts: number
  remaining_geometry_conflicts: number
}

export interface MultiPathConflictEvent {
  event_id: string
  conflict_type: 'node' | 'edge' | 'geometry'
  status: 'resolved' | 'remaining'
  time_step: number
  time_seconds: number
  cell: PathDemoCell
  from_cell: PathDemoCell
  involved_vehicle_ids: string[]
  priority_vehicle_id: string
  priority: number
  yielding_vehicle_id: string
  yielding_priority: number
  reservation_action: 'candidate_rejected'
  avoidance_action: 'wait' | 'reroute' | 'reroute_and_wait'
  wait_cell: PathDemoCell | null
  wait_start_time_seconds: number | null
  wait_end_time_seconds: number | null
  wait_duration_seconds: number
  result: 'conflict_free_plan'
}

export interface MultiPathPlan {
  topology_id: string
  topology_name: string
  topology_version: string
  algorithm: PathDemoAlgorithm
  planning_status: 'success'
  error_reason: null
  time_step_seconds: number
  speed_cells_per_second: number
  vehicle_width_cells: number
  vehicle_length_cells: number
  safety_distance_cells: number
  makespan_steps: number
  makespan_seconds: number
  vehicles: MultiPathVehiclePlan[]
  conflicts: MultiPathConflictMetrics
  conflict_events?: MultiPathConflictEvent[]
  avoidance_count: number
  planning_time_ms: number
}

export type HistoricalVehicleStatus = 'NOT_STARTED' | 'MOVING' | 'WAITING' | 'COMPLETED'

export interface HistoricalVehicleSnapshot {
  vehicleId: string
  status: HistoricalVehicleStatus
  logicalPosition: string
  currentCellId: string | null
  movedDistance: number
  accumulatedWaitSeconds: number
  progress: number
}

export interface MultiPathEvaluationVehicleMetrics {
  vehicle_id: string
  total_distance: number
  estimated_duration_seconds: number
  wait_time_seconds: number
  avoidance_count: number
  visited_states: number
}

export interface MultiPathEvaluationMetrics {
  total_path_distance: number
  makespan_seconds: number
  total_wait_time_seconds: number
  average_wait_time_seconds: number
  detected_conflicts: number
  resolved_conflicts: number
  remaining_conflicts: number
  avoidance_count: number
  vehicles: MultiPathEvaluationVehicleMetrics[]
}

export interface MultiPathEvaluationAlgorithmResult {
  algorithm: PathDemoAlgorithm
  planning_status: 'success' | 'failed'
  error_reason: string | null
  planning_time_ms: number
  metrics: MultiPathEvaluationMetrics | null
  plan: MultiPathPlan | null
}

export interface MultiPathEvaluation {
  experiment_id: string
  schema_version: string
  experiment_type: 'offline_multi_path_planning'
  status: 'success' | 'partial_failure' | 'failed'
  created_at: string
  input_snapshot: {
    topology_id: string
    topology_name: string
    topology_version: string
    mode: 'preset' | 'custom'
    scenario_id: MultiPathDemoScenario['id'] | null
    vehicles: MultiPathVehicleRequest[]
    planning_config: MultiPathPlanningConfig
    algorithms: PathDemoAlgorithm[]
  }
  results: MultiPathEvaluationAlgorithmResult[]
}

export interface MultiPathEvaluationSummary {
  experiment_id: string
  schema_version: string
  experiment_type: 'offline_multi_path_planning'
  status: 'success' | 'partial_failure' | 'failed'
  topology_id: string
  topology_name: string
  topology_version: string
  vehicle_count: number
  algorithms: PathDemoAlgorithm[]
  results: Array<Omit<MultiPathEvaluationAlgorithmResult, 'plan'>>
  created_at: string
}

export interface MultiPathEvaluationList {
  items: MultiPathEvaluationSummary[]
  total: number
  limit: number
  offset: number
}

export interface MultiPathEvaluationExport {
  schema_version: string
  experiment_type: 'offline_multi_path_planning'
  disclaimer: string
  unit_definitions: Record<string, string>
  evaluation: MultiPathEvaluation
}

export interface PathDemoVehicleView {
  vehicleId: string
  label: string
  color: string
  trajectory: TrajectoryPoint[]
  sample: TrajectorySample | null
  statusText?: string
}

export type RoadSelectionKind = 'start' | 'goal'

export interface PathDemoEndpointMarker {
  vehicleId: string
  kind: RoadSelectionKind
  label: string
  color: string
  world: [number, number, number]
}
