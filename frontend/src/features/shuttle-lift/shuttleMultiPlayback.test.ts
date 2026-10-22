import { describe, expect, it } from 'vitest'
import { sampleAllShuttleVehicles, sampleShuttleLiftStates, sampleShuttleMultiVehicle } from './shuttleMultiPlayback'
import type { ShuttleLift, ShuttleMultiPlanResponse, ShuttleMultiVehicleResult, ShuttleTrackNode } from './types'

const node = (id: string, level: 1 | 2 | 3 | 4, x: number, y: number): ShuttleTrackNode => ({
  topology_id: 'topology-666', zone_id: 'shuttle-zone', track_node_id: id, shuttle_level: level,
  row: 100, col: x, world_x: x, world_y: y, world_z: 0, track_type: 'straight', traversable: true, neighbors: [],
})
const nodes = [node('L1-r100-c1', 1, 1, 10), node('L1-r100-c3', 1, 3, 10), node('L4-r100-c3', 4, 3, 40)]
const vehicle: ShuttleMultiVehicleResult = {
  vehicle_id: 'SHUTTLE-01', priority: 1, algorithm: 'astar', start: nodes[0], destination: nodes[2],
  path: nodes.map((item) => item.track_node_id), arrival_time_seconds: 6, wait_time_seconds: 1,
  horizontal_distance_cells: 2, visited_states: 12, planning_status: 'planned',
  timed_trajectory: [
    { time_step: 0, time_seconds: 0, world_x: 1, world_y: 10, world_z: 0, track_node_id: nodes[0].track_node_id, action: 'start', lift_id: null, edge_from: null, edge_to: null },
    { time_step: 4, time_seconds: 1, world_x: 1, world_y: 10, world_z: 0, track_node_id: nodes[0].track_node_id, action: 'wait', lift_id: null, edge_from: nodes[0].track_node_id, edge_to: nodes[0].track_node_id },
    { time_step: 8, time_seconds: 2, world_x: 3, world_y: 10, world_z: 0, track_node_id: nodes[1].track_node_id, action: 'move', lift_id: null, edge_from: nodes[0].track_node_id, edge_to: nodes[1].track_node_id },
    { time_step: 24, time_seconds: 6, world_x: 3, world_y: 40, world_z: 0, track_node_id: nodes[2].track_node_id, action: 'lift', lift_id: 'shuttle-lift-a', edge_from: nodes[1].track_node_id, edge_to: nodes[2].track_node_id },
  ],
  wait_intervals: [{ track_node_id: nodes[0].track_node_id, start_time_seconds: 0, end_time_seconds: 1, duration_seconds: 1, reason: 'track_reservation' }],
  lift_usage: [{ lift_id: 'shuttle-lift-a', vehicle_id: 'SHUTTLE-01', entry_level: 1, exit_level: 4, request_time_seconds: 1.5, reposition_start_seconds: 1, reposition_end_seconds: 2, enter_start_seconds: 2, transfer_start_seconds: 3, transfer_end_seconds: 5, exit_end_seconds: 6, initial_cabin_level: 2, final_cabin_level: 4, used_as_alternate: false, reservation_status: 'reserved' }],
}
const plan: ShuttleMultiPlanResponse = {
  planning_id: 'plan-1', topology_id: 'topology-666', topology_version: '2.0.0', topology_revision: 'revision', shuttle_config_revision: 'shuttle-4l-lift-v2', coordinate_contract_version: 'shuttle-internal-levels-v1', planning_algorithm_version: 'v1', status: 'planning_success', vehicles: [vehicle], events: [],
  metrics: { requested_vehicle_count: 1, planned_vehicle_count: 1, makespan_seconds: 6, total_wait_time_seconds: 1, average_wait_time_seconds: 1, detected_track_node_conflicts: 1, detected_edge_swap_conflicts: 0, detected_geometric_conflicts: 0, lift_resource_contentions: 0, lift_a_usage_count: 1, lift_b_usage_count: 0, lift_a_occupied_seconds: 5, lift_b_occupied_seconds: 0, alternate_lift_selections: 0, replans_used: 0, unresolved_conflict_count: 0, planning_time_ms: 1 },
  safety_validation: { node_conflicts: 0, edge_conflicts: 0, geometric_conflicts: 0, lift_resource_conflicts: 0, timing_violations: 0, safe: true }, unresolved_conflicts: [], failure_reason: null, failure: null, vehicle_initial_config: [], lift_initial_state: { 'shuttle-lift-a': 2, 'shuttle-lift-b': 1 }, time_contract: { time_step_seconds: 0.25 },
}
const lift: ShuttleLift = { lift_id: 'shuttle-lift-a', label: 'Lift-A', topology_id: 'topology-666', topology_revision: 'revision', config_revision: 'shuttle-4l-lift-v2', zone_id: 'shuttle-zone', row: 100, col: 3, supported_levels: [1, 2, 3, 4], level_entrances: { L1: nodes[1].track_node_id, L2: 'L2-r100-c3', L3: 'L3-r100-c3', L4: nodes[2].track_node_id }, level_exits: { L1: nodes[1].track_node_id, L2: 'L2-r100-c3', L3: 'L3-r100-c3', L4: nodes[2].track_node_id }, capacity: 1, enter_time_seconds: 1, travel_time_per_level_seconds: 1, exit_time_seconds: 1, timing_source: 'synthetic_demo', vehicle_type: 'shuttle', state: 'idle', verification_status: 'program_verified_pending_human', validation_evidence: [] }
const liftTracks = [...nodes, node('L2-r100-c3', 2, 3, 20), node('L3-r100-c3', 3, 3, 30)]

