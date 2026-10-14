import { floorPlaneY, worldX, worldZ } from '../../lib/topology3d'
import type { TopologyPayload } from '../../types'
import { sampleTrajectory } from './pathPlayback'
import type {
  HistoricalVehicleSnapshot,
  MultiPathPlan,
  MultiPathVehiclePlan,
  PathDemoCell,
  PathDemoEndpointMarker,
  PathDemoVehicleView,
  MultiPathVehicleRequest,
  TrajectoryPoint,
  TrajectorySample,
} from './types'

export const MULTI_VEHICLE_STYLES = [
  { vehicleId: 'CTU-01', label: 'CTU-01', color: '#22d3ee' },
  { vehicleId: 'CTU-02', label: 'CTU-02', color: '#fb923c' },
  { vehicleId: 'CTU-03', label: 'CTU-03', color: '#c084fc' },
  { vehicleId: 'CTU-04', label: 'CTU-04', color: '#4ade80' },
  { vehicleId: 'CTU-05', label: 'CTU-05', color: '#fb7185' },
  { vehicleId: 'CTU-06', label: 'CTU-06', color: '#60a5fa' },
] as const

export function vehicleDisplayColor(vehicleId: string, configuredColor?: string | null): string {
  return configuredColor
    ?? MULTI_VEHICLE_STYLES.find((item) => item.vehicleId === vehicleId)?.color
    ?? '#e2e8f0'
}

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
  return sampleMultiVehicleViews(buildMultiVehicleTrajectories(plan, topology), elapsedSeconds)
}

export function buildMultiVehicleTrajectories(
  plan: MultiPathPlan,
  topology: TopologyPayload,
): PathDemoVehicleView[] {
  return plan.vehicles.map((vehiclePlan) => {
    const color = vehicleDisplayColor(vehiclePlan.vehicle_id, vehiclePlan.display_color)
    const trajectory = buildScheduledTrajectory(vehiclePlan, topology)
    return {
      vehicleId: vehiclePlan.vehicle_id,
      label: vehiclePlan.vehicle_id,
      color,
      trajectory,
      sample: null,
    }
  })
}

export function sampleMultiVehicleViews(
  preparedViews: PathDemoVehicleView[],
  elapsedSeconds: number,
): PathDemoVehicleView[] {
  return preparedViews.map((view) => ({
    ...view,
    sample: sampleTrajectory(view.trajectory, elapsedSeconds),
  }))
}

function segmentDistance(from: MultiPathVehiclePlan['steps'][number], to: MultiPathVehiclePlan['steps'][number]) {
  if (to.action !== 'move') return 0
  return Math.hypot(to.cell.row - from.cell.row, to.cell.col - from.cell.col)
}

