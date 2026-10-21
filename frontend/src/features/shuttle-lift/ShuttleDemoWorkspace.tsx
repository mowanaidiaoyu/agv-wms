import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import type { StoredRecord, TopologyPayload } from '../../types'
import { api } from '../../lib/api'
import { PathPlaybackController } from '../path-demo/PathPlaybackController'
import { PathDemoModeSelect } from '../path-demo/PathDemoModeSelect'
import type { PathDemoMode } from '../path-demo/PathDemoModeSelect'
import type { CameraActionRequest, CameraViewMode } from '../path-demo/cameraControls'
import { usePathPlayback } from '../path-demo/pathPlayback'
import type { PathDemoAlgorithm } from '../path-demo/types'
import { sampleAllShuttleVehicles, sampleShuttleLiftStates, shuttleMultiCameraSample, shuttleMultiMotionLabel } from './shuttleMultiPlayback'
import { sampleShuttlePlan, SHUTTLE_VEHICLE_CENTER_HEIGHT, shuttleCameraSample, shuttleMotionLabel } from './shuttlePlayback'
import { useShuttleWorkspaceStore } from './shuttleWorkspaceStore'
import type { ShuttleLevel, ShuttleMultiPlanResponse, ShuttleMultiVehicleRequest, ShuttlePlanResponse, ShuttleTrackNode } from './types'

const PathDemo3DScene = lazy(() => import('../path-demo/PathDemo3DScene').then((module) => ({ default: module.PathDemo3DScene })))
type WorkspaceMode = 'planning' | 'inventory'
type ScenarioId = 'parallel' | 'track-conflict' | 'lift-conflict'
type ShuttleDraft = ShuttleMultiVehicleRequest & { start_level: ShuttleLevel; target_level: ShuttleLevel }
const SHUTTLE_LEVELS: ShuttleLevel[] = [1, 2, 3, 4]
const FOUR_LEVEL_CONFIG_REVISION = 'shuttle-4l-lift-v2'
const INTERNAL_HEIGHT_CONTRACT_REVISION = 'shuttle-internal-levels-v1'

function nodesAtLevel(nodes: ShuttleTrackNode[], level: ShuttleLevel) {
  return nodes.filter((node) => node.shuttle_level === level)
}
function preferredNode(nodes: ShuttleTrackNode[], level: ShuttleLevel, preferredId: string, last = false) {
  const levelNodes = nodesAtLevel(nodes, level)
  return levelNodes.find((node) => node.track_node_id === preferredId)?.track_node_id ?? levelNodes[last ? levelNodes.length - 1 : 0]?.track_node_id ?? ''
}
function makeCustomVehicles(nodes: ShuttleTrackNode[]): ShuttleDraft[] {
  const values = [preferredNode(nodes, 1, 'L1-r100-c7'), preferredNode(nodes, 4, 'L4-r100-c51', true), preferredNode(nodes, 2, 'L2-r100-c51', true), preferredNode(nodes, 3, 'L3-r100-c7')]
  if (values.some((value) => !value)) return []
  return [
    { vehicle_id: 'SHUTTLE-01', start_track_node_id: values[0], target_track_node_id: values[1], start_level: 1, target_level: 4, priority: 1, algorithm: 'astar' },
    { vehicle_id: 'SHUTTLE-02', start_track_node_id: values[2], target_track_node_id: values[3], start_level: 2, target_level: 3, priority: 2, algorithm: 'astar' },
  ]
}
type CustomVehicleValidation = { error: string; targetErrors: string[] }

function validateCustomVehicles(vehicles: ShuttleDraft[], lookup: Map<string, ShuttleTrackNode>): CustomVehicleValidation {
  const targetErrors = vehicles.map(() => '')
  if (vehicles.length < 2 || vehicles.length > 4) return { error: '自定义多车数量必须为 2～4 辆。', targetErrors }
  if (new Set(vehicles.map((vehicle) => vehicle.vehicle_id.trim())).size !== vehicles.length) return { error: '车辆 ID 必须唯一。', targetErrors }
  if (new Set(vehicles.map((vehicle) => vehicle.start_track_node_id)).size !== vehicles.length) return { error: '多辆车不能占用相同起点。', targetErrors }
  for (const vehicle of vehicles) {
    const start = lookup.get(vehicle.start_track_node_id)
    const target = lookup.get(vehicle.target_track_node_id)
    if (!vehicle.vehicle_id.trim()) return { error: '车辆 ID 不能为空。', targetErrors }
    if (!start || !target) return { error: `${vehicle.vehicle_id} 的起点或终点不是当前真实 ShuttleGraph 节点。`, targetErrors }
    if (start.shuttle_level !== vehicle.start_level || target.shuttle_level !== vehicle.target_level) return { error: `${vehicle.vehicle_id} 的层级与轨道节点不一致。`, targetErrors }
    if (vehicle.start_track_node_id === vehicle.target_track_node_id) return { error: `${vehicle.vehicle_id} 的起点和终点不能相同。`, targetErrors }
  }
  const destinations = new Map<string, number[]>()
  vehicles.forEach((vehicle, index) => destinations.set(vehicle.target_track_node_id, [...(destinations.get(vehicle.target_track_node_id) ?? []), index]))
  for (const [trackNodeId, indexes] of destinations) {
    if (indexes.length < 2) continue
    const vehicleIds = indexes.map((index) => vehicles[index].vehicle_id)
    const message = `${vehicleIds.join(' 与 ')} 均选择 ${trackNodeId}。当前车辆到达后持续占用终点，请为这些车辆选择不同的合法终点。`
    indexes.forEach((index) => { targetErrors[index] = message })
  }
  return { error: targetErrors.find(Boolean) ?? '', targetErrors }
}

