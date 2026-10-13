import { describe, expect, it } from 'vitest'
import type { TopologyPayload } from '../../types'
import { worldX, worldZ } from '../../lib/topology3d'
import { findRoadCellAtWorldPoint, isClickGesture } from './roadSelection'

const topology: TopologyPayload = {
  id: 'selection-grid',
  name: 'Selection Grid',
  meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 4, cols: 4, cellSize: 32, originX: 0, originY: 0 },
  zones: [],
  cells: [],
  nodes: [],
  resources: [],
}

const roadCells = [
  { id: 'road', floor: '1F', row: 1, col: 2, type: 'road', component_id: 'road-component-1' },
]

describe('road cell selection', () => {
  it('maps a world point only to a verified road cell', () => {
    expect(findRoadCellAtWorldPoint(worldX(2, 4), worldZ(1, 4), topology, roadCells, '1F')?.id).toBe('road')
    expect(findRoadCellAtWorldPoint(worldX(1, 4), worldZ(1, 4), topology, roadCells, '1F')).toBeNull()
  })

  it('does not treat an OrbitControls drag as a map click', () => {
    expect(isClickGesture({ x: 10, y: 10 }, { x: 13, y: 14 })).toBe(true)
    expect(isClickGesture({ x: 10, y: 10 }, { x: 30, y: 25 })).toBe(false)
  })
})