export function sampleAllVehiclesAtTime(
  plan: MultiPathPlan,
  currentTimeSeconds: number,
): HistoricalVehicleSnapshot[] {
  const time = Math.max(0, Math.min(
    Number.isFinite(currentTimeSeconds) ? currentTimeSeconds : 0,
    plan.makespan_seconds,
  ))

  return plan.vehicles.map((vehicle) => {
    const steps = vehicle.steps
    if (!steps.length) {
      return {
        vehicleId: vehicle.vehicle_id,
        status: 'NOT_STARTED',
        logicalPosition: '无轨迹',
        currentCellId: null,
        movedDistance: 0,
        accumulatedWaitSeconds: 0,
        progress: 0,
      }
    }

    const first = steps[0]
    const final = steps[steps.length - 1]
    if (time < first.time_seconds) {
      return {
        vehicleId: vehicle.vehicle_id,
        status: 'NOT_STARTED',
        logicalPosition: first.cell.id,
        currentCellId: first.cell.id,
        movedDistance: 0,
        accumulatedWaitSeconds: 0,
        progress: 0,
      }
    }

    let movedDistance = 0
    let accumulatedWaitSeconds = 0
    let activeSegmentIndex = steps.length - 1
    for (let index = 1; index < steps.length; index += 1) {
      const previous = steps[index - 1]
      const next = steps[index]
      const segmentStart = previous.time_seconds
      const segmentEnd = next.time_seconds
      const duration = Math.max(0, segmentEnd - segmentStart)
      const elapsedInSegment = Math.max(0, Math.min(time, segmentEnd) - segmentStart)
      const fraction = duration > 0 ? elapsedInSegment / duration : time >= segmentEnd ? 1 : 0
      const distance = segmentDistance(previous, next)
      movedDistance += distance * fraction
      if (next.action === 'wait' || distance === 0) accumulatedWaitSeconds += elapsedInSegment
      if (time < segmentEnd) {
        activeSegmentIndex = index
        break
      }
    }

    if (time >= final.time_seconds) {
      return {
        vehicleId: vehicle.vehicle_id,
        status: 'COMPLETED',
        logicalPosition: final.cell.id,
        currentCellId: final.cell.id,
        movedDistance: vehicle.total_distance,
        accumulatedWaitSeconds: vehicle.wait_time_seconds,
        progress: 1,
      }
    }

    const previous = steps[Math.max(0, activeSegmentIndex - 1)]
    const next = steps[activeSegmentIndex]
    const waiting = next.action === 'wait'
      || (previous.cell.floor === next.cell.floor
        && previous.cell.row === next.cell.row
        && previous.cell.col === next.cell.col)
    const totalDistance = vehicle.total_distance
    return {
      vehicleId: vehicle.vehicle_id,
      status: waiting ? 'WAITING' : 'MOVING',
      logicalPosition: waiting ? previous.cell.id : `${previous.cell.id} → ${next.cell.id}`,
      currentCellId: previous.cell.id,
      movedDistance,
      accumulatedWaitSeconds,
      progress: totalDistance > 0 ? Math.max(0, Math.min(movedDistance / totalDistance, 1)) : 0,
    }
  })
}

export function buildConfiguredVehicleStartSample(
  vehicle: MultiPathVehicleRequest | undefined,
  roadCells: PathDemoCell[],
  topology: TopologyPayload,
): TrajectorySample | null {
  if (!vehicle?.start_cell_id) return null
  const cell = roadCells.find((candidate) => candidate.id === vehicle.start_cell_id && candidate.type === 'road')
  if (!cell) return null
  const floors = topology.meta.floors?.length ? topology.meta.floors : ['1F']
  const floorIndex = Math.max(0, floors.indexOf(cell.floor))
  return {
    vehicleId: vehicle.vehicle_id,
    topologyId: topology.id,
    floor: cell.floor,
    row: cell.row,
    col: cell.col,
    pathIndex: 0,
    elapsedSeconds: 0,
    world: [
      worldX(cell.col, topology.grid.cols),
      floorPlaneY(floorIndex) + 0.5,
      worldZ(cell.row, topology.grid.rows),
    ],
    headingRadians: 0,
  }
}


export function buildEndpointMarkers(
  vehicles: MultiPathVehicleRequest[],
  roadCells: PathDemoCell[],
  topology: TopologyPayload,
): PathDemoEndpointMarker[] {
  const cellsById = new Map(roadCells.map((cell) => [cell.id, cell]))
  const floors = topology.meta.floors?.length ? topology.meta.floors : ['1F']
  return vehicles.flatMap((vehicle) => (['start', 'goal'] as const).flatMap((kind) => {
    const cellId = kind === 'start' ? vehicle.start_cell_id : vehicle.goal_cell_id
    const cell = cellsById.get(cellId)
    if (!cell) return []
    const floorIndex = Math.max(0, floors.indexOf(cell.floor))
    return [{
      vehicleId: vehicle.vehicle_id,
      kind,
      label: `${vehicle.vehicle_id} ${kind === 'start' ? '起点' : '终点'}`,
      color: vehicleDisplayColor(vehicle.vehicle_id, vehicle.display_color),
      world: [
        worldX(cell.col, topology.grid.cols),
        floorPlaneY(floorIndex) + 0.24,
        worldZ(cell.row, topology.grid.rows),
      ] as [number, number, number],
    }]
  }))
}
