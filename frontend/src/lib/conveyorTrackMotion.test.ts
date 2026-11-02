import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { SimulationTestRunState } from '../types'
import {
  boundedConveyorCatchUp,
  collectConfirmedConveyorWaypoints,
  conveyorSegmentKind,
  conveyorYawForSegment,
  groundConveyorWaypoints,
  upperConveyorWaypoints,
  validateConveyorWaypointPath,
  type ConveyorVisualWaypoint,
} from './conveyorTrackMotion'

describe('authoritative Conveyor waypoint motion', () => {
  it('builds the Ground route at the authoritative 0.6 belt height', () => {
    const conveyor = {
      path: ['1F:92:27:lower', '1F:92:29:lower', '1F:92:31:lower'],
      occupiedByBlock: {}, capacityPerBlock: 1 as const,
      beltHeight: 0.6, gridRows: 216, gridCols: 129,
    }
    const path = groundConveyorWaypoints(conveyor)
    expect(path.map((item) => item.position[1])).toEqual([0.6, 0.6, 0.6])
    expect(validateConveyorWaypointPath(path)).toEqual({ status: 'PASS' })
    expect(path[1].position[0] - path[0].position[0]).toBe(2)
  })

  it('fills every confirmed intermediate node after a websocket jump', () => {
    const path = [0, 1, 2, 3].map((col) => ({
      nodeId: `cell-${col}`, position: [col, 3, 0] as [number, number, number],
    }))
    expect(collectConfirmedConveyorWaypoints(path, 0, 3).map((item) => item.nodeId))
      .toEqual(['cell-1', 'cell-2', 'cell-3'])
  })

  it('rejects corner cutting but accepts two cardinal edges sharing a turn node', () => {
    const legal = [
      { nodeId: 'a', position: [0, 3, 0] as [number, number, number] },
      { nodeId: 'turn', position: [2, 3, 0] as [number, number, number] },
      { nodeId: 'b', position: [2, 3, -2] as [number, number, number] },
    ]
    expect(validateConveyorWaypointPath(legal)).toEqual({ status: 'PASS' })
    expect(conveyorSegmentKind(legal[0].position, legal[1].position)).toBe('column')
    expect(conveyorSegmentKind(legal[1].position, legal[2].position)).toBe('row')
    const diagonal = [legal[0], legal[2]]
    expect(validateConveyorWaypointPath(diagonal)).toMatchObject({
      status: 'BLOCKED', stopCode: 'invalid_diagonal',
    })
  })

  it('keeps Upper coordinates and cardinal yaw from the transport-layout path', () => {
    const path = upperConveyorWaypoints([
      { nodeId: 'cell-r86-c50', row: 86, col: 50, level: 2, worldX: 0, worldY: 3, worldZ: 0, deviceIds: [] },
      { nodeId: 'cell-r86-c51', row: 86, col: 51, level: 2, worldX: 1, worldY: 3, worldZ: 0, deviceIds: [] },
    ] as SimulationTestRunState['mergeConveyor']['path'])
    expect(path.every((item) => item.position[1] === 3)).toBe(true)
    expect(conveyorYawForSegment(0, path[0].position, path[1].position)).toBeCloseTo(Math.PI / 2)
  })

  it('validates the complete real Upper route through both transfer nodes', () => {
    const root = resolve(process.cwd(), '..')
    const topology = JSON.parse(readFileSync(resolve(root, 'data', 'topology_666.json'), 'utf8'))
    const plan = JSON.parse(readFileSync(
      resolve(root, 'data', 'transport_layout_plans', 'topology-6ceb4920.arm-preview.json'),
      'utf8',
    ))
    const waypoints = plan.pickingArmPreview.previewRoute.requiredWaypoints as Array<{ row: number; col: number }>
    const coordinates: Array<{ row: number; col: number }> = [waypoints[0]]
    for (let index = 1; index < waypoints.length; index += 1) {
      const from = coordinates.at(-1)!
      const to = waypoints[index]
      const rowStep = Math.sign(to.row - from.row)
      const colStep = Math.sign(to.col - from.col)
      const distance = Math.max(Math.abs(to.row - from.row), Math.abs(to.col - from.col))
      for (let step = 1; step <= distance; step += 1) {
        coordinates.push({ row: from.row + rowStep * step, col: from.col + colStep * step })
      }
    }
    const path = coordinates.map(({ row, col }) => ({
      nodeId: `cell-r${row}-c${col}`,
      position: [
        col + 1 - topology.grid.cols / 2,
        3,
        topology.grid.rows / 2 - (row + 1),
      ] as [number, number, number],
    }))
    expect(validateConveyorWaypointPath(path)).toEqual({ status: 'PASS' })
    expect(path.every((item: ConveyorVisualWaypoint) => item.position[1] === 3)).toBe(true)
    expect(path.some((item: ConveyorVisualWaypoint) => item.nodeId === 'cell-r86-c60')).toBe(true)
    expect(path.some((item: ConveyorVisualWaypoint) => item.nodeId === 'cell-r108-c60')).toBe(true)
    expect(path.at(-1)?.nodeId).toBe('cell-r108-c68')
  })

  it('uses bounded catch-up without dropping or teleporting waypoints', () => {
    expect(boundedConveyorCatchUp(0)).toBe(1)
    expect(boundedConveyorCatchUp(3)).toBeCloseTo(1.08)
    expect(boundedConveyorCatchUp(100)).toBe(1.5)
  })
})
