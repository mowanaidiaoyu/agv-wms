import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TopologyPayload } from '../types'
import { api } from '../lib/api'
import { pathDemoApi } from '../features/path-demo/pathDemoApi'
import type { CameraActionRequest, CameraViewMode } from '../features/path-demo/cameraControls'
import type { PathDemoVehicleView, PlaybackStatus, TrajectorySample } from '../features/path-demo/types'
import { PathDemoPage } from './PathDemoPage'

vi.mock('../features/path-demo/PathDemo3DScene', () => ({
  PathDemo3DScene: ({
    cameraViewMode,
    cameraActionRequest,
    sample,
    playbackStatus,
    vehicles = [],
    followedVehicleSample,
  }: {
    cameraViewMode: CameraViewMode
    cameraActionRequest: CameraActionRequest | null
    sample: TrajectorySample | null
    playbackStatus: PlaybackStatus
    vehicles?: PathDemoVehicleView[]
    followedVehicleSample?: TrajectorySample | null
  }) => (
    <div
      data-testid="path-demo-scene"
      data-camera-view-mode={cameraViewMode}
      data-camera-action={cameraActionRequest?.type ?? ''}
      data-camera-action-sequence={cameraActionRequest?.sequence ?? ''}
      data-sample-world={sample?.world.join(',') ?? ''}
      data-playback-status={playbackStatus}
      data-vehicle-ids={vehicles.map((vehicle) => vehicle.vehicleId).join(',')}
      data-followed-vehicle={followedVehicleSample?.vehicleId ?? ''}
    />
  ),
}))

vi.mock('../lib/api', () => ({ api: { listTopologies: vi.fn() } }))
vi.mock('../features/path-demo/pathDemoApi', () => ({
  pathDemoApi: { options: vi.fn(), plan: vi.fn(), multiScenarios: vi.fn(), planMulti: vi.fn() },
}))

const topology: TopologyPayload = {
  id: 'topology-666',
  name: '666',
  meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 4, cols: 4, cellSize: 32, originX: 0, originY: 0 },
  zones: [],
  cells: [],
  nodes: [],
  resources: [],
}

const cells = [
  { id: 'cell-r1-c1', floor: '1F', row: 1, col: 1, type: 'road', component_id: 'component-1' },
  { id: 'cell-r1-c2', floor: '1F', row: 1, col: 2, type: 'road', component_id: 'component-1' },
  { id: 'cell-r1-c3', floor: '1F', row: 1, col: 3, type: 'road', component_id: 'component-1' },
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
  avoidance_count: 3,
  planning_time_ms: 2,
}
function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PathDemoPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.listTopologies).mockResolvedValue([{ id: topology.id, name: topology.name, payload: topology }])
  vi.mocked(pathDemoApi.options).mockResolvedValue(options)
  vi.mocked(pathDemoApi.plan).mockImplementation(async (_topology, _start, _goal, algorithm) => makePlan(algorithm))
  vi.mocked(pathDemoApi.multiScenarios).mockResolvedValue(multiScenarios)
  vi.mocked(pathDemoApi.planMulti).mockResolvedValue(multiPlan)
})
describe('PathDemoPage', () => {
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
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'multi' } })
    await waitFor(() => expect(screen.getByRole('combobox', { name: '冲突演示场景' })).toHaveValue('parallel'))
    expect(screen.getByRole('option', { name: '场景 B：交叉节点竞争' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '场景 C：相向边交换风险' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '规划多车路径' }))
    const scene = await screen.findByTestId('path-demo-scene')
    await waitFor(() => expect(scene).toHaveAttribute('data-vehicle-ids', 'CTU-01,CTU-02,CTU-03'))
    expect(pathDemoApi.planMulti).toHaveBeenCalledWith(topology.id, multiVehicles, 'astar')
    expect(screen.getByText('最终执行冲突')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('选择跟随车辆'), { target: { value: 'CTU-02' } })
    expect(scene).toHaveAttribute('data-followed-vehicle', 'CTU-02')
    expect(scene).toHaveAttribute('data-camera-action', 'locate')
  })

  it('shows a multi-vehicle no-solution response without starting playback', async () => {
    vi.mocked(pathDemoApi.planMulti).mockRejectedValueOnce(new Error('No conflict-free space-time path found'))
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('演示模式')).toHaveValue('single'))
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'multi' } })
    const planButton = screen.getByRole('button', { name: '规划多车路径' })
    await waitFor(() => expect(planButton).toBeEnabled())
    fireEvent.click(planButton)
    expect(await screen.findByRole('alert')).toHaveTextContent('No conflict-free space-time path found')
    expect(screen.getByRole('button', { name: '开始运行' })).toBeDisabled()
  })
})
