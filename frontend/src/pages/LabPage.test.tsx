import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMultiPathExperimentStore } from '../features/path-demo/multiPathExperimentStore'
import { pathDemoApi } from '../features/path-demo/pathDemoApi'
import type { MultiPathEvaluation, MultiPathPlan, MultiPathVehicleRequest, PathDemoCell } from '../features/path-demo/types'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'
import type { TopologyPayload } from '../types'
import { LabPage } from './LabPage'

vi.mock('../components/charts/PolicyRadar', () => ({ PolicyRadar: () => <div data-testid="policy-radar" /> }))
vi.mock('../components/charts/MultiPathComparisonChart', () => ({ MultiPathComparisonChart: () => <div data-testid="multi-path-chart" /> }))
vi.mock('../lib/api', () => ({
  api: { listScenarios: vi.fn(), listTopologies: vi.fn(), evaluateAlgorithms: vi.fn() },
}))
vi.mock('../features/path-demo/pathDemoApi', () => ({
  DEFAULT_MULTI_PATH_PLANNING_CONFIG: {
    time_step_seconds: 0.25, speed_cells_per_second: 4, max_time_steps: 512, max_expanded_states: 200000,
    vehicle_width_cells: 0.58, vehicle_length_cells: 0.72, safety_margin_cells: 0.05,
  },
  pathDemoApi: { multiScenarios: vi.fn(), evaluateMulti: vi.fn() },
}))

const topology: TopologyPayload = {
  id: 'topology-666',
  name: '666',
  meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 4, cols: 4, cellSize: 32, originX: 0, originY: 0 },
  zones: [], cells: [], nodes: [], resources: [],
}
const cells: PathDemoCell[] = [
  { id: 'cell-r1-c1', floor: '1F', row: 1, col: 1, type: 'road', component_id: 'road-component-1' },
  { id: 'cell-r1-c2', floor: '1F', row: 1, col: 2, type: 'road', component_id: 'road-component-1' },
  { id: 'cell-r2-c1', floor: '1F', row: 2, col: 1, type: 'road', component_id: 'road-component-1' },
  { id: 'cell-r2-c2', floor: '1F', row: 2, col: 2, type: 'road', component_id: 'road-component-1' },
]
const vehicles: MultiPathVehicleRequest[] = [
  { vehicle_id: 'CTU-01', vehicle_type: 'ctu', start_cell_id: cells[0].id, goal_cell_id: cells[1].id, priority: 1, display_color: '#22d3ee' },
  { vehicle_id: 'CTU-02', vehicle_type: 'ctu', start_cell_id: cells[2].id, goal_cell_id: cells[3].id, priority: 2, display_color: '#fb923c' },
]
const planningConfig = {
  time_step_seconds: 0.25, speed_cells_per_second: 4, max_time_steps: 512, max_expanded_states: 200000,
  vehicle_width_cells: 0.58, vehicle_length_cells: 0.72, safety_margin_cells: 0.05,
}
const multiScenarios = {
  topology_id: topology.id, topology_name: topology.name, topology_version: topology.meta.version,
  largest_road_component_id: 'road-component-1', road_cells: cells,
  scenarios: [
    { id: 'parallel' as const, name: '场景 A：无冲突并行运行', description: 'A', vehicles },
    { id: 'node-conflict' as const, name: '场景 B：交叉节点竞争', description: 'B', vehicles },
    { id: 'edge-swap' as const, name: '场景 C：相向边交换风险', description: 'C', vehicles },
  ],
}

function makePlan(algorithm: 'astar' | 'dijkstra'): MultiPathPlan {
  return {
    topology_id: topology.id, topology_name: topology.name, topology_version: topology.meta.version, algorithm,
    planning_status: 'success', error_reason: null, time_step_seconds: 0.25, speed_cells_per_second: 4,
    vehicle_width_cells: 0.58, vehicle_length_cells: 0.72, safety_distance_cells: 0.97,
    makespan_steps: 1, makespan_seconds: 0.25,
    vehicles: vehicles.map((vehicle, index) => ({
      ...vehicle, start: cells[index * 2], goal: cells[index * 2 + 1],
      steps: [
        { time_step: 0, time_seconds: 0, action: 'start', cell: cells[index * 2] },
        { time_step: 1, time_seconds: 0.25, action: 'move', cell: cells[index * 2 + 1] },
      ],
      total_distance: 1, estimated_duration_seconds: 0.25, wait_time_seconds: 0, avoidance_count: 0, visited_states: 2,
    })),
    conflicts: {
      detected_node_conflicts: 0, detected_edge_conflicts: 0, detected_geometry_conflicts: 0,
      resolved_node_conflicts: 0, resolved_edge_conflicts: 0, resolved_geometry_conflicts: 0,
      remaining_node_conflicts: 0, remaining_edge_conflicts: 0, remaining_geometry_conflicts: 0,
    },
    avoidance_count: 0, planning_time_ms: algorithm === 'astar' ? 1.25 : 2.5,
  }
}

