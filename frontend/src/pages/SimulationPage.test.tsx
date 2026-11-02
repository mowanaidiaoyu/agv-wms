import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'
import type { SimulationState, TopologyPayload, TransportLayoutPlan } from '../types'
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

const armPlan: TransportLayoutPlan = {
  planId: 'arm-preview-test',
  schemaVersion: '1',
  topologyId: topology.id,
  topologyRevision: '2.0.0:test',
  overallStatus: 'PARTIAL',
  pickingArm: {
    armId: 'proposed-picking-arm-test',
    status: 'proposed',
    validationStatus: 'PARTIAL',
    installation: { floor: '1F', row: 3, col: 8, level: 1 },
    mountHeight: 0,
    workRadius: 7.9,
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
      nodeId: 'pickup', topologyCellId: 'cell-r4-c2',
      position: { floor: '1F', row: 4, col: 2, level: 1 },
      beltHeight: 0.6, deckIndex: 1, direction: 'right',
      provenance: 'topology', validationStatus: 'PASS', evidence: [],
    },
    dropoff: {
      nodeId: 'dropoff', topologyCellId: 'cell-r2-c4',
      position: { floor: '1F', row: 2, col: 4, level: 2 },
      beltHeight: 3, deckIndex: 2, direction: 'right',
      provenance: 'topology', validationStatus: 'PASS', evidence: [],
    },
    liftBId: 'lift-b',
    liftBPosition: { floor: '1F', row: 10, col: 1, level: 1 },
    liftBClearanceCells: 8,
    downstreamCellId: 'cell-r2-c5',
    downstreamStatus: 'PASS',
    previewConveyorPath: [
      {
        cellId: 'cell-r2-c4', position: { floor: '1F', row: 2, col: 4, level: 2 },
        direction: 'right', beltHeight: 3, deviceIds: [], transitionToNext: 'conveyor',
        directionSource: 'topology',
      },
      {
        cellId: 'cell-r2-c5', position: { floor: '1F', row: 2, col: 5, level: 2 },
        direction: 'right', beltHeight: 3, deviceIds: [], transitionToNext: 'endpoint',
        directionSource: 'topology',
      },
    ],
    previewEndpointId: 'cell-r2-c5',
    previewPathStatus: 'PASS',
    previewStopReason: 'test endpoint',
    mergePoint: {
      mergePointId: 'right-order-merge-test',
      topologyNodeId: 'lift-test',
      row: 2,
      col: 5,
      level: 2,
      capacity: 1,
      occupiedCargoIds: [],
      acceptedCargoIds: [],
      status: 'available',
      acceptedWarehouse: 'right',
      cargoOrderIds: {},
    },
    actionSequence: ['idle', 'approach', 'grip', 'lift', 'rotate', 'place', 'release', 'return_home'],
    requiredAuthoritativeSignals: ['pickedGoodsReady', 'outputPositionAvailable', 'handoffCompleted'],
    canDeclareDelivery: false,
    evidence: [],
  },
  summary: {
    previewOnly: true,
    inventoryMutated: false,
    ordersCompleted: false,
    formalTopologyMutated: false,
    conveyorNodeCount: 2,
  },
}

