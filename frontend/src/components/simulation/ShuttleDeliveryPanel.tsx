import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, Truck } from 'lucide-react'
import { api } from '../../lib/api'
import type { ShuttleDockState, SimulationRobot, SimulationTask, TopologyNode } from '../../types'

/**
 * 四向车搬运工作台：展示并运行时调整出货位设置（取什么货物/取多少/优先级/指定车辆/
 * 循环搬运/启停），以及各出货位的搬运任务进度。设置保存在 Run 状态里，不改拓扑。
 */

/**
 * 合并拓扑出货位与 Run 状态：拓扑里新加的出货位（Run 启动后才保存的）立即出现在
 * 列表中（按拓扑节点带默认设置），用户保存设置后后端会自动补建 Run 状态并派任务。
 */
function mergeShuttleDocks(dockNodes: TopologyNode[], runDocks: ShuttleDockState[]): ShuttleDockState[] {
  const runDockById = new Map(runDocks.map((dock) => [dock.dockId, dock]))
  const merged = dockNodes.map((node) => {
    const existing = runDockById.get(node.id)
    if (existing) return existing
    return {
      dockId: node.id,
      label: node.label || node.id,
      row: node.row,
      col: node.col,
      floor: node.floor || '1F',
      skuId: node.skuId ?? null,
      skuName: node.skuName ?? null,
      largeCategory: node.largeCategory ?? null,
      quantity: node.quantity && node.quantity > 0 ? node.quantity : 1,
      priority: 'normal' as const,
      vehicleId: null,
      loop: false,
      enabled: true,
      cursor: 0,
    }
  })
  // Run 状态里有、但拓扑里已删除的出货位不再显示。
  return merged
}

type DockDraft = {
  skuId: string
  quantity: number
  priority: 'normal' | 'urgent'
  vehicleId: string
  loop: boolean
  enabled: boolean
}

function draftOf(dock: ShuttleDockState): DockDraft {
  return {
    skuId: dock.skuId ?? '',
    quantity: dock.quantity,
    priority: dock.priority ?? 'normal',
    vehicleId: dock.vehicleId ?? '',
    loop: Boolean(dock.loop),
    enabled: dock.enabled !== false,
  }
}

