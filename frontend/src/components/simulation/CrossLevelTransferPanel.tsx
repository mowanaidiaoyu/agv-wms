import { useEffect, useMemo, useState } from 'react'
import { Box, LocateFixed, MapPinned, Plus, Route, RotateCcw, X } from 'lucide-react'
import { api } from '../../lib/api'
import type {
  ShuttleContract,
  ShuttlePlan,
  ShuttleTransferInput,
  SimulationState,
} from '../../types'

type Props = {
  topologyId: string
  contract?: ShuttleContract
  pendingTransfers: ShuttleTransferInput[]
  simulation?: SimulationState
  selectedRunId: string
  onAdd: (transfer: ShuttleTransferInput) => void
  onRemove: (id: string) => void
  onFocusVehicle: (vehicleId: string) => void
  onRuntimeChanged: () => Promise<void>
}

const levels = [1, 2, 3, 4] as const
const transferStages = [
  { key: 'pickup', label: '取货' },
  { key: 'track', label: '轨道' },
  { key: 'enter_lift', label: '进入 Lift' },
  { key: 'lift_moving', label: '升降' },
  { key: 'exit_lift', label: '离开 Lift' },
  { key: 'drop', label: '放货' },
] as const

function activeTransferStage(phase: string) {
  if (phase === 'pickup_wait' || phase === 'reserved') return 'pickup'
  if (phase === 'moving_to_dropoff') return 'track'
  if (phase === 'drop_wait' || phase === 'completed') return 'drop'
  return phase
}