vi.mock('../lib/api', () => ({
  api: {
    listTopologies: vi.fn(async () => [{ id: topology.id, name: topology.name, payload: topology }]),
    getTopology: vi.fn(async () => ({ id: topology.id, name: topology.name, payload: topology })),
    getTransportLayoutPlan: vi.fn(async () => armPlan),
    getSimulationState: vi.fn(),
    getSimulationMetrics: vi.fn(),
    getGoodsCatalog: vi.fn(async () => ({
      version: 1,
      skus: [
        { skuId: 'sku-1', name: '六角螺栓 M8', spec: 'M8', unitsPerCase: 20, packagingSource: 'verified-test', verificationStatus: 'verified' },
        { skuId: 'sku-2', name: '法兰螺母 M10', spec: 'M10' },
        { skuId: 'sku-3', name: '弹簧垫圈 M8', spec: 'M8' },
        { skuId: 'sku-4', name: '内六角螺钉 M6', spec: 'M6' },
        { skuId: 'sku-5', name: '平垫圈 M12', spec: 'M12' },
        { skuId: 'sku-6', name: '高强度螺栓 M16', spec: 'M16' },
        { skuId: 'sku-7', name: '锁紧螺母 M16', spec: 'M16' },
      ],
    })),
    getFulfillmentTestScenario: vi.fn(),
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
    startFulfillmentTestSimulation: vi.fn(),
    planOrderFulfillment: vi.fn(async (_topologyId: string, order: { id: string }) => ({
      orderId: order.id,
      allocationStatus: 'ready',
      allocations: [{
        orderId: order.id,
        lineId: 'line-test',
        skuId: 'sku-1',
        quantityBase: 110,
        unitsPerCase: 20,
        rightQuantityCases: 5,
        rightQuantityBase: 100,
        leftSourceCases: 1,
        leftSourceQuantityBase: 20,
        leftPickQuantityBase: 10,
        leftReturnQuantityBase: 10,
        allocationStatus: 'ready',
        shortageQuantityBase: 0,
        inventorySufficient: true,
        packagingSource: 'verified-test',
        packagingVerificationStatus: 'verified',
        rightTasks: [],
        leftTasks: [],
      }],
      tasks: [],
      inventoryMutated: false,
      inventoryReserved: false,
      previewCargoAccepted: false,
      canDeclareCompleted: false,
      requestFingerprint: 'test',
    })),
    insertSimulationOrder: vi.fn(),
    cancelSimulationOrder: vi.fn(),
    pauseSimulation: vi.fn(),
    resumeSimulation: vi.fn(),
    stopSimulation: vi.fn(),
    resetSimulation: vi.fn(),
    setSimulationSpeed: vi.fn(),
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
    vi.mocked(api.startFulfillmentTestSimulation).mockReset()
    vi.mocked(api.insertSimulationOrder).mockReset()
    vi.mocked(api.cancelSimulationOrder).mockReset()
    vi.mocked(api.getSimulationState).mockReset()
    vi.mocked(api.getTopology).mockResolvedValue({ id: topology.id, name: topology.name, payload: topology })
    vi.mocked(api.getTransportLayoutPlan).mockResolvedValue(armPlan)
    vi.mocked(api.getFulfillmentTestScenario).mockResolvedValue({
      scenarioId: 'right-left-110-case-test',
      status: 'PASS',
      dataSource: 'simulation_test',
      isolationMode: 'run_local_snapshot',
      topologyId: topology.id,
      topologyRevision: '2.0.0:test',
      sku: { skuId: 'sku-test', unitsPerCase: 20, packagingSource: 'scenario', verificationStatus: 'simulation_test_confirmed', scope: 'simulation_test' },
      inventory: [],
      locationEvidence: {},
      order: { id: 'ORD-TEST', priority: 'normal', dispatchMode: 'balanced', maxParallelCtu: 7, targetStation: 'test', createdAt: '2026-10-30T00:00:00Z', lines: [] },
      plan: { orderId: 'ORD-TEST', allocationStatus: 'ready', allocations: [], tasks: [], inventoryMutated: false, inventoryReserved: false, previewCargoAccepted: false, canDeclareCompleted: false, requestFingerprint: 'test' },
      rightHandoff: {
        formalRouteStatus: 'BLOCKED', simulationRouteStatus: 'PASS_SIMULATION_ONLY', formalBlockers: [],
        existingDockAssessment: { deviceId: 'dock-01', validationStatus: 'blocked', reason: 'not connected' },
        sourceTrackNodeId: 'L2-r96-c31', provenLiftExitTrackNodeId: 'L1-r96-c21', handoffTrackNodeId: 'L1-r96-c27',
        armReceiveNodeId: 'cell-r92-c48', shuttlePath: [], egressPath: [], chosenLift: 'shuttle-lift-a', conveyorPath: [],
        facilities: [{
          deviceId: 'simtest-dock', deviceType: 'shuttle_conveyor_dock', inputNodeId: 'L1-r96-c27', outputNodeId: 'cell-r95-c27',
          sourceLevel: 1, targetLevel: 1, capacity: 1, transferDuration: 2, dataSource: 'simulation_test', validationStatus: 'simulation_only',
          geometry: {
            floor: '1F', beltHeight: 0.6,
            pathCells: [{ row: 96, col: 27 }, { row: 95, col: 27 }, { row: 94, col: 27 }],
            worldPoints: [{ x: -22, y: 0.6, z: 11 }, { x: -22, y: 0.6, z: 13 }],
            visualStyle: 'amber',
          },
        }],
        authority: { stateOwner: 'SimulationService', requiresUnifiedTick: true, previewSignalsAccepted: false, animationCanCompleteBusinessTask: false },
        cargo: Array.from({ length: 5 }, (_, index) => ({
          cargoId: `cargo-${index}`, orderId: 'ORD-TEST', taskId: `task-${index}`, skuId: 'sku-test', quantityCases: 1, quantityBase: 20,
          sourceLocationId: 'SIM-BIN', currentNodeId: 'L2-r96-c31', currentDeviceId: null, destinationNodeId: 'cell-r92-c48',
          status: 'waiting_pick', dataSource: 'simulation_test', authoritative: false, preview: false,
        })),
        segments: [],
      },
      businessGuards: {},
      knownPhysicalGaps: [],
    })
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

  it('keeps the picking arm mounted and focuses it without starting an independent preview', async () => {
    const topologySnapshot = structuredClone(topology)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByTestId('simulation-3d')
    expect(sceneMock.lastProps).toMatchObject({
      pickingArmLayoutPlan: armPlan,
      pickingArmFocused: false,
      pickingArmFocusRequest: 0,
      simulationMotionEpoch: 'no-run:0',
      paused: false,
    })

    const focus = await screen.findByRole('button', { name: '聚焦机械臂' })
    await waitFor(() => expect(focus).toBeEnabled())
    fireEvent.click(focus)

    expect(await screen.findByLabelText('机械臂状态面板')).toBeInTheDocument()
    expect(screen.getByText('当前阶段').parentElement).toHaveTextContent('idle')
    expect(screen.getByText('当前货箱').parentElement).toHaveTextContent('无')
    expect(screen.getByText('当前接货位').parentElement).toHaveTextContent('空闲 · 0/1')
    expect(screen.getByText(/right-order-merge-test/)).toHaveTextContent('r2, c5')
    expect(sceneMock.lastProps).toMatchObject({
      pickingArmLayoutPlan: armPlan,
      pickingArmFocused: true,
      pickingArmFocusRequest: 1,
      paused: false,
    })

    expect(screen.getByRole('button', { name: '暂停' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '继续' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重置' })).toBeDisabled()
    expect(api.pauseSimulation).not.toHaveBeenCalled()
    expect(api.resetSimulation).not.toHaveBeenCalled()
    expect(api.setSimulationSpeed).not.toHaveBeenCalled()
    expect(topology).toEqual(topologySnapshot)
    expect(api.startSimulation).not.toHaveBeenCalled()
    expect(usePlatformStore.getState().selectedRunId).toBe('')
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
    )
    expect(within(screen.getByLabelText('仿真运行状态')).getByText('运行中')).toBeInTheDocument()
  })

  it('plans a base-unit order before queuing it and shows the case/break-pack split', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation/orders?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByRole('button', { name: '新建订单' }))
    fireEvent.change(screen.getByRole('spinbutton', { name: '订单基础件数' }), { target: { value: '110' } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))
    fireEvent.click(screen.getByRole('button', { name: '提交订单' }))

    await waitFor(() => expect(api.planOrderFulfillment).toHaveBeenCalledWith(
      topology.id,
      expect.objectContaining({ lines: [expect.objectContaining({ quantityBase: 110, unitsPerCase: 20 })] }),
    ))
    expect(await screen.findByText('整箱 / 拆零计划')).toBeInTheDocument()
    expect(screen.getByText('5 箱 / 100 件')).toBeInTheDocument()
    expect(screen.getAllByText('10 件')).toHaveLength(2)
    expect(api.startSimulation).not.toHaveBeenCalled()
  })

  it('loads the F3-A scenario in the order page and exposes its simulation-only 3D overlay', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation/orders?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByRole('button', { name: '新建订单' }))
    fireEvent.click(screen.getByRole('button', { name: /110件订单仿真测试/ }))
    expect(await screen.findByLabelText('110件订单仿真测试场景')).toHaveTextContent('simulation_test')
    await waitFor(() => expect(sceneMock.lastProps).toMatchObject({
      simulationTestHandoff: expect.objectContaining({ simulationRouteStatus: 'PASS_SIMULATION_ONLY' }),
    }))
    expect(api.startSimulation).not.toHaveBeenCalled()
  })

  it('starts the audited simulation_test Run and renders only server-authoritative cargo state', async () => {
    const position = {
      nodeId: 'L2-r96-c31', row: 96, col: 31, level: 2,
      worldX: -18, worldY: 3.8, worldZ: 11,
    }
    const leftPosition = {
      nodeId: 'cell-r39-c91', row: 39, col: 91, level: 1,
      worldX: 41.5, worldY: 0, worldZ: 68.5,
    }
    const leftFrame = { cellId: leftPosition.nodeId, level: 1, stage: 'track' as const, nodeId: leftPosition.nodeId, position: leftPosition, index: 0 }
    const cargo = Array.from({ length: 5 }, (_, index) => ({
      cargoId: `cargo-${index}`, orderId: 'ORD-TEST', taskId: `task-${index}`,
      skuId: 'sku-test', quantityCases: 1, quantityBase: 20,
      sourceLocationId: 'SIM-BIN-L2-r96-c31', currentNodeId: position.nodeId,
      currentDeviceId: null, destinationNodeId: 'cell-r92-c48', status: 'waiting_pick' as const,
      dataSource: 'simulation_test' as const, authoritative: false as const, preview: false as const,
      simulationStateAuthoritative: true as const,
      previousPosition: position, currentPosition: position, positionTick: 0, waitingTicks: 0,
    }))
    const authoritativeState = {
      id: 'run-test-authoritative', status: 'running', tick: 4,
      topologyId: topology.id, topologyRevision: '2.0.0:test',
      runMode: 'simulation_test', scenarioId: 'right-left-110-case-test',
      dataSource: 'simulation_test', simulationStateAuthoritative: true,
      robots: [], tasks: [], orders: [], inventory: [], alerts: [],
      queues: { conveyor: [], station: [], buffer: [] }, speedMultiplier: 2,
      createdAt: '2026-10-30T00:00:00Z', updatedAt: '2026-10-30T00:00:04Z',
      simulationTest: {
        scenarioId: 'right-left-110-case-test', runMode: 'simulation_test', dataSource: 'simulation_test',
        topologyRevision: '2.0.0:test', simulationStateAuthoritative: true,
        formalRouteStatus: 'BLOCKED', simulationRouteStatus: 'PASS_SIMULATION_ONLY',
        syntheticTimingDisclosure: 'synthetic_demo timing',
        order: {
          orderId: 'ORD-TEST', quantityBase: 110, rightQuantityBase: 100,
          leftSourceQuantityBase: 20, leftPickQuantityBase: 10, leftReturnQuantityBase: 10,
          status: 'both_sides_transporting', canDeclareCompleted: false,
          formalOrderCompleted: false, physicalDeliveryConfirmed: false,
          rightMergeAcceptedQuantityBase: 0, leftPickedQuantityBase: 0,
          leftReturnedQuantityBase: 0, quantityAccounted: 0,
        },
        inventory: {
          isolationMode: 'run_local_snapshot', initializedOnce: true,
          right: { initialCases: 5, availableCases: 5, inTransitCases: 0, waitingArmCases: 0, mergeArrivedCases: 0 },
          left: { sourceLocationId: 'storage-01:L1', sourceQuantityBase: 20, pickQuantityBase: 10, returnQuantityBase: 10, status: 'waiting_split', dataSource: 'simulation_test' },
        },
        leftFlow: {
          status: 'waiting_pick', taskId: 'task-left', sourceLocationId: 'storage-01:L1',
          sourceCellId: 'cell-r0-c6', sourceStopCellId: 'cell-r0-c7', sourcePosition: { ...leftPosition, nodeId: 'cell-r0-c6', row: 0, col: 6 },
          cargo: {
            cargoId: 'left-case', orderId: 'ORD-TEST', taskId: 'task-left', skuId: 'sku-test',
            quantityCases: 1, quantityBase: 20, originalQuantityBase: 20,
            pickedQuantityBase: 0, remainingQuantityBase: 20, sourceLocationId: 'storage-01:L1',
            currentNodeId: 'cell-r0-c6', currentDeviceId: null, status: 'waiting_pick',
            caseType: 'full_case', dataSource: 'simulation_test', authoritative: false, preview: false,
            simulationStateAuthoritative: true,
            currentPosition: { ...leftPosition, nodeId: 'cell-r0-c6', row: 0, col: 6 },
            previousPosition: { ...leftPosition, nodeId: 'cell-r0-c6', row: 0, col: 6 },
          },
          partialCase: null,
          ctu: {
            deviceId: 'ctu-07', capacity: 1, phase: 'to_source', phaseTicks: 0,
            activeCargoId: null, routeName: 'to_source', routeIndex: 0,
            toSourceFrames: [leftFrame], toStationFrames: [leftFrame], toStorageFrames: [leftFrame],
            currentPosition: leftPosition, dataSource: 'simulation_test',
          },
          station: {
            deviceId: 'simtest-left-break-pack-r72-c66', deviceType: 'break_pack_station',
            positionCellId: 'cell-r72-c66', inputNodeId: 'cell-r72-c65', outputNodeId: 'cell-r72-c65',
            capacity: 1, reservedCargoIds: [], occupiedCargoIds: [], phase: 'idle', phaseTicks: 0,
            position: { ...leftPosition, nodeId: 'cell-r72-c66', row: 72, col: 66 },
            dataSource: 'simulation_test', validationStatus: 'simulation_only',
          },
          ledger: {
            initialSourceQuantityBase: 20, sourceQuantityBase: 20, ctuQuantityBase: 0,
            stationQuantityBase: 0, pickedQuantityBase: 0, returnedQuantityBase: 0, returnApplied: false,
          },
          quantityAudit: { sourceConserved: false, orderQuantityAccounted: false },
        },
        cargo,
        shuttle: { deviceId: 'shuttle-robot-01', capacity: 1, occupiedCargoIds: [], phase: 'idle' },
        lift: { deviceId: 'shuttle-lift-a', capacity: 1, occupiedCargoIds: [], dataSource: 'synthetic_demo', validationStatus: 'simulation_only' },
        facilities: [],
        conveyor: { path: [], occupiedByBlock: {}, capacityPerBlock: 1, gridRows: 216, gridCols: 100 },
        armInfeed: { nodeId: 'cell-r92-c48', capacity: 1, occupiedCargoIds: [], status: 'simulation_only_waiting_buffer' },
        arm: {
          armId: 'proposed-picking-arm-r91-c54', pickupNodeId: 'cell-r92-c48', dropoffNodeId: 'cell-r86-c50',
          capacity: 1, phase: 'idle', phaseElapsedTicks: 0, phaseDurationTicks: 0, phaseProgress: 0,
          activeCargoId: null, cargoPlacement: 'none', completedCargoIds: [],
          dataSource: 'simulation_test', simulationStateAuthoritative: true,
        },
        mergeConveyor: { path: [], occupiedByNode: {}, capacityPerBlock: 1 },
        mergePoint: {
          mergePointId: 'right-order-merge-r108-c68', topologyNodeId: 'lift_transfer-09',
          nodeId: 'cell-r108-c68', capacity: 1, acceptedCargoIds: [], acceptedCount: 0,
          dataSource: 'simulation_test',
        },
        events: [], resetGeneration: 0, lastProgressTick: 4, blockedTicks: 0, diagnostics: [],
      },
    } as SimulationState
    vi.mocked(api.startFulfillmentTestSimulation).mockResolvedValue({
      runId: authoritativeState.id,
      status: 'running',
      runMode: 'simulation_test',
      scenarioId: 'right-left-110-case-test',
    })
    vi.mocked(api.getSimulationState).mockResolvedValue(authoritativeState)

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/simulation/orders?topologyId=topology-test']}>
          <SimulationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByTestId('simulation-3d')
    fireEvent.click(screen.getByRole('button', { name: '新建订单' }))
    fireEvent.click(screen.getByRole('button', { name: /110件订单仿真测试/ }))
    fireEvent.click(await screen.findByRole('button', { name: '启动测试仿真' }))

    await waitFor(() => expect(api.startFulfillmentTestSimulation).toHaveBeenCalledWith(
      topology.id,
      '2.0.0:test',
      'right-left-110-case-test',
    ))
    const runPanel = await screen.findByLabelText('110件订单仿真测试运行状态')
    expect(runPanel).toHaveTextContent('服务器统一 Tick')
    expect(within(runPanel).getByText('5箱 / 100件')).toBeInTheDocument()
    expect(within(runPanel).getByText('waiting_pick · 取货20 / 拣选0 / 回库0')).toBeInTheDocument()
    expect(within(runPanel).getByText('ctu-07 · to_source')).toBeInTheDocument()
    expect(within(runPanel).getByText('0 / 110 件 · 处理中')).toBeInTheDocument()
    expect(within(runPanel).getByText('idle')).toBeInTheDocument()
    expect(within(runPanel).getByText('空闲 · 0/1')).toBeInTheDocument()
    expect(within(runPanel).getByText('0/5箱')).toBeInTheDocument()
    await waitFor(() => expect(sceneMock.lastProps).toMatchObject({
      simulationTestCargo: cargo,
      simulationTestState: authoritativeState.simulationTest,
      simulationTestHandoff: expect.objectContaining({ simulationRouteStatus: 'PASS_SIMULATION_ONLY' }),
      showSimulationDebugLabels: false,
    }))
    fireEvent.click(within(runPanel).getByRole('button', { name: '显示调试设施与Shuttle轨迹' }))
    await waitFor(() => expect(sceneMock.lastProps).toMatchObject({ showSimulationDebugLabels: true }))
    expect(api.startSimulation).not.toHaveBeenCalled()
  })
})
