import { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  ArrowLeft,
  Bell,
  Bot,
  CheckCircle2,
  ClipboardList,
  Gauge,
  Loader2,
  LocateFixed,
  PanelRightOpen,
  Pause,
  Play,
  Plus,
  RotateCcw,
  Square,
  X,
  Zap,
} from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { KpiGrid } from '../components/KpiGrid'
import {
  OrderComposer,
  OrderRail,
  OrderTimeline,
} from '../components/simulation/OrderWorkspace'
import { CrossLevelTransferPanel } from '../components/simulation/CrossLevelTransferPanel'
import { ShuttleDeliveryPanel } from '../components/simulation/ShuttleDeliveryPanel'
import { SimulationSceneBoundary } from '../components/simulation/SimulationSceneBoundary'
import { TransferInventoryPanel } from '../components/simulation/TransferInventoryPanel'
import { selectSimulationStateSource, useSimulationSocket } from '../hooks/useSimulationSocket'
import { api } from '../lib/api'
import { createDemoOrders } from '../lib/orders'
import { usePlatformStore } from '../store/usePlatformStore'
import type { ShuttleTransferInput, SimulationAlert, SimulationOrderInput } from '../types'

const Simulation3DScene = lazy(() => import('../components/topology/Simulation3DScene'))

const taskModeLabels = {
  direct: 'CTU 多箱取货',
  cross_zone: '跨区协同',
  shuttle_transfer: '跨层运输',
}

const robotTypeLabels = {
  ctu: 'CTU',
  shuttle: '四向车',
}

type DetailTab = 'metrics' | 'devices' | 'tasks' | 'alerts'
type WorkspaceTab = 'orders' | 'shuttle' | 'transfer' | 'inventory'

const runStatusLabels: Record<string, string> = {
  idle: '未启动',
  running: '运行中',
  paused: '已暂停',
  stopped: '已停止',
  completed: '已完成',
}

function alertClassName(level: SimulationAlert['level']) {
  if (level === 'error') return 'alert-card alert-error'
  if (level === 'warning') return 'alert-card alert-warning'
  return 'alert-card alert-info'
}

function LiveEventCount({ persistedCount }: { persistedCount?: number }) {
  const socketWindowCount = usePlatformStore((state) => state.liveEvents.length)
  return <strong>{persistedCount ?? socketWindowCount}</strong>
}

