import { Suspense, lazy, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  ArrowLeft,
  Bell,
  Bot,
  ClipboardList,
  Gauge,
  Loader2,
  PanelRightOpen,
  Pause,
  Play,
  Square,
  X,
  Zap,
} from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { KpiGrid } from '../components/KpiGrid'
import { useSimulationSocket } from '../hooks/useSimulationSocket'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'
import type { SimulationAlert } from '../types'

const Simulation3DScene = lazy(() => import('../components/topology/Simulation3DScene'))

const taskModeLabels = {
  direct: 'CTU 多箱取货',
  cross_zone: '跨区协同',
}

const robotTypeLabels = {
  ctu: 'CTU',
  shuttle: '四向车',
}

type DetailTab = 'metrics' | 'devices' | 'tasks' | 'alerts'

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

function LiveEventCount() {
  const count = usePlatformStore((state) => state.liveEvents.length)
  return <strong>{count}</strong>
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
  const [activeFloor, setActiveFloor] = useState('1F')
  const [startError, setStartError] = useState('')
  const [starting, setStarting] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [detailTab, setDetailTab] = useState<DetailTab>('metrics')

  const topologiesQuery = useQuery({
    queryKey: ['topologies'],
    queryFn: api.listTopologies,
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
  useEffect(() => {
    if (resolvedTopologyId && resolvedTopologyId !== selectedTopologyId) {
      setSelectedTopologyId(resolvedTopologyId)
    }
  }, [resolvedTopologyId, selectedTopologyId, setSelectedTopologyId])

  const candidateState = liveState ?? stateQuery.data
  const currentState = candidateState?.topologyId === resolvedTopologyId ? candidateState : undefined
  const currentMetrics = currentState ? (liveMetrics ?? metricsQuery.data) : undefined
  const topologyIdForBoard = currentState?.topologyId || resolvedTopologyId
  const currentTopology = availableTopologies.find((item) => item.id === resolvedTopologyId)

  useEffect(() => {
    if (candidateState?.topologyId && resolvedTopologyId && candidateState.topologyId !== resolvedTopologyId) {
      resetRun()
    }
  }, [candidateState?.topologyId, resetRun, resolvedTopologyId])

  const topologyQuery = useQuery({
    queryKey: ['topology', topologyIdForBoard],
    queryFn: () => api.getTopology(topologyIdForBoard),
    enabled: Boolean(topologyIdForBoard),
  })
  const floors = topologyQuery.data?.payload.meta.floors?.length ? topologyQuery.data.payload.meta.floors : ['1F']

  async function startRun() {
    if (starting) return
    setStartError('')
    if (!resolvedTopologyId) {
      setStartError('没有可用的拓扑，请先在拓扑编辑器中保存拓扑。')
      return
    }

    try {
      setStarting(true)
      const result = await api.startSimulation(resolvedTopologyId)
      setSelectedRunId(result.runId)
      const nextState = await api.getSimulationState(result.runId)
      setLiveState(nextState)
      await queryClient.invalidateQueries({ queryKey: ['runs'] })
    } catch (error) {
      setStartError(error instanceof Error ? error.message : '启动仿真失败')
    } finally {
      setStarting(false)
    }
  }

  async function syncRun(action: 'pause' | 'resume' | 'stop') {
    if (!selectedRunId) return

    if (action === 'pause') await api.pauseSimulation(selectedRunId)
    if (action === 'resume') await api.resumeSimulation(selectedRunId)
    if (action === 'stop') await api.stopSimulation(selectedRunId)

    const nextState = await api.getSimulationState(selectedRunId)
    setLiveState(nextState)
    await queryClient.invalidateQueries({ queryKey: ['runs'] })
  }

  const sortedTasks = [...(currentState?.tasks ?? [])].sort((left, right) => {
    const leftWeight = left.status === 'completed' ? 1 : 0
    const rightWeight = right.status === 'completed' ? 1 : 0
    return leftWeight - rightWeight || left.priority - right.priority
  })

  const alerts = currentState?.alerts ?? []
  const robots = currentState?.robots ?? []
  const isRunning = currentState?.status === 'running'
  const runStatusLabel = starting ? '启动中…' : runStatusLabels[currentState?.status ?? 'idle'] ?? currentState?.status ?? '未启动'

  function openDetails(tab: DetailTab) {
    setDetailTab(tab)
    setDetailsOpen(true)
  }

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
          <span><small>事件</small><LiveEventCount /></span>
        </div>

        <div className="simulation-actions">
          <button className="ghost-button" onClick={() => navigate('/topology')} type="button">
            <ArrowLeft size={16} />
            返回编辑
          </button>
          <button className="accent-button" onClick={startRun} disabled={!resolvedTopologyId || starting || isRunning} type="button">
            {starting ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
            {starting ? '启动中…' : isRunning ? '运行中' : '启动'}
          </button>
          <button className="ghost-button" onClick={() => void syncRun('pause')} disabled={!selectedRunId} type="button">
            <Pause size={16} />
            暂停
          </button>
          <button className="ghost-button" onClick={() => void syncRun('resume')} disabled={!selectedRunId} type="button">
            <Play size={16} />
            继续
          </button>
          <button className="danger-button" onClick={() => void syncRun('stop')} disabled={!selectedRunId} type="button">
            <Square size={16} />
            停止
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

      <main className="simulation-scene-stage">
        {startError || (!topologiesQuery.isLoading && requestedTopologyId && !requestedTopologyExists) ? (
          <div className="simulation-message-stack">
            {startError ? <p className="form-error">{startError}</p> : null}
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
            </div>

            <div className="simulation-kpi-strip" aria-label="关键仿真指标">
              <button type="button" onClick={() => openDetails('metrics')}>
                <small>吞吐量</small><strong>{currentMetrics?.throughput ?? 0}</strong>
              </button>
              <button type="button" onClick={() => openDetails('tasks')}>
                <small>任务</small><strong>{currentMetrics?.completedTasks ?? 0} / {currentMetrics?.pendingTasks ?? 0}</strong>
              </button>
              <button type="button" onClick={() => openDetails('devices')}>
                <small>设备</small><strong>{currentMetrics?.activeRobots ?? 0} / {currentMetrics?.blockedRobots ?? 0}</strong>
              </button>
              <button type="button" onClick={() => openDetails('metrics')}>
                <small>输送线</small><strong>{Math.round((currentMetrics?.conveyorUtilization ?? 0) * 100)}%</strong>
              </button>
              <button type="button" onClick={() => openDetails('alerts')}>
                <small>异常</small><strong>{(currentMetrics?.deadlocksDetected ?? 0) + (currentMetrics?.congestionEvents ?? 0)}</strong>
              </button>
            </div>

            <div className="simulation-3d-viewport simulation-main-viewport">
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
                  goods={currentState?.inventory ?? []}
                  robots={robots}
                  tasks={currentState?.tasks ?? []}
                  activeFloor={activeFloor}
                />
              </Suspense>
            </div>
          </>
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
      </main>
    </div>
  )
}
