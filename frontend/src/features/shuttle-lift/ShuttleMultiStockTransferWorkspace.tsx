import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { PathPlaybackController } from '../path-demo/PathPlaybackController'
import { PathDemoModeSelect } from '../path-demo/PathDemoModeSelect'
import type { CameraActionRequest, CameraViewMode } from '../path-demo/cameraControls'
import { usePathPlayback } from '../path-demo/pathPlayback'
import {
  sampleAllShuttleVehicles,
  sampleShuttleLiftStates,
  shuttleMultiCameraSample,
  shuttleMultiMotionLabel,
} from './shuttleMultiPlayback'
import type { ShuttleStockTransferWorkspaceProps } from './ShuttleStockTransferWorkspace'
import type {
  ShuttleDemoLocationInventory,
  ShuttleLevel,
  ShuttleMultiTransferBatch,
  ShuttleMultiTransferVehicleRequest,
} from './types'

const PathDemo3DScene = lazy(() => import('../path-demo/PathDemo3DScene').then((module) => ({ default: module.PathDemo3DScene })))
const LEVELS: ShuttleLevel[] = [1, 2, 3, 4]

type Draft = ShuttleMultiTransferVehicleRequest

function newIdempotencyKey() {
  return `shuttle-stock-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`
}

function balance(location: ShuttleDemoLocationInventory | undefined, skuId: string) {
  return location?.balances.find((item) => item.sku_id === skuId)
}

function initialDrafts(
  count: number,
  tracks: Array<{ track_node_id: string }>,
  locations: ShuttleDemoLocationInventory[],
  skuId: string,
): Draft[] {
  const sources = locations.filter((item) => (balance(item, skuId)?.quantity_available ?? 0) > 0)
  return Array.from({ length: count }, (_, index) => {
    const source = sources[index % Math.max(1, sources.length)]
    const destination = locations.find((item, candidateIndex) => (
      item.location_id !== source?.location_id && candidateIndex > index
    )) ?? locations[(index + 1) % Math.max(1, locations.length)]
    return {
      vehicle_id: `SHUTTLE-${String(index + 1).padStart(2, '0')}`,
      priority: index + 1,
      algorithm: 'astar',
      current_track_node_id: source?.access_track_node_id ?? tracks[index]?.track_node_id ?? '',
      source_location_id: source?.location_id ?? '',
      destination_location_id: destination?.location_id ?? '',
      sku_id: skuId,
      quantity: 1,
    }
  })
}

