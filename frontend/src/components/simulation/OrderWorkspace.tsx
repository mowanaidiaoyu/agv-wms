import { useEffect, useMemo, useState } from 'react'
import {
  Bot,
  Boxes,
  ChevronLeft,
  ChevronRight,
  Clock3,
  GitBranch,
  MapPin,
  Package2,
  Plus,
  ShoppingCart,
  Trash2,
  X,
  Zap,
} from 'lucide-react'
import type {
  GoodsCatalogSku,
  SimulationOrder,
  SimulationOrderDispatchMode,
  SimulationOrderInput,
  SimulationOrderLineInput,
  SimulationOrderPriority,
  SimulationTask,
} from '../../types'

type OrderView = SimulationOrderInput & Partial<SimulationOrder>

const statusLabels: Record<string, string> = {
  ready: '待启动',
  waiting_dispatch: '等待调度',
  executing: '执行中',
  returning: '取消回库中',
  cancelled: '已取消',
  completed: '已完成',
  exception: '异常',
}

const dispatchModeLabels: Record<SimulationOrderDispatchMode, string> = {
  compact: '车辆节省',
  balanced: '智能均衡',
  parallel: '并行优先',
}

function orderQuantity(order: OrderView) {
  return order.lines.reduce((sum, line) => sum + line.quantity, 0)
}

function orderStatus(order: OrderView) {
  return order.statusLabel || statusLabels[order.status || 'ready'] || '待启动'
}

export function OrderRail({
  orders,
  selectedOrderId,
  collapsed,
  onSelect,
  onToggle,
  onCreate,
  onRemove,
  removingOrderId,
  runStatus,
}: {
  orders: OrderView[]
  selectedOrderId: string
  collapsed: boolean
  onSelect: (orderId: string) => void
  onToggle: () => void
  onCreate: () => void
  onRemove?: (order: OrderView) => void
  removingOrderId?: string
  runStatus?: string
}) {
  const counts = useMemo(() => ({
    waiting: orders.filter((order) => !order.status || ['ready', 'waiting_dispatch'].includes(order.status)).length,
    executing: orders.filter((order) => order.status === 'executing').length,
    completed: orders.filter((order) => ['completed', 'cancelled'].includes(order.status ?? '')).length,
  }), [orders])
  const draftCount = orders.filter((order) => (order.status ?? 'ready') === 'ready').length
  const runtimeCount = orders.length - draftCount

  return (
    <aside className={`simulation-order-rail ${collapsed ? 'simulation-order-rail-collapsed' : ''}`}>
      <div className="simulation-order-rail-header">
        {!collapsed ? (
          <div>
            <strong>订单队列</strong>
            <span>{draftCount
              ? `${runtimeCount} 个本次订单 · ${draftCount} 个待下次启动`
              : `${runtimeCount} 个本次订单`}</span>
          </div>
        ) : <ShoppingCart size={18} />}
        <button type="button" onClick={onToggle} title={collapsed ? '展开订单' : '收起订单'}>
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </div>

      {!collapsed ? (
        <>
          <div className="simulation-order-counts">
            <span><b>{counts.waiting}</b>待处理</span>
            <span><b>{counts.executing}</b>执行中</span>
            <span><b>{counts.completed}</b>已完成</span>
          </div>
          <div className="simulation-order-list">
            {orders.length ? orders.map((order) => {
              const active = order.id === selectedOrderId
              const status = order.status || 'ready'
              const canRemove = status === 'ready' || (
                ['running', 'paused'].includes(runStatus ?? '')
                && !['completed', 'cancelled', 'exception'].includes(status)
              )
              return (
                <div
                  key={order.id}
                  className={`simulation-order-card ${active ? 'active' : ''} simulation-order-${status}`}
                >
                  <button
                    className="simulation-order-card-select"
                    type="button"
                    onClick={() => onSelect(order.id)}
                  >
                    <span className="simulation-order-card-topline">
                      <strong>{order.id}</strong>
                      {order.priority === 'urgent' ? <em><Zap size={11} />紧急</em> : null}
                    </span>
                    <span className="simulation-order-card-state">
                      <b>{orderStatus(order)}</b>
                      <small>{order.progress ?? 0}%</small>
                    </span>
                    <span className="simulation-order-progress"><i style={{ width: `${order.progress ?? 0}%` }} /></span>
                    <span className="simulation-order-card-meta">
                      <small><Package2 size={12} />{order.lines.length} SKU · {orderQuantity(order)} 箱</small>
                      <small><MapPin size={12} />{order.targetStation}</small>
                    </span>
                    <span className="simulation-order-card-plan">
                      <GitBranch size={12} />
                      {order.totalTasks ? `${order.totalTasks} 个 CTU 任务` : status === 'ready' ? '待下次启动' : dispatchModeLabels[order.dispatchMode ?? 'balanced']}
                      {order.decomposition?.activeVehicleIds.length ? ` · ${order.decomposition.activeVehicleIds.length} 辆已分配` : ''}
                    </span>
                  </button>
                  {canRemove ? (
                    <button
                      className="simulation-order-remove"
                      type="button"
                      aria-label={`${status === 'ready' ? '删除' : '取消'}订单 ${order.id}`}
                      title={status === 'ready' ? '删除待启动订单' : '安全取消订单'}
                      disabled={removingOrderId === order.id}
                      onClick={() => onRemove?.(order)}
                    >
                      {removingOrderId === order.id ? <Clock3 size={13} /> : <Trash2 size={13} />}
                    </button>
                  ) : null}
                </div>
              )
            }) : (
              <div className="simulation-orders-empty">
                <Package2 size={24} />
                <strong>还没有订单</strong>
                <span>新建订单后再启动仿真</span>
              </div>
            )}
          </div>
          <button className="simulation-new-order-button" type="button" onClick={onCreate}>
            <Plus size={15} />新建订单
          </button>
        </>
      ) : (
        <button className="simulation-order-rail-create-compact" type="button" onClick={onCreate} title="新建订单">
          <Plus size={17} />
        </button>
      )}
    </aside>
  )
}

