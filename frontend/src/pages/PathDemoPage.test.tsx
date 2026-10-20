import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TopologyPayload } from '../types'
import { api } from '../lib/api'
import { pathDemoApi } from '../features/path-demo/pathDemoApi'
import { useMultiPathExperimentStore } from '../features/path-demo/multiPathExperimentStore'
import { createCustomVehicle, validateMultiVehicleConfiguration } from '../features/path-demo/customVehicleConfig'
import type { CameraActionRequest, CameraViewMode } from '../features/path-demo/cameraControls'
import type { MultiPathVehicleRequest, PathDemoCell, PathDemoEndpointMarker, PathDemoVehicleView, PlaybackStatus, TrajectorySample } from '../features/path-demo/types'
import { PathDemoPage } from './PathDemoPage'

vi.mock('../features/path-demo/PathDemo3DScene', () => ({
  PathDemo3DScene: ({
    cameraViewMode,
    cameraActionRequest,
    sample,
    playbackStatus,
    vehicles = [],
    followedVehicleSample,
    endpointMarkers = [],
    roadSelectionEnabled,
    onRoadCellSelect,
    selectedVehicleId,
    conflictEvent,
    inventoryMode,
    selectedInventoryNodeId,
    onInventoryNodeSelect,
  }: {
    cameraViewMode: CameraViewMode
    cameraActionRequest: CameraActionRequest | null
    sample: TrajectorySample | null
    playbackStatus: PlaybackStatus
    vehicles?: PathDemoVehicleView[]
    followedVehicleSample?: TrajectorySample | null
    endpointMarkers?: PathDemoEndpointMarker[]
    roadSelectionEnabled?: boolean
    onRoadCellSelect?: (cell: PathDemoCell) => void
    selectedVehicleId?: string
    conflictEvent?: { event_id: string } | null
    inventoryMode?: boolean
    selectedInventoryNodeId?: string | null
    onInventoryNodeSelect?: (storageNodeId: string) => void
  }) => (
    <div>
      <div
        data-testid="path-demo-scene"
        data-camera-view-mode={cameraViewMode}
        data-camera-action={cameraActionRequest?.type ?? ''}
        data-camera-action-sequence={cameraActionRequest?.sequence ?? ''}
        data-sample-world={sample?.world.join(',') ?? ''}
        data-playback-status={playbackStatus}
        data-vehicle-ids={vehicles.map((vehicle) => vehicle.vehicleId).join(',')}
        data-followed-vehicle={followedVehicleSample?.vehicleId ?? ''}
        data-followed-world={followedVehicleSample?.world.join(',') ?? ''}
        data-selected-vehicle={selectedVehicleId ?? ''}
        data-endpoint-markers={endpointMarkers.map((marker) => `${marker.vehicleId}:${marker.kind}`).join(',')}
        data-road-selection-enabled={roadSelectionEnabled ? 'true' : 'false'}
        data-conflict-event={conflictEvent?.event_id ?? ''}
        data-camera-target={cameraActionRequest?.targetWorld?.join(',') ?? ''}
        data-inventory-mode={inventoryMode ? 'true' : 'false'}
        data-selected-inventory-node={selectedInventoryNodeId ?? ''}
      />
      <button
        type="button"
        data-testid="mock-road-cell"
        onClick={() => onRoadCellSelect?.({ id: 'cell-r3-c3', floor: '1F', row: 3, col: 3, type: 'road', component_id: 'component-1' })}
      >mock road cell</button>
      <button type="button" data-testid="mock-storage-node" onClick={() => onInventoryNodeSelect?.('storage-a')}>mock storage node</button>
    </div>
  ),
}))

vi.mock('../lib/api', () => ({
  api: {
    listTopologies: vi.fn(),
    getInventorySummary: vi.fn(),
    listInventoryLocations: vi.fn(),
    getInventoryLocation: vi.fn(),
    initializeSyntheticInventory: vi.fn(),
    previewSyntheticInventorySupplement: vi.fn(),
    supplementSyntheticInventory: vi.fn(),
  },
}))
vi.mock('../features/path-demo/pathDemoApi', () => ({
  DEFAULT_MULTI_PATH_PLANNING_CONFIG: {
    time_step_seconds: 0.25,
    speed_cells_per_second: 4,
    max_time_steps: 512,
    max_expanded_states: 200000,
    vehicle_width_cells: 0.58,
    vehicle_length_cells: 0.72,
    safety_margin_cells: 0.05,
  },
  pathDemoApi: { options: vi.fn(), plan: vi.fn(), multiScenarios: vi.fn(), planMulti: vi.fn() },
}))

