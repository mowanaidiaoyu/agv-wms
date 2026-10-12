import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PathPlaybackController } from '../features/path-demo/PathPlaybackController'
import { pathDemoApi } from '../features/path-demo/pathDemoApi'
import { buildTrajectory, sampleTrajectory, usePathPlayback } from '../features/path-demo/pathPlayback'
import type { PathDemoAlgorithm, PathDemoPlan } from '../features/path-demo/types'
import { api } from '../lib/api'
import './PathDemoPage.css'

const PathDemo3DScene = lazy(() =>
  import('../features/path-demo/PathDemo3DScene').then((module) => ({ default: module.PathDemo3DScene })),
)

const statusLabel = {
  idle: '待运行',
  running: '运行中',
  paused: '已暂停',
  completed: '已到达',
}
export function PathDemoPage() {
  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies })
  const [topologyId, setTopologyId] = useState('')
  const [algorithm, setAlgorithm] = useState<PathDemoAlgorithm>('astar')
  const [startCellId, setStartCellId] = useState('')
  const [goalCellId, setGoalCellId] = useState('')
  const [plan, setPlan] = useState<PathDemoPlan | null>(null)
  const [planning, setPlanning] = useState(false)
  const [planningError, setPlanningError] = useState('')

  useEffect(() => {
    if (topologyId || !topologiesQuery.data?.length) return
    const preferred = topologiesQuery.data.find((item) => item.name === '666') ?? topologiesQuery.data[0]
    setTopologyId(preferred.id)
  }, [topologiesQuery.data, topologyId])

  const topologyRecord = topologiesQuery.data?.find((item) => item.id === topologyId)
  const optionsQuery = useQuery({
    queryKey: ['path-demo-options', topologyId],
    queryFn: () => pathDemoApi.options(topologyId),
    enabled: Boolean(topologyId),
  })

  const trajectory = useMemo(
    () => (plan && topologyRecord ? buildTrajectory(plan.path_cells, topologyRecord.payload) : []),
    [plan, topologyRecord],
  )
  const duration = trajectory.at(-1)?.arrivalSeconds ?? 0
  const {
    state: playbackState,
    start: startPlayback,
    pause: pausePlayback,
    resume: resumePlayback,
    reset: resetPlayback,
    setRate: setPlaybackRate,
  } = usePathPlayback(duration)
  const sample = useMemo(
    () => sampleTrajectory(trajectory, playbackState.elapsedSeconds),
    [playbackState.elapsedSeconds, trajectory],
  )

  useEffect(() => {
    if (!optionsQuery.data) return
    setStartCellId(optionsQuery.data.default_start.id)
    setGoalCellId(optionsQuery.data.default_goal.id)
    setPlan(null)
    setPlanningError('')
    resetPlayback()
  }, [optionsQuery.data, resetPlayback])

  const invalidatePlan = () => {
    setPlan(null)
    setPlanningError('')
    resetPlayback()
  }

  const requestPlan = async () => {
    if (!topologyId || !startCellId || !goalCellId) return
    setPlanning(true)
    setPlanningError('')
    resetPlayback()
    try {
      setPlan(await pathDemoApi.plan(topologyId, startCellId, goalCellId, algorithm))
    } catch (error) {
      setPlan(null)
      setPlanningError(error instanceof Error ? error.message : '路径规划失败')
    } finally {
      setPlanning(false)
    }
  }

  const optionsError = optionsQuery.error instanceof Error ? optionsQuery.error.message : ''
  const activeFloor = plan?.start.floor ?? optionsQuery.data?.default_start.floor ?? '1F'

  return (
    <section className="path-demo-page">
      <header className="path-demo-header">
        <div>
          <p className="eyebrow">Gate C-3D-1 · deterministic path playback</p>
          <h2>真实拓扑单车 3D 轨迹演示</h2>
          <p>路径来自后端 A* / Dijkstra，只读取可行驶道路；本页不创建仿真任务或设备控制命令。</p>
        </div>
        <span className="path-demo-mode">CTU · 演示模式</span>
      </header>

      <div className="path-demo-layout">
        <aside className="path-demo-panel">
          <label>
            拓扑
            <select
              value={topologyId}
              onChange={(event) => {
                setTopologyId(event.target.value)
                setStartCellId('')
                setGoalCellId('')
                invalidatePlan()
              }}
            >
              {(topologiesQuery.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>
            车辆类型
            <select value="ctu" disabled><option value="ctu">CTU</option></select>
          </label>
          <label>
            路径算法
            <select
              value={algorithm}
              onChange={(event) => {
                setAlgorithm(event.target.value as PathDemoAlgorithm)
                invalidatePlan()
              }}
            >
              <option value="astar">A*</option>
              <option value="dijkstra">Dijkstra</option>
            </select>
          </label>
          <label>
            起点（可行驶道路）
            <select
              value={startCellId}
              disabled={!optionsQuery.data}
              onChange={(event) => {
                setStartCellId(event.target.value)
                invalidatePlan()
              }}
            >
              {(optionsQuery.data?.traversable_cells ?? []).filter((cell) => cell.type === 'road').map((cell) => (
                <option key={cell.id} value={cell.id}>{cell.id} · {cell.floor} · {cell.component_id}</option>
              ))}
            </select>
          </label>
          <label>
            终点（可行驶道路）
            <select
              value={goalCellId}
              disabled={!optionsQuery.data}
              onChange={(event) => {
                setGoalCellId(event.target.value)
                invalidatePlan()
              }}
            >
              {(optionsQuery.data?.traversable_cells ?? []).filter((cell) => cell.type === 'road').map((cell) => (
                <option key={cell.id} value={cell.id}>{cell.id} · {cell.floor} · {cell.component_id}</option>
              ))}
            </select>
          </label>

          <button
            className="accent-button path-demo-plan-button"
            type="button"
            disabled={planning || optionsQuery.isFetching || !startCellId || !goalCellId}
            onClick={() => void requestPlan()}
          >
            {planning ? '规划中…' : '规划路径'}
          </button>

          {optionsQuery.isLoading ? <p className="path-demo-note">正在分析可行驶连通分量…</p> : null}
          {optionsError ? <p className="path-demo-error">{optionsError}</p> : null}
          {planningError ? <p className="path-demo-error" role="alert">{planningError}</p> : null}

          <PathPlaybackController
            state={playbackState}
            canPlay={trajectory.length > 1}
            onStart={startPlayback}
            onPause={pausePlayback}
            onResume={resumePlayback}
            onReset={resetPlayback}
            onRateChange={setPlaybackRate}
          />

          <dl className="path-demo-stats">
            <div><dt>连通分量</dt><dd>{optionsQuery.data?.components.length ?? '—'}</dd></div>
            <div><dt>最大分量</dt><dd>{optionsQuery.data?.components[0]?.cell_count ?? '—'} 格</dd></div>
            <div><dt>路径长度</dt><dd>{plan ? `${plan.total_distance} 格` : '—'}</dd></div>
            <div><dt>路径节点</dt><dd>{plan?.path_cells.length ?? '—'}</dd></div>
            <div><dt>访问节点</dt><dd>{plan?.visited_nodes ?? '—'}</dd></div>
            <div><dt>规划耗时</dt><dd>{plan ? `${plan.planning_time_ms.toFixed(2)} ms` : '—'}</dd></div>
            <div><dt>车辆位置</dt><dd>{sample ? `${sample.floor} · (${sample.row.toFixed(2)}, ${sample.col.toFixed(2)})` : '—'}</dd></div>
            <div><dt>运动状态</dt><dd>{statusLabel[playbackState.status]}</dd></div>
            <div><dt>演示时间</dt><dd>{playbackState.elapsedSeconds.toFixed(2)} / {duration.toFixed(2)} s</dd></div>
          </dl>
        </aside>

        <div className="path-demo-viewport" aria-label="CTU 3D path demonstration">
          {topologyRecord ? (
            <Suspense fallback={<div className="path-demo-empty">正在加载 3D 场景…</div>}>
              <PathDemo3DScene
                topology={topologyRecord.payload}
                activeFloor={activeFloor}
                trajectory={trajectory}
                sample={sample}
              />
            </Suspense>
          ) : (
            <div className="path-demo-empty">正在读取拓扑…</div>
          )}
        </div>
      </div>
    </section>
  )
}
