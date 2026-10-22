import { useMemo, useState } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  Clock3,
  MapPin,
  Package2,
  Plus,
  ShoppingCart,
  X,
  Zap,
} from 'lucide-react'
import type {
  GoodsCatalogSku,
  SimulationOrder,
  SimulationOrderInput,
  SimulationOrderLineInput,
  SimulationOrderPriority,
} from '../../types'

type OrderView = SimulationOrderInput & Partial<SimulationOrder>

const statusLabels: Record<string, string> = {
  ready: '待启动',
  waiting_dispatch: '等待调度',
  executing: '执行中',
  completed: '已完成',
  exception: '异常',
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
}: {
  orders: OrderView[]
  selectedOrderId: string
  collapsed: boolean
  onSelect: (orderId: string) => void
  onToggle: () => void
  onCreate: () => void
}) {
  const counts = useMemo(() => ({
    waiting: orders.filter((order) => !order.status || ['ready', 'waiting_dispatch'].includes(order.status)).length,
    executing: orders.filter((order) => order.status === 'executing').length,
    completed: orders.filter((order) => order.status === 'completed').length,
  }), [orders])

  return (
    <aside className={`simulation-order-rail ${collapsed ? 'simulation-order-rail-collapsed' : ''}`}>
      <div className="simulation-order-rail-header">
        {!collapsed ? (
          <div>
            <strong>订单队列</strong>
            <span>{orders.length} 个订单等待仿真</span>
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
              return (
                <button
                  type="button"
                  key={order.id}
                  className={`simulation-order-card ${active ? 'active' : ''} simulation-order-${status}`}
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
                </button>
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
  return `ORD-${stamp}`
}

export function OrderComposer({
  open,
  catalog,
  onClose,
  onSubmit,
}: {
  open: boolean
  catalog: GoodsCatalogSku[]
  onClose: () => void
  onSubmit: (order: SimulationOrderInput) => void
}) {
  const [priority, setPriority] = useState<SimulationOrderPriority>('normal')
  const [targetStation, setTargetStation] = useState('出库口 01')
  const [selectedSkuId, setSelectedSkuId] = useState('')
  const [quantity, setQuantity] = useState(1)
  const [lines, setLines] = useState<SimulationOrderLineInput[]>([])
  const selectedSku = catalog.find((sku) => sku.skuId === selectedSkuId) ?? catalog[0]

  function addLine() {
    if (!selectedSku) return
    setLines((current) => {
      const existing = current.find((line) => line.skuId === selectedSku.skuId)
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
  }

  function submit() {
    if (!lines.length) return
    onSubmit({
      id: nextOrderId(),
      priority,
      targetStation,
      createdAt: new Date().toISOString(),
      lines,
    })
    setLines([])
    setPriority('normal')
  }

  function applyTemplate(templatePriority: SimulationOrderPriority, startIndex: number, count: number) {
    const selected = catalog.slice(startIndex, startIndex + count)
    setPriority(templatePriority)
    setLines(selected.map((sku, index) => ({
      id: `line-template-${index + 1}-${Date.now()}`,
      skuId: sku.skuId,
      skuName: sku.name,
      spec: sku.spec,
      quantity: index === 0 && templatePriority === 'normal' ? 2 : 1,
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
        </section>

        <section>
          <h3>添加商品</h3>
          <div className="simulation-order-add-line">
            <select value={selectedSku?.skuId ?? ''} onChange={(event) => setSelectedSkuId(event.target.value)}>
              {catalog.map((sku) => <option key={sku.skuId} value={sku.skuId}>{sku.name}</option>)}
            </select>
            <input type="number" min={1} max={5} value={quantity} onChange={(event) => setQuantity(Math.max(1, Math.min(5, Number(event.target.value) || 1)))} />
            <button type="button" onClick={addLine}><Plus size={15} />添加</button>
          </div>
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
        <span>{lines.length} 种SKU · {lines.reduce((sum, line) => sum + line.quantity, 0)} 箱</span>
        <button className="accent-button" type="button" onClick={submit} disabled={!lines.length}>提交订单</button>
      </div>
    </aside>
  )
}

const timelineStages = ['订单创建', '库存匹配', '任务生成', '设备执行', '输送交付', '出库汇合', '完成']

function currentStage(order: OrderView) {
  if (order.status === 'completed') return 6
  if (order.status === 'exception') return 1
  if (order.status === 'executing') {
    return order.statusLabel?.includes('输送') ? 4 : 3
  }
  if (order.status === 'waiting_dispatch') return 2
  return 0
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
