import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { StoredRecord, TopologyPayload } from '../../types'
import { api } from '../../lib/api'
import { ShuttleDemoWorkspace } from './ShuttleDemoWorkspace'
import { useShuttleWorkspaceStore } from './shuttleWorkspaceStore'
import type { ShuttleLevel, ShuttleLift, ShuttleMultiPlanResponse, ShuttlePlanResponse, ShuttleTrackNode } from './types'
import { shuttleLevelToWorldY } from '../../lib/topology3d'
import { SHUTTLE_VEHICLE_CENTER_HEIGHT } from './shuttleGeometry'

vi.mock('../../lib/api', () => ({ api: {
  getShuttleLevels: vi.fn(), getShuttleTracks: vi.fn(), getShuttleLifts: vi.fn(), planShuttlePath: vi.fn(),
  getShuttleMultiScenarios: vi.fn(), planShuttleMulti: vi.fn(),
} }))
vi.mock('../path-demo/PathDemo3DScene', () => ({
  PathDemo3DScene: ({ shuttleVisualization, cameraViewMode, cameraActionRequest, shuttleRackXray }: any) => (
    <div
      data-testid="shuttle-scene"
      data-track-count={shuttleVisualization?.tracks.length ?? 0}
      data-lift-count={shuttleVisualization?.lifts.length ?? 0}
      data-plan={shuttleVisualization?.plan?.chosen_lift ?? ''}
      data-multi-plan={shuttleVisualization?.multiPlan?.planning_id ?? ''}
      data-multi-samples={shuttleVisualization?.multiSamples?.length ?? 0}
      data-selected-shuttle={shuttleVisualization?.selectedShuttleId ?? ''}
      data-selected-event={shuttleVisualization?.selectedEvent?.event_id ?? ''}
      data-camera-mode={cameraViewMode}
      data-camera-action={cameraActionRequest?.type ?? ''}
      data-camera-target={cameraActionRequest?.targetWorld?.join(',') ?? ''}
      data-rack-xray={shuttleRackXray ? 'true' : 'false'}
    />
  ),
}))

