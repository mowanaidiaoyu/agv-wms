import { describe, expect, it } from 'vitest'
import type { TopologyPayload } from '../../types'
import { buildConfiguredVehicleStartSample, buildMultiVehicleViews, buildScheduledTrajectory } from './multiPathPlayback'
import type { MultiPathPlan, MultiPathVehiclePlan } from './types'

const topology: TopologyPayload = {
  id: 'topology-test',
  name: 'Test',
  meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 10, cols: 10, cellSize: 32, originX: 0, originY: 0 },
  zones: [],
  cells: [],
  nodes: [],
  resources: [],
}

const cell = (id: string, row: number, col: number) => ({
  id, floor: '1F', row, col, type: 'road', component_id: 'road-component-1',
})

const vehiclePlan: MultiPathVehiclePlan = {
  vehicle_id: 'CTU-01',
  start: cell('a', 1, 1),
  goal: cell('c', 1, 3),
  steps: [
    { time_step: 0, time_seconds: 0, action: 'start', cell: cell('a', 1, 1) },
    { time_step: 1, time_seconds: 0.25, action: 'wait', cell: cell('a', 1, 1) },
    { time_step: 2, time_seconds: 0.5, action: 'move', cell: cell('b', 1, 2) },
    { time_step: 3, time_seconds: 0.75, action: 'move', cell: cell('c', 1, 3) },
  ],
  total_distance: 2,
  estimated_duration_seconds: 0.75,
  wait_time_seconds: 0.25,
  avoidance_count: 1,
  visited_states: 4,
}

const plan: MultiPathPlan = {
  topology_id: topology.id,
  topology_name: topology.name,
  topology_version: '2.0.0',
  algorithm: 'astar',
  planning_status: 'success',
  error_reason: null,
  time_step_seconds: 0.25,
  speed_cells_per_second: 4,
  vehicle_width_cells: 0.58,
  vehicle_length_cells: 0.72,
  safety_distance_cells: 0.97,
  makespan_steps: 3,
  makespan_seconds: 0.75,
  vehicles: [vehiclePlan, { ...vehiclePlan, vehicle_id: 'CTU-02' }, { ...vehiclePlan, vehicle_id: 'CTU-03' }],
  conflicts: {
    detected_node_conflicts: 1,
    detected_edge_conflicts: 0,
    detected_geometry_conflicts: 0,
    resolved_node_conflicts: 1,
    resolved_edge_conflicts: 0,
    resolved_geometry_conflicts: 0,
    remaining_node_conflicts: 0,
    remaining_edge_conflicts: 0,
    remaining_geometry_conflicts: 0,
  },
  avoidance_count: 1,
  planning_time_ms: 1,
}

describe('multi path playback', () => {
  it('builds its timeline only from backend time-space steps, including waits', () => {
    const trajectory = buildScheduledTrajectory(vehiclePlan, topology)
    expect(trajectory.map((point) => point.arrivalSeconds)).toEqual([0, 0.25, 0.5, 0.75])
    expect(trajectory[0].world).toEqual(trajectory[1].world)
    expect(trajectory[2].world).not.toEqual(trajectory[1].world)
  })

  it('samples all vehicles from one shared elapsed time and assigns stable colors', () => {
    const views = buildMultiVehicleViews(plan, topology, 0.375)
    expect(views.map((view) => view.vehicleId)).toEqual(['CTU-01', 'CTU-02', 'CTU-03'])
    expect(views.map((view) => view.color)).toEqual(['#22d3ee', '#fb923c', '#c084fc'])
    expect(new Set(views.map((view) => view.sample?.elapsedSeconds))).toEqual(new Set([0.375]))
    expect(views[0].sample?.headingRadians).toBeCloseTo(Math.PI / 2)
  })

  it('supports six independent vehicle styles and honors configured colors', () => {
    const vehicles = Array.from({ length: 6 }, (_, index) => ({
      ...vehiclePlan,
      vehicle_id: `CTU-0${index + 1}`,
      display_color: index === 5 ? '#123456' : null,
    }))
    const views = buildMultiVehicleViews({ ...plan, vehicles }, topology, 0.25)
    expect(views).toHaveLength(6)
    expect(new Set(views.map((view) => view.color)).size).toBe(6)
    expect(views[5].color).toBe('#123456')
  })

  it('builds an unplanned camera preview from the selected vehicle configured start only', () => {
    const preview = buildConfiguredVehicleStartSample({
      vehicle_id: 'CTU-06',
      start_cell_id: 'preview-start',
      goal_cell_id: 'preview-goal',
    }, [cell('preview-start', 3, 2)], topology)
    expect(preview).toMatchObject({ vehicleId: 'CTU-06', row: 3, col: 2, elapsedSeconds: 0 })
    expect(preview?.world).toEqual([-2.5, 0.5, 1.5])
    expect(buildConfiguredVehicleStartSample({
      vehicle_id: 'CTU-06',
      start_cell_id: 'blocked',
      goal_cell_id: 'preview-goal',
    }, [{ ...cell('blocked', 3, 2), type: 'blocked' }], topology)).toBeNull()
  })
})
