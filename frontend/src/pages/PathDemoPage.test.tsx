import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TopologyPayload } from '../types'
import { api } from '../lib/api'
import { pathDemoApi } from '../features/path-demo/pathDemoApi'
import type { CameraActionRequest, CameraViewMode } from '../features/path-demo/cameraControls'
import type { PlaybackStatus, TrajectorySample } from '../features/path-demo/types'
import { PathDemoPage } from './PathDemoPage'

vi.mock('../features/path-demo/PathDemo3DScene', () => ({
  PathDemo3DScene: ({
    cameraViewMode,
    cameraActionRequest,
    sample,
    playbackStatus,
  }: {
    cameraViewMode: CameraViewMode
    cameraActionRequest: CameraActionRequest | null
    sample: TrajectorySample | null
    playbackStatus: PlaybackStatus
  }) => (
    <div
      data-testid="path-demo-scene"
      data-camera-view-mode={cameraViewMode}
      data-camera-action={cameraActionRequest?.type ?? ''}
      data-camera-action-sequence={cameraActionRequest?.sequence ?? ''}
      data-sample-world={sample?.world.join(',') ?? ''}
      data-playback-status={playbackStatus}
    />
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
})