const topology: TopologyPayload = {
  id: 'topology-666',
  name: '666',
  meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 4, cols: 4, cellSize: 32, originX: 0, originY: 0 },
  zones: [{ id: 'zone-ctu', label: 'CTU 库区', type: 'ctu', x: 0, y: 0, width: 4, height: 4 }],
  cells: [{ id: 'cell-r1-c1', floor: '1F', row: 1, col: 1, type: 'road', zoneId: 'zone-ctu', cost: 1, walkable: true }],
  nodes: [{ id: 'storage-a', type: 'storage', floor: '1F', row: 1, col: 2, zoneId: 'zone-ctu', capacity: 1, label: '库位 A' }],
  resources: [],
}

const cells = [
  { id: 'cell-r1-c1', floor: '1F', row: 1, col: 1, type: 'road', component_id: 'component-1' },
  { id: 'cell-r1-c2', floor: '1F', row: 1, col: 2, type: 'road', component_id: 'component-1' },
  { id: 'cell-r1-c3', floor: '1F', row: 1, col: 3, type: 'road', component_id: 'component-1' },
  { id: 'cell-r2-c0', floor: '1F', row: 2, col: 0, type: 'road', component_id: 'component-1' },
  { id: 'cell-r2-c1', floor: '1F', row: 2, col: 1, type: 'road', component_id: 'component-1' },
  { id: 'cell-r2-c2', floor: '1F', row: 2, col: 2, type: 'road', component_id: 'component-1' },
  { id: 'cell-r2-c3', floor: '1F', row: 2, col: 3, type: 'road', component_id: 'component-1' },
  { id: 'cell-r3-c2', floor: '1F', row: 3, col: 2, type: 'road', component_id: 'component-1' },
  { id: 'cell-r3-c3', floor: '1F', row: 3, col: 3, type: 'road', component_id: 'component-1' },
]

const options = {
  topology_id: topology.id,
  topology_name: topology.name,
  topology_version: '2.0.0',
  components: [{ id: 'component-1', floor: '1F', cell_count: 3 }],
  largest_component_id: 'component-1',
  traversable_cells: cells,
  default_start: cells[0],
  default_goal: cells[2],
}

const inventoryLocation = {
  topology_id: topology.id,
  topology_version: topology.meta.version,
  storage_node_id: 'storage-a',
  label: '库位 A',
  zone_id: 'zone-ctu',
  zone_label: 'CTU 库区',
  zone_type: 'ctu',
  floor: '1F', row: 1, col: 2, rack_level: null,
  balances: [{
    topology_id: topology.id, zone_id: 'zone-ctu', storage_node_id: 'storage-a', storage_layer: 1,
    sku: { sku_id: 'SKU-DEMO-001', sku_name: '标准紧固件套装', category: 'fastener', specification: 'M6', unit: 'box', weight_kg: 2.4, material: 'steel', source: 'synthetic_demo' },
    quantity_on_hand: 12, quantity_reserved: 2, quantity_available: 10,
    source: 'synthetic_demo', updated_at: '2026-10-20T00:00:00Z',
  }],
  access_mapping: { topology_id: topology.id, storage_node_id: 'storage-a', access_cell_id: 'cell-r1-c1', status: 'configured' as const, source: 'manual_verified', validated_at: '2026-10-20T00:00:00Z' },
  data_sources: ['synthetic_demo'],
}

const makePlan = (algorithm: 'astar' | 'dijkstra') => ({
  topology_id: topology.id,
  topology_name: topology.name,
  topology_version: '2.0.0',
  algorithm,
  start: cells[0],
  goal: cells[2],
  path_cells: cells,
  total_distance: 2,
  total_cost: 2,
  visited_nodes: 3,
  planning_time_ms: 0.5,
})

const multiVehicles = [
  { vehicle_id: 'CTU-01', start_cell_id: cells[0].id, goal_cell_id: cells[2].id },
  { vehicle_id: 'CTU-02', start_cell_id: cells[1].id, goal_cell_id: cells[0].id },
  { vehicle_id: 'CTU-03', start_cell_id: cells[2].id, goal_cell_id: cells[1].id },
]

const multiScenarios = {
  topology_id: topology.id,
  topology_name: topology.name,
  topology_version: '2.0.0',
  largest_road_component_id: 'road-component-1',
  road_cells: cells,
  scenarios: [
    { id: 'parallel' as const, name: '场景 A：无冲突并行运行', description: 'parallel', vehicles: multiVehicles },
    { id: 'node-conflict' as const, name: '场景 B：交叉节点竞争', description: 'node', vehicles: multiVehicles },
    { id: 'edge-swap' as const, name: '场景 C：相向边交换风险', description: 'edge', vehicles: multiVehicles },
  ],
}