export function SimulationPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const requestedTopologyId = searchParams.get('topologyId') ?? ''
  const queryClient = useQueryClient()
  const selectedTopologyId = usePlatformStore((state) => state.selectedTopologyId)
  const selectedRunId = usePlatformStore((state) => state.selectedRunId)
  const setSelectedTopologyId = usePlatformStore((state) => state.setSelectedTopologyId)
  const setSelectedRunId = usePlatformStore((state) => state.setSelectedRunId)
  const setLiveState = usePlatformStore((state) => state.setLiveState)
  const resetRun = usePlatformStore((state) => state.resetRun)
  const liveState = usePlatformStore((state) => state.liveState)
  const liveMetrics = usePlatformStore((state) => state.liveMetrics)
  const clearLiveEvents = usePlatformStore((state) => state.clearLiveEvents)
  const [activeFloor, setActiveFloor] = useState('1F')
  const [startError, setStartError] = useState('')
  const [starting, setStarting] = useState(false)
  const [completingPicking, setCompletingPicking] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [detailTab, setDetailTab] = useState<DetailTab>('metrics')
  const [orderComposerOpen, setOrderComposerOpen] = useState(false)
  const [ordersCollapsed, setOrdersCollapsed] = useState(false)
  const [localOrders, setLocalOrders] = useState<SimulationOrderInput[]>([])
  const [localTransfers, setLocalTransfers] = useState<ShuttleTransferInput[]>([])
  const [workspaceTab, setWorkspaceTab] = useState<WorkspaceTab>('orders')
  const [selectedOrderId, setSelectedOrderId] = useState('')
  const [selectedRobotId, setSelectedRobotId] = useState('')
  const [followSelectedRobot, setFollowSelectedRobot] = useState(false)
  const [showInventoryBoxes, setShowInventoryBoxes] = useState(false)
  const [focusRequest, setFocusRequest] = useState(0)
  const [pickupNotice, setPickupNotice] = useState<{
    id: string
    expectedSkuName: string
    actualSkuName: string
    location: string
    robotId: string
    verified: boolean
  } | null>(null)
  const seededOrdersRef = useRef(false)
  const observedPickedRef = useRef(new Map<string, number>())

  const topologiesQuery = useQuery({
    queryKey: ['topologies'],
    queryFn: api.listTopologies,
  })
  const catalogQuery = useQuery({
    queryKey: ['goods', 'catalog'],
    queryFn: api.getGoodsCatalog,
  })
  const socketConnected = useSimulationSocket(selectedRunId)

  const stateQuery = useQuery({
    queryKey: ['simulation-state', selectedRunId],
    queryFn: () => api.getSimulationState(selectedRunId),
    enabled: Boolean(selectedRunId),
    refetchInterval: selectedRunId && !socketConnected ? 1500 : false,
  })

  const metricsQuery = useQuery({
    queryKey: ['simulation-metrics', selectedRunId],
    queryFn: () => api.getSimulationMetrics(selectedRunId),
    enabled: Boolean(selectedRunId),
    refetchInterval: selectedRunId && !socketConnected ? 1500 : false,
  })

  const availableTopologies = topologiesQuery.data ?? []
  const requestedTopologyExists = requestedTopologyId
    ? availableTopologies.some((item) => item.id === requestedTopologyId)
    : false
  const resolvedTopologyId =
    (requestedTopologyExists ? requestedTopologyId : '') ||
    (selectedTopologyId && availableTopologies.some((item) => item.id === selectedTopologyId) ? selectedTopologyId : '') ||
    (!topologiesQuery.isLoading && !requestedTopologyId ? availableTopologies[0]?.id : '') ||
    ''
  const shuttleContractQuery = useQuery({
    queryKey: ['shuttle-contract', resolvedTopologyId],
    queryFn: () => api.getShuttleContract(resolvedTopologyId),
    enabled: Boolean(resolvedTopologyId),
    retry: false,
  })
  useEffect(() => {
    if (resolvedTopologyId && resolvedTopologyId !== selectedTopologyId) {
      setSelectedTopologyId(resolvedTopologyId)
    }
  }, [resolvedTopologyId, selectedTopologyId, setSelectedTopologyId])

  const candidateState = selectSimulationStateSource(liveState, stateQuery.data, socketConnected)
  const currentState = candidateState?.topologyId === resolvedTopologyId ? candidateState : undefined
  const currentMetrics = currentState
    ? (socketConnected ? (liveMetrics ?? metricsQuery.data) : (metricsQuery.data ?? liveMetrics))
    : undefined
  const topologyIdForBoard = currentState?.topologyId || resolvedTopologyId
  const currentTopology = availableTopologies.find((item) => item.id === resolvedTopologyId)

  useEffect(() => {
    if (candidateState?.topologyId && resolvedTopologyId && candidateState.topologyId !== resolvedTopologyId) {
      resetRun()
    }
  }, [candidateState?.topologyId, resetRun, resolvedTopologyId])

  useEffect(() => {
    if (!socketConnected && stateQuery.data) setLiveState(stateQuery.data)
  }, [setLiveState, socketConnected, stateQuery.data])

  useEffect(() => {
    observedPickedRef.current.clear()
    setPickupNotice(null)
  }, [selectedRunId])

  useEffect(() => {
    if (!currentState) return
    for (const task of currentState.tasks) {
      const previous = observedPickedRef.current.get(task.id) ?? 0
      if (task.picked > previous && task.orderId === selectedOrderId) {
        const pickedLine = task.lines[Math.max(0, task.picked - 1)]
        const assignedRobot = currentState.robots.find((robot) => robot.id === task.assignedRobotId)
        const actualSlot = assignedRobot?.carrying?.[Math.max(0, (assignedRobot.carrying?.length ?? 1) - 1)]
        setPickupNotice({
          id: `${task.id}-${task.picked}`,
          expectedSkuName: pickedLine?.skuName || pickedLine?.skuId || '指定货物',
          actualSkuName: actualSlot?.skuName || actualSlot?.skuId || '未检测到载货',
          location: pickedLine ? `${pickedLine.nodeId} · 第 ${pickedLine.layer} 层` : task.sourceCellId,
          robotId: task.assignedRobotId || 'CTU',
          verified: Boolean(actualSlot?.skuId && actualSlot.skuId === pickedLine?.skuId),
        })
      }
      observedPickedRef.current.set(task.id, task.picked)
    }
  }, [currentState, selectedOrderId])

  useEffect(() => {
    if (!pickupNotice) return
    const timer = window.setTimeout(() => setPickupNotice(null), 3200)
    return () => window.clearTimeout(timer)
  }, [pickupNotice])

  const topologyQuery = useQuery({
    queryKey: ['topology', topologyIdForBoard],
    queryFn: () => api.getTopology(topologyIdForBoard),
    enabled: Boolean(topologyIdForBoard),
  })
  const floors = topologyQuery.data?.payload.meta?.floors?.length ? topologyQuery.data.payload.meta.floors : ['1F']
  const topologyLoadError = topologiesQuery.isError
    ? '拓扑列表加载失败，请检查后端连接后重试。'
    : topologyQuery.isError
      ? '拓扑场景加载失败，请检查拓扑数据或后端连接后重试。'
      : ''

  async function retryTopologyLoad() {
    if (topologiesQuery.isError) {
      await topologiesQuery.refetch()
      return
    }
    await topologyQuery.refetch()
  }

  useEffect(() => {
    if (seededOrdersRef.current || !catalogQuery.data?.skus.length) return
    seededOrdersRef.current = true
    const demoOrders = createDemoOrders(catalogQuery.data.skus)
    setLocalOrders(demoOrders)
    setSelectedOrderId(demoOrders[0]?.id ?? '')
  }, [catalogQuery.data])

  const displayedOrders = useMemo(
    () => currentState?.orders?.length
      ? currentState.orders
      : localOrders.map((order) => ({
          ...order,
          status: 'ready' as const,
          statusLabel: '待启动',
          taskIds: [],
          totalTasks: 0,
          completedTasks: 0,
          progress: 0,
        })),
    [currentState?.orders, localOrders],
  )
  const selectedOrder = displayedOrders.find((order) => order.id === selectedOrderId) ?? displayedOrders[0]

  async function startRun() {
    if (starting) return
    setStartError('')
    if (!resolvedTopologyId) {
      setStartError('没有可用的拓扑，请先在拓扑编辑器中保存拓扑。')
      return
    }

    try {
      setStarting(true)
      if (!localOrders.length && !localTransfers.length) {
        setStartError('请先新建订单或加入跨层运输任务，再启动统一仿真。')
        return
      }
      resetRun()
      const result = await api.startSimulation(resolvedTopologyId, localOrders, localTransfers)
      if (!result.runId || result.status !== 'running') {
        throw new Error('后端未返回有效的运行实例。')
      }
      setSelectedRunId(result.runId)
      const nextState = await api.getSimulationState(result.runId)
      setLiveState(nextState)
      setLocalTransfers([])
      await queryClient.invalidateQueries({ queryKey: ['runs'] })
    } catch (error) {
      setStartError(error instanceof Error ? error.message : '启动仿真失败')
    } finally {
      setStarting(false)
    }
  }

  const pickingTask = currentState?.tasks.find((task) => task.phase === 'picking')

  async function completePicking() {
    if (!selectedRunId || !pickingTask || completingPicking) return
    try {
      setCompletingPicking(true)
      const nextState = await api.completeTaskPicking(selectedRunId, pickingTask.id)
      setLiveState(nextState)
    } finally {
      setCompletingPicking(false)
    }
  }

  async function syncRun(action: 'pause' | 'resume' | 'stop') {
    if (!selectedRunId) return
    setStartError('')
    try {
      if (action === 'pause') await api.pauseSimulation(selectedRunId)
      if (action === 'resume') await api.resumeSimulation(selectedRunId)
      if (action === 'stop') await api.stopSimulation(selectedRunId)

      const nextState = await api.getSimulationState(selectedRunId)
      setLiveState(nextState)
      await queryClient.invalidateQueries({ queryKey: ['runs'] })
    } catch (error) {
      setStartError(error instanceof Error ? error.message : '仿真控制失败')
    }
  }

  async function changeSpeed(multiplier: 1 | 2 | 4) {
    if (!selectedRunId) return
    setStartError('')
    try {
      await api.setSimulationSpeed(selectedRunId, multiplier)
      await refreshRunState()
    } catch (error) {
      setStartError(error instanceof Error ? error.message : '切换仿真倍速失败')
    }
  }

  async function refreshRunState() {
    if (!selectedRunId) return
    const nextState = await api.getSimulationState(selectedRunId)
    setLiveState(nextState)
  }

  async function resetRunState() {
    if (!selectedRunId) return
    await api.resetSimulation(selectedRunId)
    clearLiveEvents()
    await refreshRunState()
  }

  const sortedTasks = useMemo(() => [...(currentState?.tasks ?? [])].sort((left, right) => {
    const leftWeight = left.status === 'completed' ? 1 : 0
    const rightWeight = right.status === 'completed' ? 1 : 0
    return leftWeight - rightWeight || left.priority - right.priority
  }), [currentState?.tasks])

  const alerts = currentState?.alerts ?? []
  const robots = currentState?.robots ?? []
  const isRunning = currentState?.status === 'running'
  const sceneTasks = useMemo(() => workspaceTab === 'orders'
    ? (selectedOrder ? sortedTasks.filter((task) => task.orderId === selectedOrder.id) : sortedTasks)
    : sortedTasks.filter((task) => task.mode === 'shuttle_transfer'),
  [selectedOrder, sortedTasks, workspaceTab])
  const sceneGoods = useMemo(() => {
    const inventory = currentState?.inventory ?? []
    if (showInventoryBoxes) return inventory
    const taskLocations = new Set(
      sceneTasks.flatMap((task) => (task.lines ?? []).map((line) => `${line.nodeId}:${line.layer}`)),
    )
    return inventory.filter((slot) => taskLocations.has(`${slot.nodeId}:${slot.layer}`))
  }, [currentState?.inventory, sceneTasks, showInventoryBoxes])
  const selectedRobot = robots.find((robot) => robot.id === selectedRobotId)
  const focusTask = (selectedRobot?.currentTaskId
    ? sortedTasks.find((task) => task.id === selectedRobot.currentTaskId)
    : undefined)
    ?? sceneTasks.find((task) => task.status === 'assigned')
    ?? sceneTasks.find((task) => !['completed', 'cancelled', 'failed'].includes(task.status))
  const focusRobot = selectedRobot
    ?? (focusTask?.assignedRobotId ? robots.find((robot) => robot.id === focusTask.assignedRobotId) : undefined)
  const isPicking = focusTask?.phase === 'pickup_from_ctu' || focusTask?.phase === 'pickup_from_shuttle'
  const handlingLine = focusRobot?.handlingAction
    ? focusTask?.lines.find((line) => (
        line.nodeId === focusRobot.handlingAction?.nodeId
        && line.layer === focusRobot.handlingAction?.targetLayer
      ))
    : undefined
  const focusLine = isPicking
    ? handlingLine ?? focusTask?.lines?.[Math.min(focusTask.currentLineIndex, Math.max(0, focusTask.lines.length - 1))]
    : undefined
  const focusDestinationLabel = focusLine
    ? `${focusLine.nodeId} · 第 ${focusLine.layer} 层`
    : focusTask?.mode === 'shuttle_transfer'
      ? `${focusTask.destinationLocationId ?? focusTask.deliveryCellId} · L${focusTask.targetLayer}`
      : focusTask
        ? `出库交付点 · ${focusTask.deliveryCellId}`
        : ''
  const runStatusLabel = starting
    ? '启动中…'
    : currentState?.stopReason === 'runtime_error'
      ? '运行异常'
      : currentState?.status === 'stopped' && currentState.stopReason === 'safety_limit'
        ? '异常停止'
        : runStatusLabels[currentState?.status ?? 'idle'] ?? currentState?.status ?? '未启动'
  const safetyStopMessage = currentState?.status === 'stopped' && currentState.stopReason === 'safety_limit'
    ? '仿真达到安全运行上限，仍有任务未完成。请在运行详情中检查不可达任务、设备故障或持续交通阻塞。'
    : ''
  const runtimeErrorMessage = currentState?.stopReason === 'runtime_error'
    ? `仿真后台任务在 Tick ${currentState.runtimeError?.tick ?? currentState.tick} 异常停止：${currentState.runtimeError?.message ?? '未知错误'}`
    : ''
  const taskFailureMessage = currentState?.stopReason === 'task_failed' || currentState?.stopReason === 'transfer_failed'
    ? '仿真因任务失败停止。请在运行详情中检查任务失败原因与告警。'
    : ''
  const speedMultiplier = currentState?.speedMultiplier ?? 2

  return (
    <div className="simulation-workbench">
      <header className="simulation-command-bar">
        <div className="simulation-heading">
          <strong>仿真工作台</strong>
          <span>
            {currentTopology
              ? `${currentTopology.name}（${currentTopology.id}）`
              : '正在读取拓扑…'}
          </span>
        </div>

        <div className="simulation-run-summary" aria-label="仿真运行状态">
          <span><small>Run</small><strong>{selectedRunId || '未启动'}</strong></span>
          <span className={starting || isRunning ? 'simulation-status-live' : ''} aria-live="polite">
            <small>状态</small><strong>{runStatusLabel}</strong>
          </span>
          <span><small>Tick</small><strong>{currentState?.tick ?? 0}</strong></span>
          <span><small>事件</small><LiveEventCount persistedCount={currentState?.eventCount} /></span>
        </div>

        <div className="simulation-actions">
          <button className="ghost-button" onClick={() => navigate('/topology')} type="button">
            <ArrowLeft size={16} />
            返回编辑
          </button>
          <button className="ghost-button simulation-new-order-command" onClick={() => setOrderComposerOpen(true)} disabled={isRunning || starting} type="button">
            <Plus size={16} />
            新建订单
          </button>
          {pickingTask ? (
            <button className="ghost-button" onClick={() => void completePicking()} disabled={completingPicking} type="button">
              <CheckCircle2 size={16} />
              完成拣货
            </button>
          ) : null}
          <div className="simulation-speed-control" role="group" aria-label="仿真运行倍速">
            {([1, 2, 4] as const).map((multiplier) => (
              <button
                key={multiplier}
                className={speedMultiplier === multiplier ? 'active' : ''}
                type="button"
                aria-pressed={speedMultiplier === multiplier}
                disabled={!selectedRunId || ['stopped', 'completed'].includes(currentState?.status ?? '')}
                onClick={() => void changeSpeed(multiplier)}
              >
                {multiplier}×
              </button>
            ))}
          </div>
          <button className="accent-button" onClick={startRun} disabled={!resolvedTopologyId || starting || isRunning} type="button">
            {starting ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
            {starting ? '启动中…' : isRunning ? '运行中' : '启动'}
          </button>
          <button className="ghost-button" onClick={() => void syncRun('pause')} disabled={!selectedRunId || currentState?.status !== 'running'} type="button">
            <Pause size={16} />
            暂停
          </button>
          <button className="ghost-button" onClick={() => void syncRun('resume')} disabled={!selectedRunId || currentState?.status !== 'paused'} type="button">
            <Play size={16} />
            继续
          </button>
          <button className="danger-button" onClick={() => void syncRun('stop')} disabled={!selectedRunId || ['stopped', 'completed'].includes(currentState?.status ?? '')} type="button">
            <Square size={16} />
            停止
          </button>
          <button className="ghost-button" onClick={() => void resetRunState()} disabled={!selectedRunId} type="button">
            <RotateCcw size={16} />
            重置
          </button>
          <button
            className={`ghost-button simulation-details-button ${detailsOpen ? 'simulation-details-button-active' : ''}`}
            onClick={() => setDetailsOpen((open) => !open)}
            type="button"
            aria-expanded={detailsOpen}
          >
            <PanelRightOpen size={16} />
            运行详情
            {alerts.length ? <b>{alerts.length}</b> : null}
          </button>
        </div>
      </header>

      <main className={`simulation-scene-stage ${workspaceTab === 'orders' && ordersCollapsed ? 'simulation-orders-collapsed' : 'simulation-orders-expanded'}`}>
        <div className="simulation-workspace-tabs" role="tablist" aria-label="工作台功能">
          <button className={workspaceTab === 'orders' ? 'active' : ''} onClick={() => setWorkspaceTab('orders')} type="button">订单任务</button>
          <button className={workspaceTab === 'shuttle' ? 'active' : ''} onClick={() => setWorkspaceTab('shuttle')} type="button">四向车搬运</button>
          <button className={workspaceTab === 'transfer' ? 'active' : ''} onClick={() => setWorkspaceTab('transfer')} type="button">跨层运输</button>
          <button className={workspaceTab === 'inventory' ? 'active' : ''} onClick={() => setWorkspaceTab('inventory')} type="button">库存详情</button>
        </div>
        {workspaceTab === 'orders' ? (
          <OrderRail
            orders={displayedOrders}
            selectedOrderId={selectedOrder?.id ?? ''}
            collapsed={ordersCollapsed}
            onSelect={setSelectedOrderId}
            onToggle={() => setOrdersCollapsed((collapsed) => !collapsed)}
            onCreate={() => setOrderComposerOpen(true)}
          />
        ) : workspaceTab === 'shuttle' ? (
          <ShuttleDeliveryPanel
            runId={selectedRunId}
            dockNodes={
              topologyQuery.data?.payload.nodes.filter((node) => node.type === 'shuttle_dock') ?? []
            }
            runDocks={currentState?.shuttleDocks ?? []}
            tasks={sortedTasks}
            robots={robots}
            onChanged={() => {
              void refreshRunState()
            }}
          />
        ) : workspaceTab === 'transfer' ? (
          <CrossLevelTransferPanel
            topologyId={resolvedTopologyId}
            contract={shuttleContractQuery.data}
            pendingTransfers={localTransfers}
            simulation={currentState}
            selectedRunId={selectedRunId}
            onAdd={(transfer) => setLocalTransfers((items) => [...items, transfer])}
            onRemove={(id) => setLocalTransfers((items) => items.filter((item) => item.id !== id))}
            onFocusVehicle={(id) => {
              setSelectedRobotId(id)
              setFollowSelectedRobot(true)
              setFocusRequest((request) => request + 1)
            }}
            onRuntimeChanged={refreshRunState}
          />
        ) : (
          <TransferInventoryPanel simulation={currentState} />
        )}
        {startError || safetyStopMessage || runtimeErrorMessage || taskFailureMessage || (!topologiesQuery.isLoading && requestedTopologyId && !requestedTopologyExists) ? (
          <div className="simulation-message-stack">
            {startError ? <p className="form-error">{startError}</p> : null}
            {safetyStopMessage ? <p className="form-error">{safetyStopMessage}</p> : null}
            {runtimeErrorMessage ? <p className="form-error" role="alert">{runtimeErrorMessage}</p> : null}
            {taskFailureMessage ? <p className="form-error" role="alert">{taskFailureMessage}</p> : null}
            {!topologiesQuery.isLoading && requestedTopologyId && !requestedTopologyExists ? (
              <p className="form-error">指定的拓扑不存在或尚未保存，请返回拓扑编辑器重新保存。</p>
            ) : null}
          </div>
        ) : null}

        {topologyQuery.data?.payload ? (
          <>
            <div className="topology-floor-switcher simulation-floor-switcher" aria-label="simulation-floor-switcher">
              {floors.map((floor) => (
                <button
                  key={floor}
                  className={`ghost-button topology-floor-chip ${activeFloor === floor ? 'topology-floor-chip-active' : ''}`}
                  type="button"
                  onClick={() => setActiveFloor(floor)}
                >
                  {floor}
                </button>
              ))}
              <button
                className={`ghost-button topology-floor-chip ${showInventoryBoxes ? 'topology-floor-chip-active' : ''}`}
                type="button"
                aria-pressed={showInventoryBoxes}
                onClick={() => setShowInventoryBoxes((visible) => !visible)}
              >
                彩色库存
              </button>
            </div>

            {focusTask ? (
              <div className="simulation-task-focus-card" aria-label="当前聚焦任务">
                <div className="simulation-task-focus-title">
                  <span>当前聚焦任务</span>
                  <div>
                    <b>{focusRobot?.phaseLabel || focusTask.phaseLabel || '等待调度'}</b>
                    <button type="button" onClick={() => setFocusRequest((request) => request + 1)}>
                      <LocateFixed size={12} />定位目标
                    </button>
                    {focusRobot ? (
                      <button type="button" onClick={() => {
                        setSelectedRobotId(focusRobot.id)
                        setFollowSelectedRobot((value) => !value)
                      }}>
                        {followSelectedRobot ? '停止跟随' : '相机跟随'}
                      </button>
                    ) : null}
                  </div>
                </div>
                <div className="simulation-task-focus-main">
                  <strong>{focusRobot?.id || '等待分配机器人'}</strong>
                  <i>→</i>
                  <strong>{focusDestinationLabel}</strong>
                </div>
                <div className="simulation-task-focus-goods">
                  <span>{focusLine?.skuName || focusLine?.skuId || '前往出库交付点'}</span>
                  <small>{focusRobot ? `车载 ${focusRobot.load}/${focusRobot.capacity} 箱` : '等待设备接单'}</small>
                </div>
              </div>
            ) : null}

            {pickupNotice ? (
              <div className={`simulation-pickup-notice ${pickupNotice.verified ? '' : 'simulation-pickup-notice-error'}`} role="status">
                <CheckCircle2 size={19} />
                <div>
                  <strong>{pickupNotice.verified ? '取货成功，SKU 校验通过' : '取货异常，SKU 校验不一致'} · {pickupNotice.robotId}</strong>
                  <span>预期：{pickupNotice.expectedSkuName}</span>
                  <small>实际：{pickupNotice.actualSkuName}　来源：{pickupNotice.location}</small>
                </div>
              </div>
            ) : null}

            <div className="simulation-3d-viewport simulation-main-viewport">
              <SimulationSceneBoundary key={topologyQuery.data.payload.id}>
                <Suspense
                  fallback={
                    <div className="simulation-3d-loading">
                      <Loader2 className="spin" size={26} />
                      <p className="muted-copy">正在装配 3D 场景…</p>
                    </div>
                  }
                >
                  <Simulation3DScene
                    topology={topologyQuery.data.payload}
                    goods={sceneGoods}
                    robots={robots}
                    tasks={sceneTasks}
                    activeFloor={activeFloor}
                    focusRequest={focusRequest}
                    transferInventory={currentState?.transferInventory ?? []}
                    shuttleLifts={shuttleContractQuery.data?.lifts ?? []}
                    selectedRobotId={selectedRobotId}
                    followSelectedRobot={followSelectedRobot}
                    onSelectRobot={(id) => setSelectedRobotId(id)}
                    paused={currentState?.status === 'paused'}
                    transitionDuration={currentState?.tickIntervalSeconds ?? 0.3}
                  />
                </Suspense>
              </SimulationSceneBoundary>
            </div>
            {workspaceTab === 'orders' ? <OrderTimeline order={selectedOrder} /> : null}
          </>
        ) : topologyLoadError ? (
          <div className="simulation-empty-state" role="alert">
            <p className="form-error">{topologyLoadError}</p>
            <button className="ghost-button" type="button" onClick={() => void retryTopologyLoad()}>
              重试加载拓扑
            </button>
          </div>
        ) : !topologiesQuery.isLoading && !resolvedTopologyId ? (
          <div className="simulation-empty-state" role="alert">
            <p className="form-error">没有可用的拓扑场景，请返回拓扑编辑器保存后再试。</p>
            <button className="ghost-button" type="button" onClick={() => navigate('/topology')}>
              返回拓扑编辑器
            </button>
          </div>
        ) : (
          <div className="simulation-empty-state">
            <Loader2 className="spin" size={24} />
            <p>正在读取拓扑场景…</p>
          </div>
        )}

        {detailsOpen ? (
        <aside className="simulation-detail-drawer simulation-detail-drawer-open">
          <div className="simulation-detail-header">
            <div>
              <strong>运行详情</strong>
              <span>需要排查时再打开，不占用主场景空间</span>
            </div>
            <button type="button" onClick={() => setDetailsOpen(false)} aria-label="关闭运行详情">
              <X size={18} />
            </button>
          </div>

          <div className="simulation-detail-tabs" role="tablist" aria-label="运行详情分类">
            <button className={detailTab === 'metrics' ? 'active' : ''} onClick={() => setDetailTab('metrics')} type="button">
              <Gauge size={15} />指标
            </button>
            <button className={detailTab === 'devices' ? 'active' : ''} onClick={() => setDetailTab('devices')} type="button">
              <Bot size={15} />设备 <b>{robots.length}</b>
            </button>
            <button className={detailTab === 'tasks' ? 'active' : ''} onClick={() => setDetailTab('tasks')} type="button">
              <ClipboardList size={15} />任务 <b>{sortedTasks.length}</b>
            </button>
            <button className={detailTab === 'alerts' ? 'active' : ''} onClick={() => setDetailTab('alerts')} type="button">
              <Bell size={15} />告警 <b>{alerts.length}</b>
            </button>
          </div>

          <div className="simulation-detail-body">
            {detailTab === 'metrics' ? (
              <section className="simulation-detail-section">
                <h3><Activity size={17} />实时指标</h3>
                <KpiGrid metrics={currentMetrics} />
              </section>
            ) : null}

            {detailTab === 'devices' ? (
              <section className="simulation-detail-section">
                <h3><Bot size={17} />设备状态</h3>
                {robots.length ? (
                  <div className="table-wrap">
                    <table>
                      <thead><tr><th>设备</th><th>类型</th><th>载箱</th><th>当前位置</th><th>状态</th><th>阶段</th><th>等待</th></tr></thead>
                      <tbody>
                        {robots.map((robot) => (
                          <tr key={robot.id}>
                            <td>{robot.id}</td>
                            <td>{robotTypeLabels[robot.type]}</td>
                            <td>{robot.load} / {robot.capacity}</td>
                            <td>{robot.currentCellId}<br /><span className="muted-copy">({robot.currentRow}, {robot.currentCol})</span></td>
                            <td>{robot.status}</td>
                            <td>{robot.phaseLabel}</td>
                            <td>{robot.waitTicks}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <p className="muted-copy">仿真启动后显示设备状态。</p>}
              </section>
            ) : null}

            {detailTab === 'tasks' ? (
              <section className="simulation-detail-section">
                <h3><ClipboardList size={17} />任务阶段</h3>
                {sortedTasks.length ? (
                  <div className="table-wrap">
                    <table>
                      <thead><tr><th>任务</th><th>模式</th><th>状态</th><th>阶段</th><th>源格</th><th>目标格</th><th>设备</th></tr></thead>
                      <tbody>
                        {sortedTasks.map((task) => (
                          <tr key={task.id}>
                            <td>{task.id}</td>
                            <td>{taskModeLabels[task.mode]}</td>
                            <td>{task.status}</td>
                            <td>{task.phaseLabel}</td>
                            <td>{task.sourceCellId}<br /><span className="muted-copy">{task.lines?.length ?? 1} 项 · 已取 {task.picked ?? 0}</span></td>
                            <td>{task.targetCellId}</td>
                            <td>{task.assignedRobotId ?? '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <p className="muted-copy">仿真启动后显示任务进度。</p>}
              </section>
            ) : null}

            {detailTab === 'alerts' ? (
              <section className="simulation-detail-section">
                <h3><Bell size={17} />告警与解释</h3>
                <div className="alert-stack">
                  {alerts.length ? alerts.map((alert, index) => (
                    <div key={`${alert.tick}-${index}`} className={alertClassName(alert.level)}>
                      <Zap size={16} />
                      <div>
                        <strong>{alert.category}</strong>
                        <p>{alert.message}</p>
                        <p className="muted-copy">建议：{alert.suggestion}</p>
                      </div>
                    </div>
                  )) : <p className="muted-copy">当前没有告警。</p>}
                </div>
              </section>
            ) : null}
          </div>
        </aside>
        ) : null}
        <OrderComposer
          open={orderComposerOpen}
          catalog={catalogQuery.data?.skus ?? []}
          catalogError={catalogQuery.isError
            ? (catalogQuery.error instanceof Error ? catalogQuery.error.message : '未知错误')
            : ''}
          onRetryCatalog={() => { void catalogQuery.refetch() }}
          onClose={() => setOrderComposerOpen(false)}
          onSubmit={(order) => {
            setLocalOrders((orders) => [order, ...orders])
            setSelectedOrderId(order.id)
            setOrderComposerOpen(false)
          }}
        />
      </main>
    </div>
  )
}