describe('shuttle multi playback', () => {
  it('uses the real timed trajectory and keeps a waiting vehicle fixed', () => {
    const sample = sampleShuttleMultiVehicle(vehicle, plan.topology_id, 0.5, 'running')
    expect(sample?.phase).toBe('waiting_traffic')
    expect(sample?.world[0]).toBe(1)
    expect(sample?.accumulatedWaitSeconds).toBeCloseTo(0.5)
  })

  it('clamps all vehicles at their actual arrival position', () => {
    const [sample] = sampleAllShuttleVehicles(plan, 99, 'completed')
    expect(sample.phase).toBe('arrived')
    expect(sample.world[0]).toBe(3)
    expect(sample.world[1]).toBeGreaterThan(40)
  })

  it('renders empty reposition without a passenger, then binds the real occupant during transfer', () => {
    const reposition = sampleShuttleLiftStates(plan, [lift], liftTracks, 1.5)[0]
    expect(reposition.status).toBe('repositioning_empty')
    expect(reposition.occupantVehicleId).toBeNull()
    const occupied = sampleShuttleLiftStates(plan, [lift], liftTracks, 4)[0]
    expect(occupied.status).toBe('occupied')
    expect(occupied.occupantVehicleId).toBe('SHUTTLE-01')
  })

  it('reports persisted Pick and Drop service dwells without moving the vehicle', () => {
    const serviceVehicle: ShuttleMultiVehicleResult = {
      ...vehicle,
      arrival_time_seconds: 4,
      timed_trajectory: [
        { ...vehicle.timed_trajectory[0], time_step: 0, time_seconds: 0 },
        { ...vehicle.timed_trajectory[0], time_step: 4, time_seconds: 1, action: 'pick_service' },
        { ...vehicle.timed_trajectory[0], time_step: 8, time_seconds: 2, action: 'pick_service' },
        { ...vehicle.timed_trajectory[2], time_step: 12, time_seconds: 3, action: 'drop_service' },
        { ...vehicle.timed_trajectory[2], time_step: 16, time_seconds: 4, action: 'drop_service' },
      ],
      wait_intervals: [],
    }
    const picking = sampleShuttleMultiVehicle(serviceVehicle, plan.topology_id, 1.5, 'running')
    const dropping = sampleShuttleMultiVehicle(serviceVehicle, plan.topology_id, 3.5, 'running')
    expect(picking?.phase).toBe('picking')
    expect(dropping?.phase).toBe('dropping')
  })
})
