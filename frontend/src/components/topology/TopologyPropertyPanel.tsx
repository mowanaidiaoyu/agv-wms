import { useState } from 'react'
import { Trash2, Layers, Box, MapPin, Type, RefreshCw } from 'lucide-react'
import { TopologyGlyph } from '../TopologyGlyph'
import {
  CELL_LABELS,
  DIRECTION_LABELS,
  NODE_LABELS,
  OBJECT_LABELS,
  ZONE_LABELS,
  boxToRect,
  conveyorAnchorOf,
  floorsOf,
  isPointInBox,
  nodeFootprint,
  shuttleSlotAisleMask,
  shuttleSlotLift,
  shuttleSlotRemoved,
  slotNumberFor,
} from '../../lib/topology'
import type { EditorObjectType } from '../../types'
import { useTopologyEditorStore } from '../../store/useTopologyEditorStore'
import type { CellType, ConveyorDirection, NodeType, TopologyCell, TopologyPayload, ZoneType } from '../../types'

const CELL_TYPES: CellType[] = ['road', 'blocked', 'conveyor']
const ZONE_TYPES: ZoneType[] = ['ctu', 'shuttle', 'port']
const DIRECTIONS: ConveyorDirection[] = ['right', 'down', 'left', 'up']

function NumericField({
  label,
  value,
  min,
  step,
  onChange,
  disabled,
}: {
  label: string
  value: number
  min?: number
  step?: number
  onChange: (value: number) => void
  disabled?: boolean
}) {
  return (
    <label className="topo-property-field">
      <span>{label}</span>
      <input
        type="number"
        value={Number.isFinite(value) ? value : ''}
        min={min}
        step={step ?? 1}
        disabled={disabled}
        onChange={(event) => {
          const next = Number(event.target.value)
          if (Number.isFinite(next)) onChange(next)
        }}
      />
    </label>
  )
}

function ReadonlyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="topo-property-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

const KIND_LABELS: Record<EditorObjectType | NodeType, string> = { ...OBJECT_LABELS, ...NODE_LABELS }

function EntityBadge({ kind, id }: { kind: EditorObjectType | NodeType; id: string }) {
  return (
    <div className="topo-property-hero">
      <TopologyGlyph kind={kind} size="panel" />
      <div className="topo-property-hero-text">
        <p className="topology-property-eyebrow">{KIND_LABELS[kind]}</p>
        <h3>{id}</h3>
      </div>
    </div>
  )
}

