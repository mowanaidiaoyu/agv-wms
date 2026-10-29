import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'
import type { TopologyPayload, TransportLayoutPlan } from '../types'
import { SimulationPage } from './SimulationPage'

const sceneMock = vi.hoisted(() => ({
  error: null as Error | null,
  lastProps: null as Record<string, unknown> | null,
}))

const topology: TopologyPayload = {
  id: 'topology-test',
  name: '测试拓扑',
  meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 12, cols: 20, cellSize: 32, originX: 0, originY: 0 },
  zones: [],
  cells: [],
  nodes: [],
}

const layoutPlan: TransportLayoutPlan = {
  planId: 'layout-test',
  schemaVersion: '1',
  topologyId: topology.id,
  topologyRevision: '2.0.0:test',
  overallStatus: 'PARTIAL',
  recommendedOptionId: 'scheme-a',
  pickingArm: {
    armId: 'proposed-picking-arm-test',
    status: 'proposed',
    validationStatus: 'PARTIAL',
    installation: { floor: '1F', row: 3, col: 8, level: 1 },
    mountHeight: 0,
    workRadius: 3.45,
    kinematics: {
      upperArmLength: 4,
      forearmLength: 3.9,
      shoulderHeight: 4,
      baseRadius: 0.68,
      upperArmThickness: 0.42,
      forearmThickness: 0.38,
      jointRadius: 0.32,
      gripperScale: 1.45,
      safeTravelHeight: 6.3,
      linkCollisionRadius: 0.24,
    },
    pickup: {
      nodeId: 'proposed-pick-output',
      position: { floor: '1F', row: 2, col: 7, level: 2 },
      beltHeight: 3,
      provenance: 'proposed',
      validationStatus: 'PARTIAL',
      evidence: [],
    },
    dropoff: {
      nodeId: 'existing-conveyor',
      topologyCellId: 'cell-r4-c7',
      position: { floor: '1F', row: 4, col: 7, level: 1 },
      beltHeight: 1.5,
      deckIndex: 1,
      direction: 'right',
      provenance: 'topology',
      validationStatus: 'PASS',
      evidence: [],
    },
    liftBId: 'shuttle-lift-b',
    liftBPosition: { floor: '1F', row: 10, col: 1, level: 1 },
    liftBClearanceCells: 10,
    downstreamCellId: 'cell-r4-c8',
    downstreamStatus: 'BLOCKED',
    previewConveyorPath: [
      {
        cellId: 'cell-r4-c7',
        position: { floor: '1F', row: 4, col: 7, level: 1 },
        direction: 'right',
        beltHeight: 1.5,
        deviceIds: [],
        transitionToNext: 'conveyor',
        transferDeviceId: null,
      },
      {
        cellId: 'cell-r4-c8',
        position: { floor: '1F', row: 4, col: 8, level: 1 },
        direction: 'right',
        beltHeight: 1.5,
        deviceIds: [],
        transitionToNext: 'endpoint',
        transferDeviceId: null,
      },
    ],
    previewEndpointId: 'cell-r4-c8',
    previewPathStatus: 'PASS',
    previewStopReason: 'test endpoint',
    previewTransferDeviceId: null,
    previewTransferStatus: null,
    previewTransferEvidence: [],
    actionSequence: ['idle', 'approach', 'grip', 'lift', 'rotate', 'place', 'release', 'return_home'],
    requiredAuthoritativeSignals: ['pickedGoodsReady', 'outputPositionAvailable', 'handoffCompleted'],
    canDeclareDelivery: false,
    evidence: [],
  },
  references: [],
  facilities: [],
  connections: [],
  routes: [],
  options: [{
    optionId: 'scheme-a',
    label: '方案 A',
    status: 'PARTIAL',
    description: 'Preview test',
    facilityIds: [],
    connectionIds: [],
    assumptions: [],
    blockingReasons: [],
  }],
  issues: [],
  summary: {},
  assumptions: [],
}

