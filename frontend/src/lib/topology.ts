import type {
  CellType,
  ConveyorDeckConfig,
  ConveyorDeckKey,
  ConveyorDecks,
  ConveyorDirection,
  ConveyorRunMode,
  ConveyorSide,
  EditorObjectType,
  NodeType,
  TopologyCell,
  TopologyNode,
  TopologyPayload,
  Zone,
  ZoneType,
} from '../types'

/** 地面层标签；v1 中 1F 的 cell id 不含楼层前缀。 */
export const GROUND_FLOOR = '1F'

export const OBJECT_LABELS: Record<EditorObjectType, string> = {
  select: '选择',
  batch: '批量选择',
  erase: '删除',
  road: '通行路',
  blocked: '障碍物',
  conveyor: '输送带',
  ground_conveyor: '地面输送线',
  ctu_robot: 'CTU',
  shuttle_robot: '四向车',
  lift_transfer: '升降移载机',
  turn_transfer: '转向移载机',
  shuttle_dock: '四向车出货位',
}

export const CELL_LABELS: Record<CellType, string> = {
  road: '通行路',
  blocked: '障碍物',
  conveyor: '输送带',
}

export const NODE_LABELS: Record<NodeType, string> = {
  storage: '库位',
  ctu_robot: 'CTU',
  shuttle_robot: '四向车',
  lift_transfer: '升降移载机',
  turn_transfer: '转向移载机',
  shuttle_dock: '四向车出货位',
  picking_handoff: '机械臂交接处',
}

export const ZONE_LABELS: Record<ZoneType, string> = {
  ctu: 'CTU 高架库',
  shuttle: '四向车平库',
  port: '输送线出入口',
  pick_station: '人工拣货位',
  drop_zone: '放置区',
}

export const DIRECTION_LABELS: Record<ConveyorDirection, string> = {
  right: '向右',
  left: '向左',
  up: '向上',
  down: '向下',
}

export const CONVEYOR_SIDE_LABELS: Record<ConveyorSide, string> = {
  north: '北侧',
  east: '东侧',
  south: '南侧',
  west: '西侧',
}

export const CONVEYOR_DECK_LABELS: Record<ConveyorDeckKey, string> = {
  lower: '下层',
  upper: '上层',
}

const DIRECTION_TO_SIDE: Record<ConveyorDirection, ConveyorSide> = {
  right: 'east',
  left: 'west',
  up: 'north',
  down: 'south',
}

const SIDE_TO_DIRECTION: Record<ConveyorSide, ConveyorDirection> = {
  north: 'up',
  east: 'right',
  south: 'down',
  west: 'left',
}

const SIDE_STEP: Record<ConveyorSide, readonly [number, number]> = {
  north: [-2, 0],
  east: [0, 2],
  south: [2, 0],
  west: [0, -2],
}

export function oppositeConveyorSide(side: ConveyorSide): ConveyorSide {
  return side === 'north' ? 'south' : side === 'south' ? 'north' : side === 'east' ? 'west' : 'east'
}

export function conveyorSideFromDirection(direction: ConveyorDirection): ConveyorSide {
  return DIRECTION_TO_SIDE[direction]
}

export function conveyorDirectionFromSide(side: ConveyorSide): ConveyorDirection {
  return SIDE_TO_DIRECTION[side]
}

export const NODE_PREFIX: Record<NodeType, string> = {
  storage: 'storage',
  ctu_robot: 'ctu',
  shuttle_robot: 'shuttle',
  lift_transfer: 'lift_transfer',
  turn_transfer: 'turn_transfer',
  shuttle_dock: 'dock',
  picking_handoff: 'handoff',
}

/** 只读展示型（节点需要“贴地”，通常在通行路格上承载）。 */
export const BUSINESS_NODE_TYPES: ReadonlySet<NodeType> = new Set(['storage'])

export const DEVICE_NODE_TYPES: ReadonlySet<NodeType> = new Set(['ctu_robot', 'shuttle_robot'])

/** 可落在输送带格上的节点（升降移载机 / 转向移载机）。 */
export const CONVEYOR_MOUNTED_NODE_TYPES: ReadonlySet<NodeType> = new Set(['lift_transfer', 'turn_transfer', 'shuttle_dock'])

export const NODE_TOOL: Partial<Record<NodeType, EditorObjectType>> = {
  ctu_robot: 'ctu_robot',
  shuttle_robot: 'shuttle_robot',
  lift_transfer: 'lift_transfer',
  turn_transfer: 'turn_transfer',
}

export function isNodeTool(tool: EditorObjectType): boolean {
  return (NODE_TOOL as Record<string, EditorObjectType>)[tool] === tool && (NODE_PREFIX as Record<string, NodeType>)[tool] !== undefined
}

export const ZONE_TYPE_ORDER: ZoneType[] = ['ctu', 'shuttle', 'port', 'pick_station', 'drop_zone']

/** 画刷工具（铺格）；ground_conveyor 是伪工具：铺 conveyor 格并带地面标记。 */
export const CELL_BRUSH_TOOLS: ReadonlySet<EditorObjectType> = new Set([
  'road',
  'blocked',
  'conveyor',
  'ground_conveyor',
])

/** 「批量选择」工具在「批量放置」动作下可选放置的对象类型（铺格 + 节点）。 */
export const BATCH_PLACE_TYPES: EditorObjectType[] = [
  'road',
  'blocked',
  'conveyor',
  'ground_conveyor',
  'ctu_robot',
  'shuttle_robot',
  'lift_transfer',
  'turn_transfer',
]

export interface GridPoint {
  row: number
  col: number
}

/** 归一化框（含端点，row 向下 / col 向右）。 */
export interface CellBox {
  row0: number
  col0: number
  row1: number
  col1: number
}

export function normalizeSelectionBox(from: GridPoint, to: GridPoint): CellBox {
  return {
    row0: Math.min(from.row, to.row),
    col0: Math.min(from.col, to.col),
    row1: Math.max(from.row, to.row),
    col1: Math.max(from.col, to.col),
  }
}

export function boxToRect(box: CellBox) {
  return {
    left: box.col0,
    top: box.row0,
    width: box.col1 - box.col0 + 1,
    height: box.row1 - box.row0 + 1,
  }
}

export function isPointInBox(box: CellBox, row: number, col: number): boolean {
  return row >= box.row0 && row <= box.row1 && col >= box.col0 && col <= box.col1
}

/** 按行优先枚举矩形框内所有格点，供「批量放置」铺格用。 */
export function cellsInBox(box: CellBox): GridPoint[] {
  const points: GridPoint[] = []
  for (let row = box.row0; row <= box.row1; row += 1) {
    for (let col = box.col0; col <= box.col1; col += 1) {
      points.push({ row, col })
    }
  }
  return points
}

export function boxesEqual(a: CellBox | null, b: CellBox | null): boolean {
  if (a === b) return true
  if (!a || !b) return false
  return a.row0 === b.row0 && a.col0 === b.col0 && a.row1 === b.row1 && a.col1 === b.col1
}

export function sameFloorValue(value: string | null | undefined, floor = GROUND_FLOOR): string {
  return value || floor
}

export function floorOf(item: { floor?: string } | undefined | null): string {
  return item?.floor || GROUND_FLOOR
}

/** 与 GridBoard 保持一致：1F 无楼层前缀，其它层带 `cell-{floor}-r{row}-c{col}`。 */
export function buildCellId(row: number, col: number, floor = GROUND_FLOOR): string {
  if (floor === GROUND_FLOOR || !floor) return `cell-r${row}-c${col}`
  return `cell-${floor}-r${row}-c${col}`
}

export function parseCellId(id: string): { floor: string; row: number; col: number } | null {
  const single = /^cell-r(\d+)-c(\d+)$/.exec(id)
  if (single) return { floor: GROUND_FLOOR, row: Number(single[1]), col: Number(single[2]) }
  const multi = /^cell-(.+)-r(\d+)-c(\d+)$/.exec(id)
  if (multi) return { floor: multi[1], row: Number(multi[2]), col: Number(multi[3]) }
  return null
}

export function walkableForType(type: CellType): boolean {
  return type !== 'blocked'
}

export function defaultCostForType(type: CellType): number {
  return type === 'conveyor' ? 0.6 : 1
}

export function makeCell(row: number, col: number, type: CellType, floor = GROUND_FLOOR): TopologyCell {
  const cell: TopologyCell = {
    id: buildCellId(row, col, floor),
    floor,
    row,
    col,
    type,
    zoneId: null,
    cost: defaultCostForType(type),
    walkable: walkableForType(type),
    conveyorDirection: type === 'conveyor' ? 'right' : null,
  }
  if (type === 'conveyor') cell.levels = 1
  return cell
}

export function findCell(
  topology: Pick<TopologyPayload, 'cells'>,
  row: number,
  col: number,
  floor = GROUND_FLOOR,
): TopologyCell | undefined {
  return topology.cells.find((cell) => cell.row === row && cell.col === col && floorOf(cell) === floor)
}

export function findNodeAt(
  topology: Pick<TopologyPayload, 'nodes' | 'zones'>,
  row: number,
  col: number,
  floor = GROUND_FLOOR,
): TopologyNode | undefined {
  return topology.nodes.find((node) => {
    if (floorOf(node) !== floor) return false
    const size = nodeFootprint(topology, node)
    return row >= node.row && row < node.row + size && col >= node.col && col < node.col + size
  })
}

export function findZoneById(topology: Pick<TopologyPayload, 'zones'>, zoneId: string): Zone | undefined {
  return topology.zones.find((zone) => zone.id === zoneId)
}

