import { describe, it, expect } from 'vitest'
import { emptyTopology } from '../data/topologyDefaults'
import type { TopologyPayload } from '../types'
import {
  conveyorBlockAnchor,
  conveyorBlockCellIds,
  conveyorBlocks,
  conveyorDecksOf,
  conveyorRunFromCell,
  configureConveyorRun,
  createZone,
  eraseAt,
  fillNodes,
  moveConveyorRun,
  paintCell,
  paintStroke,
  placeNode,
  removeZone,
  setShuttleSlotLift,
  setShuttleSlotRemoved,
  shuttleSlotLift,
  shuttleSlotRemoved,
  slotNumberFor,
  swapConveyorDecks,
  updateConveyorBlockDeck,
  updateZoneGeometry,
  validateConveyorTopology,
} from './topology'

function base() {
  return emptyTopology()
}

describe('slotNumberFor', () => {
  it('shuttle 库位按 2×2 槽编号', () => {
    let t = createZone(base(), 'shuttle', { row0: 0, col0: 0, row1: 3, col1: 3 })
    t = fillNodes(t, 'storage', { row0: 0, col0: 0, row1: 3, col1: 3 }).topology
    const numbers = t.nodes.filter((n) => n.type === 'storage').map((n) => slotNumberFor(t, n))
    expect(numbers).toEqual(['A-01-01', 'A-01-02', 'A-02-01', 'A-02-02'])
  })

  it('ctu 库位按 1×1 编号', () => {
    let t = createZone(base(), 'ctu', { row0: 2, col0: 2, row1: 3, col1: 3 })
    t = fillNodes(t, 'storage', { row0: 2, col0: 2, row1: 3, col1: 3 }).topology
    const numbers = t.nodes.filter((n) => n.type === 'storage').map((n) => slotNumberFor(t, n))
    expect(numbers).toEqual(['A-01-01', 'A-01-02', 'A-02-01', 'A-02-02'])
  })

  it('非库位或无区域返回 null', () => {
    const robot = placeNode(base(), 'ctu_robot', 1, 1)!
    expect(slotNumberFor(robot.topology, robot.topology.nodes[0])).toBeNull()
    const storage = placeNode(base(), 'storage', 2, 2)!
    expect(slotNumberFor(storage.topology, storage.topology.nodes[0])).toBeNull()
  })
})

describe('removeZone', () => {
  it('删除 CTU 区域时一并移除该区域自动生成的库位节点', () => {
    let t = createZone(base(), 'ctu', { row0: 0, col0: 0, row1: 1, col1: 1 })
    const zoneId = t.zones[0].id
    expect(t.nodes.filter((node) => node.type === 'storage' && node.zoneId === zoneId)).toHaveLength(4)

    t = removeZone(t, zoneId)
    expect(t.zones).toHaveLength(0)
    expect(t.nodes.filter((node) => node.type === 'storage')).toHaveLength(0)
  })

  it('删除区域时按位置清掉旧数据里没有 zoneId 的库位', () => {
    let t = createZone(base(), 'ctu', { row0: 0, col0: 0, row1: 1, col1: 1 })
    const zoneId = t.zones[0].id
    // 模拟旧数据：库位节点没有 zoneId，但位置在货架区域内
    t = { ...t, nodes: t.nodes.map((node) => ({ ...node, zoneId: null })) }

    t = removeZone(t, zoneId)
    expect(t.zones).toHaveLength(0)
    expect(t.nodes.filter((node) => node.type === 'storage')).toHaveLength(0)
  })

  it('删除区域保留其它类型节点，只清空其 zoneId', () => {
    let t = createZone(base(), 'ctu', { row0: 0, col0: 0, row1: 1, col1: 1 })
    const zoneId = t.zones[0].id
    const placed = placeNode(t, 'ctu_robot', 0, 0)!
    t = {
      ...placed.topology,
      nodes: placed.topology.nodes.map((node) => (node.type === 'ctu_robot' ? { ...node, zoneId } : node)),
    }

    t = removeZone(t, zoneId)
    const robot = t.nodes.find((node) => node.type === 'ctu_robot')
    expect(robot).toBeTruthy()
    expect(robot?.zoneId).toBeNull()
  })
})

describe('conveyorRunFromCell', () => {
  it('单个 2×2 块返回 4 格连通段', () => {
    const t = paintCell(base(), 'conveyor', 0, 0).topology
    expect(conveyorRunFromCell(t, 'cell-r0-c0')).toHaveLength(4)
  })

  it('相邻两块连通为同一段（8 格）', () => {
    let t = paintCell(base(), 'conveyor', 0, 0).topology
    t = paintCell(t, 'conveyor', 0, 2).topology
    expect(conveyorRunFromCell(t, 'cell-r0-c0')).toHaveLength(8)
  })

  it('非输送带格返回空', () => {
    expect(conveyorRunFromCell(base(), 'cell-r0-c0')).toEqual([])
  })
})

