import { describe, expect, it } from 'vitest'
import type { ConveyorDirection, TopologyCell, TopologyNode, TopologyPayload } from '../types'
import { buildPickingArmConveyorPath, validateServerPickingArmPath } from './pickingArmConveyor'

function conveyor(
  row: number,
  col: number,
  direction: ConveyorDirection = 'right',
  levels = 2,
): TopologyCell {
  return {
    id: `cell-r${row}-c${col}`,
    floor: '1F',
    row,
    col,
    type: 'conveyor',
    cost: 1,
    walkable: true,
    conveyorDirection: direction,
    levels,
    conveyorGround: false,
  }
}

function topology(cells: TopologyCell[], nodes: TopologyNode[] = []): TopologyPayload {
  return {
    id: 'topology-test',
    name: 'test',
    meta: { version: '1', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
    grid: { rows: 20, cols: 20, cellSize: 32, originX: 0, originY: 0 },
    zones: [],
    cells,
    nodes,
  }
}

describe('strict picking-arm conveyor path', () => {
  it('moves from c50 to c51 and follows the complete valid right-running sequence', () => {
    const cells = [50, 51, 52, 53].map((col) => conveyor(6, col))
    const path = buildPickingArmConveyorPath(topology(cells), 'cell-r6-c50', 2)
    expect(path.status).toBe('PASS')
    expect(path.steps.map((step) => step.cellId)).toEqual([
      'cell-r6-c50', 'cell-r6-c51', 'cell-r6-c52', 'cell-r6-c53',
    ])
    expect(path.steps.every((step) => step.direction === 'right')).toBe(true)
  })

  it('stops before a missing conveyor cell instead of crossing the gap', () => {
    const path = buildPickingArmConveyorPath(
      topology([conveyor(6, 50), conveyor(6, 51), conveyor(6, 53)]),
      'cell-r6-c50',
      2,
    )
    expect(path.steps.map((step) => step.cellId)).toEqual(['cell-r6-c50', 'cell-r6-c51'])
    expect(path.endpointId).toBe('cell-r6-c51')
    expect(path.stopCode).toBe('MISSING_NEXT_CELL')
  })

  it('follows a device-backed orthogonal turn without a diagonal segment', () => {
    const cells = [
      conveyor(6, 5, 'right'),
      conveyor(6, 6, 'down'),
      conveyor(7, 6, 'down'),
    ]
    const turn: TopologyNode = {
      id: 'turn-1', type: 'turn_transfer', floor: '1F', row: 6, col: 6,
      capacity: 1, label: 'turn',
    }
    const path = buildPickingArmConveyorPath(topology(cells, [turn]), 'cell-r6-c5', 2)
    expect(path.steps.map((step) => [step.position.row, step.position.col])).toEqual([
      [6, 5], [6, 6], [7, 6],
    ])
    for (const [from, to] of path.steps.slice(0, -1).map((step, index) => [step, path.steps[index + 1]] as const)) {
      expect(Math.abs(from.position.row - to.position.row) + Math.abs(from.position.col - to.position.col)).toBe(1)
    }
  })

  it('blocks a direction change without a turn-transfer device', () => {
    const path = buildPickingArmConveyorPath(
      topology([conveyor(6, 5, 'right'), conveyor(6, 6, 'down'), conveyor(7, 6, 'down')]),
      'cell-r6-c5',
      2,
    )
    expect(path.status).toBe('BLOCKED')
    expect(path.stopCode).toBe('TURN_DEVICE_MISSING')
    expect(path.endpointId).toBe('cell-r6-c5')
  })

  it('does not cross to a cell that lacks the required physical level', () => {
    const path = buildPickingArmConveyorPath(
      topology([conveyor(6, 5, 'right', 2), conveyor(6, 6, 'right', 1)]),
      'cell-r6-c5',
      2,
    )
    expect(path.status).toBe('BLOCKED')
    expect(path.stopCode).toBe('LEVEL_MISMATCH')
    expect(path.steps.map((step) => step.cellId)).toEqual(['cell-r6-c5'])
  })

  it('accepts an orthogonal preview transfer to the target line only with a real turn device', () => {
    const cells = [
      conveyor(6, 5), conveyor(6, 6), conveyor(5, 6), conveyor(4, 6), conveyor(4, 7),
    ]
    const turn: TopologyNode = {
      id: 'turn-1', type: 'turn_transfer', floor: '1F', row: 6, col: 6,
      capacity: 1, label: 'turn',
    }
    const steps = cells.map((cell, index) => ({
      cellId: cell.id,
      position: { floor: '1F', row: cell.row, col: cell.col, level: 2 },
      direction: cell.conveyorDirection ?? 'right',
      beltHeight: 3,
      deviceIds: cell.id === 'cell-r6-c6' ? ['turn-1'] : [],
      transitionToNext: index === 1 || index === 2
        ? 'device_transfer' as const
        : index === cells.length - 1
          ? 'endpoint' as const
          : 'conveyor' as const,
      transferDeviceId: index === 1 || index === 2 ? 'turn-1' : null,
    }))
    const result = validateServerPickingArmPath(topology(cells, [turn]), 'cell-r6-c5', 2, steps)
    expect(result.status).toBe('PASS')
    expect(result.steps.map((step) => step.cellId)).toEqual([
      'cell-r6-c5', 'cell-r6-c6', 'cell-r5-c6', 'cell-r4-c6', 'cell-r4-c7',
    ])
  })

  it('follows the ARM-3 preview down to turn_transfer-15 then right without a diagonal', () => {
    const cells = [
      conveyor(4, 6), conveyor(5, 6), conveyor(6, 6),
      conveyor(6, 7), conveyor(6, 8),
    ]
    const turn: TopologyNode = {
      id: 'turn_transfer-15', type: 'turn_transfer', floor: '1F', row: 6, col: 6,
      capacity: 1, label: 'turn',
    }
    const steps = cells.map((cell, index) => ({
      cellId: cell.id,
      position: { floor: '1F', row: cell.row, col: cell.col, level: 2 },
      direction: cell.conveyorDirection ?? 'right',
      beltHeight: 3,
      deviceIds: cell.row === 6 && cell.col === 6 ? ['turn_transfer-15'] : [],
      transitionToNext: index < 2
        ? 'device_transfer' as const
        : index === cells.length - 1
          ? 'endpoint' as const
          : 'conveyor' as const,
      transferDeviceId: index < 2 ? 'turn_transfer-15' : null,
    }))
    const result = validateServerPickingArmPath(
      topology(cells, [turn]),
      'cell-r4-c6',
      2,
      steps,
    )
    expect(result.status).toBe('PASS')
    expect(result.steps.map((step) => [step.position.row, step.position.col])).toEqual([
      [4, 6], [5, 6], [6, 6], [6, 7], [6, 8],
    ])
  })

  it('refuses the vertical-to-horizontal preview when the transfer device is absent', () => {
    const cells = [conveyor(4, 6), conveyor(5, 6), conveyor(6, 6), conveyor(6, 7)]
    const steps = cells.map((cell, index) => ({
      cellId: cell.id,
      position: { floor: '1F', row: cell.row, col: cell.col, level: 2 },
      direction: 'right' as const,
      beltHeight: 3,
      deviceIds: [],
      transitionToNext: index < 2 ? 'device_transfer' as const : 'conveyor' as const,
      transferDeviceId: index < 2 ? 'turn_transfer-15' : null,
    }))
    expect(validateServerPickingArmPath(
      topology(cells), 'cell-r4-c6', 2, steps,
    ).stopCode).toBe('TURN_DEVICE_MISSING')
  })
})