function makeEvaluation(experimentId = 'experiment-1'): MultiPathEvaluation {
  return {
    experiment_id: experimentId,
    created_at: '2026-10-13T00:00:00Z',
    input_snapshot: {
      topology_id: topology.id, topology_name: topology.name, topology_version: topology.meta.version,
      mode: 'preset', scenario_id: 'parallel', vehicles, planning_config: planningConfig, algorithms: ['astar', 'dijkstra'],
    },
    results: (['astar', 'dijkstra'] as const).map((algorithm) => ({
      algorithm, planning_status: 'success', error_reason: null, planning_time_ms: algorithm === 'astar' ? 1.25 : 2.5,
      metrics: {
        total_path_distance: 2, makespan_seconds: 0.25, total_wait_time_seconds: 0,
        average_wait_time_seconds: 0, detected_conflicts: 0, resolved_conflicts: 0, remaining_conflicts: 0,
        avoidance_count: 0,
        vehicles: vehicles.map((vehicle) => ({
          vehicle_id: vehicle.vehicle_id, total_distance: 1, estimated_duration_seconds: 0.25,
          wait_time_seconds: 0, avoidance_count: 0, visited_states: 2,
        })),
      },
      plan: makePlan(algorithm),
    })),
  }
}

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/lab']}>
        <Routes>
          <Route path="/lab" element={<LabPage />} />
          <Route path="/lab/multi-path-result/:algorithm" element={<div>轨迹结果路由</div>} />
          <Route path="/path-demo" element={<div>路径演示路由</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolver) => { resolve = resolver })
  return { promise, resolve }
}

beforeEach(() => {
  vi.clearAllMocks()
  usePlatformStore.setState({ selectedTopologyId: '', selectedScenarioId: '', evaluation: [] })
  useMultiPathExperimentStore.setState({ customDraft: null, evaluation: null, viewedAlgorithm: null })
  vi.mocked(api.listTopologies).mockResolvedValue([{ id: topology.id, name: topology.name, payload: topology }])
  vi.mocked(api.listScenarios).mockResolvedValue([])
  vi.mocked(pathDemoApi.multiScenarios).mockResolvedValue(multiScenarios)
  vi.mocked(pathDemoApi.evaluateMulti).mockResolvedValue(makeEvaluation())
})

describe('LabPage multi-path evaluation', () => {
  it('keeps an in-memory experiment snapshot when returning to the lab without changing input', async () => {
    useMultiPathExperimentStore.setState({ evaluation: makeEvaluation('return-snapshot') })
    renderPage()
    expect(await screen.findByText(/实验快照 return-snapshot/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('仓库拓扑')).toHaveValue(topology.id))
    expect(useMultiPathExperimentStore.getState().evaluation?.experiment_id).toBe('return-snapshot')
  })

  it('runs the real comparison contract for topology 666 and preset A/B/C input', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('仓库拓扑')).toHaveValue(topology.id))
    await screen.findByRole('option', { name: '场景 B：交叉节点竞争' })
    fireEvent.change(screen.getByLabelText('预设场景'), { target: { value: 'node-conflict' } })
    await waitFor(() => expect(screen.getByRole('button', { name: '运行 A* / Dijkstra 对比' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '运行 A* / Dijkstra 对比' }))

    await waitFor(() => expect(pathDemoApi.evaluateMulti).toHaveBeenCalledWith(expect.objectContaining({
      topologyId: topology.id, mode: 'preset', scenarioId: 'node-conflict', vehicles: [],
    })))
    const table = await screen.findByRole('table', { name: '多车路径算法对比结果' })
    expect(within(table).getByText('总路径（格）')).toBeInTheDocument()
    expect(within(table).getByText('规划耗时（ms）')).toBeInTheDocument()
    expect(within(table).getAllByText('成功')).toHaveLength(2)
    expect(screen.getByTestId('multi-path-chart')).toBeInTheDocument()

    fireEvent.click(within(table).getAllByRole('button', { name: '查看 3D 轨迹' })[0])
    expect(await screen.findByText('轨迹结果路由')).toBeInTheDocument()
  })

  it('reuses the current custom vehicle DTO and planning conditions', async () => {
    useMultiPathExperimentStore.setState({
      customDraft: { topologyId: topology.id, topologyVersion: topology.meta.version, vehicles, planningConfig },
    })
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('仓库拓扑')).toHaveValue(topology.id))
    fireEvent.change(screen.getByLabelText('实验模式'), { target: { value: 'custom' } })
    expect(await screen.findByText('2 辆 CTU')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: '运行 A* / Dijkstra 对比' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '运行 A* / Dijkstra 对比' }))
    await waitFor(() => expect(pathDemoApi.evaluateMulti).toHaveBeenCalledWith({
      topologyId: topology.id, mode: 'custom', scenarioId: undefined, vehicles, planningConfig,
    }))
  })

  it('does not let an obsolete request overwrite the latest experiment result', async () => {
    const first = deferred<MultiPathEvaluation>()
    const second = deferred<MultiPathEvaluation>()
    vi.mocked(pathDemoApi.evaluateMulti)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('仓库拓扑')).toHaveValue(topology.id))
    await screen.findByRole('option', { name: '场景 B：交叉节点竞争' })
    await waitFor(() => expect(screen.getByRole('button', { name: '运行 A* / Dijkstra 对比' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '运行 A* / Dijkstra 对比' }))
    fireEvent.change(screen.getByLabelText('预设场景'), { target: { value: 'node-conflict' } })
    await waitFor(() => expect(screen.getByRole('button', { name: '运行 A* / Dijkstra 对比' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '运行 A* / Dijkstra 对比' }))

    await act(async () => second.resolve(makeEvaluation('latest')))
    await waitFor(() => expect(useMultiPathExperimentStore.getState().evaluation?.experiment_id).toBe('latest'))
    await act(async () => first.resolve(makeEvaluation('obsolete')))
    expect(useMultiPathExperimentStore.getState().evaluation?.experiment_id).toBe('latest')
  })
})