export function TopologyPropertyPanel() {
  const draft = useTopologyEditorStore((state) => state.draft)
  const selection = useTopologyEditorStore((state) => state.selection)
  const activeFloor = useTopologyEditorStore((state) => state.activeFloor)
  const updateCell = useTopologyEditorStore((state) => state.updateCell)
  const updateNode = useTopologyEditorStore((state) => state.updateNode)
  const moveNode = useTopologyEditorStore((state) => state.moveNode)
  const patchZone = useTopologyEditorStore((state) => state.patchZone)
  const resyncZone = useTopologyEditorStore((state) => state.resyncZone)
  const deleteZone = useTopologyEditorStore((state) => state.deleteZone)
  const batchAssignZone = useTopologyEditorStore((state) => state.batchAssignZone)
  const deleteSelectedNode = useTopologyEditorStore((state) => state.deleteSelectedNode)
  const deleteSelectedCell = useTopologyEditorStore((state) => state.deleteSelectedCell)
  const updateConveyorLevels = useTopologyEditorStore((state) => state.updateConveyorLevels)
  const applyShuttleAisle = useTopologyEditorStore((state) => state.applyShuttleAisle)
  const applyShuttleSlotRemove = useTopologyEditorStore((state) => state.applyShuttleSlotRemove)
  const applyShuttleLift = useTopologyEditorStore((state) => state.applyShuttleLift)
  const activeShuttleLevel = useTopologyEditorStore((state) => state.activeShuttleLevel)
  const setActiveShuttleLevel = useTopologyEditorStore((state) => state.setActiveShuttleLevel)

  const zones = draft.zones
  const floors = floorsOf(draft)
  const cell = selection?.kind === 'cell' ? draft.cells.find((item) => item.id === selection.id) : undefined
  const node = selection?.kind === 'node' ? draft.nodes.find((item) => item.id === selection.id) : undefined
  const zone = selection?.kind === 'zone' ? draft.zones.find((item) => item.id === selection.id) : undefined

  if (selection?.kind === 'storage-batch') {
    return <StorageBatchEditor selection={selection} zones={zones} activeFloor={activeFloor} draft={draft} onAssign={batchAssignZone} />
  }

  if (selection?.kind === 'conveyor-batch') {
    return <ConveyorBatchEditor selection={selection} onApply={updateConveyorLevels} />
  }

  if (selection?.kind === 'shuttle-batch') {
    return (
      <ShuttleBatchEditor
        selection={selection}
        onApply={applyShuttleAisle}
        onRemove={applyShuttleSlotRemove}
        onLift={applyShuttleLift}
        activeShuttleLevel={activeShuttleLevel}
        onLevelChange={setActiveShuttleLevel}
      />
    )
  }

  if (selection?.kind === 'shuttle-slot') {
    return (
      <ShuttleSlotEditor
        selection={selection}
        draft={draft}
        onApply={applyShuttleAisle}
        onRemove={applyShuttleSlotRemove}
        onLift={applyShuttleLift}
        activeShuttleLevel={activeShuttleLevel}
        onLevelChange={setActiveShuttleLevel}
      />
    )
  }

  if (selection?.kind === 'conveyor-run') {
    return (
      <ConveyorRunEditor
        selection={selection}
        draft={draft}
        onUpdateCell={updateCell}
        onSetLevels={updateConveyorLevels}
      />
    )
  }

  if (cell) {
    return (
      <div className="topo-property-body">
        <EntityBadge kind={cell.type === 'road' ? 'road' : cell.type === 'blocked' ? 'blocked' : 'conveyor'} id={cell.id} />
        <ReadonlyRow label="坐标" value={`(${cell.row}, ${cell.col}) ${cell.floor ?? '1F'}`} />
        <label className="topo-property-field">
          <span>介质类型</span>
          <select
            value={cell.type}
            onChange={(event) => {
              const type = event.target.value as CellType
              updateCell(cell.id, {
                type,
                walkable: type !== 'blocked',
                conveyorDirection: type === 'conveyor' ? (cell.conveyorDirection ?? 'right') : null,
              })
            }}
          >
            {CELL_TYPES.map((item) => (
              <option key={item} value={item}>
                {CELL_LABELS[item]}
              </option>
            ))}
          </select>
        </label>
        {cell.type === 'conveyor' ? (
          <>
            <label className="topo-property-field">
              <span>输送方向</span>
              <select
                value={cell.conveyorDirection ?? 'right'}
                onChange={(event) => updateCell(cell.id, { conveyorDirection: event.target.value as ConveyorDirection })}
              >
                {DIRECTIONS.map((direction) => (
                  <option key={direction} value={direction}>
                    {DIRECTION_LABELS[direction]}
                  </option>
                ))}
              </select>
            </label>
            <label className="topo-property-field">
              <span>层数</span>
              <select
                value={cell.levels ?? 1}
                onChange={(event) => updateCell(cell.id, { levels: Number(event.target.value) })}
              >
                <option value={1}>单层</option>
                <option value={2}>双层</option>
              </select>
            </label>
          </>
        ) : null}
        <NumericField
          label="通行代价 cost"
          value={cell.cost}
          step={0.1}
          min={0.1}
          onChange={(cost) => updateCell(cell.id, { cost })}
        />
        <label className="topo-property-field">
          <span>所属区域</span>
          <select
            value={cell.zoneId ?? ''}
            onChange={(event) => updateCell(cell.id, { zoneId: event.target.value || null })}
          >
            <option value="">（无）</option>
            {zones.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label} · {ZONE_LABELS[item.type]}
              </option>
            ))}
          </select>
        </label>
        <button className="ghost-button topo-property-danger" type="button" onClick={deleteSelectedCell}>
          <Trash2 size={14} />
          删除该格
        </button>
      </div>
    )
  }

  if (node) {
    const kind = node.type
    return (
      <div className="topo-property-body">
        <EntityBadge kind={kind} id={node.id} />
        {node.type === 'storage' && nodeFootprint(draft, node) > 1 ? (
          <ReadonlyRow label="占位" value="2×2 四格（四向车平库）" />
        ) : null}
        {node.type === 'storage' && node.zoneId ? (
          <ReadonlyRow label="编号" value={slotNumberFor(draft, node) ?? '—'} />
        ) : null}
        <div className="topo-property-geometry">
          <NumericField
            label="行 row"
            value={node.row}
            min={0}
            onChange={(row) => moveNode(node.id, Math.max(0, Math.floor(row)), node.col, node.floor ?? '1F')}
          />
          <NumericField
            label="列 col"
            value={node.col}
            min={0}
            onChange={(col) => moveNode(node.id, node.row, Math.max(0, Math.floor(col)), node.floor ?? '1F')}
          />
        </div>
        <label className="topo-property-field">
          <span>楼层</span>
          <select value={node.floor ?? '1F'} onChange={(event) => moveNode(node.id, node.row, node.col, event.target.value)}>
            {floors.map((floor) => (
              <option key={floor} value={floor}>
                {floor}
              </option>
            ))}
          </select>
        </label>
        <label className="topo-property-field">
          <span>标签</span>
          <input value={node.label} onChange={(event) => updateNode(node.id, { label: event.target.value })} />
        </label>
        <NumericField label="容量" value={node.capacity} min={1} onChange={(capacity) => updateNode(node.id, { capacity: Math.max(1, Math.floor(capacity)) })} />
        <label className="topo-property-field">
          <span>所属区域</span>
          <select value={node.zoneId ?? ''} onChange={(event) => updateNode(node.id, { zoneId: event.target.value || null })}>
            <option value="">（无）</option>
            {zones.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label} · {ZONE_LABELS[item.type]}
              </option>
            ))}
          </select>
        </label>

        <button className="ghost-button topo-property-danger" type="button" onClick={deleteSelectedNode}>
          <Trash2 size={14} />
          删除该节点
        </button>
      </div>
    )
  }

  if (zone) {
    const storageNodes = draft.nodes.filter((n) => n.type === 'storage' && n.zoneId === zone.id)
    return (
      <div className="topo-property-body">
        <EntityBadge kind="storage" id={zone.id} />
        <ReadonlyRow label="区域类型" value={ZONE_LABELS[zone.type]} />
        <div className="topo-property-zone-metrics">
          <span>W×H {zone.width}×{zone.height}</span>
          <span>库位 {storageNodes.length}</span>
          <span>右下 ({zone.y + zone.height - 1}, {zone.x + zone.width - 1})</span>
        </div>
        <label className="topo-property-field">
          <span>标签</span>
          <input value={zone.label} onChange={(event) => patchZone(zone.id, { label: event.target.value })} />
        </label>
        <label className="topo-property-field">
          <span>类型</span>
          <select
            value={zone.type}
            onChange={(event) => {
              patchZone(zone.id, { type: event.target.value as ZoneType })
              resyncZone(zone.id)
            }}
          >
            {ZONE_TYPES.map((item) => (
              <option key={item} value={item}>
                {ZONE_LABELS[item]}
              </option>
            ))}
          </select>
        </label>
        <div className="topo-property-geometry">
          <NumericField label="x" value={zone.x} onChange={(x) => patchZone(zone.id, { x: Math.max(0, Math.floor(x)) })} />
          <NumericField label="y" value={zone.y} onChange={(y) => patchZone(zone.id, { y: Math.max(0, Math.floor(y)) })} />
          <NumericField label="宽" value={zone.width} min={1} onChange={(width) => patchZone(zone.id, { width: Math.max(1, Math.floor(width)) })} />
          <NumericField label="高" value={zone.height} min={1} onChange={(height) => patchZone(zone.id, { height: Math.max(1, Math.floor(height)) })} />
        </div>
        <button className="ghost-button" type="button" onClick={() => resyncZone(zone.id)}>
          <RefreshCw size={14} />
          重算归属
        </button>
        <button className="ghost-button topo-property-danger" type="button" onClick={() => deleteZone(zone.id)}>
          <Trash2 size={14} />
          删除区域
        </button>
      </div>
    )
  }

  return (
    <div className="topo-property-body topo-property-empty">
      <Box size={40} strokeWidth={1.2} />
      <p className="surface-subtitle">未选择对象</p>
      <p className="muted-copy">
        用“选择”点选格子、节点或框选库位；用区域工具拉矩形；选中后在此编辑属性。
      </p>
      <div className="topo-property-legend">
        <p>
          <Type size={13} /> 当前层 {activeFloor}
        </p>
        <p>
          <Layers size={13} /> 共 {draft.zones.length} 个区域 / {draft.cells.length} 格 / {draft.nodes.length} 节点
        </p>
      </div>
    </div>
  )
}

