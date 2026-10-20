import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { StoredRecord, TopologyPayload } from '../../types'
import { api } from '../../lib/api'
import { PathPlaybackController } from '../path-demo/PathPlaybackController'
import type { CameraActionRequest, CameraViewMode } from '../path-demo/cameraControls'
import { usePathPlayback } from '../path-demo/pathPlayback'
import type { PathDemoAlgorithm } from '../path-demo/types'
import { sampleShuttlePlan, shuttleCameraSample, shuttleMotionLabel } from './shuttlePlayback'
import type { ShuttleLevel, ShuttlePlanResponse, ShuttleTrackNode } from './types'

const PathDemo3DScene = lazy(() =>
  import('../path-demo/PathDemo3DScene').then((module) => ({ default: module.PathDemo3DScene })),
)

type WorkspaceMode = 'planning' | 'inventory' | 'shuttle'

function nodesAtLevel(nodes: ShuttleTrackNode[], level: ShuttleLevel) {
  return nodes.filter((node) => node.shuttle_level === level)
}

function preferredNode(nodes: ShuttleTrackNode[], level: ShuttleLevel, preferredId: string, last = false) {
  const levelNodes = nodesAtLevel(nodes, level)
  return levelNodes.find((node) => node.track_node_id === preferredId)?.track_node_id
    ?? levelNodes[last ? levelNodes.length - 1 : 0]?.track_node_id
    ?? ''
}

