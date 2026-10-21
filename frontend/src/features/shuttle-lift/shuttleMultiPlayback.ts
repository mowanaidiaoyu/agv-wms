import { SHUTTLE_TRACK_DECK_THICKNESS } from '../../lib/topology3d'
import type { PlaybackStatus, TrajectorySample } from '../path-demo/types'
import { SHUTTLE_VEHICLE_CENTER_HEIGHT } from './shuttleGeometry'
import type {
  ShuttleLevel,
  ShuttleLift,
  ShuttleLiftPlaybackState,
  ShuttleMultiPlanResponse,
  ShuttleMultiPlaybackSample,
  ShuttleMultiVehicleResult,
  ShuttlePlanningEvent,
  ShuttleTimedTrajectorySample,
  ShuttleTrackNode,
} from './types'

export const SHUTTLE_MULTI_COLORS = ['#22d3ee', '#fb923c', '#c084fc', '#84cc16'] as const

const clamp = (value: number, minimum: number, maximum: number) => (
  Math.max(minimum, Math.min(maximum, value))
)

const interpolate = (start: number, end: number, alpha: number) => start + (end - start) * alpha

function nodeParts(nodeId: string | null | undefined) {
  const match = nodeId?.match(/^L([1-4])-r(-?\d+)-c(-?\d+)$/)
  return match
    ? { level: Number(match[1]) as ShuttleLevel, row: Number(match[2]), col: Number(match[3]) }
    : null
}

function enclosingSamples(trajectory: ShuttleTimedTrajectorySample[], elapsedSeconds: number) {
  const upperIndex = trajectory.findIndex((sample) => sample.time_seconds >= elapsedSeconds - 1e-9)
  const nextIndex = upperIndex < 0 ? trajectory.length - 1 : upperIndex
  const previousIndex = Math.max(0, nextIndex - 1)
  const previous = trajectory[previousIndex]
  const next = trajectory[nextIndex]
  const duration = next.time_seconds - previous.time_seconds
  const alpha = duration > 0 ? clamp((elapsedSeconds - previous.time_seconds) / duration, 0, 1) : 1
  return { previous, next, previousIndex, nextIndex, alpha }
}

function activeWait(vehicle: ShuttleMultiVehicleResult, elapsedSeconds: number) {
  return vehicle.wait_intervals.find((wait) => (
    elapsedSeconds >= wait.start_time_seconds - 1e-9
    && elapsedSeconds < wait.end_time_seconds - 1e-9
  ))
}

function accumulatedWait(vehicle: ShuttleMultiVehicleResult, elapsedSeconds: number) {
  return vehicle.wait_intervals.reduce((total, wait) => total + Math.max(
    0,
    Math.min(elapsedSeconds, wait.end_time_seconds) - wait.start_time_seconds,
  ), 0)
}

function samplePhase(
  vehicle: ShuttleMultiVehicleResult,
  previous: ShuttleTimedTrajectorySample,
  next: ShuttleTimedTrajectorySample,
  elapsedSeconds: number,
  playbackStatus: PlaybackStatus,
) {
  if (elapsedSeconds >= vehicle.arrival_time_seconds - 1e-9) return 'arrived' as const
  if (elapsedSeconds <= 1e-9 && playbackStatus === 'idle') return 'not_started' as const
  const wait = activeWait(vehicle, elapsedSeconds)
  if (wait?.reason === 'lift_resource') return 'waiting_lift' as const
  if (wait) return 'waiting_traffic' as const
  const action = next.action === 'start' ? previous.action : next.action
  if (action === 'turn') return 'turning' as const
  if (action === 'lift_enter') return 'entering_lift' as const
  if (action === 'lift_exit') return 'exiting_lift' as const
  if (action === 'lift') return next.world_y >= previous.world_y ? 'moving_up' as const : 'moving_down' as const
  return 'moving_track' as const
}

