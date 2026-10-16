import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { HistoricalPlaybackControls } from '../features/path-demo/HistoricalPlaybackControls'
import type { CameraActionRequest, CameraViewMode } from '../features/path-demo/cameraControls'
import {
  buildMultiVehicleTrajectories,
  sampleAllVehiclesAtTime,
  sampleMultiVehicleViews,
} from '../features/path-demo/multiPathPlayback'
import { useMultiPathExperimentStore } from '../features/path-demo/multiPathExperimentStore'
import { pathDemoApi } from '../features/path-demo/pathDemoApi'
import { usePathPlayback } from '../features/path-demo/pathPlayback'
import type { PathDemoAlgorithm } from '../features/path-demo/types'
import { api } from '../lib/api'
import './LabPage.css'
import './PathDemoPage.css'

const PathDemo3DScene = lazy(() =>
  import('../features/path-demo/PathDemo3DScene').then((module) => ({ default: module.PathDemo3DScene })),
)

const algorithmLabel = { astar: '时空 A*', dijkstra: '时空 Dijkstra' }

export function MultiPathResultPage() {
  const params = useParams<{ experimentId?: string; algorithm: string }>()
  const algorithm = params.algorithm as PathDemoAlgorithm
  const storedEvaluation = useMultiPathExperimentStore((state) => state.evaluation)
  const memoryEvaluation = params.experimentId ? null : storedEvaluation
  const evaluationQuery = useQuery({
    queryKey: ['multi-path-evaluation', params.experimentId],
    queryFn: () => pathDemoApi.evaluation(params.experimentId ?? ''),
    enabled: Boolean(params.experimentId),
  })
  const evaluation = memoryEvaluation ?? evaluationQuery.data
  const result = evaluation?.results.find((item) => item.algorithm === algorithm)
  const plan = result?.plan ?? null
  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies, enabled: Boolean(plan) })
  const topologyRecord = topologiesQuery.data?.find((item) => item.id === plan?.topology_id)
  const topologyOptionsQuery = useQuery({
    queryKey: ['path-demo-options', plan?.topology_id],
    queryFn: () => pathDemoApi.options(plan?.topology_id ?? ''),
    enabled: Boolean(plan),
  })
  const savedFingerprint = evaluation?.input_snapshot.topology_content_sha256
  const topologyFingerprintMatches = Boolean(
    savedFingerprint
      && topologyRecord?.payload.meta.version === plan?.topology_version
      && topologyOptionsQuery.data?.topology_content_sha256 === savedFingerprint,
  )
  const [selectedVehicleId, setSelectedVehicleId] = useState(plan?.vehicles[0]?.vehicle_id ?? '')
  const [cameraViewMode, setCameraViewMode] = useState<CameraViewMode>('free')
  const [cameraActionRequest, setCameraActionRequest] = useState<CameraActionRequest | null>(null)
  const duration = plan?.makespan_seconds ?? 0
  const timelineKey = `${evaluation?.experiment_id ?? 'missing'}:${algorithm}`
  const playback = usePathPlayback(duration, timelineKey)
  const timeStepSeconds = plan?.time_step_seconds ?? Number.NaN

  useEffect(() => {
    if (!plan?.vehicles.length) return
    if (!plan.vehicles.some((vehicle) => vehicle.vehicle_id === selectedVehicleId)) {
      setSelectedVehicleId(plan.vehicles[0].vehicle_id)
    }
  }, [plan, selectedVehicleId])

  const preparedVehicleViews = useMemo(
    () => plan && topologyRecord && topologyFingerprintMatches
      ? buildMultiVehicleTrajectories(plan, topologyRecord.payload)
      : [],
    [plan, topologyRecord, topologyFingerprintMatches],
  )
  const vehicleViews = useMemo(
    () => sampleMultiVehicleViews(preparedVehicleViews, playback.state.elapsedSeconds),
    [playback.state.elapsedSeconds, preparedVehicleViews],
  )
  const vehicleSnapshots = useMemo(
    () => plan ? sampleAllVehiclesAtTime(plan, playback.state.elapsedSeconds) : [],
    [plan, playback.state.elapsedSeconds],
  )
  const selectedSnapshot = vehicleSnapshots.find((vehicle) => vehicle.vehicleId === selectedVehicleId) ?? null
  const selectedSample = vehicleViews.find((vehicle) => vehicle.vehicleId === selectedVehicleId)?.sample ?? null
  const requestCameraAction = (type: CameraActionRequest['type']) => {
    setCameraActionRequest((current) => ({ type, sequence: (current?.sequence ?? 0) + 1 }))
  }
  const seekHistoricalTime = (elapsedSeconds: number) => {
    playback.seek(elapsedSeconds)
    if (cameraViewMode === 'follow') requestCameraAction('switch-follow-target')
  }

  if (evaluationQuery.isLoading) {
    return <section className="multi-path-result-empty"><h2>正在读取持久化实验轨迹…</h2></section>
  }

  if (evaluationQuery.isError) {
    return (
      <section className="multi-path-result-empty">
        <h2>无法读取持久化实验轨迹</h2>
        <p>{evaluationQuery.error.message}。系统不会静默重新规划。</p>
        <Link to="/reports">返回回放与报告</Link>
      </section>
    )
  }

  if (!evaluation || !result || !['astar', 'dijkstra'].includes(algorithm)) {
    return (
      <section className="multi-path-result-empty">
        <h2>实验轨迹快照已失效或不可用</h2>
        <p>旧链接需要当前浏览器内存中的快照；稳定历史链接会从后端读取保存结果。系统不会静默重新规划。</p>
        <Link className="accent-button" to="/lab">返回算法实验室</Link>
      </section>
    )
  }

  if (!plan) {
    return (
      <section className="multi-path-result-empty">
        <h2>{algorithmLabel[algorithm]} 本次规划失败，无可回放轨迹</h2>
        <p>{result.error_reason ?? '未返回失败原因'}。系统不会以新规划替代历史失败结果。</p>
        <Link to={`/reports/multi-path/${evaluation.experiment_id}`}>返回实验报告</Link>
      </section>
    )
  }

  if (topologiesQuery.isError || topologyOptionsQuery.isError) {
    return <section className="multi-path-result-empty"><h2>无法加载实验拓扑</h2><p>无法验证历史拓扑版本和内容指纹，已拒绝 3D 回放。</p><Link to={`/reports/multi-path/${evaluation.experiment_id}`}>返回实验报告</Link></section>
  }

  if (topologiesQuery.isSuccess && !topologyRecord) {
    return (
      <section className="multi-path-result-empty">
        <h2>历史实验拓扑不存在</h2>
        <p>本 Gate 未归档完整拓扑快照，不能用其他拓扑替代，已拒绝 3D 回放。</p>
        <Link to={`/reports/multi-path/${evaluation.experiment_id}`}>返回实验报告</Link>
      </section>
    )
  }

  if (!savedFingerprint) {
    return (
      <section className="multi-path-result-empty">
        <h2>历史实验缺少拓扑内容指纹</h2>
        <p>旧记录的指标仍可查看和导出，但无法安全证明当前拓扑与规划时一致，已拒绝 3D 回放。</p>
        <Link to={`/reports/multi-path/${evaluation.experiment_id}`}>返回实验报告</Link>
      </section>
    )
  }

  if (topologyRecord && topologyOptionsQuery.isSuccess && !topologyFingerprintMatches) {
    return (
      <section className="multi-path-result-empty">
        <h2>拓扑版本或内容与实验快照不一致</h2>
        <p>快照版本为 {plan.topology_version}，当前版本为 {topologyRecord.payload.meta.version}。内容指纹校验未通过，已拒绝播放。</p>
        <Link to={`/reports/multi-path/${evaluation.experiment_id}`}>返回实验报告</Link>
      </section>
    )
  }

  return (
    <section className="path-demo-page multi-path-result-page">
      <header className="path-demo-header">
        <div>
          <p className="eyebrow">实验快照 {evaluation.experiment_id}</p>
          <h2>{algorithmLabel[algorithm]} · 多车 3D 轨迹</h2>
          <p>直接回放本次评测返回的时空轨迹，不重新规划；这是离线路径演示，不是完整仓储任务仿真。</p>
        </div>
        <Link className="ghost-button" to={`/reports/multi-path/${evaluation.experiment_id}`}>返回实验报告</Link>
      </header>
      <div className="path-demo-layout">
        <aside className="path-demo-panel">
          <dl className="path-demo-stats">
            <div><dt>拓扑</dt><dd>{plan.topology_name}</dd></div>
            <div><dt>拓扑版本</dt><dd>{plan.topology_version}</dd></div>
            <div><dt>算法</dt><dd>{algorithmLabel[algorithm]}</dd></div>
            <div><dt>车辆</dt><dd>{plan.vehicles.length}</dd></div>
            <div><dt>Makespan</dt><dd>{plan.makespan_seconds.toFixed(2)} s</dd></div>
          </dl>
          <HistoricalPlaybackControls
            state={playback.state}
            totalDuration={duration}
            timeStepSeconds={timeStepSeconds}
            onStart={playback.start}
            onPause={playback.pause}
            onResume={playback.resume}
            onReset={playback.reset}
            onSeek={seekHistoricalTime}
            onRestart={playback.restart}
            onRateChange={playback.setRate}
          />
          <section className="path-demo-camera-controls" aria-label="实验轨迹视角控制">
            <div className="path-demo-camera-heading">
              <strong>视角控制</strong>
              <span className={`path-demo-view-badge is-${cameraViewMode}`}>当前视角：{cameraViewMode === 'follow' ? '跟随' : '自由'}</span>
            </div>
            <label>
              选择跟随车辆
              <select
                value={selectedVehicleId}
                onChange={(event) => {
                  setSelectedVehicleId(event.target.value)
                  requestCameraAction(cameraViewMode === 'follow' ? 'switch-follow-target' : 'locate')
                }}
              >
                {plan.vehicles.map((vehicle) => <option key={vehicle.vehicle_id} value={vehicle.vehicle_id}>{vehicle.vehicle_id}</option>)}
              </select>
            </label>
            <div className="path-demo-camera-buttons">
              <button className="ghost-button" type="button" disabled={!selectedSample} onClick={() => { setCameraViewMode('free'); requestCameraAction('locate') }}>定位小车</button>
              <button className={cameraViewMode === 'follow' ? 'accent-button' : 'ghost-button'} type="button" disabled={!selectedSample} onClick={() => setCameraViewMode((current) => current === 'follow' ? 'free' : 'follow')}>跟随小车：{cameraViewMode === 'follow' ? '开' : '关'}</button>
              <button className="ghost-button" type="button" disabled={cameraViewMode === 'free'} onClick={() => setCameraViewMode('free')}>自由视角</button>
              <button className="ghost-button" type="button" onClick={() => { setCameraViewMode('free'); requestCameraAction('overview') }}>仓库全景</button>
            </div>
            <p className="path-demo-note">当前车辆：{selectedVehicleId}</p>
          </section>
          <div className="path-demo-vehicle-results">
            {plan.vehicles.map((vehicle) => (
              <div key={vehicle.vehicle_id}>
                <strong>{vehicle.vehicle_id}</strong><span>{vehicle.total_distance} 格</span><span>等待 {vehicle.wait_time_seconds.toFixed(2)} s</span>
              </div>
            ))}
          </div>
          <section className="historical-vehicle-status" aria-label="历史车辆状态">
            <strong>车辆实时状态</strong>
            {vehicleSnapshots.map((vehicle) => (
              <button
                type="button"
                key={vehicle.vehicleId}
                className={`historical-vehicle-row${vehicle.vehicleId === selectedVehicleId ? ' is-selected' : ''}`}
                onClick={() => {
                  setSelectedVehicleId(vehicle.vehicleId)
                  requestCameraAction(cameraViewMode === 'follow' ? 'switch-follow-target' : 'locate')
                }}
              >
                <strong>{vehicle.vehicleId}</strong>
                <small>已移动 {vehicle.movedDistance.toFixed(2)} 格 · 等待 {vehicle.accumulatedWaitSeconds.toFixed(2)} s</small>
                <span className={`historical-status-badge is-${vehicle.status.toLowerCase().replace('_', '-')}`}>{vehicle.status}</span>
                <span className="historical-position">位置：{vehicle.logicalPosition}</span>
                <progress aria-label={`${vehicle.vehicleId} 路径进度`} max={1} value={vehicle.progress} />
              </button>
            ))}
            {selectedSnapshot ? (
              <div className="historical-selected-detail" aria-label="当前选中车辆详情">
                <strong>当前车辆：{selectedSnapshot.vehicleId}</strong>
                <span>状态：{selectedSnapshot.status}</span>
                <span>逻辑位置：{selectedSnapshot.logicalPosition}</span>
                <span>路径进度：{(selectedSnapshot.progress * 100).toFixed(1)}%</span>
              </div>
            ) : null}
          </section>
        </aside>
        <div className="path-demo-viewport" aria-label="算法实验多车 3D 轨迹">
          {topologyRecord && topologyFingerprintMatches ? (
            <Suspense fallback={<div className="path-demo-empty">正在加载 3D 场景…</div>}>
              <PathDemo3DScene
                topology={topologyRecord.payload}
                activeFloor={plan.vehicles[0]?.start.floor ?? '1F'}
                trajectory={[]}
                sample={null}
                playbackStatus={playback.state.status}
                cameraViewMode={cameraViewMode}
                cameraActionRequest={cameraActionRequest}
                vehicles={vehicleViews}
                followedVehicleSample={selectedSample}
                selectedVehicleId={selectedVehicleId}
              />
            </Suspense>
          ) : <div className="path-demo-empty">正在读取实验拓扑…</div>}
        </div>
      </div>
    </section>
  )
}