export function ShuttleMultiStockTransferWorkspace({
  topologyRecord,
  topologies,
  onTopologyChange,
  demoMode,
  onDemoModeChange,
  onReturnToPlanning,
  activeBatchId,
  onActiveBatchIdChange,
  onTransferModeChange,
}: ShuttleStockTransferWorkspaceProps) {
  const topologyId = topologyRecord.id
  const trackQueries = useQueries({ queries: LEVELS.map((level) => ({
    queryKey: ['shuttle-tracks', topologyId, level],
    queryFn: () => api.getShuttleTracks(topologyId, level),
  })) })
  const liftsQuery = useQuery({ queryKey: ['shuttle-lifts', topologyId], queryFn: () => api.getShuttleLifts(topologyId) })
  const skusQuery = useQuery({ queryKey: ['shuttle-demo-skus', topologyId], queryFn: () => api.listShuttleDemoSkus(topologyId) })
  const locationsQuery = useQuery({
    queryKey: ['shuttle-demo-multi-transfer-locations', topologyId],
    queryFn: () => api.listShuttleDemoInventoryLocations(topologyId, { transferEligible: true, limit: 5000 }),
  })
  const restoredBatchQuery = useQuery({
    queryKey: ['shuttle-demo-transfer-batch', activeBatchId],
    queryFn: () => api.getShuttleDemoTransferBatch(activeBatchId!),
    enabled: Boolean(activeBatchId),
    refetchOnWindowFocus: false,
  })
  const tracks = useMemo(() => trackQueries.flatMap((query) => query.data?.nodes ?? []), [trackQueries])
  const lifts = useMemo(() => liftsQuery.data?.lifts ?? [], [liftsQuery.data])
  const locations = useMemo(() => locationsQuery.data?.locations ?? [], [locationsQuery.data?.locations])
  const [vehicleCount, setVehicleCount] = useState(2)
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [batch, setBatch] = useState<ShuttleMultiTransferBatch | null>(null)
  const [selectedVehicleId, setSelectedVehicleId] = useState('SHUTTLE-01')
  const [submitting, setSubmitting] = useState(false)
  const [initializing, setInitializing] = useState(false)
  const [error, setError] = useState('')
  const [configurationExpanded, setConfigurationExpanded] = useState(true)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [rackXray, setRackXray] = useState(false)
  const [cameraViewMode, setCameraViewMode] = useState<CameraViewMode>('free')
  const [cameraActionRequest, setCameraActionRequest] = useState<CameraActionRequest | null>(null)
  const [recoveredReadOnly, setRecoveredReadOnly] = useState(false)
  const [autoStart, setAutoStart] = useState(false)
  const idempotencyKey = useRef(newIdempotencyKey())
  const requestSequence = useRef(0)
  const cameraSequence = useRef(0)
  const advanceInFlight = useRef(false)

  useEffect(() => {
    if (drafts.length || !tracks.length || !locations.length || !skusQuery.data?.length) return
    setDrafts(initialDrafts(vehicleCount, tracks, locations, skusQuery.data[0].sku_id))
  }, [drafts.length, locations, skusQuery.data, tracks, vehicleCount])

  const duration = batch?.plan?.metrics.makespan_seconds ?? 0
  const playback = usePathPlayback(duration, batch?.batch_id ?? `${topologyId}:multi-transfer-empty`)
  const samples = useMemo(
    () => sampleAllShuttleVehicles(batch?.plan ?? null, playback.state.elapsedSeconds, playback.state.status),
    [batch?.plan, playback.state.elapsedSeconds, playback.state.status],
  )
  const liftStates = useMemo(
    () => sampleShuttleLiftStates(batch?.plan ?? null, lifts, tracks, playback.state.elapsedSeconds),
    [batch?.plan, lifts, playback.state.elapsedSeconds, tracks],
  )
  const selectedSample = samples.find((item) => item.vehicleId === selectedVehicleId) ?? samples[0] ?? null
  const cameraSample = shuttleMultiCameraSample(selectedSample)
  const multiTransfers = useMemo(() => (batch?.tasks ?? []).flatMap((task) => {
    const source = locations.find((item) => item.location_id === task.source_location_id)
    const destination = locations.find((item) => item.location_id === task.destination_location_id)
    return source && destination ? [{
      vehicleId: task.vehicle_id,
      source,
      destination,
      skuId: task.sku_id,
      quantity: task.quantity,
      status: task.status,
      runtimePhase: task.runtime_phase,
      vehicleLoadQuantity: task.vehicle_load_quantity,
    }] : []
  }), [batch?.tasks, locations])

  useEffect(() => {
    const restored = restoredBatchQuery.data
    if (!restored || batch?.batch_id === restored.batch_id) return
    if (restored.topology_id !== topologyId) {
      setError(`批次 ${restored.batch_id} 不属于当前拓扑。`)
      return
    }
    setBatch(restored)
    setDrafts(restored.tasks.map((task) => ({
      vehicle_id: task.vehicle_id,
      priority: task.priority ?? 1,
      algorithm: task.algorithm,
      current_track_node_id: task.current_track_node_id,
      source_location_id: task.source_location_id,
      destination_location_id: task.destination_location_id,
      sku_id: task.sku_id,
      quantity: task.quantity,
    })))
    setVehicleCount(restored.tasks.length)
    setSelectedVehicleId(restored.tasks[0]?.vehicle_id ?? 'SHUTTLE-01')
    setRecoveredReadOnly(true)
    playback.reset()
  }, [batch?.batch_id, playback, restoredBatchQuery.data, topologyId])

  useEffect(() => {
    if (autoStart && duration > 0) {
      playback.start()
      setAutoStart(false)
    }
  }, [autoStart, duration, playback])

  useEffect(() => {
    if (recoveredReadOnly || !batch?.plan || batch.status !== 'running' || advanceInFlight.current) return
    const elapsed = playback.state.elapsedSeconds
    const due = batch.plan.vehicles.some((vehicle) => [
      vehicle.pickup_service_end_seconds,
      vehicle.drop_service_end_seconds,
    ].some((milestone) => milestone !== null && milestone !== undefined
      && milestone > batch.simulation_elapsed_seconds + 1e-9 && elapsed >= milestone - 1e-9))
    if (!due) return
    advanceInFlight.current = true
    void api.advanceShuttleDemoTransferBatch(batch.batch_id, elapsed)
      .then((next) => {
        setBatch(next)
        void Promise.all([locationsQuery.refetch(), skusQuery.refetch()])
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)))
      .finally(() => { advanceInFlight.current = false })
  }, [batch, locationsQuery, playback.state.elapsedSeconds, recoveredReadOnly, skusQuery])

  const changeDraft = (index: number, patch: Partial<Draft>) => {
    setDrafts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item))
    setBatch(null)
    setRecoveredReadOnly(false)
    setError('')
    onActiveBatchIdChange?.(null)
    idempotencyKey.current = newIdempotencyKey()
    playback.reset()
  }
  const changeCount = (count: number) => {
    setVehicleCount(count)
    const skuId = skusQuery.data?.[0]?.sku_id ?? ''
    setDrafts((current) => {
      if (current.length >= count) return current.slice(0, count)
      const defaults = initialDrafts(count, tracks, locations, skuId)
      return [...current, ...defaults.slice(current.length)]
    })
    idempotencyKey.current = newIdempotencyKey()
    setBatch(null)
    onActiveBatchIdChange?.(null)
    playback.reset()
  }

  const validationError = useMemo(() => {
    if (drafts.length !== vehicleCount || drafts.some((item) => !item.current_track_node_id || !item.source_location_id || !item.destination_location_id || !item.sku_id)) return '请完整配置每辆四向车。'
    if (new Set(drafts.map((item) => item.vehicle_id)).size !== drafts.length) return '车辆 ID 必须唯一。'
    if (new Set(drafts.map((item) => item.current_track_node_id)).size !== drafts.length) return '车辆起点不能同时占用同一轨道节点。'
    const terminalNodes = drafts.map((item) => locations.find((location) => location.location_id === item.destination_location_id)?.access_track_node_id)
    if (new Set(terminalNodes).size !== terminalNodes.length) return '车辆最终停靠轨道节点必须不同。'
    const required = new Map<string, number>()
    for (const item of drafts) {
      if (!Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 100) return `${item.vehicle_id} 搬运数量必须为 1～100。`
      const key = `${item.source_location_id}\u0000${item.sku_id}`
      required.set(key, (required.get(key) ?? 0) + item.quantity)
    }
    for (const [key, quantity] of required) {
      const [locationId, skuId] = key.split('\u0000')
      const available = balance(locations.find((item) => item.location_id === locationId), skuId)?.quantity_available ?? 0
      if (quantity > available) return `${locationId} 的 ${skuId} 共享可用库存不足：需要 ${quantity}，可用 ${available}。`
    }
    return ''
  }, [drafts, locations, vehicleCount])

  const initialize = async () => {
    setInitializing(true); setError('')
    try {
      await api.initializeShuttleDemoInventory(topologyId)
      await Promise.all([locationsQuery.refetch(), skusQuery.refetch()])
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)) }
    finally { setInitializing(false) }
  }
  const submit = async () => {
    if (validationError) return
    const sequence = ++requestSequence.current
    setSubmitting(true); setError(''); setBatch(null); setRecoveredReadOnly(false); playback.reset()
    try {
      const next = await api.createShuttleDemoTransferBatch({
        topology_id: topologyId,
        idempotency_key: idempotencyKey.current,
        vehicle_requests: drafts,
      })
      if (sequence !== requestSequence.current) return
      setBatch(next)
      onActiveBatchIdChange?.(next.batch_id)
      if (next.status === 'failed') setError(next.failure_reason ?? '多车协同搬运规划失败。')
      else setAutoStart(true)
    } catch (caught) {
      if (sequence === requestSequence.current) setError(caught instanceof Error ? caught.message : String(caught))
    } finally { if (sequence === requestSequence.current) setSubmitting(false) }
  }
  const cancel = async () => {
    if (!batch || !['reserved', 'planned', 'running'].includes(batch.status)) return
    try {
      const next = await api.cancelShuttleDemoTransferBatch(batch.batch_id)
      setBatch(next)
      playback.pause()
      await Promise.all([locationsQuery.refetch(), skusQuery.refetch()])
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)) }
  }
  const requestCamera = (type: CameraActionRequest['type']) => {
    cameraSequence.current += 1
    setCameraActionRequest({ type, sequence: cameraSequence.current })
  }

  return <div className={`path-demo-layout shuttle-unified-layout${sidebarCollapsed ? ' is-sidebar-collapsed' : ''}`}>
    <aside className="path-demo-panel shuttle-demo-panel shuttle-transfer-panel">
      {sidebarCollapsed ? <button className="ghost-button shuttle-sidebar-rail" type="button" aria-label="展开配置侧栏" onClick={() => setSidebarCollapsed(false)}>☰</button> : <>
        <div className="shuttle-panel-heading"><strong>SKU 货物搬运</strong><button className="ghost-button" type="button" onClick={onReturnToPlanning}>返回路径规划</button><button className="ghost-button" type="button" onClick={() => setSidebarCollapsed(true)}>收起侧栏</button></div>
        <label>拓扑<select value={topologyId} onChange={(event) => onTopologyChange(event.target.value)}>{topologies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <PathDemoModeSelect value={demoMode} onChange={onDemoModeChange} />
        <div className="shuttle-demo-badge">synthetic_demo · SQLite 事务库存</div>
        <div className="shuttle-transfer-mode-switch" role="group" aria-label="搬运车辆模式"><button className="ghost-button" type="button" onClick={() => onTransferModeChange?.('single')}>单车搬运</button><button className="accent-button" type="button">多车协同（2～4 辆）</button></div>
        <button className="ghost-button" type="button" disabled={initializing} onClick={() => void initialize()}>{initializing ? '正在初始化…' : '初始化模拟库存'}</button>
        <button className="ghost-button shuttle-config-toggle" type="button" aria-expanded={configurationExpanded} onClick={() => setConfigurationExpanded((value) => !value)}>{configurationExpanded ? '收起搬运配置' : '展开搬运配置'}</button>
        {configurationExpanded ? <section className="shuttle-config-section" aria-label="多车 SKU 搬运配置">
          <label>车辆数量<select aria-label="协同搬运车辆数量" value={vehicleCount} onChange={(event) => changeCount(Number(event.target.value))}>{[2, 3, 4].map((count) => <option key={count} value={count}>{count} 辆</option>)}</select></label>
          {drafts.map((draft, index) => {
            const sourceOptions = locations.filter((item) => (balance(item, draft.sku_id)?.quantity_available ?? 0) > 0)
            const source = locations.find((item) => item.location_id === draft.source_location_id)
            return <fieldset className="shuttle-transfer-vehicle-card" key={draft.vehicle_id}><legend>{draft.vehicle_id}</legend>
              <div className="shuttle-level-grid"><label>优先级<input aria-label={`${draft.vehicle_id} 优先级`} type="number" min={1} max={100} value={draft.priority} onChange={(event) => changeDraft(index, { priority: Number(event.target.value) })} /></label><label>算法<select aria-label={`${draft.vehicle_id} 算法`} value={draft.algorithm} onChange={(event) => changeDraft(index, { algorithm: event.target.value as Draft['algorithm'] })}><option value="astar">A*</option><option value="dijkstra">Dijkstra</option></select></label></div>
              <label>车辆起点<select aria-label={`${draft.vehicle_id} 车辆起点`} value={draft.current_track_node_id} onChange={(event) => changeDraft(index, { current_track_node_id: event.target.value })}>{tracks.map((node) => <option key={node.track_node_id} value={node.track_node_id}>{node.track_node_id}</option>)}</select></label>
              <label>SKU<select aria-label={`${draft.vehicle_id} SKU`} value={draft.sku_id} onChange={(event) => changeDraft(index, { sku_id: event.target.value, source_location_id: '' })}>{skusQuery.data?.map((sku) => <option key={sku.sku_id} value={sku.sku_id}>{sku.sku_id} · 可用 {sku.quantity_available}</option>)}</select></label>
              <label>取货货位<select aria-label={`${draft.vehicle_id} 取货货位`} value={draft.source_location_id} onChange={(event) => changeDraft(index, { source_location_id: event.target.value })}><option value="">请选择</option>{sourceOptions.map((item) => <option key={item.location_id} value={item.location_id}>{item.location_id} · 可用 {balance(item, draft.sku_id)?.quantity_available ?? 0}</option>)}</select></label>
              <label>送达货位<select aria-label={`${draft.vehicle_id} 送达货位`} value={draft.destination_location_id} onChange={(event) => changeDraft(index, { destination_location_id: event.target.value })}><option value="">请选择</option>{locations.filter((item) => item.location_id !== source?.location_id).map((item) => <option key={item.location_id} value={item.location_id}>{item.location_id} · L{item.level}</option>)}</select></label>
              <label>数量<input aria-label={`${draft.vehicle_id} 搬运数量`} type="number" min={1} max={100} value={draft.quantity} onChange={(event) => changeDraft(index, { quantity: Number(event.target.value) })} /></label>
            </fieldset>
          })}
        </section> : null}
        {validationError ? <p className="path-demo-error" role="alert">{validationError}</p> : null}
        <button className="accent-button path-demo-plan-button" type="button" disabled={Boolean(validationError) || submitting} onClick={() => void submit()}>{submitting ? '正在原子预留并规划…' : '规划并开始多车搬运'}</button>
        {error ? <p className="path-demo-error" role="alert">{error}</p> : null}
        {recoveredReadOnly ? <p className="path-demo-note" role="status">批次已从后端只读恢复；刷新不会再次预留库存，也不会重复 Pick / Drop。</p> : null}
        <PathPlaybackController state={playback.state} canPlay={duration > 0 && !recoveredReadOnly && batch?.status !== 'failed'} onStart={playback.start} onPause={playback.pause} onResume={playback.resume} onReset={playback.reset} onRateChange={playback.setRate} />
        {batch && ['reserved', 'planned', 'running'].includes(batch.status) ? <button className="ghost-button danger" type="button" onClick={() => void cancel()}>取消当前搬运批次</button> : null}
        {batch?.plan ? <dl className="path-demo-stats shuttle-transfer-stats"><div><dt>批次状态</dt><dd>{batch.status}</dd></div><div><dt>安全验证</dt><dd>{batch.plan.safety_validation.safe ? '通过' : '未通过'}</dd></div><div><dt>Makespan</dt><dd>{batch.plan.metrics.makespan_seconds.toFixed(2)} s</dd></div><div><dt>总等待</dt><dd>{batch.plan.metrics.total_wait_time_seconds.toFixed(2)} s</dd></div><div><dt>最终冲突</dt><dd>{batch.plan.metrics.unresolved_conflict_count}</dd></div><div><dt>服务时间</dt><dd>Pick {batch.pickup_service_seconds}s / Drop {batch.drop_service_seconds}s</dd></div></dl> : null}
        {batch?.tasks.map((task) => {
          const picked = task.ledger.some((item) => item.event_type === 'pick')
          const dropped = task.ledger.some((item) => item.event_type === 'drop')
          return <div className="shuttle-transfer-task-row" key={task.task_id}><strong>{task.vehicle_id}</strong><span>{task.runtime_phase ?? task.status}</span><span>{task.sku_id} × {task.quantity}</span><span>起点 {task.source_inventory.quantity_on_hand + (picked ? task.quantity : 0)} → {task.source_inventory.quantity_on_hand}</span><span>终点 {task.destination_inventory.quantity_on_hand - (dropped ? task.quantity : 0)} → {task.destination_inventory.quantity_on_hand}</span><span>载货 {task.vehicle_load_quantity}</span></div>
        })}
        {batch?.plan ? <><label>跟随车辆<select aria-label="选择搬运跟随车辆" value={selectedVehicleId} onChange={(event) => setSelectedVehicleId(event.target.value)}>{batch.plan.vehicles.map((vehicle) => <option key={vehicle.vehicle_id} value={vehicle.vehicle_id}>{vehicle.vehicle_id}</option>)}</select></label><p className="path-demo-note">当前状态：{selectedSample ? shuttleMultiMotionLabel[selectedSample.phase] : '待运行'}</p></> : null}
        <section className="path-demo-camera-controls"><div className="path-demo-camera-buttons"><button className="ghost-button" type="button" disabled={!cameraSample} onClick={() => requestCamera('locate')}>定位车辆</button><button className={cameraViewMode === 'follow' ? 'accent-button' : 'ghost-button'} type="button" disabled={!cameraSample} onClick={() => setCameraViewMode((value) => value === 'follow' ? 'free' : 'follow')}>跟随车辆</button><button className="ghost-button" type="button" onClick={() => { setCameraViewMode('free'); requestCamera('overview') }}>仓库全景</button><button className={rackXray ? 'accent-button' : 'ghost-button'} type="button" onClick={() => setRackXray((value) => !value)}>透视货架：{rackXray ? '开' : '关'}</button></div></section>
      </>}
    </aside>
    <div className="path-demo-viewport" aria-label="四向车多车 SKU 协同搬运 3D 演示"><Suspense fallback={<div className="path-demo-empty">正在加载四向车 3D 场景…</div>}><PathDemo3DScene topology={topologyRecord.payload} activeFloor="1F" trajectory={[]} sample={null} playbackStatus={playback.state.status} cameraViewMode={cameraViewMode} cameraActionRequest={cameraActionRequest} followedVehicleSample={cameraSample} shuttleVisualization={{ tracks, lifts, plan: null, sample: null, multiPlan: batch?.plan ?? null, multiSamples: samples, liftStates, selectedLiftId: null, selectedShuttleId: selectedVehicleId, transfer: null, multiTransfers }} shuttleRackXray={rackXray} /></Suspense></div>
  </div>
}
