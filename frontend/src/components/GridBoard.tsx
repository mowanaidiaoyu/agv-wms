import type { ReactNode } from 'react'
import { Minus, Plus, Scan, Scaling } from 'lucide-react'
import { useGridViewport } from '../hooks/useGridViewport'
import { TopologyGlyph } from './TopologyGlyph'
import type { ConveyorDirection, EditorObjectType, NodeType, TopologyCell, TopologyNode, Zone } from '../types'

type BoardRobot = {
  id: string
  type: 'ctu' | 'shuttle'
  floor?: string
  row: number
  col: number
  status?: string
  failed?: boolean
  currentTaskId?: string | null
  phaseLabel?: string
}

type GridBoardSelectionBox = {
  row0: number
  col0: number
  row1: number
  col1: number
}

type GridBoardProps = {
  rows: number
  cols: number
  visualSize?: number
  cells?: TopologyCell[]
  nodes?: TopologyNode[]
  zones?: Zone[]
  robots?: BoardRobot[]
  activeFloor?: string
  selectedCellId?: string
  selectedNodeId?: string
  selectionBox?: GridBoardSelectionBox | null
  zonesAllFloors?: boolean
  hideEmptyState?: boolean
  interactiveMode?: 'edit' | 'readonly'
  initialView?: 'fit' | 'actual'
  viewportSizing?: 'content-aware' | 'screen-fill'
  minViewportHeight?: number
  maxViewportHeight?: number
  enableZoom?: boolean
  enablePan?: boolean
  panModifierActive?: boolean
  controlsAddon?: ReactNode
  onCellMouseDown?: (row: number, col: number) => void
  onCellMouseEnter?: (row: number, col: number) => void
  onCellClick?: (row: number, col: number) => void
}

const nodeTypeToObjectType: Record<NodeType, NodeType> = {
  storage: 'storage',
  ctu_robot: 'ctu_robot',
  shuttle_robot: 'shuttle_robot',
  lift_transfer: 'lift_transfer',
  turn_transfer: 'turn_transfer',
}

const objectLabels: Record<EditorObjectType | NodeType, string> = {
  select: '选择',
  batch: '批量选择',
  erase: '删除',
  road: '通行路',
  blocked: '障碍物',
  conveyor: '输送带',
  storage: '库位',
  ctu_robot: 'CTU',
  shuttle_robot: '四向车',
  lift_transfer: '升降移载机',
  turn_transfer: '转向移载机',
}

function buildCellId(row: number, col: number, floor = '1F') {
  if (floor === '1F') {
    return `cell-r${row}-c${col}`
  }
  return `cell-${floor}-r${row}-c${col}`
}

function sameFloor(value?: string | null, activeFloor = '1F') {
  return (value || '1F') === activeFloor
}

function legacyCellId(row: number, col: number) {
  return `cell-r${row}-c${col}`
}

function zoneClassName(zone: Zone) {
  return `grid-zone grid-zone-${zone.type}`
}

function cellObjectType(cell: TopologyCell | undefined): EditorObjectType | null {
  if (!cell) return null
  if (cell.type === 'blocked') return 'blocked'
  if (cell.type === 'conveyor') return 'conveyor'
  if (cell.type === 'road') return 'road'
  return null
}

function robotObjectType(type: BoardRobot['type']): EditorObjectType {
  return type === 'ctu' ? 'ctu_robot' : 'shuttle_robot'
}