/** 节点占地边长：处理托盘的节点 2×2，处理料箱的节点 1×1。 */
export function nodeFootprint(
  topology: Pick<TopologyPayload, 'zones'>,
  node: Pick<TopologyNode, 'type' | 'zoneId'>,
): number {
  const zoneType = findZoneById(topology, node.zoneId ?? '')?.type
  // 输送带上的设备（升降移载机 / 转向移载机 / 四向车出货位）固定 2×2，与输送带块同口径
  if (node.type === 'lift_transfer' || node.type === 'turn_transfer' || node.type === 'shuttle_dock') {
    return 2
  }
  // 库位 / 四向车：四向车库位(托盘) 2×2，CTU 料箱位 1×1
  if (node.type === 'storage' || node.type === 'shuttle_robot') {
    return zoneType === 'shuttle' ? 2 : 1
  }
  return 1
}

/** 输送带 2×2 占位：一个块 = 2×2 四格，锚点向下取偶（与四向车库位 nodeFootprint 同口径）。 */
export const CONVEYOR_FOOTPRINT = 2

/** 输送带块锚点（row/col 向下取偶）——旧数据的回退口径：无显式 anchor 时按此推导。 */
export function conveyorBlockAnchor(row: number, col: number): GridPoint {
  return { row: row - (row % 2), col: col - (col % 2) }
}

/** 某输送带格所属块的锚点：优先用格子上存的 anchor，旧数据回退到向下取偶。 */
export function conveyorAnchorOf(cell: Pick<TopologyCell, 'row' | 'col' | 'conveyorAnchor'>): GridPoint {
  return cell.conveyorAnchor ?? conveyorBlockAnchor(cell.row, cell.col)
}

/** 锚点处的 2×2 块 4 格坐标（左上起，row 向下 / col 向右）。 */
export function conveyorBlockCellsAt(anchor: GridPoint): GridPoint[] {
  return [
    { row: anchor.row, col: anchor.col },
    { row: anchor.row, col: anchor.col + 1 },
    { row: anchor.row + 1, col: anchor.col },
    { row: anchor.row + 1, col: anchor.col + 1 },
  ]
}

/** 某输送带格所属块内所有现存格子的 id（用于编辑时整块联动）。 */
export function conveyorBlockCellIds(
  topology: Pick<TopologyPayload, 'cells'>,
  cellId: string,
): string[] {
  const cell = topology.cells.find((item) => item.id === cellId)
  if (!cell || cell.type !== 'conveyor') return [cellId]
  const floor = floorOf(cell)
  const anchor = conveyorAnchorOf(cell)
  const ids = conveyorBlockCellsAt(anchor).map((point) => buildCellId(point.row, point.col, floor))
  return ids.filter((id) => topology.cells.some((item) => item.id === id))
}

export interface ConveyorBlock {
  key: string
  floor: string
  row: number
  col: number
  anchorCell: TopologyCell
  cellIds: string[]
  lineId: string | null
}

export interface ConveyorValidationIssue {
  severity: 'error' | 'warning'
  blockKey: string
  deck: ConveyorDeckKey
  message: string
}

function defaultDeck(inputSide: ConveyorSide, outputSide: ConveyorSide, role: ConveyorDeckConfig['role']): ConveyorDeckConfig {
  return { enabled: true, inputSide, outputSide, role, speed: 1, capacity: 1 }
}

/** 旧 conveyorDirection 自动映射为下层去程；双层旧线的上层默认反向回程。 */
export function legacyConveyorDecks(cell: TopologyCell): ConveyorDecks {
  const outputSide = conveyorSideFromDirection(cell.conveyorDirection ?? 'right')
  const inputSide = oppositeConveyorSide(outputSide)
  const lower = defaultDeck(inputSide, outputSide, 'outbound')
  if (cell.conveyorGround || (cell.levels ?? 1) < 2) return { lower }
  return {
    lower,
    upper: defaultDeck(outputSide, inputSide, 'return'),
  }
}

export function conveyorAnchorCell(
  topology: Pick<TopologyPayload, 'cells'>,
  cell: TopologyCell,
): TopologyCell {
  const anchor = conveyorAnchorOf(cell)
  const floor = floorOf(cell)
  return topology.cells.find(
    (item) => item.type === 'conveyor' && floorOf(item) === floor && item.row === anchor.row && item.col === anchor.col,
  ) ?? cell
}

export function conveyorDecksOf(
  topology: Pick<TopologyPayload, 'cells'>,
  cell: TopologyCell,
): ConveyorDecks {
  const anchorCell = conveyorAnchorCell(topology, cell)
  return anchorCell.conveyorDecks ?? legacyConveyorDecks(anchorCell)
}

export function conveyorModeOf(
  topology: Pick<TopologyPayload, 'cells'>,
  cell: TopologyCell,
): ConveyorRunMode {
  const anchorCell = conveyorAnchorCell(topology, cell)
  if (anchorCell.conveyorMode) return anchorCell.conveyorMode
  if (anchorCell.conveyorGround || (anchorCell.levels ?? 1) < 2) return 'single'
  return 'round_trip'
}

export function conveyorBlocks(
  topology: Pick<TopologyPayload, 'cells'>,
  ids?: Iterable<string>,
): ConveyorBlock[] {
  const allowed = ids ? new Set(ids) : null
  const byKey = new Map<string, ConveyorBlock>()
  for (const cell of topology.cells) {
    if (cell.type !== 'conveyor' || (allowed && !allowed.has(cell.id))) continue
    const anchor = conveyorAnchorOf(cell)
    const floor = floorOf(cell)
    const key = `${floor}:${anchor.row}:${anchor.col}`
    const current = byKey.get(key)
    if (current) {
      current.cellIds.push(cell.id)
      if (cell.row === anchor.row && cell.col === anchor.col) {
        current.anchorCell = cell
        current.lineId = cell.conveyorLineId ?? null
      }
      continue
    }
    byKey.set(key, {
      key,
      floor,
      row: anchor.row,
      col: anchor.col,
      anchorCell: cell,
      cellIds: [cell.id],
      lineId: cell.conveyorLineId ?? null,
    })
  }
  return [...byKey.values()].sort((left, right) =>
    left.floor.localeCompare(right.floor) || left.row - right.row || left.col - right.col,
  )
}

function neighboringBlock(blocks: ConveyorBlock[], block: ConveyorBlock, side: ConveyorSide): ConveyorBlock | undefined {
  const [dr, dc] = SIDE_STEP[side]
  return blocks.find(
    (candidate) => candidate.floor === block.floor
      && candidate.row === block.row + dr
      && candidate.col === block.col + dc
      && (!block.lineId && !candidate.lineId || block.lineId === candidate.lineId),
  )
}

function sideBetweenBlocks(from: ConveyorBlock, to: ConveyorBlock): ConveyorSide | null {
  const dr = to.row - from.row
  const dc = to.col - from.col
  if (dr === -2 && dc === 0) return 'north'
  if (dr === 2 && dc === 0) return 'south'
  if (dr === 0 && dc === 2) return 'east'
  if (dr === 0 && dc === -2) return 'west'
  return null
}

/** 按块级邻接排序，避免 2×2 块内部四个格子被误当成四段输送线。 */
export function orderConveyorBlocks(blocks: ConveyorBlock[]): ConveyorBlock[] {
  if (blocks.length <= 1) return blocks.slice()
  const neighbors = (block: ConveyorBlock) => blocks.filter((candidate) =>
    candidate.key !== block.key && sideBetweenBlocks(block, candidate) !== null,
  )
  const head = blocks.find((block) => neighbors(block).length <= 1) ?? blocks[0]
  const ordered: ConveyorBlock[] = []
  const seen = new Set<string>()
  let current: ConveyorBlock | undefined = head
  while (current && !seen.has(current.key)) {
    seen.add(current.key)
    ordered.push(current)
    current = neighbors(current).find((candidate) => !seen.has(candidate.key))
  }
  for (const block of blocks) if (!seen.has(block.key)) ordered.push(block)
  return ordered
}

/** 自动为简单连通线生成逐块入口/出口；双层往返时上层严格反向。 */
export function configureConveyorRun(
  topology: TopologyPayload,
  ids: string[],
  mode: ConveyorRunMode,
): TopologyPayload {
  const next = cloneTopology(topology)
  const selected = new Set(ids)
  const blocks = orderConveyorBlocks(conveyorBlocks(next, selected))
  if (!blocks.length) return topology
  const byId = new Map(next.cells.map((cell) => [cell.id, cell]))
  const first = blocks[0]
  const lineId = first.lineId ?? `cv-${first.floor}-${first.row}-${first.col}`

  blocks.forEach((block, index) => {
    const previous = blocks[index - 1]
    const following = blocks[index + 1]
    const existing = conveyorDecksOf(next, block.anchorCell)
    const output = following
      ? sideBetweenBlocks(block, following)!
      : previous
        ? sideBetweenBlocks(previous, block)!
        : existing.lower.outputSide
    const input = previous
      ? oppositeConveyorSide(sideBetweenBlocks(previous, block)!)
      : oppositeConveyorSide(output)
    const lower = mode === 'independent'
      ? existing.lower
      : defaultDeck(input, output, 'outbound')
    const upper = mode === 'single'
      ? undefined
      : mode === 'independent'
        ? (existing.upper ?? defaultDeck(output, input, 'return'))
        : defaultDeck(output, input, 'return')
    const direction = conveyorDirectionFromSide(lower.outputSide)
    for (const id of block.cellIds) {
      const cell = byId.get(id)
      if (!cell) continue
      cell.levels = mode === 'single' ? 1 : 2
      cell.conveyorGround = mode === 'single' ? cell.conveyorGround : false
      cell.conveyorDirection = direction
      cell.conveyorLineId = lineId
      if (cell.id === block.anchorCell.id) {
        cell.conveyorMode = mode
        cell.conveyorDecks = { lower, ...(upper ? { upper } : {}) }
      } else {
        delete cell.conveyorMode
        delete cell.conveyorDecks
      }
    }
  })
  return next
}

