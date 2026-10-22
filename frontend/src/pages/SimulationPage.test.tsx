import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'
import type { TopologyPayload } from '../types'
import { SimulationPage } from './SimulationPage'

const topology: TopologyPayload = {
  id: 'topology-test',
  name: '测试拓扑',
  meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 12, cols: 20, cellSize: 32, originX: 0, originY: 0 },
  zones: [],
  cells: [],
  nodes: [],
}

vi.mock('../lib/api', () => ({
  api: {
    listTopologies: vi.fn(async () => [{ id: topology.id, name: topology.name, payload: topology }]),
    getTopology: vi.fn(async () => ({ id: topology.id, name: topology.name, payload: topology })),
    getSimulationState: vi.fn(),
    getSimulationMetrics: vi.fn(),
    getGoodsCatalog: vi.fn(async () => ({
      version: 1,
      skus: [
        { skuId: 'sku-1', name: '六角螺栓 M8', spec: 'M8' },
        { skuId: 'sku-2', name: '法兰螺母 M10', spec: 'M10' },
        { skuId: 'sku-3', name: '弹簧垫圈 M8', spec: 'M8' },
        { skuId: 'sku-4', name: '内六角螺钉 M6', spec: 'M6' },
        { skuId: 'sku-5', name: '平垫圈 M12', spec: 'M12' },
        { skuId: 'sku-6', name: '高强度螺栓 M16', spec: 'M16' },
        { skuId: 'sku-7', name: '锁紧螺母 M16', spec: 'M16' },
      ],
    })),
    startSimulation: vi.fn(),
    pauseSimulation: vi.fn(),
    resumeSimulation: vi.fn(),
    stopSimulation: vi.fn(),
    wsUrl: vi.fn(),
  },
}))

vi.mock('../components/topology/Simulation3DScene', () => ({
  default: () => <div data-testid="simulation-3d" />,
}))

describe('SimulationPage', () => {
  beforeEach(() => {
    usePlatformStore.setState({
      selectedTopologyId: topology.id,
      selectedRunId: '',
      liveState: null,
      liveMetrics: null,
      liveEvents: [],
    })
    vi.mocked(api.startSimulation).mockReset()
    vi.mocked(api.getSimulationState).mockReset()
    vi.mocked(api.getSimulationMetrics).mockResolvedValue({
      throughput: 0,
      completedTasks: 0,
      pendingTasks: 0,
      activeRobots: 0,
      blockedRobots: 0,
      conveyorUtilization: 0,
      deadlocksDetected: 0,
      congestionEvents: 0,
      urgentOrdersHandled: 0,
      avgEtaTicks: 0,
      avgCompletionTicks: 0,
      stationQueueDepth: 0,
      conveyorQueueDepth: 0,
      bufferOccupancy: 0,
    })
    vi.stubGlobal('WebSocket', class {
      onopen: (() => void) | null = null
      onclose: (() => void) | null = null
      onerror: (() => void) | null = null
      onmessage: (() => void) | null = null
      constructor() {
        queueMicrotask(() => this.onopen?.())
      }
      close() {
        this.onclose?.()
      }
    })
  })

  it('keeps diagnostic modules out of the main scene until details are opened', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByTestId('simulation-3d')).toBeInTheDocument()
    expect((await screen.findAllByText('ORD-DEMO-001')).length).toBeGreaterThan(0)
    expect(screen.getByText('订单队列')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '实时指标' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '设备状态' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '运行详情' }))
    expect(screen.getByRole('heading', { name: '实时指标' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '设备 0' }))
    expect(screen.getByRole('heading', { name: '设备状态' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '关闭运行详情' }))
    expect(screen.queryByRole('heading', { name: '设备状态' })).not.toBeInTheDocument()
  })

  it('shows starting feedback immediately and running state after startup completes', async () => {
    let resolveStart!: (value: { runId: string; status: string }) => void
    vi.mocked(api.startSimulation).mockReturnValue(new Promise((resolve) => { resolveStart = resolve }))
    vi.mocked(api.getSimulationState).mockResolvedValue({
      id: 'run-test',
      status: 'running',
      tick: 0,
      topologyId: topology.id,
      robots: [],
      tasks: [],
      inventory: [],
      alerts: [],
      queues: { conveyor: [], station: [], buffer: [] },
      createdAt: '2026-10-21T00:00:00Z',
      updatedAt: '2026-10-21T00:00:00Z',
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByRole('button', { name: '启动' }))
    expect(screen.getByRole('button', { name: '启动中…' })).toBeDisabled()
    expect(within(screen.getByLabelText('仿真运行状态')).getByText('启动中…')).toBeInTheDocument()

    await act(async () => resolveStart({ runId: 'run-test', status: 'running' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '运行中' })).toBeDisabled())
    expect(within(screen.getByLabelText('仿真运行状态')).getByText('运行中')).toBeInTheDocument()
  })
})