function StorageBatchEditor({
  selection,
  zones,
  activeFloor,
  draft,
  onAssign,
}: {
  selection: { kind: 'storage-batch'; ids: string[]; box: { row0: number; col0: number; row1: number; col1: number } }
  zones: { id: string; label: string; type: ZoneType }[]
  activeFloor: string
  draft: { nodes: { id: string; type: NodeType; row: number; col: number; floor?: string; zoneId?: string | null }[] }
  onAssign: (ids: string[], zoneId: string | null) => void
}) {
  const [value, setValue] = useState('')
  const box = selection.box
  const rect = box ? boxToRect(box) : null
  const storageNodes = draft.nodes.filter(
    (n) => n.type === 'storage' && (n.floor ?? '1F') === activeFloor && box && isPointInBox(box, n.row, n.col),
  )
  const ctuCount = storageNodes.filter((n) => n.zoneId && zones.find((z) => z.id === n.zoneId)?.type === 'ctu').length
  const shuttleCount = storageNodes.filter((n) => n.zoneId && zones.find((z) => z.id === n.zoneId)?.type === 'shuttle').length

  return (
    <div className="topo-property-body">
      <div className="topo-property-hero">
        <MapPin size={26} />
        <div className="topo-property-hero-text">
          <p className="topology-property-eyebrow">库位批量</p>
          <h3>框选 {selection.ids.length} 个</h3>
        </div>
      </div>
      <ReadonlyRow label="框选范围" value={rect ? `${rect.width}×${rect.height} @ (${box.row0},${box.col0})` : '（无）'} />
      <div className="topo-property-zone-metrics">
        <span>共 {selection.ids.length} 库位</span>
        <span>CTU {ctuCount}</span>
        <span>四向车 {shuttleCount}</span>
      </div>
      <label className="topo-property-field">
        <span>批量归属区域</span>
        <select value={value} onChange={(event) => setValue(event.target.value)}>
          <option value="">（取消归属）</option>
          {zones.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label} · {ZONE_LABELS[item.type]}
            </option>
          ))}
        </select>
      </label>
      <button
        className="accent-button"
        type="button"
        onClick={() => onAssign(selection.ids, value || null)}
      >
        应用到 {selection.ids.length} 个库位
      </button>
    </div>
  )
}