const topology: TopologyPayload = {
  id: 'topology-666', name: '666', meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 216, cols: 129, cellSize: 32, originX: 0, originY: 0 }, zones: [], cells: [], nodes: [], resources: [],
}
const record: StoredRecord<TopologyPayload> = { id: topology.id, name: topology.name, payload: topology }
const makeNode = (level: ShuttleLevel, col: number): ShuttleTrackNode => ({
  topology_id: topology.id, zone_id: 'zone-shuttle', track_node_id: `L${level}-r100-c${col}`,
  shuttle_level: level, row: 100, col, world_x: col, world_y: shuttleLevelToWorldY(level), world_z: 7,
  track_type: col === 19 ? 'lift_entry' : 'straight', traversable: true, neighbors: [],
})
const tracks = [1, 2, 3, 4].flatMap((level) => [7, 19, 51].map((col) => makeNode(level as ShuttleLevel, col)))
const lift: ShuttleLift = {
  lift_id: 'shuttle-lift-a', label: 'Lift-A', topology_id: topology.id,
  topology_revision: '2.0.0:hash', config_revision: 'shuttle-4l-lift-v2', zone_id: 'zone-shuttle', row: 100, col: 19,
  supported_levels: [1, 2, 3, 4], level_entrances: { L1: 'L1-r100-c19', L2: 'L2-r100-c19', L3: 'L3-r100-c19', L4: 'L4-r100-c19' },
  level_exits: { L1: 'L1-r100-c19', L2: 'L2-r100-c19', L3: 'L3-r100-c19', L4: 'L4-r100-c19' },
  capacity: 1, enter_time_seconds: 1, travel_time_per_level_seconds: 3, exit_time_seconds: 1,
  timing_source: 'synthetic_demo', vehicle_type: 'shuttle', state: 'idle',
  verification_status: 'program_verified_pending_human', validation_evidence: ['real aisles'],
}
const plan: ShuttlePlanResponse = {
  topology_id: topology.id, topology_revision: '2.0.0:hash', config_revision: 'shuttle-4l-lift-v2', coordinate_contract_revision: 'shuttle-internal-levels-v1', algorithm: 'astar',
  start: tracks[0], destination: tracks[11], chosen_lift: lift.lift_id,
  path_nodes: [
    { ...tracks[0], arrival_seconds: 0, phase: 'start' },
    { ...tracks[1], arrival_seconds: 6, phase: 'track' },
    { ...tracks[10], arrival_seconds: 17, phase: 'lift' },
    { ...tracks[11], arrival_seconds: 33, phase: 'track' },
  ],
  path_edges: [
    { from_track_node: tracks[0].track_node_id, to_track_node: tracks[1].track_node_id, edge_type: 'track', lift_id: null, duration_seconds: 6, start_seconds: 0, motion_start_seconds: 0, motion_end_seconds: 6, end_seconds: 6, distance_cells: 12, turn_time_seconds: 0 },
    { from_track_node: tracks[1].track_node_id, to_track_node: tracks[10].track_node_id, edge_type: 'lift', lift_id: lift.lift_id, duration_seconds: 11, start_seconds: 6, motion_start_seconds: 7, motion_end_seconds: 16, end_seconds: 17, distance_cells: 0, turn_time_seconds: 0 },
    { from_track_node: tracks[10].track_node_id, to_track_node: tracks[11].track_node_id, edge_type: 'track', lift_id: null, duration_seconds: 16, start_seconds: 17, motion_start_seconds: 17, motion_end_seconds: 33, end_seconds: 33, distance_cells: 32, turn_time_seconds: 0 },
  ],
  lift_transitions: [{ lift_id: lift.lift_id, from_level: 1, to_level: 4, entrance_track_node: tracks[1].track_node_id, exit_track_node: tracks[10].track_node_id, enter_at_seconds: 6, lift_start_seconds: 7, arrive_level_seconds: 16, exit_at_seconds: 17, events: ['enter_lift', 'lift_started', 'arrive_level', 'exit_lift'] }],
  horizontal_distance_cells: 44, estimated_duration_seconds: 33, visited_states: 12, planning_time_ms: 1,
  result_status: 'success', failure_reason: null, timing_source: 'synthetic_demo', cost_contract: { time_unit: 'seconds' },
}

