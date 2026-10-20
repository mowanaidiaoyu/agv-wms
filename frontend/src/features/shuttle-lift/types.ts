import type { PathDemoAlgorithm, PlaybackStatus } from '../path-demo/types'

export type ShuttleLevel = 1 | 2 | 3
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
  nodes: ShuttleTrackNode[]
}

export interface ShuttleLiftsResponse {
  topology_id: string
  topology_revision: string
  config_revision: string
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
  vehicleId: 'SHUTTLE-01'
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

export interface ShuttleVisualizationState {
  tracks: ShuttleTrackNode[]
  lifts: ShuttleLift[]
  plan: ShuttlePlanResponse | null
  sample: ShuttlePlaybackSample | null
  selectedLiftId: string | null
}
