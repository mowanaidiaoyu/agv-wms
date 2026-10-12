import { floorPlaneY, worldX, worldZ } from '../../lib/topology3d'
import type { TopologyPayload } from '../../types'
import { sampleTrajectory } from './pathPlayback'
import type { MultiPathPlan, MultiPathVehiclePlan, PathDemoVehicleView, TrajectoryPoint } from './types'

export const MULTI_VEHICLE_STYLES = [
  { vehicleId: 'CTU-01', label: 'CTU-01', color: '#22d3ee' },
  { vehicleId: 'CTU-02', label: 'CTU-02', color: '#fb923c' },
  { vehicleId: 'CTU-03', label: 'CTU-03', color: '#c084fc' },
] as const

export function buildScheduledTrajectory(
  vehiclePlan: MultiPathVehiclePlan,
  topology: TopologyPayload,
): TrajectoryPoint[] {
  const floors = topology.meta.floors?.length ? topology.meta.floors : ['1F']
  return vehiclePlan.steps.map((step, index) => {
    const floorIndex = Math.max(0, floors.indexOf(step.cell.floor))
    return {
      vehicleId: vehiclePlan.vehicle_id,
      topologyId: topology.id,
      floor: step.cell.floor,
      row: step.cell.row,
      col: step.cell.col,
      pathIndex: index,
      arrivalSeconds: step.time_seconds,
      world: [
        worldX(step.cell.col, topology.grid.cols),
        floorPlaneY(floorIndex) + 0.5,
        worldZ(step.cell.row, topology.grid.rows),
      ],
    }
  })
}

export function buildMultiVehicleViews(
  plan: MultiPathPlan,
  topology: TopologyPayload,
  elapsedSeconds: number,
): PathDemoVehicleView[] {
  return plan.vehicles.map((vehiclePlan) => {
    const style = MULTI_VEHICLE_STYLES.find((item) => item.vehicleId === vehiclePlan.vehicle_id) ?? {
      vehicleId: vehiclePlan.vehicle_id,
      label: vehiclePlan.vehicle_id,
      color: '#e2e8f0',
    }
    const trajectory = buildScheduledTrajectory(vehiclePlan, topology)
    return {
      vehicleId: vehiclePlan.vehicle_id,
      label: style.label,
      color: style.color,
      trajectory,
      sample: sampleTrajectory(trajectory, elapsedSeconds),
    }
  })
}