export function CrossLevelTransferPanel({
  topologyId,
  contract,
  pendingTransfers,
  simulation,
  selectedRunId,
  onAdd,
  onRemove,
  onFocusVehicle,
  onRuntimeChanged,
}: Props) {
  const [sourceLevel, setSourceLevel] = useState<(typeof levels)[number]>(1)
  const [destinationLevel, setDestinationLevel] = useState<(typeof levels)[number]>(4)
  const [sourceTrackNode, setSourceTrackNode] = useState('')
  const [destinationTrackNode, setDestinationTrackNode] = useState('')
  const [skuId, setSkuId] = useState('SKU-001')
  const [quantity, setQuantity] = useState(2)
  const [sourceQuantity, setSourceQuantity] = useState(10)
  const [destinationQuantity, setDestinationQuantity] = useState(5)
  const [algorithm, setAlgorithm] = useState<'astar' | 'dijkstra'>('astar')
  const [plan, setPlan] = useState<ShuttlePlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const sourceNodes = useMemo(
    () => contract?.tracks.filter((node) => node.shuttle_level === sourceLevel) ?? [],
    [contract?.tracks, sourceLevel],
  )
  const destinationNodes = useMemo(
    () => contract?.tracks.filter((node) => node.shuttle_level === destinationLevel) ?? [],
    [contract?.tracks, destinationLevel],
  )

  useEffect(() => {
    const preferred = sourceNodes.find((node) => node.track_node_id === 'L1-r100-c7') ?? sourceNodes[0]
    if (!sourceNodes.some((node) => node.track_node_id === sourceTrackNode)) {
      setSourceTrackNode(preferred?.track_node_id ?? '')
      setPlan(null)
    }
  }, [sourceNodes, sourceTrackNode])

  useEffect(() => {
    const preferred = destinationNodes.find((node) => node.track_node_id === 'L4-r100-c51') ?? destinationNodes[0]
    if (!destinationNodes.some((node) => node.track_node_id === destinationTrackNode)) {
      setDestinationTrackNode(preferred?.track_node_id ?? '')
      setPlan(null)
    }
  }, [destinationNodes, destinationTrackNode])

  async function previewPlan() {
    if (!sourceTrackNode || !destinationTrackNode) return
    setBusy(true)
    setError('')
    try {
      setPlan(await api.planShuttleTransfer(topologyId, sourceTrackNode, destinationTrackNode, algorithm))
    } catch (caught) {
      setPlan(null)
      setError(caught instanceof Error ? caught.message : '路径规划失败')
    } finally {
      setBusy(false)
    }
  }

  function addTransfer() {
    if (!plan || plan.result_status !== 'success') {
      setError('请先生成可执行的 ShuttleGraph 路径。')
      return
    }
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 100) {
      setError('运输箱数必须是 1～100 的整数。')
      return
    }
    if (!Number.isInteger(sourceQuantity) || sourceQuantity < 0 || !Number.isInteger(destinationQuantity) || destinationQuantity < 0) {
      setError('源库存和目标库存必须是非负整数。')
      return
    }
    if (quantity > sourceQuantity) {
      setError('运输箱数不能超过源库位初始库存。')
      return
    }
    if (sourceTrackNode === destinationTrackNode) {
      setError('起点和终点轨道节点不能相同。')
      return
    }
    const suffix = `${Date.now()}-${pendingTransfers.length + 1}`
    onAdd({
      id: `transfer-${suffix}`,
      vehicleId: `SHUTTLE-UNIFIED-${String(pendingTransfers.length + 1).padStart(2, '0')}`,
      skuId,
      skuName: skuId,
      sourceLocationId: `SIM-BIN-${sourceTrackNode}`,
      destinationLocationId: `SIM-BIN-${destinationTrackNode}`,
      sourceTrackNode,
      destinationTrackNode,
      sourceLevel,
      destinationLevel,
      quantity,
      algorithm,
      initialSourceQuantity: sourceQuantity,
      initialDestinationQuantity: destinationQuantity,
    })
    setError('')
  }

  const runtimeTasks = simulation?.tasks.filter((task) => task.mode === 'shuttle_transfer') ?? []
  return (
    <aside className="cross-level-transfer-panel" aria-label="跨层运输">
      <div className="cross-level-panel-heading">
        <div>
          <strong>跨层运输</strong>
          <span>C ShuttleGraph · 统一仿真 tick</span>
        </div>
        <Route size={18} />
      </div>

      <div className="cross-level-form">
        <label>
          SKU
          <input value={skuId} onChange={(event) => { setSkuId(event.target.value); setPlan(null) }} />
        </label>
        <div className="cross-level-field-row">
          <label>
            源楼层
            <select value={sourceLevel} onChange={(event) => setSourceLevel(Number(event.target.value) as 1 | 2 | 3 | 4)}>
              {levels.map((level) => <option key={level} value={level}>L{level}</option>)}
            </select>
          </label>
          <label>
            目标楼层
            <select value={destinationLevel} onChange={(event) => setDestinationLevel(Number(event.target.value) as 1 | 2 | 3 | 4)}>
              {levels.map((level) => <option key={level} value={level}>L{level}</option>)}
            </select>
          </label>
        </div>
        <label>
          起点轨道节点
          <select value={sourceTrackNode} onChange={(event) => { setSourceTrackNode(event.target.value); setPlan(null) }}>
            {sourceNodes.map((node) => <option key={node.track_node_id}>{node.track_node_id}</option>)}
          </select>
        </label>
        <label>
          终点轨道节点
          <select value={destinationTrackNode} onChange={(event) => { setDestinationTrackNode(event.target.value); setPlan(null) }}>
            {destinationNodes.map((node) => <option key={node.track_node_id}>{node.track_node_id}</option>)}
          </select>
        </label>
        <div className="cross-level-field-row cross-level-field-row-three">
          <label>箱数<input type="number" min={1} max={100} value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} /></label>
          <label>源库存<input type="number" min={0} value={sourceQuantity} onChange={(event) => setSourceQuantity(Number(event.target.value))} /></label>
          <label>目标库存<input type="number" min={0} value={destinationQuantity} onChange={(event) => setDestinationQuantity(Number(event.target.value))} /></label>
        </div>
        <label>
          算法
          <select value={algorithm} onChange={(event) => { setAlgorithm(event.target.value as 'astar' | 'dijkstra'); setPlan(null) }}>
            <option value="astar">A*</option>
            <option value="dijkstra">Dijkstra</option>
          </select>
        </label>
        <div className="cross-level-form-actions">
          <button type="button" className="ghost-button" onClick={() => void previewPlan()} disabled={busy || !contract?.planningEnabled}>
            <MapPinned size={15} />{busy ? '规划中…' : '规划路径'}
          </button>
          <button type="button" className="accent-button" onClick={addTransfer} disabled={!plan}>
            <Plus size={15} />加入仿真
          </button>
        </div>
      </div>

      {error ? <p className="form-error">{error}</p> : null}
      {contract ? (
        <div className="cross-level-contract">
          <span>{contract.configRevision}</span>
          <span>{contract.tracks.length} 个轨道节点</span>
          <span>{contract.lifts.map((lift) => lift.label).join(' / ')}</span>
        </div>
      ) : <p className="muted-copy">正在读取 ShuttleGraph…</p>}

      {plan ? (
        <div className="cross-level-plan-card">
          <strong>{plan.algorithm.toUpperCase()} · {plan.path_nodes.length} 节点</strong>
          <span>预计 {plan.estimated_duration_seconds.toFixed(2)} 秒 · {plan.chosen_lift ?? '同层直达'}</span>
          <small>时间参数：{plan.timing_source}；执行阶段由统一 tick 播放适配层驱动。</small>
        </div>
      ) : null}

      <section className="cross-level-list">
        <h4>本次仿真任务</h4>
        {pendingTransfers.map((transfer) => (
          <article key={transfer.id}>
            <div><Box size={14} /><strong>{transfer.skuId} · {transfer.quantity} 箱</strong></div>
            <span>{transfer.sourceTrackNode} → {transfer.destinationTrackNode}</span>
            <small>待统一 Run 启动</small>
            <button type="button" onClick={() => onRemove(transfer.id)} aria-label="移除运输任务"><X size={14} /></button>
          </article>
        ))}
        {!pendingTransfers.length && !runtimeTasks.length ? <p className="muted-copy">规划路径后加入本次统一仿真。</p> : null}
        {runtimeTasks.map((task) => (
          <article key={task.id} className="cross-level-runtime-task">
            <div><Box size={14} /><strong>{task.skuId} · {task.quantity} 箱</strong></div>
            <span>{task.phaseLabel}</span>
            <small>在途 {task.inTransitQuantity ?? 0} 箱 · {task.plan?.chosen_lift ?? '未选升降机'}</small>
            <div className="cross-level-stage-strip" aria-label={`运输阶段 ${task.id}`}>
              {transferStages.map((stage) => (
                <i key={stage.key} className={activeTransferStage(task.phase) === stage.key ? 'active' : ''}>{stage.label}</i>
              ))}
            </div>
            <button type="button" onClick={() => task.assignedRobotId && onFocusVehicle(task.assignedRobotId)} aria-label="定位四向车">
              <LocateFixed size={14} />
            </button>
            {task.status === 'failed' && selectedRunId ? (
              <button className="cross-level-retry" type="button" onClick={async () => { await api.retryShuttleTransfer(selectedRunId, task.id); await onRuntimeChanged() }}>
                <RotateCcw size={13} />重试
              </button>
            ) : null}
            {!['completed', 'cancelled'].includes(task.status) && selectedRunId ? (
              <button className="cross-level-cancel" type="button" onClick={async () => { await api.cancelShuttleTransfer(selectedRunId, task.id); await onRuntimeChanged() }}>
                取消
              </button>
            ) : null}
          </article>
        ))}
      </section>
    </aside>
  )
}