export function sampleShuttleMultiVehicle(
  vehicle: ShuttleMultiVehicleResult,
  topologyId: string,
  elapsedSeconds: number,
  playbackStatus: PlaybackStatus,
): ShuttleMultiPlaybackSample | null {
  if (!vehicle.timed_trajectory.length) return null
  const elapsed = clamp(
    Number.isFinite(elapsedSeconds) ? elapsedSeconds : 0,
    0,
    vehicle.arrival_time_seconds,
  )
  const { previous, next, previousIndex, alpha } = enclosingSamples(vehicle.timed_trajectory, elapsed)
  const sourceParts = nodeParts(previous.track_node_id ?? next.edge_from ?? vehicle.start.track_node_id)
  const targetParts = nodeParts(next.track_node_id ?? next.edge_to ?? vehicle.destination.track_node_id)
  const parts = alpha >= 1 ? targetParts ?? sourceParts : sourceParts ?? targetParts
  const dx = next.world_x - previous.world_x
  const dz = next.world_z - previous.world_z
  const headingRadians = Math.abs(dx) + Math.abs(dz) > 1e-9 ? Math.atan2(dx, dz) : 0
  const phase = samplePhase(vehicle, previous, next, elapsed, playbackStatus)
  return {
    vehicleId: vehicle.vehicle_id,
    topologyId,
    trackNodeId: next.track_node_id ?? previous.track_node_id ?? next.edge_to ?? next.edge_from ?? '',
    shuttleLevel: parts?.level ?? vehicle.start.shuttle_level,
    row: interpolate(sourceParts?.row ?? vehicle.start.row, targetParts?.row ?? vehicle.destination.row, alpha),
    col: interpolate(sourceParts?.col ?? vehicle.start.col, targetParts?.col ?? vehicle.destination.col, alpha),
    pathIndex: previousIndex,
    elapsedSeconds: elapsed,
    world: [
      interpolate(previous.world_x, next.world_x, alpha),
      interpolate(previous.world_y, next.world_y, alpha) + SHUTTLE_VEHICLE_CENTER_HEIGHT,
      interpolate(previous.world_z, next.world_z, alpha),
    ],
    headingRadians,
    phase,
    liftId: next.lift_id ?? previous.lift_id,
    playbackStatus,
    accumulatedWaitSeconds: accumulatedWait(vehicle, elapsed),
    arrivalTimeSeconds: vehicle.arrival_time_seconds,
  }
}

export function sampleAllShuttleVehicles(
  plan: ShuttleMultiPlanResponse | null,
  elapsedSeconds: number,
  playbackStatus: PlaybackStatus,
) {
  if (!plan || plan.status !== 'planning_success') return []
  return plan.vehicles
    .map((vehicle) => sampleShuttleMultiVehicle(vehicle, plan.topology_id, elapsedSeconds, playbackStatus))
    .filter((sample): sample is ShuttleMultiPlaybackSample => Boolean(sample))
}

export function shuttleMultiCameraSample(sample: ShuttleMultiPlaybackSample | null): TrajectorySample | null {
  if (!sample) return null
  return {
    vehicleId: sample.vehicleId,
    topologyId: sample.topologyId,
    floor: `L${sample.shuttleLevel}`,
    row: sample.row,
    col: sample.col,
    pathIndex: sample.pathIndex,
    elapsedSeconds: sample.elapsedSeconds,
    world: sample.world,
    headingRadians: sample.headingRadians,
  }
}

function levelSurfaceY(level: ShuttleLevel, tracks: ShuttleTrackNode[]) {
  return tracks.find((node) => node.shuttle_level === level)?.world_y ?? 0
}

function platformY(level: ShuttleLevel, tracks: ShuttleTrackNode[]) {
  return levelSurfaceY(level, tracks) - SHUTTLE_TRACK_DECK_THICKNESS / 2
}