export function ShuttleDemoWorkspace({
  topologyRecord,
  topologies,
  onTopologyChange,
  onWorkspaceModeChange,
}: {
  topologyRecord: StoredRecord<TopologyPayload>
  topologies: Array<StoredRecord<TopologyPayload>>
  onTopologyChange: (topologyId: string) => void
  onWorkspaceModeChange: (mode: WorkspaceMode) => void
}) {
  const topologyId = topologyRecord.id
  const levelsQuery = useQuery({
    queryKey: ['shuttle-levels', topologyId], queryFn: () => api.getShuttleLevels(topologyId),
  })
  const tracksQuery = useQuery({
    queryKey: ['shuttle-tracks', topologyId], queryFn: () => api.getShuttleTracks(topologyId),
  })
  const liftsQuery = useQuery({
    queryKey: ['shuttle-lifts', topologyId], queryFn: () => api.getShuttleLifts(topologyId),
  })
  const tracks = useMemo(() => tracksQuery.data?.nodes ?? [], [tracksQuery.data])
  const lifts = useMemo(() => liftsQuery.data?.lifts ?? [], [liftsQuery.data])
  const [startLevel, setStartLevel] = useState<ShuttleLevel>(1)
  const [endLevel, setEndLevel] = useState<ShuttleLevel>(3)
  const [startNodeId, setStartNodeId] = useState('')
  const [endNodeId, setEndNodeId] = useState('')
  const [algorithm, setAlgorithm] = useState<PathDemoAlgorithm>('astar')
  const [plan, setPlan] = useState<ShuttlePlanResponse | null>(null)
  const [planning, setPlanning] = useState(false)
  const [error, setError] = useState('')
  const [cameraViewMode, setCameraViewMode] = useState<CameraViewMode>('free')
  const [cameraActionRequest, setCameraActionRequest] = useState<CameraActionRequest | null>(null)
  const [selectedLiftId, setSelectedLiftId] = useState<string | null>(null)
  const requestRef = useRef(0)
  const cameraSequenceRef = useRef(0)

  useEffect(() => {
    requestRef.current += 1
    setPlan(null)
    setError('')
    setPlanning(false)
    setCameraViewMode('free')
    setCameraActionRequest(null)
    setSelectedLiftId(null)
    setStartLevel(1)
    setEndLevel(3)
    setStartNodeId('')
    setEndNodeId('')
  }, [topologyId])

  useEffect(() => {
    if (!tracks.length) return
    setStartNodeId((current) => nodesAtLevel(tracks, startLevel).some((node) => node.track_node_id === current)
      ? current
      : preferredNode(tracks, startLevel, `L${startLevel}-r100-c7`))
  }, [startLevel, tracks])
  useEffect(() => {
    if (!tracks.length) return
    setEndNodeId((current) => nodesAtLevel(tracks, endLevel).some((node) => node.track_node_id === current)
      ? current
      : preferredNode(tracks, endLevel, `L${endLevel}-r100-c51`, true))
  }, [endLevel, tracks])

  const invalidatePlan = () => {
    requestRef.current += 1
    setPlan(null)
    setError('')
    setCameraViewMode('free')
  }
  const playback = usePathPlayback(plan?.estimated_duration_seconds ?? 0, plan
    ? `${plan.topology_revision}:${plan.algorithm}:${plan.start?.track_node_id}:${plan.destination?.track_node_id}`
    : `${topologyId}:empty`)
  const sample = useMemo(
    () => sampleShuttlePlan(plan, playback.state.elapsedSeconds, playback.state.status),
    [plan, playback.state.elapsedSeconds, playback.state.status],
  )
  const cameraSample = useMemo(() => shuttleCameraSample(sample), [sample])
  const trackLookup = useMemo(() => new Map(tracks.map((node) => [node.track_node_id, node])), [tracks])

  const requestCamera = (type: CameraActionRequest['type'], targetWorld?: [number, number, number]) => {
    cameraSequenceRef.current += 1
    setCameraActionRequest({ type, sequence: cameraSequenceRef.current, targetWorld })
  }
  const focusLift = (liftId: string) => {
    const lift = lifts.find((item) => item.lift_id === liftId)
    const entrance = lift ? trackLookup.get(lift.level_entrances.L2) : null
    if (!entrance) return
    setSelectedLiftId(liftId)
    setCameraViewMode('free')
    requestCamera('focus-point', [entrance.world_x, entrance.world_y, entrance.world_z])
  }
  const requestPlan = async () => {
    const sequence = requestRef.current + 1
    requestRef.current = sequence
    setPlanning(true)
    setError('')
    try {
      const next = await api.planShuttlePath({
        topology_id: topologyId,
        start_track_node: startNodeId,
        start_level: startLevel,
        end_track_node: endNodeId,
        end_level: endLevel,
        algorithm,
      })
      if (requestRef.current !== sequence) return
      if (next.result_status !== 'success') {
        setPlan(null)
        setError(next.failure_reason ?? '四向车跨层路径规划失败。')
        return
      }
      setPlan(next)
      setSelectedLiftId(next.chosen_lift)
    } catch (caught) {
      if (requestRef.current === sequence) setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      if (requestRef.current === sequence) setPlanning(false)
    }
  }
  const planningEnabled = Boolean(levelsQuery.data?.planning_enabled && liftsQuery.data?.planning_enabled)
  const queryError = [levelsQuery.error, tracksQuery.error, liftsQuery.error].find(Boolean)

  return (
    <div className="path-demo-layout">
      <aside className="path-demo-panel shuttle-demo-panel">
        <label>
          拓扑
          <select value={topologyId} onChange={(event) => onTopologyChange(event.target.value)}>
            {topologies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <div className="path-demo-workspace-tabs is-three" role="tablist" aria-label="工作台模式">
          <button type="button" role="tab" aria-selected={false} onClick={() => onWorkspaceModeChange('planning')}>轨迹规划</button>
          <button type="button" role="tab" aria-selected={false} onClick={() => onWorkspaceModeChange('inventory')}>库存查看</button>
          <button type="button" role="tab" aria-selected className="active">四向车跨层</button>
        </div>

        <section className="shuttle-contract-card" aria-label="四向车轨道契约">
          <strong>三层真实轨道 · 双提升机</strong>
          <span>数据：slotAisles · 配置：{levelsQuery.data?.config_revision ?? '读取中'}</span>
          <span className={planningEnabled ? 'is-verified' : 'is-blocked'}>
            {levelsQuery.data?.verification_status ?? '正在校验'}
          </span>
          <span>程序验证候选：{levelsQuery.data?.lift_candidate_count ?? '—'} 处</span>
          <p>轨道层 L1/L2/L3 不等同于建筑楼层或 CTU 货架层。</p>
          <p>原 3D 数据具备第 4 个货架层位，本 Gate 严格只规划 L1–L3，L4 不进入 ShuttleGraph。</p>
        </section>

        <div className="shuttle-level-grid">
          <label>起点层
            <select aria-label="四向车起点层" value={startLevel} onChange={(event) => { setStartLevel(Number(event.target.value) as ShuttleLevel); invalidatePlan() }}>
              {[1, 2, 3].map((level) => <option key={level} value={level}>L{level}</option>)}
            </select>
          </label>
          <label>终点层
            <select aria-label="四向车终点层" value={endLevel} onChange={(event) => { setEndLevel(Number(event.target.value) as ShuttleLevel); invalidatePlan() }}>
              {[1, 2, 3].map((level) => <option key={level} value={level}>L{level}</option>)}
            </select>
          </label>
        </div>
        <label>起点轨道节点
          <select aria-label="四向车起点轨道节点" value={startNodeId} onChange={(event) => { setStartNodeId(event.target.value); invalidatePlan() }}>
            {nodesAtLevel(tracks, startLevel).map((node) => <option key={node.track_node_id} value={node.track_node_id}>{node.track_node_id}</option>)}
          </select>
        </label>
        <label>终点轨道节点
          <select aria-label="四向车终点轨道节点" value={endNodeId} onChange={(event) => { setEndNodeId(event.target.value); invalidatePlan() }}>
            {nodesAtLevel(tracks, endLevel).map((node) => <option key={node.track_node_id} value={node.track_node_id}>{node.track_node_id}</option>)}
          </select>
        </label>
        <label>路径算法
          <select aria-label="四向车路径算法" value={algorithm} onChange={(event) => { setAlgorithm(event.target.value as PathDemoAlgorithm); invalidatePlan() }}>
            <option value="astar">A*</option><option value="dijkstra">Dijkstra</option>
          </select>
        </label>
        <button className="accent-button path-demo-plan-button" type="button" disabled={!planningEnabled || planning || !startNodeId || !endNodeId} onClick={() => void requestPlan()}>
          {planning ? '规划中…' : '规划跨层路径'}
        </button>
        {queryError ? <p className="path-demo-error" role="alert">{String(queryError)}</p> : null}
        {error ? <p className="path-demo-error" role="alert">{error}</p> : null}
        {!planningEnabled && levelsQuery.data ? <p className="path-demo-error">当前轨道或提升机配置未通过程序验证，已禁止运行。</p> : null}
        {(levelsQuery.data?.warnings ?? []).map((warning) => <p className="path-demo-note" key={warning}>{warning}</p>)}

        <PathPlaybackController
          state={playback.state}
          canPlay={Boolean(plan?.result_status === 'success' && plan.estimated_duration_seconds > 0)}
          onStart={playback.start}
          onPause={playback.pause}
          onResume={playback.resume}
          onReset={playback.reset}
          onRateChange={playback.setRate}
        />
        <section className="path-demo-camera-controls" aria-label="四向车视角控制">
          <div className="path-demo-camera-heading"><strong>视角控制</strong><span className={`path-demo-view-badge is-${cameraViewMode}`}>当前视角：{cameraViewMode === 'follow' ? '跟随' : '自由'}</span></div>
          <div className="path-demo-camera-buttons">
            <button className="ghost-button" type="button" disabled={!sample} onClick={() => { setCameraViewMode('free'); requestCamera('locate') }}>定位四向车</button>
            <button className={cameraViewMode === 'follow' ? 'accent-button' : 'ghost-button'} type="button" disabled={!sample} onClick={() => setCameraViewMode((mode) => mode === 'follow' ? 'free' : 'follow')}>跟随四向车</button>
            <button className="ghost-button" type="button" onClick={() => setCameraViewMode('free')}>自由视角</button>
            <button className="ghost-button" type="button" onClick={() => { setCameraViewMode('free'); requestCamera('overview') }}>仓库全景</button>
            <button className="ghost-button" type="button" disabled={!lifts.some((lift) => lift.lift_id === 'shuttle-lift-a')} onClick={() => focusLift('shuttle-lift-a')}>查看 Lift-A</button>
            <button className="ghost-button" type="button" disabled={!lifts.some((lift) => lift.lift_id === 'shuttle-lift-b')} onClick={() => focusLift('shuttle-lift-b')}>查看 Lift-B</button>
          </div>
        </section>

        <dl className="path-demo-stats shuttle-stats">
          <div><dt>当前轨道层</dt><dd>{sample ? `L${sample.shuttleLevel}` : `L${startLevel}`}</dd></div>
          <div><dt>当前轨道节点</dt><dd>{sample?.trackNodeId ?? (startNodeId || '—')}</dd></div>
          <div><dt>运行状态</dt><dd>{sample ? shuttleMotionLabel[sample.phase] : '待规划'}</dd></div>
          <div><dt>使用提升机</dt><dd>{plan?.chosen_lift ?? '—'}</dd></div>
          <div><dt>水平路径长度</dt><dd>{plan ? `${plan.horizontal_distance_cells.toFixed(0)} 格` : '—'}</dd></div>
          <div><dt>预计仿真耗时</dt><dd>{plan ? `${plan.estimated_duration_seconds.toFixed(2)} s` : '—'}</dd></div>
          <div><dt>当前阶段</dt><dd>{sample ? shuttleMotionLabel[sample.phase] : '—'}</dd></div>
          <div><dt>演示时间</dt><dd>{playback.state.elapsedSeconds.toFixed(2)} / {(plan?.estimated_duration_seconds ?? 0).toFixed(2)} s</dd></div>
        </dl>
        <p className="path-demo-note">提升机容量为 1；本 Gate 仅实现单车离线跨层回放，尚未实现多车提升机资源预约。</p>
      </aside>

      <div className="path-demo-viewport" aria-label="四向车三层轨道与提升机 3D 演示">
        <Suspense fallback={<div className="path-demo-empty">正在加载四向车 3D 场景…</div>}>
          <PathDemo3DScene
            topology={topologyRecord.payload}
            activeFloor="1F"
            trajectory={[]}
            sample={null}
            playbackStatus={playback.state.status}
            cameraViewMode={cameraViewMode}
            cameraActionRequest={cameraActionRequest}
            followedVehicleSample={cameraSample}
            shuttleVisualization={{ tracks, lifts, plan, sample, selectedLiftId }}
          />
        </Suspense>
      </div>
    </div>
  )
}
