import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import type { StoredRecord, TopologyPayload } from '../../types'
import { api } from '../../lib/api'
import { PathPlaybackController } from '../path-demo/PathPlaybackController'
import type { CameraActionRequest, CameraViewMode } from '../path-demo/cameraControls'
import { usePathPlayback } from '../path-demo/pathPlayback'
import type { PathDemoAlgorithm } from '../path-demo/types'
import { sampleShuttlePlan, SHUTTLE_VEHICLE_CENTER_HEIGHT, shuttleCameraSample, shuttleMotionLabel } from './shuttlePlayback'
import { useShuttleWorkspaceStore } from './shuttleWorkspaceStore'
import type { ShuttleLevel, ShuttlePlanResponse, ShuttleTrackNode } from './types'

const PathDemo3DScene = lazy(() =>
  import('../path-demo/PathDemo3DScene').then((module) => ({ default: module.PathDemo3DScene })),
)

type WorkspaceMode = 'planning' | 'inventory'
type VehicleType = 'ctu' | 'shuttle'
const SHUTTLE_LEVELS: ShuttleLevel[] = [1, 2, 3, 4]
const FOUR_LEVEL_CONFIG_REVISION = 'shuttle-4l-lift-v2'
const INTERNAL_HEIGHT_CONTRACT_REVISION = 'shuttle-internal-levels-v1'

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
  onVehicleTypeChange,
}: {
  topologyRecord: StoredRecord<TopologyPayload>
  topologies: Array<StoredRecord<TopologyPayload>>
  onTopologyChange: (topologyId: string) => void
  onWorkspaceModeChange: (mode: WorkspaceMode) => void
  onVehicleTypeChange: (vehicleType: VehicleType) => void
}) {
  const topologyId = topologyRecord.id
  const topologyVersion = topologyRecord.payload.meta.version
  const savedDraft = useShuttleWorkspaceStore.getState().drafts[topologyId]
  const initialDraft = savedDraft?.topologyVersion === topologyVersion ? savedDraft : null
  const saveDraft = useShuttleWorkspaceStore((state) => state.saveDraft)
  const levelsQuery = useQuery({
    queryKey: ['shuttle-levels', topologyId], queryFn: () => api.getShuttleLevels(topologyId),
  })
  const trackQueries = useQueries({
    queries: SHUTTLE_LEVELS.map((level) => ({
      queryKey: ['shuttle-tracks', topologyId, level],
      queryFn: () => api.getShuttleTracks(topologyId, level),
    })),
  })
  const liftsQuery = useQuery({
    queryKey: ['shuttle-lifts', topologyId], queryFn: () => api.getShuttleLifts(topologyId),
  })
  const tracks = useMemo(
    () => trackQueries.flatMap((query) => query.data?.nodes ?? []),
    [trackQueries],
  )
  const lifts = useMemo(() => liftsQuery.data?.lifts ?? [], [liftsQuery.data])
  const [startLevel, setStartLevel] = useState<ShuttleLevel>(initialDraft?.startLevel ?? 1)
  const [endLevel, setEndLevel] = useState<ShuttleLevel>(initialDraft?.endLevel ?? 4)
  const [startNodeId, setStartNodeId] = useState(initialDraft?.startNodeId ?? '')
  const [endNodeId, setEndNodeId] = useState(initialDraft?.endNodeId ?? '')
  const [algorithm, setAlgorithm] = useState<PathDemoAlgorithm>(initialDraft?.algorithm ?? 'astar')
  const [stateTopologyId, setStateTopologyId] = useState(topologyId)
  const [plan, setPlan] = useState<ShuttlePlanResponse | null>(null)
  const [planning, setPlanning] = useState(false)
  const [error, setError] = useState('')
  const [cameraViewMode, setCameraViewMode] = useState<CameraViewMode>('free')
  const [cameraActionRequest, setCameraActionRequest] = useState<CameraActionRequest | null>(null)
  const [selectedLiftId, setSelectedLiftId] = useState<string | null>(null)
  const [rackXray, setRackXray] = useState(false)
  const requestRef = useRef(0)
  const cameraSequenceRef = useRef(0)

  useEffect(() => {
    const nextDraft = useShuttleWorkspaceStore.getState().drafts[topologyId]
    const validDraft = nextDraft?.topologyVersion === topologyVersion ? nextDraft : null
    requestRef.current += 1
    setPlan(null)
    setError('')
    setPlanning(false)
    setCameraViewMode('free')
    setCameraActionRequest(null)
    setSelectedLiftId(null)
    setRackXray(false)
    setStartLevel(validDraft?.startLevel ?? 1)
    setEndLevel(validDraft?.endLevel ?? 4)
    setStartNodeId(validDraft?.startNodeId ?? '')
    setEndNodeId(validDraft?.endNodeId ?? '')
    setAlgorithm(validDraft?.algorithm ?? 'astar')
    setStateTopologyId(topologyId)
  }, [topologyId, topologyVersion])

  useEffect(() => {
    if (stateTopologyId !== topologyId) return
    saveDraft(topologyId, { topologyVersion, startLevel, endLevel, startNodeId, endNodeId, algorithm })
  }, [algorithm, endLevel, endNodeId, saveDraft, startLevel, startNodeId, stateTopologyId, topologyId, topologyVersion])

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

  const startNodes = nodesAtLevel(tracks, startLevel)
  const endNodes = nodesAtLevel(tracks, endLevel)
  const selectedStartNodeId = startNodes.some((node) => node.track_node_id === startNodeId) ? startNodeId : ''
  const selectedEndNodeId = endNodes.some((node) => node.track_node_id === endNodeId) ? endNodeId : ''

  const changeStartLevel = (nextLevel: ShuttleLevel) => {
    setStartLevel(nextLevel)
    setStartNodeId(preferredNode(tracks, nextLevel, `L${nextLevel}-r100-c7`))
    invalidatePlan()
  }
  const changeEndLevel = (nextLevel: ShuttleLevel) => {
    setEndLevel(nextLevel)
    setEndNodeId(preferredNode(tracks, nextLevel, `L${nextLevel}-r100-c51`, true))
    invalidatePlan()
  }

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
    const levelKey = `L${startLevel}` as keyof NonNullable<typeof lift>['level_entrances']
    const entrance = lift ? trackLookup.get(lift.level_entrances[levelKey]) : null
    if (!entrance) return
    setSelectedLiftId(liftId)
    setCameraViewMode('free')
    requestCamera('focus-point', [entrance.world_x, entrance.world_y + SHUTTLE_VEHICLE_CENTER_HEIGHT, entrance.world_z])
  }
  const requestPlan = async () => {
    const sequence = requestRef.current + 1
    requestRef.current = sequence
    setPlanning(true)
    setError('')
    try {
      const next = await api.planShuttlePath({
        topology_id: topologyId,
        start_track_node: selectedStartNodeId,
        start_level: startLevel,
        end_track_node: selectedEndNodeId,
        end_level: endLevel,
        algorithm,
      })
      if (requestRef.current !== sequence) return
      if (
        next.config_revision !== FOUR_LEVEL_CONFIG_REVISION
        || next.coordinate_contract_revision !== INTERNAL_HEIGHT_CONTRACT_REVISION
      ) {
        setPlan(null)
        setError('规划结果使用了旧版四向车坐标契约，请重启后端后重新规划。')
        return
      }
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
  const trackQueryErrorIndex = trackQueries.findIndex((query) => query.isError)
  const trackQueryError = trackQueryErrorIndex >= 0 ? trackQueries[trackQueryErrorIndex].error : null
  const configRevisions = [
    levelsQuery.data?.config_revision,
    liftsQuery.data?.config_revision,
    ...trackQueries.map((query) => query.data?.config_revision),
  ].filter((revision): revision is string => Boolean(revision))
  const coordinateContractRevisions = [
    levelsQuery.data?.coordinate_contract_revision,
    liftsQuery.data?.coordinate_contract_revision,
    ...trackQueries.map((query) => query.data?.coordinate_contract_revision),
  ].filter((revision): revision is 'shuttle-internal-levels-v1' => Boolean(revision))
  const contractQueriesSettled = Boolean(levelsQuery.data && liftsQuery.data)
    && trackQueries.every((query) => query.isSuccess)
  const hasFourLevelContract = configRevisions.length === 6
    && configRevisions.every((revision) => revision === FOUR_LEVEL_CONFIG_REVISION)
    && coordinateContractRevisions.length === 6
    && coordinateContractRevisions.every((revision) => revision === INTERNAL_HEIGHT_CONTRACT_REVISION)
  const planningEnabled = Boolean(
    levelsQuery.data?.planning_enabled
    && liftsQuery.data?.planning_enabled
    && hasFourLevelContract
    && startNodes.length
    && endNodes.length,
  )
  const queryError = [levelsQuery.error, trackQueryError, liftsQuery.error].find(Boolean)
  const versionMismatch = contractQueriesSettled && !hasFourLevelContract
  const startTrackQuery = trackQueries[startLevel - 1]
  const endTrackQuery = trackQueries[endLevel - 1]

  return (
    <div className="path-demo-layout">
      <aside className="path-demo-panel shuttle-demo-panel">
        <label>
          拓扑
          <select value={topologyId} onChange={(event) => onTopologyChange(event.target.value)}>
            {topologies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <div className="path-demo-workspace-tabs" role="tablist" aria-label="工作台模式">
          <button type="button" role="tab" aria-selected className="active">路径规划</button>
          <button type="button" role="tab" aria-selected={false} onClick={() => { invalidatePlan(); onWorkspaceModeChange('inventory') }}>库存查看</button>
        </div>
        <section className="path-demo-secondary-entry" aria-label="CTU 次级入口">
          <div><strong>四向车路径规划</strong><span>当前主工作台 · 同层与跨层自动判断</span></div>
          <button type="button" className="ghost-button" onClick={() => { invalidatePlan(); onVehicleTypeChange('ctu') }}>进入 CTU 平面道路与多车实验</button>
        </section>

        <section className="shuttle-contract-card" aria-label="四向车轨道契约">
          <strong>四层真实轨道 · 双提升机</strong>
          <span>数据：slotAisles · 配置：{levelsQuery.data?.config_revision ?? '读取中'}</span>
          <span className={planningEnabled ? 'is-verified' : 'is-blocked'}>
            {levelsQuery.data?.verification_status ?? '正在校验'}
          </span>
          <span>程序验证候选：{levelsQuery.data?.lift_candidate_count ?? '—'} 处</span>
          <p>轨道层 L1/L2/L3/L4 不等同于建筑楼层或 CTU 货架层。</p>
          <p>每层轨道严格来自 slotAisles 的 bit0–bit3；跨层边仅由程序验证的 Lift-A / Lift-B 提供。</p>
        </section>

        <div className="shuttle-level-grid">
          <label>起点层
            <select aria-label="四向车起点层" value={startLevel} onChange={(event) => changeStartLevel(Number(event.target.value) as ShuttleLevel)}>
              {SHUTTLE_LEVELS.map((level) => <option key={level} value={level}>L{level}</option>)}
            </select>
          </label>
          <label>终点层
            <select aria-label="四向车终点层" value={endLevel} onChange={(event) => changeEndLevel(Number(event.target.value) as ShuttleLevel)}>
              {SHUTTLE_LEVELS.map((level) => <option key={level} value={level}>L{level}</option>)}
            </select>
          </label>
        </div>
        <label>起点轨道节点
          <select aria-label="四向车起点轨道节点" value={selectedStartNodeId} disabled={startTrackQuery.isPending || startTrackQuery.isError || startNodes.length === 0} onChange={(event) => { setStartNodeId(event.target.value); invalidatePlan() }}>
            {!selectedStartNodeId ? <option value="">{startTrackQuery.isPending ? `正在读取 L${startLevel} 轨道节点…` : startTrackQuery.isError ? `L${startLevel} 轨道接口读取失败` : `当前层无可用轨道节点`}</option> : null}
            {startNodes.map((node) => <option key={node.track_node_id} value={node.track_node_id}>{node.track_node_id}</option>)}
          </select>
        </label>
        <label>终点轨道节点
          <select aria-label="四向车终点轨道节点" value={selectedEndNodeId} disabled={endTrackQuery.isPending || endTrackQuery.isError || endNodes.length === 0} onChange={(event) => { setEndNodeId(event.target.value); invalidatePlan() }}>
            {!selectedEndNodeId ? <option value="">{endTrackQuery.isPending ? `正在读取 L${endLevel} 轨道节点…` : endTrackQuery.isError ? `L${endLevel} 轨道接口读取失败` : `当前层无可用轨道节点`}</option> : null}
            {endNodes.map((node) => <option key={node.track_node_id} value={node.track_node_id}>{node.track_node_id}</option>)}
          </select>
        </label>
        <label>路径算法
          <select aria-label="四向车路径算法" value={algorithm} onChange={(event) => { setAlgorithm(event.target.value as PathDemoAlgorithm); invalidatePlan() }}>
            <option value="astar">A*</option><option value="dijkstra">Dijkstra</option>
          </select>
        </label>
        <button className="accent-button path-demo-plan-button" type="button" disabled={!planningEnabled || planning || !selectedStartNodeId || !selectedEndNodeId} onClick={() => void requestPlan()}>
          {planning ? '规划中…' : startLevel === endLevel ? '规划同层路径' : '规划跨层路径'}
        </button>
        {queryError ? <p className="path-demo-error" role="alert">{String(queryError)}</p> : null}
        {versionMismatch ? <p className="path-demo-error" role="alert">检测到旧版 Shuttle 服务（配置 {levelsQuery.data?.config_revision ?? '未知'}，坐标 {levelsQuery.data?.coordinate_contract_revision ?? '缺失'}）。请重启后端并确认加载 {FOUR_LEVEL_CONFIG_REVISION} / {INTERNAL_HEIGHT_CONTRACT_REVISION}，旧高度响应不会用于 L4 规划。</p> : null}
        {!queryError && !startTrackQuery.isPending && startNodes.length === 0 ? <p className="path-demo-error" role="alert">L{startLevel} 未返回可用轨道节点，无法选择起点。</p> : null}
        {!queryError && !endTrackQuery.isPending && endNodes.length === 0 ? <p className="path-demo-error" role="alert">L{endLevel} 未返回可用轨道节点，无法选择终点。</p> : null}
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
            <button className={rackXray ? 'accent-button' : 'ghost-button'} type="button" aria-pressed={rackXray} onClick={() => setRackXray((enabled) => !enabled)}>透视货架：{rackXray ? '开' : '关'}</button>
          </div>
        </section>

        <dl className="path-demo-stats shuttle-stats">
          <div><dt>当前轨道层</dt><dd>{sample ? `L${sample.shuttleLevel}` : `L${startLevel}`}</dd></div>
          <div><dt>当前轨道节点</dt><dd>{sample?.trackNodeId ?? (selectedStartNodeId || '—')}</dd></div>
          <div><dt>运行状态</dt><dd>{sample ? shuttleMotionLabel[sample.phase] : '待规划'}</dd></div>
          <div><dt>使用提升机</dt><dd>{plan?.chosen_lift ?? '—'}</dd></div>
          <div><dt>水平路径长度</dt><dd>{plan ? `${plan.horizontal_distance_cells.toFixed(0)} 格` : '—'}</dd></div>
          <div><dt>预计仿真耗时</dt><dd>{plan ? `${plan.estimated_duration_seconds.toFixed(2)} s` : '—'}</dd></div>
          <div><dt>当前阶段</dt><dd>{sample ? shuttleMotionLabel[sample.phase] : '—'}</dd></div>
          <div><dt>演示时间</dt><dd>{playback.state.elapsedSeconds.toFixed(2)} / {(plan?.estimated_duration_seconds ?? 0).toFixed(2)} s</dd></div>
        </dl>
        <p className="path-demo-note">提升机容量为 1；本 Gate 仅实现单车离线跨层回放，尚未实现多车提升机资源预约。</p>
      </aside>

      <div className="path-demo-viewport" aria-label="四向车四层轨道与提升机 3D 演示">
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
            shuttleRackXray={rackXray}
          />
        </Suspense>
      </div>
    </div>
  )
}