export function updateConveyorBlockDeck(
  topology: TopologyPayload,
  cellId: string,
  deck: ConveyorDeckKey,
  patch: Partial<ConveyorDeckConfig>,
): TopologyPayload {
  const source = topology.cells.find((cell) => cell.id === cellId && cell.type === 'conveyor')
  if (!source) return topology
  const next = cloneTopology(topology)
  const nextSource = next.cells.find((cell) => cell.id === cellId)!
  const anchorCell = conveyorAnchorCell(next, nextSource)
  const decks = conveyorDecksOf(next, anchorCell)
  const base = deck === 'lower'
    ? decks.lower
    : decks.upper ?? defaultDeck('west', 'east', 'return')
  const updated = { ...base, ...patch, enabled: true }
  anchorCell.conveyorDecks = deck === 'lower'
    ? { ...decks, lower: updated }
    : { ...decks, upper: updated }
  anchorCell.conveyorMode = 'independent'
  anchorCell.levels = anchorCell.conveyorDecks.upper?.enabled ? 2 : 1
  if (deck === 'lower') {
    const direction = conveyorDirectionFromSide(updated.outputSide)
    for (const id of conveyorBlockCellIds(next, anchorCell.id)) {
      const cell = next.cells.find((item) => item.id === id)
      if (cell) cell.conveyorDirection = direction
    }
  }
  return next
}

export function reverseConveyorDeck(
  topology: TopologyPayload,
  ids: string[],
  deck: ConveyorDeckKey,
): TopologyPayload {
  let next = topology
  for (const block of conveyorBlocks(topology, ids)) {
    const config = conveyorDecksOf(next, block.anchorCell)[deck]
    if (!config) continue
    next = updateConveyorBlockDeck(next, block.anchorCell.id, deck, {
      inputSide: config.outputSide,
      outputSide: config.inputSide,
    })
  }
  return next
}

/** 交换双层线的物理用途和走向；旧下层配置移到上层，旧上层配置移到下层。 */
export function swapConveyorDecks(
  topology: TopologyPayload,
  ids: string[],
): TopologyPayload {
  const next = cloneTopology(topology)
  for (const block of conveyorBlocks(next, ids)) {
    const anchor = conveyorAnchorCell(next, block.anchorCell)
    const decks = conveyorDecksOf(next, anchor)
    if (!decks.upper?.enabled) continue
    anchor.conveyorDecks = {
      lower: { ...decks.upper },
      upper: { ...decks.lower },
    }
    anchor.conveyorMode = 'independent'
    anchor.levels = 2
    const direction = conveyorDirectionFromSide(anchor.conveyorDecks.lower.outputSide)
    for (const id of conveyorBlockCellIds(next, anchor.id)) {
      const cell = next.cells.find((item) => item.id === id)
      if (cell) cell.conveyorDirection = direction
    }
  }
  return next
}

/** 从指定块沿某层的出口逐块前进，供 3D 动画和仿真使用。 */
export function conveyorDeckRoute(
  topology: Pick<TopologyPayload, 'cells'>,
  startCell: TopologyCell,
  deck: ConveyorDeckKey,
): ConveyorBlock[] {
  const blocks = conveyorBlocks(topology)
  const anchor = conveyorAnchorOf(startCell)
  let current = blocks.find(
    (block) => block.floor === floorOf(startCell) && block.row === anchor.row && block.col === anchor.col,
  )
  const route: ConveyorBlock[] = []
  const seen = new Set<string>()
  while (current && !seen.has(current.key)) {
    const config = conveyorDecksOf(topology, current.anchorCell)[deck]
    if (!config?.enabled) break
    route.push(current)
    seen.add(current.key)
    current = neighboringBlock(blocks, current, config.outputSide)
  }
  return route
}

export function validateConveyorTopology(topology: Pick<TopologyPayload, 'cells' | 'nodes' | 'zones'>): ConveyorValidationIssue[] {
  const blocks = conveyorBlocks(topology)
  const issues: ConveyorValidationIssue[] = []
  for (const block of blocks) {
    const severity: ConveyorValidationIssue['severity'] = block.anchorCell.conveyorDecks ? 'error' : 'warning'
    const decks = conveyorDecksOf(topology, block.anchorCell)
    const configs: [ConveyorDeckKey, ConveyorDeckConfig | undefined][] = [
      ['lower', decks.lower],
      ['upper', decks.upper],
    ]
    for (const [deck, config] of configs) {
      if (!config?.enabled) continue
      if (block.anchorCell.conveyorGround && deck === 'upper') {
        issues.push({ severity, blockKey: block.key, deck, message: '地面输送线只能使用下层。' })
      }
      if (config.inputSide === config.outputSide) {
        issues.push({ severity, blockKey: block.key, deck, message: '入口侧和出口侧不能相同。' })
        continue
      }
      const previous = neighboringBlock(blocks, block, config.inputSide)
      if (previous?.anchorCell.conveyorDecks) {
        const previousConfig = conveyorDecksOf(topology, previous.anchorCell)[deck]
        const expectedOutput = oppositeConveyorSide(config.inputSide)
        if (!previousConfig?.enabled || previousConfig.outputSide !== expectedOutput) {
          issues.push({
            severity,
            blockKey: block.key,
            deck,
            message: `${CONVEYOR_SIDE_LABELS[config.inputSide]}入口与上一块${CONVEYOR_SIDE_LABELS[expectedOutput]}出口不匹配。`,
          })
        }
      }
      const next = neighboringBlock(blocks, block, config.outputSide)
      if (next?.anchorCell.conveyorDecks) {
        const nextConfig = conveyorDecksOf(topology, next.anchorCell)[deck]
        const expectedInput = oppositeConveyorSide(config.outputSide)
        if (!nextConfig?.enabled || nextConfig.inputSide !== expectedInput) {
          issues.push({
            severity,
            blockKey: block.key,
            deck,
            message: `${CONVEYOR_SIDE_LABELS[config.outputSide]}出口与下一块${CONVEYOR_SIDE_LABELS[expectedInput]}入口不匹配。`,
          })
        }
      }
    }
  }
  for (const node of topology.nodes) {
    if (node.type !== 'turn_transfer') continue
    const floor = node.floor ?? GROUND_FLOOR
    const block = blocks.find((item) =>
      item.floor === floor && item.row === node.row && item.col === node.col,
    )
    const blockKey = block?.key ?? `${floor}:${node.row}:${node.col}`
    const deck: ConveyorDeckKey = node.transferDeck
      ?? (block && !block.anchorCell.conveyorGround && (block.anchorCell.levels ?? 1) > 1 ? 'upper' : 'lower')
    if (!block) {
      issues.push({
        severity: 'error',
        blockKey,
        deck,
        message: `${node.label}必须安装在输送模块上。`,
      })
      continue
    }
    if (deck === 'upper' && (block.anchorCell.conveyorGround || (block.anchorCell.levels ?? 1) < 2)) {
      issues.push({
        severity: 'error',
        blockKey,
        deck,
        message: `${node.label}选择了上层，但当前输送模块没有可用上层。`,
      })
    }
    if (node.transferInputSide && node.transferInputSide === node.transferOutputSide) {
      issues.push({
        severity: 'error',
        blockKey,
        deck,
        message: `${node.label}的入口方向和出口方向不能相同。`,
      })
    }
  }
  for (const zone of topology.zones) {
    const binding = zone.conveyorBinding
    if (!binding || (zone.type !== 'port' && zone.type !== 'pick_station')) continue
    const block = blocks.find((item) => item.key === binding.blockId)
    if (!block) {
      issues.push({
        severity: 'error',
        blockKey: binding.blockId,
        deck: 'lower',
        message: `${zone.label}绑定的输送块不存在。`,
      })
      continue
    }
    const decks = conveyorDecksOf(topology, block.anchorCell)
    if (zone.type === 'port') {
      if (decks.lower.inputSide !== binding.side) {
        issues.push({
          severity: 'error', blockKey: block.key, deck: 'lower',
          message: `${zone.label}连接侧与下层入口侧不一致。`,
        })
      }
      if (decks.upper?.enabled && decks.upper.outputSide !== binding.side) {
        issues.push({
          severity: 'error', blockKey: block.key, deck: 'upper',
          message: `${zone.label}连接侧与上层回程出口侧不一致。`,
        })
      }
    } else {
      if (decks.lower.outputSide !== binding.side) {
        issues.push({
          severity: 'error', blockKey: block.key, deck: 'lower',
          message: `${zone.label}连接侧与下层去程出口侧不一致。`,
        })
      }
      if (decks.upper?.enabled && decks.upper.inputSide !== binding.side) {
        issues.push({
          severity: 'error', blockKey: block.key, deck: 'upper',
          message: `${zone.label}连接侧与上层回程入口侧不一致。`,
        })
      }
    }
  }
  return issues
}

/** 库位槽位编号（派生，不入库）：`{区域字母}-{槽行}-{槽列}`。shuttle 按 2×2 槽计，ctu 按 1×1 计。 */
export function slotNumberFor(
  topology: Pick<TopologyPayload, 'zones'>,
  node: Pick<TopologyNode, 'type' | 'zoneId' | 'row' | 'col'>,
): string | null {
  if (node.type !== 'storage' || !node.zoneId) return null
  const zone = findZoneById(topology, node.zoneId)
  if (!zone) return null
  const size = nodeFootprint(topology, node)
  const bayRow = Math.floor((node.row - zone.y) / size)
  const bayCol = Math.floor((node.col - zone.x) / size)
  return `${storageZoneLetter(topology, zone)}-${String(bayRow + 1).padStart(2, '0')}-${String(bayCol + 1).padStart(2, '0')}`
}

function storageZoneLetter(topology: Pick<TopologyPayload, 'zones'>, zone: Zone): string {
  const storageZones = topology.zones
    .filter((item) => item.type === 'ctu' || item.type === 'shuttle')
    .slice()
    .sort((a, b) => a.y - b.y || a.x - b.x || a.id.localeCompare(b.id))
  const index = storageZones.findIndex((item) => item.id === zone.id)
  return index >= 0 ? String.fromCharCode(65 + (index % 26)) : '?'
}

export function zoneAtRectPoint(topology: Pick<TopologyPayload, 'zones'>, row: number, col: number): Zone | undefined {
  const candidates = topology.zones.filter(
    (zone) => row >= zone.y && row < zone.y + zone.height && col >= zone.x && col < zone.x + zone.width,
  )
  if (candidates.length === 0) return undefined
  candidates.sort((a, b) => a.width * a.height - b.width * b.height)
  return candidates[0]
}

