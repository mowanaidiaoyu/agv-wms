import { describe, expect, it } from 'vitest'
import type { ShuttlePlanResponse, ShuttleTrackNode } from './types'
import { sampleShuttlePlan, shuttleCameraSample } from './shuttlePlayback'

function node(id: string, level: 1 | 2 | 3, row: number, col: number, y: number): ShuttleTrackNode {
  return {
    topology_id: 'topology-666', zone_id: 'zone-shuttle', track_node_id: id,
    shuttle_level: level, row, col, world_x: col, world_y: y, world_z: -row,
    track_type: id.includes('lift') ? 'lift_entry' : 'straight', traversable: true, neighbors: [],
  }
}

const start = node('L1-start', 1, 10, 1, 1.35)
const liftL1 = node('L1-lift', 1, 10, 3, 1.35)
const liftL3 = node('L3-lift', 3, 10, 3, 4.15)
const goal = node('L3-goal', 3, 10, 5, 4.15)
const plan: ShuttlePlanResponse = {
  topology_id: 'topology-666', topology_revision: '2.0.0:hash', config_revision: 'v1', algorithm: 'astar',
  start, destination: goal, chosen_lift: 'shuttle-lift-a', result_status: 'success', failure_reason: null,
  horizontal_distance_cells: 4, estimated_duration_seconds: 12, visited_states: 4, planning_time_ms: 1,
  timing_source: 'synthetic_demo', cost_contract: { time_unit: 'seconds' },
  path_nodes: [
    { ...start, arrival_seconds: 0, phase: 'start' },
    { ...liftL1, arrival_seconds: 2, phase: 'track' },
    { ...liftL3, arrival_seconds: 10, phase: 'lift' },
    { ...goal, arrival_seconds: 12, phase: 'track' },
  ],
  path_edges: [
    { from_track_node: start.track_node_id, to_track_node: liftL1.track_node_id, edge_type: 'track', lift_id: null, duration_seconds: 2, start_seconds: 0, motion_start_seconds: 0, motion_end_seconds: 2, end_seconds: 2, distance_cells: 2, turn_time_seconds: 0 },
    { from_track_node: liftL1.track_node_id, to_track_node: liftL3.track_node_id, edge_type: 'lift', lift_id: 'shuttle-lift-a', duration_seconds: 8, start_seconds: 2, motion_start_seconds: 3, motion_end_seconds: 9, end_seconds: 10, distance_cells: 0, turn_time_seconds: 0 },
    { from_track_node: liftL3.track_node_id, to_track_node: goal.track_node_id, edge_type: 'track', lift_id: null, duration_seconds: 2, start_seconds: 10, motion_start_seconds: 10, motion_end_seconds: 12, end_seconds: 12, distance_cells: 2, turn_time_seconds: 0 },
  ],
  lift_transitions: [{
    lift_id: 'shuttle-lift-a', from_level: 1, to_level: 3,
    entrance_track_node: liftL1.track_node_id, exit_track_node: liftL3.track_node_id,
    enter_at_seconds: 2, lift_start_seconds: 3, arrive_level_seconds: 9, exit_at_seconds: 10,
    events: ['enter_lift', 'lift_started', 'arrive_level', 'exit_lift'],
  }],
}

describe('shuttle cross-level playback', () => {
  it('keeps the vehicle at the entrance during lift entry and moves vertically continuously', () => {
    const entering = sampleShuttlePlan(plan, 2.5, 'running')!
    const risingA = sampleShuttlePlan(plan, 4, 'running')!
    const risingB = sampleShuttlePlan(plan, 7, 'running')!
    expect(entering.phase).toBe('entering_lift')
    expect(entering.world[1]).toBeCloseTo(liftL1.world_y + 0.22)
    expect(risingA.phase).toBe('moving_up')
    expect(risingB.world[1]).toBeGreaterThan(risingA.world[1])
    expect(risingB.world[0]).toBe(risingA.world[0])
    expect(risingB.world[2]).toBe(risingA.world[2])
  })

  it('holds at the target level during lift exit and then follows the L3 track', () => {
    const exiting = sampleShuttlePlan(plan, 9.5, 'running')!
    const track = sampleShuttlePlan(plan, 11, 'running')!
    expect(exiting.phase).toBe('exiting_lift')
    expect(exiting.shuttleLevel).toBe(3)
    expect(track.phase).toBe('moving_track')
    expect(track.world[0]).toBeGreaterThan(exiting.world[0])
  })

  it('clamps completion and produces the same position while paused', () => {
    const pausedA = sampleShuttlePlan(plan, 6, 'paused')!
    const pausedB = sampleShuttlePlan(plan, 6, 'paused')!
    const completed = sampleShuttlePlan(plan, 99, 'completed')!
    expect(pausedA.world).toEqual(pausedB.world)
    expect(completed.phase).toBe('arrived')
    expect(completed.trackNodeId).toBe(goal.track_node_id)
    expect(completed.elapsedSeconds).toBe(12)
  })

  it('uses the rendered shuttle sample as the camera source', () => {
    const sample = sampleShuttlePlan(plan, 5, 'running')!
    expect(shuttleCameraSample(sample)).toMatchObject({
      vehicleId: 'SHUTTLE-01', floor: 'L1', world: sample.world, elapsedSeconds: 5,
    })
  })

  it('returns no position for failed or absent plans', () => {
    expect(sampleShuttlePlan(null, 0, 'idle')).toBeNull()
    expect(sampleShuttlePlan({ ...plan, result_status: 'failed', path_nodes: [], path_edges: [] }, 0, 'idle')).toBeNull()
  })
})
