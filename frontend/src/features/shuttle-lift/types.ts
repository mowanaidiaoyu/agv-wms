import type { PathDemoAlgorithm, PlaybackStatus } from '../path-demo/types'

export type ShuttleLevel = 1 | 2 | 3 | 4
export type ShuttleVerificationStatus =
  | 'program_verified_pending_human'
  | 'unverified'
  | 'rejected'
  | 'topology_version_mismatch'

export interface ShuttleTrackNode {
  topology_id: string
  zone_id: string
  track_node_id: string
  shuttle_level: ShuttleLevel
  row: number
  col: number
  world_x: number
  world_y: number
  world_z: number
  track_type: 'straight' | 'intersection' | 'turn' | 'lift_entry' | 'lift_exit'
  traversable: boolean
  neighbors: string[]
}

export interface ShuttleLevelSummary {
  shuttle_level: ShuttleLevel
  node_count: number
  component_count: number
  component_sizes: number[]
}

export interface ShuttleTopologyLevelsResponse {
  topology_id: string
  topology_name: string
  topology_revision: string
  topology_version: string
  config_revision: string
  coordinate_contract_revision: 'shuttle-internal-levels-v1'
  data_source: 'topology_slot_aisles'
  verification_status: ShuttleVerificationStatus
  planning_enabled: boolean
  lift_candidate_count: number
  lift_selection_basis: string[]
  levels: ShuttleLevelSummary[]
  warnings: string[]
}

export interface ShuttleLift {
  lift_id: 'shuttle-lift-a' | 'shuttle-lift-b'
  label: string
  topology_id: string
  topology_revision: string
  config_revision: string
  zone_id: string
  row: number
  col: number
  supported_levels: ShuttleLevel[]
  level_entrances: Record<`L${ShuttleLevel}`, string>
  level_exits: Record<`L${ShuttleLevel}`, string>
  capacity: 1
  enter_time_seconds: number
  travel_time_per_level_seconds: number
  exit_time_seconds: number
  timing_source: 'synthetic_demo'
  vehicle_type: 'shuttle'
  state: 'idle' | 'moving_up' | 'moving_down' | 'occupied' | 'unavailable'
  verification_status: ShuttleVerificationStatus
  validation_evidence: string[]
}

export interface ShuttleTracksResponse {
  topology_id: string
  topology_revision: string
  config_revision: string
  coordinate_contract_revision: 'shuttle-internal-levels-v1'
  nodes: ShuttleTrackNode[]
}

export interface ShuttleLiftsResponse {
  topology_id: string
  topology_revision: string
  config_revision: string
  coordinate_contract_revision: 'shuttle-internal-levels-v1'
  planning_enabled: boolean
  lifts: ShuttleLift[]
  warnings: string[]
}

export interface ShuttlePlanRequest {
  topology_id: string
  start_track_node: string
  start_level: ShuttleLevel
  end_track_node: string
  end_level: ShuttleLevel
  algorithm: PathDemoAlgorithm
}

export interface ShuttlePathNodeStep extends ShuttleTrackNode {
  arrival_seconds: number
  phase: 'start' | 'track' | 'lift'
}

export interface ShuttlePathEdge {
  from_track_node: string
  to_track_node: string
  edge_type: 'track' | 'lift'
  lift_id: string | null
  duration_seconds: number
  start_seconds: number
  motion_start_seconds: number
  motion_end_seconds: number
  end_seconds: number
  distance_cells: number
  turn_time_seconds: number
}

export interface ShuttleLiftTransition {
  lift_id: string
  from_level: ShuttleLevel
  to_level: ShuttleLevel
  entrance_track_node: string
  exit_track_node: string
  enter_at_seconds: number
  lift_start_seconds: number
  arrive_level_seconds: number
  exit_at_seconds: number
  events: Array<'enter_lift' | 'lift_started' | 'arrive_level' | 'exit_lift'>
}

export interface ShuttlePlanResponse {
  topology_id: string
  topology_revision: string
  config_revision: string
  coordinate_contract_revision: 'shuttle-internal-levels-v1'
  algorithm: PathDemoAlgorithm
  start: ShuttleTrackNode | null
  destination: ShuttleTrackNode | null
  chosen_lift: string | null
  path_nodes: ShuttlePathNodeStep[]
  path_edges: ShuttlePathEdge[]
  lift_transitions: ShuttleLiftTransition[]
  horizontal_distance_cells: number
  estimated_duration_seconds: number
  visited_states: number
  planning_time_ms: number
  result_status: 'success' | 'failed'
  failure_reason: string | null
  timing_source: 'synthetic_demo'
  cost_contract: Record<string, string | number>
}

