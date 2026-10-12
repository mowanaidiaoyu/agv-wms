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
  rate: 1 | 2 | 4
}

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