const NODE_ZONE_AFFINITY: Record<NodeType, ReadonlySet<ZoneType>> = {
  storage: new Set(['ctu', 'shuttle']),
  ctu_robot: new Set(['ctu']),
  shuttle_robot: new Set(['shuttle']),
  lift_transfer: new Set<ZoneType>(),
  turn_transfer: new Set<ZoneType>(),
  shuttle_dock: new Set<ZoneType>(),
  picking_handoff: new Set<ZoneType>(),
}

export function zoneRefForNodeType(
  topology: Pick<TopologyPayload, 'zones'>,
  nodeType: NodeType,
  row: number,
  col: number,
): string | null {
  const containing = topology.zones.filter(
    (zone) => row >= zone.y && row < zone.y + zone.height && col >= zone.x && col < zone.x + zone.width,
  )
  if (containing.length === 0) return null
  const affinity = NODE_ZONE_AFFINITY[nodeType]
  const matched = containing
    .filter((zone) => affinity.has(zone.type))
    .sort((a, b) => a.width * a.height - b.width * b.height)
  return matched[0]?.id ?? null
}

export function buildNextNodeId(nodes: TopologyNode[], type: NodeType): string {
  const prefix = NODE_PREFIX[type]
  const existing = new Set(nodes.map((node) => node.id))
  let index = 1
  for (;;) {
    const candidate = `${prefix}-${String(index).padStart(2, '0')}`
    if (!existing.has(candidate)) return candidate
    index += 1
  }
}

export function defaultNodeLabel(type: NodeType, id: string): string {
  const friendly = NODE_LABELS[type] || id
  const suffix = id.split('-').slice(-1)[0]
  if (BUSINESS_NODE_TYPES.has(type)) return `${friendly} ${suffix}`
  return `${friendly}-${suffix}`
}

export function makeNode(type: NodeType, row: number, col: number, floor = GROUND_FLOOR): TopologyNode {
  const id = buildNextNodeId([], type)
  return {
    id,
    type,
    floor,
    row,
    col,
    zoneId: null,
    capacity: 1,
    label: defaultNodeLabel(type, id),
    ...(type === 'turn_transfer' ? {
      transferDeck: 'lower' as ConveyorDeckKey,
      transferInputSide: 'west' as ConveyorSide,
      transferOutputSide: 'north' as ConveyorSide,
    } : {}),
  }
}

/** 从鼠标拖拽的连续格序列中回填输送带方向：每个格子取“上一格 → 本格”的走向。 */
export function directionFromCells(from: GridPoint, to: GridPoint): ConveyorDirection | null {
  const dr = to.row - from.row
  const dc = to.col - from.col
  if (dc === 1 && dr === 0) return 'right'
  if (dc === -1 && dr === 0) return 'left'
  if (dr === 1 && dc === 0) return 'down'
  if (dr === -1 && dc === 0) return 'up'
  return null
}

/** 采样 from→to 的连续路径（x=col, y=row，超采样取整，去重）。 */
export function interpolateGridCells(from: GridPoint, to: GridPoint): GridPoint[] {
  if (!from || !to) return []
  const dRow = to.row - from.row
  const dCol = to.col - from.col
  const steps = Math.max(Math.abs(dRow), Math.abs(dCol))
  if (steps === 0) return [{ row: from.row, col: from.col }]
  const cells: GridPoint[] = []
  const seen = new Set<string>()
  for (let step = 0; step <= steps; step += 1) {
    const cell = {
      row: Math.round(from.row + (dRow * step) / steps),
      col: Math.round(from.col + (dCol * step) / steps),
    }
    const key = `${cell.row}:${cell.col}`
    if (seen.has(key)) continue
    seen.add(key)
    cells.push(cell)
  }
  return cells
}

/** 每层一个 cell 只能有一种介质；把重复 key 的格点过滤成有序路径。 */
export function uniqueOrderedCells(cells: GridPoint[]): GridPoint[] {
  const seen = new Set<string>()
  const result: GridPoint[] = []
  for (const cell of cells) {
    const key = `${cell.row}:${cell.col}`
    if (seen.has(key)) continue
    seen.add(key)
    result.push(cell)
  }
  return result
}

const CONVEYOR_DIRS: ReadonlyArray<readonly [number, number]> = [
  [-1, 0],
  [1, 0],
  [0, -1],
  [0, 1],
]

/** 从某输送带格出发，取同层 4 邻接的输送带连通段，并按路径顺序排序（端点起）。 */
export function conveyorRunFromCell(
  topology: Pick<TopologyPayload, 'cells'>,
  cellId: string,
  floor = GROUND_FLOOR,
): TopologyCell[] {
  const start = topology.cells.find(
    (cell) => cell.id === cellId && cell.type === 'conveyor' && floorOf(cell) === floor,
  )
  if (!start) return []

  const at = new Map<string, TopologyCell>()
  for (const cell of topology.cells) {
    if (
      cell.type === 'conveyor'
      && floorOf(cell) === floor
      && (!start.conveyorLineId || cell.conveyorLineId === start.conveyorLineId)
    ) at.set(`${cell.row}:${cell.col}`, cell)
  }
  const neighbor = (cell: TopologyCell, dr: number, dc: number) => at.get(`${cell.row + dr}:${cell.col + dc}`)

  // 1) 泛洪取连通分量
  const ids = new Set<string>([start.id])
  const stack: TopologyCell[] = [start]
  while (stack.length) {
    const cur = stack.pop()!
    for (const [dr, dc] of CONVEYOR_DIRS) {
      const nb = neighbor(cur, dr, dc)
      if (nb && !ids.has(nb.id)) {
        ids.add(nb.id)
        stack.push(nb)
      }
    }
  }
  const cells = topology.cells.filter((cell) => ids.has(cell.id))

  // 2) 从度数 ≤1 的端点起沿邻接走一遍得到有序路径
  const degree = (cell: TopologyCell) =>
    CONVEYOR_DIRS.reduce((n, [dr, dc]) => {
      const nb = neighbor(cell, dr, dc)
      return n + (nb && ids.has(nb.id) ? 1 : 0)
    }, 0)
  const head = cells.find((cell) => degree(cell) <= 1) ?? cells[0]
  const ordered: TopologyCell[] = []
  const seen = new Set<string>()
  let cur: TopologyCell | undefined = head
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id)
    ordered.push(cur)
    let next: TopologyCell | undefined
    for (const [dr, dc] of CONVEYOR_DIRS) {
      const nb = neighbor(cur, dr, dc)
      if (nb && ids.has(nb.id) && !seen.has(nb.id)) {
        next = nb
        break
      }
    }
    cur = next
  }
  return ordered
}

function cloneTopology(topology: TopologyPayload): TopologyPayload {
  return {
    ...topology,
    meta: {
      ...topology.meta,
      viewport: { ...topology.meta.viewport },
      tags: [...(topology.meta.tags ?? [])],
      floors: [...(topology.meta.floors ?? [GROUND_FLOOR])],
    },
    grid: { ...topology.grid },
    zones: topology.zones.map((zone) => ({
      ...zone,
      ...(zone.conveyorBinding ? { conveyorBinding: { ...zone.conveyorBinding } } : {}),
      ...(zone.slotAisles ? { slotAisles: { ...zone.slotAisles } } : {}),
      ...(zone.removedSlots ? { removedSlots: [...zone.removedSlots] } : {}),
      ...(zone.slotLifts ? { slotLifts: [...zone.slotLifts] } : {}),
    })),
    cells: topology.cells.map((cell) => ({
      ...cell,
      ...(cell.conveyorAnchor ? { conveyorAnchor: { ...cell.conveyorAnchor } } : {}),
    })),
    nodes: topology.nodes.map((node) => ({ ...node })),
  }
}

/** 保证 cell 处于某一 zone 矩形时带 zoneId（采用面积最小优先，避免重叠时误归）。 */
function referenceZoneForCell(topology: TopologyPayload, row: number, col: number): string | null {
  const zone = zoneAtRectPoint(topology, row, col)
  return zone?.id ?? null
}

/**
 * 重铺一个格子的介质。若目标格已被业务节点占据（输送带闸口除外），跳过以免破坏语义。
 */
export function paintCell(
  topology: TopologyPayload,
  type: CellType,
  row: number,
  col: number,
  floor = GROUND_FLOOR,
  directionFrom?: GridPoint | null,
  options?: { skipOccupied?: boolean; ground?: boolean },
): { topology: TopologyPayload; painted: boolean } {
  if (type === 'conveyor') {
    return paintConveyorBlock(topology, row, col, floor, directionFrom, options)
  }
  return paintSingleCell(topology, type, row, col, floor, null, null, options)
}

function paintSingleCell(
  topology: TopologyPayload,
  type: CellType,
  row: number,
  col: number,
  floor: string,
  direction: ConveyorDirection | null,
  anchor: GridPoint | null = null,
  options?: { skipOccupied?: boolean; ground?: boolean },
): { topology: TopologyPayload; painted: boolean } {
  if (row < 0 || row >= topology.grid.rows || col < 0 || col >= topology.grid.cols) return { topology, painted: false }
  const node = findNodeAt(topology, row, col, floor)
  const existing = findCell(topology, row, col, floor)
  const occupiedByNode = Boolean(node)

  // 介质变化若会破坏节点落位语义则拒绝。
  if (occupiedByNode && options?.skipOccupied !== false) {
    const allowSwitch = type === 'road'
    if (existing && existing.type !== type && !(allowSwitch && existing.type !== 'conveyor')) return { topology, painted: false }
    if (existing && existing.type === type) return { topology, painted: false }
    if (!existing && type !== 'road') return { topology, painted: false }
  }

  const next = cloneTopology(topology)
  const zoneId = referenceZoneForCell(next, row, col)

  if (existing) {
    const index = next.cells.findIndex((cell) => cell.id === existing.id)
    next.cells[index] = {
      id: existing.id,
      floor: existing.floor,
      row: existing.row,
      col: existing.col,
      type,
      zoneId: existing.zoneId ?? zoneId,
      cost: existing.cost,
      walkable: walkableForType(type),
      conveyorDirection: type === 'conveyor' ? (direction ?? existing.conveyorDirection ?? 'right') : null,
      conveyorAnchor: type === 'conveyor' ? (anchor ?? { row, col }) : null,
      // 重铺时显式写标记：普通输送带工具会把地面线刷回架空线
      ...(type === 'conveyor' ? { levels: existing.levels ?? 1, conveyorGround: Boolean(options?.ground) } : {}),
    }
  } else {
    const cell = makeCell(row, col, type, floor)
    cell.zoneId = zoneId
    if (type === 'conveyor') {
      cell.conveyorDirection = direction ?? 'right'
      cell.conveyorAnchor = anchor ?? { row, col }
      cell.conveyorGround = Boolean(options?.ground)
    }
    next.cells.push(cell)
  }
  return { topology: finalizeTopology(next), painted: true }
}

