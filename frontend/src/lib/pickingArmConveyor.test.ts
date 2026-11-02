import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type {
  ConveyorDirection,
  ConveyorSide,
  PickingArmConveyorStep,
  RightWarehouseMergePoint,
  TopologyCell,
  TopologyNode,
  TopologyPayload,
} from '../types'
import { buildPickingArmConveyorPath, validateServerPickingArmPath } from './pickingArmConveyor'

function realTopology(): TopologyPayload {
  let directory = resolve(process.cwd())
  const checked: string[] = []
  while (true) {
    const candidate = join(directory, 'data', 'topology_666.json')
    checked.push(candidate)
    if (existsSync(candidate)) {
      return JSON.parse(readFileSync(candidate, 'utf8')) as TopologyPayload
    }
    const parent = dirname(directory)
    if (parent === directory) break
    directory = parent
  }
  throw new Error(`Real topology file was not found. Checked:\n${checked.join('\n')}`)
}

const rightMergePoint: RightWarehouseMergePoint = {
  mergePointId: 'right-order-merge-r108-c68',
  topologyNodeId: 'lift_transfer-09',
  row: 108,
  col: 68,
  level: 2,
  capacity: 1,
  occupiedCargoIds: [],
  acceptedCargoIds: [],
  status: 'available',
  acceptedWarehouse: 'right',
  cargoOrderIds: {},
}