describe('conveyor 2×2 占位', () => {
  it('刷一格铺出 2×2 四格', () => {
    const t = paintCell(base(), 'conveyor', 4, 4).topology
    const cells = t.cells
      .filter((c) => c.type === 'conveyor')
      .map((c) => `${c.row},${c.col}`)
      .sort()
    expect(cells).toEqual(['4,4', '4,5', '5,4', '5,5'])
  })

  it('奇数起点：锚点直接落在所点格（不再归一到偶）', () => {
    const t = paintCell(base(), 'conveyor', 5, 5).topology
    const cells = t.cells
      .filter((c) => c.type === 'conveyor')
      .map((c) => `${c.row},${c.col}`)
      .sort()
    expect(cells).toEqual(['5,5', '5,6', '6,5', '6,6'])
  })

  it('删除块内任一格清掉整块', () => {
    const painted = paintCell(base(), 'conveyor', 4, 4).topology
    const erased = eraseAt(painted, 5, 5)
    expect(erased.erased).toBe('cell')
    expect(erased.topology.cells.filter((c) => c.type === 'conveyor')).toHaveLength(0)
  })

  it('conveyorBlockAnchor 向下取偶', () => {
    expect(conveyorBlockAnchor(4, 4)).toEqual({ row: 4, col: 4 })
    expect(conveyorBlockAnchor(5, 3)).toEqual({ row: 4, col: 2 })
  })
})

describe('conveyor 奇数起点', () => {
  it('重叠保护：与错位块重叠时整块拒绝且原块不变', () => {
    let t = paintCell(base(), 'conveyor', 4, 4).topology
    t = paintCell(t, 'conveyor', 4, 5).topology
    const cells = t.cells
      .filter((c) => c.type === 'conveyor')
      .map((c) => `${c.row},${c.col}`)
      .sort()
    // 只有 (4,4) 那一块被铺出，错位的 (4,5) 块因与 (4,5) 格重叠而被拒
    expect(cells).toEqual(['4,4', '4,5', '5,4', '5,5'])
  })

  it('相邻奇锚点块连成一条 8 格输送线', () => {
    let t = paintCell(base(), 'conveyor', 4, 5).topology
    t = paintCell(t, 'conveyor', 4, 7).topology
    const startId = t.cells.find((c) => c.type === 'conveyor' && c.row === 4 && c.col === 5)!.id
    expect(conveyorRunFromCell(t, startId)).toHaveLength(8)
  })

  it('conveyorBlockCellIds 对奇锚点块返回正确的 4 格', () => {
    const t = paintCell(base(), 'conveyor', 3, 5).topology
    const cell = t.cells.find((c) => c.row === 3 && c.col === 5)!
    const ids = conveyorBlockCellIds(t, cell.id)
    expect(ids).toHaveLength(4)
    const coords = ids
      .map((id) => t.cells.find((c) => c.id === id)!)
      .map((c) => `${c.row},${c.col}`)
      .sort()
    expect(coords).toEqual(['3,5', '3,6', '4,5', '4,6'])
  })

  it('moveConveyorRun 奇位移：块仍 2×2 且锚点随动', () => {
    let t = paintCell(base(), 'conveyor', 4, 5).topology
    const startId = t.cells.find((c) => c.type === 'conveyor')!.id
    const moved = moveConveyorRun(t, startId, 1, 0).topology
    const cells = moved.cells
      .filter((c) => c.type === 'conveyor')
      .map((c) => `${c.row},${c.col}`)
      .sort()
    expect(cells).toEqual(['5,5', '5,6', '6,5', '6,6'])
    // 锚点也应平移 1 行
    const anchor = moved.cells.find((c) => c.row === 5 && c.col === 5)!
    expect(anchor.conveyorAnchor).toEqual({ row: 5, col: 5 })
  })
})