function ConveyorBatchEditor({
  selection,
  onApply,
}: {
  selection: { kind: 'conveyor-batch'; ids: string[]; box: { row0: number; col0: number; row1: number; col1: number } }
  onApply: (ids: string[], levels: number) => void
}) {
  const [levels, setLevels] = useState(2)
  const box = selection.box
  const rect = boxToRect(box)

  return (
    <div className="topo-property-body">
      <div className="topo-property-hero">
        <Layers size={26} />
        <div className="topo-property-hero-text">
          <p className="topology-property-eyebrow">输送带批量</p>
          <h3>框选 {selection.ids.length} 格</h3>
        </div>
      </div>
      <ReadonlyRow label="框选范围" value={rect ? `${rect.width}×${rect.height} @ (${box.row0},${box.col0})` : '（无）'} />
      <div className="topo-property-zone-metrics">
        <span>共 {selection.ids.length} 格输送带</span>
      </div>
      <label className="topo-property-field">
        <span>批量层数</span>
        <select value={levels} onChange={(event) => setLevels(Number(event.target.value))}>
          <option value={1}>单层</option>
          <option value={2}>双层</option>
        </select>
      </label>
      <button className="accent-button" type="button" onClick={() => onApply(selection.ids, levels)}>
        应用到 {selection.ids.length} 格输送带
      </button>
    </div>
  )
}