function multiFailureMessage(next: ShuttleMultiPlanResponse) {
  const failure = next.failure
  if (!failure) return next.failure_reason ?? '多车规划失败。'
  if (failure.failure_code === 'duplicate_terminal_destination') {
    const vehicleIds = Array.isArray(failure.details.vehicle_ids) ? failure.details.vehicle_ids.join(' 与 ') : '多辆四向车'
    const trackNodeId = typeof failure.details.track_node_id === 'string' ? failure.details.track_node_id : '同一终点'
    return `${vehicleIds} 均选择 ${trackNodeId}。当前车辆到达后持续占用终点，请为这些车辆选择不同的合法终点。`
  }
  if (failure.failure_code === 'terminal_geometry_conflict') return `不同终点的最终车体安全包围盒重叠。${failure.failure_reason}`
  if (failure.failure_code === 'search_limit_exceeded') {
    const states = failure.expanded_states === null || failure.max_expanded_states === null ? '' : `（${failure.expanded_states}/${failure.max_expanded_states} 状态）`
    return `${failure.vehicle_id ?? '车辆'}：当前搜索配置下未找到安全路径，已达到状态展开上限${states}。这不等于已经证明全局无解。`
  }
  if (failure.failure_code === 'unreachable_track') return `${failure.vehicle_id ?? '车辆'}：真实轨道不可达。${failure.failure_reason}`
  if (failure.failure_code === 'reservation_conflict_unresolved') return `${failure.vehicle_id ?? '车辆'}：时空预约冲突未能解决。${failure.failure_reason}`
  return failure.failure_reason
}
export function ShuttleDemoWorkspace({ topologyRecord, topologies, onTopologyChange, onWorkspaceModeChange, demoMode, onDemoModeChange }: {
  topologyRecord: StoredRecord<TopologyPayload>
  topologies: Array<StoredRecord<TopologyPayload>>
  onTopologyChange: (topologyId: string) => void
  onWorkspaceModeChange: (mode: WorkspaceMode) => void
  demoMode: PathDemoMode
  onDemoModeChange: (mode: PathDemoMode) => void
}) {
  const topologyId = topologyRecord.id
  const topologyVersion = topologyRecord.payload.meta.version
  const savedDraft = useShuttleWorkspaceStore.getState().drafts[topologyId]
  const initialDraft = savedDraft?.topologyVersion === topologyVersion ? savedDraft : null
  const saveDraft = useShuttleWorkspaceStore((state) => state.saveDraft)
  const levelsQuery = useQuery({ queryKey: ['shuttle-levels', topologyId], queryFn: () => api.getShuttleLevels(topologyId) })
  const trackQueries = useQueries({ queries: SHUTTLE_LEVELS.map((level) => ({ queryKey: ['shuttle-tracks', topologyId, level], queryFn: () => api.getShuttleTracks(topologyId, level) })) })
  const liftsQuery = useQuery({ queryKey: ['shuttle-lifts', topologyId], queryFn: () => api.getShuttleLifts(topologyId) })
  const scenariosQuery = useQuery({ queryKey: ['shuttle-multi-scenarios', topologyId], queryFn: () => api.getShuttleMultiScenarios(topologyId), enabled: demoMode === 'shuttle_multi_preset' })
  const tracks = useMemo(() => trackQueries.flatMap((query) => query.data?.nodes ?? []), [trackQueries])
  const lifts = useMemo(() => liftsQuery.data?.lifts ?? [], [liftsQuery.data])
  const trackLookup = useMemo(() => new Map(tracks.map((node) => [node.track_node_id, node])), [tracks])
  const [startLevel, setStartLevel] = useState<ShuttleLevel>(initialDraft?.startLevel ?? 1)
  const [endLevel, setEndLevel] = useState<ShuttleLevel>(initialDraft?.endLevel ?? 4)
  const [startNodeId, setStartNodeId] = useState(initialDraft?.startNodeId ?? '')
  const [endNodeId, setEndNodeId] = useState(initialDraft?.endNodeId ?? '')
  const [algorithm, setAlgorithm] = useState<PathDemoAlgorithm>(initialDraft?.algorithm ?? 'astar')
  const [singlePlan, setSinglePlan] = useState<ShuttlePlanResponse | null>(null)
  const [multiPlan, setMultiPlan] = useState<ShuttleMultiPlanResponse | null>(null)
  const [scenarioId, setScenarioId] = useState<ScenarioId>('parallel')
  const [customVehicles, setCustomVehicles] = useState<ShuttleDraft[]>([])
  const [selectedShuttleId, setSelectedShuttleId] = useState('SHUTTLE-01')
  const [planning, setPlanning] = useState(false)
  const [error, setError] = useState('')
  const [cameraViewMode, setCameraViewMode] = useState<CameraViewMode>('free')
  const [cameraActionRequest, setCameraActionRequest] = useState<CameraActionRequest | null>(null)
  const [selectedLiftId, setSelectedLiftId] = useState<string | null>(null)
  const [rackXray, setRackXray] = useState(false)
  const [configurationExpanded, setConfigurationExpanded] = useState(true)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [stateTopologyId, setStateTopologyId] = useState(topologyId)
  const requestRef = useRef(0)
  const cameraSequenceRef = useRef(0)
  const isSingle = demoMode === 'shuttle_single'
  const isPreset = demoMode === 'shuttle_multi_preset'
  const activeScenario = scenariosQuery.data?.scenarios.find((scenario) => scenario.scenario_id === scenarioId)
  const activeVehicleRequests = useMemo(
    () => isPreset ? activeScenario?.vehicle_requests ?? [] : customVehicles,
    [activeScenario, customVehicles, isPreset],
  )
  const customValidation = demoMode === 'shuttle_multi_custom' ? validateCustomVehicles(customVehicles, trackLookup) : { error: '', targetErrors: [] }
  const customValidationError = customValidation.error

  const invalidatePlan = () => {
    requestRef.current += 1
    setSinglePlan(null); setMultiPlan(null); setError(''); setCameraViewMode('free')
  }
  useEffect(() => {
    const nextDraft = useShuttleWorkspaceStore.getState().drafts[topologyId]
    const validDraft = nextDraft?.topologyVersion === topologyVersion ? nextDraft : null
    requestRef.current += 1
    setSinglePlan(null); setMultiPlan(null); setError(''); setPlanning(false); setCameraViewMode('free'); setCameraActionRequest(null); setSelectedLiftId(null); setRackXray(false)
    setStartLevel(validDraft?.startLevel ?? 1); setEndLevel(validDraft?.endLevel ?? 4); setStartNodeId(validDraft?.startNodeId ?? ''); setEndNodeId(validDraft?.endNodeId ?? ''); setAlgorithm(validDraft?.algorithm ?? 'astar'); setCustomVehicles([]); setStateTopologyId(topologyId)
  }, [topologyId, topologyVersion])
  useEffect(() => {
    if (stateTopologyId === topologyId) saveDraft(topologyId, { topologyVersion, startLevel, endLevel, startNodeId, endNodeId, algorithm })
  }, [algorithm, endLevel, endNodeId, saveDraft, startLevel, startNodeId, stateTopologyId, topologyId, topologyVersion])
  useEffect(() => {
    if (!tracks.length) return
    setStartNodeId((current) => nodesAtLevel(tracks, startLevel).some((node) => node.track_node_id === current) ? current : preferredNode(tracks, startLevel, `L${startLevel}-r100-c7`))
    setEndNodeId((current) => nodesAtLevel(tracks, endLevel).some((node) => node.track_node_id === current) ? current : preferredNode(tracks, endLevel, `L${endLevel}-r100-c51`, true))
    setCustomVehicles((current) => current.length ? current : makeCustomVehicles(tracks))
  }, [endLevel, startLevel, tracks])
  useEffect(() => {
    const available = multiPlan?.vehicles.map((vehicle) => vehicle.vehicle_id) ?? activeVehicleRequests.map((vehicle) => vehicle.vehicle_id)
    if (available.length) setSelectedShuttleId((current) => available.includes(current) ? current : available[0])
  }, [activeVehicleRequests, multiPlan])
  useEffect(() => {
    requestRef.current += 1
    setSinglePlan(null); setMultiPlan(null); setError(''); setPlanning(false)
  }, [demoMode])

  const startNodes = nodesAtLevel(tracks, startLevel)
  const endNodes = nodesAtLevel(tracks, endLevel)
  const selectedStartNodeId = startNodes.some((node) => node.track_node_id === startNodeId) ? startNodeId : ''
  const selectedEndNodeId = endNodes.some((node) => node.track_node_id === endNodeId) ? endNodeId : ''
  const duration = isSingle ? singlePlan?.estimated_duration_seconds ?? 0 : multiPlan?.metrics.makespan_seconds ?? 0
  const playback = usePathPlayback(duration, isSingle ? `${topologyId}:single:${singlePlan?.start?.track_node_id ?? ''}:${singlePlan?.destination?.track_node_id ?? ''}` : `${topologyId}:multi:${multiPlan?.planning_id ?? 'empty'}`)
  const singleSample = useMemo(() => sampleShuttlePlan(singlePlan, playback.state.elapsedSeconds, playback.state.status), [singlePlan, playback.state.elapsedSeconds, playback.state.status])
  const multiSamples = useMemo(() => sampleAllShuttleVehicles(multiPlan, playback.state.elapsedSeconds, playback.state.status), [multiPlan, playback.state.elapsedSeconds, playback.state.status])
  const selectedMultiSample = multiSamples.find((sample) => sample.vehicleId === selectedShuttleId) ?? null
  const cameraSample = useMemo(() => isSingle ? shuttleCameraSample(singleSample) : shuttleMultiCameraSample(selectedMultiSample), [isSingle, selectedMultiSample, singleSample])
  const liftStates = useMemo(() => sampleShuttleLiftStates(multiPlan, lifts, tracks, playback.state.elapsedSeconds), [lifts, multiPlan, playback.state.elapsedSeconds, tracks])
  const requestCamera = (type: CameraActionRequest['type'], targetWorld?: [number, number, number]) => { cameraSequenceRef.current += 1; setCameraActionRequest({ type, sequence: cameraSequenceRef.current, targetWorld }) }
  const focusLift = (liftId: string) => {
    const lift = lifts.find((item) => item.lift_id === liftId)
    const entrance = lift ? trackLookup.get(lift.level_entrances.L1) : null
    if (!entrance) return
    setSelectedLiftId(liftId); setCameraViewMode('free'); requestCamera('focus-point', [entrance.world_x, entrance.world_y + SHUTTLE_VEHICLE_CENTER_HEIGHT, entrance.world_z])
  }
  const contractRevisions = [levelsQuery.data?.config_revision, liftsQuery.data?.config_revision, ...trackQueries.map((query) => query.data?.config_revision)].filter(Boolean)
  const coordinateRevisions = [levelsQuery.data?.coordinate_contract_revision, liftsQuery.data?.coordinate_contract_revision, ...trackQueries.map((query) => query.data?.coordinate_contract_revision)].filter(Boolean)
  const settled = Boolean(levelsQuery.data && liftsQuery.data) && trackQueries.every((query) => query.isSuccess)
  const hasContract = contractRevisions.length === 6 && contractRevisions.every((revision) => revision === FOUR_LEVEL_CONFIG_REVISION) && coordinateRevisions.length === 6 && coordinateRevisions.every((revision) => revision === INTERNAL_HEIGHT_CONTRACT_REVISION)
  const planningEnabled = Boolean(levelsQuery.data?.planning_enabled && liftsQuery.data?.planning_enabled && hasContract && tracks.length)
  const validateMultiResponse = (next: ShuttleMultiPlanResponse) => {
    if (next.topology_id !== topologyId || next.topology_version !== topologyVersion) return '多车结果与当前拓扑版本不一致，已拒绝播放。'
    if (next.topology_revision !== levelsQuery.data?.topology_revision || next.shuttle_config_revision !== FOUR_LEVEL_CONFIG_REVISION) return '多车结果使用了失效的拓扑或 Shuttle 配置版本。'
    if (next.coordinate_contract_version !== INTERNAL_HEIGHT_CONTRACT_REVISION || !next.planning_algorithm_version) return '多车结果缺少受支持的坐标或算法版本。'
    if (next.status !== 'planning_success') return multiFailureMessage(next)
    if (!next.safety_validation.safe || next.metrics.unresolved_conflict_count > 0 || next.unresolved_conflicts.length) return '最终安全校验仍有残留冲突，已拒绝播放。'
    return ''
  }
  const requestPlan = async () => {
    const sequence = requestRef.current + 1
    requestRef.current = sequence; setPlanning(true); setError(''); setSinglePlan(null); setMultiPlan(null); playback.reset()
    try {
      if (isSingle) {
        const next = await api.planShuttlePath({ topology_id: topologyId, start_track_node: selectedStartNodeId, start_level: startLevel, end_track_node: selectedEndNodeId, end_level: endLevel, algorithm })
        if (requestRef.current !== sequence) return
        if (next.config_revision !== FOUR_LEVEL_CONFIG_REVISION || next.coordinate_contract_revision !== INTERNAL_HEIGHT_CONTRACT_REVISION) throw new Error('规划结果使用了旧版四向车坐标契约，请重启后端后重新规划。')
        if (next.result_status !== 'success') throw new Error(next.failure_reason ?? '四向车路径规划失败。')
        setSinglePlan(next); setSelectedLiftId(next.chosen_lift)
      } else {
        if (isPreset && (!scenariosQuery.data || scenariosQuery.data.topology_revision !== levelsQuery.data?.topology_revision || scenariosQuery.data.shuttle_config_revision !== FOUR_LEVEL_CONFIG_REVISION)) throw new Error('预设场景版本与当前拓扑不一致，请重新加载。')
        if (!activeVehicleRequests.length || customValidationError) throw new Error(customValidationError || (isPreset ? '预设场景尚未加载。' : '请先配置车辆。'))
        const next = await api.planShuttleMulti({ topology_id: topologyId, topology_revision: levelsQuery.data!.topology_revision, shuttle_config_revision: FOUR_LEVEL_CONFIG_REVISION, coordinate_contract_version: INTERNAL_HEIGHT_CONTRACT_REVISION, vehicle_requests: activeVehicleRequests.map(({ vehicle_id, start_track_node_id, target_track_node_id, priority, algorithm: vehicleAlgorithm }) => ({ vehicle_id, start_track_node_id, target_track_node_id, priority, algorithm: vehicleAlgorithm })) })
        if (requestRef.current !== sequence) return
        const responseError = validateMultiResponse(next)
        if (responseError) throw new Error(responseError)
        setMultiPlan(next)
      }
    } catch (caught) {
      if (requestRef.current === sequence) { setSinglePlan(null); setMultiPlan(null); setError(caught instanceof Error ? caught.message : String(caught)) }
    } finally { if (requestRef.current === sequence) setPlanning(false) }
  }
  const updateCustom = (vehicleId: string, patch: Partial<ShuttleDraft>) => { setCustomVehicles((current) => current.map((vehicle) => vehicle.vehicle_id === vehicleId ? { ...vehicle, ...patch } : vehicle)); invalidatePlan() }
  const addVehicle = () => {
    if (customVehicles.length >= 4) return
    const index = customVehicles.length + 1; const level = ((index - 1) % 4 + 1) as ShuttleLevel; const targetLevel = ((index + 1) % 4 + 1) as ShuttleLevel
    setCustomVehicles((current) => [...current, { vehicle_id: `SHUTTLE-${String(index).padStart(2, '0')}`, start_track_node_id: preferredNode(tracks, level, `L${level}-r100-c7`), target_track_node_id: preferredNode(tracks, targetLevel, `L${targetLevel}-r100-c51`, true), start_level: level, target_level: targetLevel, priority: index, algorithm }]); invalidatePlan()
  }
  const queryError = [levelsQuery.error, liftsQuery.error, ...trackQueries.map((query) => query.error)].find(Boolean)
  const canPlan = planningEnabled && !planning && (isSingle ? Boolean(selectedStartNodeId && selectedEndNodeId) : Boolean(activeVehicleRequests.length && !customValidationError))

  return <div className={`path-demo-layout shuttle-unified-layout${sidebarCollapsed ? ' is-sidebar-collapsed' : ''}`}>
    <aside className="path-demo-panel shuttle-demo-panel">
      {sidebarCollapsed ? <button className="ghost-button shuttle-sidebar-rail" type="button" aria-label="展开配置侧栏" onClick={() => setSidebarCollapsed(false)}>☰</button> : <>
        <div className="shuttle-panel-heading"><strong>四向车路径规划</strong><button className="ghost-button" type="button" onClick={() => setSidebarCollapsed(true)}>收起侧栏</button></div>
        <label>拓扑<select value={topologyId} onChange={(event) => onTopologyChange(event.target.value)}>{topologies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <div className="path-demo-workspace-tabs" role="tablist" aria-label="工作台模式"><button type="button" role="tab" aria-selected className="active">路径规划</button><button type="button" role="tab" aria-selected={false} onClick={() => { invalidatePlan(); playback.reset(); onWorkspaceModeChange('inventory') }}>库存查看</button></div>
        <PathDemoModeSelect value={demoMode} onChange={(nextMode) => { invalidatePlan(); playback.reset(); onDemoModeChange(nextMode) }} />
        <button className="ghost-button shuttle-config-toggle" type="button" aria-expanded={configurationExpanded} onClick={() => setConfigurationExpanded((current) => !current)}>{configurationExpanded ? '收起路径配置' : '展开路径配置'}</button>
        {configurationExpanded ? <section className="shuttle-config-section" aria-label="四向车规划配置">
          {isSingle ? <label>路径算法<select aria-label="四向车路径算法" value={algorithm} onChange={(event) => { setAlgorithm(event.target.value as PathDemoAlgorithm); invalidatePlan() }}><option value="astar">A*</option><option value="dijkstra">Dijkstra</option></select></label> : null}
          {isSingle ? <><div className="shuttle-level-grid"><label>起点层<select aria-label="四向车起点层" value={startLevel} onChange={(event) => { const level = Number(event.target.value) as ShuttleLevel; setStartLevel(level); setStartNodeId(preferredNode(tracks, level, `L${level}-r100-c7`)); invalidatePlan() }}>{SHUTTLE_LEVELS.map((level) => <option key={level} value={level}>L{level}</option>)}</select></label><label>终点层<select aria-label="四向车终点层" value={endLevel} onChange={(event) => { const level = Number(event.target.value) as ShuttleLevel; setEndLevel(level); setEndNodeId(preferredNode(tracks, level, `L${level}-r100-c51`, true)); invalidatePlan() }}>{SHUTTLE_LEVELS.map((level) => <option key={level} value={level}>L{level}</option>)}</select></label></div>
            <label>起点轨道节点<select aria-label="四向车起点轨道节点" value={selectedStartNodeId} disabled={!startNodes.length} onChange={(event) => { setStartNodeId(event.target.value); invalidatePlan() }}>{!selectedStartNodeId ? <option value="">当前层无可用轨道节点</option> : null}{startNodes.map((node) => <option key={node.track_node_id}>{node.track_node_id}</option>)}</select></label>
            <label>终点轨道节点<select aria-label="四向车终点轨道节点" value={selectedEndNodeId} disabled={!endNodes.length} onChange={(event) => { setEndNodeId(event.target.value); invalidatePlan() }}>{!selectedEndNodeId ? <option value="">当前层无可用轨道节点</option> : null}{endNodes.map((node) => <option key={node.track_node_id}>{node.track_node_id}</option>)}</select></label></> : null}
          {isPreset ? <label>预设场景<select aria-label="四向车预设场景" value={scenarioId} disabled={!scenariosQuery.data} onChange={(event) => { setScenarioId(event.target.value as ScenarioId); invalidatePlan() }}>{scenariosQuery.data?.scenarios.map((scenario) => <option key={scenario.scenario_id} value={scenario.scenario_id}>{scenario.name}</option>)}</select><small>{activeScenario?.description}</small></label> : null}
          {demoMode === 'shuttle_multi_custom' ? <section className="shuttle-custom-list" aria-label="自定义四向车配置"><div className="shuttle-panel-heading"><strong>车辆配置（{customVehicles.length}/4）</strong><button className="ghost-button" type="button" disabled={customVehicles.length >= 4} onClick={addVehicle}>添加车辆</button></div>
            {customVehicles.map((vehicle, vehicleIndex) => <fieldset key={vehicle.vehicle_id} className={selectedShuttleId === vehicle.vehicle_id ? 'is-selected' : ''} onClick={() => setSelectedShuttleId(vehicle.vehicle_id)}><legend>{vehicle.vehicle_id}</legend><label>车辆 ID<input aria-label={`${vehicle.vehicle_id} 车辆 ID`} value={vehicle.vehicle_id} onChange={(event) => { const nextId = event.target.value; setCustomVehicles((current) => current.map((item) => item.vehicle_id === vehicle.vehicle_id ? { ...item, vehicle_id: nextId } : item)); if (selectedShuttleId === vehicle.vehicle_id) setSelectedShuttleId(nextId); invalidatePlan() }} /></label><div className="shuttle-level-grid"><label>起点层<select aria-label={`${vehicle.vehicle_id} 起点层`} value={vehicle.start_level} onChange={(event) => { const level = Number(event.target.value) as ShuttleLevel; updateCustom(vehicle.vehicle_id, { start_level: level, start_track_node_id: preferredNode(tracks, level, `L${level}-r100-c7`) }) }}>{SHUTTLE_LEVELS.map((level) => <option key={level} value={level}>L{level}</option>)}</select></label><label>终点层<select aria-label={`${vehicle.vehicle_id} 终点层`} value={vehicle.target_level} onChange={(event) => { const level = Number(event.target.value) as ShuttleLevel; updateCustom(vehicle.vehicle_id, { target_level: level, target_track_node_id: preferredNode(tracks, level, `L${level}-r100-c51`, true) }) }}>{SHUTTLE_LEVELS.map((level) => <option key={level} value={level}>L{level}</option>)}</select></label></div>
              <label>起点<select aria-label={`${vehicle.vehicle_id} 起点`} value={vehicle.start_track_node_id} onChange={(event) => updateCustom(vehicle.vehicle_id, { start_track_node_id: event.target.value })}>{nodesAtLevel(tracks, vehicle.start_level).map((node) => <option key={node.track_node_id}>{node.track_node_id}</option>)}</select></label><label className={customValidation.targetErrors[vehicleIndex] ? 'shuttle-field-error' : ''}>终点<select aria-label={`${vehicle.vehicle_id} 终点`} aria-invalid={Boolean(customValidation.targetErrors[vehicleIndex])} value={vehicle.target_track_node_id} onChange={(event) => updateCustom(vehicle.vehicle_id, { target_track_node_id: event.target.value })}>{nodesAtLevel(tracks, vehicle.target_level).map((node) => <option key={node.track_node_id}>{node.track_node_id}</option>)}</select>{customValidation.targetErrors[vehicleIndex] ? <small className="shuttle-inline-error" role="alert">{customValidation.targetErrors[vehicleIndex]}</small> : null}</label><div className="shuttle-level-grid"><label>优先级<input aria-label={`${vehicle.vehicle_id} 优先级`} type="number" min={1} max={100} value={vehicle.priority} onChange={(event) => updateCustom(vehicle.vehicle_id, { priority: Math.max(1, Math.min(100, Number(event.target.value) || 1)) })} /></label><label>路径算法<select aria-label={`${vehicle.vehicle_id} 路径算法`} value={vehicle.algorithm} onChange={(event) => updateCustom(vehicle.vehicle_id, { algorithm: event.target.value as PathDemoAlgorithm })}><option value="astar">A*</option><option value="dijkstra">Dijkstra</option></select></label></div><button className="ghost-button danger" type="button" disabled={customVehicles.length <= 2} onClick={() => { const next = customVehicles.filter((item) => item.vehicle_id !== vehicle.vehicle_id); setCustomVehicles(next); if (selectedShuttleId === vehicle.vehicle_id) setSelectedShuttleId(next[0]?.vehicle_id ?? ''); invalidatePlan() }}>删除车辆</button></fieldset>)}
            {customValidationError ? <p className="path-demo-error">{customValidationError}</p> : null}</section> : null}
          {!isSingle ? <details className="shuttle-advanced-config"><summary>高级参数</summary><span>时间片：{String(multiPlan?.time_contract.time_step_seconds ?? '由后端默认配置')} s</span><span>车辆上限：4</span><span>规划、安全距离及搜索边界由现有多车服务统一校验；前端不改写预约语义。</span></details> : null}
        </section> : null}
        {!configurationExpanded ? <p className="path-demo-note">配置已收起 · {isSingle ? `${selectedStartNodeId || '—'} → ${selectedEndNodeId || '—'}` : `${activeVehicleRequests.length} 辆四向车`}</p> : null}
        <button className="accent-button path-demo-plan-button" type="button" disabled={!canPlan} onClick={() => void requestPlan()}>{planning ? '规划中…' : isSingle ? (startLevel === endLevel ? '规划同层路径' : '规划跨层路径') : '开始多车规划'}</button>
        {queryError ? <p className="path-demo-error" role="alert">{String(queryError)}</p> : null}{scenariosQuery.isError ? <div><p className="path-demo-error" role="alert">{String(scenariosQuery.error)}</p><button className="ghost-button" type="button" onClick={() => void scenariosQuery.refetch()}>重新加载预设场景</button></div> : null}
        {settled && !hasContract ? <p className="path-demo-error" role="alert">检测到旧版 Shuttle 服务（配置 {levelsQuery.data?.config_revision ?? '未知'}）。请重启后端并确认加载 {FOUR_LEVEL_CONFIG_REVISION} / {INTERNAL_HEIGHT_CONTRACT_REVISION}。</p> : null}
        {!queryError && !startNodes.length && isSingle ? <p className="path-demo-error" role="alert">L{startLevel} 未返回可用轨道节点，无法选择起点。</p> : null}{!queryError && !endNodes.length && isSingle ? <p className="path-demo-error" role="alert">L{endLevel} 未返回可用轨道节点，无法选择终点。</p> : null}{error ? <p className="path-demo-error" role="alert">{error}</p> : null}{!planningEnabled && levelsQuery.data ? <p className="path-demo-error">当前轨道或提升机配置未通过程序验证，已禁止运行。</p> : null}
        <PathPlaybackController state={playback.state} canPlay={duration > 0} onStart={playback.start} onPause={playback.pause} onResume={playback.resume} onReset={playback.reset} onRateChange={playback.setRate} />
        <label className="shuttle-timeline">统一仿真时间轴<input aria-label="统一仿真时间轴" type="range" min={0} max={Math.max(duration, 0.25)} step={0.25} value={playback.state.elapsedSeconds} disabled={!duration} onChange={(event) => playback.seek(Number(event.target.value))} /><span>{playback.state.elapsedSeconds.toFixed(2)} / {duration.toFixed(2)} s</span></label>
        <section className="path-demo-camera-controls" aria-label="四向车视角控制"><div className="path-demo-camera-heading"><strong>视角控制</strong><span className={`path-demo-view-badge is-${cameraViewMode}`}>当前视角：{cameraViewMode === 'follow' ? '跟随' : '自由'}</span></div>{!isSingle ? <label>选择跟随车辆<select aria-label="选择跟随四向车" value={selectedShuttleId} disabled={!multiSamples.length} onChange={(event) => { setSelectedShuttleId(event.target.value); requestCamera(cameraViewMode === 'follow' ? 'switch-follow-target' : 'locate') }}>{multiPlan?.vehicles.map((vehicle) => <option key={vehicle.vehicle_id}>{vehicle.vehicle_id}</option>)}</select></label> : null}<div className="path-demo-camera-buttons"><button className="ghost-button" type="button" disabled={!cameraSample} onClick={() => { setCameraViewMode('free'); requestCamera('locate') }}>定位四向车</button><button className={cameraViewMode === 'follow' ? 'accent-button' : 'ghost-button'} type="button" disabled={!cameraSample} onClick={() => setCameraViewMode((mode) => mode === 'follow' ? 'free' : 'follow')}>跟随四向车</button><button className="ghost-button" type="button" onClick={() => setCameraViewMode('free')}>自由视角</button><button className="ghost-button" type="button" onClick={() => { setCameraViewMode('free'); requestCamera('overview') }}>仓库全景</button><button className="ghost-button" type="button" disabled={!lifts.some((lift) => lift.lift_id === 'shuttle-lift-a')} onClick={() => focusLift('shuttle-lift-a')}>查看 Lift-A</button><button className="ghost-button" type="button" disabled={!lifts.some((lift) => lift.lift_id === 'shuttle-lift-b')} onClick={() => focusLift('shuttle-lift-b')}>查看 Lift-B</button><button className={rackXray ? 'accent-button' : 'ghost-button'} type="button" aria-pressed={rackXray} onClick={() => setRackXray((enabled) => !enabled)}>透视货架：{rackXray ? '开' : '关'}</button></div></section>
        {multiPlan ? <><dl className="path-demo-stats shuttle-stats"><div><dt>规划结果</dt><dd>{multiPlan.safety_validation.safe ? '成功（零残留冲突）' : '失败（存在残留冲突）'}</dd></div><div><dt>成功车辆</dt><dd>{multiPlan.metrics.planned_vehicle_count} / {multiPlan.metrics.requested_vehicle_count}</dd></div><div><dt>Makespan</dt><dd>{multiPlan.metrics.makespan_seconds.toFixed(2)} s</dd></div><div><dt>总等待</dt><dd>{multiPlan.metrics.total_wait_time_seconds.toFixed(2)} s</dd></div><div><dt>平均等待</dt><dd>{multiPlan.metrics.average_wait_time_seconds.toFixed(2)} s</dd></div><div><dt>最终未解决冲突</dt><dd>{multiPlan.metrics.unresolved_conflict_count}</dd></div><div><dt>Lift-A 使用</dt><dd>{multiPlan.metrics.lift_a_usage_count} 次 / {multiPlan.metrics.lift_a_occupied_seconds.toFixed(2)} s</dd></div><div><dt>Lift-B 使用</dt><dd>{multiPlan.metrics.lift_b_usage_count} 次 / {multiPlan.metrics.lift_b_occupied_seconds.toFixed(2)} s</dd></div></dl>
          <div className="shuttle-vehicle-status-list" aria-label="四向车实时状态">{multiPlan.vehicles.map((vehicle) => { const current = multiSamples.find((sample) => sample.vehicleId === vehicle.vehicle_id); return <button type="button" key={vehicle.vehicle_id} className={selectedShuttleId === vehicle.vehicle_id ? 'is-selected' : ''} onClick={() => setSelectedShuttleId(vehicle.vehicle_id)}><strong>{vehicle.vehicle_id}</strong><span>{current ? `L${current.shuttleLevel} · ${current.trackNodeId || '轨道区间'}` : '尚未开始'}</span><span>{current ? shuttleMultiMotionLabel[current.phase] : '尚未开始'}{current?.liftId ? ` · ${current.liftId}` : ''}</span><span>等待 {current?.accumulatedWaitSeconds.toFixed(2) ?? '0.00'} / {vehicle.wait_time_seconds.toFixed(2)} s</span><span>到达 {vehicle.arrival_time_seconds.toFixed(2)} s</span></button> })}</div>
          <section className="shuttle-lift-status" aria-label="双提升机实时状态">{liftStates.map((lift) => <div key={lift.liftId}><strong>{lift.liftId === 'shuttle-lift-a' ? 'Lift-A' : 'Lift-B'}</strong><span>L{lift.currentLevel}{lift.targetLevel ? ` → L${lift.targetLevel}` : ''} · {lift.direction === 'up' ? '上行' : lift.direction === 'down' ? '下行' : '停止'}</span><span>{lift.status === 'repositioning_empty' ? '空载调位' : lift.status === 'occupied' ? `载运 ${lift.occupantVehicleId}` : lift.status === 'reserved' ? '已预约' : lift.status === 'entering' ? '车辆进入中' : lift.status === 'exiting' ? '车辆驶出中' : '空闲'}</span><span>预约：{lift.reservationStartSeconds === null ? '—' : `${lift.reservationStartSeconds.toFixed(2)}–${lift.reservationEndSeconds?.toFixed(2)} s`}</span></div>)}</section></> : <dl className="path-demo-stats shuttle-stats"><div><dt>当前轨道层</dt><dd>{singleSample ? `L${singleSample.shuttleLevel}` : `L${startLevel}`}</dd></div><div><dt>运行状态</dt><dd>{singleSample ? shuttleMotionLabel[singleSample.phase] : '待规划'}</dd></div><div><dt>使用提升机</dt><dd>{singlePlan?.chosen_lift ?? '—'}</dd></div><div><dt>水平距离</dt><dd>{singlePlan ? `${singlePlan.horizontal_distance_cells.toFixed(0)} 格` : '—'}</dd></div><div><dt>仿真时长</dt><dd>{singlePlan ? `${singlePlan.estimated_duration_seconds.toFixed(2)} s` : '—'}</dd></div></dl>}
        <details className="shuttle-contract-card"><summary><strong>四层真实轨道 · Lift-A / Lift-B</strong><span className={planningEnabled ? 'is-verified' : 'is-blocked'}> · 空间位置待人工复核</span></summary><span>轨道来源：slotAisles</span><span>配置版本：{levelsQuery.data?.config_revision ?? '读取中'}</span><span>坐标契约：{levelsQuery.data?.coordinate_contract_revision ?? '读取中'}</span><span>程序验证：{levelsQuery.data?.verification_status ?? '正在校验'}</span><span>运行时间来源：synthetic_demo</span><p>L1～L4 是 Shuttle 内部轨道层；单车路径、多车预约计划与已执行轨迹是不同对象。本工作台仅播放离线规划结果，不控制真实设备。</p></details>
      </>}
    </aside>
    <div className="path-demo-viewport" aria-label="四向车四层轨道与双提升机 3D 演示"><Suspense fallback={<div className="path-demo-empty">正在加载四向车 3D 场景…</div>}><PathDemo3DScene topology={topologyRecord.payload} activeFloor="1F" trajectory={[]} sample={null} playbackStatus={playback.state.status} cameraViewMode={cameraViewMode} cameraActionRequest={cameraActionRequest} followedVehicleSample={cameraSample} shuttleVisualization={{ tracks, lifts, plan: singlePlan, sample: singleSample, multiPlan, multiSamples, liftStates, selectedShuttleId, selectedLiftId }} shuttleRackXray={rackXray} /></Suspense></div>
  </div>
}