function DockCard({
  runId,
  dock,
  tasks,
  robots,
  onChanged,
}: {
  runId: string
  dock: ShuttleDockState
  tasks: SimulationTask[]
  robots: SimulationRobot[]
  onChanged: () => void
}) {
  const catalogQuery = useQuery({ queryKey: ['goods', 'catalog'], queryFn: () => api.getGoodsCatalog() })
  const skus = catalogQuery.data?.skus ?? []
  const [draft, setDraft] = useState<DockDraft>(() => draftOf(dock))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setDraft(draftOf(dock))
    // 出货位设置来自运行状态，状态刷新时同步回表单。
  }, [dock.skuId, dock.quantity, dock.priority, dock.vehicleId, dock.loop, dock.enabled])

  const dirty =
    draft.skuId !== (dock.skuId ?? '') ||
    draft.quantity !== dock.quantity ||
    draft.priority !== (dock.priority ?? 'normal') ||
    draft.vehicleId !== (dock.vehicleId ?? '') ||
    draft.loop !== Boolean(dock.loop) ||
    draft.enabled !== dock.enabled

  const dockTasks = tasks.filter((task) => task.dockId === dock.dockId)

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      await api.updateShuttleDock(runId, dock.dockId, {
        skuId: draft.skuId || null,
        skuName: skus.find((item) => item.skuId === draft.skuId)?.name ?? null,
        largeCategory: skus.find((item) => item.skuId === draft.skuId)?.largeCategory ?? null,
        quantity: draft.quantity,
        priority: draft.priority,
        vehicleId: draft.vehicleId || null,
        loop: draft.loop,
        enabled: draft.enabled,
      })
      onChanged()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={`shuttle-dock-card ${dock.enabled ? '' : 'disabled'}`}>
      <div className="shuttle-dock-card-head">
        <b>{dock.label}</b>
        <span>
          交付格 {dock.floor} · ({dock.row}, {dock.col})
        </span>
      </div>
      <div className="shuttle-dock-form">
        <label>
          <span>取什么货物</span>
          <select value={draft.skuId} onChange={(event) => setDraft({ ...draft, skuId: event.target.value })}>
            <option value="">（未设置）</option>
            {skus.map((sku) => (
              <option key={sku.skuId} value={sku.skuId}>
                {sku.name}（{sku.skuId}）
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>取多少（箱）</span>
          <input
            type="number"
            min={1}
            value={draft.quantity}
            onChange={(event) => setDraft({ ...draft, quantity: Math.max(1, Math.floor(Number(event.target.value) || 1)) })}
          />
        </label>
        <label>
          <span>优先级</span>
          <select
            value={draft.priority}
            onChange={(event) => setDraft({ ...draft, priority: event.target.value as 'normal' | 'urgent' })}
          >
            <option value="normal">普通</option>
            <option value="urgent">加急（优先派车）</option>
          </select>
        </label>
        <label>
          <span>指定车辆</span>
          <select value={draft.vehicleId} onChange={(event) => setDraft({ ...draft, vehicleId: event.target.value })}>
            <option value="">自动分配</option>
            {robots
              .filter((robot) => robot.type === 'shuttle')
              .map((robot) => (
                <option key={robot.id} value={robot.id}>
                  {robot.id}
                </option>
              ))}
          </select>
        </label>
        <label className="shuttle-dock-check">
          <input
            type="checkbox"
            checked={draft.loop}
            onChange={(event) => setDraft({ ...draft, loop: event.target.checked })}
          />
          <span>循环搬运（完成一趟自动补一趟）</span>
        </label>
        <label className="shuttle-dock-check">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
          />
          <span>启用（停用后不再派新任务）</span>
        </label>
      </div>
      <div className="shuttle-dock-actions">
        <button
          className="accent-button"
          type="button"
          disabled={!dirty || saving || !runId}
          onClick={() => void save()}
        >
          {saving ? '保存中…' : dirty ? '应用设置（重排未派任务）' : '已同步'}
        </button>
        {error ? <span className="shuttle-dock-error">{error}</span> : null}
      </div>
      {dockTasks.length ? (
        <div className="shuttle-dock-tasks">
          {dockTasks.map((task) => (
            <div key={task.id} className={`shuttle-delivery-task ${task.status === 'completed' ? 'done' : ''}`}>
              <div className="shuttle-delivery-task-head">
                <b>{task.id}</b>
                <span>
                  {task.taskKind === 'relocate' ? '倒库 · ' : ''}
                  {task.phaseLabel || task.status}
                </span>
              </div>
              <div className="shuttle-delivery-task-meta">
                <span>
                  {task.lines?.[0]?.skuName || task.lines?.[0]?.skuId || '—'} · {task.sourceCellId} → {task.deliveryCellId}
                </span>
                <small>
                  {task.assignedRobotId ?? '等待车辆'} · {task.status}
                </small>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted-copy">该出货位暂无任务（启用并保存后会自动生成）。</p>
      )}
    </div>
  )
}

export function ShuttleDeliveryPanel({
  runId,
  dockNodes,
  runDocks,
  tasks,
  robots,
  onChanged,
}: {
  runId: string
  dockNodes: TopologyNode[]
  runDocks: ShuttleDockState[]
  tasks: SimulationTask[]
  robots: SimulationRobot[]
  onChanged: () => void
}) {
  const docks = mergeShuttleDocks(dockNodes, runDocks)
  const shuttleTasks = tasks.filter(
    (task) => task.robotType === 'shuttle' && task.mode !== 'shuttle_transfer',
  )
  const shuttleRobots = robots.filter((robot) => robot.type === 'shuttle')
  const pending = shuttleTasks.filter((task) => task.status === 'pending').length
  const running = shuttleTasks.filter((task) => task.status === 'assigned').length
  const done = shuttleTasks.filter((task) => task.status === 'completed').length

  return (
    <aside className="cross-level-transfer-panel shuttle-delivery-panel" aria-label="四向车搬运">
      <div className="cross-level-panel-heading">
        <Truck size={20} />
        <div>
          <h3>四向车搬运 · 整箱出库</h3>
          <p>
            每个出货位是一个交付点：四向车从车库取货 → 经巷道出库 → 驶上地面输送线就地交付，货箱沿带面运走。设置保存在当前 Run 里，不改拓扑。
          </p>
        </div>
      </div>

      <div className="shuttle-delivery-summary">
        <span>待派 {pending}</span>
        <span>执行中 {running}</span>
        <span>已完成 {done}</span>
      </div>

      <div className="shuttle-delivery-robots">
        {shuttleRobots.length ? (
          shuttleRobots.map((robot) => (
            <div key={robot.id} className="shuttle-delivery-robot">
              <Bot size={15} />
              <b>{robot.id}</b>
              <span>{robot.phaseLabel || robot.status}</span>
              <small>
                {robot.load}/{robot.capacity} 箱
              </small>
            </div>
          ))
        ) : (
          <p className="muted-copy">
            拓扑中还没有四向车设备：请到拓扑编辑器用「四向车」工具在车库巷道上放置，保存后重新启动仿真。
          </p>
        )}
      </div>

      {docks.length ? (
        docks.map((dock) => (
          <DockCard
            key={dock.dockId}
            runId={runId}
            dock={dock}
            tasks={shuttleTasks}
            robots={robots}
            onChanged={onChanged}
          />
        ))
      ) : (
        <div className="shuttle-dock-card">
          <p className="muted-copy">
            拓扑中还没有「四向车出货位」：到拓扑编辑器用「出货位」工具放在地面输送线上（只能放在地面线，
            四向车仅沿巷道、升降通道和地面输送线移动），点击「保存拓扑」后无需重启仿真，出货位会立即出现在这里；
            在卡片上设置取什么货物、取多少并保存，即自动生成搬运任务。
          </p>
        </div>
      )}
    </aside>
  )
}
