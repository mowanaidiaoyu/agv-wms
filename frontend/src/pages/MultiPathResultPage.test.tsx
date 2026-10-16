import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMultiPathExperimentStore } from '../features/path-demo/multiPathExperimentStore'
import { pathDemoApi } from '../features/path-demo/pathDemoApi'
import type { MultiPathEvaluation, MultiPathPlan, PathDemoCell, PathDemoVehicleView } from '../features/path-demo/types'
import { api } from '../lib/api'
import type { TopologyPayload } from '../types'
import { MultiPathResultPage } from './MultiPathResultPage'

vi.mock('../lib/api', () => ({ api: { listTopologies: vi.fn() } }))
vi.mock('../features/path-demo/pathDemoApi', () => ({ pathDemoApi: { options: vi.fn(), evaluation: vi.fn() } }))
vi.mock('../features/path-demo/PathDemo3DScene', () => ({
  PathDemo3DScene: ({ vehicles, selectedVehicleId, cameraActionRequest }: {
    vehicles: PathDemoVehicleView[]
    selectedVehicleId?: string
    cameraActionRequest?: { type: string } | null
  }) => (
    <div
      data-testid="result-scene"
      data-vehicles={vehicles.map((vehicle) => vehicle.vehicleId).join(',')}
      data-selected={selectedVehicleId}
      data-camera-action={cameraActionRequest?.type ?? ''}
      data-samples={vehicles.map((vehicle) => `${vehicle.vehicleId}:${vehicle.sample?.row.toFixed(2)},${vehicle.sample?.col.toFixed(2)}`).join('|')}
    />
  ),
}))

const topology: TopologyPayload = {
  id: 'topology-666', name: '666',
  meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 4, cols: 4, cellSize: 32, originX: 0, originY: 0 },
  zones: [], cells: [], nodes: [], resources: [],
}
const topologyFingerprint = 'test-topology-fingerprint'
const cells: PathDemoCell[] = [
  { id: 'cell-r1-c1', floor: '1F', row: 1, col: 1, type: 'road', component_id: 'road-component-1' },
  { id: 'cell-r1-c2', floor: '1F', row: 1, col: 2, type: 'road', component_id: 'road-component-1' },
  { id: 'cell-r2-c1', floor: '1F', row: 2, col: 1, type: 'road', component_id: 'road-component-1' },
  { id: 'cell-r2-c2', floor: '1F', row: 2, col: 2, type: 'road', component_id: 'road-component-1' },
]

