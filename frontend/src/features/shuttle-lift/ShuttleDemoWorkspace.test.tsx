import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { StoredRecord, TopologyPayload } from '../../types'
import { api } from '../../lib/api'
import { ShuttleDemoWorkspace } from './ShuttleDemoWorkspace'
import type { ShuttleLift, ShuttlePlanResponse, ShuttleTrackNode } from './types'

vi.mock('../../lib/api', () => ({ api: {
  getShuttleLevels: vi.fn(), getShuttleTracks: vi.fn(), getShuttleLifts: vi.fn(), planShuttlePath: vi.fn(),
} }))
vi.mock('../path-demo/PathDemo3DScene', () => ({
  PathDemo3DScene: ({ shuttleVisualization, cameraViewMode, cameraActionRequest }: any) => (
    <div
      data-testid="shuttle-scene"
      data-track-count={shuttleVisualization?.tracks.length ?? 0}
      data-lift-count={shuttleVisualization?.lifts.length ?? 0}
      data-plan={shuttleVisualization?.plan?.chosen_lift ?? ''}
      data-camera-mode={cameraViewMode}
      data-camera-action={cameraActionRequest?.type ?? ''}
    />
  ),
}))

const topology: TopologyPayload = {
  id: 'topology-666', name: '666', meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 216, cols: 129, cellSize: 32, originX: 0, originY: 0 }, zones: [], cells: [], nodes: [], resources: [],
}
const record: StoredRecord<TopologyPayload> = { id: topology.id, name: topology.name, payload: topology }
const makeNode = (level: 1 | 2 | 3, col: number): ShuttleTrackNode => ({
  topology_id: topology.id, zone_id: 'zone-shuttle', track_node_id: `L${level}-r100-c${col}`,
  shuttle_level: level, row: 100, col, world_x: col, world_y: level * 1.4 - 0.05, world_z: 7,
  track_type: col === 19 ? 'lift_entry' : 'straight', traversable: true, neighbors: [],
})
const tracks = [1, 2, 3].flatMap((level) => [7, 19, 51].map((col) => makeNode(level as 1 | 2 | 3, col)))
const lift: ShuttleLift = {
  lift_id: 'shuttle-lift-a', label: 'Lift-A', topology_id: topology.id,
  topology_revision: '2.0.0:hash', config_revision: 'v1', zone_id: 'zone-shuttle', row: 100, col: 19,
  supported_levels: [1, 2, 3], level_entrances: { L1: 'L1-r100-c19', L2: 'L2-r100-c19', L3: 'L3-r100-c19' },
  level_exits: { L1: 'L1-r100-c19', L2: 'L2-r100-c19', L3: 'L3-r100-c19' },
  capacity: 1, enter_time_seconds: 1, travel_time_per_level_seconds: 3, exit_time_seconds: 1,
  timing_source: 'synthetic_demo', vehicle_type: 'shuttle', state: 'idle',
  verification_status: 'program_verified_pending_human', validation_evidence: ['real aisles'],
}
const plan: ShuttlePlanResponse = {
  topology_id: topology.id, topology_revision: '2.0.0:hash', config_revision: 'v1', algorithm: 'astar',
  start: tracks[0], destination: tracks[8], chosen_lift: lift.lift_id,
  path_nodes: [
    { ...tracks[0], arrival_seconds: 0, phase: 'start' },
    { ...tracks[1], arrival_seconds: 6, phase: 'track' },
    { ...tracks[7], arrival_seconds: 14, phase: 'lift' },
    { ...tracks[8], arrival_seconds: 30, phase: 'track' },
  ],
  path_edges: [
    { from_track_node: tracks[0].track_node_id, to_track_node: tracks[1].track_node_id, edge_type: 'track', lift_id: null, duration_seconds: 6, start_seconds: 0, motion_start_seconds: 0, motion_end_seconds: 6, end_seconds: 6, distance_cells: 12, turn_time_seconds: 0 },
    { from_track_node: tracks[1].track_node_id, to_track_node: tracks[7].track_node_id, edge_type: 'lift', lift_id: lift.lift_id, duration_seconds: 8, start_seconds: 6, motion_start_seconds: 7, motion_end_seconds: 13, end_seconds: 14, distance_cells: 0, turn_time_seconds: 0 },
    { from_track_node: tracks[7].track_node_id, to_track_node: tracks[8].track_node_id, edge_type: 'track', lift_id: null, duration_seconds: 16, start_seconds: 14, motion_start_seconds: 14, motion_end_seconds: 30, end_seconds: 30, distance_cells: 32, turn_time_seconds: 0 },
  ],
  lift_transitions: [{ lift_id: lift.lift_id, from_level: 1, to_level: 3, entrance_track_node: tracks[1].track_node_id, exit_track_node: tracks[7].track_node_id, enter_at_seconds: 6, lift_start_seconds: 7, arrive_level_seconds: 13, exit_at_seconds: 14, events: ['enter_lift', 'lift_started', 'arrive_level', 'exit_lift'] }],
  horizontal_distance_cells: 44, estimated_duration_seconds: 30, visited_states: 12, planning_time_ms: 1,
  result_status: 'success', failure_reason: null, timing_source: 'synthetic_demo', cost_contract: { time_unit: 'seconds' },
}