vi.mock('../lib/api', () => ({
  api: {
    listTopologies: vi.fn(async () => [{ id: topology.id, name: topology.name, payload: topology }]),
    getTopology: vi.fn(async () => ({ id: topology.id, name: topology.name, payload: topology })),
    getTransportLayoutPlan: vi.fn(async () => layoutPlan),
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
    getShuttleContract: vi.fn(async () => ({
      topologyId: topology.id,
      topologyRevision: 'test',
      configRevision: 'test',
      coordinateContractRevision: 'shuttle-internal-levels-v1',
      verificationStatus: 'unverified',
      planningEnabled: false,
      tracks: [],
      lifts: [{
        lift_id: 'shuttle-lift-a',
        label: 'Lift-A',
        row: 100,
        col: 19,
        supported_levels: [1, 2, 3, 4],
        verification_status: 'validated_from_topology',
        timing_source: 'synthetic_demo',
      }],
      warnings: [],
    })),
    startSimulation: vi.fn(),
    pauseSimulation: vi.fn(),
    resumeSimulation: vi.fn(),
    stopSimulation: vi.fn(),
    resetSimulation: vi.fn(),
    planShuttleTransfer: vi.fn(),
    cancelShuttleTransfer: vi.fn(),
    retryShuttleTransfer: vi.fn(),
    wsUrl: vi.fn(),
  },
}))

vi.mock('../components/topology/Simulation3DScene', () => ({
  default: (props: Record<string, unknown>) => {
    sceneMock.lastProps = props
    if (sceneMock.error) throw sceneMock.error
    return <div data-testid="simulation-3d" />
  },
}))