const multiPlan = {
  topology_id: topology.id,
  topology_name: topology.name,
  topology_version: '2.0.0',
  algorithm: 'astar' as const,
  planning_status: 'success' as const,
  error_reason: null,
  time_step_seconds: 0.25,
  speed_cells_per_second: 4,
  vehicle_width_cells: 0.58,
  vehicle_length_cells: 0.72,
  safety_distance_cells: 0.97,
  makespan_steps: 2,
  makespan_seconds: 0.5,
  vehicles: multiVehicles.map((vehicle, index) => ({
    vehicle_id: vehicle.vehicle_id,
    start: cells[index],
    goal: cells[(index + 1) % cells.length],
    steps: [
      { time_step: 0, time_seconds: 0, action: 'start' as const, cell: cells[index] },
      { time_step: 1, time_seconds: 0.25, action: 'wait' as const, cell: cells[index] },
      { time_step: 2, time_seconds: 0.5, action: 'move' as const, cell: cells[(index + 1) % cells.length] },
    ],
    total_distance: 1,
    estimated_duration_seconds: 0.5,
    wait_time_seconds: 0.25,
    avoidance_count: 1,
    visited_states: 3,
  })),
  conflicts: {
    detected_node_conflicts: 1,
    detected_edge_conflicts: 1,
    detected_geometry_conflicts: 0,
    resolved_node_conflicts: 1,
    resolved_edge_conflicts: 1,
    resolved_geometry_conflicts: 0,
    remaining_node_conflicts: 0,
    remaining_edge_conflicts: 0,
    remaining_geometry_conflicts: 0,
  },
  conflict_events: [{
    event_id: 'node-CTU-02-CTU-01-1-cell-r1-c2',
    conflict_type: 'node' as const,
    status: 'resolved' as const,
    time_step: 1,
    time_seconds: 0.25,
    cell: cells[1],
    from_cell: cells[1],
    involved_vehicle_ids: ['CTU-01', 'CTU-02'],
    priority_vehicle_id: 'CTU-01',
    priority: 1,
    yielding_vehicle_id: 'CTU-02',
    yielding_priority: 2,
    reservation_action: 'candidate_rejected' as const,
    avoidance_action: 'wait' as const,
    wait_cell: cells[1],
    wait_start_time_seconds: 0,
    wait_end_time_seconds: 0.25,
    wait_duration_seconds: 0.25,
    result: 'conflict_free_plan' as const,
  }],
  avoidance_count: 3,
  planning_time_ms: 2,
}

const sixVehiclePlan = {
  ...multiPlan,
  vehicles: Array.from({ length: 6 }, (_, index) => {
    const vehicleId = `CTU-0${index + 1}`
    const plannedStart = index === 5 ? cells[8] : cells[index]
    const plannedGoal = cells[(index + 1) % cells.length]
    return {
      vehicle_id: vehicleId,
      start: plannedStart,
      goal: plannedGoal,
      steps: [
        { time_step: 0, time_seconds: 0, action: 'start' as const, cell: plannedStart },
        { time_step: 1, time_seconds: 0.25, action: 'wait' as const, cell: plannedStart },
        { time_step: 2, time_seconds: 0.5, action: 'move' as const, cell: plannedGoal },
      ],
      total_distance: 1,
      estimated_duration_seconds: 0.5,
      wait_time_seconds: 0.25,
      avoidance_count: 1,
      visited_states: 3,
    }
  }),
}
function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PathDemoPage />
    </QueryClientProvider>,
  )
}

async function enterCustomModeWithSixVehicles() {
  fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'custom' } })
  const addButton = await screen.findByRole('button', { name: '添加车辆' })
  await waitFor(() => expect(screen.getByText('车辆配置（3/6）')).toBeInTheDocument())
  fireEvent.click(addButton)
  fireEvent.click(addButton)
  fireEvent.click(addButton)
  await waitFor(() => expect(screen.getByText('车辆配置（6/6）')).toBeInTheDocument())
}