function ConveyorRunEditor({
  selection,
  draft,
  onUpdateCell,
  onSetLevels,
}: {
  selection: { kind: 'conveyor-run'; cellId: string; ids: string[]; floor: string }
  draft: TopologyPayload
  onUpdateCell: (id: string, patch: Partial<TopologyCell>) => void
  onSetLevels: (ids: string[], levels: number) => void
}) {
  const runCells = draft.cells.filter((cell) => selection.ids.includes(cell.id))

  // 按 2×2 块锚点去重，得到逐块方向编辑项（保持逐块方向可改，不回归单格编辑能力）
  const blocks: { row: number; col: number; cellId: string; direction: ConveyorDirection }[] = []
  const seen = new Set<string>()
  for (const cell of runCells) {
    const a = conveyorAnchorOf(cell)
    const key = `${a.row}:${a.col}`
    if (seen.has(key)) continue
    seen.add(key)
    const anchorCell = runCells.find((item) => item.row === a.row && item.col === a.col)
    blocks.push({
      row: a.row,
      col: a.col,
      cellId: anchorCell?.id ?? cell.id,
      direction: (anchorCell?.conveyorDirection ?? cell.conveyorDirection ?? 'right') as ConveyorDirection,
    })
  }

  const level = runCells[0]?.levels ?? 1

  return (
    <div className="topo-property-body">
      <div className="topo-property-hero">
        <Layers size={26} />
        <div className="topo-property-hero-text">
          <p className="topology-property-eyebrow">输送线</p>
          <h3>整条连通输送线 · {runCells.length} 格</h3>
        </div>
      </div>
      <ReadonlyRow label="规模" value={`${blocks.length} 个 2×2 块 · ${selection.floor}`} />
      <div className="topo-property-note">
        <strong>拖动可移动整条输送线</strong>
        <p>在画布按住该输送带拖动，即可整体平移到新位置（保持 2×2 块对齐）。</p>
      </div>
      <label className="topo-property-field">
        <span>整线层数</span>
        <select value={level} onChange={(event) => onSetLevels(selection.ids, Number(event.target.value))}>
          <option value={1}>单层</option>
          <option value={2}>双层</option>
        </select>
      </label>
      {blocks.map((block) => (
        <label className="topo-property-field" key={`${block.row}-${block.col}`}>
          <span>块 ({block.row}, {block.col}) 方向</span>
          <select
            value={block.direction}
            onChange={(event) => onUpdateCell(block.cellId, { conveyorDirection: event.target.value as ConveyorDirection })}
          >
            {DIRECTIONS.map((direction) => (
              <option key={direction} value={direction}>
                {DIRECTION_LABELS[direction]}
              </option>
            ))}
          </select>
        </label>
      ))}
    </div>
  )
}

function ShuttleLevelChips({ active, onChange }: { active: number; onChange: (level: number) => void }) {
  return (
    <div className="topology-floor-switcher">
      {[1, 2, 3, 4].map((level) => (
        <button
          key={level}
          type="button"
          className={`topology-floor-chip ${level === active ? 'topology-floor-chip-active' : ''}`}
          onClick={() => onChange(level)}
        >
          第{level}层
        </button>
      ))}
    </div>
  )
}

function ShuttleBatchEditor({
  selection,
  onApply,
  onRemove,
  onLift,
  activeShuttleLevel,
  onLevelChange,
}: {
  selection: { kind: 'shuttle-batch'; slots: { row: number; col: number }[]; box: { row0: number; col0: number; row1: number; col1: number } }
  onApply: (slots: { row: number; col: number }[], aisle: boolean) => void
  onRemove: (slots: { row: number; col: number }[], removed: boolean) => void
  onLift: (slots: { row: number; col: number }[], lift: boolean) => void
  activeShuttleLevel: number
  onLevelChange: (level: number) => void
}) {
  const box = selection.box
  const rect = boxToRect(box)

  return (
    <div className="topo-property-body">
      <div className="topo-property-hero">
        <MapPin size={26} />
        <div className="topo-property-hero-text">
          <p className="topology-property-eyebrow">四向车库巷道</p>
          <h3>框选 {selection.slots.length} 个货架槽位</h3>
        </div>
      </div>
      <ReadonlyRow label="框选范围" value={rect ? `${rect.width}×${rect.height} @ (${box.row0},${box.col0})` : '（无）'} />
      <div className="topo-property-zone-metrics">
        <span>共 {selection.slots.length} 个 2×2 槽位</span>
      </div>
      <label className="topo-property-field">
        <span>货架层</span>
        <ShuttleLevelChips active={activeShuttleLevel} onChange={onLevelChange} />
      </label>
      <button className="accent-button" type="button" onClick={() => onApply(selection.slots, true)}>
        第 {activeShuttleLevel} 层设为巷道（蓝色，供四向车通行）
      </button>
      <button className="ghost-button" type="button" onClick={() => onApply(selection.slots, false)}>
        第 {activeShuttleLevel} 层恢复货架（橙色存储）
      </button>
      <button className="accent-button" type="button" onClick={() => onLift(selection.slots, true)}>
        设为升降通道（绿色竖井，跨层必经）
      </button>
      <button className="ghost-button" type="button" onClick={() => onLift(selection.slots, false)}>
        恢复货架（取消升降通道）
      </button>
      <button className="ghost-button topo-property-danger" type="button" onClick={() => onRemove(selection.slots, true)}>
        <Trash2 size={14} />
        删除选中槽位（挖空为空位）
      </button>
      <button className="ghost-button" type="button" onClick={() => onRemove(selection.slots, false)}>
        恢复选中槽位（回到货架）
      </button>
    </div>
  )
}