const multiVehicleRequests = [
  { vehicle_id: 'SHUTTLE-01', start_track_node_id: tracks[0].track_node_id, target_track_node_id: tracks[11].track_node_id, priority: 1, algorithm: 'astar' as const },
  { vehicle_id: 'SHUTTLE-02', start_track_node_id: tracks[3].track_node_id, target_track_node_id: tracks[8].track_node_id, priority: 2, algorithm: 'astar' as const },
]
const multiPlan: ShuttleMultiPlanResponse = {
  planning_id: 'shuttle-multi-test', topology_id: topology.id, topology_version: '2.0.0', topology_revision: '2.0.0:hash', shuttle_config_revision: 'shuttle-4l-lift-v2', coordinate_contract_version: 'shuttle-internal-levels-v1', planning_algorithm_version: 'prioritized-space-time-v1', status: 'planning_success',
  vehicles: multiVehicleRequests.map((request, index) => ({
    vehicle_id: request.vehicle_id, priority: request.priority, algorithm: request.algorithm,
    start: tracks[index * 3], destination: tracks[index === 0 ? 11 : 8], path: [request.start_track_node_id, request.target_track_node_id],
    timed_trajectory: [
      { time_step: 0, time_seconds: 0, world_x: tracks[index * 3].world_x, world_y: tracks[index * 3].world_y, world_z: tracks[index * 3].world_z, track_node_id: request.start_track_node_id, action: 'start' as const, lift_id: null, edge_from: null, edge_to: null },
      { time_step: 8, time_seconds: 2, world_x: tracks[index === 0 ? 11 : 8].world_x, world_y: tracks[index === 0 ? 11 : 8].world_y, world_z: tracks[index === 0 ? 11 : 8].world_z, track_node_id: request.target_track_node_id, action: 'move' as const, lift_id: null, edge_from: request.start_track_node_id, edge_to: request.target_track_node_id },
    ],
    wait_intervals: index ? [{ track_node_id: request.start_track_node_id, start_time_seconds: 0, end_time_seconds: 0.25, duration_seconds: 0.25, reason: 'track_reservation' as const }] : [], lift_usage: [], arrival_time_seconds: 2, wait_time_seconds: index ? 0.25 : 0, horizontal_distance_cells: 2, visited_states: 8, planning_status: 'planned' as const,
  })),
  events: [{ event_id: 'event-1', simulation_time_seconds: 0.25, vehicle_ids: ['SHUTTLE-01', 'SHUTTLE-02'], event_type: 'track_node_conflict_detected', resource_id: null, track_node_id: tracks[1].track_node_id, details: {}, resolution_status: 'candidate_rejected' }],
  metrics: { requested_vehicle_count: 2, planned_vehicle_count: 2, makespan_seconds: 2, total_wait_time_seconds: 0.25, average_wait_time_seconds: 0.125, detected_track_node_conflicts: 1, detected_edge_swap_conflicts: 0, detected_geometric_conflicts: 0, lift_resource_contentions: 0, lift_a_usage_count: 0, lift_b_usage_count: 0, lift_a_occupied_seconds: 0, lift_b_occupied_seconds: 0, alternate_lift_selections: 0, replans_used: 0, unresolved_conflict_count: 0, planning_time_ms: 2 },
  safety_validation: { node_conflicts: 0, edge_conflicts: 0, geometric_conflicts: 0, lift_resource_conflicts: 0, timing_violations: 0, safe: true }, unresolved_conflicts: [], failure_reason: null, failure: null, vehicle_initial_config: multiVehicleRequests, lift_initial_state: { 'shuttle-lift-a': 1, 'shuttle-lift-b': 1 }, time_contract: { time_step_seconds: 0.25 },
}

function renderWorkspace(onMode = vi.fn(), onDemoMode = vi.fn(), demoMode: 'shuttle_single' | 'shuttle_multi_preset' | 'shuttle_multi_custom' = 'shuttle_single') {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ShuttleDemoWorkspace
      topologyRecord={record}
      topologies={[record]}
      onTopologyChange={vi.fn()}
      onWorkspaceModeChange={onMode}
      demoMode={demoMode}
      onDemoModeChange={onDemoMode}
    />
  </QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  useShuttleWorkspaceStore.setState({ drafts: {} })
  vi.mocked(api.getShuttleLevels).mockResolvedValue({
    topology_id: topology.id, topology_name: '666', topology_revision: '2.0.0:hash', topology_version: '2.0.0', config_revision: 'shuttle-4l-lift-v2', coordinate_contract_revision: 'shuttle-internal-levels-v1',
    data_source: 'topology_slot_aisles', verification_status: 'program_verified_pending_human', planning_enabled: true,
    lift_candidate_count: 5, lift_selection_basis: ['north/south coverage'],
    levels: [1, 2, 3, 4].map((level) => ({ shuttle_level: level as ShuttleLevel, node_count: 3, component_count: 1, component_sizes: [3] })), warnings: ['人工复核'],
  })
  vi.mocked(api.getShuttleTracks).mockImplementation(async (_topologyId, level) => ({
    topology_id: topology.id,
    topology_revision: '2.0.0:hash',
    config_revision: 'shuttle-4l-lift-v2',
    coordinate_contract_revision: 'shuttle-internal-levels-v1',
    nodes: tracks.filter((node) => node.shuttle_level === level),
  }))
  vi.mocked(api.getShuttleLifts).mockResolvedValue({ topology_id: topology.id, topology_revision: '2.0.0:hash', config_revision: 'shuttle-4l-lift-v2', coordinate_contract_revision: 'shuttle-internal-levels-v1', planning_enabled: true, lifts: [lift], warnings: [] })
  vi.mocked(api.planShuttlePath).mockResolvedValue(plan)
  vi.mocked(api.getShuttleMultiScenarios).mockResolvedValue({
    topology_id: topology.id, topology_revision: '2.0.0:hash', shuttle_config_revision: 'shuttle-4l-lift-v2', coordinate_contract_version: 'shuttle-internal-levels-v1',
    scenarios: [
      { scenario_id: 'parallel', name: '场景 A：分层无冲突并行运行', description: '并行', vehicle_requests: multiVehicleRequests, evidence: {} },
      { scenario_id: 'track-conflict', name: '场景 B：同层交叉轨道竞争', description: '交叉', vehicle_requests: multiVehicleRequests, evidence: {} },
      { scenario_id: 'lift-conflict', name: '场景 C：Lift-A 资源竞争', description: '提升机', vehicle_requests: multiVehicleRequests, evidence: {} },
    ],
  })
  vi.mocked(api.planShuttleMulti).mockResolvedValue(multiPlan)
})