function nextOrderId() {
  const date = new Date()
  const stamp = `${date.getHours().toString().padStart(2, '0')}${date.getMinutes().toString().padStart(2, '0')}${date.getSeconds().toString().padStart(2, '0')}`
  return `ORD-${stamp}-${String(Date.now()).slice(-4)}`
}

export function OrderComposer({
  open,
  catalog,
  catalogError = '',
  onRetryCatalog,
  onClose,
  onSubmit,
}: {
  open: boolean
  catalog: GoodsCatalogSku[]
  catalogError?: string
  onRetryCatalog?: () => void
  onClose: () => void
  onSubmit: (order: SimulationOrderInput) => void | Promise<void>
}) {
  const [priority, setPriority] = useState<SimulationOrderPriority>('normal')
  const [dispatchMode, setDispatchMode] = useState<SimulationOrderDispatchMode>('balanced')
  const [maxParallelCtu, setMaxParallelCtu] = useState(7)
  const [targetStation, setTargetStation] = useState('出库口 01')
  const [selectedSkuId, setSelectedSkuId] = useState('')
  const [quantity, setQuantity] = useState(1)
  const [lines, setLines] = useState<SimulationOrderLineInput[]>([])
  const [formError, setFormError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const selectedSku = catalog.find((sku) => sku.skuId === selectedSkuId)

  useEffect(() => {
    if (!catalog.length) {
      setSelectedSkuId('')
      return
    }
    if (!catalog.some((sku) => sku.skuId === selectedSkuId)) {
      setSelectedSkuId(catalog[0].skuId)
    }
  }, [catalog, selectedSkuId])

  function addLine() {
    setFormError('')
    if (!catalog.length) {
      setFormError('商品目录尚未加载，请稍后重试。')
      return
    }
    if (!selectedSku) {
      setFormError('所选商品已不在当前目录中，请重新选择。')
      return
    }
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 20) {
      setFormError('商品数量必须是 1～20 箱的整数。')
      return
    }
    const existing = lines.find((line) => line.skuId === selectedSku.skuId)
    if (existing && existing.quantity + quantity > 20) {
      setFormError(`同一 SKU 单个订单最多 20 箱；当前已有 ${existing.quantity} 箱。`)
      return
    }
    setLines((current) => {
      if (existing) {
        return current.map((line) => line.skuId === selectedSku.skuId ? { ...line, quantity: line.quantity + quantity } : line)
      }
      return [...current, {
        id: `line-${current.length + 1}-${Date.now()}`,
        skuId: selectedSku.skuId,
        skuName: selectedSku.name,
        spec: selectedSku.spec,
        quantity,
      }]
    })
    setQuantity(1)
  }

  async function submit() {
    if (submitting) return
    setFormError('')
    if (!lines.length) {
      setFormError('请至少添加一种商品后再提交订单。')
      return
    }
    try {
      setSubmitting(true)
      await onSubmit({
        id: nextOrderId(),
        priority,
        dispatchMode,
        maxParallelCtu,
        targetStation,
        createdAt: new Date().toISOString(),
        lines,
      })
      setLines([])
      setPriority('normal')
      setDispatchMode('balanced')
      setMaxParallelCtu(7)
      setQuantity(1)
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : '订单提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  function applyTemplate(templatePriority: SimulationOrderPriority, startIndex: number, count: number) {
    const selected = catalog.slice(startIndex, startIndex + count)
    if (!selected.length) {
      setFormError('商品目录不足，无法应用该订单模板。')
      return
    }
    setFormError('')
    setPriority(templatePriority)
    setDispatchMode(templatePriority === 'urgent' ? 'compact' : 'balanced')
    setLines(selected.map((sku, index) => ({
      id: `line-template-${index + 1}-${Date.now()}`,
      skuId: sku.skuId,
      skuName: sku.name,
      spec: sku.spec,
      quantity: index === 0 && templatePriority === 'normal' ? 2 : 1,
    })))
  }

  function applyFleetTemplate() {
    const selected = catalog.slice(0, 7)
    if (selected.length < 7) {
      setFormError('商品目录不足 7 种，无法生成多车协同订单。')
      return
    }
    setFormError('')
    setPriority('normal')
    setDispatchMode('parallel')
    setMaxParallelCtu(7)
    setLines(selected.map((sku, index) => ({
      id: `line-fleet-${index + 1}-${Date.now()}`,
      skuId: sku.skuId,
      skuName: sku.name,
      spec: sku.spec,
      quantity: 2,
    })))
  }

  return (
    <aside className={`simulation-order-composer ${open ? 'open' : ''}`} aria-hidden={!open}>
      <div className="simulation-order-composer-header">
        <div>
          <strong>新建仿真订单</strong>
          <span>订单提交后将直接进入当前仿真队列</span>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭新建订单"><X size={18} /></button>
      </div>
      <div className="simulation-order-composer-body">
        <section>
          <h3>快速模板</h3>
          <div className="simulation-order-templates">
            <button type="button" onClick={() => applyTemplate('normal', 0, 3)}>
              <Package2 size={17} /><span><b>CTU多箱订单</b><small>3种SKU，自动组合取货</small></span>
            </button>
            <button type="button" onClick={() => applyTemplate('urgent', 5, 2)}>
              <Zap size={17} /><span><b>紧急插单</b><small>高优先级进入任务队列</small></span>
            </button>
            <button type="button" onClick={applyFleetTemplate}>
              <Bot size={17} /><span><b>多车协同订单</b><small>一张订单拆为 7 个 CTU 任务</small></span>
            </button>
          </div>
        </section>

        <section>
          <h3>订单信息</h3>
          <div className="simulation-order-priority">
            <button className={priority === 'normal' ? 'active' : ''} type="button" onClick={() => setPriority('normal')}>普通订单</button>
            <button className={priority === 'urgent' ? 'active urgent' : ''} type="button" onClick={() => setPriority('urgent')}>紧急订单</button>
          </div>
          <label>目标站台
            <select value={targetStation} onChange={(event) => setTargetStation(event.target.value)}>
              <option>出库口 01</option>
              <option>出库口 02</option>
            </select>
          </label>
          <h3>CTU 拆解策略</h3>
          <div className="simulation-order-dispatch">
            {(['compact', 'balanced', 'parallel'] as const).map((mode) => (
              <button className={dispatchMode === mode ? 'active' : ''} type="button" key={mode} onClick={() => setDispatchMode(mode)}>
                <b>{dispatchModeLabels[mode]}</b>
                <small>{mode === 'compact' ? '尽量装满单车' : mode === 'balanced' ? '兼顾距离与车辆数' : '优先多车并行'}</small>
              </button>
            ))}
          </div>
          <label>最大并行 CTU
            <select value={maxParallelCtu} onChange={(event) => setMaxParallelCtu(Number(event.target.value))} disabled={dispatchMode === 'compact'}>
              {[1, 2, 3, 4, 5, 6, 7].map((count) => <option key={count} value={count}>{count} 辆</option>)}
            </select>
          </label>
        </section>

        <section>
          <h3>添加商品</h3>
          {catalogError ? (
            <div className="simulation-order-catalog-error" role="alert">
              <p className="form-error">商品目录加载失败：{catalogError}</p>
              {onRetryCatalog ? <button type="button" className="ghost-button" onClick={onRetryCatalog}>重试商品目录</button> : null}
            </div>
          ) : null}
          <div className="simulation-order-add-line">
            <select aria-label="商品" value={selectedSkuId} onChange={(event) => { setSelectedSkuId(event.target.value); setFormError('') }} disabled={!catalog.length}>
              {!catalog.length ? <option value="">{catalogError ? '商品目录不可用' : '商品目录加载中…'}</option> : null}
              {catalog.map((sku) => <option key={sku.skuId} value={sku.skuId}>{sku.name}</option>)}
            </select>
            <input aria-label="数量" type="number" min={1} max={20} value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} />
            <button type="button" onClick={addLine} disabled={!catalog.length}><Plus size={15} />添加</button>
          </div>
          {formError ? <p className="form-error" role="alert">{formError}</p> : null}
          <div className="simulation-order-lines">
            {lines.map((line) => (
              <div key={line.id}>
                <span><b>{line.skuName}</b><small>{line.spec || line.skuId}</small></span>
                <strong>× {line.quantity}</strong>
                <button type="button" onClick={() => setLines((current) => current.filter((item) => item.id !== line.id))} aria-label={`删除 ${line.skuName}`}><X size={14} /></button>
              </div>
            ))}
            {!lines.length ? <p>选择模板或添加商品后，这里会显示订单明细。</p> : null}
          </div>
        </section>
      </div>
      <div className="simulation-order-composer-footer">
        <span>{lines.length} 种SKU · {lines.reduce((sum, line) => sum + line.quantity, 0)} 箱 · {dispatchModeLabels[dispatchMode]}</span>
        <button className="accent-button" type="button" onClick={() => void submit()} disabled={!lines.length || submitting}>
          {submitting ? '提交中…' : '提交订单'}
        </button>
      </div>
    </aside>
  )
}