function ShuttleSlotEditor({
  selection,
  draft,
  onApply,
  onRemove,
  onLift,
  activeShuttleLevel,
  onLevelChange,
}: {
  selection: { kind: 'shuttle-slot'; zoneId: string; row: number; col: number; floor: string }
  draft: TopologyPayload
  onApply: (slots: { row: number; col: number }[], aisle: boolean, floor?: string) => void
  onRemove: (slots: { row: number; col: number }[], removed: boolean, floor?: string) => void
  onLift: (slots: { row: number; col: number }[], lift: boolean, floor?: string) => void
  activeShuttleLevel: number
  onLevelChange: (level: number) => void
}) {
  const { zoneId, row, col, floor } = selection
  const floorKey = floor ?? '1F'
  const removed = shuttleSlotRemoved(draft, zoneId, row, col)
  const isLift = shuttleSlotLift(draft, zoneId, row, col)
  const mask = shuttleSlotAisleMask(draft, zoneId, row, col, floorKey)
  const isAisle = Boolean(mask & (1 << (activeShuttleLevel - 1)))

  return (
    <div className="topo-property-body">
      <div className="topo-property-hero">
        <MapPin size={26} />
        <div className="topo-property-hero-text">
          <p className="topology-property-eyebrow">四向车库库位</p>
          <h3>{removed ? '已删除（空位）' : isLift ? '升降通道（跨层竖井）' : `第 ${activeShuttleLevel} 层 · ${isAisle ? '巷道（通行）' : '货架（存储）'}`}</h3>
        </div>
      </div>
      <ReadonlyRow label="槽位" value={`(${row}, ${col}) 2×2 · ${floorKey}`} />
      {removed ? (
        <button className="accent-button" type="button" onClick={() => onRemove([{ row, col }], false, floor)}>
          恢复该槽位（回到货架）
        </button>
      ) : (
        <>
          {isLift ? (
            <>
              <div className="topo-property-zone-metrics">
                <span>贯穿 4 层货架的升降竖井，四向车跨层必经</span>
              </div>
              <button className="ghost-button" type="button" onClick={() => onLift([{ row, col }], false, floor)}>
                恢复货架（取消升降通道）
              </button>
            </>
          ) : (
            <>
              <label className="topo-property-field">
                <span>货架层</span>
                <ShuttleLevelChips active={activeShuttleLevel} onChange={onLevelChange} />
              </label>
              <div className="topo-property-zone-metrics">
                {[1, 2, 3, 4].map((level) => (
                  <span key={level}>
                    第 {level} 层 {mask & (1 << (level - 1)) ? '巷道' : '货架'}
                  </span>
                ))}
              </div>
              {isAisle ? (
                <button className="ghost-button" type="button" onClick={() => onApply([{ row, col }], false, floor)}>
                  第 {activeShuttleLevel} 层恢复货架（橙色存储）
                </button>
              ) : (
                <button className="accent-button" type="button" onClick={() => onApply([{ row, col }], true, floor)}>
                  第 {activeShuttleLevel} 层设为巷道（蓝色，供四向车通行）
                </button>
              )}
              <button className="accent-button" type="button" onClick={() => onLift([{ row, col }], true, floor)}>
                设为升降通道（绿色竖井，跨层必经）
              </button>
            </>
          )}
          <button className="ghost-button topo-property-danger" type="button" onClick={() => onRemove([{ row, col }], true, floor)}>
            <Trash2 size={14} />
            删除该槽位（挖空为空位）
          </button>
        </>
      )}
    </div>
  )
}
