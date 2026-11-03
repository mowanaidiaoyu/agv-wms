import { interpolationProgress } from './simulationMotion'

export const PICKING_ARM_PHASES = [
  'idle',
  'approach',
  'grip',
  'lift',
  'rotate',
  'place',
  'release',
  'return_home',
] as const

export type PickingArmPhase = (typeof PICKING_ARM_PHASES)[number]
export type TotePlacement = 'none' | 'pickup' | 'gripper' | 'output'
export type CargoPlaybackState =
  | 'waiting_for_release'
  | 'conveyor_moving'
  | 'conveyor_waiting'
  | 'preview_endpoint_reached'

export interface PickingArmSignals {
  cargoToken: string | null
  pickedGoodsReady: boolean
  outputPositionAvailable: boolean
  legalPickupNode: boolean
  legalDropoffNode: boolean
  authoritativeHandoffCompleted: boolean
}

export interface PickingArmState {
  phase: PickingArmPhase
  phaseElapsed: number
  totePlacement: TotePlacement
  activeCargoToken: string | null
  consumedCargoToken: string | null
  completedCycles: number
  handoffRequested: boolean
  cargoPlaybackState: CargoPlaybackState
  conveyorDistance: number
}

export interface PickingArmAdvanceOptions {
  deltaSeconds: number
  speedMultiplier: 1 | 2 | 4
  paused: boolean
  signals: PickingArmSignals
  conveyorPathLength?: number
  conveyorSpeed?: number
}

export interface PickingArmPosePoints {
  home: [number, number, number]
  pickup: [number, number, number]
  pickupLift: [number, number, number]
  dropLift: [number, number, number]
  dropoff: [number, number, number]
}

export interface PickingArmPose {
  target: [number, number, number]
  gripperGap: number
}

export const PICKING_ARM_PHASE_SECONDS: Record<Exclude<PickingArmPhase, 'idle'>, number> = {
  approach: 1.2,
  grip: 0.45,
  lift: 0.9,
  rotate: 1.4,
  place: 1.0,
  release: 0.45,
  return_home: 1.2,
}

const NEXT_PHASE: Record<Exclude<PickingArmPhase, 'idle'>, PickingArmPhase> = {
  approach: 'grip',
  grip: 'lift',
  lift: 'rotate',
  rotate: 'place',
  place: 'release',
  release: 'return_home',
  return_home: 'idle',
}

export function createInitialPickingArmState(
  signals?: Partial<PickingArmSignals>,
): PickingArmState {
  return {
    phase: 'idle',
    phaseElapsed: 0,
    totePlacement: signals?.pickedGoodsReady ? 'pickup' : 'none',
    activeCargoToken: null,
    consumedCargoToken: null,
    completedCycles: 0,
    handoffRequested: false,
    cargoPlaybackState: 'waiting_for_release',
    conveyorDistance: 0,
  }
}

export function resetPickingArmState(
  state: PickingArmState,
  signals: PickingArmSignals,
): PickingArmState {
  return {
    ...createInitialPickingArmState(signals),
    consumedCargoToken: signals.cargoToken ?? state.consumedCargoToken,
  }
}

function canStart(signals: PickingArmSignals, state: PickingArmState): boolean {
  return Boolean(
    signals.cargoToken
      && signals.pickedGoodsReady
      && signals.legalPickupNode
      && signals.legalDropoffNode
      && signals.cargoToken !== state.consumedCargoToken
      && state.cargoPlaybackState !== 'conveyor_moving',
  )
}

function completePhase(
  state: PickingArmState,
  signals: PickingArmSignals,
  conveyorPathLength: number,
): PickingArmState {
  if (state.phase === 'place' && !signals.outputPositionAvailable) {
    return { ...state, phaseElapsed: PICKING_ARM_PHASE_SECONDS.place }
  }
  if (state.phase === 'return_home') {
    return {
      ...state,
      phase: 'idle',
      phaseElapsed: 0,
      activeCargoToken: null,
      consumedCargoToken: state.activeCargoToken,
      completedCycles: state.completedCycles + 1,
    }
  }
  const next = NEXT_PHASE[state.phase as Exclude<PickingArmPhase, 'idle'>]
  return {
    ...state,
    phase: next,
    phaseElapsed: 0,
    totePlacement: state.phase === 'grip'
      ? 'gripper'
      : state.phase === 'release'
        ? 'output'
        : state.totePlacement,
    handoffRequested: state.phase === 'release' || state.handoffRequested,
    cargoPlaybackState: state.phase === 'release'
      ? conveyorPathLength > 0
        ? 'conveyor_moving'
        : 'conveyor_waiting'
      : state.cargoPlaybackState,
  }
}