export function sampleShuttleLiftStates(
  plan: ShuttleMultiPlanResponse | null,
  lifts: ShuttleLift[],
  tracks: ShuttleTrackNode[],
  elapsedSeconds: number,
): ShuttleLiftPlaybackState[] {
  return lifts.map((lift) => {
    const initialLevel = plan?.lift_initial_state[lift.lift_id] ?? 1
    const usages = (plan?.vehicles ?? [])
      .flatMap((vehicle) => vehicle.lift_usage)
      .filter((usage) => usage.lift_id === lift.lift_id)
      .sort((first, second) => first.reposition_start_seconds - second.reposition_start_seconds)
    let settledLevel = initialLevel
    for (const usage of usages) {
      if (elapsedSeconds < usage.reposition_start_seconds) break
      const reservation = {
        reservationStartSeconds: usage.reposition_start_seconds,
        reservationEndSeconds: usage.exit_end_seconds,
      }
      if (elapsedSeconds < usage.reposition_end_seconds) {
        const duration = usage.reposition_end_seconds - usage.reposition_start_seconds
        const alpha = duration > 0 ? (elapsedSeconds - usage.reposition_start_seconds) / duration : 1
        return {
          liftId: lift.lift_id,
          platformY: interpolate(platformY(usage.initial_cabin_level, tracks), platformY(usage.entry_level, tracks), alpha),
          currentLevel: usage.initial_cabin_level,
          targetLevel: usage.entry_level,
          direction: usage.entry_level > usage.initial_cabin_level ? 'up' : 'down',
          status: 'repositioning_empty',
          occupantVehicleId: null,
          ...reservation,
        }
      }
      if (elapsedSeconds < usage.enter_start_seconds) {
        return {
          liftId: lift.lift_id, platformY: platformY(usage.entry_level, tracks),
          currentLevel: usage.entry_level, targetLevel: usage.exit_level, direction: 'idle',
          status: 'reserved', occupantVehicleId: null, ...reservation,
        }
      }
      if (elapsedSeconds < usage.transfer_start_seconds) {
        return {
          liftId: lift.lift_id, platformY: platformY(usage.entry_level, tracks),
          currentLevel: usage.entry_level, targetLevel: usage.exit_level, direction: 'idle',
          status: 'entering', occupantVehicleId: usage.vehicle_id, ...reservation,
        }
      }
      if (elapsedSeconds < usage.transfer_end_seconds) {
        const duration = usage.transfer_end_seconds - usage.transfer_start_seconds
        const alpha = duration > 0 ? (elapsedSeconds - usage.transfer_start_seconds) / duration : 1
        return {
          liftId: lift.lift_id,
          platformY: interpolate(platformY(usage.entry_level, tracks), platformY(usage.exit_level, tracks), alpha),
          currentLevel: alpha < 1 ? usage.entry_level : usage.exit_level,
          targetLevel: usage.exit_level,
          direction: usage.exit_level > usage.entry_level ? 'up' : 'down',
          status: 'occupied', occupantVehicleId: usage.vehicle_id, ...reservation,
        }
      }
      if (elapsedSeconds < usage.exit_end_seconds) {
        return {
          liftId: lift.lift_id, platformY: platformY(usage.exit_level, tracks),
          currentLevel: usage.exit_level, targetLevel: usage.exit_level, direction: 'idle',
          status: 'exiting', occupantVehicleId: usage.vehicle_id, ...reservation,
        }
      }
      settledLevel = usage.final_cabin_level
    }
    return {
      liftId: lift.lift_id, platformY: platformY(settledLevel, tracks), currentLevel: settledLevel,
      targetLevel: null, direction: 'idle', status: 'idle', occupantVehicleId: null,
      reservationStartSeconds: null, reservationEndSeconds: null,
    }
  })
}

export function eventDisplay(event: ShuttlePlanningEvent) {
  const candidate = ['track_node_conflict_detected', 'edge_swap_conflict_detected', 'geometric_conflict_detected'].includes(event.event_type)
  return {
    candidate,
    resultLabel: candidate && event.resolution_status === 'candidate_rejected'
      ? '潜在冲突 · 已避让'
      : event.resolution_status === 'resolved_by_wait'
        ? '已通过等待解决'
        : event.resolution_status === 'reserved'
          ? '资源已预约'
          : '已完成',
  }
}

export const shuttleMultiMotionLabel: Record<ShuttleMultiPlaybackSample['phase'], string> = {
  not_started: '尚未开始',
  turning: '换向准备',
  moving_track: '轨道移动',
  waiting_traffic: '等待让行',
  waiting_lift: '等待提升机',
  entering_lift: '进入提升机',
  moving_up: '正在上升',
  moving_down: '正在下降',
  exiting_lift: '驶出提升机',
  arrived: '已到达',
}
