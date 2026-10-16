import { Suspense, lazy, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Pause, Play, Square, Zap } from 'lucide-react'
import { KpiGrid } from '../components/KpiGrid'
import { Surface } from '../components/Surface'
import { useSimulationSocket } from '../hooks/useSimulationSocket'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'
import type { SimulationAlert, SimulationResourceState } from '../types'

const Simulation3DScene = lazy(() => import('../components/topology/Simulation3DScene'))

const resourceLabels: Record<SimulationResourceState['type'], string> = {
  conveyor_segment: '输送线',
  buffer_slot: '缓存位',
  station: '站台',
  lift: '提升机',
}

const taskModeLabels = {
  direct: 'CTU 多箱取货',
  cross_zone: '跨区协同',
}

const robotTypeLabels = {
  ctu: 'CTU',
  shuttle: '四向车',
}

function alertClassName(level: SimulationAlert['level']) {
  if (level === 'error') return 'alert-card alert-error'
  if (level === 'warning') return 'alert-card alert-warning'
  return 'alert-card alert-info'
}

export function SimulationPage() {
  const queryClient = useQueryClient()
  const selectedTopologyId = usePlatformStore((state) => state.selectedTopologyId)
  const selectedScenarioId = usePlatformStore((state) => state.selectedScenarioId)
  const selectedRunId = usePlatformStore((state) => state.selectedRunId)
  const setSelectedTopologyId = usePlatformStore((state) => state.setSelectedTopologyId)
  const setSelectedScenarioId = usePlatformStore((state) => state.setSelectedScenarioId)
  const setSelectedRunId = usePlatformStore((state) => state.setSelectedRunId)
  const setLiveState = usePlatformStore((state) => state.setLiveState)
  const liveState = usePlatformStore((state) => state.liveState)
  const liveMetrics = usePlatformStore((state) => state.liveMetrics)
  const liveEvents = usePlatformStore((state) => state.liveEvents)
  const [activeFloor, setActiveFloor] = useState('1F')
  const [startError, setStartError] = useState('')

  const topologiesQuery = useQuery({
    queryKey: ['topologies'],
    queryFn: api.listTopologies,
  })
  const scenariosQuery = useQuery({
    queryKey: ['scenarios'],
    queryFn: api.listScenarios,
  })

  useSimulationSocket(selectedRunId)

  const stateQuery = useQuery({
    queryKey: ['simulation-state', selectedRunId],
    queryFn: () => api.getSimulationState(selectedRunId),
    enabled: Boolean(selectedRunId),
    refetchInterval: selectedRunId ? 1500 : false,
  })

  const metricsQuery = useQuery({
    queryKey: ['simulation-metrics', selectedRunId],
    queryFn: () => api.getSimulationMetrics(selectedRunId),
    enabled: Boolean(selectedRunId),
    refetchInterval: selectedRunId ? 1500 : false,
  })

  const availableTopologies = topologiesQuery.data ?? []
  const availableScenarios = scenariosQuery.data ?? []
  const resolvedTopologyId =
    (selectedTopologyId && availableTopologies.some((item) => item.id === selectedTopologyId) ? selectedTopologyId : '') ||
    availableTopologies[0]?.id ||
    ''
  const resolvedScenarioId = (() => {
    const selectedScenario = availableScenarios.find((item) => item.id === selectedScenarioId)
    if (selectedScenario && selectedScenario.payload.topologyId === resolvedTopologyId) {
      return selectedScenarioId
    }
    return availableScenarios.find((item) => item.payload.topologyId === resolvedTopologyId)?.id || ''
  })()

  useEffect(() => {
    if (resolvedTopologyId && resolvedTopologyId !== selectedTopologyId) {
      setSelectedTopologyId(resolvedTopologyId)
    }
    if (resolvedScenarioId && resolvedScenarioId !== selectedScenarioId) {
      setSelectedScenarioId(resolvedScenarioId)
    }
    if (!resolvedScenarioId && selectedScenarioId) {
      setSelectedScenarioId('')
    }
  }, [resolvedScenarioId, resolvedTopologyId, selectedScenarioId, selectedTopologyId, setSelectedScenarioId, setSelectedTopologyId])

  const currentState = liveState ?? stateQuery.data
  const currentMetrics = liveMetrics ?? metricsQuery.data
  const topologyIdForBoard = currentState?.topologyId || resolvedTopologyId

  const topologyQuery = useQuery({
    queryKey: ['topology', topologyIdForBoard],
    queryFn: () => api.getTopology(topologyIdForBoard),
    enabled: Boolean(topologyIdForBoard),
  })
  const floors = topologyQuery.data?.payload.meta.floors?.length ? topologyQuery.data.payload.meta.floors : ['1F']

  async function startRun() {
    setStartError('')
    if (!resolvedTopologyId || !resolvedScenarioId) {
      setStartError('没有可用的拓扑或场景，请先保存示例数据后再启动。')
      return
    }

    try {
      const result = await api.startSimulation(resolvedTopologyId, resolvedScenarioId)
      setSelectedRunId(result.runId)
      const nextState = await api.getSimulationState(result.runId)
      setLiveState(nextState)
      await queryClient.invalidateQueries({ queryKey: ['runs'] })
    } catch (error) {
      setStartError(error instanceof Error ? error.message : '启动仿真失败')
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

  return (
    <div className="page-grid simulation-grid">
      <Surface
        title="仿真工作台"
        subtitle="直接从示例拓扑和场景启动，观察机器人、输送线、缓存位和站台在网格中的协同流转。"
        action={
          <div className="surface-actions">
            <button
              className="accent-button"
              onClick={startRun}
              disabled={!resolvedTopologyId || !resolvedScenarioId}
              type="button"
            >
              <Play size={16} />
              启动
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
          </div>
        }
      >
        <div className="run-status-bar">
          <div>
            <span>当前 Run</span>
            <strong>{selectedRunId || '未启动'}</strong>
          </div>
          <div>
            <span>状态</span>
            <strong>{currentState?.status ?? 'idle'}</strong>
          </div>
          <div>
            <span>Tick</span>
            <strong>{currentState?.tick ?? 0}</strong>
          </div>
          <div>
            <span>实时事件</span>
            <strong>{liveEvents.length}</strong>
          </div>
        </div>
        {startError ? <p className="form-error">{startError}</p> : null}
      </Surface>

      <Surface title="实时指标" subtitle="优先显示 WebSocket 推送，轮询作为兜底。">
        <KpiGrid metrics={currentMetrics} />
      </Surface>

      <Surface
        title="场景棋盘"
        subtitle="采用更接近 6.6 的卡片网格，把格子、业务节点、资源和机器人都压到同一张场景板里。"
      >
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
            <div className="simulation-3d-viewport">
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
                  robots={currentState?.robots ?? []}
                  tasks={currentState?.tasks ?? []}
                  activeFloor={activeFloor}
                />
              </Suspense>
            </div>
          </>
        ) : (
          <p className="muted-copy">选择拓扑或启动仿真后，这里会显示场景棋盘。</p>
        )}
      </Surface>

      <Surface title="设备状态" subtitle="按格子坐标跟踪设备位置、目标位置、阶段和等待时长。">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>设备</th>
                <th>类型</th>
                <th>载箱</th>
                <th>当前位置</th>
                <th>目标格</th>
                <th>状态</th>
                <th>阶段</th>
                <th>等待 Tick</th>
                <th>当前任务</th>
              </tr>
            </thead>
            <tbody>
              {(currentState?.robots ?? []).map((robot) => (
                <tr key={robot.id}>
                  <td>{robot.id}</td>
                  <td>{robotTypeLabels[robot.type]}</td>
                  <td>{robot.load} / {robot.capacity}</td>
                  <td>
                    {robot.currentCellId}
                    <br />
                    <span className="muted-copy">
                      ({robot.currentRow}, {robot.currentCol})
                      {robot.currentFloor ? ` / ${robot.currentFloor}` : ''}
                    </span>
                  </td>
                  <td>{robot.targetCellId || '-'}</td>
                  <td>{robot.status}</td>
                  <td>{robot.phaseLabel}</td>
                  <td>{robot.waitTicks}</td>
                  <td>{robot.currentTaskId ?? '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Surface>

      <Surface title="任务阶段" subtitle="重点观察直送、交接、上输送线、缓存等待和站台交付的完整闭环。">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>任务</th>
                <th>模式</th>
                <th>状态</th>
                <th>阶段</th>
                <th>源格</th>
                <th>目标格</th>
                <th>设备</th>
                <th>优先级</th>
                <th>紧急</th>
              </tr>
            </thead>
            <tbody>
              {sortedTasks.map((task) => (
                <tr key={task.id}>
                  <td>{task.id}</td>
                  <td>{taskModeLabels[task.mode]}</td>
                  <td>{task.status}</td>
                  <td>{task.phaseLabel}</td>
                  <td>
                    {task.sourceCellId}
                    <br />
                    <span className="muted-copy">
                      {task.lines?.length ?? 1} 项 · 已取 {task.picked ?? 0}
                    </span>
                    <ul className="task-lines">
                      {(task.lines ?? []).map((line, index) => {
                        const picked = index < (task.picked ?? 0)
                        const attr = [line.spec, line.grade, line.surfaceTreatment].filter(Boolean).join(' · ')
                        const label = line.skuName ? `${line.skuName}${attr ? `（${attr}）` : ''}` : line.nodeId
                        return (
                          <li key={`${line.nodeId}-${line.layer}-${index}`} className={picked ? 'task-line-picked' : ''}>
                            {picked ? '✓ ' : ''}{label}
                          </li>
                        )
                      })}
                    </ul>
                  </td>
                  <td>{task.targetCellId}</td>
                  <td>{task.assignedRobotId ?? '-'}</td>
                  <td>{task.priority}</td>
                  <td>{task.urgent ? '是' : '否'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Surface>

      <Surface title="资源占用" subtitle="单独观察输送线、缓存位、站台队列以及全局等待情况。">
        <div className="resource-grid">
          {(currentState?.resources ?? []).map((resource) => (
            <article key={resource.id} className="resource-card">
              <div className="resource-card-head">
                <strong>{resourceLabels[resource.type]}</strong>
                <span>{resource.label}</span>
              </div>
              <p>占用: {resource.occupiedBy.length} / {resource.capacity}</p>
              <p>排队: {resource.queue.length}</p>
              <p>绑定格子: {resource.cellIds.join(', ') || '-'}</p>
              <p>占用对象: {resource.occupiedBy.join(', ') || '空闲'}</p>
            </article>
          ))}
          <article className="resource-card">
            <div className="resource-card-head">
              <strong>全局队列</strong>
              <span>实时概览</span>
            </div>
            <p>输送线队列: {currentState?.queues.conveyor.length ?? 0}</p>
            <p>缓存等待: {currentState?.queues.buffer.length ?? 0}</p>
            <p>站台排队: {currentState?.queues.station.length ?? 0}</p>
          </article>
        </div>
      </Surface>

      <Surface title="告警与解释" subtitle="把死锁、站台阻塞和输送线拥堵展示成可追溯、可解释的事件。">
        <div className="alert-stack">
          {(currentState?.alerts ?? []).length ? (
            (currentState?.alerts ?? []).map((alert, index) => (
              <div key={`${alert.tick}-${index}`} className={alertClassName(alert.level)}>
                <Zap size={16} />
                <div>
                  <strong>{alert.category}</strong>
                  <p>{alert.message}</p>
                  <p className="muted-copy">建议: {alert.suggestion}</p>
                </div>
              </div>
            ))
          ) : (
            <p className="muted-copy">当前没有新的告警，说明仿真运行比较平稳。</p>
          )}
        </div>
      </Surface>
    </div>
  )
}