describe('SimulationPage', () => {
  beforeEach(() => {
    sceneMock.error = null
    sceneMock.lastProps = null
    usePlatformStore.setState({
      selectedTopologyId: topology.id,
      selectedRunId: '',
      liveState: null,
      liveMetrics: null,
      liveEvents: [],
    })
    vi.mocked(api.startSimulation).mockReset()
    vi.mocked(api.resetSimulation).mockReset()
    vi.mocked(api.getSimulationState).mockReset()
    vi.mocked(api.getTopology).mockResolvedValue({ id: topology.id, name: topology.name, payload: topology })
    vi.mocked(api.getTransportLayoutPlan).mockResolvedValue(layoutPlan)
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

  it('loads topology asynchronously and keeps the initial order queue visible', async () => {
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
    expect((await screen.findAllByText('ORD-DEMO-002')).length).toBeGreaterThan(0)
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

  it('keeps one 3D scene mounted while transfer and inventory tabs render distinct content', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation/orders?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    const scene = await screen.findByTestId('simulation-3d')
    expect(sceneMock.lastProps).toMatchObject({
      shuttleLifts: [expect.objectContaining({ lift_id: 'shuttle-lift-a' })],
    })

    fireEvent.click(screen.getByRole('button', { name: '跨层运输' }))
    expect(screen.getByLabelText('跨层运输')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '规划路径' })).toBeInTheDocument()
    expect(screen.getByTestId('simulation-3d')).toBe(scene)

    fireEvent.click(screen.getByRole('button', { name: '库存详情' }))
    expect(screen.getByLabelText('库存详情')).toBeInTheDocument()
    expect(screen.getByText(/此处不生成或猜测 WMS 库存/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '规划路径' })).not.toBeInTheDocument()
    expect(screen.getByTestId('simulation-3d')).toBe(scene)
  })

  it('toggles the isolated handoff layout preview without changing topology or simulation state', async () => {
    const topologySnapshot = structuredClone(topology)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation/orders?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    const scene = await screen.findByTestId('simulation-3d')
    const toggle = await screen.findByRole('button', { name: '接驳布局预览' })
    expect(sceneMock.lastProps).toMatchObject({ layoutPreviewVisible: false })

    fireEvent.click(toggle)
    expect(await screen.findByLabelText('接驳布局预览面板')).toBeInTheDocument()
    expect(sceneMock.lastProps).toMatchObject({
      layoutPreviewVisible: true,
      layoutOptionId: 'scheme-a',
      layoutPlan,
    })
    expect(screen.getByTestId('simulation-3d')).toBe(scene)
    expect(topology).toEqual(topologySnapshot)
    expect(usePlatformStore.getState().selectedRunId).toBe('')

    fireEvent.click(toggle)
    await waitFor(() => expect(screen.queryByLabelText('接驳布局预览面板')).not.toBeInTheDocument())
    expect(sceneMock.lastProps).toMatchObject({ layoutPreviewVisible: false })
    expect(topology).toEqual(topologySnapshot)
  })

  it('toggles and resets the picking-arm preview without creating a run or mutating topology', async () => {
    const topologySnapshot = structuredClone(topology)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation/orders?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    const scene = await screen.findByTestId('simulation-3d')
    const toggle = await screen.findByRole('button', { name: '人工拣选出口机械臂预览' })
    expect(sceneMock.lastProps).toMatchObject({ pickingArmPreviewVisible: false })

    fireEvent.click(toggle)
    expect(await screen.findByLabelText('人工拣选出口机械臂预览面板')).toBeInTheDocument()
    expect(sceneMock.lastProps).toMatchObject({
      pickingArmPreviewVisible: true,
      pickingArmPreviewCycleToken: 'arm-preview-1',
    })
    fireEvent.click(screen.getByRole('button', { name: '重新装载预览周转箱' }))
    expect(sceneMock.lastProps).toMatchObject({ pickingArmPreviewCycleToken: 'arm-preview-2' })

    const reset = screen.getByRole('button', { name: '重置' })
    expect(reset).toBeEnabled()
    fireEvent.click(reset)
    await waitFor(() => expect(sceneMock.lastProps).toMatchObject({ pickingArmResetRequest: 1 }))
    expect(screen.getByTestId('simulation-3d')).toBe(scene)
    expect(topology).toEqual(topologySnapshot)
    expect(usePlatformStore.getState().selectedRunId).toBe('')
    expect(api.resetSimulation).not.toHaveBeenCalled()
  })

  it('shows a retryable error without removing orders or controls when topology loading fails', async () => {
    vi.mocked(api.getTopology)
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce({ id: topology.id, name: topology.name, payload: topology })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation/orders?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByText('拓扑场景加载失败，请检查拓扑数据或后端连接后重试。')).toBeInTheDocument()
    expect(screen.getByText('订单队列')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '启动' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重试加载拓扑' }))
    expect(await screen.findByTestId('simulation-3d')).toBeInTheDocument()
  })

  it('keeps legacy state compatible when cross-level inventory fields are absent', async () => {
    usePlatformStore.setState({
      selectedTopologyId: topology.id,
      selectedRunId: 'legacy-run',
      liveState: {
        id: 'legacy-run',
        status: 'paused',
        tick: 3,
        topologyId: topology.id,
        robots: [],
        tasks: [],
        inventory: [{ nodeId: 'storage-1', cellId: 'cell-r1-c1', layer: 1, skuId: 'SKU-001', qty: 5 }],
        alerts: [],
        queues: { conveyor: [], station: [], buffer: [] },
        createdAt: '2026-10-21T00:00:00Z',
        updatedAt: '2026-10-21T00:00:00Z',
      },
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation/orders?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByTestId('simulation-3d')).toBeInTheDocument()
    expect(sceneMock.lastProps).toMatchObject({ transferInventory: [], paused: true })
    expect(sceneMock.lastProps).toMatchObject({ goods: [] })
    fireEvent.click(screen.getByRole('button', { name: '彩色库存' }))
    expect(sceneMock.lastProps).toMatchObject({ goods: [expect.objectContaining({ skuId: 'SKU-001' })] })
    expect(screen.getByText('订单队列')).toBeInTheDocument()
  })

  it('shows a backend loop failure as an abnormal stop instead of running', async () => {
    usePlatformStore.setState({
      selectedTopologyId: topology.id,
      selectedRunId: 'failed-run',
      liveState: {
        id: 'failed-run',
        status: 'stopped',
        stopReason: 'runtime_error',
        runtimeError: { type: 'RuntimeError', message: 'pickup adapter exploded', tick: 2 },
        tick: 2,
        topologyId: topology.id,
        robots: [],
        tasks: [],
        inventory: [],
        alerts: [],
        queues: { conveyor: [], station: [], buffer: [] },
        createdAt: '2026-10-23T00:00:00Z',
        updatedAt: '2026-10-23T00:00:02Z',
      },
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation/orders?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByText('运行异常')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Tick 2')
    expect(screen.getByRole('alert')).toHaveTextContent('pickup adapter exploded')
    expect(screen.getByRole('button', { name: '继续' })).toBeDisabled()
  })

  it('contains a 3D initialization error inside the viewport and allows retry', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    sceneMock.error = new Error('topology.resources is not iterable')
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation/orders?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByText('3D 场景初始化失败，订单与运行控制仍可继续使用。')).toBeInTheDocument()
    expect(screen.getByText('topology.resources is not iterable')).toBeInTheDocument()
    expect(screen.getByText('订单队列')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '启动' })).toBeInTheDocument()

    sceneMock.error = null
    fireEvent.click(screen.getByRole('button', { name: '重试 3D 场景' }))
    expect(await screen.findByTestId('simulation-3d')).toBeInTheDocument()
    consoleError.mockRestore()
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
    expect(api.startSimulation).toHaveBeenCalledWith(
      topology.id,
      expect.arrayContaining([expect.objectContaining({ id: 'ORD-DEMO-001' })]),
      [],
    )
    expect(within(screen.getByLabelText('仿真运行状态')).getByText('运行中')).toBeInTheDocument()
  })
})
