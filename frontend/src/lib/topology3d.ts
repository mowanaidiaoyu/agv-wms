import { GROUND_FLOOR, conveyorAnchorOf, floorsOf, nodeFootprint } from './topology'
import type {
  CellType,
  ConveyorDirection,
  GoodsSlot,
  NodeType,
  TopologyCell,
  TopologyNode,
  TopologyPayload,
  Zone,
  ZoneType,
} from '../types'

/**
 * 3D 编辑语义常量（来自 CAD 图文字：CTU 库 17 层、四向车平库 4 层）。
 * buildSceneSpec(topology) 从同一份 draft 派生纯几何描述；3D 编辑器在其中做 CRUD。
 */
export const CELL = 1 // 一格的世界长度
export const FLOOR_HEIGHT = 4 // 楼层净高
export const LEVEL_HEIGHT = 0.5 // 货架每层高度
export const ZONE_LEVELS: Record<ZoneType, number> = {
  ctu: 17,
  shuttle: 4,
  port: 1,
}

/** 货架每层高度（按区域类型）：CTU 料箱 0.5，四向车托盘 1.4（平库货位更高）。 */
export const ZONE_LEVEL_HEIGHT: Record<ZoneType, number> = {
  ctu: 0.5,
  shuttle: 1.4,
  port: 0.5,
}

/** CTU 车载货层数：一次最多装 5 箱（与后端 ROBOT_CARRY_CAPACITY["ctu"] 一致）。 */
export const CTU_ROBOT_LEVELS = 5

/** 商品大类 → 库内货箱颜色（18 类真实 SKU 大类；未知大类回退灰色）。 */
export const GOODS_CATEGORY_COLORS: Record<string, string> = {
  螺母: '#ef4444',
  螺栓: '#f97316',
  垫圈: '#f59e0b',
  螺钉: '#84cc16',
  自攻螺钉: '#22c55e',
  螺柱: '#10b981',
  膨胀件: '#14b8a6',
  组合件: '#06b6d4',
  销键: '#0ea5e9',
  铆钉: '#3b82f6',
  索具: '#6366f1',
  其它: '#8b5cf6',
  挡圈: '#a855f7',
  焊接: '#d946ef',
  电力配件: '#ec4899',
  原材料: '#f43f5e',
  建筑配件: '#94a3b8',
  木螺钉: '#78716c',
}

export const GOODS_DEFAULT_COLOR = '#94a3b8'

export type SceneColor = string

export type Direction3D = { dx: number; dz: number }

/** 2D 方向 → 3D 走向（列 → +x，行增大 → -z） */
export const DIRECTION_VEC3: Record<ConveyorDirection, Direction3D> = {
  right: { dx: 1, dz: 0 },
  down: { dx: 0, dz: -1 },
  left: { dx: -1, dz: 0 },
  up: { dx: 0, dz: 1 },
}

/** 楼层在世界里的基准高度（每层 slab 底面所在 y）。 */
export function floorPlaneY(floorIndex: number): number {
  return floorIndex * FLOOR_HEIGHT
}

/** 世界坐标 → 网格坐标（worldX/worldZ 的逆）。 */
export function worldToCell(x: number, z: number, cols: number, rows: number): { row: number; col: number } {
  const col = Math.round(x + cols / 2 - 0.5)
  const row = Math.round(rows / 2 - z - 0.5)
  return {
    col: Math.max(0, Math.min(cols - 1, col)),
    row: Math.max(0, Math.min(rows - 1, row)),
  }
}

/** 实体元数据：选择/删除/悬停的对象身份。 */
export type SceneEntityKind = 'cell' | 'node' | 'zone' | 'resource' | 'shuttle-slot'

export interface SceneEntity {
  kind: SceneEntityKind
  id: string
  floor: string
}

export interface SceneBox {
  kind: 'box'
  key: string
  position: [number, number, number]
  size: [number, number, number]
  color: string
  emissive?: string
  opacity?: number
  /** 细长部件（杆/管线）用 wire 材质更耐看 */
  wire?: boolean
  floor: string
  entity?: SceneEntity
  /** 四向车库槽位锚点（2×2 槽左上角），用于按槽位高亮/选择（含已删除槽位线框标记） */
  slotRow?: number
  slotCol?: number
}