function plan(): MultiPathPlan {
  return {
    topology_id: topology.id, topology_name: topology.name, topology_version: topology.meta.version,
    algorithm: 'astar', planning_status: 'success', error_reason: null,
    time_step_seconds: 0.25, speed_cells_per_second: 4, vehicle_width_cells: 0.58,
    vehicle_length_cells: 0.72, safety_distance_cells: 0.97, makespan_steps: 1, makespan_seconds: 0.25,
    vehicles: ['CTU-01', 'CTU-02'].map((vehicleId, index) => ({
      vehicle_id: vehicleId, vehicle_type: 'ctu', priority: index + 1,
      display_color: index ? '#fb923c' : '#22d3ee', start: cells[index * 2], goal: cells[index * 2 + 1],
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
    avoidance_count: 0, planning_time_ms: 1,
  }
}

function evaluation(): MultiPathEvaluation {
  const astarPlan = plan()
  return {
    experiment_id: 'snapshot-1', schema_version: 'multi-path-evaluation/v1',
    experiment_type: 'offline_multi_path_planning', status: 'success', created_at: '2026-10-13T00:00:00Z',
    input_snapshot: {
      topology_id: topology.id, topology_name: topology.name, topology_version: topology.meta.version,
      topology_content_sha256: topologyFingerprint,
      mode: 'preset', scenario_id: 'parallel',
      vehicles: astarPlan.vehicles.map((vehicle) => ({
        vehicle_id: vehicle.vehicle_id, vehicle_type: 'ctu', start_cell_id: vehicle.start.id,
        goal_cell_id: vehicle.goal.id, priority: vehicle.priority, display_color: vehicle.display_color,
      })),
      planning_config: {
        time_step_seconds: 0.25, speed_cells_per_second: 4, max_time_steps: 512, max_expanded_states: 200000,
        vehicle_width_cells: 0.58, vehicle_length_cells: 0.72, safety_margin_cells: 0.05,
      },
      algorithms: ['astar', 'dijkstra'],
    },
    results: [{
      algorithm: 'astar', planning_status: 'success', error_reason: null, planning_time_ms: 1,
      metrics: {
        total_path_distance: 2, makespan_seconds: 0.25, total_wait_time_seconds: 0,
        average_wait_time_seconds: 0, detected_conflicts: 0, resolved_conflicts: 0, remaining_conflicts: 0,
        avoidance_count: 0,
        vehicles: astarPlan.vehicles.map((vehicle) => ({
          vehicle_id: vehicle.vehicle_id, total_distance: 1, estimated_duration_seconds: 0.25,
          wait_time_seconds: 0, avoidance_count: 0, visited_states: 2,
        })),
      },
      plan: astarPlan,
    }],
  }
}

function sixVehicleEvaluation(): MultiPathEvaluation {
  const snapshot = evaluation()
  const result = snapshot.results[0]
  const baseVehicle = result.plan!.vehicles[0]
  const vehicles = Array.from({ length: 6 }, (_, index) => ({
    ...baseVehicle,
    vehicle_id: `CTU-0${index + 1}`,
    priority: index + 1,
  }))
  result.plan = { ...result.plan!, vehicles }
  result.metrics = {
    ...result.metrics!,
    total_path_distance: 6,
    vehicles: vehicles.map((vehicle) => ({
      vehicle_id: vehicle.vehicle_id,
      total_distance: vehicle.total_distance,
      estimated_duration_seconds: vehicle.estimated_duration_seconds,
      wait_time_seconds: vehicle.wait_time_seconds,
      avoidance_count: vehicle.avoidance_count,
      visited_states: vehicle.visited_states,
    })),
  }
  snapshot.input_snapshot.vehicles = vehicles.map((vehicle) => ({
    vehicle_id: vehicle.vehicle_id,
    vehicle_type: 'ctu',
    start_cell_id: vehicle.start.id,
    goal_cell_id: vehicle.goal.id,
    priority: vehicle.priority,
  }))
  return snapshot
}

function renderPage(path = '/lab/multi-path-result/astar') {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/lab/multi-path-result/:algorithm" element={<MultiPathResultPage />} />
          <Route path="/lab/multi-path-result/:experimentId/:algorithm" element={<MultiPathResultPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  useMultiPathExperimentStore.setState({ customDraft: null, evaluation: null, viewedAlgorithm: null })
  vi.mocked(api.listTopologies).mockResolvedValue([{ id: topology.id, name: topology.name, payload: topology }])
  vi.mocked(pathDemoApi.options).mockResolvedValue({
    topology_id: topology.id, topology_name: topology.name, topology_version: topology.meta.version,
    topology_content_sha256: topologyFingerprint,
    components: [], largest_component_id: 'road-component-1', traversable_cells: cells,
    default_start: cells[0], default_goal: cells[1],
  })
  vi.mocked(pathDemoApi.evaluation).mockResolvedValue(evaluation())
})

describe('MultiPathResultPage', () => {
  it('shows an explicit expired message instead of replanning after the in-memory snapshot is lost', () => {
    renderPage()
    expect(screen.getByText('实验轨迹快照已失效或不可用')).toBeInTheDocument()
    expect(screen.getByText(/不会静默重新规划/)).toBeInTheDocument()
    expect(api.listTopologies).not.toHaveBeenCalled()
  })

  it('plays the exact evaluation plan and switches the followed vehicle', async () => {
    useMultiPathExperimentStore.setState({ evaluation: evaluation(), viewedAlgorithm: 'astar' })
    renderPage()
    expect(await screen.findByText('时空 A* · 多车 3D 轨迹')).toBeInTheDocument()
    const scene = await screen.findByTestId('result-scene')
    await waitFor(() => expect(scene).toHaveAttribute('data-vehicles', 'CTU-01,CTU-02'))
    expect(scene).toHaveAttribute('data-selected', 'CTU-01')
    fireEvent.change(screen.getByLabelText('选择跟随车辆'), { target: { value: 'CTU-02' } })
    await waitFor(() => expect(scene).toHaveAttribute('data-selected', 'CTU-02'))
    expect(screen.getByRole('button', { name: '开始运行' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '暂停' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '仓库全景' })).toBeEnabled()
  })

  it('reloads a stable historical route from the backend without replanning', async () => {
    renderPage('/lab/multi-path-result/snapshot-1/astar')
    expect(await screen.findByText('时空 A* · 多车 3D 轨迹')).toBeInTheDocument()
    expect(pathDemoApi.evaluation).toHaveBeenCalledWith('snapshot-1')
  })

  it('refuses replay when the current topology version differs from the snapshot', async () => {
    vi.mocked(api.listTopologies).mockResolvedValueOnce([{
      id: topology.id,
      name: topology.name,
      payload: { ...topology, meta: { ...topology.meta, version: 'new-version' } },
    }])
    renderPage('/lab/multi-path-result/snapshot-1/astar')
    expect(await screen.findByText('拓扑版本或内容与实验快照不一致')).toBeInTheDocument()
    expect(screen.queryByTestId('result-scene')).not.toBeInTheDocument()
  })

  it('seeks every vehicle on one historical timeline without another backend request', async () => {
    renderPage('/lab/multi-path-result/snapshot-1/astar')
    const slider = await screen.findByLabelText('历史轨迹时间轴')
    const scene = await screen.findByTestId('result-scene')
    expect(slider).toHaveAttribute('max', '0.25')
    fireEvent.pointerDown(slider)
    fireEvent.change(slider, { target: { value: '0.125' } })
    await waitFor(() => expect(scene).toHaveAttribute(
      'data-samples',
      'CTU-01:1.00,1.50|CTU-02:2.00,1.50',
    ))
    expect(screen.getByText('0.13 s / 0.25 s')).toBeInTheDocument()
    expect(pathDemoApi.evaluation).toHaveBeenCalledTimes(1)
  })

  it('steps to exact saved ticks, clamps the end and exposes synchronized completion state', async () => {
    renderPage('/lab/multi-path-result/snapshot-1/astar')
    await screen.findByLabelText('历史轨迹时间轴')
    fireEvent.click(screen.getByRole('button', { name: '下一个时间片' }))
    expect(await screen.findByText('0.25 s / 0.25 s')).toBeInTheDocument()
    expect(screen.getAllByText(/COMPLETED/)).toHaveLength(3)
    expect(screen.getByRole('button', { name: '下一个时间片' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '上一个时间片' }))
    expect(await screen.findByText('0.00 s / 0.25 s')).toBeInTheDocument()
    expect(screen.getAllByText(/MOVING/)).toHaveLength(3)
  })

  it('reports a missing persisted experiment and never attempts to fabricate a replay', async () => {
    vi.mocked(pathDemoApi.evaluation).mockRejectedValueOnce(new Error('Multi-path evaluation not found'))
    renderPage('/lab/multi-path-result/missing/astar')
    expect(await screen.findByText(/Multi-path evaluation not found/)).toBeInTheDocument()
    expect(api.listTopologies).not.toHaveBeenCalled()
    expect(screen.queryByTestId('result-scene')).not.toBeInTheDocument()
  })

  it('keeps CTU-06 selected while all six saved tracks seek together', async () => {
    useMultiPathExperimentStore.setState({ evaluation: sixVehicleEvaluation(), viewedAlgorithm: 'astar' })
    renderPage()
    const scene = await screen.findByTestId('result-scene')
    fireEvent.change(screen.getByDisplayValue('CTU-01'), { target: { value: 'CTU-06' } })
    expect(scene).toHaveAttribute('data-selected', 'CTU-06')
    const followButton = document.querySelector<HTMLButtonElement>('.path-demo-camera-buttons button:nth-child(2)')
    expect(followButton).not.toBeNull()
    fireEvent.click(followButton!)
    fireEvent.change(screen.getByLabelText('历史轨迹时间轴'), { target: { value: '0.125' } })
    await waitFor(() => {
      expect(scene.getAttribute('data-samples')).toContain('CTU-06:1.00,1.50')
      expect(scene).toHaveAttribute('data-selected', 'CTU-06')
      expect(scene).toHaveAttribute('data-camera-action', 'switch-follow-target')
    })
  })
})