export function advancePickingArm(
  state: PickingArmState,
  options: PickingArmAdvanceOptions,
): PickingArmState {
  if (options.paused || options.deltaSeconds <= 0) return state
  let next = state
  const conveyorWasMoving = state.cargoPlaybackState === 'conveyor_moving'
  if (next.phase === 'idle') {
    if (!canStart(options.signals, next)) {
      next = {
        ...next,
        totePlacement: options.signals.pickedGoodsReady && options.signals.cargoToken !== next.consumedCargoToken
          ? 'pickup'
          : next.totePlacement,
      }
    } else {
      next = {
        ...next,
        phase: 'approach',
        phaseElapsed: 0,
        totePlacement: 'pickup',
        activeCargoToken: options.signals.cargoToken,
        handoffRequested: false,
        cargoPlaybackState: 'waiting_for_release',
        conveyorDistance: 0,
      }
    }
  }

  let remaining = options.deltaSeconds * options.speedMultiplier
  let guard = 0
  while (remaining > 0 && next.phase !== 'idle' && guard < 16) {
    guard += 1
    const duration = PICKING_ARM_PHASE_SECONDS[next.phase]
    const available = Math.max(0, duration - next.phaseElapsed)
    const consumed = Math.min(remaining, available)
    next = { ...next, phaseElapsed: next.phaseElapsed + consumed }
    remaining -= consumed
    if (next.phaseElapsed + 1e-9 < duration) break
    const completed = completePhase(next, options.signals, options.conveyorPathLength ?? 0)
    if (completed.phase === next.phase && completed.phaseElapsed === next.phaseElapsed) break
    next = completed
  }
  if (conveyorWasMoving) {
    const pathLength = Math.max(0, options.conveyorPathLength ?? 0)
    const distance = Math.min(
      pathLength,
      next.conveyorDistance
        + options.deltaSeconds * options.speedMultiplier * Math.max(0.01, options.conveyorSpeed ?? 1.35),
    )
    next = {
      ...next,
      conveyorDistance: distance,
      cargoPlaybackState: distance >= pathLength
        ? 'preview_endpoint_reached'
        : 'conveyor_moving',
    }
  }
  return next
}

function mix(
  from: [number, number, number],
  to: [number, number, number],
  progress: number,
): [number, number, number] {
  return [
    from[0] + (to[0] - from[0]) * progress,
    from[1] + (to[1] - from[1]) * progress,
    from[2] + (to[2] - from[2]) * progress,
  ]
}

export function pickingArmPose(
  state: PickingArmState,
  points: PickingArmPosePoints,
): PickingArmPose {
  if (state.phase === 'idle') return { target: points.home, gripperGap: 0.24 }
  const duration = PICKING_ARM_PHASE_SECONDS[state.phase]
  const progress = interpolationProgress(state.phaseElapsed, duration)
  if (state.phase === 'approach') {
    const target = progress < 0.55
      ? mix(points.home, points.pickupLift, progress / 0.55)
      : mix(points.pickupLift, points.pickup, (progress - 0.55) / 0.45)
    return { target, gripperGap: 0.24 }
  }
  if (state.phase === 'grip') return { target: points.pickup, gripperGap: 0.24 - progress * 0.17 }
  if (state.phase === 'lift') return { target: mix(points.pickup, points.pickupLift, progress), gripperGap: 0.07 }
  if (state.phase === 'rotate') return { target: mix(points.pickupLift, points.dropLift, progress), gripperGap: 0.07 }
  if (state.phase === 'place') return { target: mix(points.dropLift, points.dropoff, progress), gripperGap: 0.07 }
  if (state.phase === 'release') return { target: points.dropoff, gripperGap: 0.07 + progress * 0.17 }
  const target = progress < 0.4
    ? mix(points.dropoff, points.dropLift, progress / 0.4)
    : mix(points.dropLift, points.home, (progress - 0.4) / 0.6)
  return { target, gripperGap: 0.24 }
}

export function toteStatusFor(state: PickingArmState): string {
  if (state.cargoPlaybackState === 'conveyor_moving') return 'conveyor_moving'
  if (state.cargoPlaybackState === 'conveyor_waiting') return 'conveyor_waiting'
  if (state.cargoPlaybackState === 'preview_endpoint_reached') return '已到达预览路径终点'
  if (state.totePlacement === 'pickup') return 'picked_goods_ready'
  if (state.totePlacement === 'gripper') return 'robot_arm_transfer'
  if (state.totePlacement === 'output') return 'waiting_handoff_confirmation'
  return 'not_present'
}

export function canDeclarePickingArmDelivery(
  state: PickingArmState,
  signals: PickingArmSignals,
): boolean {
  return Boolean(
    state.totePlacement === 'output'
      && state.handoffRequested
      && signals.legalDropoffNode
      && signals.authoritativeHandoffCompleted,
  )
}