describe('四向车库升降通道（电梯）', () => {
  it('设为升降通道后记录 slotLifts 且 4 格铺 road', () => {
    let t = createZone(base(), 'shuttle', { row0: 0, col0: 0, row1: 3, col1: 3 })
    const zoneId = t.zones[0].id
    t = setShuttleSlotLift(t, [{ row: 0, col: 0 }], true)
    expect(shuttleSlotLift(t, zoneId, 0, 0)).toBe(true)
    const roads = t.cells.filter((c) => c.type === 'road' && c.row < 2 && c.col < 2)
    expect(roads).toHaveLength(4)
  })

  it('恢复货架后移除标记并清掉 4 格 road', () => {
    let t = createZone(base(), 'shuttle', { row0: 0, col0: 0, row1: 3, col1: 3 })
    const zoneId = t.zones[0].id
    t = setShuttleSlotLift(t, [{ row: 0, col: 0 }], true)
    t = setShuttleSlotLift(t, [{ row: 0, col: 0 }], false)
    expect(shuttleSlotLift(t, zoneId, 0, 0)).toBe(false)
    expect(t.cells.filter((c) => c.type === 'road' && c.row < 2 && c.col < 2)).toHaveLength(0)
  })

  it('三态互斥：设为升降通道会清掉「已删除」状态', () => {
    let t = createZone(base(), 'shuttle', { row0: 0, col0: 0, row1: 3, col1: 3 })
    const zoneId = t.zones[0].id
    t = setShuttleSlotRemoved(t, [{ row: 0, col: 0 }], true)
    expect(shuttleSlotRemoved(t, zoneId, 0, 0)).toBe(true)
    t = setShuttleSlotLift(t, [{ row: 0, col: 0 }], true)
    expect(shuttleSlotLift(t, zoneId, 0, 0)).toBe(true)
    expect(shuttleSlotRemoved(t, zoneId, 0, 0)).toBe(false)
  })

  it('移动区域后升降通道标记随 "行,列" 平移', () => {
    let t = createZone(base(), 'shuttle', { row0: 0, col0: 0, row1: 3, col1: 3 })
    const zoneId = t.zones[0].id
    t = setShuttleSlotLift(t, [{ row: 0, col: 0 }], true)
    t = updateZoneGeometry(t, zoneId, { x: 2, y: 2 })
    expect(shuttleSlotLift(t, zoneId, 2, 2)).toBe(true)
    expect(shuttleSlotLift(t, zoneId, 0, 0)).toBe(false)
  })
})

describe('moveConveyorRun（拖动平移输送线）', () => {
  // 画一条 L 形输送线：3 个 2×2 块锚点 (0,0)/(0,2)/(2,2)，共 12 格连通
  function conveyorL() {
    let t = base()
    t = paintCell(t, 'conveyor', 0, 0).topology
    t = paintCell(t, 'conveyor', 0, 2).topology
    t = paintCell(t, 'conveyor', 2, 2).topology
    return t
  }
  const startIdOf = (t: TopologyPayload) => t.cells.find((c) => c.type === 'conveyor' && c.row === 0 && c.col === 0)!.id

  it('整体平移：cell 坐标整体平移且连通格数不变', () => {
    const t = conveyorL()
    const before = t.cells.filter((c) => c.type === 'conveyor').map((c) => `${c.row},${c.col}`).sort()
    const moved = moveConveyorRun(t, startIdOf(t), 2, 2).topology
    const after = moved.cells.filter((c) => c.type === 'conveyor').map((c) => `${c.row},${c.col}`).sort()
    const expected = before.map((key) => {
      const [r, c] = key.split(',').map(Number)
      return `${r + 2},${c + 2}`
    })
    expect(after).toEqual(expected)
  })

  it('目标被 blocked 占用时拒绝并保留原状', () => {
    let t = conveyorL()
    t = paintCell(t, 'blocked', 4, 4).topology
    const result = moveConveyorRun(t, startIdOf(t), 4, 4)
    expect(result.message).toBeTruthy()
    expect(result.topology.cells.filter((c) => c.type === 'conveyor')).toHaveLength(12)
  })
})

