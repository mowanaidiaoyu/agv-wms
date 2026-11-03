import { useEffect, useMemo, useState } from 'react'
import { Box, ClipboardList, PackageCheck } from 'lucide-react'
import type { SimulationState } from '../../types'

export function TransferInventoryPanel({ simulation }: { simulation?: SimulationState }) {
  const balances = useMemo(() => simulation?.transferInventory ?? [], [simulation?.transferInventory])
  const transferTasks = useMemo(
    () => (simulation?.tasks ?? []).filter((task) => task.mode === 'shuttle_transfer'),
    [simulation?.tasks],
  )
  const skuIds = useMemo(
    () => [...new Set([...balances.map((item) => item.skuId), ...transferTasks.map((task) => task.skuId).filter(Boolean)])] as string[],
    [balances, transferTasks],
  )
  const [selectedSkuId, setSelectedSkuId] = useState('')

  useEffect(() => {
    if (!skuIds.length) {
      setSelectedSkuId('')
      return
    }
    if (!skuIds.includes(selectedSkuId)) setSelectedSkuId(skuIds[0])
  }, [selectedSkuId, skuIds])

  const selectedBalances = balances.filter((item) => item.skuId === selectedSkuId)
  const selectedTasks = transferTasks.filter((task) => task.skuId === selectedSkuId)
  const activeTask = selectedTasks.find((task) => !['completed', 'cancelled', 'failed'].includes(task.status))
    ?? selectedTasks.at(-1)
  const events = (simulation?.transferEvents ?? [])
    .filter((event) => event.skuId === selectedSkuId)
    .slice()
    .reverse()
  const inTransit = selectedTasks.reduce((sum, task) => sum + (task.inTransitQuantity ?? 0), 0)
  const onHand = selectedBalances.reduce((sum, item) => sum + item.quantityOnHand, 0)
  const reserved = selectedBalances.reduce((sum, item) => sum + item.quantityReserved, 0)

  return (
    <aside className="cross-level-transfer-panel transfer-inventory-panel" aria-label="库存详情">
      <div className="cross-level-panel-heading">
        <div>
          <strong>库存详情</strong>
          <span>Run 隔离快照 · 与 3D 库位同源</span>
        </div>
        <PackageCheck size={18} />
      </div>

      {!simulation ? (
        <p className="muted-copy">启动统一仿真后显示 transferInventory 快照；此处不生成或猜测 WMS 库存。</p>
      ) : !skuIds.length ? (
        <p className="muted-copy">当前 Run 没有跨层运输库存。</p>
      ) : (
        <>
          <label className="transfer-inventory-sku-filter">
            SKU
            <select value={selectedSkuId} onChange={(event) => setSelectedSkuId(event.target.value)}>
              {skuIds.map((skuId) => <option key={skuId}>{skuId}</option>)}
            </select>
          </label>

          <div className="transfer-inventory-summary">
            <span><small>现存</small><strong>{onHand}</strong></span>
            <span><small>预留</small><strong>{reserved}</strong></span>
            <span><small>在途</small><strong>{inTransit}</strong></span>
            <span><small>守恒总量</small><strong>{onHand + inTransit}</strong></span>
          </div>

          <section className="transfer-inventory-locations">
            <h4><Box size={14} />库位库存</h4>
            {selectedBalances.map((balance) => {
              const role = balance.locationId === activeTask?.sourceLocationId
                ? '源库位'
                : balance.locationId === activeTask?.destinationLocationId
                  ? '目标库位'
                  : '关联库位'
              return (
                <article key={`${balance.locationId}-${balance.skuId}`}>
                  <div><strong>{role} · L{balance.level}</strong><small>{balance.locationId}</small></div>
                  <span>现存 {balance.quantityOnHand}</span>
                  <span>预留 {balance.quantityReserved}</span>
                  <span>可用 {balance.quantityAvailable}</span>
                </article>
              )
            })}
          </section>

          <section className="transfer-inventory-current-task">
            <h4><ClipboardList size={14} />当前运输任务</h4>
            {activeTask ? (
              <article>
                <strong>{activeTask.id}</strong>
                <span>{activeTask.sourceLocationId} → {activeTask.destinationLocationId}</span>
                <small>{activeTask.phaseLabel} · 在途 {activeTask.inTransitQuantity ?? 0} 箱</small>
              </article>
            ) : <p className="muted-copy">当前 SKU 没有运输任务。</p>}
          </section>

          <section className="transfer-inventory-events">
            <h4>最近库存变更事件</h4>
            {events.slice(0, 10).map((event) => (
              <p key={event.eventId}>
                <b>Tick {event.tick}</b>
                <span>{event.eventType} · {event.quantity} 箱</span>
                <small>现存 {event.quantityOnHandAfter ?? '-'} / 预留 {event.quantityReservedAfter ?? '-'} / 在途 {event.inTransitAfter ?? '-'}</small>
              </p>
            ))}
            {!events.length ? <p className="muted-copy">尚无库存变更事件。</p> : null}
          </section>
        </>
      )}
    </aside>
  )
}
