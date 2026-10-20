import type { PlaybackStatus, TrajectorySample } from '../path-demo/types'
import type {
  ShuttlePathEdge,
  ShuttlePathNodeStep,
  ShuttlePlanResponse,
  ShuttlePlaybackSample,
} from './types'

const VEHICLE_CENTER_HEIGHT = 0.22

function centeredWorld(node: ShuttlePathNodeStep): [number, number, number] {
  return [node.world_x, node.world_y + VEHICLE_CENTER_HEIGHT, node.world_z]
}

function interpolate(start: number, end: number, alpha: number) {
  return start + (end - start) * Math.max(0, Math.min(1, alpha))
}

function heading(source: ShuttlePathNodeStep, target: ShuttlePathNodeStep, fallback = 0) {
  const dx = target.world_x - source.world_x
  const dz = target.world_z - source.world_z
  return Math.abs(dx) + Math.abs(dz) > 1e-9 ? Math.atan2(dx, dz) : fallback
}

function edgePhase(edge: ShuttlePathEdge, elapsed: number, source: ShuttlePathNodeStep, target: ShuttlePathNodeStep) {
  if (edge.edge_type === 'lift') {
    if (elapsed < edge.motion_start_seconds) return 'entering_lift' as const
    if (elapsed > edge.motion_end_seconds) return 'exiting_lift' as const
    return target.shuttle_level > source.shuttle_level ? 'moving_up' as const : 'moving_down' as const
  }
  return elapsed < edge.motion_start_seconds ? 'turning' as const : 'moving_track' as const
}

export function sampleShuttlePlan(
  plan: ShuttlePlanResponse | null,
  elapsedSeconds: number,
  playbackStatus: PlaybackStatus,
): ShuttlePlaybackSample | null {
  if (!plan || plan.result_status !== 'success' || !plan.path_nodes.length) return null
  const duration = plan.estimated_duration_seconds
  const elapsed = Math.max(0, Math.min(Number.isFinite(elapsedSeconds) ? elapsedSeconds : 0, duration))
  const finalNode = plan.path_nodes[plan.path_nodes.length - 1]
  if (!plan.path_edges.length || elapsed >= duration) {
    return {
      vehicleId: 'SHUTTLE-01', topologyId: plan.topology_id,
      trackNodeId: finalNode.track_node_id, shuttleLevel: finalNode.shuttle_level,
      row: finalNode.row, col: finalNode.col, pathIndex: plan.path_nodes.length - 1,
      elapsedSeconds: elapsed, world: centeredWorld(finalNode), headingRadians: 0,
      phase: elapsed >= duration && duration > 0 ? 'arrived' : 'idle', liftId: null, playbackStatus,
    }
  }
  const edgeIndex = Math.max(0, plan.path_edges.findIndex((edge) => elapsed <= edge.end_seconds + 1e-9))
  const edge = plan.path_edges[edgeIndex]
  const source = plan.path_nodes[edgeIndex]
  const target = plan.path_nodes[edgeIndex + 1]
  const motionDuration = edge.motion_end_seconds - edge.motion_start_seconds
  const alpha = motionDuration > 0 ? (elapsed - edge.motion_start_seconds) / motionDuration : 1
  const sourceWorld = centeredWorld(source)
  const targetWorld = centeredWorld(target)
  const clampedAlpha = elapsed <= edge.motion_start_seconds ? 0 : elapsed >= edge.motion_end_seconds ? 1 : alpha
  return {
    vehicleId: 'SHUTTLE-01', topologyId: plan.topology_id,
    trackNodeId: clampedAlpha >= 1 ? target.track_node_id : source.track_node_id,
    shuttleLevel: clampedAlpha >= 1 ? target.shuttle_level : source.shuttle_level,
    row: interpolate(source.row, target.row, clampedAlpha),
    col: interpolate(source.col, target.col, clampedAlpha),
    pathIndex: edgeIndex,
    elapsedSeconds: elapsed,
    world: [
      interpolate(sourceWorld[0], targetWorld[0], clampedAlpha),
      interpolate(sourceWorld[1], targetWorld[1], clampedAlpha),
      interpolate(sourceWorld[2], targetWorld[2], clampedAlpha),
    ],
    headingRadians: heading(source, target),
    phase: edgePhase(edge, elapsed, source, target),
    liftId: edge.lift_id,
    playbackStatus,
  }
}

export function shuttleCameraSample(sample: ShuttlePlaybackSample | null): TrajectorySample | null {
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

export const shuttleMotionLabel: Record<ShuttlePlaybackSample['phase'], string> = {
  idle: '待运行',
  turning: '换向准备',
  moving_track: '同层轨道行驶',
  entering_lift: '驶入提升机',
  moving_up: '提升机上升',
  moving_down: '提升机下降',
  exiting_lift: '驶出提升机',
  arrived: '已到达',
}