function renderWorkspace(onMode = vi.fn()) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ShuttleDemoWorkspace topologyRecord={record} topologies={[record]} onTopologyChange={vi.fn()} onWorkspaceModeChange={onMode} />
  </QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(api.getShuttleLevels).mockResolvedValue({
    topology_id: topology.id, topology_name: '666', topology_revision: '2.0.0:hash', topology_version: '2.0.0', config_revision: 'v1',
    data_source: 'topology_slot_aisles', verification_status: 'program_verified_pending_human', planning_enabled: true,
    lift_candidate_count: 5, lift_selection_basis: ['north/south coverage'],
    levels: [1, 2, 3].map((level) => ({ shuttle_level: level as 1 | 2 | 3, node_count: 3, component_count: 1, component_sizes: [3] })), warnings: ['人工复核'],
  })
  vi.mocked(api.getShuttleTracks).mockResolvedValue({ topology_id: topology.id, topology_revision: '2.0.0:hash', config_revision: 'v1', nodes: tracks })
  vi.mocked(api.getShuttleLifts).mockResolvedValue({ topology_id: topology.id, topology_revision: '2.0.0:hash', config_revision: 'v1', planning_enabled: true, lifts: [lift], warnings: [] })
  vi.mocked(api.planShuttlePath).mockResolvedValue(plan)
})

describe('ShuttleDemoWorkspace', () => {
  it('shows three real levels, fixed lifts, and the isolated shuttle scene', async () => {
    renderWorkspace()
    expect(await screen.findByText('三层真实轨道 · 双提升机')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-track-count', '9'))
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-lift-count', '1')
    expect(screen.getAllByRole('option', { name: 'L3' })).toHaveLength(2)
  })

  it('submits the same selected nodes to A* and renders the returned lift path', async () => {
    renderWorkspace()
    await waitFor(() => expect(screen.getByLabelText('四向车起点轨道节点')).toHaveValue('L1-r100-c7'))
    fireEvent.click(screen.getByRole('button', { name: '规划跨层路径' }))
    await waitFor(() => expect(api.planShuttlePath).toHaveBeenCalledWith(expect.objectContaining({
      start_level: 1, end_level: 3, algorithm: 'astar', start_track_node: 'L1-r100-c7', end_track_node: 'L3-r100-c51',
    })))
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-plan', 'shuttle-lift-a')
    expect(screen.getByText('44 格')).toBeInTheDocument()
  })

  it('supports Dijkstra and all requested level selectors without using CTU roads', async () => {
    renderWorkspace()
    await screen.findByTestId('shuttle-scene')
    fireEvent.change(screen.getByLabelText('四向车起点层'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('四向车终点层'), { target: { value: '1' } })
    fireEvent.change(screen.getByLabelText('四向车路径算法'), { target: { value: 'dijkstra' } })
    await waitFor(() => expect(screen.getByLabelText('四向车起点轨道节点')).toHaveValue('L2-r100-c7'))
    fireEvent.click(screen.getByRole('button', { name: '规划跨层路径' }))
    await waitFor(() => expect(api.planShuttlePath).toHaveBeenCalledWith(expect.objectContaining({ start_level: 2, end_level: 1, algorithm: 'dijkstra' })))
  })

  it('keeps camera follow and lift focus controls independent from planning time', async () => {
    renderWorkspace()
    await waitFor(() => expect(screen.getByRole('button', { name: '查看 Lift-A' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '查看 Lift-A' }))
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-camera-action', 'focus-point')
    fireEvent.click(screen.getByRole('button', { name: '规划跨层路径' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '跟随四向车' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '跟随四向车' }))
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-camera-mode', 'follow')
  })

  it('blocks execution when verification is not valid and can return to CTU modes', async () => {
    vi.mocked(api.getShuttleLevels).mockResolvedValueOnce({
      topology_id: topology.id, topology_name: '666', topology_revision: 'stale', topology_version: '2.0.0', config_revision: 'v0',
      data_source: 'topology_slot_aisles', verification_status: 'topology_version_mismatch', planning_enabled: false, levels: [], warnings: ['版本不匹配'],
      lift_candidate_count: 0, lift_selection_basis: [],
    })
    const onMode = vi.fn()
    renderWorkspace(onMode)
    expect(await screen.findByText('当前轨道或提升机配置未通过程序验证，已禁止运行。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '规划跨层路径' })).toBeDisabled()
    fireEvent.click(screen.getByRole('tab', { name: '轨迹规划' }))
    expect(onMode).toHaveBeenCalledWith('planning')
  })
})