beforeEach(() => {
  useMultiPathExperimentStore.setState({ customDraft: null, evaluation: null, viewedAlgorithm: null })
  vi.mocked(api.listTopologies).mockResolvedValue([{ id: topology.id, name: topology.name, payload: topology }])
  vi.mocked(api.getInventorySummary).mockResolvedValue({
    topology_id: topology.id, storage_location_count: 1, stocked_location_count: 1,
    positive_inventory_location_count: 1, unconfigured_location_count: 0, zero_inventory_location_count: 0,
    sku_count: 1, quantity_on_hand: 12, quantity_reserved: 2, quantity_available: 10,
    data_sources: ['synthetic_demo'],
  })
  vi.mocked(api.listInventoryLocations).mockResolvedValue([inventoryLocation])
  vi.mocked(api.getInventoryLocation).mockResolvedValue(inventoryLocation)
  vi.mocked(api.previewSyntheticInventorySupplement).mockResolvedValue({
    topology_id: topology.id, eligible_location_count: 0, requested_location_count: 100,
    estimated_insert_count: 0, fill_all: false,
    before: { topology_id: topology.id, storage_location_count: 1, stocked_location_count: 1, positive_inventory_location_count: 1, unconfigured_location_count: 0, zero_inventory_location_count: 0, sku_count: 1, quantity_on_hand: 12, quantity_reserved: 2, quantity_available: 10, data_sources: ['synthetic_demo'] },
  })
  vi.mocked(pathDemoApi.options).mockResolvedValue(options)
  vi.mocked(pathDemoApi.plan).mockImplementation(async (_topology, _start, _goal, algorithm) => makePlan(algorithm))
  vi.mocked(pathDemoApi.multiScenarios).mockResolvedValue(multiScenarios)
  vi.mocked(pathDemoApi.planMulti).mockResolvedValue(multiPlan)
})
describe('PathDemoPage', () => {
  it('shares the current custom vehicle DTO with the algorithm lab store', async () => {
    renderPage()
    await enterCustomModeWithSixVehicles()
    await waitFor(() => expect(useMultiPathExperimentStore.getState().customDraft?.vehicles).toHaveLength(6))
    expect(useMultiPathExperimentStore.getState().customDraft).toMatchObject({
      topologyId: topology.id,
      topologyVersion: topology.meta.version,
      planningConfig: { time_step_seconds: 0.25, speed_cells_per_second: 4 },
    })
    expect(useMultiPathExperimentStore.getState().customDraft?.vehicles[5].vehicle_id).toBe('CTU-06')
  })

  it('loads 666 defaults, plans a real path, and invalidates it when the algorithm changes', async () => {
    renderPage()
    expect(await screen.findByText('真实拓扑单车 3D 轨迹演示')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('起点（可行驶道路）')).toHaveValue(cells[0].id))

    fireEvent.click(screen.getByRole('button', { name: '规划路径' }))
    expect(await screen.findByText('2 格')).toBeInTheDocument()
    expect(pathDemoApi.plan).toHaveBeenLastCalledWith(topology.id, cells[0].id, cells[2].id, 'astar')

    fireEvent.change(screen.getByLabelText('路径算法'), { target: { value: 'dijkstra' } })
    expect(screen.queryByText('2 格')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '规划路径' }))
    await waitFor(() => expect(pathDemoApi.plan).toHaveBeenLastCalledWith(topology.id, cells[0].id, cells[2].id, 'dijkstra'))
  })

  it('shows an unreachable-path error without inventing a trajectory', async () => {
    vi.mocked(pathDemoApi.plan).mockRejectedValueOnce(new Error('different connected components'))
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('终点（可行驶道路）')).toHaveValue(cells[2].id))
    fireEvent.click(screen.getByRole('button', { name: '规划路径' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('different connected components')
    expect(screen.getByRole('button', { name: '开始运行' })).toBeDisabled()
  })

  it('disables vehicle camera actions until the rendered vehicle has a position', async () => {
    renderPage()
    await screen.findByTestId('path-demo-scene')
    expect(screen.getByRole('button', { name: '定位小车' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '跟随小车：关' })).toBeDisabled()
    expect(screen.getByText('请先规划路径，生成车辆位置后再定位或跟随。')).toBeInTheDocument()
  })

  it('uses the rendered trajectory sample for locate and preserves follow across vehicle reset', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('起点（可行驶道路）')).toHaveValue(cells[0].id))
    fireEvent.click(screen.getByRole('button', { name: '规划路径' }))
    const scene = await screen.findByTestId('path-demo-scene')
    await waitFor(() => expect(scene).toHaveAttribute('data-sample-world', '-0.5,0.5,0.5'))

    fireEvent.click(screen.getByRole('button', { name: '定位小车' }))
    expect(scene).toHaveAttribute('data-camera-action', 'locate')

    fireEvent.click(screen.getByRole('button', { name: '跟随小车：关' }))
    expect(scene).toHaveAttribute('data-camera-view-mode', 'follow')
    fireEvent.click(screen.getByRole('button', { name: '重置' }))
    expect(scene).toHaveAttribute('data-camera-view-mode', 'follow')
    expect(scene).toHaveAttribute('data-playback-status', 'idle')
  })

  it('exits follow without resetting the camera and uses overview as an explicit action', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('终点（可行驶道路）')).toHaveValue(cells[2].id))
    fireEvent.click(screen.getByRole('button', { name: '规划路径' }))
    const scene = await screen.findByTestId('path-demo-scene')

    fireEvent.click(screen.getByRole('button', { name: '跟随小车：关' }))
    fireEvent.click(screen.getByRole('button', { name: '自由视角' }))
    expect(scene).toHaveAttribute('data-camera-view-mode', 'free')
    expect(scene).toHaveAttribute('data-camera-action', '')

    fireEvent.click(screen.getByRole('button', { name: '跟随小车：关' }))
    fireEvent.click(screen.getByRole('button', { name: '仓库全景' }))
    expect(scene).toHaveAttribute('data-camera-view-mode', 'free')
    expect(scene).toHaveAttribute('data-camera-action', 'overview')
  })

  it('stops following an invalidated path', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('终点（可行驶道路）')).toHaveValue(cells[2].id))
    fireEvent.click(screen.getByRole('button', { name: '规划路径' }))
    const scene = await screen.findByTestId('path-demo-scene')
    await waitFor(() => expect(screen.getByRole('button', { name: '跟随小车：关' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '跟随小车：关' }))
    expect(scene).toHaveAttribute('data-camera-view-mode', 'follow')

    fireEvent.change(screen.getByLabelText('路径算法'), { target: { value: 'dijkstra' } })
    expect(scene).toHaveAttribute('data-camera-view-mode', 'free')
    expect(scene).toHaveAttribute('data-sample-world', '')
  })

  it('plans and renders three backend-scheduled vehicles with selectable follow targets', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('终点（可行驶道路）')).toHaveValue(cells[2].id))
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'preset' } })
    await waitFor(() => expect(screen.getByRole('combobox', { name: '冲突演示场景' })).toHaveValue('parallel'))
    expect(screen.getByRole('option', { name: '场景 B：交叉节点竞争' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '场景 C：相向边交换风险' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '开始多车规划' }))
    const scene = await screen.findByTestId('path-demo-scene')
    await waitFor(() => expect(scene).toHaveAttribute('data-vehicle-ids', 'CTU-01,CTU-02,CTU-03'))
    expect(pathDemoApi.planMulti).toHaveBeenCalledWith({
      topologyId: topology.id,
      algorithm: 'astar',
      mode: 'preset',
      scenarioId: 'parallel',
    })
    expect(screen.getByText('检测节点冲突')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('选择跟随车辆'), { target: { value: 'CTU-02' } })
    expect(scene).toHaveAttribute('data-followed-vehicle', 'CTU-02')
    expect(scene).toHaveAttribute('data-camera-action', 'locate')
  })

  it('shows the real scene-B node event, seeks before it, and focuses its backend cell', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'preset' } })
    const scenarioSelect = await screen.findByRole('combobox', { name: '冲突演示场景' })
    await waitFor(() => expect(scenarioSelect).toHaveValue('parallel'))
    fireEvent.change(scenarioSelect, { target: { value: 'node-conflict' } })
    await waitFor(() => expect(scenarioSelect).toHaveValue('node-conflict'))
    fireEvent.click(screen.getByRole('button', { name: '开始多车规划' }))

    expect(await screen.findByRole('region', { name: '冲突避让事件' })).toHaveTextContent('cell-r1-c2')
    expect(screen.getByText('规划候选冲突，不是实际碰撞')).toBeInTheDocument()
    expect(screen.getByText(/CTU-01 P1 → CTU-02 P2/)).toBeInTheDocument()
    expect(screen.getByTestId('path-demo-scene')).toHaveAttribute('data-conflict-event', 'node-CTU-02-CTU-01-1-cell-r1-c2')

    fireEvent.change(screen.getByLabelText('播放速度'), { target: { value: '2' } })
    fireEvent.click(screen.getByLabelText('竞争事件自动慢放'))
    await waitFor(() => expect(screen.getByLabelText('播放速度')).toHaveValue('0.25'))
    expect(screen.getByText('总等待时间').nextElementSibling).toHaveTextContent('0.75 s')

    fireEvent.click(screen.getByRole('button', { name: '定位该事件' }))
    const scene = screen.getByTestId('path-demo-scene')
    expect(scene).toHaveAttribute('data-camera-action', 'focus-point')
    expect(scene).toHaveAttribute('data-camera-target', '0.5,0.22,0.5')
    expect(scene).toHaveAttribute('data-playback-status', 'paused')
    expect(screen.getAllByText(/等待让行/).length).toBeGreaterThan(0)
  })

  it('shows a multi-vehicle no-solution response without starting playback', async () => {
    vi.mocked(pathDemoApi.planMulti).mockRejectedValueOnce(new Error('No conflict-free space-time path found'))
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'preset' } })
    const planButton = screen.getByRole('button', { name: '开始多车规划' })
    await waitFor(() => expect(planButton).toBeEnabled())
    fireEvent.click(planButton)
    expect(await screen.findByRole('alert')).toHaveTextContent('No conflict-free space-time path found')
    expect(screen.getByRole('button', { name: '开始运行' })).toBeDisabled()
  })

  it('adds and deletes custom vehicles without affecting the preset list', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'custom' } })
    const addButton = await screen.findByRole('button', { name: '添加车辆' })
    await waitFor(() => expect(screen.getByText('车辆配置（3/6）')).toBeInTheDocument())
    fireEvent.click(addButton)
    fireEvent.click(addButton)
    fireEvent.click(addButton)
    expect(screen.getByText('车辆配置（6/6）')).toBeInTheDocument()
    expect(addButton).toBeDisabled()
    expect(screen.getAllByDisplayValue('CTU-06')).toHaveLength(2)

    const deleteButtons = screen.getAllByRole('button', { name: '删除车辆' })
    fireEvent.click(deleteButtons.at(-1)!)
    expect(screen.getByText('车辆配置（5/6）')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'preset' } })
    expect(await screen.findByRole('combobox', { name: '冲突演示场景' })).toHaveValue('parallel')
    expect(screen.getByText('场景 A：无冲突并行运行')).toBeInTheDocument()
  })

  it('selects a verified road cell for the active vehicle and exposes endpoint markers', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'custom' } })
    await waitFor(() => expect(screen.getByText('车辆配置（3/6）')).toBeInTheDocument())
    fireEvent.click(screen.getAllByRole('button', { name: '图选起点' })[0])
    expect(screen.getByRole('status')).toHaveTextContent('正在为 CTU-01 选择起点')
    expect(screen.getByTestId('path-demo-scene')).toHaveAttribute('data-road-selection-enabled', 'true')
    fireEvent.click(screen.getByTestId('mock-road-cell'))
    expect(screen.getByLabelText('CTU-01 起点')).toHaveValue('cell-r3-c3')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByTestId('path-demo-scene')).toHaveAttribute('data-endpoint-markers', expect.stringContaining('CTU-01:start'))
  })

  it('ignores an obsolete planning response after the mode changes', async () => {
    let resolvePlan: ((value: typeof multiPlan) => void) | undefined
    vi.mocked(pathDemoApi.planMulti).mockImplementationOnce(() => new Promise((resolve) => { resolvePlan = resolve }))
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'preset' } })
    const planButton = await screen.findByRole('button', { name: '开始多车规划' })
    await waitFor(() => expect(planButton).toBeEnabled())
    fireEvent.click(planButton)
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'single' } })
    resolvePlan?.(multiPlan)
    await waitFor(() => expect(screen.getByTestId('path-demo-scene')).toHaveAttribute('data-vehicle-ids', ''))
    expect(screen.getByText('未规划')).toBeInTheDocument()
  })

  it('keeps CTU-06 selected and locates its configured start before planning', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))
    await enterCustomModeWithSixVehicles()

    const vehicleSelect = screen.getByLabelText('选择跟随车辆')
    const scene = screen.getByTestId('path-demo-scene')
    expect(vehicleSelect).toHaveValue('CTU-06')
    expect(screen.getByText('当前车辆：CTU-06')).toBeInTheDocument()
    expect(scene).toHaveAttribute('data-selected-vehicle', 'CTU-06')
    expect(scene).toHaveAttribute('data-followed-vehicle', 'CTU-06')
    expect(scene).toHaveAttribute('data-followed-world', '0.5,0.5,-1.5')
    expect(screen.getByText('当前车辆尚未规划路径，可定位到已配置起点。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '定位小车' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '跟随小车：关' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '定位小车' }))
    expect(scene).toHaveAttribute('data-camera-action', 'locate')

    fireEvent.change(screen.getByLabelText('CTU-01 优先级'), { target: { value: '9' } })
    expect(vehicleSelect).toHaveValue('CTU-06')

    const ctu02Fieldset = screen.getByDisplayValue('CTU-02').closest('fieldset')
    expect(ctu02Fieldset).not.toBeNull()
    fireEvent.click(within(ctu02Fieldset!).getByRole('button', { name: '删除车辆' }))
    expect(vehicleSelect).toHaveValue('CTU-06')
    expect(screen.getByText('当前车辆：CTU-06')).toBeInTheDocument()

    const ctu06Input = screen.getAllByDisplayValue('CTU-06').find((element) => element.tagName === 'INPUT')
    const ctu06Fieldset = ctu06Input?.closest('fieldset')
    expect(ctu06Fieldset).not.toBeNull()
    fireEvent.click(within(ctu06Fieldset!).getByRole('button', { name: '删除车辆' }))
    expect(vehicleSelect).toHaveValue('CTU-01')
    expect(screen.getByText('当前车辆：CTU-01')).toBeInTheDocument()
  })

  it('switches FOLLOW from CTU-01 to CTU-06 and CTU-03 using planned samples', async () => {
    vi.mocked(pathDemoApi.planMulti).mockResolvedValueOnce(sixVehiclePlan)
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))
    await enterCustomModeWithSixVehicles()
    fireEvent.click(screen.getByRole('button', { name: '开始多车规划' }))

    const scene = screen.getByTestId('path-demo-scene')
    const vehicleSelect = screen.getByLabelText('选择跟随车辆')
    await waitFor(() => expect(scene).toHaveAttribute('data-vehicle-ids', 'CTU-01,CTU-02,CTU-03,CTU-04,CTU-05,CTU-06'))

    fireEvent.change(vehicleSelect, { target: { value: 'CTU-01' } })
    fireEvent.click(screen.getByRole('button', { name: '跟随小车：关' }))
    expect(scene).toHaveAttribute('data-camera-view-mode', 'follow')

    fireEvent.change(vehicleSelect, { target: { value: 'CTU-06' } })
    expect(vehicleSelect).toHaveValue('CTU-06')
    expect(screen.getByText('当前车辆：CTU-06')).toBeInTheDocument()
    expect(scene).toHaveAttribute('data-followed-vehicle', 'CTU-06')
    expect(scene).toHaveAttribute('data-followed-world', '1.5,0.5,-1.5')
    expect(scene).toHaveAttribute('data-camera-action', 'switch-follow-target')

    fireEvent.change(vehicleSelect, { target: { value: 'CTU-03' } })
    expect(vehicleSelect).toHaveValue('CTU-03')
    expect(scene).toHaveAttribute('data-followed-vehicle', 'CTU-03')
    expect(scene).toHaveAttribute('data-camera-view-mode', 'follow')

    fireEvent.click(screen.getByRole('button', { name: '开始运行' }))
    const pauseButton = await screen.findByRole('button', { name: '暂停' })
    fireEvent.click(pauseButton)
    expect(await screen.findByRole('button', { name: '继续' })).toBeInTheDocument()
    expect(vehicleSelect).toHaveValue('CTU-03')
    fireEvent.click(screen.getByRole('button', { name: '继续' }))
    expect(vehicleSelect).toHaveValue('CTU-03')
  })

  it('disables unplanned locate when the selected custom vehicle has no legal start', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))
    await enterCustomModeWithSixVehicles()
    fireEvent.change(screen.getByLabelText('CTU-06 起点'), { target: { value: '' } })
    expect(screen.getByLabelText('选择跟随车辆')).toHaveValue('CTU-06')
    expect(screen.getByRole('button', { name: '定位小车' })).toBeDisabled()
    expect(screen.getByText('当前车辆尚未配置合法起点，无法定位。')).toBeInTheDocument()
  })

  it('does not let an obsolete custom plan response replace CTU-06 selection', async () => {
    let resolvePlan: ((value: typeof sixVehiclePlan) => void) | undefined
    vi.mocked(pathDemoApi.planMulti).mockImplementationOnce(() => new Promise((resolve) => { resolvePlan = resolve }))
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))
    await enterCustomModeWithSixVehicles()
    fireEvent.click(screen.getByRole('button', { name: '开始多车规划' }))
    fireEvent.change(screen.getByLabelText('CTU-01 优先级'), { target: { value: '8' } })
    resolvePlan?.(sixVehiclePlan)

    await waitFor(() => expect(screen.getByText('未规划')).toBeInTheDocument())
    expect(screen.getByLabelText('选择跟随车辆')).toHaveValue('CTU-06')
    expect(screen.getByTestId('path-demo-scene')).toHaveAttribute('data-vehicle-ids', '')
  })

  it('shares the selected topology with inventory mode and locates real storage and access coordinates', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))

    fireEvent.click(screen.getByRole('tab', { name: '库存查看' }))

    await waitFor(() => expect(api.getInventorySummary).toHaveBeenCalledWith(topology.id))
    expect(api.listInventoryLocations).toHaveBeenCalledWith(topology.id)
    await waitFor(() => expect(screen.getByText('正库存')).toBeInTheDocument())
    expect(screen.getByText('12', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByLabelText('搜索 SKU 或库位')).toBeInTheDocument()
    expect(screen.getByTestId('path-demo-scene')).toHaveAttribute('data-inventory-mode', 'true')

    fireEvent.click(screen.getByTestId('mock-storage-node'))
    expect(await screen.findByText('标准紧固件套装')).toBeInTheDocument()
    expect(screen.getByTestId('path-demo-scene')).toHaveAttribute('data-selected-inventory-node', 'storage-a')
    expect(screen.getByTestId('path-demo-scene')).toHaveAttribute('data-camera-target', '0.5,0.5,0.5')

    fireEvent.click(screen.getByRole('button', { name: '查看停靠点' }))
    expect(screen.getByTestId('path-demo-scene')).toHaveAttribute('data-camera-target', '-0.5,0.22,0.5')

    fireEvent.click(screen.getByRole('tab', { name: '轨迹规划' }))
    expect(screen.getByTestId('path-demo-scene')).toHaveAttribute('data-inventory-mode', 'false')
    expect(screen.getByLabelText('演示模式')).toBeInTheDocument()
  })

  it('shows inventory API failures without affecting the planning workspace', async () => {
    vi.mocked(api.getInventorySummary).mockRejectedValueOnce(new Error('summary unavailable'))
    vi.mocked(api.listInventoryLocations).mockRejectedValueOnce(new Error('locations unavailable'))
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))

    fireEvent.click(screen.getByRole('tab', { name: '库存查看' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('库存加载失败')

    fireEvent.click(screen.getByRole('tab', { name: '轨迹规划' }))
    expect(screen.getByLabelText('演示模式')).toBeInTheDocument()
  })

  it('switches inventory queries with topology ID and ignores the late previous response', async () => {
    const topologyB = { ...topology, id: 'topology-b', name: 'Warehouse B' }
    vi.mocked(api.listTopologies).mockResolvedValue([
      { id: topology.id, name: topology.name, payload: topology },
      { id: topologyB.id, name: topologyB.name, payload: topologyB },
    ])
    const summaryForOld = {
      topology_id: topology.id, storage_location_count: 1, stocked_location_count: 1,
      positive_inventory_location_count: 1, unconfigured_location_count: 0, zero_inventory_location_count: 0,
      sku_count: 1, quantity_on_hand: 12, quantity_reserved: 2, quantity_available: 10,
      data_sources: ['synthetic_demo'],
    }
    const summaryForB = { ...summaryForOld, topology_id: topologyB.id, quantity_on_hand: 77, quantity_available: 75 }
    let resolveOldSummary: ((value: typeof summaryForOld) => void) | undefined
    let resolveOldLocations: ((value: typeof inventoryLocation[]) => void) | undefined
    vi.mocked(api.getInventorySummary).mockImplementation((topologyId) => (
      topologyId === topology.id
        ? new Promise((resolve) => { resolveOldSummary = resolve })
        : Promise.resolve(summaryForB)
    ))
    vi.mocked(api.listInventoryLocations).mockImplementation((topologyId) => (
      topologyId === topology.id
        ? new Promise((resolve) => { resolveOldLocations = resolve })
        : Promise.resolve([])
    ))

    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))
    fireEvent.click(screen.getByRole('tab', { name: '库存查看' }))
    fireEvent.change(screen.getByLabelText('拓扑'), { target: { value: topologyB.id } })

    await waitFor(() => expect(api.getInventorySummary).toHaveBeenCalledWith(topologyB.id))
    await waitFor(() => expect(screen.getByText('77', { selector: 'strong' })).toBeInTheDocument())
    await act(async () => {
      resolveOldSummary?.(summaryForOld)
      resolveOldLocations?.([inventoryLocation])
    })
    expect(screen.getByText('77', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.queryByText('12', { selector: 'strong' })).not.toBeInTheDocument()
  })
})

describe('custom multi-vehicle configuration helpers', () => {
  it('allocates unique IDs up to six vehicles and validates duplicate endpoints', () => {
    let vehicles: MultiPathVehicleRequest[] = multiVehicles.map((vehicle, index) => ({ ...vehicle, vehicle_type: 'ctu', priority: index + 1 }))
    for (let count = 4; count <= 6; count += 1) {
      const next = createCustomVehicle(vehicles, cells)
      expect(next?.vehicle_id).toBe(`CTU-0${count}`)
      vehicles = [...vehicles, next!]
    }
    expect(createCustomVehicle(vehicles, cells)).toBeNull()
    expect(validateMultiVehicleConfiguration(vehicles.slice(0, 5), cells)).toBe('')
    expect(validateMultiVehicleConfiguration([
      vehicles[0],
      { ...vehicles[1], start_cell_id: vehicles[0].start_cell_id },
    ], cells)).toContain('同一个起点')
  })
})
