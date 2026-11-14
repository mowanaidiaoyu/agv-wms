import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'
import type { SimulationState, SimulationTask, TopologyPayload, TransportLayoutPlan } from '../types'
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

function taskFixture(id: string, orderId: string, phase: string): SimulationTask {
  return {
    id,
    orderId,
    robotType: 'shuttle',
    mode: 'direct',
    status: 'assigned',
    phase,
    phaseLabel: phase,
    sourceCellId: 'cell-r1-c1',
    sourceLayer: 1,
    targetCellId: 'cell-r84-c50',
    targetLayer: 2,
    deliveryCellId: 'cell-r92-c48',
    lines: [{ nodeId: 'storage-test', cellId: 'cell-r1-c1', layer: 1, skuId: 'SKU-A', skuName: '测试货物' }],
    deliveredGoods: [{ nodeId: 'storage-test', cellId: 'cell-r1-c1', layer: 1, skuId: 'SKU-A', skuName: '测试货物' }],
    currentLineIndex: 0,
    picked: 1,
    priority: 1,
    etaTicks: 1,
    urgent: false,
    assignedRobotId: null,
    remainingTicks: 0,
    history: [],
    createdTick: 0,
    startedTick: 1,
    completedTick: null,
  }
}

function simulationStateFixture(id: string, topologyId = topology.id): SimulationState {
  return {
    id,
    status: 'running',
    tick: 12,
    topologyId,
    robots: [],
    tasks: [],
    orders: [],
    inventory: [],
    alerts: [],
    queues: { conveyor: [], station: [], buffer: [] },
    createdAt: '2026-10-28T00:00:00Z',
    updatedAt: '2026-10-28T00:00:12Z',
  }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location-probe">{location.pathname}{location.search}</output>
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
    insertSimulationOrder: vi.fn(),
    cancelSimulationOrder: vi.fn(),
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

function renderSimulationPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/simulation?topologyId=topology-test']}>
        <SimulationPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

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
    vi.mocked(api.insertSimulationOrder).mockReset()
    vi.mocked(api.cancelSimulationOrder).mockReset()
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
    expect(sceneMock.lastProps).toMatchObject({ followSelectedRobot: false })
    expect((await screen.findAllByText('ORD-DEMO-001')).length).toBeGreaterThan(0)
    expect(screen.getByText('订单队列')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '新建订单' })).toHaveLength(1)
    expect(screen.getByLabelText('订单拆解与 CTU 分配')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '7车联动订单' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '实时指标' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '设备状态' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '运行详情' }))
    expect(screen.getByRole('heading', { name: '实时指标' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '设备 0' }))
    expect(screen.getByRole('heading', { name: '设备状态' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '关闭运行详情' }))
    expect(screen.queryByRole('heading', { name: '设备状态' })).not.toBeInTheDocument()
  })

  it('replaces the inventory primary entry with analytics and passes only topologyId without a verified run', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation?topologyId=topology-test']}>
          <SimulationPage />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByTestId('simulation-3d')
    expect(screen.queryByRole('button', { name: '库存详情' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '性能分析' }))
    await waitFor(() => expect(screen.getByTestId('location-probe')).toHaveTextContent('/simulation/analytics?topologyId=topology-test'))
    expect(screen.getByTestId('location-probe')).not.toHaveTextContent(/runId=(?:undefined|null)?(?:&|$)/)
    expect(api.startSimulation).not.toHaveBeenCalled()
    expect(api.resetSimulation).not.toHaveBeenCalled()
    expect(api.insertSimulationOrder).not.toHaveBeenCalled()
    expect(api.cancelSimulationOrder).not.toHaveBeenCalled()
    expect(api.pauseSimulation).not.toHaveBeenCalled()
    expect(api.resumeSimulation).not.toHaveBeenCalled()
    expect(api.stopSimulation).not.toHaveBeenCalled()
  })

  it('passes a runId only after the authoritative state confirms the selected run and topology', async () => {
    const state = simulationStateFixture('run-verified')
    usePlatformStore.setState({ selectedRunId: state.id, liveState: state })
    vi.mocked(api.getSimulationState).mockResolvedValue(state)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation?topologyId=topology-test']}>
          <SimulationPage />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByRole('button', { name: '性能分析' }))
    await waitFor(() => expect(screen.getByTestId('location-probe')).toHaveTextContent('/simulation/analytics?topologyId=topology-test&runId=run-verified'))
    expect(api.startSimulation).not.toHaveBeenCalled()
    expect(api.resetSimulation).not.toHaveBeenCalled()
  })

  it('does not leak a stale runId from another topology into analytics navigation', async () => {
    const staleState = simulationStateFixture('run-stale', 'topology-other')
    usePlatformStore.setState({ selectedRunId: staleState.id, liveState: staleState })
    vi.mocked(api.getSimulationState).mockResolvedValue(staleState)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation?topologyId=topology-test']}>
          <SimulationPage />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByRole('button', { name: '性能分析' }))
    await waitFor(() => expect(screen.getByTestId('location-probe')).toHaveTextContent('/simulation/analytics?topologyId=topology-test'))
    expect(screen.getByTestId('location-probe')).not.toHaveTextContent('run-stale')
    expect(api.startSimulation).not.toHaveBeenCalled()
    expect(api.resetSimulation).not.toHaveBeenCalled()
  })

  it('inserts a new order into the active run and refreshes the authoritative queue', async () => {
    usePlatformStore.setState({
      selectedTopologyId: topology.id,
      selectedRunId: 'run-active',
      liveState: {
        id: 'run-active',
        status: 'running',
        tick: 12,
        topologyId: topology.id,
        robots: [],
        tasks: [],
        orders: [],
        inventory: [],
        alerts: [],
        queues: { conveyor: [], station: [], buffer: [] },
        createdAt: '2026-10-28T00:00:00Z',
        updatedAt: '2026-10-28T00:00:12Z',
      },
    })
    vi.mocked(api.insertSimulationOrder).mockImplementation(async (_runId, order) => ({
      id: 'run-active',
      status: 'running',
      tick: 12,
      topologyId: topology.id,
      robots: [],
      tasks: [],
      orders: [{
        ...order,
        status: 'waiting_dispatch',
        statusLabel: '等待调度',
        taskIds: [],
        totalTasks: 0,
        completedTasks: 0,
        progress: 0,
      }],
      inventory: [],
      alerts: [],
      queues: { conveyor: [], station: [], buffer: [] },
      createdAt: '2026-10-28T00:00:00Z',
      updatedAt: '2026-10-28T00:00:12Z',
    }))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByTestId('simulation-3d')
    expect(screen.getByRole('button', { name: '人工拣选出口机械臂预览' })).toBeDisabled()
    expect(sceneMock.lastProps).toMatchObject({
      pickingArmPreviewVisible: false,
      simulationActive: true,
    })
    fireEvent.click(screen.getByRole('button', { name: '新建订单' }))
    fireEvent.click(screen.getByRole('button', { name: /CTU多箱订单/ }))
    fireEvent.click(screen.getByRole('button', { name: '提交订单' }))

    await waitFor(() => expect(api.insertSimulationOrder).toHaveBeenCalledWith(
      'run-active',
      expect.objectContaining({ lines: expect.any(Array) }),
    ))
    const inserted = vi.mocked(api.insertSimulationOrder).mock.calls[0][1]
    expect(await screen.findByText(`订单 ${inserted.id} 已插入当前仿真并进入调度。`)).toBeInTheDocument()
    expect(screen.getAllByText(inserted.id).length).toBeGreaterThan(0)
  })

  it('keeps an independent arm conveyor task visible when another order is selected', async () => {
    const selectedTask = taskFixture('task-selected', 'ORDER-SELECTED', 'pickup_from_shuttle')
    const armTask = {
      ...taskFixture('task-arm', 'SHUTTLE-DOCK', 'arm_conveyor_delivery'),
      armPlacedCellId: 'cell-r84-c50',
      conveyorTransport: {
        phase: 'arm_conveyor_delivery',
        deck: 'upper' as const,
        route: ['1F:84:50:upper', '1F:84:52:upper'],
        index: 0,
        currentBlockId: '1F:84:50:upper',
        nextBlockId: '1F:84:52:upper',
        waitingTicks: 0,
        movedTicks: 0,
        startedTick: 10,
      },
    }
    usePlatformStore.setState({
      selectedTopologyId: topology.id,
      selectedRunId: 'run-arm-visibility',
      liveState: {
        id: 'run-arm-visibility',
        status: 'running',
        tick: 10,
        topologyId: topology.id,
        robots: [],
        tasks: [selectedTask, armTask],
        orders: [{
          id: 'ORDER-SELECTED',
          priority: 'normal',
          dispatchMode: 'parallel',
          maxParallelCtu: 1,
          targetStation: '出库口 01',
          createdAt: '2026-11-03T00:00:00Z',
          lines: [{ id: 'line-1', skuId: 'SKU-A', skuName: '测试货物', quantity: 1 }],
          status: 'executing',
          statusLabel: '执行中',
          taskIds: [selectedTask.id],
          totalTasks: 1,
          completedTasks: 0,
          progress: 0,
        }],
        inventory: [],
        alerts: [],
        queues: { conveyor: [], station: [], buffer: [] },
        createdAt: '2026-11-03T00:00:00Z',
        updatedAt: '2026-11-03T00:00:10Z',
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

    await screen.findByTestId('simulation-3d')
    await waitFor(() => expect(sceneMock.lastProps?.tasks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: selectedTask.id }),
        expect.objectContaining({ id: armTask.id, phase: 'arm_conveyor_delivery' }),
      ]),
    ))
  })

  it('shows and deletes a next-run draft immediately after the current run is stopped', async () => {
    usePlatformStore.setState({
      selectedTopologyId: topology.id,
      selectedRunId: 'run-stopped',
      liveState: {
        id: 'run-stopped',
        status: 'stopped',
        tick: 28,
        topologyId: topology.id,
        robots: [],
        tasks: [],
        orders: [{
          id: 'ORD-HISTORY-001',
          priority: 'normal',
          dispatchMode: 'parallel',
          maxParallelCtu: 7,
          targetStation: '出库口 01',
          createdAt: '2026-10-28T00:00:00Z',
          lines: [{ id: 'history-line', skuId: 'sku-1', skuName: '六角螺栓 M8', quantity: 1 }],
          status: 'completed',
          statusLabel: '已完成',
          taskIds: [],
          totalTasks: 1,
          completedTasks: 1,
          progress: 100,
        }],
        inventory: [],
        alerts: [],
        queues: { conveyor: [], station: [], buffer: [] },
        createdAt: '2026-10-28T00:00:00Z',
        updatedAt: '2026-10-28T00:00:28Z',
      },
    })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByTestId('simulation-3d')
    expect(screen.getAllByText('ORD-HISTORY-001').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: '新建订单' }))
    fireEvent.click(screen.getByRole('button', { name: /CTU多箱订单/ }))
    fireEvent.click(screen.getByRole('button', { name: '提交订单' }))

    const removeButton = await screen.findByRole('button', { name: /删除订单 ORD-/ })
    const draftId = removeButton.getAttribute('aria-label')?.replace('删除订单 ', '') ?? ''
    expect(draftId).toMatch(/^ORD-/)
    expect(screen.getAllByText(draftId).length).toBeGreaterThan(0)
    expect(screen.getByText('待下次启动')).toBeInTheDocument()
    expect(api.insertSimulationOrder).not.toHaveBeenCalled()

    fireEvent.click(removeButton)
    await waitFor(() => expect(screen.queryByRole('button', { name: `删除订单 ${draftId}` })).not.toBeInTheDocument())
    expect(screen.getAllByText('ORD-HISTORY-001').length).toBeGreaterThan(0)
    confirm.mockRestore()
  })

  it('keeps one 3D scene mounted while the transfer area opens and returns from its inventory panel', async () => {
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
    expect(screen.getByRole('button', { name: '查看库存' })).toBeInTheDocument()
    expect(screen.getByTestId('simulation-3d')).toBe(scene)

    fireEvent.click(screen.getByRole('button', { name: '查看库存' }))
    expect(screen.getByLabelText('库存详情')).toBeInTheDocument()
    expect(screen.getByText(/此处不生成或猜测 WMS 库存/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '规划路径' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '返回跨层运输' })).toBeInTheDocument()
    expect(screen.getByTestId('simulation-3d')).toBe(scene)

    fireEvent.click(screen.getByRole('button', { name: '返回跨层运输' }))
    expect(screen.getByLabelText('跨层运输')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看库存' })).toBeInTheDocument()
    expect(screen.getByTestId('simulation-3d')).toBe(scene)

    fireEvent.click(screen.getByRole('button', { name: '订单调度' }))
    expect((await screen.findAllByText('ORD-DEMO-001')).length).toBeGreaterThan(0)
    expect(screen.getByTestId('simulation-3d')).toBe(scene)
  })

  it('preserves pending transfer and order drafts across transfer and inventory workspace switches', async () => {
    vi.mocked(api.getShuttleContract).mockResolvedValueOnce({
      topologyId: topology.id,
      topologyRevision: 'test',
      configRevision: 'test',
      coordinateContractRevision: 'shuttle-internal-levels-v1',
      verificationStatus: 'program_verified_pending_human',
      planningEnabled: true,
      warnings: [],
      lifts: [],
      tracks: [
        { topology_id: topology.id, zone_id: 'zone-1', track_node_id: 'L1-r100-c7', shuttle_level: 1, row: 100, col: 7, world_x: 0, world_y: 0, world_z: 0, track_type: 'straight', traversable: true, neighbors: [] },
        { topology_id: topology.id, zone_id: 'zone-4', track_node_id: 'L4-r100-c51', shuttle_level: 4, row: 100, col: 51, world_x: 10, world_y: 4, world_z: 0, track_type: 'straight', traversable: true, neighbors: [] },
      ],
    })
    vi.mocked(api.planShuttleTransfer).mockResolvedValueOnce({
      topologyId: topology.id,
      topologyRevision: 'test',
      configRevision: 'test',
      coordinateContractRevision: 'shuttle-internal-levels-v1',
      algorithm: 'astar',
      chosen_lift: null,
      path_nodes: [],
      path_edges: [],
      lift_transitions: [],
      estimated_duration_seconds: 10,
      result_status: 'success',
      timing_source: 'synthetic_demo',
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation/orders?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByTestId('simulation-3d')
    expect((await screen.findAllByText('ORD-DEMO-001')).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: '跨层运输' }))
    const planButton = await screen.findByRole('button', { name: '规划路径' })
    await waitFor(() => expect(planButton).toBeEnabled())
    fireEvent.click(planButton)
    await screen.findByText(/ASTAR · 0 节点/)
    fireEvent.click(screen.getByRole('button', { name: '加入仿真' }))
    expect(screen.getByText('SKU-001 · 2 箱')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '查看库存' }))
    expect(screen.getByLabelText('库存详情')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回跨层运输' }))
    expect(screen.getByText('SKU-001 · 2 箱')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '订单调度' }))
    expect((await screen.findAllByText('ORD-DEMO-001')).length).toBeGreaterThan(0)
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
      expect.arrayContaining([expect.objectContaining({
        id: 'ORD-DEMO-001',
        dispatchMode: 'parallel',
        maxParallelCtu: 7,
      })]),
      [],
      undefined,
      undefined,
      undefined,
    )
    expect(within(screen.getByLabelText('仿真运行状态')).getByText('运行中')).toBeInTheDocument()
  })

  it('keeps metric distance disabled by default and exposes editable example values', async () => {
    renderSimulationPage()
    await screen.findByTestId('simulation-3d')
    expect(screen.getByLabelText('下次新建 Run 启用米制模拟')).not.toBeChecked()
    expect(screen.getByLabelText('每格水平长度')).toHaveValue(0.8)
    expect(screen.getByLabelText('Shuttle层高')).toHaveValue(3)
    expect(screen.getByLabelText('仓库楼层高度')).toHaveValue(4)
    expect(screen.getByLabelText('每格水平长度')).toBeDisabled()
  })

  it('keeps the next-Run draft when the shared run configuration drawer closes', async () => {
    renderSimulationPage()
    await screen.findByTestId('simulation-3d')
    const summary = screen.getByLabelText('当前Run冻结配置摘要')
    expect(within(summary).getAllByText('未冻结')).toHaveLength(4)
    fireEvent.click(screen.getByRole('button', { name: '运行配置' }))
    expect(document.querySelector('.simulation-run-config-drawer')).toHaveClass('open')
    fireEvent.click(screen.getByLabelText('下次新建 Run 启用米制模拟'))
    fireEvent.change(screen.getByLabelText('每格水平长度'), { target: { value: '1.1' } })
    fireEvent.click(screen.getByRole('button', { name: '关闭运行配置' }))
    expect(document.querySelector('.simulation-run-config-drawer')).not.toHaveClass('open')
    fireEvent.click(screen.getByRole('button', { name: '运行配置' }))
    expect(screen.getByLabelText('每格水平长度')).toHaveValue(1.1)
    expect(within(summary).queryByText(/1.1 m\/格/)).not.toBeInTheDocument()
  })

  it('sends the complete user-defined physics contract and displays the frozen Run values', async () => {
    const frozen = {
      physicsContractVersion: 'motion-physics-v1',
      coordinateContractVersion: 'topology-grid-v1',
      metersPerCell: 0.8,
      shuttleLevelHeightMeters: 3,
      floorHeightMeters: 4,
      source: 'USER_DEFINED_SIMULATION' as const,
      calibrationStatus: 'UNVERIFIED' as const,
    }
    vi.mocked(api.startSimulation).mockResolvedValue({ runId: 'run-physics', status: 'running' })
    vi.mocked(api.getSimulationState).mockResolvedValue({
      ...simulationStateFixture('run-physics'),
      motionPhysicsContract: frozen,
    })
    renderSimulationPage()
    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByLabelText('下次新建 Run 启用米制模拟'))
    fireEvent.click(screen.getByRole('button', { name: '启动' }))
    await waitFor(() => expect(api.startSimulation).toHaveBeenCalledWith(
      topology.id,
      expect.any(Array),
      [],
      frozen,
      undefined,
      undefined,
    ))
    expect(await screen.findByText(/冻结值：0.8 m\/格 · Shuttle 3 m\/层 · 楼层 4 m\/层/)).toBeInTheDocument()
    expect(screen.getByLabelText('每格水平长度')).toBeDisabled()
    expect(screen.getByText(/冻结合同不会被添加订单或播放倍率修改/)).toBeInTheDocument()
  })

  it('freezes vehicle speed separately from playback and sends the experiment selection', async () => {
    const speed = {
      speedContractVersion: 'motion-speed-v1',
      simTickSeconds: 0.6,
      ctuSpeedMetersPerSecond: 1,
      shuttleSpeedMetersPerSecond: 1.2,
      liftSpeedMetersPerSecond: 0.5,
      source: 'USER_DEFINED_SIMULATION' as const,
      calibrationStatus: 'UNVERIFIED' as const,
    }
    vi.mocked(api.startSimulation).mockResolvedValue({ runId: 'run-speed', status: 'running' })
    vi.mocked(api.getSimulationState).mockResolvedValue({
      ...simulationStateFixture('run-speed'),
      motionPhysicsContract: {
        physicsContractVersion: 'motion-physics-v1',
        coordinateContractVersion: 'topology-grid-v1',
        metersPerCell: 0.8,
        shuttleLevelHeightMeters: 3,
        floorHeightMeters: 4,
        source: 'USER_DEFINED_SIMULATION',
        calibrationStatus: 'UNVERIFIED',
      },
      motionSpeedContract: speed,
    })
    renderSimulationPage()
    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByLabelText('下次新建 Run 启用米制模拟'))
    fireEvent.click(screen.getByLabelText('下次新建 Run 启用物理速度模拟'))
    fireEvent.click(screen.getByRole('button', { name: '启动' }))
    await waitFor(() => expect(api.startSimulation).toHaveBeenCalled())
    const call = vi.mocked(api.startSimulation).mock.calls.at(-1)
    expect(call?.[4]).toEqual(speed)
    expect(call?.[5]).toBeUndefined()
    expect(await screen.findByText(/冻结速度：CTU 1 m\/s/)).toBeInTheDocument()
    expect(screen.queryByText(/播放倍率不代表车辆 m\/s/)).not.toBeInTheDocument()
  })

  it('uses the algorithm selection cards only for the next Run experiment contract', async () => {
    vi.mocked(api.startSimulation).mockResolvedValue({ runId: 'run-algorithm', status: 'running' })
    vi.mocked(api.getSimulationState).mockResolvedValue(simulationStateFixture('run-algorithm'))
    renderSimulationPage()
    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByRole('button', { name: '运行配置' }))
    fireEvent.click(screen.getByLabelText('下次新建 Run 启用米制模拟'))
    fireEvent.click(screen.getByLabelText('下次新建 Run 启用物理速度模拟'))
    fireEvent.click(screen.getByLabelText('将下次 Run 标记为可复现实验'))
    fireEvent.click(within(screen.getByRole('group', { name: '路径算法' })).getByRole('button', { name: /Dijkstra/ }))
    fireEvent.click(within(screen.getByRole('group', { name: '分配算法' })).getByRole('button', { name: /Hungarian/ }))
    fireEvent.click(screen.getByRole('button', { name: '完成' }))
    fireEvent.click(screen.getByRole('button', { name: '启动' }))
    await waitFor(() => expect(api.startSimulation).toHaveBeenCalled())
    expect(vi.mocked(api.startSimulation).mock.calls.at(-1)?.[5]).toMatchObject({
      scenarioId: 'p5-comparison-01',
      topologyRevision: '2.0.0:test',
      pathPolicy: 'dijkstra',
      assignmentPolicy: 'hungarian',
      variableDimension: 'pathPolicy',
    })
    expect(vi.mocked(api.startSimulation).mock.calls.at(-1)?.[5]).not.toHaveProperty('observationTicks')
  })

  it('freezes an explicit throughput observation window without changing ordinary P5 experiments', async () => {
    vi.mocked(api.startSimulation).mockResolvedValue({ runId: 'run-throughput-window', status: 'running' })
    vi.mocked(api.getSimulationState).mockResolvedValue(simulationStateFixture('run-throughput-window'))
    renderSimulationPage()
    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByRole('button', { name: '运行配置' }))
    fireEvent.click(screen.getByLabelText('下次新建 Run 启用米制模拟'))
    fireEvent.click(screen.getByLabelText('下次新建 Run 启用物理速度模拟'))
    fireEvent.click(screen.getByLabelText('将下次 Run 标记为可复现实验'))
    fireEvent.click(screen.getByLabelText('启用固定吞吐观察窗口'))
    fireEvent.change(screen.getByLabelText('吞吐观察窗口Tick'), { target: { value: '600' } })
    fireEvent.click(screen.getByRole('button', { name: '完成' }))
    fireEvent.click(screen.getByRole('button', { name: '启动' }))
    await waitFor(() => expect(api.startSimulation).toHaveBeenCalled())
    expect(vi.mocked(api.startSimulation).mock.calls.at(-1)?.[5]).toMatchObject({ observationTicks: 600 })
  })

  it.each(['0', '-0.1', ''])('blocks Run creation for invalid metersPerCell %s', async (value) => {
    renderSimulationPage()
    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByLabelText('下次新建 Run 启用米制模拟'))
    fireEvent.change(screen.getByLabelText('每格水平长度'), { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: '启动' }))
    expect(await screen.findByText('每格水平长度必须是有限正数。')).toBeInTheDocument()
    expect(api.startSimulation).not.toHaveBeenCalled()
  })

  it('states that a legacy Run without a frozen contract is unaffected by the next-Run draft', async () => {
    const state = simulationStateFixture('run-legacy')
    state.status = 'completed'
    usePlatformStore.setState({ selectedRunId: state.id, liveState: state })
    vi.mocked(api.getSimulationState).mockResolvedValue(state)

    renderSimulationPage()

    fireEvent.click(screen.getByRole('button', { name: '运行配置' }))
    expect(await screen.findByText('当前 Run 未采用米制合同；下一次启动草稿不会改写此 Run。')).toBeInTheDocument()
    expect(screen.getAllByText('历史 Run：只能查看历史冻结配置')).toHaveLength(2)
  })

  it('reports a backend contract-loss error instead of claiming the new Run is metric-enabled', async () => {
    vi.mocked(api.startSimulation).mockResolvedValue({ runId: 'run-contract-lost', status: 'running' })
    vi.mocked(api.getSimulationState).mockResolvedValue(simulationStateFixture('run-contract-lost'))
    renderSimulationPage()
    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByLabelText('下次新建 Run 启用米制模拟'))
    fireEvent.click(screen.getByRole('button', { name: '启动' }))

    expect(await screen.findByText(/当前后端未冻结已提交的米制合同/)).toBeInTheDocument()
    expect(screen.getByText('当前 Run 未采用米制合同；下一次启动草稿不会改写此 Run。')).toBeInTheDocument()
  })
})
