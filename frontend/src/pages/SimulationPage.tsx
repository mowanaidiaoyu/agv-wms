import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Pause, Play, Square, Zap } from 'lucide-react'
import { api } from '../lib/api'
import { Surface } from '../components/Surface'
import { usePlatformStore } from '../store/usePlatformStore'
import { useSimulationSocket } from '../hooks/useSimulationSocket'
import { KpiGrid } from '../components/KpiGrid'
import type { SimulationAlert, SimulationResourceState } from '../types'

function resourceTitle(resource: SimulationResourceState) {
  const titleMap: Record<string, string> = {
    conveyor_segment: '输送线',
    buffer_slot: '缓存位',
    station: '站台',
    lift: '提升机',
  }
  return titleMap[resource.type] ?? resource.type
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
  const setSelectedRunId = usePlatformStore((state) => state.setSelectedRunId)
  const setLiveState = usePlatformStore((state) => state.setLiveState)
  const liveState = usePlatformStore((state) => state.liveState)
  const liveMetrics = usePlatformStore((state) => state.liveMetrics)
  const liveEvents = usePlatformStore((state) => state.liveEvents)

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

  async function startRun() {
    const result = await api.startSimulation(selectedTopologyId, selectedScenarioId)
    setSelectedRunId(result.runId)
    const nextState = await api.getSimulationState(result.runId)
    setLiveState(nextState)
    await queryClient.invalidateQueries({ queryKey: ['runs'] })
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

  const currentState = liveState ?? stateQuery.data
  const currentMetrics = liveMetrics ?? metricsQuery.data
  const taskPreview = [...(currentState?.tasks ?? [])].sort((left, right) => {
    const leftWeight = left.status === 'completed' ? 1 : 0
    const rightWeight = right.status === 'completed' ? 1 : 0
    return leftWeight - rightWeight || left.priority - right.priority
  })

  return (
    <div className="page-grid simulation-grid">
      <Surface
        title="仿真工作台"
        subtitle="直接从默认拓扑和默认场景启动，观察任务阶段、资源占用与可解释告警。"
        action={
          <div className="surface-actions">
            <button className="accent-button" onClick={startRun} disabled={!selectedTopologyId || !selectedScenarioId}>
              <Play size={16} />
              启动
            </button>
            <button className="ghost-button" onClick={() => void syncRun('pause')} disabled={!selectedRunId}>
              <Pause size={16} />
              暂停
            </button>
            <button className="ghost-button" onClick={() => void syncRun('resume')} disabled={!selectedRunId}>
              <Play size={16} />
              继续
            </button>
            <button className="danger-button" onClick={() => void syncRun('stop')} disabled={!selectedRunId}>
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
      </Surface>

      <Surface title="实时指标" subtitle="优先显示 WebSocket 推送，轮询作为兜底。">
        <KpiGrid metrics={currentMetrics} />
      </Surface>

      <Surface title="设备状态" subtitle="跟踪机器人位置、阶段与等待时长。">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>设备</th>
                <th>类型</th>
                <th>当前位置</th>
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
                  <td>{robot.type}</td>
                  <td>{robot.currentNode}</td>
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

      <Surface title="任务阶段" subtitle="重点观察四向车交接、输送线等待和站台排队。">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>任务</th>
                <th>模式</th>
                <th>状态</th>
                <th>阶段</th>
                <th>设备</th>
                <th>优先级</th>
                <th>紧急</th>
              </tr>
            </thead>
            <tbody>
              {taskPreview.map((task) => (
                <tr key={task.id}>
                  <td>{task.id}</td>
                  <td>{task.mode === 'cross_zone' ? '跨区协同' : '直取直送'}</td>
                  <td>{task.status}</td>
                  <td>{task.phaseLabel}</td>
                  <td>{task.assignedRobotId ?? '-'}</td>
                  <td>{task.priority}</td>
                  <td>{task.urgent ? '是' : '否'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Surface>

      <Surface title="资源占用" subtitle="单独观察输送线、缓存位和站台队列。">
        <div className="resource-grid">
          {(currentState?.resources ?? []).map((resource) => (
            <article key={resource.id} className="resource-card">
              <div className="resource-card-head">
                <strong>{resourceTitle(resource)}</strong>
                <span>{resource.label}</span>
              </div>
              <p>占用: {resource.occupiedBy.length} / {resource.capacity}</p>
              <p>排队: {resource.queue.length}</p>
              <p>对象: {resource.occupiedBy.join(', ') || '空闲'}</p>
            </article>
          ))}
          <article className="resource-card">
            <div className="resource-card-head">
              <strong>全局队列</strong>
              <span>状态总览</span>
            </div>
            <p>输送线队列: {currentState?.queues.conveyor.length ?? 0}</p>
            <p>缓存等待: {currentState?.queues.buffer.length ?? 0}</p>
            <p>站台排队: {currentState?.queues.station.length ?? 0}</p>
          </article>
        </div>
      </Surface>

      <Surface title="告警与解释" subtitle="把死锁、站台阻塞和输送线拥堵展示成可解释事件。">
        <div className="alert-stack">
          {(currentState?.alerts ?? []).length ? (
            (currentState?.alerts ?? []).map((alert, index) => (
              <div key={`${alert.tick}-${index}`} className={alertClassName(alert.level)}>
                <Zap size={16} />
                <div>
                  <strong>{alert.category}</strong>
                  <p>{alert.message}</p>
                  <p className="muted-copy">建议：{alert.suggestion}</p>
                </div>
              </div>
            ))
          ) : (
            <p className="muted-copy">当前没有新的告警，说明仿真运行较平稳。</p>
          )}
        </div>
      </Surface>
    </div>
  )
}
