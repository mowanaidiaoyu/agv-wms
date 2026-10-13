import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { PathPlaybackController } from '../features/path-demo/PathPlaybackController'
import type { CameraActionRequest, CameraViewMode } from '../features/path-demo/cameraControls'
import { buildMultiVehicleViews } from '../features/path-demo/multiPathPlayback'
import { useMultiPathExperimentStore } from '../features/path-demo/multiPathExperimentStore'
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
  const params = useParams<{ algorithm: string }>()
  const algorithm = params.algorithm as PathDemoAlgorithm
  const evaluation = useMultiPathExperimentStore((state) => state.evaluation)
  const result = evaluation?.results.find((item) => item.algorithm === algorithm)
  const plan = result?.plan ?? null
  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies, enabled: Boolean(plan) })
  const topologyRecord = topologiesQuery.data?.find((item) => item.id === plan?.topology_id)
  const topologyVersionMatches = topologyRecord?.payload.meta.version === plan?.topology_version
  const [selectedVehicleId, setSelectedVehicleId] = useState(plan?.vehicles[0]?.vehicle_id ?? '')
  const [cameraViewMode, setCameraViewMode] = useState<CameraViewMode>('free')
  const [cameraActionRequest, setCameraActionRequest] = useState<CameraActionRequest | null>(null)
  const duration = plan?.makespan_seconds ?? 0
  const playback = usePathPlayback(duration)

  useEffect(() => {
    if (!plan?.vehicles.length) return
    if (!plan.vehicles.some((vehicle) => vehicle.vehicle_id === selectedVehicleId)) {
      setSelectedVehicleId(plan.vehicles[0].vehicle_id)
    }
  }, [plan, selectedVehicleId])

  const vehicleViews = useMemo(
    () => plan && topologyRecord && topologyVersionMatches
      ? buildMultiVehicleViews(plan, topologyRecord.payload, playback.state.elapsedSeconds)
      : [],
    [plan, playback.state.elapsedSeconds, topologyRecord, topologyVersionMatches],
  )
  const selectedSample = vehicleViews.find((vehicle) => vehicle.vehicleId === selectedVehicleId)?.sample ?? null
  const requestCameraAction = (type: CameraActionRequest['type']) => {
    setCameraActionRequest((current) => ({ type, sequence: (current?.sequence ?? 0) + 1 }))
  }

  if (!evaluation || !result || !plan || !['astar', 'dijkstra'].includes(algorithm)) {
    return (
      <section className="multi-path-result-empty">
        <h2>实验轨迹快照已失效或不可用</h2>
        <p>评测结果只保存在当前浏览器内存中。请返回算法实验室重新运行评测；系统不会静默重新规划。</p>
        <Link className="accent-button" to="/lab">返回算法实验室</Link>
      </section>
    )
  }

  if (topologiesQuery.isError) {
    return <section className="multi-path-result-empty"><h2>无法加载实验拓扑</h2><Link to="/lab">返回算法实验室</Link></section>
  }

  if (topologyRecord && !topologyVersionMatches) {
    return (
      <section className="multi-path-result-empty">
        <h2>拓扑版本与实验快照不一致</h2>
        <p>快照版本为 {plan.topology_version}，当前版本为 {topologyRecord.payload.meta.version}。为避免错误回放，已拒绝播放。</p>
        <Link to="/lab">返回算法实验室</Link>
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
        <Link className="ghost-button" to="/lab">返回算法实验室</Link>
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
          <PathPlaybackController
            state={playback.state}
            canPlay={duration > 0}
            onStart={playback.start}
            onPause={playback.pause}
            onResume={playback.resume}
            onReset={playback.reset}
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
        </aside>
        <div className="path-demo-viewport" aria-label="算法实验多车 3D 轨迹">
          {topologyRecord && topologyVersionMatches ? (
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