export function GridBoard({
  rows,
  cols,
  visualSize = 48,
  cells = [],
  nodes = [],
  zones = [],
  robots = [],
  activeFloor = '1F',
  selectedCellId,
  selectedNodeId,
  selectionBox,
  zonesAllFloors = false,
  hideEmptyState = false,
  interactiveMode = 'readonly',
  initialView = 'fit',
  viewportSizing = 'screen-fill',
  minViewportHeight,
  maxViewportHeight,
  enableZoom = true,
  enablePan = true,
  panModifierActive = false,
  controlsAddon,
  onCellMouseDown,
  onCellMouseEnter,
  onCellClick,
}: GridBoardProps) {
  const boardWidth = cols * visualSize
  const boardHeight = rows * visualSize
  const canPanFromPointer = enablePan && (interactiveMode === 'readonly' || panModifierActive)
  const visibleCells = cells.filter((cell) => sameFloor(cell.floor, activeFloor))
  const visibleNodes = nodes.filter((node) => sameFloor(node.floor, activeFloor))
  const visibleRobots = robots.filter((robot) => sameFloor(robot.floor, activeFloor))
  const isEmptyTopology = visibleCells.length === 0 && visibleNodes.length === 0
  const viewportStyle =
    viewportSizing === 'content-aware'
      ? {
          aspectRatio: `${cols} / ${rows}`,
          minHeight: `${minViewportHeight ?? 380}px`,
          maxHeight: `${maxViewportHeight ?? 680}px`,
        }
      : undefined

  const {
    viewportRef,
    view,
    isDragging,
    fitToView,
    resetToActual,
    zoomIn,
    zoomOut,
    transformStyle,
    beginPan,
    handlePointerMove,
    handlePointerUp,
  } = useGridViewport({
    boardWidth,
    boardHeight,
    initialView,
    fitMode: viewportSizing === 'content-aware' ? 'width-priority' : 'contain',
  })

  const cellLookup = new Map(visibleCells.map((cell) => [cell.id, cell]))
  const cellIdByPosition = new Map(visibleCells.map((cell) => [`${cell.row}:${cell.col}`, cell.id]))
  const nodesByCell = new Map<string, TopologyNode[]>()
  const robotsByCell = new Map<string, BoardRobot[]>()

  for (const node of visibleNodes) {
    const cellId = cellIdByPosition.get(`${node.row}:${node.col}`) ?? buildCellId(node.row, node.col, activeFloor)
    const bucket = nodesByCell.get(cellId) ?? []
    bucket.push(node)
    nodesByCell.set(cellId, bucket)
  }

  for (const robot of visibleRobots) {
    const cellId = cellIdByPosition.get(`${robot.row}:${robot.col}`) ?? buildCellId(robot.row, robot.col, activeFloor)
    const bucket = robotsByCell.get(cellId) ?? []
    bucket.push(robot)
    robotsByCell.set(cellId, bucket)
  }

  const zoneOverlays = zones.filter((zone) => zonesAllFloors || sameFloor(zone.floor, activeFloor)).map((zone) => (
    <div
      key={zone.id}
      className={zoneClassName(zone)}
      style={{
        left: zone.x * visualSize,
        top: zone.y * visualSize,
        width: zone.width * visualSize,
        height: zone.height * visualSize,
      }}
      title={zone.label}
    >
      <span className="grid-zone-label">{zone.label}</span>
    </div>
  ))

  const tiles = Array.from({ length: rows * cols }, (_, index) => {
    const row = Math.floor(index / cols)
    const col = index % cols
    const canonicalId = buildCellId(row, col, activeFloor)
    const id = cellIdByPosition.get(`${row}:${col}`) ?? canonicalId
    const cell = cellLookup.get(id) ?? (activeFloor === '1F' ? cellLookup.get(legacyCellId(row, col)) : undefined)
    const tileNodes = nodesByCell.get(id) ?? []
    const tileRobots = robotsByCell.get(id) ?? []
    const primaryNode = tileNodes[0] ?? null
    const currentCellObject = cellObjectType(cell)
    const objectType = primaryNode ? nodeTypeToObjectType[primaryNode.type] : currentCellObject
    const hasSelectedNode = primaryNode?.id === selectedNodeId

    const titleParts = [
      `坐标 ${row},${col}`,
      objectType ? `对象: ${objectLabels[objectType]}` : '对象: 空白',
      cell ? `介质: ${objectLabels[currentCellObject ?? 'road']}` : '介质: 未铺设',
      cell?.type === 'conveyor' && cell.conveyorDirection ? `方向: ${cell.conveyorDirection}` : '',
      primaryNode ? `节点: ${primaryNode.label}` : '',
      ...tileRobots.map((robot) =>
        [
          `设备 ${robot.id}`,
          `类型 ${robot.type === 'ctu' ? 'CTU' : '四向车'}`,
          robot.status ? `状态 ${robot.status}` : '',
          robot.phaseLabel ? `阶段 ${robot.phaseLabel}` : '',
          robot.currentTaskId ? `任务 ${robot.currentTaskId}` : '',
        ]
          .filter(Boolean)
          .join(' / '),
      ),
    ].filter(Boolean)

    return (
      <button
        key={id}
        type="button"
        title={titleParts.join('\n')}
        aria-label={titleParts.join('，')}
        className={`grid-scene-cell grid-scene-cell-${cell?.type ?? 'default'} ${selectedCellId === id ? 'grid-scene-cell-selected' : ''} ${hasSelectedNode ? 'grid-scene-cell-node-selected' : ''} ${interactiveMode === 'readonly' ? 'grid-scene-cell-readonly' : ''}`}
        style={{ width: visualSize, height: visualSize }}
        onMouseDown={() => {
          if (!canPanFromPointer) onCellMouseDown?.(row, col)
        }}
        onMouseEnter={() => {
          if (!canPanFromPointer) onCellMouseEnter?.(row, col)
        }}
        onClick={() => {
          if (!canPanFromPointer) onCellClick?.(row, col)
        }}
      >
        {objectType ? (
          <div className={`grid-scene-object-layer ${primaryNode ? 'grid-scene-object-layer-node' : 'grid-scene-object-layer-medium'}`}>
            <TopologyGlyph
              kind={objectType}
              size="board"
              direction={(primaryNode ? undefined : cell?.conveyorDirection) as ConveyorDirection | undefined}
              className={hasSelectedNode ? 'topology-glyph-emphasis' : ''}
            />
          </div>
        ) : null}

        {tileRobots.length ? (
          <div className="grid-scene-runtime-robot-layer">
            {tileRobots.slice(0, 2).map((robot) => (
              <span
                key={robot.id}
                className={`grid-scene-runtime-robot ${robot.failed ? 'grid-scene-runtime-robot-failed' : ''}`}
              >
                <TopologyGlyph kind={robotObjectType(robot.type)} size="board" className={robot.failed ? 'topology-glyph-runtime-failed' : ''} />
              </span>
            ))}
          </div>
        ) : null}

        <span className="grid-scene-coord">
          {row},{col}
        </span>
      </button>
    )
  })

  return (
    <div className={`grid-scene-shell ${viewportSizing === 'content-aware' ? 'grid-scene-shell-content-aware' : ''}`}>
      <div className="grid-scene-controls">
        <button className="ghost-button grid-scene-control-button" onClick={() => zoomOut()} disabled={!enableZoom} type="button" title="缩小">
          <Minus size={14} />
        </button>
        <button className="ghost-button grid-scene-control-button" onClick={() => zoomIn()} disabled={!enableZoom} type="button" title="放大">
          <Plus size={14} />
        </button>
        <button className="ghost-button grid-scene-control-button" onClick={fitToView} type="button" title="适配全图">
          <Scan size={14} />
          适配
        </button>
        <button className="ghost-button grid-scene-control-button" onClick={resetToActual} type="button" title="恢复 100%">
          <Scaling size={14} />
          100%
        </button>
        {controlsAddon}
        <span className="grid-scene-zoom-indicator">{Math.round(view.zoom * 100)}%</span>
      </div>

      <div
        ref={viewportRef}
        className={`grid-scene-viewport ${viewportSizing === 'content-aware' ? 'grid-scene-viewport-content-aware' : 'grid-scene-viewport-screen-fill'} ${canPanFromPointer ? 'grid-scene-viewport-pannable' : ''} ${isDragging ? 'grid-scene-viewport-dragging' : ''}`}
        style={viewportStyle}
        onPointerDown={canPanFromPointer ? beginPan : undefined}
        onPointerMove={canPanFromPointer ? handlePointerMove : undefined}
        onPointerUp={canPanFromPointer ? handlePointerUp : undefined}
        onPointerCancel={canPanFromPointer ? handlePointerUp : undefined}
      >
        {isEmptyTopology && !hideEmptyState ? (
          <div className="grid-scene-empty-state">
            <strong>当前拓扑还是空白画布</strong>
            <p>先在左侧放置通行路、输送带或功能节点，画布会随内容自动贴合。</p>
          </div>
        ) : null}
        <div className="grid-scene-transform" style={transformStyle}>
          <div
            className="grid-scene-board"
            style={{
              width: boardWidth,
              height: boardHeight,
            }}
          >
            <div
              className="grid-scene-grid"
              style={{
                gridTemplateColumns: `repeat(${cols}, ${visualSize}px)`,
                gridAutoRows: `${visualSize}px`,
              }}
            >
              {tiles}
            </div>
            <div className="grid-scene-zones">{zoneOverlays}</div>
            {selectionBox ? (
              <div
                className="grid-scene-selection-box"
                style={{
                  left: selectionBox.col0 * visualSize,
                  top: selectionBox.row0 * visualSize,
                  width: (selectionBox.col1 - selectionBox.col0 + 1) * visualSize,
                  height: (selectionBox.row1 - selectionBox.row0 + 1) * visualSize,
                }}
              />
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