describe('ShuttleDemoWorkspace', () => {
  it('shows four real levels, fixed lifts, and the isolated shuttle scene', async () => {
    renderWorkspace()
    expect(await screen.findByText('四层真实轨道 · Lift-A / Lift-B')).toBeInTheDocument()
    expect(screen.getByLabelText('演示模式')).toHaveValue('shuttle_single')
    expect(screen.queryByRole('button', { name: '进入 CTU 平面道路与多车实验' })).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-track-count', '12'))
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-lift-count', '1')
    expect(screen.getAllByRole('option', { name: 'L4' })).toHaveLength(2)
  })

  it('submits the same selected nodes to A* and renders the returned lift path', async () => {
    renderWorkspace()
    await waitFor(() => expect(screen.getByLabelText('四向车起点轨道节点')).toHaveValue('L1-r100-c7'))
    fireEvent.click(screen.getByRole('button', { name: '规划跨层路径' }))
    await waitFor(() => expect(api.planShuttlePath).toHaveBeenCalledWith(expect.objectContaining({
      start_level: 1, end_level: 4, algorithm: 'astar', start_track_node: 'L1-r100-c7', end_track_node: 'L4-r100-c51',
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
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute(
      'data-camera-target',
      `19,${shuttleLevelToWorldY(1) + SHUTTLE_VEHICLE_CENTER_HEIGHT},7`,
    )
    fireEvent.click(screen.getByRole('button', { name: '规划跨层路径' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '跟随四向车' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '跟随四向车' }))
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-camera-mode', 'follow')
  })

  it('offers a visual-only rack xray without changing the route input', async () => {
    renderWorkspace()
    await screen.findByTestId('shuttle-scene')
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-rack-xray', 'false')

    fireEvent.click(screen.getByRole('button', { name: '透视货架：关' }))

    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-rack-xray', 'true')
    expect(api.planShuttlePath).not.toHaveBeenCalled()
  })

  it('blocks execution when verification is not valid and only offers Shuttle modes', async () => {
    vi.mocked(api.getShuttleLevels).mockResolvedValueOnce({
      topology_id: topology.id, topology_name: '666', topology_revision: 'stale', topology_version: '2.0.0', config_revision: 'v0', coordinate_contract_revision: 'shuttle-internal-levels-v1',
      data_source: 'topology_slot_aisles', verification_status: 'topology_version_mismatch', planning_enabled: false, levels: [], warnings: ['版本不匹配'],
      lift_candidate_count: 0, lift_selection_basis: [],
    })
    const onMode = vi.fn()
    const onDemoMode = vi.fn()
    renderWorkspace(onMode, onDemoMode)
    expect(await screen.findByText('当前轨道或提升机配置未通过程序验证，已禁止运行。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '规划跨层路径' })).toBeDisabled()
    expect(screen.queryByRole('option', { name: /CTU/ })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'shuttle_multi_preset' } })
    expect(onDemoMode).toHaveBeenCalledWith('shuttle_multi_preset')
  })

  it('does not let a late single planning response survive switching Shuttle modes', async () => {
    let resolvePlan!: (value: ShuttlePlanResponse) => void
    vi.mocked(api.planShuttlePath).mockReturnValueOnce(new Promise((resolve) => { resolvePlan = resolve }))
    const onDemoMode = vi.fn()
    renderWorkspace(vi.fn(), onDemoMode)
    await waitFor(() => expect(screen.getByLabelText('四向车终点轨道节点')).toHaveValue('L4-r100-c51'))

    fireEvent.click(screen.getByRole('button', { name: '规划跨层路径' }))
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'shuttle_multi_preset' } })
    resolvePlan(plan)

    await waitFor(() => expect(onDemoMode).toHaveBeenCalledWith('shuttle_multi_preset'))
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-plan', '')
  })

  it('restores a still-valid per-topology Shuttle configuration after remounting', async () => {
    const first = renderWorkspace()
    await waitFor(() => expect(screen.getByLabelText('四向车终点轨道节点')).toHaveValue('L4-r100-c51'))
    fireEvent.change(screen.getByLabelText('四向车起点层'), { target: { value: '4' } })
    fireEvent.change(screen.getByLabelText('四向车终点层'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('四向车路径算法'), { target: { value: 'dijkstra' } })
    await waitFor(() => expect(screen.getByLabelText('四向车起点轨道节点')).toHaveValue('L4-r100-c7'))
    first.unmount()

    renderWorkspace()
    expect(await screen.findByLabelText('四向车起点层')).toHaveValue('4')
    expect(screen.getByLabelText('四向车终点层')).toHaveValue('2')
    expect(screen.getByLabelText('四向车路径算法')).toHaveValue('dijkstra')
    await waitFor(() => expect(screen.getByLabelText('四向车起点轨道节点')).toHaveValue('L4-r100-c7'))
  })

  it('loads a real L4 endpoint list and submits the selected L4 node', async () => {
    renderWorkspace()

    fireEvent.change(await screen.findByLabelText('四向车终点层'), { target: { value: '4' } })
    const endpoint = await screen.findByLabelText('四向车终点轨道节点')
    await waitFor(() => expect(endpoint).toHaveValue('L4-r100-c51'))
    expect(api.getShuttleTracks).toHaveBeenCalledWith(topology.id, 4)
    expect(Array.from((endpoint as HTMLSelectElement).options).map((option) => option.value)).toEqual([
      'L4-r100-c7', 'L4-r100-c19', 'L4-r100-c51',
    ])

    fireEvent.click(screen.getByRole('button', { name: '规划跨层路径' }))
    await waitFor(() => expect(api.planShuttlePath).toHaveBeenCalledWith(expect.objectContaining({
      end_level: 4,
      end_track_node: 'L4-r100-c51',
    })))
  })

  it('shows an explicit empty option instead of a blank select when L4 has no nodes', async () => {
    vi.mocked(api.getShuttleTracks).mockImplementation(async (_topologyId, level) => ({
      topology_id: topology.id,
      topology_revision: '2.0.0:hash',
      config_revision: 'shuttle-4l-lift-v2',
      coordinate_contract_revision: 'shuttle-internal-levels-v1',
      nodes: level === 4 ? [] : tracks.filter((node) => node.shuttle_level === level),
    }))

    renderWorkspace()

    expect(await screen.findByRole('alert', { name: '' })).toHaveTextContent('L4 未返回可用轨道节点')
    expect(screen.getByLabelText('四向车终点轨道节点')).toHaveDisplayValue('当前层无可用轨道节点')
    expect(screen.getByLabelText('四向车终点轨道节点')).toBeDisabled()
    expect(screen.getByRole('button', { name: '规划跨层路径' })).toBeDisabled()
  })

  it('rejects a stale three-level service response with restart guidance', async () => {
    vi.mocked(api.getShuttleLevels).mockResolvedValueOnce({
      topology_id: topology.id, topology_name: '666', topology_revision: '2.0.0:hash', topology_version: '2.0.0', config_revision: 'shuttle-3l-lift-v1', coordinate_contract_revision: 'shuttle-internal-levels-v1',
      data_source: 'topology_slot_aisles', verification_status: 'program_verified_pending_human', planning_enabled: true,
      lift_candidate_count: 5, lift_selection_basis: [], levels: [], warnings: [],
    })

    renderWorkspace()

    expect(await screen.findByText(/检测到旧版 Shuttle 服务/)).toHaveTextContent('shuttle-3l-lift-v1')
    expect(screen.getByRole('button', { name: '规划跨层路径' })).toBeDisabled()
  })

  it('does not play a plan returned with the obsolete height contract', async () => {
    vi.mocked(api.planShuttlePath).mockResolvedValueOnce({
      ...plan,
      coordinate_contract_revision: undefined as unknown as 'shuttle-internal-levels-v1',
    })
    renderWorkspace()
    await waitFor(() => expect(screen.getByLabelText('四向车终点轨道节点')).toHaveValue('L4-r100-c51'))

    fireEvent.click(screen.getByRole('button', { name: '规划跨层路径' }))

    expect(await screen.findByText('规划结果使用了旧版四向车坐标契约，请重启后端后重新规划。')).toBeInTheDocument()
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-plan', '')
  })

  it('loads preset A/B/C and sends the exact selected vehicles to the real multi API', async () => {
    renderWorkspace(vi.fn(), vi.fn(), 'shuttle_multi_preset')
    expect(await screen.findByRole('option', { name: '场景 B：同层交叉轨道竞争' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('四向车预设场景'), { target: { value: 'track-conflict' } })
    fireEvent.click(screen.getByRole('button', { name: '开始多车规划' }))
    await waitFor(() => expect(api.planShuttleMulti).toHaveBeenCalledWith(expect.objectContaining({
      topology_id: topology.id, topology_revision: '2.0.0:hash', vehicle_requests: multiVehicleRequests,
    })))
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-multi-plan', 'shuttle-multi-test')
    expect(screen.getByText('潜在冲突 · 已避让')).toBeInTheDocument()
  })

  it('seeks and pauses the unified clock when an event is selected', async () => {
    renderWorkspace(vi.fn(), vi.fn(), 'shuttle_multi_preset')
    await screen.findByRole('option', { name: '场景 A：分层无冲突并行运行' })
    fireEvent.click(screen.getByRole('button', { name: '开始多车规划' }))
    const event = await screen.findByRole('button', { name: /轨道节点竞争/ })
    fireEvent.click(event)
    expect(screen.getByLabelText('统一仿真时间轴')).toHaveValue('0')
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-selected-event', 'event-1')
  })

  it('keeps the selected Shuttle stable and supports config/sidebar collapsing', async () => {
    renderWorkspace(vi.fn(), vi.fn(), 'shuttle_multi_preset')
    await screen.findByRole('option', { name: '场景 A：分层无冲突并行运行' })
    fireEvent.click(screen.getByRole('button', { name: '开始多车规划' }))
    const selector = await screen.findByLabelText('选择跟随四向车')
    fireEvent.change(selector, { target: { value: 'SHUTTLE-02' } })
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-selected-shuttle', 'SHUTTLE-02')
    fireEvent.click(screen.getByRole('button', { name: '收起路径配置' }))
    expect(screen.getByRole('button', { name: '开始多车规划' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '收起侧栏' }))
    expect(screen.getByRole('button', { name: '展开配置侧栏' })).toBeInTheDocument()
  })

  it('keeps custom vehicle algorithms independent and enforces the 2–4 vehicle boundary', async () => {
    renderWorkspace(vi.fn(), vi.fn(), 'shuttle_multi_custom')
    expect(await screen.findByText('车辆配置（2/4）')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('SHUTTLE-02 路径算法'), { target: { value: 'dijkstra' } })
    fireEvent.click(screen.getByRole('button', { name: '添加车辆' }))
    fireEvent.click(screen.getByRole('button', { name: '添加车辆' }))
    expect(await screen.findByText('车辆配置（4/4）')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加车辆' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '开始多车规划' }))
    await waitFor(() => expect(api.planShuttleMulti).toHaveBeenCalled())
    const request = vi.mocked(api.planShuttleMulti).mock.calls[0][0]
    expect(request.vehicle_requests.find((vehicle) => vehicle.vehicle_id === 'SHUTTLE-01')?.algorithm).toBe('astar')
    expect(request.vehicle_requests.find((vehicle) => vehicle.vehicle_id === 'SHUTTLE-02')?.algorithm).toBe('dijkstra')
  })

  it('marks every repeated terminal near its vehicle and blocks the request until corrected', async () => {
    renderWorkspace(vi.fn(), vi.fn(), 'shuttle_multi_custom')
    await screen.findByText('车辆配置（2/4）')

    fireEvent.change(screen.getByLabelText('SHUTTLE-02 终点层'), { target: { value: '4' } })

    const duplicateMessage = /SHUTTLE-01 与 SHUTTLE-02 均选择 L4-r100-c51/
    await waitFor(() => expect(screen.getAllByText(duplicateMessage)).toHaveLength(3))
    expect(screen.getByLabelText('SHUTTLE-01 终点')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('SHUTTLE-02 终点')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: '开始多车规划' })).toBeDisabled()
    expect(api.planShuttleMulti).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('SHUTTLE-02 终点'), { target: { value: 'L4-r100-c19' } })

    await waitFor(() => expect(screen.queryByText(duplicateMessage)).not.toBeInTheDocument())
    expect(screen.getByLabelText('SHUTTLE-01 终点')).toHaveAttribute('aria-invalid', 'false')
    expect(screen.getByRole('button', { name: '开始多车规划' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '开始多车规划' }))
    await waitFor(() => expect(api.planShuttleMulti).toHaveBeenCalledTimes(1))
  })

  it('shows a structured search limit explanation and removes an obsolete playable plan', async () => {
    renderWorkspace(vi.fn(), vi.fn(), 'shuttle_multi_preset')
    await screen.findByRole('option', { name: '场景 A：分层无冲突并行运行' })
    fireEvent.click(screen.getByRole('button', { name: '开始多车规划' }))
    await waitFor(() => expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-multi-plan', 'shuttle-multi-test'))

    vi.mocked(api.planShuttleMulti).mockResolvedValueOnce({
      ...multiPlan,
      planning_id: 'failed-plan',
      status: 'failed',
      vehicles: [],
      events: [],
      failure_reason: 'Vehicle SHUTTLE-02 exceeded max_expanded_states=400000',
      failure: {
        vehicle_id: 'SHUTTLE-02', failure_code: 'search_limit_exceeded', expanded_states: 400001,
        max_expanded_states: 400000, planning_time_ms: 1250,
        failure_reason: 'Vehicle SHUTTLE-02 exceeded max_expanded_states=400000', details: {},
      },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始多车规划' }))

    expect(await screen.findByText(/SHUTTLE-02：当前搜索配置下未找到安全路径，已达到状态展开上限/)).toHaveTextContent('400001/400000 状态')
    expect(screen.getByText(/这不等于已经证明全局无解/)).toBeInTheDocument()
    expect(screen.getByTestId('shuttle-scene')).toHaveAttribute('data-multi-plan', '')
  })
})