function conveyor(
  row: number,
  col: number,
  direction: ConveyorDirection = 'right',
  levels = 2,
): TopologyCell {
  const outputSide: Record<ConveyorDirection, ConveyorSide> = {
    right: 'east', left: 'west', up: 'north', down: 'south',
  }
  const inputSide: Record<ConveyorSide, ConveyorSide> = {
    east: 'west', west: 'east', north: 'south', south: 'north',
  }
  return {
    id: `cell-r${row}-c${col}`,
    floor: '1F',
    row,
    col,
    type: 'conveyor',
    cost: 1,
    walkable: true,
    conveyorDirection: direction,
    conveyorDecks: {
      lower: { enabled: true, inputSide: inputSide[outputSide[direction]], outputSide: outputSide[direction], role: 'outbound' },
      upper: { enabled: true, inputSide: inputSide[outputSide[direction]], outputSide: outputSide[direction], role: 'outbound' },
    },
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

function step(cell: TopologyCell, transitionToNext: PickingArmConveyorStep['transitionToNext']): PickingArmConveyorStep {
  return {
    cellId: cell.id,
    position: { floor: '1F', row: cell.row, col: cell.col, level: 2 },
    direction: cell.conveyorDirection ?? 'right',
    beltHeight: 3,
    deviceIds: [],
    transitionToNext,
    directionSource: 'topology',
  }
}

describe('strict picking-arm conveyor path', () => {
  it('follows the four real upper-deck segments to the right merge point', () => {
    const path = buildPickingArmConveyorPath(
      realTopology(), 'cell-r86-c50', 2, rightMergePoint,
    )
    expect(path.status).toBe('PASS')
    expect(path.steps.map((item) => [item.position.row, item.position.col])).toEqual([
      ...Array.from({ length: 11 }, (_, index) => [86, 50 + index]),
      ...Array.from({ length: 22 }, (_, index) => [87 + index, 60]),
      ...Array.from({ length: 8 }, (_, index) => [108, 61 + index]),
    ])
    expect(path.steps.find((item) => item.cellId === 'cell-r86-c60')).toMatchObject({
      direction: 'down', transitionToNext: 'device_transfer', transferDeviceId: 'turn_transfer-15',
    })
    expect(path.steps.find((item) => item.cellId === 'cell-r108-c60')).toMatchObject({
      direction: 'right', transitionToNext: 'device_transfer', transferDeviceId: 'turn_transfer-13',
    })
    expect(path.steps.at(-1)).toMatchObject({
      cellId: 'cell-r108-c68', transitionToNext: 'endpoint', deviceIds: ['lift_transfer-09'],
    })
    expect(validateServerPickingArmPath(
      realTopology(), 'cell-r86-c50', 2, path.steps, rightMergePoint,
    )).toMatchObject({ status: 'PASS', endpointId: 'cell-r108-c68' })
  })

  it('blocks the required merge route when a real intermediate node is missing', () => {
    const actual = realTopology()
    const broken = {
      ...actual,
      cells: actual.cells.filter((cell) => cell.id !== 'cell-r90-c60'),
    }
    expect(buildPickingArmConveyorPath(
      broken, 'cell-r86-c50', 2, rightMergePoint,
    )).toMatchObject({ status: 'BLOCKED', stopCode: 'MISSING_NEXT_CELL' })
  })

  it('follows every real right-running conveyor cell to the endpoint', () => {
    const cells = [50, 51, 52, 53].map((col) => conveyor(6, col))
    const path = buildPickingArmConveyorPath(topology(cells), 'cell-r6-c50', 2)
    expect(path.status).toBe('PASS')
    expect(path.steps.map((item) => item.cellId)).toEqual([
      'cell-r6-c50', 'cell-r6-c51', 'cell-r6-c52', 'cell-r6-c53',
    ])
    expect(path.steps.at(-1)?.transitionToNext).toBe('endpoint')
  })

  it('stops explicitly before a missing conveyor instead of crossing a gap', () => {
    const path = buildPickingArmConveyorPath(
      topology([conveyor(6, 50), conveyor(6, 51), conveyor(6, 53)]),
      'cell-r6-c50',
      2,
    )
    expect(path.steps.map((item) => item.cellId)).toEqual(['cell-r6-c50', 'cell-r6-c51'])
    expect(path.endpointId).toBe('cell-r6-c51')
    expect(path.stopCode).toBe('MISSING_NEXT_CELL')
  })

  it('blocks a direction change without a real turn-transfer device', () => {
    const path = buildPickingArmConveyorPath(
      topology([conveyor(6, 5, 'right'), conveyor(6, 6, 'down'), conveyor(7, 6, 'down')]),
      'cell-r6-c5',
      2,
    )
    expect(path).toMatchObject({ status: 'BLOCKED', stopCode: 'TURN_DEVICE_MISSING' })
  })

  it('allows an orthogonal turn only at a real turn-transfer node', () => {
    const cells = [
      conveyor(6, 5, 'right'),
      conveyor(6, 6, 'down'),
      conveyor(7, 6, 'down'),
    ]
    const turn: TopologyNode = {
      id: 'turn-1', type: 'turn_transfer', floor: '1F', row: 6, col: 6,
      capacity: 1, label: 'turn', transferDeck: 'upper',
      transferInputSide: 'west', transferOutputSide: 'south',
    }
    const path = buildPickingArmConveyorPath(topology(cells, [turn]), 'cell-r6-c5', 2)
    expect(path.status).toBe('PASS')
    expect(path.steps.map((item) => [item.position.row, item.position.col])).toEqual([
      [6, 5], [6, 6], [7, 6],
    ])
    expect(path.steps[1]).toMatchObject({ transitionToNext: 'device_transfer', transferDeviceId: 'turn-1' })
  })

  it('blocks a route that changes physical conveyor level', () => {
    const path = buildPickingArmConveyorPath(
      topology([conveyor(6, 5, 'right', 2), conveyor(6, 6, 'right', 1)]),
      'cell-r6-c5',
      2,
    )
    expect(path).toMatchObject({ status: 'BLOCKED', stopCode: 'LEVEL_MISMATCH' })
  })

  it('rejects a server path that moves opposite to topology direction', () => {
    const cells = [conveyor(6, 5), conveyor(6, 6)]
    const steps = [step(cells[0], 'conveyor'), step(cells[1], 'endpoint')]
    steps[0] = { ...steps[0], direction: 'left' }
    expect(validateServerPickingArmPath(
      topology(cells), 'cell-r6-c5', 2, steps,
    )).toMatchObject({ status: 'BLOCKED', stopCode: 'DIRECTION_MISMATCH' })
  })

  it('rejects a non-adjacent server edge even when both cells are real', () => {
    const cells = [conveyor(6, 5), conveyor(6, 7)]
    const steps = [step(cells[0], 'conveyor'), step(cells[1], 'endpoint')]
    expect(validateServerPickingArmPath(
      topology(cells), 'cell-r6-c5', 2, steps,
    )).toMatchObject({ status: 'BLOCKED', stopCode: 'DIRECTION_MISMATCH' })
  })
})