/** 铺一个 2×2 输送带块：锚点 = 所点格子（支持奇数起点），4 格同向；目标格已属于别的块则整块拒绝。 */
function paintConveyorBlock(
  topology: TopologyPayload,
  row: number,
  col: number,
  floor: string,
  _directionFrom: GridPoint | null | undefined,
  options?: { skipOccupied?: boolean; ground?: boolean },
): { topology: TopologyPayload; painted: boolean } {
  const anchor = { row, col }
  if (anchor.row < 0 || anchor.row + 1 >= topology.grid.rows || anchor.col < 0 || anchor.col + 1 >= topology.grid.cols) {
    return { topology, painted: false }
  }
  // 重叠保护：4 格中任一格已是属于「其它块」的输送带，则整块拒绝，避免半块/块重叠
  for (const point of conveyorBlockCellsAt(anchor)) {
    const existing = findCell(topology, point.row, point.col, floor)
    if (existing && existing.type === 'conveyor') {
      const a = conveyorAnchorOf(existing)
      if (a.row !== anchor.row || a.col !== anchor.col) return { topology, painted: false }
    }
  }
  // 方向不再根据绘制手势推断；每个 2×2 模块在属性面板中独立手动设置。
  const direction: ConveyorDirection = 'right'
  let next = topology
  let painted = false
  for (const point of conveyorBlockCellsAt(anchor)) {
    const result = paintSingleCell(next, 'conveyor', point.row, point.col, floor, direction, anchor, options)
    next = result.topology
    painted = painted || result.painted
  }
  return { topology: next, painted }
}

/** 一段连刷：cells 需有序，方向按相邻走向回填。输送带按 2×2 块锚点归并。 */
export function paintStroke(
  topology: TopologyPayload,
  type: CellType,
  cells: GridPoint[],
  floor = GROUND_FLOOR,
  options?: { ground?: boolean },
): TopologyPayload {
  const ordered = type === 'conveyor' ? snapConveyorAnchors(uniqueOrderedCells(cells)) : uniqueOrderedCells(cells)
  let current = topology
  let previous: GridPoint | null = null
  for (const point of ordered) {
    if (previous && previous.row === point.row && previous.col === point.col) continue
    const result = paintCell(current, type, point.row, point.col, floor, previous, options)
    current = result.topology
    previous = point
  }
  return current
}

/** 把连续刷点按格子本身去重保序（锚点即所刷格子）。重叠保护在 paintConveyorBlock 里拦下相邻错位块，连刷自然以每 2 格一块铺出。 */
function snapConveyorAnchors(cells: GridPoint[]): GridPoint[] {
  const seen = new Set<string>()
  const result: GridPoint[] = []
  for (const point of cells) {
    const key = `${point.row}:${point.col}`
    if (seen.has(key)) continue
    seen.add(key)
    result.push(point)
  }
  return result
}

/**
 * 放置节点。目标为不可通行/缺失时自动铺 road；已占格先移除旧节点再落新节点。
 * 返回 null 表示被拒（附 message）。
 */
export function placeNode(
  topology: TopologyPayload,
  type: NodeType,
  row: number,
  col: number,
  floor = GROUND_FLOOR,
): { topology: TopologyPayload; message?: string } | null {
  if (row < 0 || row >= topology.grid.rows || col < 0 || col >= topology.grid.cols) {
    return { topology, message: '目标超出网格范围。' }
  }
  // 出货位只能放在地面输送线上：四向车仅沿车库巷道、升降通道和地面输送线移动。
  if (type === 'shuttle_dock') {
    const hit = findCell(topology, row, col, floor)
    if (!hit || hit.type !== 'conveyor' || !hit.conveyorGround) {
      return { topology, message: '出货位只能放在地面输送线上（四向车仅沿巷道、升降通道和地面输送线移动）。' }
    }
  }
  // 输送带上的设备（闸口/缓存位/移载机）吸附到所在 2×2 块锚点，与输送带块同格对齐
  if (CONVEYOR_MOUNTED_NODE_TYPES.has(type)) {
    const hit = findCell(topology, row, col, floor)
    if (hit?.type === 'conveyor') {
      const anchor = conveyorAnchorOf(hit)
      row = anchor.row
      col = anchor.col
    }
  }
  const existingNode = findNodeAt(topology, row, col, floor)
  const existingCell = findCell(topology, row, col, floor)

  if (existingCell?.type === 'conveyor' && !CONVEYOR_MOUNTED_NODE_TYPES.has(type)) {
    return { topology, message: '该格是输送带，节点只能放在通行格上；先删除输送带再放置。' }
  }

  let next = cloneTopology(topology)
  if (existingNode) next = eraseAt(next, row, col, floor).topology

  const nodeCell = findCell(next, row, col, floor)
  if (!nodeCell) {
    const road = makeCell(row, col, 'road', floor)
    road.zoneId = referenceZoneForCell(next, row, col)
    next.cells.push(road)
  } else if (nodeCell.type === 'blocked') {
    const index = next.cells.findIndex((cell) => cell.id === nodeCell.id)
    next.cells[index] = { ...nodeCell, type: 'road', walkable: true, conveyorDirection: null, cost: 1 }
  }

  const id = buildNextNodeId(next.nodes, type)
  const zoneId = zoneRefForNodeType(next, type, row, col)
  const size = nodeFootprint(next, { type, zoneId })
  if (size > 1) {
    if (row + size - 1 >= next.grid.rows || col + size - 1 >= next.grid.cols) {
      return { topology, message: '2×2 节点超出网格范围。' }
    }
    for (let r = row; r < row + size; r += 1) {
      for (let c = col; c < col + size; c += 1) {
        if (r === row && c === col) continue
        if (findNodeAt(next, r, c, floor)) {
          return { topology, message: '该区域已被其它节点占据，放不下 2×2 节点。' }
        }
      }
    }
  }
  const node: TopologyNode = {
    id,
    type,
    floor,
    row,
    col,
    zoneId,
    capacity: 1,
    label: defaultNodeLabel(type, id),
  }
  if (type === 'turn_transfer') {
    const mount = nodeCell?.type === 'conveyor' ? nodeCell : null
    const transferDeck: ConveyorDeckKey = mount && !mount.conveyorGround && (mount.levels ?? 1) > 1
      ? 'upper'
      : 'lower'
    const decks = mount ? conveyorDecksOf(next, mount) : null
    const config = decks?.[transferDeck] ?? decks?.lower
    node.transferDeck = transferDeck
    node.transferInputSide = config?.inputSide ?? 'west'
    node.transferOutputSide = config?.outputSide ?? 'north'
  }
  next.nodes.push(node)
  return { topology: finalizeTopology(next) }
}

/**
 * 在归一化矩形框内批量放置同类型节点（每格一个）。逐格复用 placeNode 的落位规则，
 * 跳过被拒 / 越界的格子。返回最终拓扑与成功放置数量。
 */
export function fillNodes(
  topology: TopologyPayload,
  type: NodeType,
  box: CellBox,
  floor = GROUND_FLOOR,
): { topology: TopologyPayload; placed: number; message?: string } {
  let current = topology
  let placed = 0
  let lastMessage: string | undefined
  for (let row = box.row0; row <= box.row1; row += 1) {
    for (let col = box.col0; col <= box.col1; col += 1) {
      if (findNodeAt(current, row, col, floor)) continue
      const result = placeNode(current, type, row, col, floor)
      if (result && !result.message) {
        current = result.topology
        placed += 1
      } else if (result?.message) {
        lastMessage = result.message
      }
    }
  }
  return { topology: current, placed, message: placed ? undefined : lastMessage }
}

/**
 * 移动节点到新格（保留 id / label / capacity，重新计算 zoneId）。目标格被占先移除旧节点；
 * 目标为输送带且类型不允许时拒绝。引用该节点的资源底格 cellIds 同步更新到新格。
 */
