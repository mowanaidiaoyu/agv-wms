import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import type { TopologyPayload } from '../../types'
import type { MultiPathEvaluation, MultiPathPlan, PathDemoCell } from './types'
import { pathDemoApi } from './pathDemoApi'
import { MultiPathEvaluationReports } from './MultiPathEvaluationReports'

vi.mock('../../components/charts/MultiPathComparisonChart', () => ({ MultiPathComparisonChart: () => <div data-testid="history-chart" /> }))
vi.mock('../../lib/api', () => ({ api: { listTopologies: vi.fn() } }))
vi.mock('./pathDemoApi', () => ({
  pathDemoApi: { evaluations: vi.fn(), evaluation: vi.fn(), evaluationExport: vi.fn() },
}))

const topology: TopologyPayload = {
  id: 'topology-666', name: '666',
  meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 2, cols: 2, cellSize: 32, originX: 0, originY: 0 },
  zones: [], cells: [], nodes: [], resources: [],
}
const cells: PathDemoCell[] = [
  { id: 'cell-r0-c0', floor: '1F', row: 0, col: 0, type: 'road', component_id: 'road-1' },
  { id: 'cell-r0-c1', floor: '1F', row: 0, col: 1, type: 'road', component_id: 'road-1' },
]

function plan(): MultiPathPlan {
  return {
    topology_id: topology.id, topology_name: topology.name, topology_version: topology.meta.version,
    algorithm: 'astar', planning_status: 'success', error_reason: null,
    time_step_seconds: 0.25, speed_cells_per_second: 4, vehicle_width_cells: 0.58,
    vehicle_length_cells: 0.72, safety_distance_cells: 0.97, makespan_steps: 1, makespan_seconds: 0.25,
    vehicles: [{
      vehicle_id: 'CTU-01', vehicle_type: 'ctu', priority: 1, display_color: '#22d3ee',
      start: cells[0], goal: cells[1], steps: [
        { time_step: 0, time_seconds: 0, action: 'start', cell: cells[0] },
        { time_step: 1, time_seconds: 0.25, action: 'move', cell: cells[1] },
      ],
      total_distance: 1, estimated_duration_seconds: 0.25, wait_time_seconds: 0, avoidance_count: 0, visited_states: 2,
    }],
    conflicts: {
      detected_node_conflicts: 0, detected_edge_conflicts: 0, detected_geometry_conflicts: 0,
      resolved_node_conflicts: 0, resolved_edge_conflicts: 0, resolved_geometry_conflicts: 0,
      remaining_node_conflicts: 0, remaining_edge_conflicts: 0, remaining_geometry_conflicts: 0,
    },
    avoidance_count: 0, planning_time_ms: 1.25,
  }
}

function evaluation(): MultiPathEvaluation {
  const astarPlan = plan()
  return {
    experiment_id: 'history-1', schema_version: 'multi-path-evaluation/v1',
    experiment_type: 'offline_multi_path_planning', status: 'partial_failure', created_at: '2026-10-14T00:00:00Z',
    input_snapshot: {
      topology_id: topology.id, topology_name: topology.name, topology_version: topology.meta.version,
      mode: 'preset', scenario_id: 'parallel', algorithms: ['astar', 'dijkstra'],
      vehicles: [{ vehicle_id: 'CTU-01', vehicle_type: 'ctu', start_cell_id: cells[0].id, goal_cell_id: cells[1].id, priority: 1 }],
      planning_config: {
        time_step_seconds: 0.25, speed_cells_per_second: 4, max_time_steps: 512, max_expanded_states: 200000,
        vehicle_width_cells: 0.58, vehicle_length_cells: 0.72, safety_margin_cells: 0.05,
      },
    },
    results: [
      {
        algorithm: 'astar', planning_status: 'success', error_reason: null, planning_time_ms: 1.25,
        metrics: {
          total_path_distance: 1, makespan_seconds: 0.25, total_wait_time_seconds: 0,
          average_wait_time_seconds: 0, detected_conflicts: 0, resolved_conflicts: 0, remaining_conflicts: 0,
          avoidance_count: 0,
          vehicles: [{ vehicle_id: 'CTU-01', total_distance: 1, estimated_duration_seconds: 0.25, wait_time_seconds: 0, avoidance_count: 0, visited_states: 2 }],
        },
        plan: astarPlan,
      },
      { algorithm: 'dijkstra', planning_status: 'failed', error_reason: 'search boundary reached', planning_time_ms: 9, metrics: null, plan: null },
    ],
  }
}

