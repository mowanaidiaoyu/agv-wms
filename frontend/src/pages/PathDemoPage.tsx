import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PathPlaybackController } from '../features/path-demo/PathPlaybackController'
import { pathDemoApi } from '../features/path-demo/pathDemoApi'
import { buildMultiVehicleViews } from '../features/path-demo/multiPathPlayback'
import { buildTrajectory, sampleTrajectory, usePathPlayback } from '../features/path-demo/pathPlayback'
import type { CameraActionRequest, CameraViewMode } from '../features/path-demo/cameraControls'
import type {
  MultiPathPlan,
  MultiPathVehicleRequest,
  PathDemoAlgorithm,
  PathDemoPlan,
} from '../features/path-demo/types'
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

function multiVehicleStatusLabel(globalStatus: keyof typeof statusLabel, elapsedSeconds: number, durationSeconds: number) {
  if (globalStatus === 'idle') return '待运行'
  if (elapsedSeconds >= durationSeconds) return '已到达'
  if (globalStatus === 'paused') return '已暂停'
  return '运行中'
}

type DemoMode = 'single' | 'multi'

export function PathDemoPage() {
  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies })
  const [topologyId, setTopologyId] = useState('')
  const [demoMode, setDemoMode] = useState<DemoMode>('single')
  const [algorithm, setAlgorithm] = useState<PathDemoAlgorithm>('astar')
  const [startCellId, setStartCellId] = useState('')
  const [goalCellId, setGoalCellId] = useState('')
  const [plan, setPlan] = useState<PathDemoPlan | null>(null)
  const [multiPlan, setMultiPlan] = useState<MultiPathPlan | null>(null)
  const [multiScenarioId, setMultiScenarioId] = useState('')
  const [multiVehicles, setMultiVehicles] = useState<MultiPathVehicleRequest[]>([])
  const [followVehicleId, setFollowVehicleId] = useState('CTU-01')
  const [planning, setPlanning] = useState(false)
  const [planningError, setPlanningError] = useState('')
  const [cameraViewMode, setCameraViewMode] = useState<CameraViewMode>('free')
  const [cameraActionRequest, setCameraActionRequest] = useState<CameraActionRequest | null>(null)

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
  const multiScenariosQuery = useQuery({
    queryKey: ['path-demo-multi-scenarios', topologyId],
    queryFn: () => pathDemoApi.multiScenarios(topologyId),
    enabled: Boolean(topologyId) && demoMode === 'multi',
  })

  const trajectory = useMemo(
    () => (plan && topologyRecord ? buildTrajectory(plan.path_cells, topologyRecord.payload) : []),
    [plan, topologyRecord],
  )
  const singleDuration = trajectory.at(-1)?.arrivalSeconds ?? 0
  const duration = demoMode === 'multi' ? (multiPlan?.makespan_seconds ?? 0) : singleDuration
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
  const multiVehicleViews = useMemo(
    () => (
      demoMode === 'multi' && multiPlan && topologyRecord
        ? buildMultiVehicleViews(multiPlan, topologyRecord.payload, playbackState.elapsedSeconds)
        : []
    ),
    [demoMode, multiPlan, playbackState.elapsedSeconds, topologyRecord],
  )
  const followedVehicleSample = demoMode === 'multi'
    ? (multiVehicleViews.find((vehicle) => vehicle.vehicleId === followVehicleId)?.sample ?? null)
    : sample

  useEffect(() => {
    if (!optionsQuery.data) return
    setStartCellId(optionsQuery.data.default_start.id)
    setGoalCellId(optionsQuery.data.default_goal.id)
    setPlan(null)
    setMultiPlan(null)
    setPlanningError('')
    setCameraViewMode('free')
    setCameraActionRequest(null)
    resetPlayback()
  }, [optionsQuery.data, resetPlayback])

  useEffect(() => {
    if (!multiScenariosQuery.data?.scenarios.length || multiScenarioId) return
    const initialScenario = multiScenariosQuery.data.scenarios[0]
    setMultiScenarioId(initialScenario.id)
    setMultiVehicles(initialScenario.vehicles)
    setFollowVehicleId(initialScenario.vehicles[0]?.vehicle_id ?? 'CTU-01')
  }, [multiScenarioId, multiScenariosQuery.data])

  const invalidatePlan = () => {
    setPlan(null)
    setMultiPlan(null)
    setPlanningError('')
    setCameraViewMode('free')
    setCameraActionRequest(null)
    resetPlayback()
  }

  const requestCameraAction = (type: CameraActionRequest['type']) => {
    setCameraActionRequest((current) => ({ type, sequence: (current?.sequence ?? 0) + 1 }))
  }

  const requestPlan = async () => {
    if (!topologyId) return
    if (demoMode === 'single' && (!startCellId || !goalCellId)) return
    if (demoMode === 'multi' && multiVehicles.length !== 3) return
    setPlanning(true)
    setPlanningError('')
    setPlan(null)
    setMultiPlan(null)
    setCameraViewMode('free')
    setCameraActionRequest(null)
    resetPlayback()
    try {
      if (demoMode === 'multi') {
        setMultiPlan(await pathDemoApi.planMulti(topologyId, multiVehicles, algorithm))
      } else {
        setPlan(await pathDemoApi.plan(topologyId, startCellId, goalCellId, algorithm))
      }
    } catch (error) {
      setPlan(null)
      setMultiPlan(null)
      setPlanningError(error instanceof Error ? error.message : '路径规划失败')
    } finally {
      setPlanning(false)
    }
  }

  const optionsError = optionsQuery.error instanceof Error ? optionsQuery.error.message : ''
  const scenariosError = multiScenariosQuery.error instanceof Error ? multiScenariosQuery.error.message : ''
  const activeFloor = multiPlan?.vehicles[0]?.start.floor ?? plan?.start.floor ?? optionsQuery.data?.default_start.floor ?? '1F'
  const roadCells = multiScenariosQuery.data?.road_cells ?? []
  const canPlay = demoMode === 'multi' ? Boolean(multiPlan && multiPlan.makespan_seconds > 0) : trajectory.length > 1

  return (
    <section className="path-demo-page">
      <header className="path-demo-header">
        <div>
          <p className="eyebrow">Gate C-3D-3 · deterministic space-time path playback</p>
          <h2>{demoMode === 'multi' ? '真实拓扑多车 3D 冲突避让演示' : '真实拓扑单车 3D 轨迹演示'}</h2>
          <p>路径来自后端 A* / Dijkstra；多车模式使用 0.25 秒时间片的优先级时空规划，不创建真实设备控制命令。</p>
        </div>
        <span className="path-demo-mode">CTU · {demoMode === 'multi' ? '多车离线演示' : '单车演示模式'}</span>
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
                setMultiScenarioId('')
                setMultiVehicles([])
                invalidatePlan()
              }}
            >
              {(topologiesQuery.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>
            演示模式
            <select
              value={demoMode}
              onChange={(event) => {
                const nextMode = event.target.value as DemoMode
                setDemoMode(nextMode)
                invalidatePlan()
                if (nextMode === 'multi') {
                  const initialScenario = multiScenariosQuery.data?.scenarios[0]
                  if (initialScenario) {
                    setMultiScenarioId(initialScenario.id)
                    setMultiVehicles(initialScenario.vehicles)
                  }
                }
              }}
            >
              <option value="single">单车轨迹</option>
              <option value="multi">三车冲突避让</option>
            </select>
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
          {demoMode === 'single' ? (
            <>
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
            </>
          ) : (
            <section className="path-demo-multi-config" aria-label="多车路径配置">
              <label>
                冲突演示场景
                <select
                  value={multiScenarioId}
                  disabled={!multiScenariosQuery.data}
                  onChange={(event) => {
                    const selected = multiScenariosQuery.data?.scenarios.find((item) => item.id === event.target.value)
                    if (!selected) return
                    setMultiScenarioId(selected.id)
                    setMultiVehicles(selected.vehicles)
                    setFollowVehicleId(selected.vehicles[0]?.vehicle_id ?? 'CTU-01')
                    invalidatePlan()
                  }}
                >
                  {(multiScenariosQuery.data?.scenarios ?? []).map((scenario) => (
                    <option key={scenario.id} value={scenario.id}>{scenario.name}</option>
                  ))}
                  {multiScenarioId === 'custom' ? <option value="custom">自定义</option> : null}
                </select>
              </label>
              {multiScenariosQuery.data?.scenarios.find((item) => item.id === multiScenarioId)?.description ? (
                <p className="path-demo-note">
                  {multiScenariosQuery.data.scenarios.find((item) => item.id === multiScenarioId)?.description}
                </p>
              ) : null}
              {multiVehicles.map((vehicle, vehicleIndex) => (
                <fieldset key={vehicle.vehicle_id} className="path-demo-vehicle-config">
                  <legend>{vehicle.vehicle_id}</legend>
                  <label>
                    {vehicle.vehicle_id} 起点
                    <select
                      value={vehicle.start_cell_id}
                      onChange={(event) => {
                        setMultiScenarioId('custom')
                        setMultiVehicles((current) => current.map((item, index) => (
                          index === vehicleIndex ? { ...item, start_cell_id: event.target.value } : item
                        )))
                        invalidatePlan()
                      }}
                    >
                      {roadCells.map((cell) => <option key={cell.id} value={cell.id}>{cell.id}</option>)}
                    </select>
                  </label>
                  <label>
                    {vehicle.vehicle_id} 终点
                    <select
                      value={vehicle.goal_cell_id}
                      onChange={(event) => {
                        setMultiScenarioId('custom')
                        setMultiVehicles((current) => current.map((item, index) => (
                          index === vehicleIndex ? { ...item, goal_cell_id: event.target.value } : item
                        )))
                        invalidatePlan()
                      }}
                    >
                      {roadCells.map((cell) => <option key={cell.id} value={cell.id}>{cell.id}</option>)}
                    </select>
                  </label>
                </fieldset>
              ))}
            </section>
          )}

          <button
            className="accent-button path-demo-plan-button"
            type="button"
            disabled={
              planning
              || optionsQuery.isFetching
              || (demoMode === 'single' ? !startCellId || !goalCellId : multiVehicles.length !== 3)
            }
            onClick={() => void requestPlan()}
          >
            {planning ? '规划中…' : demoMode === 'multi' ? '规划多车路径' : '规划路径'}
          </button>

          {optionsQuery.isLoading ? <p className="path-demo-note">正在分析可行驶连通分量…</p> : null}
          {optionsError ? <p className="path-demo-error">{optionsError}</p> : null}
          {scenariosError ? <p className="path-demo-error">{scenariosError}</p> : null}
          {planningError ? <p className="path-demo-error" role="alert">{planningError}</p> : null}

          <PathPlaybackController
            state={playbackState}
            canPlay={canPlay}
            onStart={startPlayback}
            onPause={pausePlayback}
            onResume={resumePlayback}
            onReset={resetPlayback}
            onRateChange={setPlaybackRate}
          />
          <section className="path-demo-camera-controls" aria-label="视角控制">
            <div className="path-demo-camera-heading">
              <strong>视角控制</strong>
              <span className={`path-demo-view-badge is-${cameraViewMode}`}>
                当前视角：{cameraViewMode === 'follow' ? '跟随' : '自由'}
              </span>
            </div>
            {demoMode === 'multi' ? (
              <label>
                选择跟随车辆
                <select
                  value={followVehicleId}
                  disabled={!multiPlan}
                  onChange={(event) => {
                    setFollowVehicleId(event.target.value)
                    requestCameraAction('locate')
                  }}
                >
                  {multiVehicles.map((vehicle) => (
                    <option key={vehicle.vehicle_id} value={vehicle.vehicle_id}>{vehicle.vehicle_id}</option>
                  ))}
                </select>
              </label>
            ) : null}
            <div className="path-demo-camera-buttons">
              <button
                className="ghost-button"
                type="button"
                disabled={!followedVehicleSample}
                onClick={() => {
                  setCameraViewMode('free')
                  requestCameraAction('locate')
                }}
              >
                定位小车
              </button>
              <button
                className={cameraViewMode === 'follow' ? 'accent-button' : 'ghost-button'}
                type="button"
                disabled={!followedVehicleSample}
                aria-pressed={cameraViewMode === 'follow'}
                onClick={() => setCameraViewMode((current) => (current === 'follow' ? 'free' : 'follow'))}
              >
                跟随小车：{cameraViewMode === 'follow' ? '开' : '关'}
              </button>
              <button
                className="ghost-button"
                type="button"
                disabled={cameraViewMode === 'free'}
                onClick={() => setCameraViewMode('free')}
              >
                自由视角
              </button>
              <button
                className="ghost-button"
                type="button"
                onClick={() => {
                  setCameraViewMode('free')
                  requestCameraAction('overview')
                }}
              >
                仓库全景
              </button>
            </div>
            <p className="path-demo-note">当前车辆：{demoMode === 'multi' ? followVehicleId : 'CTU-01'}</p>
            {!followedVehicleSample ? <p className="path-demo-note">请先规划路径，生成车辆位置后再定位或跟随。</p> : null}
          </section>

          <dl className="path-demo-stats">
            <div><dt>连通分量</dt><dd>{optionsQuery.data?.components.length ?? '—'}</dd></div>
            <div><dt>最大分量</dt><dd>{optionsQuery.data?.components[0]?.cell_count ?? '—'} 格</dd></div>
            {demoMode === 'multi' ? (
              <>
                <div><dt>车辆数量</dt><dd>{multiPlan?.vehicles.length ?? '—'}</dd></div>
                <div><dt>检测节点冲突</dt><dd>{multiPlan?.conflicts.detected_node_conflicts ?? '—'}</dd></div>
                <div><dt>检测边冲突</dt><dd>{multiPlan?.conflicts.detected_edge_conflicts ?? '—'}</dd></div>
                <div><dt>最终执行冲突</dt><dd>{multiPlan ? multiPlan.conflicts.remaining_node_conflicts + multiPlan.conflicts.remaining_edge_conflicts + multiPlan.conflicts.remaining_geometry_conflicts : '—'}</dd></div>
                <div><dt>避让次数</dt><dd>{multiPlan?.avoidance_count ?? '—'}</dd></div>
                <div><dt>规划耗时</dt><dd>{multiPlan ? `${multiPlan.planning_time_ms.toFixed(2)} ms` : '—'}</dd></div>
              </>
            ) : (
              <>
                <div><dt>路径长度</dt><dd>{plan ? `${plan.total_distance} 格` : '—'}</dd></div>
                <div><dt>路径节点</dt><dd>{plan?.path_cells.length ?? '—'}</dd></div>
                <div><dt>访问节点</dt><dd>{plan?.visited_nodes ?? '—'}</dd></div>
                <div><dt>规划耗时</dt><dd>{plan ? `${plan.planning_time_ms.toFixed(2)} ms` : '—'}</dd></div>
                <div><dt>车辆位置</dt><dd>{sample ? `${sample.floor} · (${sample.row.toFixed(2)}, ${sample.col.toFixed(2)})` : '—'}</dd></div>
              </>
            )}
            <div><dt>运动状态</dt><dd>{statusLabel[playbackState.status]}</dd></div>
            <div><dt>演示时间</dt><dd>{playbackState.elapsedSeconds.toFixed(2)} / {duration.toFixed(2)} s</dd></div>
          </dl>
          {demoMode === 'multi' && multiPlan ? (
            <div className="path-demo-vehicle-results" aria-label="多车规划结果">
              {multiPlan.vehicles.map((vehicle) => (
                <div key={vehicle.vehicle_id}>
                  <strong>{vehicle.vehicle_id}</strong>
                  <span>{vehicle.total_distance} 格</span>
                  <span>等待 {vehicle.wait_time_seconds.toFixed(2)} s</span>
                  <span>{multiVehicleStatusLabel(playbackState.status, playbackState.elapsedSeconds, vehicle.estimated_duration_seconds)}</span>
                </div>
              ))}
            </div>
          ) : null}
        </aside>

        <div className="path-demo-viewport" aria-label="CTU 3D path demonstration">
          {topologyRecord ? (
            <Suspense fallback={<div className="path-demo-empty">正在加载 3D 场景…</div>}>
              <PathDemo3DScene
                topology={topologyRecord.payload}
                activeFloor={activeFloor}
                trajectory={trajectory}
                sample={sample}
                playbackStatus={playbackState.status}
                cameraViewMode={cameraViewMode}
                cameraActionRequest={cameraActionRequest}
                vehicles={multiVehicleViews}
                followedVehicleSample={followedVehicleSample}
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