export function moveNode(
  topology: TopologyPayload,
  nodeId: string,
  row: number,
  col: number,
  floor = GROUND_FLOOR,
): { topology: TopologyPayload; message?: string } | null {
  const node = topology.nodes.find((item) => item.id === nodeId)
  if (!node) return { topology, message: '找不到该节点。' }
  if (row < 0 || row >= topology.grid.rows || col < 0 || col >= topology.grid.cols) {
    return { topology, message: '目标超出网格范围。' }
  }
  // 出货位只能放在地面输送线上：四向车仅沿车库巷道、升降通道和地面输送线移动。
  if (node.type === 'shuttle_dock') {
    const hit = findCell(topology, row, col, floor)
    if (!hit || hit.type !== 'conveyor' || !hit.conveyorGround) {
      return { topology, message: '出货位只能放在地面输送线上（四向车仅沿巷道、升降通道和地面输送线移动）。' }
    }
  }
  // 输送带上的设备（闸口/缓存位/移载机）吸附到所在 2×2 块锚点，与输送带块同格对齐
  if (CONVEYOR_MOUNTED_NODE_TYPES.has(node.type)) {
    const hit = findCell(topology, row, col, floor)
    if (hit?.type === 'conveyor') {
      const anchor = conveyorAnchorOf(hit)
      row = anchor.row
      col = anchor.col
    }
  }
  if (node.row === row && node.col === col && floorOf(node) === floor) {
    return { topology }
  }

  const existingCell = findCell(topology, row, col, floor)
  if (existingCell?.type === 'conveyor' && !CONVEYOR_MOUNTED_NODE_TYPES.has(node.type)) {
    return { topology, message: '该格是输送带，节点只能放在通行格上；先删除输送带再放置。' }
  }

  const next = cloneTopology(topology)
  next.nodes = next.nodes.filter((item) => item.id !== nodeId)

  const targetNode = findNodeAt(next, row, col, floor)
  if (targetNode) next.nodes = next.nodes.filter((item) => item.id !== targetNode.id)

  const nodeCell = findCell(next, row, col, floor)
  if (!nodeCell) {
    const road = makeCell(row, col, 'road', floor)
    road.zoneId = referenceZoneForCell(next, row, col)
    next.cells.push(road)
  } else if (nodeCell.type === 'blocked') {
    const index = next.cells.findIndex((cell) => cell.id === nodeCell.id)
    next.cells[index] = { ...nodeCell, type: 'road', walkable: true, conveyorDirection: null, cost: 1 }
  }

  const zoneId = zoneRefForNodeType(next, node.type, row, col)
  const size = nodeFootprint(next, { type: node.type, zoneId })
  if (size > 1) {
    if (row + size - 1 >= next.grid.rows || col + size - 1 >= next.grid.cols) {
      return { topology, message: '2×2 节点超出网格范围。' }
    }
    for (let r = row; r < row + size; r += 1) {
      for (let c = col; c < col + size; c += 1) {
        if (r === row && c === col) continue
        if (findNodeAt(next, r, c, floor)) {
          return { topology, message: '该区域已被其它节点占据，放不下 2×2 节点。' }
        }
      }
    }
  }

  const moved: TopologyNode = {
    ...node,
    row,
    col,
    floor,
    zoneId,
  }
  next.nodes.push(moved)

  return { topology: finalizeTopology(next) }
}

/** 平移区域矩形（左上角偏移 dRow/dCol，夹在网格内），并重算归属。 */
export function moveZone(
  topology: TopologyPayload,
  zoneId: string,
  dRow: number,
  dCol: number,
): TopologyPayload {
  const zone = zoneAtId(topology, zoneId)
  if (!zone) return topology
  const x = Math.max(0, Math.min(topology.grid.cols - zone.width, zone.x + dCol))
  const y = Math.max(0, Math.min(topology.grid.rows - zone.height, zone.y + dRow))
  if (x === zone.x && y === zone.y) return topology
  return updateZoneGeometry(topology, zoneId, { x, y })
}

/**
 * 平移整条输送线（4 邻接连通段）：把 run 内所有 conveyor 格整体平移 dRow/dCol，
 * 保持 2×2 块对齐、方向/层数不变。目标被占用或越界时返回 message 拒绝。
 */
export function moveConveyorRun(
  topology: TopologyPayload,
  startCellId: string,
  dRow: number,
  dCol: number,
  floor = GROUND_FLOOR,
): { topology: TopologyPayload; message?: string } {
  const dr = Math.round(dRow)
  const dc = Math.round(dCol)
  if (dr === 0 && dc === 0) return { topology }

  const run = conveyorRunFromCell(topology, startCellId, floor)
  if (!run.length) return { topology, message: '不是输送带，无法移动。' }

  const runIdSet = new Set(run.map((cell) => cell.id))
  const runCoordSet = new Set(run.map((cell) => `${cell.row}:${cell.col}`))

  // 落在带上的设备节点（同层），随线平移
  const mountedNodes = topology.nodes.filter(
    (node) =>
      CONVEYOR_MOUNTED_NODE_TYPES.has(node.type) &&
      floorOf(node) === floor &&
      runCoordSet.has(`${node.row}:${node.col}`),
  )
  const mountedIds = new Set(mountedNodes.map((node) => node.id))

  // 校验：越界 / 目标被非本 run 的格或节点占据（road 允许覆盖）
  for (const cell of run) {
    const nr = cell.row + dr
    const nc = cell.col + dc
    if (nr < 0 || nr >= topology.grid.rows || nc < 0 || nc >= topology.grid.cols) {
      return { topology, message: '目标超出网格范围。' }
    }
    const existing = findCell(topology, nr, nc, floor)
    if (existing && !runIdSet.has(existing.id) && existing.type !== 'road') {
      return { topology, message: '目标位置已被其它格子占用。' }
    }
    const node = findNodeAt(topology, nr, nc, floor)
    if (node && !mountedIds.has(node.id)) {
      return { topology, message: '目标位置已有节点占用。' }
    }
  }
  for (const node of mountedNodes) {
    const nr = node.row + dr
    const nc = node.col + dc
    if (nr < 0 || nr >= topology.grid.rows || nc < 0 || nc >= topology.grid.cols) {
      return { topology, message: '设备节点目标超出网格范围。' }
    }
    const other = findNodeAt(topology, nr, nc, floor)
    if (other && !mountedIds.has(other.id)) {
      return { topology, message: '设备节点目标已被其它节点占用。' }
    }
  }

  let next = cloneTopology(topology)

  // 目标处被覆盖的 road 格要清掉（腾给 conveyor）
  const overwriteIds = new Set<string>()
  for (const cell of run) {
    const existing = findCell(next, cell.row + dr, cell.col + dc, floor)
    if (existing && !runIdSet.has(existing.id) && existing.type === 'road') {
      overwriteIds.add(existing.id)
    }
  }

  // 删除旧 run 格与目标处被覆盖的 road 格
  const removeIds = new Set([...runIdSet, ...overwriteIds])
  next.cells = next.cells.filter((cell) => !removeIds.has(cell.id))

  // 新增平移后的 conveyor 格（拷贝方向/层数/成本，重算 zoneId，锚点随动）
  for (const cell of run) {
    const nr = cell.row + dr
    const nc = cell.col + dc
    const a = conveyorAnchorOf(cell)
    next.cells.push({
      ...cell,
      id: buildCellId(nr, nc, floor),
      row: nr,
      col: nc,
      floor,
      zoneId: referenceZoneForCell(next, nr, nc),
      conveyorAnchor: { row: a.row + dr, col: a.col + dc },
    })
  }

  // 设备节点随动（简化模型后无输送带设备，此分支仅保留占位，正常为空）
  next.nodes = next.nodes.map((node) => {
    if (!mountedIds.has(node.id)) return node
    return {
      ...node,
      row: node.row + dr,
      col: node.col + dc,
      zoneId: zoneRefForNodeType(next, node.type, node.row + dr, node.col + dc),
    }
  })

  return { topology: finalizeTopology(next) }
}

/** 擦除一个对象：优先节点，其次格子；同时清理资源引用。 */
export function eraseAt(
  topology: TopologyPayload,
  row: number,
  col: number,
  floor = GROUND_FLOOR,
): { topology: TopologyPayload; erased: 'node' | 'cell' | null } {
  let next = cloneTopology(topology)
  const node = findNodeAt(next, row, col, floor)
  if (node) {
    next.nodes = next.nodes.filter((item) => item.id !== node.id)
    next = finalizeTopology(next)
    return { topology: next, erased: 'node' }
  }
  const cell = findCell(next, row, col, floor)
  if (cell) {
    if (cell.type === 'conveyor') {
      const blockIds = new Set(conveyorBlockCellIds(next, cell.id))
      next.cells = next.cells.filter((item) => !blockIds.has(item.id))
    } else {
      next.cells = next.cells.filter((item) => item.id !== cell.id)
    }
    next = finalizeTopology(next)
    return { topology: next, erased: 'cell' }
  }
  return { topology: topology, erased: null }
}

/** 框选内四向车库（shuttle）的 2×2 货架槽位锚点（含端点，槽位与框选矩形相交即选中）。 */
export function shuttleSlotsInBox(
  topology: TopologyPayload,
  box: CellBox,
): { row: number; col: number }[] {
  const slots: { row: number; col: number }[] = []
  for (const zone of topology.zones) {
    if (zone.type !== 'shuttle') continue
    for (let r = zone.y; r + 1 < zone.y + zone.height; r += 2) {
      for (let c = zone.x; c + 1 < zone.x + zone.width; c += 2) {
        const hit =
          isPointInBox(box, r, c) ||
          isPointInBox(box, r, c + 1) ||
          isPointInBox(box, r + 1, c) ||
          isPointInBox(box, r + 1, c + 1)
        if (hit) slots.push({ row: r, col: c })
      }
    }
  }
  return slots
}

/**
 * 四向车库 2×2 槽位在 4 个「货架层」上的巷道位掩码（bit 0 = 第 1 层 … bit 3 = 第 4 层）。
 * 新数据读 `zone.slotAisles`；旧数据（未存 slotAisles）按「4 格全 road = 所有层都是巷道」兼容回退。
 */
export function shuttleSlotAisleMask(
  topology: Pick<TopologyPayload, 'cells' | 'zones'>,
  zoneId: string,
  row: number,
  col: number,
  floor = GROUND_FLOOR,
): number {
  const zone = findZoneById(topology, zoneId)
  if (!zone) return 0
  const stored = zone.slotAisles?.[`${row},${col}`]
  if (stored !== undefined) return stored
  for (let dr = 0; dr < 2; dr += 1) {
    for (let dc = 0; dc < 2; dc += 1) {
      const cell = findCell(topology, row + dr, col + dc, floor)
      if (!cell || cell.type !== 'road') return 0
    }
  }
  return 0b1111
}