export interface SceneSlab {
  kind: 'slab'
  key: string
  label: string
  /** 中心 */
  x: number
  z: number
  width: number
  depth: number
  y: number
  entity?: SceneEntity
}

/** 独立货架（按 17/4 层映射），渲染为立柱 + 层板，不带回填 */
export interface SceneRack {
  kind: 'rack'
  key: string
  x: number
  z: number
  y: number
  cellsW: number
  cellsD: number
  levels: number
  slotSize: number
  levelHeight: number
  color: string
  /** 每层货架（层板）颜色，与 levels 等长；缺省时整架用 color。用于四向车库逐层巷道着色 */
  levelColors?: string[]
  zoneType: ZoneType
  label: string
  floor: string
  entity?: SceneEntity
  /** 四向车库槽位锚点（2×2 槽左上角），用于按槽位高亮/选择 */
  slotRow?: number
  slotCol?: number
}

export interface SceneTower {
  kind: 'tower'
  key: string
  x: number
  z: number
  y: number
  height: number
  color: string
  floor: string
  entity?: SceneEntity
}

export type SceneItem = SceneBox | SceneSlab | SceneRack | SceneTower

export interface SceneCamera {
  position: [number, number, number]
  target: [number, number, number]
}

export interface SceneSpec {
  floors: string[]
  cols: number
  rows: number
  maxY: number
  items: SceneItem[]
  camera: SceneCamera
}

/**
 * 商品层单独派生，供编辑器缓存。商品位置只依赖网格、楼层顺序与库位节点，
 * 道路等拓扑内容变化时无需重新生成全部商品实例。
 */
export function buildGoodsSceneBoxes(
  topology: TopologyPayload,
  goods: GoodsSlot[],
  floors = floorsOf(topology),
): SceneBox[] {
  if (!goods.length) return []
  const { rows, cols } = topology.grid
  const nodeById = new Map(topology.nodes.map((node) => [node.id, node]))
  const levelHeight = ZONE_LEVEL_HEIGHT.ctu
  const boxes: SceneBox[] = []
  for (const slot of goods) {
    const node = nodeById.get(slot.nodeId)
    if (!node) continue
    const floor = itemFloor(node)
    const floorIndex = Math.max(0, floors.indexOf(floor))
    const y = floorIndex * FLOOR_HEIGHT + (Math.max(1, slot.layer || 1) - 0.5) * levelHeight
    const color = GOODS_CATEGORY_COLORS[slot.largeCategory ?? ''] ?? GOODS_DEFAULT_COLOR
    boxes.push({
      kind: 'box',
      key: `goods-${slot.nodeId}-${slot.layer}`,
      position: [worldX(node.col, cols), y, worldZ(node.row, rows)],
      size: [CELL * 0.6, levelHeight * 0.7, CELL * 0.6],
      color,
      emissive: color,
      opacity: 0.9,
      floor,
    })
  }
  return boxes
}

export function worldX(col: number, cols: number): number {
  return col + 0.5 - cols / 2
}

export function worldZ(row: number, rows: number): number {
  return rows / 2 - (row + 0.5)
}

/** 仿真机器人所在格的世界坐标（贴地，即该格 floor 平面中心；车身局部再自行上抬）。 */
export function robotWorldPosition(
  floor: string,
  row: number,
  col: number,
  floors: string[],
  cols: number,
  rows: number,
): [number, number, number] {
  const floorIndex = Math.max(0, floors.indexOf(floor))
  return [worldX(col, cols), floorIndex * FLOOR_HEIGHT, worldZ(row, rows)]
}

/** 四向车库槽位实体 id 编码：`zoneId@row,col`。 */
export function shuttleSlotEntityId(zoneId: string, row: number, col: number): string {
  return `${zoneId}@${row},${col}`
}

/** 解析 shuttle-slot 实体 id（shuttleSlotEntityId 的逆）。 */
export function parseShuttleSlotEntityId(id: string): { zoneId: string; row: number; col: number } | null {
  const at = id.indexOf('@')
  if (at < 0) return null
  const zoneId = id.slice(0, at)
  const parts = id.slice(at + 1).split(',')
  if (parts.length !== 2) return null
  const row = Number(parts[0])
  const col = Number(parts[1])
  if (!Number.isInteger(row) || !Number.isInteger(col)) return null
  return { zoneId, row, col }
}