export interface ShuttleMultiVehicleRequest {
  vehicle_id: string
  start_track_node_id: string
  target_track_node_id: string
  priority: number
  algorithm: PathDemoAlgorithm
}

export interface ShuttleMultiPlanningOptions {
  time_step_seconds?: 0.25
  max_vehicles?: number
  max_time_steps?: number
  max_expanded_states?: number
  max_lift_candidates?: number
  max_replans?: number
  vehicle_length_cells?: number
  vehicle_width_cells?: number
  safety_margin_cells?: number
  lift_initial_levels?: Record<'shuttle-lift-a' | 'shuttle-lift-b', ShuttleLevel>
}

export interface ShuttleMultiPlanRequest {
  topology_id: string
  topology_revision: string
  shuttle_config_revision: string
  coordinate_contract_version: 'shuttle-internal-levels-v1'
  vehicle_requests: ShuttleMultiVehicleRequest[]
  planning_options?: ShuttleMultiPlanningOptions
}

export interface ShuttleTimedTrajectorySample {
  time_step: number
  time_seconds: number
  world_x: number
  world_y: number
  world_z: number
  track_node_id: string | null
  action: 'start' | 'wait' | 'turn' | 'move' | 'lift_enter' | 'lift' | 'lift_exit'
  lift_id: string | null
  edge_from: string | null
  edge_to: string | null
}

export interface ShuttleMultiWaitInterval {
  track_node_id: string
  start_time_seconds: number
  end_time_seconds: number
  duration_seconds: number
  reason: 'track_reservation' | 'lift_resource' | 'turn'
}

export interface ShuttleMultiLiftUsage {
  lift_id: string
  vehicle_id: string
  entry_level: ShuttleLevel
  exit_level: ShuttleLevel
  request_time_seconds: number
  reposition_start_seconds: number
  reposition_end_seconds: number
  enter_start_seconds: number
  transfer_start_seconds: number
  transfer_end_seconds: number
  exit_end_seconds: number
  initial_cabin_level: ShuttleLevel
  final_cabin_level: ShuttleLevel
  used_as_alternate: boolean
  reservation_status: 'reserved'
}

export interface ShuttleMultiVehicleResult {
  vehicle_id: string
  priority: number
  algorithm: PathDemoAlgorithm
  start: ShuttleTrackNode
  destination: ShuttleTrackNode
  path: string[]
  timed_trajectory: ShuttleTimedTrajectorySample[]
  wait_intervals: ShuttleMultiWaitInterval[]
  lift_usage: ShuttleMultiLiftUsage[]
  arrival_time_seconds: number
  wait_time_seconds: number
  horizontal_distance_cells: number
  visited_states: number
  planning_status: 'planned'
}

export type ShuttlePlanningEventType =
  | 'track_node_conflict_detected'
  | 'edge_swap_conflict_detected'
  | 'geometric_conflict_detected'
  | 'lift_requested'
  | 'lift_reservation_denied'
  | 'lift_reserved'
  | 'lift_reposition_started'
  | 'lift_reposition_completed'
  | 'vehicle_wait_started'
  | 'vehicle_wait_completed'
  | 'lift_entered'
  | 'lift_transfer_started'
  | 'lift_transfer_completed'
  | 'lift_exited'

export interface ShuttlePlanningEvent {
  event_id: string
  simulation_time_seconds: number
  vehicle_ids: string[]
  event_type: ShuttlePlanningEventType
  resource_id: string | null
  track_node_id: string | null
  details: Record<string, unknown>
  resolution_status: 'candidate_rejected' | 'resolved_by_wait' | 'reserved' | 'completed'
}

export interface ShuttleMultiMetrics {
  requested_vehicle_count: number
  planned_vehicle_count: number
  makespan_seconds: number
  total_wait_time_seconds: number
  average_wait_time_seconds: number
  detected_track_node_conflicts: number
  detected_edge_swap_conflicts: number
  detected_geometric_conflicts: number
  lift_resource_contentions: number
  lift_a_usage_count: number
  lift_b_usage_count: number
  lift_a_occupied_seconds: number
  lift_b_occupied_seconds: number
  alternate_lift_selections: number
  replans_used: number
  unresolved_conflict_count: number
  planning_time_ms: number
}