const timelineStages = ['订单创建', '库存匹配', '任务拆解', 'CTU分配', '协同执行', '输送交付', '完成']

function currentStage(order: OrderView) {
  if (order.status === 'completed') return 6
  if (order.status === 'exception') return 1
  if (order.status === 'executing') {
    if (order.statusLabel?.includes('输送')) return 5
    return order.decomposition?.assignedTaskCount === order.decomposition?.taskCount ? 4 : 3
  }
  if (order.status === 'waiting_dispatch') return 2
  return 0
}

export function OrderOrchestrationPanel({
  order,
  tasks,
  onFocusRobot,
}: {
  order?: OrderView
  tasks: SimulationTask[]
  onFocusRobot?: (robotId: string) => void
}) {
  if (!order) return null
  const decomposition = order.decomposition
  const requested = decomposition?.requestedQuantity ?? orderQuantity(order)
  const allocated = decomposition?.allocatedQuantity ?? 0
  const assigned = tasks.filter((task) => task.assignedRobotId).length
  const strategy = order.dispatchMode ?? decomposition?.strategy ?? 'balanced'
  return (
    <section className="simulation-order-orchestration" aria-label="订单拆解与 CTU 分配">
      <header>
        <span><GitBranch size={15} /><b>订单编排</b><small>{dispatchModeLabels[strategy]}</small></span>
        <strong>{requested} 箱 → {tasks.length || '待拆解'} 个任务 → {assigned} 辆 CTU</strong>
      </header>
      <div className="simulation-order-orchestration-flow">
        <span className={decomposition ? 'done' : 'active'}><Boxes size={14} /><b>库存匹配</b><small>{decomposition ? `${allocated}/${requested} 箱` : '启动后匹配'}</small></span>
        <i>→</i>
        <span className={tasks.length ? 'done' : ''}><GitBranch size={14} /><b>任务拆解</b><small>{tasks.length ? `${tasks.length} 个工作包` : `目标 ${order.maxParallelCtu ?? 7} 辆`}</small></span>
        <i>→</i>
        <span className={assigned === tasks.length && tasks.length ? 'done' : tasks.length ? 'active' : ''}><Bot size={14} /><b>CTU 分配</b><small>{assigned}/{tasks.length || 0} 已分配</small></span>
      </div>
      {tasks.length ? (
        <div className="simulation-order-task-map">
          {tasks.map((task, index) => (
            <button type="button" key={task.id} disabled={!task.assignedRobotId} onClick={() => task.assignedRobotId && onFocusRobot?.(task.assignedRobotId)}>
              <span><b>T{String(task.taskIndex ?? index + 1).padStart(2, '0')}</b><small>{task.lines.length} 箱</small></span>
              <em>→</em>
              <span>
                <b>{task.assignedRobotId ?? '等待分配'} · {task.assignedPortLabel ?? '出库口待选'}</b>
                <small>{task.sourceGroupLabel ?? task.phaseLabel}{task.portQueuePosition ? ` · 出口队列 ${task.portQueuePosition}` : ''}</small>
              </span>
            </button>
          ))}
        </div>
      ) : <p>启动仿真后，将在这里展示库存匹配、任务拆解和车辆分配结果。</p>}
    </section>
  )
}

export function OrderTimeline({ order }: { order?: OrderView }) {
  if (!order) return null
  const active = currentStage(order)
  return (
    <div className="simulation-order-timeline">
      <span className="simulation-order-timeline-title">
        <b>{order.id}</b><small><Clock3 size={12} />{orderStatus(order)}</small>
      </span>
      <div className="simulation-order-timeline-steps">
        {timelineStages.map((stage, index) => (
          <span key={stage} className={index < active ? 'done' : index === active ? 'active' : ''}>
            <i>{index < active ? '✓' : index + 1}</i><b>{stage}</b>
          </span>
        ))}
      </div>
    </div>
  )
}