/** 四向车库 2×2 槽位是否已被删除（空位，不再渲染货架/巷道）。 */
export function shuttleSlotRemoved(
  topology: Pick<TopologyPayload, 'zones'>,
  zoneId: string,
  row: number,
  col: number,
): boolean {
  const zone = findZoneById(topology, zoneId)
  if (!zone) return false
  return zone.removedSlots?.includes(`${row},${col}`) ?? false
}

/** 四向车库 2×2 槽位是否为「升降通道（电梯）」竖井。 */
export function shuttleSlotLift(
  topology: Pick<TopologyPayload, 'zones'>,
  zoneId: string,
  row: number,
  col: number,
): boolean {
  const zone = findZoneById(topology, zoneId)
  if (!zone) return false
  return zone.slotLifts?.includes(`${row},${col}`) ?? false
}

/**
 * 把一组 2×2 槽位在指定「货架层」（level 1..4）上设为巷道 / 恢复货架，每层独立。
 * 槽位状态记录在所在 shuttle 区域的 `slotAisles`（位掩码）；同时把「任一货架层为巷道」同步为
 * 地面层 road 格，保证 2D 仿真通行性一致（四向车沿巷道在各层之间上下移动，无需单独升降通道）。
 */
export function setShuttleSlotAisle(
  topology: TopologyPayload,
  slots: { row: number; col: number }[],
  aisle: boolean,
  floor = GROUND_FLOOR,
  level = 1,
): TopologyPayload {
  let next = cloneTopology(topology)
  const bit = 1 << (Math.max(1, Math.min(4, level)) - 1)

  for (const slot of slots) {
    const zone = zoneAtRectPoint(next, slot.row, slot.col)
    if (!zone || zone.type !== 'shuttle') continue
    const key = `${slot.row},${slot.col}`
    const current = shuttleSlotAisleMask(next, zone.id, slot.row, slot.col, floor)
    const mask = aisle ? current | bit : current & ~bit
    const slotAisles = { ...(zone.slotAisles ?? {}) }
    if (mask) slotAisles[key] = mask
    else delete slotAisles[key]
    next = {
      ...next,
      zones: next.zones.map((z) => (z.id === zone.id ? { ...z, slotAisles } : z)),
    }
  }

  // 通行性同步：任一货架层为巷道 → 铺 road；所有层都不是巷道 → 清 road
  for (const slot of slots) {
    const zone = zoneAtRectPoint(next, slot.row, slot.col)
    if (!zone || zone.type !== 'shuttle') continue
    const mask = zone.slotAisles?.[`${slot.row},${slot.col}`] ?? 0
    if (mask) {
      for (let dr = 0; dr < 2; dr += 1) {
        for (let dc = 0; dc < 2; dc += 1) {
          next = paintCell(next, 'road', slot.row + dr, slot.col + dc, floor).topology
        }
      }
    } else {
      const roadIds = new Set<string>()
      for (let dr = 0; dr < 2; dr += 1) {
        for (let dc = 0; dc < 2; dc += 1) {
          const cell = findCell(next, slot.row + dr, slot.col + dc, floor)
          if (cell && cell.type === 'road') roadIds.add(cell.id)
        }
      }
      if (roadIds.size) next.cells = next.cells.filter((cell) => !roadIds.has(cell.id))
    }
  }
  return finalizeTopology(next)
}

/**
 * 删除 / 恢复四向车库的 2×2 槽位。删除后该位置不再渲染货架/巷道（记录在 zone.removedSlots），
 * 同时清掉槽位内的 road/货架底格与落位在此的 storage 库位节点，恢复时仅移除标记（回到默认货架）。
 */
export function setShuttleSlotRemoved(
  topology: TopologyPayload,
  slots: { row: number; col: number }[],
  removed: boolean,
  floor = GROUND_FLOOR,
): TopologyPayload {
  let next = cloneTopology(topology)

  for (const slot of slots) {
    const zone = zoneAtRectPoint(next, slot.row, slot.col)
    if (!zone || zone.type !== 'shuttle') continue
    const key = `${slot.row},${slot.col}`
    const removedSet = new Set(zone.removedSlots ?? [])
    if (removed) removedSet.add(key)
    else removedSet.delete(key)

    // 清掉该槽位的逐层巷道位掩码，避免「删除」与「巷道」状态叠加
    const slotAisles = { ...(zone.slotAisles ?? {}) }
    if (removed) delete slotAisles[key]

    next = {
      ...next,
      zones: next.zones.map((z) =>
        z.id === zone.id
          ? {
              ...z,
              removedSlots: removedSet.size ? [...removedSet] : undefined,
              slotAisles: Object.keys(slotAisles).length ? slotAisles : undefined,
            }
          : z,
      ),
    }

    if (removed) {
      // 清掉槽位 4 格内的所有格子（road / 货架底格），空出位置
      const cellIds = new Set<string>()
      for (let dr = 0; dr < 2; dr += 1) {
        for (let dc = 0; dc < 2; dc += 1) {
          const cell = findCell(next, slot.row + dr, slot.col + dc, floor)
          if (cell) cellIds.add(cell.id)
        }
      }
      if (cellIds.size) next.cells = next.cells.filter((cell) => !cellIds.has(cell.id))

      // 移除落位在此 2×2 槽位的 storage 库位节点（含 1×1/2×2 占地）
      next.nodes = next.nodes.filter((node) => {
        if (node.type !== 'storage') return true
        if (floorOf(node) !== floor) return true
        return !(node.row >= slot.row && node.row < slot.row + 2 && node.col >= slot.col && node.col < slot.col + 2)
      })
    }
  }

  return finalizeTopology(next)
}

/**
 * 把一组 2×2 槽位设为「升降通道（电梯）」/ 恢复货架。升降通道是贯穿 4 层货架的整根竖井，
 * 记录在所在 shuttle 区域的 `slotLifts`（与 removedSlots / slotAisles 三态互斥）：设为通道时从
 * removedSlots 移除、清掉逐层巷道位掩码、移除落位 storage 库位节点，并把槽位 4 格铺成 road
 * （四向车可到达升降位）；恢复时仅移除标记并清掉这 4 格 road（回到默认货架）。
 */
export function setShuttleSlotLift(
  topology: TopologyPayload,
  slots: { row: number; col: number }[],
  lift: boolean,
  floor = GROUND_FLOOR,
): TopologyPayload {
  let next = cloneTopology(topology)

  for (const slot of slots) {
    const zone = zoneAtRectPoint(next, slot.row, slot.col)
    if (!zone || zone.type !== 'shuttle') continue
    const key = `${slot.row},${slot.col}`
    const liftSet = new Set(zone.slotLifts ?? [])
    if (lift) liftSet.add(key)
    else liftSet.delete(key)

    // 三态互斥：设为升降通道时，清掉「删除」与「逐层巷道」状态
    const removedSet = new Set(zone.removedSlots ?? [])
    const slotAisles = { ...(zone.slotAisles ?? {}) }
    if (lift) {
      removedSet.delete(key)
      delete slotAisles[key]
    }

    next = {
      ...next,
      zones: next.zones.map((z) =>
        z.id === zone.id
          ? {
              ...z,
              slotLifts: liftSet.size ? [...liftSet] : undefined,
              removedSlots: removedSet.size ? [...removedSet] : undefined,
              slotAisles: Object.keys(slotAisles).length ? slotAisles : undefined,
            }
          : z,
      ),
    }
  }

  // 通行性同步：升降通道的槽位 4 格铺 road（供四向车到达）；恢复时清掉这 4 格 road。
  for (const slot of slots) {
    const zone = zoneAtRectPoint(next, slot.row, slot.col)
    if (!zone || zone.type !== 'shuttle') continue
    const isLift = zone.slotLifts?.includes(`${slot.row},${slot.col}`) ?? false
    if (isLift) {
      for (let dr = 0; dr < 2; dr += 1) {
        for (let dc = 0; dc < 2; dc += 1) {
          next = paintCell(next, 'road', slot.row + dr, slot.col + dc, floor).topology
        }
      }
      // 移除落位在此 2×2 槽位的 storage 库位节点（含 1×1/2×2 占地）
      next.nodes = next.nodes.filter((node) => {
        if (node.type !== 'storage') return true
        if (floorOf(node) !== floor) return true
        return !(node.row >= slot.row && node.row < slot.row + 2 && node.col >= slot.col && node.col < slot.col + 2)
      })
    } else {
      const roadIds = new Set<string>()
      for (let dr = 0; dr < 2; dr += 1) {
        for (let dc = 0; dc < 2; dc += 1) {
          const cell = findCell(next, slot.row + dr, slot.col + dc, floor)
          if (cell && cell.type === 'road') roadIds.add(cell.id)
        }
      }
      if (roadIds.size) next.cells = next.cells.filter((cell) => !roadIds.has(cell.id))
    }
  }

  return finalizeTopology(next)
}

export function zoneAtId(topology: TopologyPayload, zoneId: string): Zone | undefined {
  return topology.zones.find((zone) => zone.id === zoneId)
}

export function nextZoneId(zones: Zone[], type: ZoneType): string {
  const base = `zone-${type}`
  const existing = new Set(zones.map((zone) => zone.id))
  if (!existing.has(base)) return base
  let index = 2
  while (existing.has(`${base}-${index}`)) index += 1
  return `${base}-${index}`
}

function zoneLabelFor(type: ZoneType, id: string): string {
  const base = ZONE_LABELS[type].replace(/\s.*$/, '')
  const suffix = id.replace(/^zone-/, '').split('-').slice(-1)[0]
  return suffix === type ? `${base}` : `${base} ${suffix}`
}

