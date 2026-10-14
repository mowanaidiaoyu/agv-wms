import { describe, expect, it } from 'vitest'
import { advancePlayback } from './pathPlayback'
import {
  conflictSeekTime,
  primaryNodeConflict,
  resolveConflictPlaybackRate,
} from './conflictVisualization'
import type { MultiPathConflictEvent, PathDemoCell } from './types'

const cell: PathDemoCell = { id: 'cell-r96-c21', floor: '1F', row: 96, col: 21, type: 'road', component_id: 'road-component-1' }
const event: MultiPathConflictEvent = {
  event_id: 'node-CTU-02-CTU-01-2-cell-r96-c21',
  conflict_type: 'node',
  status: 'resolved',
  time_step: 2,
  time_seconds: 0.5,
  cell,
  from_cell: cell,
  involved_vehicle_ids: ['CTU-01', 'CTU-02'],
  priority_vehicle_id: 'CTU-01',
  priority: 1,
  yielding_vehicle_id: 'CTU-02',
  yielding_priority: 2,
  reservation_action: 'candidate_rejected',
  avoidance_action: 'reroute_and_wait',
  wait_cell: { ...cell, id: 'cell-r96-c22', col: 22 },
  wait_start_time_seconds: 0.5,
  wait_end_time_seconds: 0.75,
  wait_duration_seconds: 0.25,
  result: 'conflict_free_plan',
}

describe('scene-B conflict visualization helpers', () => {
  it('selects only a real node event and seeks one legal time step before it', () => {
    const edge = { ...event, event_id: 'edge-event', conflict_type: 'edge' as const, time_step: 1 }
    expect(primaryNodeConflict([edge, event])).toBe(event)
    expect(conflictSeekTime(event, 0.25)).toBe(0.25)
    expect(primaryNodeConflict([edge])).toBeNull()
  })

  it('uses 0.25x only around the real event and restores the preferred rate', () => {
    expect(resolveConflictPlaybackRate(2, true, event, 0.25, 0.25)).toBe(0.25)
    expect(resolveConflictPlaybackRate(2, true, event, 0.99, 0.25)).toBe(0.25)
    expect(resolveConflictPlaybackRate(2, true, event, 1, 0.25)).toBe(2)
    expect(resolveConflictPlaybackRate(0.5, false, event, 0.5, 0.25)).toBe(0.5)
    expect(event.wait_duration_seconds).toBe(0.25)
  })

  it('advances wall-clock playback correctly at 0.25x and 0.5x', () => {
    expect(advancePlayback({ status: 'running', elapsedSeconds: 0, rate: 0.25 }, 1, 10).elapsedSeconds).toBe(0.25)
    expect(advancePlayback({ status: 'running', elapsedSeconds: 0, rate: 0.5 }, 1, 10).elapsedSeconds).toBe(0.5)
  })
})
