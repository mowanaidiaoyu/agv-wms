import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TopologyPayload } from '../types'
import { api } from '../lib/api'
import { pathDemoApi } from '../features/path-demo/pathDemoApi'
import { PathDemoPage } from './PathDemoPage'

vi.mock('../features/path-demo/PathDemo3DScene', () => ({
  PathDemo3DScene: ({ cameraResetToken }: { cameraResetToken: number }) => (
    <div data-testid="path-demo-scene" data-camera-reset-token={cameraResetToken} />
  ),
}))

vi.mock('../lib/api', () => ({ api: { listTopologies: vi.fn() } }))
vi.mock('../features/path-demo/pathDemoApi', () => ({
  pathDemoApi: { options: vi.fn(), plan: vi.fn() },
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

  it('resets only the camera view from the operation panel', async () => {
    renderPage()
    const scene = await screen.findByTestId('path-demo-scene')
    expect(scene).toHaveAttribute('data-camera-reset-token', '0')
    fireEvent.click(screen.getByRole('button', { name: '重置视角' }))
    expect(scene).toHaveAttribute('data-camera-reset-token', '1')
  })
})