describe('双层输送线独立路由', () => {
  it('绘制转弯线路时不再根据手势自动推断模块方向', () => {
    const topology = paintStroke(base(), 'conveyor', [
      { row: 0, col: 0 },
      { row: 0, col: 2 },
      { row: 2, col: 2 },
    ])
    const blocks = conveyorBlocks(topology)

    expect(blocks).toHaveLength(3)
    expect(blocks.every((block) => block.anchorCell.conveyorDirection === 'right')).toBe(true)
    expect(blocks.every((block) => block.anchorCell.conveyorDecks == null)).toBe(true)
  })

  it('自动生成下层去程和上层反向回程，并通过连接校验', () => {
    let topology = base()
    topology = paintCell(topology, 'conveyor', 0, 0).topology
    topology = paintCell(topology, 'conveyor', 0, 2).topology
    topology = paintCell(topology, 'conveyor', 2, 2).topology
    const ids = topology.cells.filter((cell) => cell.type === 'conveyor').map((cell) => cell.id)

    topology = configureConveyorRun(topology, ids, 'round_trip')
    const blocks = conveyorBlocks(topology, ids)
    const first = blocks.find((block) => block.row === 0 && block.col === 0)!
    const corner = blocks.find((block) => block.row === 0 && block.col === 2)!
    const last = blocks.find((block) => block.row === 2 && block.col === 2)!

    expect(conveyorDecksOf(topology, first.anchorCell).lower).toMatchObject({ inputSide: 'west', outputSide: 'east' })
    expect(conveyorDecksOf(topology, corner.anchorCell).lower).toMatchObject({ inputSide: 'west', outputSide: 'south' })
    expect(conveyorDecksOf(topology, last.anchorCell).upper).toMatchObject({ inputSide: 'south', outputSide: 'north' })
    expect(validateConveyorTopology(topology).filter((issue) => issue.severity === 'error')).toEqual([])
  })

  it('旧双层方向自动迁移为上下层相反方向', () => {
    let topology = paintCell(base(), 'conveyor', 0, 0).topology
    topology.cells = topology.cells.map((cell) => ({ ...cell, levels: 2, conveyorDirection: 'down' }))
    const block = conveyorBlocks(topology)[0]
    const decks = conveyorDecksOf(topology, block.anchorCell)
    expect(decks.lower).toMatchObject({ inputSide: 'north', outputSide: 'south', role: 'outbound' })
    expect(decks.upper).toMatchObject({ inputSide: 'south', outputSide: 'north', role: 'return' })
  })

  it('可以交换上下层的用途和走向', () => {
    let topology = paintCell(base(), 'conveyor', 0, 0).topology
    const ids = topology.cells.filter((cell) => cell.type === 'conveyor').map((cell) => cell.id)
    topology = configureConveyorRun(topology, ids, 'round_trip')
    const before = conveyorDecksOf(topology, conveyorBlocks(topology)[0].anchorCell)
    topology = swapConveyorDecks(topology, ids)
    const block = conveyorBlocks(topology)[0]
    const after = conveyorDecksOf(topology, block.anchorCell)

    expect(after.lower).toEqual(before.upper)
    expect(after.upper).toEqual(before.lower)
    expect(block.anchorCell.conveyorMode).toBe('independent')
  })

  it('校验业务区域与输送端点的固定连接侧', () => {
    let topology = base()
    topology = paintCell(topology, 'conveyor', 0, 0).topology
    topology = paintCell(topology, 'conveyor', 0, 2).topology
    const ids = topology.cells.filter((cell) => cell.type === 'conveyor').map((cell) => cell.id)
    topology = configureConveyorRun(topology, ids, 'round_trip')
    topology.zones = [{
      id: 'port-1', label: '出库口 01', type: 'port', x: 0, y: 0, width: 1, height: 1,
      conveyorBinding: { blockId: '1F:0:0', side: 'north' },
    }]

    expect(validateConveyorTopology(topology).some((issue) => issue.message.includes('下层入口侧'))).toBe(true)
    topology.zones[0].conveyorBinding = { blockId: '1F:0:0', side: 'west' }
    expect(validateConveyorTopology(topology).filter((issue) => issue.severity === 'error')).toEqual([])
  })

  it('选择已编号线路时不会吞并紧贴的另一条线路', () => {
    let topology = paintCell(base(), 'conveyor', 0, 0).topology
    topology = paintCell(topology, 'conveyor', 0, 2).topology
    topology.cells = topology.cells.map((cell) => ({
      ...cell,
      conveyorLineId: cell.col < 2 ? 'line-a' : 'line-b',
    }))
    const start = topology.cells.find((cell) => cell.row === 0 && cell.col === 0)!

    expect(conveyorRunFromCell(topology, start.id).map((cell) => cell.conveyorLineId)).toEqual([
      'line-a', 'line-a', 'line-a', 'line-a',
    ])
  })

  it('只手动配置一个模块时不把相邻旧模块误判成断路', () => {
    let topology = paintCell(base(), 'conveyor', 0, 0).topology
    topology = paintCell(topology, 'conveyor', 0, 2).topology
    const first = conveyorBlocks(topology).find((block) => block.col === 0)!
    topology = updateConveyorBlockDeck(topology, first.anchorCell.id, 'lower', {
      inputSide: 'west', outputSide: 'east',
    })

    expect(validateConveyorTopology(topology).filter((issue) => issue.severity === 'error')).toEqual([])
  })

  it('两个手动配置模块的出口和入口不匹配时才报错', () => {
    let topology = paintCell(base(), 'conveyor', 0, 0).topology
    topology = paintCell(topology, 'conveyor', 0, 2).topology
    const blocks = conveyorBlocks(topology)
    topology = updateConveyorBlockDeck(topology, blocks.find((block) => block.col === 0)!.anchorCell.id, 'lower', {
      inputSide: 'west', outputSide: 'east',
    })
    topology = updateConveyorBlockDeck(topology, blocks.find((block) => block.col === 2)!.anchorCell.id, 'lower', {
      inputSide: 'north', outputSide: 'south',
    })

    expect(validateConveyorTopology(topology).some((issue) => issue.message.includes('出口与下一块'))).toBe(true)
  })
})
