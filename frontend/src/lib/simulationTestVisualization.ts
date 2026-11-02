import type { SimulationTestCargoState } from '../types'

export type SimulationPoint = readonly [number, number, number]

export const SIMULATION_CARGO_BODY_HEIGHT = 0.58
export const SIMULATION_CARGO_CENTER_OFFSET_Y = 0.3

const SHUTTLE_BOUND_CARGO_STATUSES = new Set<SimulationTestCargoState['status']>([
  'shuttle_picking',
  'shuttle_transporting',
  'lift_waiting',
  'lift_moving',
  'shuttle_to_handoff',
])

/** Device-owned cargo is rendered only inside that device model. */
export function isSimulationCargoRenderedByDevice(status: SimulationTestCargoState['status']): boolean {
  return SHUTTLE_BOUND_CARGO_STATUSES.has(status) || status === 'arm_processing'
}

export function shouldRenderSimulationCargoIndependently(
  cargo: Pick<SimulationTestCargoState, 'cargoId' | 'status'>,
  armActiveCargoId?: string | null,
): boolean {
  return cargo.cargoId !== armActiveCargoId && !isSimulationCargoRenderedByDevice(cargo.status)
}

export function isSimulationConveyorMotion(status: SimulationTestCargoState['status']): boolean {
  return status === 'conveyor_transporting' || status === 'merge_conveyor_transporting'
}

/** The visual may interpolate only inside the latest authoritative edge. */
export function interpolateSimulationPoint(
  from: SimulationPoint,
  to: SimulationPoint,
  progress: number,
): [number, number, number] {
  const bounded = Math.max(0, Math.min(1, progress))
  return [
    from[0] + (to[0] - from[0]) * bounded,
    from[1] + (to[1] - from[1]) * bounded,
    from[2] + (to[2] - from[2]) * bounded,
  ]
}

export function simulationMotionYaw(
  currentYaw: number,
  from: SimulationPoint,
  to: SimulationPoint,
): number {
  const dx = to[0] - from[0]
  const dz = to[2] - from[2]
  if (Math.hypot(dx, dz) <= 0.0001) return currentYaw
  let target = Math.atan2(dx, dz)
  while (target - currentYaw > Math.PI) target -= Math.PI * 2
  while (target - currentYaw < -Math.PI) target += Math.PI * 2
  return target
}

export function rollerRotationDirection(direction: readonly [number, number]): 1 | -1 {
  return direction[0] < 0 || (direction[0] === 0 && direction[1] < 0) ? -1 : 1
}

export function advanceSimulationRollerAngle(
  current: number,
  deltaSeconds: number,
  direction: readonly [number, number],
  active: boolean,
  paused: boolean,
  radiansPerSecond = 9,
): number {
  if (!active || paused) return current
  return current + deltaSeconds * radiansPerSecond * rollerRotationDirection(direction)
}

export function simulationCargoBottomY(baseY: number): number {
  return baseY + SIMULATION_CARGO_CENTER_OFFSET_Y - SIMULATION_CARGO_BODY_HEIGHT / 2
}

export function boundedSimulationTransitionDuration(tickIntervalSeconds: number): number {
  return Math.min(1.2, Math.max(0.08, tickIntervalSeconds * 1.08))
}
