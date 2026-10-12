import { describe, it, expect } from 'vitest'
import { emptyTopology } from '../data/defaults'
import {
  conveyorBlockAnchor,
  conveyorRunFromCell,
  createZone,
  eraseAt,
  fillNodes,
  paintCell,
  placeNode,
  slotNumberFor,
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
    const station = placeNode(base(), 'station', 1, 1)!
    expect(slotNumberFor(station.topology, station.topology.nodes[0])).toBeNull()
    const storage = placeNode(base(), 'storage', 2, 2)!
    expect(slotNumberFor(storage.topology, storage.topology.nodes[0])).toBeNull()
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

  it('非偶坐标归一到块锚点', () => {
    const t = paintCell(base(), 'conveyor', 5, 5).topology
    const cells = t.cells
      .filter((c) => c.type === 'conveyor')
      .map((c) => `${c.row},${c.col}`)
      .sort()
    expect(cells).toEqual(['4,4', '4,5', '5,4', '5,5'])
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