/** 画一个矩形区域（box 已归一化，含端点）。 */
export function createZone(
  topology: TopologyPayload,
  type: ZoneType,
  box: CellBox,
  options?: { label?: string; syncMembers?: boolean },
): TopologyPayload {
  if (box.row1 < box.row0 || box.col1 < box.col0) return topology
  const next = cloneTopology(topology)
  const x = box.col0
  const y = box.row0
  const width = box.col1 - box.col0 + 1
  const height = box.row1 - box.row0 + 1
  const id = nextZoneId(next.zones, type)
  const zone: Zone = {
    id,
    label: options?.label?.trim() || zoneLabelFor(type, id),
    type,
    x,
    y,
    width,
    height,
  }
  next.zones.push(zone)

  // CTU 高架库：画区域时按「一格一库位」自动补 storage 节点（17 层料箱位），
  // 与 build_inventory / seed_demo_ctu_storage.py 的口径一致。
  if (type === 'ctu') {
    const usedIds = new Set(next.nodes.map((node) => node.id))
    let index = 1
    for (let r = y; r < y + height; r += 1) {
      for (let c = x; c < x + width; c += 1) {
        let nodeId = ''
        do {
          nodeId = `storage-${String(index).padStart(2, '0')}`
          index += 1
        } while (usedIds.has(nodeId))
        usedIds.add(nodeId)
        next.nodes.push({
          id: nodeId,
          type: 'storage',
          floor: GROUND_FLOOR,
          row: r,
          col: c,
          zoneId: id,
          capacity: 17,
          label: `CTU-${zone.label}-${r}-${c}`,
        })
      }
    }
  }

  if (options?.syncMembers !== false) return syncZoneMembership(next, id)
  return next
}

/**
 * 让区域内部对象归属该 zone：格子只要在矩形内且未归属其它 zone 就归它；
 * 节点仅在语义匹配该 zone 类型时归属（storage→ctu/shuttle 等）。
 */
export function syncZoneMembership(topology: TopologyPayload, zoneId: string): TopologyPayload {
  const zone = zoneAtId(topology, zoneId)
  if (!zone) return topology
  const next = cloneTopology(topology)
  const inside = (row: number, col: number) =>
    row >= zone.y && row < zone.y + zone.height && col >= zone.x && col < zone.x + zone.width

  next.cells = next.cells.map((cell) => {
    if (cell.zoneId === zoneId) {
      return inside(cell.row, cell.col) ? cell : { ...cell, zoneId: null }
    }
    if (cell.zoneId == null && inside(cell.row, cell.col)) {
      return { ...cell, zoneId }
    }
    return cell
  })

  const affinity = NODE_ZONE_AFFINITY
  next.nodes = next.nodes.map((node) => {
    if (node.zoneId === zoneId) {
      return inside(node.row, node.col) && affinity[node.type].has(zone.type) ? node : { ...node, zoneId: null }
    }
    if (node.zoneId == null && inside(node.row, node.col) && affinity[node.type].has(zone.type)) {
      return { ...node, zoneId }
    }
    return node
  })
  return next
}

export function updateZoneGeometry(
  topology: TopologyPayload,
  zoneId: string,
  patch: { x?: number; y?: number; width?: number; height?: number },
): TopologyPayload {
  const zone = zoneAtId(topology, zoneId)
  if (!zone) return topology
  const next = cloneTopology(topology)
  const index = next.zones.findIndex((item) => item.id === zoneId)
  const dRow = (patch.y ?? zone.y) - zone.y
  const dCol = (patch.x ?? zone.x) - zone.x
  next.zones[index] = {
    ...zone,
    ...patch,
    width: Math.max(1, patch.width ?? zone.width),
    height: Math.max(1, patch.height ?? zone.height),
    // 槽位级数据（slotAisles / removedSlots / slotLifts）的键为绝对 "行,列"，区域平移时同步平移键
    ...(dRow !== 0 || dCol !== 0
      ? {
          slotAisles: translateSlotAisleKeys(zone.slotAisles, dRow, dCol),
          removedSlots: translateRemovedSlotKeys(zone.removedSlots, dRow, dCol),
          slotLifts: translateSlotLiftKeys(zone.slotLifts, dRow, dCol),
        }
      : {}),
  }
  return syncZoneMembership(next, zoneId)
}

/** 区域平移时，把四向车库逐层巷道位掩码的键（绝对 "行,列"）整体平移。 */
function translateSlotAisleKeys(
  slotAisles: Record<string, number> | undefined,
  dRow: number,
  dCol: number,
): Record<string, number> | undefined {
  if (!slotAisles) return undefined
  const next: Record<string, number> = {}
  for (const [key, value] of Object.entries(slotAisles)) {
    const [row, col] = key.split(',').map(Number)
    next[`${row + dRow},${col + dCol}`] = value
  }
  return next
}

/** 区域平移时，把被删除槽位列表（绝对 "行,列"）整体平移。 */
function translateRemovedSlotKeys(
  removedSlots: string[] | undefined,
  dRow: number,
  dCol: number,
): string[] | undefined {
  if (!removedSlots || removedSlots.length === 0) return undefined
  return removedSlots.map((key) => {
    const [row, col] = key.split(',').map(Number)
    return `${row + dRow},${col + dCol}`
  })
}

/** 区域平移时，把升降通道槽位列表（绝对 "行,列"）整体平移。 */
function translateSlotLiftKeys(
  slotLifts: string[] | undefined,
  dRow: number,
  dCol: number,
): string[] | undefined {
  if (!slotLifts || slotLifts.length === 0) return undefined
  return slotLifts.map((key) => {
    const [row, col] = key.split(',').map(Number)
    return `${row + dRow},${col + dCol}`
  })
}

export function removeZone(topology: TopologyPayload, zoneId: string): TopologyPayload {
  const next = cloneTopology(topology)
  const removed = next.zones.find((zone) => zone.id === zoneId)
  const keepZoneIds = new Set(next.zones.filter((zone) => zone.id !== zoneId).map((zone) => zone.id))
  next.zones = next.zones.filter((zone) => zone.id !== zoneId)
  // CTU 区域画出来时会自动生成「一格一库位」的 storage 节点；删区域时一并删掉这些库位，
  // 否则货架已删除、库位节点还在，3D 场景仍会按库存把货物画在原位置。
  // 旧数据里的库位可能没有 zoneId，此时按位置是否落在被删区域矩形内判断。
  // 其它类型节点（如 CTU 车）只清空 zoneId，不随区域删除。
  const insideRemovedCtu = (node: TopologyNode) =>
    removed?.type === 'ctu' &&
    node.row >= removed.y &&
    node.row < removed.y + removed.height &&
    node.col >= removed.x &&
    node.col < removed.x + removed.width
  next.nodes = next.nodes.filter((node) => {
    if (node.type !== 'storage') return true
    if (node.zoneId === zoneId) return false
    const unowned = node.zoneId == null || !keepZoneIds.has(node.zoneId)
    return !(unowned && insideRemovedCtu(node))
  })
  next.cells = next.cells.map((cell) => (cell.zoneId === zoneId ? { ...cell, zoneId: null } : cell))
  next.nodes = next.nodes.map((node) => (node.zoneId === zoneId ? { ...node, zoneId: null } : node))
  // 若某 zone 引用的 zoneId 已被删，改空。
  next.cells = next.cells.map((cell) => (cell.zoneId && !keepZoneIds.has(cell.zoneId) ? { ...cell, zoneId: null } : cell))
  next.nodes = next.nodes.map((node) => (node.zoneId && !keepZoneIds.has(node.zoneId) ? { ...node, zoneId: null } : node))
  return next
}

/**
 * 编辑结束后的统一收尾。简化模型（无 resources 资源层）后无独立资源需要清理，直接透传。
 */
export function finalizeTopology(topology: TopologyPayload): TopologyPayload {
  return topology
}

export function addFloor(topology: TopologyPayload): TopologyPayload {
  const next = cloneTopology(topology)
  const floors = next.meta.floors ?? [GROUND_FLOOR]
  const numbered = floors
    .map((floor) => /^(\d+)F$/.exec(floor))
    .filter((match): match is RegExpExecArray => Boolean(match))
    .map((match) => Number(match[1]))
  const nextNumber = numbered.length ? Math.max(...numbered) + 1 : 1
  const label = `${nextNumber}F`
  next.meta.floors = [...floors, label]
  return next
}

export function removeFloor(topology: TopologyPayload, floor: string): TopologyPayload {
  if (floor === GROUND_FLOOR) return topology
  const next = cloneTopology(topology)
  next.meta.floors = (next.meta.floors ?? [GROUND_FLOOR]).filter((item) => item !== floor)
  next.cells = next.cells.filter((cell) => floorOf(cell) !== floor)
  next.nodes = next.nodes.filter((node) => floorOf(node) !== floor)
  return finalizeTopology(next)
}

export function floorsOf(topology: TopologyPayload): string[] {
  const floors = topology.meta.floors ?? []
  return floors.length ? floors : [GROUND_FLOOR]
}

export function resizeGrid(topology: TopologyPayload, rows: number, cols: number): TopologyPayload {
  const safeRows = Math.max(1, Math.floor(rows))
  const safeCols = Math.max(1, Math.floor(cols))
  const next = cloneTopology(topology)
  next.grid = { ...next.grid, rows: safeRows, cols: safeCols }
  next.cells = next.cells.filter((cell) => cell.row < safeRows && cell.col < safeCols)
  next.nodes = next.nodes.filter((node) => node.row < safeRows && node.col < safeCols)
  return finalizeTopology(next)
}

/** 结构等值比较（用于 dirty 判定，忽略键顺序）。 */
export function deepEqual(left: unknown, right: unknown): boolean {
  if (left === right) return true
  if (typeof left !== 'object' || typeof right !== 'object' || left === null || right === null) return false
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false
    return left.every((value, index) => deepEqual(value, right[index]))
  }
  const leftKeys = Object.keys(left as Record<string, unknown>)
  const rightKeys = Object.keys(right as Record<string, unknown>)
  if (leftKeys.length !== rightKeys.length) return false
  return leftKeys.every((key) => deepEqual((left as Record<string, unknown>)[key], (right as Record<string, unknown>)[key]))
}

export function nodeAtId(topology: TopologyPayload, nodeId: string): TopologyNode | undefined {
  return topology.nodes.find((node) => node.id === nodeId)
}

export function cellAtId(topology: TopologyPayload, cellId: string): TopologyCell | undefined {
  return topology.cells.find((cell) => cell.id === cellId)
}
