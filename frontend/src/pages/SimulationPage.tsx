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
  RotateCcw,
  Square,
  X,
  Zap,
} from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { KpiGrid } from '../components/KpiGrid'
import {
  OrderComposer,
  OrderOrchestrationPanel,
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
import { findLayoutPreviewItem } from '../lib/transportLayout'
import { usePlatformStore } from '../store/usePlatformStore'
import type { ShuttleTransferInput, SimulationAlert, SimulationOrder, SimulationOrderInput } from '../types'

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
type RunControlAction = 'pause' | 'resume' | 'stop' | 'reset'

const runControlProgressLabels: Record<RunControlAction, string> = {
  pause: '暂停中…',
  resume: '继续中…',
  stop: '停止中…',
  reset: '重置中…',
}

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
  const [controlAction, setControlAction] = useState<RunControlAction | null>(null)
  const [controlNotice, setControlNotice] = useState('')
  const [completingPicking, setCompletingPicking] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [detailTab, setDetailTab] = useState<DetailTab>('metrics')
  const [orderComposerOpen, setOrderComposerOpen] = useState(false)
  const [ordersCollapsed, setOrdersCollapsed] = useState(false)
  const [localOrders, setLocalOrders] = useState<SimulationOrderInput[]>([])
  const [removingOrderId, setRemovingOrderId] = useState('')
  const [localTransfers, setLocalTransfers] = useState<ShuttleTransferInput[]>([])
  const [workspaceTab, setWorkspaceTab] = useState<WorkspaceTab>('orders')
  const [selectedOrderId, setSelectedOrderId] = useState('')
  const [selectedRobotId, setSelectedRobotId] = useState('')
  const [followSelectedRobot, setFollowSelectedRobot] = useState(false)
  const [showInventoryBoxes, setShowInventoryBoxes] = useState(false)
  const [showLayoutPreview, setShowLayoutPreview] = useState(false)
  const [showPickingArmPreview, setShowPickingArmPreview] = useState(false)
  const [pickingArmPreviewCycle, setPickingArmPreviewCycle] = useState(0)
  const [pickingArmResetRequest, setPickingArmResetRequest] = useState(0)
  const [selectedLayoutOptionId, setSelectedLayoutOptionId] = useState('')
  const [selectedLayoutItemId, setSelectedLayoutItemId] = useState('')
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
  const transportLayoutQuery = useQuery({
    queryKey: ['transport-layout-plan', resolvedTopologyId],
    queryFn: () => api.getTransportLayoutPlan(resolvedTopologyId),
    enabled: Boolean(resolvedTopologyId),
    retry: false,
  })
  useEffect(() => {
    const plan = transportLayoutQuery.data
    if (!plan) return
    setSelectedLayoutOptionId(plan.recommendedOptionId)
    setSelectedLayoutItemId('')
  }, [transportLayoutQuery.data])
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
  const selectedLayoutItem = transportLayoutQuery.data && selectedLayoutItemId
    ? findLayoutPreviewItem(transportLayoutQuery.data, selectedLayoutItemId)
    : undefined

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
    setFollowSelectedRobot(false)
  }, [selectedRunId])

  useEffect(() => {
    if (!controlNotice) return
    const timer = window.setTimeout(() => setControlNotice(''), 4200)
    return () => window.clearTimeout(timer)
  }, [controlNotice])

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
    if (seededOrdersRef.current || selectedRunId || !catalogQuery.data?.skus.length) return
    seededOrdersRef.current = true
    const demoOrders = createDemoOrders(catalogQuery.data.skus)
    setLocalOrders(demoOrders)
    setSelectedOrderId(demoOrders[0]?.id ?? '')
  }, [catalogQuery.data, selectedRunId])

  const displayedOrders = useMemo(() => {
    const runtimeOrders = currentState?.orders ?? []
    const runtimeOrderIds = new Set(runtimeOrders.map((order) => order.id))
    const draftOrders = localOrders
      .filter((order) => !runtimeOrderIds.has(order.id))
      .map((order) => ({
          ...order,
          status: 'ready' as const,
          statusLabel: '待启动',
          taskIds: [],
          totalTasks: 0,
          completedTasks: 0,
          progress: 0,
        }))
    return [...draftOrders, ...runtimeOrders]
  }, [currentState?.orders, localOrders])
  const selectedOrder = displayedOrders.find((order) => order.id === selectedOrderId) ?? displayedOrders[0]

  async function startRun() {
    if (starting) return
    setStartError('')
    setControlNotice('')
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
      setLocalOrders([])
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
    if (!selectedRunId || controlAction) return
    setStartError('')
    setControlNotice('')
    setControlAction(action)
    try {
      if (action === 'pause') await api.pauseSimulation(selectedRunId)
      if (action === 'resume') await api.resumeSimulation(selectedRunId)
      if (action === 'stop') await api.stopSimulation(selectedRunId)

      const nextState = await api.getSimulationState(selectedRunId)
      setLiveState(nextState)
      setControlNotice(action === 'pause'
        ? '仿真已暂停。'
        : action === 'resume'
          ? '仿真已继续运行。'
          : '仿真已停止；点击“重置”可恢复到初始暂停状态。')
      await queryClient.invalidateQueries({ queryKey: ['runs'] })
    } catch (error) {
      setStartError(error instanceof Error ? error.message : '仿真控制失败')
    } finally {
      setControlAction(null)
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
    setPickingArmResetRequest((request) => request + 1)
    if (!selectedRunId || controlAction) return
    setStartError('')
    setControlNotice('')
    setControlAction('reset')
    try {
      await api.resetSimulation(selectedRunId)
      clearLiveEvents()
      await refreshRunState()
      setControlNotice('仿真已重置到 Tick 0，并保持暂停；点击“继续”重新运行。')
    } catch (error) {
      setStartError(error instanceof Error ? error.message : '重置仿真失败')
    } finally {
      setControlAction(null)
    }
  }

  async function removeOrder(order: SimulationOrderInput & Partial<SimulationOrder>) {
    if (removingOrderId) return
    const status = order.status ?? 'ready'
    if (status === 'ready') {
      if (!window.confirm(`确定删除待启动订单 ${order.id} 吗？`)) return
      const remaining = displayedOrders.filter((item) => item.id !== order.id)
      setLocalOrders((orders) => orders.filter((item) => item.id !== order.id))
      if (selectedOrderId === order.id) setSelectedOrderId(remaining[0]?.id ?? '')
      setControlNotice(`待启动订单 ${order.id} 已删除。`)
      return
    }
    if (!selectedRunId || !currentState || !['running', 'paused'].includes(currentState.status)) return
    const relatedTasks = currentState.tasks.filter((task) => task.orderId === order.id)
    const hasPickedGoods = relatedTasks.some((task) => task.picked > 0 || Boolean(task.deliveredGoods?.length))
    const message = hasPickedGoods
      ? `订单 ${order.id} 已有 CTU 取货。取消后系统会先将货箱送回原库位，是否继续？`
      : `确定取消订单 ${order.id} 吗？尚未取货的任务会立即释放。`
    if (!window.confirm(message)) return
    try {
      setRemovingOrderId(order.id)
      const nextState = await api.cancelSimulationOrder(selectedRunId, order.id)
      setLiveState(nextState)
      setControlNotice(hasPickedGoods
        ? `订单 ${order.id} 已进入取消回库流程。`
        : `订单 ${order.id} 已取消。`)
    } catch (error) {
      setStartError(error instanceof Error ? error.message : '取消订单失败')
    } finally {
      setRemovingOrderId('')
    }
  }

  const sortedTasks = useMemo(() => [...(currentState?.tasks ?? [])].sort((left, right) => {
    const leftWeight = left.status === 'completed' ? 1 : 0
    const rightWeight = right.status === 'completed' ? 1 : 0
    return leftWeight - rightWeight || left.priority - right.priority
  }), [currentState?.tasks])

  const alerts = currentState?.alerts ?? []
  const trafficMonitor = currentState?.trafficMonitor
  const robots = currentState?.robots ?? []
  const isRunning = currentState?.status === 'running'
  const isRunActive = ['running', 'paused'].includes(currentState?.status ?? '')
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
        ? `${focusTask.assignedPortLabel ?? '出库交付点'} · ${focusTask.deliveryCellId}`
        : ''
  const runStatusLabel = starting
    ? '启动中…'
    : controlAction
      ? runControlProgressLabels[controlAction]
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
          <button className="accent-button" onClick={startRun} disabled={!resolvedTopologyId || starting || isRunActive} type="button">
            {starting ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
            {starting ? '启动中…' : isRunning ? '运行中' : '启动'}
          </button>
          <button className="ghost-button" onClick={() => void syncRun('pause')} disabled={!selectedRunId || currentState?.status !== 'running' || Boolean(controlAction)} type="button">
            {controlAction === 'pause' ? <Loader2 className="spin" size={16} /> : <Pause size={16} />}
            {controlAction === 'pause' ? '暂停中…' : '暂停'}
          </button>
          <button className="ghost-button" onClick={() => void syncRun('resume')} disabled={!selectedRunId || currentState?.status !== 'paused' || Boolean(controlAction)} type="button">
            {controlAction === 'resume' ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
            {controlAction === 'resume' ? '继续中…' : '继续'}
          </button>
          <button className="danger-button" onClick={() => void syncRun('stop')} disabled={!selectedRunId || ['stopped', 'completed'].includes(currentState?.status ?? '') || Boolean(controlAction)} type="button">
            {controlAction === 'stop' ? <Loader2 className="spin" size={16} /> : <Square size={16} />}
            {controlAction === 'stop' ? '停止中…' : '停止'}
          </button>
          <button className="ghost-button" onClick={() => void resetRunState()} disabled={(!selectedRunId || Boolean(controlAction)) && !showPickingArmPreview} type="button">
            {controlAction === 'reset' ? <Loader2 className="spin" size={16} /> : <RotateCcw size={16} />}
            {controlAction === 'reset' ? '重置中…' : '重置'}
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
          <button className={workspaceTab === 'orders' ? 'active' : ''} onClick={() => setWorkspaceTab('orders')} type="button">订单调度</button>
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
            onRemove={(order) => { void removeOrder(order) }}
            removingOrderId={removingOrderId}
            runStatus={currentState?.status}
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
              setFollowSelectedRobot(false)
              setFocusRequest((request) => request + 1)
            }}
            onRuntimeChanged={refreshRunState}
          />
        ) : (
          <TransferInventoryPanel simulation={currentState} />
        )}
        {startError || controlNotice || safetyStopMessage || runtimeErrorMessage || taskFailureMessage || (!topologiesQuery.isLoading && requestedTopologyId && !requestedTopologyExists) ? (
          <div className="simulation-message-stack">
            {startError ? <p className="form-error">{startError}</p> : null}
            {controlNotice ? <p className="simulation-control-notice" role="status">{controlNotice}</p> : null}
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
              <button
                className={`ghost-button topology-floor-chip ${showLayoutPreview ? 'topology-floor-chip-active' : ''}`}
                type="button"
                aria-label="接驳布局预览"
                aria-pressed={showLayoutPreview}
                disabled={!transportLayoutQuery.data}
                title={transportLayoutQuery.isError ? '当前拓扑没有兼容的接驳方案，或 Revision 不匹配' : '显示独立 proposed 布局，不修改正式拓扑'}
                onClick={() => setShowLayoutPreview((visible) => !visible)}
              >
                接驳布局
              </button>
              <button
                className={`ghost-button topology-floor-chip ${showPickingArmPreview ? 'topology-floor-chip-active' : ''}`}
                type="button"
                aria-label="人工拣选出口机械臂预览"
                aria-pressed={showPickingArmPreview}
                disabled={!transportLayoutQuery.data?.pickingArm}
                title="独立视觉预览；不会扣库存、累计交付或完成订单"
                onClick={() => {
                  setShowPickingArmPreview((visible) => {
                    if (!visible) setPickingArmPreviewCycle((cycle) => cycle + 1)
                    return !visible
                  })
                }}
              >
                机械臂预览
              </button>
            </div>

            {showLayoutPreview && transportLayoutQuery.data ? (
              <aside className="simulation-layout-preview-panel" aria-label="接驳布局预览面板">
                <header>
                  <div>
                    <strong>接驳布局预览</strong>
                    <span>{transportLayoutQuery.data.planId}</span>
                  </div>
                  <b className={`layout-status layout-status-${transportLayoutQuery.data.overallStatus.toLowerCase()}`}>
                    {transportLayoutQuery.data.overallStatus}
                  </b>
                </header>
                <p>仅显示 proposed 施工方案；不会写入拓扑、库存或仿真状态。</p>
                <div className="simulation-layout-options" role="group" aria-label="接驳方案">
                  {transportLayoutQuery.data.options.map((option) => (
                    <button
                      key={option.optionId}
                      type="button"
                      className={selectedLayoutOptionId === option.optionId ? 'active' : ''}
                      onClick={() => {
                        setSelectedLayoutOptionId(option.optionId)
                        setSelectedLayoutItemId('')
                      }}
                    >
                      <span>{option.label}</span>
                      <small>{option.status}</small>
                    </button>
                  ))}
                </div>
                <div className="simulation-layout-legend" aria-label="预览图例">
                  <span><i className="legend-left" />左侧 CTU</span>
                  <span><i className="legend-right" />右侧 Shuttle</span>
                  <span><i className="legend-verified" />真实已验证</span>
                  <span><i className="legend-proposed" />拟新增</span>
                  <span><i className="legend-blocked" />阻断/冲突</span>
                  <span><i className="legend-merge" />Merge/Outbound</span>
                </div>
                {selectedLayoutItem ? (
                  <section className="simulation-layout-selection" aria-label="候选设施详情">
                    <strong>
                      {'facilityId' in selectedLayoutItem
                        ? selectedLayoutItem.facilityId
                        : 'referenceId' in selectedLayoutItem
                          ? selectedLayoutItem.referenceId
                          : selectedLayoutItem.connectionId}
                    </strong>
                    <span>
                      {'position' in selectedLayoutItem
                        ? `r${selectedLayoutItem.position.row}, c${selectedLayoutItem.position.col}, L${selectedLayoutItem.position.level}`
                        : `${selectedLayoutItem.direction} · ${selectedLayoutItem.edgeType}`}
                    </span>
                    {'purpose' in selectedLayoutItem ? <p>{selectedLayoutItem.purpose}</p> : null}
                    {'handoffAction' in selectedLayoutItem && selectedLayoutItem.handoffAction
                      ? <p>{selectedLayoutItem.handoffAction}</p>
                      : null}
                    {'conflicts' in selectedLayoutItem && selectedLayoutItem.conflicts.length ? (
                      <ul>{selectedLayoutItem.conflicts.map((conflict) => <li key={conflict}>{conflict}</li>)}</ul>
                    ) : null}
                  </section>
                ) : (
                  <small className="simulation-layout-hint">点击场景中的候选设施、路径或绿色真实节点查看详情。</small>
                )}
              </aside>
            ) : null}

            {showPickingArmPreview && transportLayoutQuery.data?.pickingArm ? (
              <aside className="simulation-arm-preview-panel" aria-label="人工拣选出口机械臂预览面板">
                <header>
                  <div>
                    <strong>人工拣选出口机械臂</strong>
                    <span>{transportLayoutQuery.data.pickingArm.armId}</span>
                  </div>
                  <b className="layout-status layout-status-partial">PREVIEW</b>
                </header>
                <p>搬运已拣订单周转箱；动画结束不会更改库存、订单交付数或订单状态。</p>
                <dl>
                  <div><dt>安装</dt><dd>r{transportLayoutQuery.data.pickingArm.installation.row},c{transportLayoutQuery.data.pickingArm.installation.col},L{transportLayoutQuery.data.pickingArm.installation.level}</dd></div>
                  <div><dt>取货</dt><dd>{transportLayoutQuery.data.pickingArm.pickup.topologyCellId} · ground</dd></div>
                  <div><dt>放货</dt><dd>{transportLayoutQuery.data.pickingArm.dropoff.topologyCellId} · L{transportLayoutQuery.data.pickingArm.dropoff.position.level}</dd></div>
                  <div><dt>方向</dt><dd>放货后沿 row86 向右 · 衔接段 r86,c51…c59 已删除，待重绘</dd></div>
                  <div><dt>终点</dt><dd>{transportLayoutQuery.data.pickingArm.previewEndpointId} · 预览路径终点</dd></div>
                  <div><dt>路径</dt><dd>{transportLayoutQuery.data.pickingArm.previewConveyorPath.length} 个真实 Conveyor 节点</dd></div>
                </dl>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => setPickingArmPreviewCycle((cycle) => cycle + 1)}
                >
                  重新装载预览周转箱
                </button>
                <small>暂停、继续、1×/2×/4× 与重置复用页面上方 Run 控制。</small>
              </aside>
            ) : null}

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
                    layoutPlan={transportLayoutQuery.data}
                    layoutPreviewVisible={showLayoutPreview}
                    layoutOptionId={selectedLayoutOptionId}
                    selectedLayoutItemId={selectedLayoutItemId}
                    onSelectLayoutItem={setSelectedLayoutItemId}
                    pickingArmPreviewVisible={showPickingArmPreview}
                    pickingArmPreviewCycleToken={`arm-preview-${pickingArmPreviewCycle}`}
                    pickingArmResetRequest={pickingArmResetRequest}
                    speedMultiplier={speedMultiplier}
                  />
                </Suspense>
              </SimulationSceneBoundary>
            </div>
            {workspaceTab === 'orders' ? (
              <>
                <OrderOrchestrationPanel
                  order={selectedOrder}
                  tasks={sceneTasks}
                  onFocusRobot={(robotId) => {
                    setSelectedRobotId(robotId)
                    setFollowSelectedRobot(false)
                    setFocusRequest((request) => request + 1)
                  }}
                />
                <OrderTimeline order={selectedOrder} />
              </>
            ) : null}
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
                      <thead><tr><th>设备</th><th>类型</th><th>载箱</th><th>当前位置</th><th>状态</th><th>阶段</th><th>等待/阻塞者</th></tr></thead>
                      <tbody>
                        {robots.map((robot) => (
                          <tr key={robot.id}>
                            <td>{robot.id}</td>
                            <td>{robotTypeLabels[robot.type]}</td>
                            <td>{robot.load} / {robot.capacity}</td>
                            <td>{robot.currentCellId}<br /><span className="muted-copy">({robot.currentRow}, {robot.currentCol})</span></td>
                            <td>{robot.status}</td>
                            <td>{robot.phaseLabel}</td>
                            <td>{robot.waitTicks}{robot.blockedByRobotId ? <><br /><span className="muted-copy">→ {robot.blockedByRobotId}</span></> : null}</td>
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
                      <thead><tr><th>任务</th><th>模式</th><th>状态</th><th>阶段</th><th>源格</th><th>出库口</th><th>设备</th></tr></thead>
                      <tbody>
                        {sortedTasks.map((task) => (
                          <tr key={task.id}>
                            <td>{task.id}</td>
                            <td>{taskModeLabels[task.mode]}</td>
                            <td>{task.status}</td>
                            <td>{task.phaseLabel}</td>
                            <td>{task.sourceCellId}<br /><span className="muted-copy">{task.lines?.length ?? 1} 项 · 已取 {task.picked ?? 0}</span></td>
                            <td>{task.assignedPortLabel ?? task.targetCellId}<br /><span className="muted-copy">{task.deliveryCellId}{task.portQueuePosition ? ` · 队列 ${task.portQueuePosition}` : ''}</span></td>
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
                {trafficMonitor?.suspectedDeadlocks.length ? (
                  <div className="simulation-deadlock-stack" aria-label="死锁检测与恢复">
                    {trafficMonitor.suspectedDeadlocks.map((group) => (
                      <div className={`simulation-deadlock-card ${group.level}`} key={group.id}>
                        <strong>{group.type === 'cyclic_wait' ? '循环等待' : '交通活锁'} · {group.level === 'confirmed' ? '已确认' : '疑似'}</strong>
                        <p>{group.robotIds.join(' → ')}</p>
                        <small>{group.reason} · 持续 {group.consecutiveTicks} Tick</small>
                        {group.priorityRobotId ? <p>优先通行：{group.priorityRobotId}</p> : null}
                        {group.actions?.length ? (
                          <ul>
                            {group.actions.map((action, index) => (
                              <li key={`${action.robotId}-${index}`}>{action.robotId}：{action.action}{action.cellId ? ` → ${action.cellId}` : ''}</li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
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
          onSubmit={async (order) => {
            if (selectedRunId && !currentState) {
              throw new Error('正在同步仿真状态，请稍后再提交订单。')
            }
            if (selectedRunId && currentState && ['running', 'paused'].includes(currentState.status)) {
              const nextState = await api.insertSimulationOrder(selectedRunId, order)
              setLiveState(nextState)
              setSelectedOrderId(order.id)
              setOrderComposerOpen(false)
              setControlNotice(`订单 ${order.id} 已插入当前仿真并进入调度。`)
              return
            }
            setLocalOrders((orders) => [order, ...orders])
            setSelectedOrderId(order.id)
            setOrderComposerOpen(false)
          }}
        />
      </main>
    </div>
  )
}
