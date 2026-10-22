import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import type { StoredRecord, TopologyPayload } from '../../types'
import { api } from '../../lib/api'
import { PathPlaybackController } from '../path-demo/PathPlaybackController'
import type { CameraActionRequest, CameraViewMode } from '../path-demo/cameraControls'
import { usePathPlayback } from '../path-demo/pathPlayback'
import type { PathDemoAlgorithm } from '../path-demo/types'
import { sampleShuttlePlan, shuttleCameraSample } from './shuttlePlayback'
import type {
  ShuttleDemoInventoryBalance,
  ShuttleDemoLocationInventory,
  ShuttleLevel,
  ShuttleTransferTask,
} from './types'

const PathDemo3DScene = lazy(() => import('../path-demo/PathDemo3DScene').then((module) => ({ default: module.PathDemo3DScene })))
const LEVELS: ShuttleLevel[] = [1, 2, 3, 4]

const statusLabel: Record<ShuttleTransferTask['status'], string> = {
  created: '已创建', reserved: '已预留', planned: '已规划', moving_to_pickup: '前往取货位',
  picked: '已取货', moving_to_dropoff: '运往目标货位', delivered: '已送达',
  failed: '失败', cancelled: '已取消',
}

function skuBalance(location: ShuttleDemoLocationInventory | null, skuId: string): ShuttleDemoInventoryBalance | null {
  return location?.balances.find((item) => item.sku_id === skuId) ?? null
}