export interface ShuttleMultiPlanResponse {
  planning_id: string
  topology_id: string
  topology_version: string
  topology_revision: string
  shuttle_config_revision: string
  coordinate_contract_version: 'shuttle-internal-levels-v1'
  planning_algorithm_version: string
  status: 'planning_success' | 'failed' | 'unsafe'
  vehicles: ShuttleMultiVehicleResult[]
  events: ShuttlePlanningEvent[]
  metrics: ShuttleMultiMetrics
  safety_validation: {
    node_conflicts: number
    edge_conflicts: number
    geometric_conflicts: number
    lift_resource_conflicts: number
    timing_violations: number
    safe: boolean
  }
  unresolved_conflicts: Array<Record<string, unknown>>
  failure_reason: string | null
  failure: {
    vehicle_id: string | null
    failure_code:
      | 'invalid_vehicle_count'
      | 'duplicate_vehicle_id'
      | 'invalid_priority'
      | 'invalid_planning_request'
      | 'initial_occupancy_conflict'
      | 'duplicate_terminal_destination'
      | 'terminal_geometry_conflict'
      | 'search_limit_exceeded'
      | 'unreachable_track'
      | 'reservation_conflict_unresolved'
      | 'configuration_mismatch'
    expanded_states: number | null
    max_expanded_states: number | null
    planning_time_ms: number
    failure_reason: string
    details: Record<string, unknown>
  } | null
  vehicle_initial_config: ShuttleMultiVehicleRequest[]
  lift_initial_state: Record<string, ShuttleLevel>
  time_contract: Record<string, unknown>
}

export interface ShuttleMultiScenariosResponse {
  topology_id: string
  topology_revision: string
  shuttle_config_revision: string
  coordinate_contract_version: 'shuttle-internal-levels-v1'
  scenarios: Array<{
    scenario_id: 'parallel' | 'track-conflict' | 'lift-conflict'
    name: string
    description: string
    vehicle_requests: ShuttleMultiVehicleRequest[]
    evidence: Record<string, unknown>
  }>
}

export type ShuttleMotionPhase =
  | 'idle'
  | 'turning'
  | 'moving_track'
  | 'entering_lift'
  | 'moving_up'
  | 'moving_down'
  | 'exiting_lift'
  | 'arrived'

export interface ShuttlePlaybackSample {
  vehicleId: string
  topologyId: string
  trackNodeId: string
  shuttleLevel: ShuttleLevel
  row: number
  col: number
  pathIndex: number
  elapsedSeconds: number
  world: [number, number, number]
  headingRadians: number
  phase: ShuttleMotionPhase
  liftId: string | null
  playbackStatus: PlaybackStatus
}

export type ShuttleMultiMotionPhase =
  | 'not_started'
  | 'turning'
  | 'moving_track'
  | 'waiting_traffic'
  | 'waiting_lift'
  | 'entering_lift'
  | 'moving_up'
  | 'moving_down'
  | 'exiting_lift'
  | 'arrived'

export interface ShuttleMultiPlaybackSample extends Omit<ShuttlePlaybackSample, 'phase'> {
  phase: ShuttleMultiMotionPhase
  accumulatedWaitSeconds: number
  arrivalTimeSeconds: number
}

export interface ShuttleLiftPlaybackState {
  liftId: string
  platformY: number
  currentLevel: ShuttleLevel
  targetLevel: ShuttleLevel | null
  direction: 'up' | 'down' | 'idle'
  status: 'idle' | 'repositioning_empty' | 'reserved' | 'entering' | 'occupied' | 'exiting'
  occupantVehicleId: string | null
  reservationStartSeconds: number | null
  reservationEndSeconds: number | null
}

export interface ShuttleVisualizationState {
  tracks: ShuttleTrackNode[]
  lifts: ShuttleLift[]
  plan: ShuttlePlanResponse | null
  sample: ShuttlePlaybackSample | null
  multiPlan?: ShuttleMultiPlanResponse | null
  multiSamples?: ShuttleMultiPlaybackSample[]
  liftStates?: ShuttleLiftPlaybackState[]
  selectedShuttleId?: string | null
  selectedLiftId: string | null
}
