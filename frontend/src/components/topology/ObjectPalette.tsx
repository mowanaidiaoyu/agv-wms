import { clsx } from 'clsx'
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { TopologyGlyph } from '../TopologyGlyph'
import { OBJECT_LABELS, ZONE_LABELS, ZONE_TYPE_ORDER } from '../../lib/topology'
import { useTopologyEditorStore } from '../../store/useTopologyEditorStore'
import type { CellType, EditorObjectType, ZoneType } from '../../types'

const TOOL_HINTS: Partial<Record<EditorObjectType, string>> = {
  select: '点选格子 / 节点 / 区域；按住拖动框选多个库位',
  batch: '先点起点、再点终点框选矩形；按上下文条选择「批量选择 / 批量放置」',
  erase: '点击或拖拽删除对象（节点优先）',
  road: '拖拽铺设通行路',
  blocked: '拖拽铺设障碍物（不可通行）',
  conveyor: '拖拽铺设输送带，方向自动按走向',
  ctu_robot: '点击放单个；拖拽矩形批量放置 CTU 设备',
  shuttle_robot: '点击放单个；拖拽矩形批量放置四向车设备',
  lift_transfer: '点击放单个；拖拽矩形批量放置升降移载机（可落在输送带格上）',
  turn_transfer: '点击放单个；拖拽矩形批量放置转向移载机（可落在输送带格上）',
}

const ZONE_HINTS: Record<ZoneType, string> = {
  ctu: '拖拽拉出 CTU 高架库矩形（自动生成 17 层库位）',
  shuttle: '拖拽拉出四向车平库矩形',
  port: '拖拽拉出输送线出入口矩形（下方入、上方出）',
}

const CELL_TOOL_TYPES: CellType[] = ['road', 'blocked', 'conveyor']
const NODE_TOOL_TYPES: EditorObjectType[] = ['ctu_robot', 'shuttle_robot', 'lift_transfer', 'turn_transfer']

const COMPACT_TOOL_LABELS: Partial<Record<EditorObjectType, string>> = {
  select: '选择',
  batch: '批量',
  erase: '删除',
  road: '道路',
  blocked: '障碍',
  conveyor: '输送',
  ctu_robot: 'CTU',
  shuttle_robot: '四向车',
  lift_transfer: '升降',
  turn_transfer: '转向',
}

const COMPACT_ZONE_LABELS: Record<ZoneType, string> = {
  ctu: 'CTU库',
  shuttle: '四向库',
  port: '出入口',
}

function ToolButton({
  tool,
  active,
  onClick,
}: {
  tool: EditorObjectType
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={clsx('topo-tool-button', active && 'topo-tool-button-active')}
      aria-pressed={active}
      title={TOOL_HINTS[tool] ?? OBJECT_LABELS[tool]}
      onClick={onClick}
    >
      <TopologyGlyph kind={tool} size="card" />
      <span className="topo-tool-label">{OBJECT_LABELS[tool]}</span>
      <span className="topo-tool-compact-label">{COMPACT_TOOL_LABELS[tool] ?? OBJECT_LABELS[tool]}</span>
    </button>
  )
}

function ZoneButton({
  type,
  active,
  onClick,
}: {
  type: ZoneType
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={clsx('topo-zone-button', active && 'topo-zone-button-active')}
      aria-pressed={active}
      title={ZONE_HINTS[type]}
      onClick={onClick}
    >
      <span className={`topo-zone-swatch topo-zone-swatch-${type}`} />
      <span className="topo-zone-label">{ZONE_LABELS[type]}</span>
      <span className="topo-zone-compact-label">{COMPACT_ZONE_LABELS[type]}</span>
    </button>
  )
}

export function ObjectPalette({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const activeTool = useTopologyEditorStore((state) => state.activeTool)
  const activeZoneType = useTopologyEditorStore((state) => state.activeZoneType)
  const setActiveTool = useTopologyEditorStore((state) => state.setActiveTool)
  const setActiveZoneType = useTopologyEditorStore((state) => state.setActiveZoneType)

  const hint = activeZoneType
    ? ZONE_HINTS[activeZoneType]
    : (TOOL_HINTS[activeTool] ?? '')

  return (
    <div className={clsx('topo-palette', collapsed && 'topo-palette-collapsed')}>
      <div className="topo-palette-header">
        <strong>编辑工具</strong>
        <button
          type="button"
          className="topo-palette-toggle"
          onClick={onToggle}
          title={collapsed ? '展开编辑工具' : '收起编辑工具'}
          aria-label={collapsed ? '展开编辑工具' : '收起编辑工具'}
        >
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
        </button>
      </div>
      <div className="topo-palette-group">
        <p className="topo-palette-group-title">指针</p>
        <ToolButton tool="select" active={!activeZoneType && activeTool === 'select'} onClick={() => setActiveTool('select')} />
        <ToolButton tool="batch" active={!activeZoneType && activeTool === 'batch'} onClick={() => setActiveTool('batch')} />
        <ToolButton tool="erase" active={!activeZoneType && activeTool === 'erase'} onClick={() => setActiveTool('erase')} />
      </div>

      <div className="topo-palette-group">
        <p className="topo-palette-group-title">铺装</p>
        {CELL_TOOL_TYPES.map((cell) => (
          <ToolButton
            key={cell}
            tool={cell}
            active={!activeZoneType && activeTool === cell}
            onClick={() => setActiveTool(cell)}
          />
        ))}
      </div>

      <div className="topo-palette-group">
        <p className="topo-palette-group-title">区域（拉矩形）</p>
        {ZONE_TYPE_ORDER.map((zone) => (
          <ZoneButton
            key={zone}
            type={zone}
            active={activeZoneType === zone}
            onClick={() => setActiveZoneType(activeZoneType === zone ? null : zone)}
          />
        ))}
      </div>

      <div className="topo-palette-group">
        <p className="topo-palette-group-title">节点</p>
        {NODE_TOOL_TYPES.map((tool) => (
          <ToolButton
            key={tool}
            tool={tool}
            active={!activeZoneType && activeTool === tool}
            onClick={() => setActiveTool(tool)}
          />
        ))}
      </div>

      <p className="topo-palette-hint">{hint}</p>
    </div>
  )
}
