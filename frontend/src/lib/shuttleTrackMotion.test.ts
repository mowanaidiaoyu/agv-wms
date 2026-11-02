import { describe, expect, it } from 'vitest'
import type { SimulationTestShuttleFrame } from '../types'
import {
  classifyShuttleTrackSegment,
  collectConfirmedShuttleWaypoints,
  headingAtShuttleFrame,
  isLegalShuttleTrackSegment,
  shuttleDirectionLabel,
  shuttleYawForSegment,
} from './shuttleTrackMotion'

function frame(index: number, row: number, col: number, level = 1): SimulationTestShuttleFrame {
  return {
    cellId: `cell-r${row}-c${col}`,
    nodeId: `L${Math.round(level)}-r${row}-c${col}`,
    index,
    level,
    stage: level % 1 ? 'lift_moving' : 'track',
    position: {
      nodeId: `L${Math.round(level)}-r${row}-c${col}`,
      row,
      col,
      level,
      worldX: col + 1 - 129 / 2,
      worldY: 0.6 + (level - 1) * 1.4,
      worldZ: 216 / 2 - (row + 1),
    },
  }
}

describe('ShuttleGraph-constrained visual motion', () => {
  it('accepts 2x2 row/column edges and rejects a diagonal shortcut', () => {
    expect(classifyShuttleTrackSegment([0, 0.6, 0], [0, 0.6, -2])).toBe('row')
    expect(classifyShuttleTrackSegment([0, 0.6, 0], [2, 0.6, 0])).toBe('column')
    const diagonal = classifyShuttleTrackSegment([0, 0.6, 0], [2, 0.6, -2])
    expect(diagonal).toBe('invalid_diagonal')
    expect(isLegalShuttleTrackSegment(diagonal)).toBe(false)
  })

  it('allows Lift movement only when horizontal coordinates remain fixed', () => {
    expect(classifyShuttleTrackSegment([1, 2, 3], [1, 0.6, 3])).toBe('lift')
    expect(classifyShuttleTrackSegment([1, 2, 3], [2, 0.6, 3])).toBe('invalid_lift_drift')
  })

  it('uses cardinal yaw and a 90-degree turn only at the shared node', () => {
    const south = shuttleYawForSegment(0, [0, 0.6, 0], [0, 0.6, -2])
    const east = shuttleYawForSegment(south, [0, 0.6, -2], [2, 0.6, -2])
    expect(south).toBeCloseTo(Math.PI)
    expect(east).toBeCloseTo(Math.PI / 2)
    expect(Math.abs(east - south)).toBeCloseTo(Math.PI / 2)
    expect(shuttleDirectionLabel('row', south)).toBe('+row')
    expect(shuttleDirectionLabel('column', east)).toBe('+col')
  })

  it('fills skipped websocket snapshots with every confirmed intermediate frame', () => {
    const frames = [frame(0, 100, 39), frame(1, 102, 39), frame(2, 104, 39), frame(3, 104, 41)]
    const waypoints = collectConfirmedShuttleWaypoints(frames, 0, 3)
    expect(waypoints.map((item) => item.nodeId)).toEqual([
      'L1-r102-c39', 'L1-r104-c39', 'L1-r104-c41',
    ])
    expect(waypoints.map((item) => classifyShuttleTrackSegment(
      item === waypoints[0] ? [frames[0].position.worldX, frames[0].position.worldY, frames[0].position.worldZ] : waypoints[waypoints.indexOf(item) - 1].position,
      item.position,
    ))).toEqual(['row', 'row', 'column'])
    expect(collectConfirmedShuttleWaypoints(frames, -1, 0).map((item) => item.nodeId))
      .toEqual(['L1-r100-c39'])
  })

  it('restores the latest cardinal heading instead of replaying from frame zero', () => {
    const frames = [frame(0, 100, 39), frame(1, 102, 39), frame(2, 104, 39), frame(3, 104, 41)]
    const yaw = headingAtShuttleFrame(frames, 3)
    expect(yaw).toBeCloseTo(Math.PI / 2)
  })
})