function renderReports(selectedExperimentId = 'history-1') {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><MultiPathEvaluationReports selectedExperimentId={selectedExperimentId} /></MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  const detail = evaluation()
  vi.mocked(pathDemoApi.evaluations).mockResolvedValue({
    items: [{
      experiment_id: detail.experiment_id, schema_version: detail.schema_version, experiment_type: detail.experiment_type,
      status: detail.status, topology_id: topology.id, topology_name: topology.name, topology_version: topology.meta.version,
      vehicle_count: 1, algorithms: ['astar', 'dijkstra'], created_at: detail.created_at,
      results: detail.results.map(({ plan: _plan, ...result }) => result),
    }],
    total: 1, limit: 10, offset: 0,
  })
  vi.mocked(pathDemoApi.evaluation).mockResolvedValue(detail)
  vi.mocked(pathDemoApi.evaluationExport).mockResolvedValue({
    schema_version: detail.schema_version, experiment_type: detail.experiment_type,
    disclaimer: 'offline experiment', unit_definitions: { makespan_seconds: 'seconds' }, evaluation: detail,
  })
  vi.mocked(api.listTopologies).mockResolvedValue([{ id: topology.id, name: topology.name, payload: topology }])
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:report'), configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
})

describe('MultiPathEvaluationReports', () => {
  it('reads list and detail from persisted APIs and exposes exact historical trajectory links', async () => {
    renderReports()
    expect(await screen.findByRole('table', { name: '多车路径算法实验记录' })).toBeInTheDocument()
    expect(pathDemoApi.evaluations).toHaveBeenCalledWith({ limit: 10, offset: 0 })
    expect(pathDemoApi.evaluation).toHaveBeenCalledWith('history-1')
    const metrics = await screen.findByRole('table', { name: '持久化多车算法指标' })
    expect(within(metrics).getByText('search boundary reached', { exact: false })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看 3D 轨迹' })).toHaveAttribute('href', '/lab/multi-path-result/history-1/astar')
    expect(screen.getByTestId('history-chart')).toBeInTheDocument()
  })

  it('exports the server-side saved snapshot', async () => {
    renderReports()
    const button = await screen.findByRole('button', { name: '导出实验 JSON' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    await waitFor(() => expect(pathDemoApi.evaluationExport).toHaveBeenCalledWith('history-1'))
    expect(URL.createObjectURL).toHaveBeenCalled()
  })

  it('keeps metrics readable but refuses replay on topology version mismatch', async () => {
    vi.mocked(api.listTopologies).mockResolvedValueOnce([{
      id: topology.id, name: topology.name,
      payload: { ...topology, meta: { ...topology.meta, version: '3.0.0' } },
    }])
    renderReports()
    expect(await screen.findByRole('table', { name: '持久化多车算法指标' })).toBeInTheDocument()
    expect(await screen.findByText(/已拒绝 3D 回放/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '查看 3D 轨迹' })).not.toBeInTheDocument()
  })

  it('shows persistence read errors instead of inventing a result', async () => {
    vi.mocked(pathDemoApi.evaluation).mockRejectedValueOnce(new Error('stored snapshot corrupted'))
    renderReports()
    expect(await screen.findByRole('alert')).toHaveTextContent('无法读取实验快照：stored snapshot corrupted')
    expect(screen.queryByRole('table', { name: '持久化多车算法指标' })).not.toBeInTheDocument()
  })
})