function itemFloor(item: { floor?: string }): string {
  return item.floor ?? GROUND_FLOOR
}

/** 取某区域在“哪些楼层”真正落位（该层存在属于它的格子或库位节点），供货架锚定 */
function zoneFloorMembership(zone: Zone, cells: TopologyCell[], nodes: TopologyNode[]): string[] {
  const floors = new Set<string>()
  for (const cell of cells) {
    if (cell.zoneId === zone.id) floors.add(itemFloor(cell))
  }
  for (const node of nodes) {
    if (node.zoneId === zone.id && node.type === 'storage') floors.add(itemFloor(node))
  }
  if (!floors.size) floors.add(GROUND_FLOOR)
  return [...floors]
}

export function buildSceneSpec(topology: TopologyPayload, goods?: GoodsSlot[]): SceneSpec {
  const floors = floorsOf(topology)
  const { rows, cols } = topology.grid
  const width = cols * CELL
  const depth = rows * CELL
  const cellColor: Record<CellType, string> = {
    road: '#2e3a52',
    blocked: '#8b2f2f',
    conveyor: '#2f6f9f',
  }
  const aisleColor = '#3a4f6e' // 四向车库内巷道（通行路），与普通路面色区分
  const shuttleLiftColor = '#22c55e' // 四向车库升降通道（电梯）竖井，绿色区别于货架橙/巷道蓝
  const zonePadColor: Record<ZoneType, string> = {
    ctu: '#0e7490',
    shuttle: '#b45309',
    port: '#0d9488',
  }
  const items: SceneItem[] = []
  const floorIndexOf = (floor?: string) => Math.max(0, floors.indexOf(floor ?? GROUND_FLOOR))

  // 每层地面
  let maxY = 0
  floors.forEach((floor, index) => {
    const y = index * FLOOR_HEIGHT
    items.push({
      kind: 'slab',
      key: `floor-${floor}`,
      label: floor,
      x: 0,
      z: 0,
      width,
      depth,
      y,
    })
    maxY = Math.max(maxY, y)
  })

  // 格子（每层独立）
  const zoneById = new Map<string, Zone>()
  for (const zone of topology.zones) {
    zoneById.set(zone.id, zone)
  }
  const cellAt = new Map<string, TopologyCell>()
  for (const cell of topology.cells) {
    cellAt.set(`${itemFloor(cell)}:${cell.row}:${cell.col}`, cell)
  }

  // blocked → 墙；conveyor → 传送带体 + 方向标记；road → 路面亮纹
  for (const cell of topology.cells) {
    const floor = itemFloor(cell)
    const fy = floorIndexOf(floor)
    const y = fy * FLOOR_HEIGHT
    const x = worldX(cell.col, cols)
    const z = worldZ(cell.row, rows)
    const entity: SceneEntity = { kind: 'cell', id: cell.id, floor }

    if (cell.type === 'road') {
      const zone = cell.zoneId ? zoneById.get(cell.zoneId) : undefined
      const isShuttleAisle = zone?.type === 'shuttle'
      items.push({
        kind: 'box',
        key: cell.id,
        position: [x, y + 0.2, z],
        size: [CELL * 0.92, 0.04, CELL * 0.92],
        color: isShuttleAisle ? aisleColor : cellColor.road,
        floor,
        entity,
      })
    } else if (cell.type === 'blocked') {
      items.push({
        kind: 'box',
        key: cell.id,
        position: [x, y + 0.55, z],
        size: [CELL * 0.92, 1.1, CELL * 0.92],
        color: cellColor.blocked,
        floor,
        entity,
      })
    } else {
      // conveyor：2×2 占位，只画块锚点格（任意奇偶），一块 2×2 传送带；其余 3 格被覆盖跳过
      const anchor = conveyorAnchorOf(cell)
      if (cell.row !== anchor.row || cell.col !== anchor.col) continue
      const levels = Math.max(1, cell.levels ?? 1)
      const dir = DIRECTION_VEC3[cell.conveyorDirection ?? 'right']
      const beltW = CELL * 2 * 0.98 // 相邻块贴合，消除块间间隔，视觉上连续
      const cx = x + CELL / 2
      const cz = z - CELL / 2

      // 单层/双层统一为「四角立柱 + 悬空带面」的抬升结构，层数决定带面数量
      const deckBase = 0.42 // 最低带面离地高度
      const deckStep = 0.42 // 相邻带面垂直间距
      const deckYs = Array.from({ length: levels }, (_, i) => deckBase + i * deckStep)
      const topY = deckYs[deckYs.length - 1]

      // 四角立柱：从地面一直撑到最上层带面
      const legTop = topY + 0.05
      const legCenter = (0.05 + legTop) / 2
      const legHeight = legTop - 0.05
      const corner = beltW / 2 - 0.06
      const corners: [number, number][] = [
        [corner, corner],
        [corner, -corner],
        [-corner, corner],
        [-corner, -corner],
      ]
      for (const [ox, oz] of corners) {
        items.push({
          kind: 'box',
          key: `${cell.id}-post-${ox}-${oz}`,
          position: [cx + ox, y + legCenter, cz + oz],
          size: [0.06, legHeight, 0.06],
          color: '#64748b',
          floor,
          entity,
        })
      }

      deckYs.forEach((deckY, deckIndex) => {
        // 传送带面
        items.push({
          kind: 'box',
          key: deckIndex === 0 ? cell.id : `${cell.id}-upper-${deckIndex}`,
          position: [cx, y + deckY, cz],
          size: [beltW, 0.1, beltW],
          color: cellColor.conveyor,
          emissive: '#0b2740',
          floor,
          entity,
        })
        // 两侧护栏（与走向平行，贴 2×2 外沿）
        const alongX = dir.dx !== 0
        const railSize: [number, number, number] = alongX
          ? [beltW, 0.06, 0.05]
          : [0.05, 0.06, beltW]
        const perp: [number, number] = [-dir.dz, dir.dx]
        const edge = beltW / 2 - 0.02
        for (const side of [1, -1]) {
          items.push({
            kind: 'box',
            key: `${cell.id}-rail-${deckIndex}-${side}`,
            position: [cx + perp[0] * edge * side, y + deckY + 0.05, cz + perp[1] * edge * side],
            size: railSize,
            color: '#7dd3fc',
            floor,
            entity,
          })
        }
      })

      // 方向箭头（置于最上层，块中心沿走向偏移）
      items.push({
        kind: 'box',
        key: `${cell.id}-arrow`,
        position: [cx + dir.dx * 0.5, y + topY + 0.09, cz + dir.dz * 0.5],
        size: [0.24, 0.06, 0.24],
        color: '#fbbf24',
        emissive: '#f59e0b',
        floor,
        entity,
      })
    }
  }

  // 区域：按层锚定货架 / 贴地盘
  for (const zone of topology.zones) {
    const baseColor = zonePadColor[zone.type]
    const levels = ZONE_LEVELS[zone.type]
    const anchorFloors = zoneFloorMembership(zone, topology.cells, topology.nodes)
    for (const floor of anchorFloors) {
      const y = floorIndexOf(floor) * FLOOR_HEIGHT
      const cx = zone.x + zone.width / 2 - cols / 2
      const cz = rows / 2 - (zone.y + zone.height / 2)
      const entity: SceneEntity = { kind: 'zone', id: zone.id, floor }
      if (zone.type === 'shuttle') {
        // 四向车库：类似 CTU 货架，按 2×2 槽位铺 4 层货架；某槽位 4 格全为 road（巷道）则挖空，
        // 让四向车在其中上下左右通行。薄底板 + 线框仅用于标出区域边界。
        items.push({
          kind: 'box',
          key: `zone-${zone.id}-${floor}`,
          position: [cx, y + 0.21, cz],
          size: [zone.width * CELL, 0.1, zone.height * CELL],
          color: baseColor,
          emissive: baseColor,
          opacity: 0.4,
          floor,
          entity,
        })
        items.push({
          kind: 'box',
          key: `zone-${zone.id}-${floor}-outline`,
          position: [cx, y + 0.26, cz],
          size: [zone.width * CELL + 0.06, 0.2, zone.height * CELL + 0.06],
          color: baseColor,
          wire: true,
          floor,
          entity,
        })
        for (let r = zone.y; r + 1 < zone.y + zone.height; r += 2) {
          for (let c = zone.x; c + 1 < zone.x + zone.width; c += 2) {
            // 已删除槽位：不铺货架/巷道，只画一个贴地线框，标识「空位」且可点选/框选恢复
            if (zone.removedSlots?.includes(`${r},${c}`)) {
              items.push({
                kind: 'box',
                key: `zone-${zone.id}-${floor}-slot-${r}-${c}-empty`,
                position: [worldX(c, cols) + CELL / 2, y + 0.28, worldZ(r, rows) - CELL / 2],
                size: [2 * CELL - 0.14, 0.02, 2 * CELL - 0.14],
                color: '#94a3b8',
                wire: true,
                floor,
                entity: { kind: 'shuttle-slot', id: shuttleSlotEntityId(zone.id, r, c), floor },
                slotRow: r,
                slotCol: c,
              })
              continue
            }
            // 升降通道（电梯）槽位：渲染一根贯穿 4 层的绿色竖井，标识四向车跨层必经的固定上下位
            if (zone.slotLifts?.includes(`${r},${c}`)) {
              const liftHeight = ZONE_LEVELS.shuttle * ZONE_LEVEL_HEIGHT.shuttle
              items.push({
                kind: 'box',
                key: `zone-${zone.id}-${floor}-slot-${r}-${c}-lift`,
                position: [worldX(c, cols) + CELL / 2, y + liftHeight / 2, worldZ(r, rows) - CELL / 2],
                size: [2 * CELL - 0.14, liftHeight, 2 * CELL - 0.14],
                color: shuttleLiftColor,
                emissive: '#15803d',
                floor,
                entity: { kind: 'shuttle-slot', id: shuttleSlotEntityId(zone.id, r, c), floor },
                slotRow: r,
                slotCol: c,
              })
              continue
            }
            // 槽位在 4 个货架层上的巷道位掩码（bit0 = 第 1 层 … bit3 = 第 4 层）；旧数据按 4 格全 road 回退
            let mask = zone.slotAisles?.[`${r},${c}`]
            if (mask === undefined) {
              let allRoad = true
              for (let dr = 0; dr < 2; dr += 1) {
                for (let dc = 0; dc < 2; dc += 1) {
                  if (cellAt.get(`${floor}:${r + dr}:${c + dc}`)?.type !== 'road') {
                    allRoad = false
                    break
                  }
                }
                if (!allRoad) break
              }
              mask = allRoad ? 0b1111 : 0
            }
            const levelCount = ZONE_LEVELS.shuttle
            const levelColors: string[] = []
            for (let l = 0; l < levelCount; l += 1) {
              levelColors.push(mask & (1 << l) ? aisleColor : baseColor)
            }
            items.push({
              kind: 'rack',
              key: `zone-${zone.id}-${floor}-slot-${r}-${c}`,
              x: worldX(c, cols) + CELL / 2,
              z: worldZ(r, rows) - CELL / 2,
              y,
              cellsW: 2,
              cellsD: 2,
              levels: levelCount,
              slotSize: 2,
              levelHeight: ZONE_LEVEL_HEIGHT.shuttle,
              color: mask === 0b1111 ? aisleColor : baseColor,
              levelColors,
              zoneType: 'shuttle',
              label: zone.label,
              floor,
              entity: { kind: 'shuttle-slot', id: shuttleSlotEntityId(zone.id, r, c), floor },
              slotRow: r,
              slotCol: c,
            })
          }
        }
      } else if (levels > 1) {
        items.push({
          kind: 'rack',
          key: `zone-${zone.id}-${floor}`,
          x: cx,
          z: cz,
          y,
          cellsW: zone.width,
          cellsD: zone.height,
          levels,
          slotSize: 1,
          levelHeight: ZONE_LEVEL_HEIGHT[zone.type],
          color: baseColor,
          zoneType: zone.type,
          label: zone.label,
          floor,
          entity,
        })
      } else {
        // 水平区域（输送线出入口 port）：抬升底盘 + 四角立柱 + 顶部线框围栏，看清并便于选中/删除
        const halfW = (zone.width * CELL) / 2
        const halfD = (zone.height * CELL) / 2
        const inset = 0.2
        items.push({
          kind: 'box',
          key: `zone-${zone.id}-${floor}`,
          position: [cx, y + 0.12, cz],
          size: [zone.width * CELL, 0.24, zone.height * CELL],
          color: baseColor,
          emissive: baseColor,
          opacity: 0.55,
          floor,
          entity,
        })
        const corners: [number, number][] = [
          [cx - halfW + inset, cz - halfD + inset],
          [cx + halfW - inset, cz - halfD + inset],
          [cx - halfW + inset, cz + halfD - inset],
          [cx + halfW - inset, cz + halfD - inset],
        ]
        corners.forEach(([px, pz], i) => {
          items.push({
            kind: 'box',
            key: `zone-${zone.id}-${floor}-post-${i}`,
            position: [px, y + 0.7, pz],
            size: [0.24, 1.4, 0.24],
            color: baseColor,
            emissive: baseColor,
            floor,
            entity,
          })
        })
        items.push({
          kind: 'box',
          key: `zone-${zone.id}-${floor}-outline`,
          position: [cx, y + 0.26, cz],
          size: [zone.width * CELL + 0.06, 0.28, zone.height * CELL + 0.06],
          color: baseColor,
          wire: true,
          floor,
          entity,
        })
        if (zone.type === 'port') {
          // 「下方入、上方出」：底边中点绿色入口块 + 顶边中点黄色出口块（行号大 = 下方 = 世界 -z）
          const padW = Math.min(zone.width * CELL * 0.8, 1.6)
          items.push({
            kind: 'box',
            key: `zone-${zone.id}-${floor}-port-in`,
            position: [cx, y + 0.52, cz - halfD + 0.35],
            size: [padW, 0.5, 0.5],
            color: '#34d399',
            emissive: '#059669',
            floor,
            entity,
          })
          items.push({
            kind: 'box',
            key: `zone-${zone.id}-${floor}-port-out`,
            position: [cx, y + 0.52, cz + halfD - 0.35],
            size: [padW, 0.5, 0.5],
            color: '#fbbf24',
            emissive: '#d97706',
            floor,
            entity,
          })
        }
      }
      maxY = Math.max(maxY, y + (levels > 1 ? levels * ZONE_LEVEL_HEIGHT[zone.type] : FLOOR_HEIGHT))
    }
  }

  // 节点：区域内库位不重复画；lift 成柱；其余小部件
  const nodeColors: Record<NodeType, string> = {
    storage: '#5b6b85',
    ctu_robot: '#22d3ee',
    shuttle_robot: '#c084fc',
    lift_transfer: '#f97316',
    turn_transfer: '#14b8a6',
  }
  for (const node of topology.nodes) {
    const floor = itemFloor(node)
    const y = floorIndexOf(floor) * FLOOR_HEIGHT
    const x = worldX(node.col, cols)
    const z = worldZ(node.row, rows)
    const color = nodeColors[node.type]
    const entity: SceneEntity = { kind: 'node', id: node.id, floor }
    if (node.type === 'storage') {
      // 库位节点统一画出（含区域内货架里的库位），便于查看、选中与删除
      if (nodeFootprint(topology, node) === 2) {
        // 四向车库位：2×2 库位。货架由所在 shuttle 区域统一铺出，这里只画一个可点选的库位标记
        items.push({
          kind: 'box',
          key: node.id,
          position: [x + CELL / 2, y + 0.4, z - CELL / 2],
          size: [CELL * 1.4, 0.5, CELL * 1.4],
          color,
          emissive: '#202b3c',
          floor,
          entity,
        })
      } else {
        items.push({
          kind: 'box',
          key: node.id,
          position: [x, y + 0.34, z],
          size: [CELL * 0.7, 0.6, CELL * 0.7],
          color,
          emissive: '#202b3c',
          floor,
          entity,
        })
      }
      continue
    }
    if (node.type === 'shuttle_robot') {
      // 四向车：2×2 占位（与四向车库位同口径），在巷道内移动
      const w = CELL * 2 * 0.88
      items.push({
        kind: 'box',
        key: node.id,
        position: [x + CELL / 2, y + 0.42, z - CELL / 2],
        size: [w, 0.8, w],
        color,
        emissive: '#6d28d9',
        floor,
        entity,
      })
      continue
    }
    if (node.type === 'ctu_robot') {
      // CTU 车：5 层载货位，每层高度与 CTU 库货架层一致（ZONE_LEVEL_HEIGHT.ctu = 0.5），
      // 做成 4 根立柱 + 5 块搁板的小货架，5 层共 2.5 高
      const levelHeight = ZONE_LEVEL_HEIGHT.ctu
      const w = CELL * 0.7
      const d = CELL * 0.7
      const halfW = w / 2
      const halfD = d / 2
      const baseH = 0.12
      const baseY = y + 0.08
      const shelfH = 0.08
      const baseTop = baseY + baseH / 2
      const rackHeight = CTU_ROBOT_LEVELS * levelHeight

      // 底座（车辆底盘）
      items.push({
        kind: 'box',
        key: node.id,
        position: [x, baseY, z],
        size: [w, baseH, d],
        color,
        emissive: '#0e7490',
        floor,
        entity,
      })

      // 4 根立柱（底座顶 → 5 层顶）
      const postCenterY = baseTop + rackHeight / 2
      const postOffsets: [number, number][] = [
        [-halfW + 0.08, -halfD + 0.08],
        [halfW - 0.08, -halfD + 0.08],
        [-halfW + 0.08, halfD - 0.08],
        [halfW - 0.08, halfD - 0.08],
      ]
      postOffsets.forEach(([px, pz], i) => {
        items.push({
          kind: 'box',
          key: `${node.id}-post-${i}`,
          position: [x + px, postCenterY, z + pz],
          size: [0.07, rackHeight, 0.07],
          color,
          emissive: '#0e7490',
          floor,
          entity,
        })
      })

      // 5 块搁板：每层底部一块，层距 = CTU 库货架层高
      for (let i = 0; i < CTU_ROBOT_LEVELS; i += 1) {
        items.push({
          kind: 'box',
          key: `${node.id}-level-${i}`,
          position: [x, baseTop + i * levelHeight + shelfH / 2, z],
          size: [w, shelfH, d],
          color,
          emissive: '#0e7490',
          floor,
          entity,
        })
      }
      continue
    }
    if (node.type === 'lift_transfer' || node.type === 'turn_transfer') {
      // 输送线设备：替换该段带面，渲染在与带面同一层高（单层 0.42 / 双层 0.84），不叠在带面之上
      const under = cellAt.get(`${floor}:${node.row}:${node.col}`)
      const onConveyor = under?.type === 'conveyor'
      const levels = onConveyor ? Math.max(1, under.levels ?? 1) : 1
      const baseH = node.type === 'lift_transfer' ? 0.9 : 0.35
      const w = CELL * 2 * 0.96
      if (onConveyor) {
        const span = (levels - 1) * 0.42
        items.push({
          kind: 'box',
          key: node.id,
          position: [x + CELL / 2, y + 0.42 + span / 2, z - CELL / 2],
          size: [w, baseH + span, w],
          color,
          floor,
          entity,
        })
      } else {
        items.push({
          kind: 'box',
          key: node.id,
          position: [x + CELL / 2, y + baseH / 2, z - CELL / 2],
          size: [w, baseH, w],
          color,
          floor,
          entity,
        })
      }
      continue
    }
  }

  // 仿真工作台继续沿用完整场景；编辑器可直接调用 buildGoodsSceneBoxes 独立缓存商品层。
  if (goods?.length) items.push(...buildGoodsSceneBoxes(topology, goods, floors))

  // 摄像机与取景
  const totalW = width
  const totalD = depth
  const span = Math.max(totalW, totalD, 4)
  const camY = Math.max(maxY + 4, span * 0.72)
  const camX = span * 0.95
  const camZ = span * 0.95
  const midY = maxY / 2

  const camera: SceneCamera = {
    position: [camX, camY, camZ],
    target: [0, midY, 0],
  }

  return { floors, cols, rows, maxY, items, camera }
}