export function ShuttleStockTransferWorkspace({
  topologyRecord,
  topologies,
  onTopologyChange,
  onWorkspaceModeChange,
}: {
  topologyRecord: StoredRecord<TopologyPayload>
  topologies: Array<StoredRecord<TopologyPayload>>
  onTopologyChange: (topologyId: string) => void
  onWorkspaceModeChange: (mode: 'planning' | 'inventory') => void
}) {
  const topologyId = topologyRecord.id
  const tracksQueries = useQueries({ queries: LEVELS.map((level) => ({
    queryKey: ['shuttle-tracks', topologyId, level],
    queryFn: () => api.getShuttleTracks(topologyId, level),
  })) })
  const liftsQuery = useQuery({ queryKey: ['shuttle-lifts', topologyId], queryFn: () => api.getShuttleLifts(topologyId) })
  const [skuSearch, setSkuSearch] = useState('')
  const normalizedSkuSearch = skuSearch.trim()
  const skusQuery = useQuery({
    queryKey: ['shuttle-demo-skus', topologyId, normalizedSkuSearch],
    queryFn: () => api.listShuttleDemoSkus(topologyId, normalizedSkuSearch || undefined),
  })
  const [selectedSkuId, setSelectedSkuId] = useState('')
  const [level, setLevel] = useState<'all' | ShuttleLevel>('all')
  const [sourceLocationId, setSourceLocationId] = useState('')
  const [destinationLocationId, setDestinationLocationId] = useState('')
  const [quantity, setQuantity] = useState(1)
  const [algorithm, setAlgorithm] = useState<PathDemoAlgorithm>('astar')
  const [task, setTask] = useState<ShuttleTransferTask | null>(null)
  const [before, setBefore] = useState<{ source: number; destination: number } | null>(null)
  const [initializing, setInitializing] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [configurationExpanded, setConfigurationExpanded] = useState(true)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [rackXray, setRackXray] = useState(false)
  const [cameraViewMode, setCameraViewMode] = useState<CameraViewMode>('free')
  const [cameraActionRequest, setCameraActionRequest] = useState<CameraActionRequest | null>(null)
  const [autoStart, setAutoStart] = useState(false)
  const requestSequence = useRef(0)
  const cameraSequence = useRef(0)
  const advanceInFlight = useRef(false)
  const tracks = useMemo(() => tracksQueries.flatMap((query) => query.data?.nodes ?? []), [tracksQueries])
  const lifts = useMemo(() => liftsQuery.data?.lifts ?? [], [liftsQuery.data])

  useEffect(() => {
    const ids = skusQuery.data?.map((sku) => sku.sku_id) ?? []
    setSelectedSkuId((current) => ids.includes(current) ? current : ids[0] ?? '')
  }, [skusQuery.data])

  const locationOptions = { level: level === 'all' ? undefined : level, transferEligible: true, limit: 5000 }
  const sourcesQuery = useQuery({
    queryKey: ['shuttle-demo-source-locations', topologyId, level, selectedSkuId],
    queryFn: () => api.listShuttleDemoInventoryLocations(topologyId, { ...locationOptions, skuId: selectedSkuId }),
    enabled: Boolean(selectedSkuId),
  })
  const destinationsQuery = useQuery({
    queryKey: ['shuttle-demo-destination-locations', topologyId, level],
    queryFn: () => api.listShuttleDemoInventoryLocations(topologyId, locationOptions),
    enabled: Boolean(selectedSkuId),
  })
  const sources = useMemo(() => sourcesQuery.data?.locations ?? [], [sourcesQuery.data?.locations])
  const destinations = useMemo(() => destinationsQuery.data?.locations ?? [], [destinationsQuery.data?.locations])
  useEffect(() => {
    if (task) return
    setSourceLocationId((current) => sources.some((item) => item.location_id === current)
      ? current : sources[0]?.location_id ?? '')
  }, [sources, task])
  useEffect(() => {
    if (task) return
    setDestinationLocationId((current) => destinations.some((item) => item.location_id === current && item.location_id !== sourceLocationId)
      ? current : destinations.find((item) => item.location_id !== sourceLocationId)?.location_id ?? '')
  }, [destinations, sourceLocationId, task])

  const source = sources.find((item) => item.location_id === sourceLocationId) ?? null
  const destination = destinations.find((item) => item.location_id === destinationLocationId) ?? null
  const sourceBalance = skuBalance(source, selectedSkuId)
  const destinationBalance = skuBalance(destination, selectedSkuId)
  const duration = task?.plan?.estimated_duration_seconds ?? 0
  const playback = usePathPlayback(duration, task?.task_id ?? `${topologyId}:transfer-empty`)
  const startPlayback = playback.start
  const sample = useMemo(
    () => sampleShuttlePlan(task?.plan ?? null, playback.state.elapsedSeconds, playback.state.status),
    [playback.state.elapsedSeconds, playback.state.status, task?.plan],
  )
  const cameraSample = useMemo(() => shuttleCameraSample(sample), [sample])

  useEffect(() => {
    if (autoStart && duration > 0) {
      startPlayback()
      setAutoStart(false)
    }
  }, [autoStart, duration, startPlayback])

  useEffect(() => {
    if (!task || advanceInFlight.current || task.pickup_at_seconds === null || task.dropoff_at_seconds === null) return
    let milestone: number | null = null
    if (['moving_to_pickup', 'planned', 'reserved'].includes(task.status)
      && playback.state.elapsedSeconds >= task.pickup_at_seconds) milestone = task.pickup_at_seconds
    if (['picked', 'moving_to_dropoff'].includes(task.status)
      && playback.state.elapsedSeconds >= task.dropoff_at_seconds) milestone = task.dropoff_at_seconds
    if (milestone === null) return
    advanceInFlight.current = true
    void api.advanceShuttleDemoTransfer(task.task_id, milestone)
      .then((next) => {
        setTask(next)
        void sourcesQuery.refetch()
        void destinationsQuery.refetch()
        void skusQuery.refetch()
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)))
      .finally(() => { advanceInFlight.current = false })
  }, [destinationsQuery, playback.state.elapsedSeconds, skusQuery, sourcesQuery, task])

  const clearTask = () => {
    requestSequence.current += 1
    setTask(null)
    setBefore(null)
    setError('')
    playback.reset()
  }

  const requestCamera = (type: CameraActionRequest['type'], targetWorld?: [number, number, number]) => {
    cameraSequence.current += 1
    setCameraActionRequest({ type, sequence: cameraSequence.current, targetWorld })
  }
  const initialize = async () => {
    setInitializing(true); setError('')
    try {
      await api.initializeShuttleDemoInventory(topologyId)
      await skusQuery.refetch()
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)) }
    finally { setInitializing(false) }
  }
  const submit = async () => {
    if (!source || !destination || !sourceBalance) return
    const sequence = requestSequence.current + 1
    requestSequence.current = sequence
    setSubmitting(true); setError(''); setTask(null); playback.reset()
    setBefore({ source: sourceBalance.quantity_on_hand, destination: destinationBalance?.quantity_on_hand ?? 0 })
    try {
      const currentTrack = tracks.some((node) => node.track_node_id === 'L1-r100-c7')
        ? 'L1-r100-c7' : tracks[0]?.track_node_id
      const created = await api.createShuttleDemoTransfer({
        topology_id: topologyId,
        source_location_id: source.location_id,
        destination_location_id: destination.location_id,
        sku_id: selectedSkuId,
        quantity,
        algorithm,
        vehicle_id: 'SHUTTLE-01',
        current_track_node_id: currentTrack,
      })
      const started = await api.planStartShuttleDemoTransfer(created.task_id)
      if (requestSequence.current !== sequence) return
      setTask(started)
      setAutoStart(true)
    } catch (caught) {
      if (requestSequence.current === sequence) setError(caught instanceof Error ? caught.message : String(caught))
    } finally { if (requestSequence.current === sequence) setSubmitting(false) }
  }
  const cancel = async () => {
    if (!task) return
    try {
      const next = await api.cancelShuttleDemoTransfer(task.task_id)
      setTask(next); playback.pause()
      await Promise.all([sourcesQuery.refetch(), destinationsQuery.refetch(), skusQuery.refetch()])
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)) }
  }

  const available = sourceBalance?.quantity_available ?? 0
  const canSubmit = Boolean(source && destination && source.location_id !== destination.location_id
    && source.transfer_eligible && destination.transfer_eligible
    && Number.isInteger(quantity) && quantity > 0 && quantity <= available && quantity <= 100
    && tracks.length && !submitting)
  const conservedBefore = before ? before.source + before.destination : null
  const conservedNow = task
    ? task.source_inventory.quantity_on_hand + task.destination_inventory.quantity_on_hand + task.vehicle_load_quantity
    : null
  const transferVisualization = source && destination ? {
    source, destination, skuId: selectedSkuId, quantity,
    status: task?.status ?? 'created' as const,
    vehicleLoadQuantity: task?.vehicle_load_quantity ?? 0,
  } : null

  return (
    <div className={`path-demo-layout shuttle-unified-layout${sidebarCollapsed ? ' is-sidebar-collapsed' : ''}`}>
      <aside className="path-demo-panel shuttle-demo-panel shuttle-transfer-panel">
        {sidebarCollapsed ? (
          <button className="ghost-button shuttle-sidebar-rail" type="button" aria-label="展开配置侧栏" onClick={() => setSidebarCollapsed(false)}>☰</button>
        ) : <>
          <div className="shuttle-panel-heading"><strong>SKU 货物搬运</strong><button className="ghost-button" type="button" onClick={() => setSidebarCollapsed(true)}>收起侧栏</button></div>
          <label>拓扑<select value={topologyId} onChange={(event) => onTopologyChange(event.target.value)}>{topologies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <div className="shuttle-demo-badge">synthetic_demo · 离线逻辑货物搬运</div>
          <button className="ghost-button" type="button" disabled={initializing} onClick={() => void initialize()}>{initializing ? '正在初始化…' : '初始化模拟库存'}</button>
          <small className="path-demo-note">初始化幂等，不重置已发生的搬运，也不修改 CTU 正式库存。</small>
          {!skusQuery.isLoading && !skusQuery.data?.length ? <p className="path-demo-note">尚无 Shuttle 模拟库存，请先初始化。</p> : null}
          <button className="ghost-button shuttle-config-toggle" type="button" aria-expanded={configurationExpanded} onClick={() => setConfigurationExpanded((value) => !value)}>{configurationExpanded ? '收起搬运配置' : '展开搬运配置'}</button>
          {configurationExpanded ? <section className="shuttle-config-section" aria-label="Shuttle SKU 搬运配置">
            <label>搜索 SKU<input aria-label="搜索 SKU" type="search" value={skuSearch} placeholder="SKU ID 或商品名称" onChange={(event) => { clearTask(); setSkuSearch(event.target.value) }} /></label>
            <label>SKU<select aria-label="搬运 SKU" value={selectedSkuId} onChange={(event) => { clearTask(); setSelectedSkuId(event.target.value) }}><option value="">请选择 SKU</option>{skusQuery.data?.map((sku) => <option key={sku.sku_id} value={sku.sku_id}>{sku.sku_id} · {sku.sku_name} · 可用 {sku.quantity_available}</option>)}</select></label>
            <label>楼层筛选<select aria-label="货位楼层筛选" value={level} onChange={(event) => { clearTask(); setLevel(event.target.value === 'all' ? 'all' : Number(event.target.value) as ShuttleLevel) }}><option value="all">全部楼层</option>{LEVELS.map((item) => <option key={item} value={item}>L{item}</option>)}</select></label>
            <label>起点货位<select aria-label="起点货位" value={sourceLocationId} onChange={(event) => { clearTask(); setSourceLocationId(event.target.value) }}><option value="">请选择有可用库存的货位</option>{sources.map((item) => { const balance = skuBalance(item, selectedSkuId); return <option key={item.location_id} value={item.location_id}>{item.location_id} · L{item.level} · 可用 {balance?.quantity_available ?? 0} · 可模拟搬运</option> })}</select></label>
            <label>目标货位<select aria-label="目标货位" value={destinationLocationId} onChange={(event) => { clearTask(); setDestinationLocationId(event.target.value) }}><option value="">请选择目标货位</option>{destinations.filter((item) => item.location_id !== sourceLocationId).map((item) => <option key={item.location_id} value={item.location_id}>{item.location_id} · L{item.level} · 当前 {skuBalance(item, selectedSkuId)?.quantity_on_hand ?? 0} · 可模拟搬运</option>)}</select></label>
            <div className="shuttle-level-grid"><label>搬运数量<input aria-label="搬运数量" type="number" min={1} max={100} value={quantity} onChange={(event) => { clearTask(); setQuantity(Number(event.target.value)) }} /></label><label>路径算法<select aria-label="搬运路径算法" value={algorithm} onChange={(event) => { clearTask(); setAlgorithm(event.target.value as PathDemoAlgorithm) }}><option value="astar">A*</option><option value="dijkstra">Dijkstra</option></select></label></div>
            <small className="path-demo-note">演示 Shuttle 载荷上限：100 件；这是 synthetic_demo 假设，不复用 CTU 五箱语义。</small>
            <small className="path-demo-note">演示访问关系已通过程序邻接与几何检查；正式物理审核仍未完成。</small>
          </section> : null}
          {quantity > available && source ? <p className="path-demo-error" role="alert">起点可用库存不足：当前可用 {available}。</p> : null}
          <button className="accent-button path-demo-plan-button" type="button" disabled={!canSubmit} onClick={() => void submit()}>{submitting ? '正在校验并规划…' : '规划并开始模拟搬运'}</button>
          {error ? <p className="path-demo-error" role="alert">{error}</p> : null}
          <PathPlaybackController state={playback.state} canPlay={duration > 0} onStart={playback.start} onPause={playback.pause} onResume={playback.resume} onReset={playback.reset} onRateChange={playback.setRate} />
          {task && !['delivered', 'failed', 'cancelled'].includes(task.status) ? <button className="ghost-button danger" type="button" onClick={() => void cancel()}>取消当前任务</button> : null}
          <dl className="path-demo-stats shuttle-transfer-stats">
            <div><dt>任务状态</dt><dd>{task ? statusLabel[task.status] : '待创建'}</dd></div>
            <div><dt>车辆载货</dt><dd>{task?.vehicle_load_quantity ?? 0} 件</dd></div>
            <div><dt>起点现有 / 预留</dt><dd>{task ? `${task.source_inventory.quantity_on_hand} / ${task.source_inventory.quantity_reserved}` : `${sourceBalance?.quantity_on_hand ?? 0} / ${sourceBalance?.quantity_reserved ?? 0}`}</dd></div>
            <div><dt>目标现有</dt><dd>{task?.destination_inventory.quantity_on_hand ?? destinationBalance?.quantity_on_hand ?? 0}</dd></div>
            <div><dt>Pick / Drop</dt><dd>{task?.pickup_at_seconds?.toFixed(2) ?? '—'} / {task?.dropoff_at_seconds?.toFixed(2) ?? '—'} s</dd></div>
            <div><dt>货物守恒</dt><dd>{conservedBefore === null ? '—' : `${conservedBefore} → ${conservedNow}`}</dd></div>
          </dl>
          <section className="path-demo-camera-controls"><div className="path-demo-camera-heading"><strong>视角控制</strong><span className={`path-demo-view-badge is-${cameraViewMode}`}>{cameraViewMode === 'follow' ? '跟随' : '自由'}</span></div><div className="path-demo-camera-buttons"><button className="ghost-button" type="button" disabled={!cameraSample} onClick={() => requestCamera('locate')}>定位四向车</button><button className={cameraViewMode === 'follow' ? 'accent-button' : 'ghost-button'} type="button" disabled={!cameraSample} onClick={() => setCameraViewMode((value) => value === 'follow' ? 'free' : 'follow')}>跟随四向车</button><button className="ghost-button" type="button" disabled={!source} onClick={() => source && requestCamera('focus-point', [source.world_x, source.world_y, source.world_z])}>查看取货位</button><button className="ghost-button" type="button" disabled={!destination} onClick={() => destination && requestCamera('focus-point', [destination.world_x, destination.world_y, destination.world_z])}>查看目标货位</button><button className="ghost-button" type="button" onClick={() => { setCameraViewMode('free'); requestCamera('overview') }}>仓库全景</button><button className={rackXray ? 'accent-button' : 'ghost-button'} type="button" onClick={() => setRackXray((value) => !value)}>透视货架：{rackXray ? '开' : '关'}</button></div></section>
          <details className="shuttle-contract-card"><summary><strong>高级建模与审核</strong></summary><p>演示资格只证明真实货架候选与真实 ShuttleGraph 的程序邻接、连通和车体包围盒检查；正式物理 Pick Face 仍为未审核。</p><button className="ghost-button" type="button" onClick={() => onWorkspaceModeChange('inventory')}>打开高级建模与审核</button></details>
        </>}
      </aside>
      <div className="path-demo-viewport" aria-label="四向车 SKU 货物搬运 3D 演示">
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
            shuttleVisualization={{
              tracks, lifts, plan: task?.plan ?? null, sample,
              selectedLiftId: null, selectedShuttleId: 'SHUTTLE-01',
              transfer: transferVisualization,
            }}
            shuttleRackXray={rackXray}
          />
        </Suspense>
      </div>
    </div>
  )
}
