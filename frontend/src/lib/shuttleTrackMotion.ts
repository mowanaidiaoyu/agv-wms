import type { SimulationTestShuttleFrame } from '../types'
import { simulationMotionYaw, type SimulationPoint } from './simulationTestVisualization'

const EPSILON = 1e-6

export type ShuttleTrackSegmentKind =
  | 'stationary'
  | 'row'
  | 'column'
  | 'lift'
  | 'invalid_diagonal'
  | 'invalid_lift_drift'

export interface ShuttleVisualWaypoint {
  nodeId: string
  level: number
  stage: SimulationTestShuttleFrame['stage']
  position: [number, number, number]
}

export function frameToShuttleWaypoint(frame: SimulationTestShuttleFrame): ShuttleVisualWaypoint {
  return {
    nodeId: frame.nodeId,
    level: frame.position.level,
    stage: frame.stage,
    position: [frame.position.worldX, frame.position.worldY, frame.position.worldZ],
  }
}

export function classifyShuttleTrackSegment(
  from: SimulationPoint,
  to: SimulationPoint,
): ShuttleTrackSegmentKind {
  const dx = Math.abs(to[0] - from[0])
  const dy = Math.abs(to[1] - from[1])
  const dz = Math.abs(to[2] - from[2])
  const changesX = dx > EPSILON
  const changesY = dy > EPSILON
  const changesZ = dz > EPSILON
  if (changesY && (changesX || changesZ)) return 'invalid_lift_drift'
  if (changesX && changesZ) return 'invalid_diagonal'
  if (changesY) return 'lift'
  if (changesX) return 'column'
  if (changesZ) return 'row'
  return 'stationary'
}

export function isLegalShuttleTrackSegment(kind: ShuttleTrackSegmentKind): boolean {
  return kind !== 'invalid_diagonal' && kind !== 'invalid_lift_drift'
}

export function shuttleYawForSegment(
  currentYaw: number,
  from: SimulationPoint,
  to: SimulationPoint,
): number {
  const kind = classifyShuttleTrackSegment(from, to)
  if (kind === 'row' || kind === 'column') return simulationMotionYaw(currentYaw, from, to)
  return currentYaw
}

export function collectConfirmedShuttleWaypoints(
  frames: SimulationTestShuttleFrame[],
  previousIndex: number,
  confirmedIndex: number,
): ShuttleVisualWaypoint[] {
  if (confirmedIndex <= previousIndex || !frames.length) return []
  const start = Math.max(0, previousIndex + 1)
  const end = Math.min(confirmedIndex, frames.length - 1)
  return frames.slice(start, end + 1).map(frameToShuttleWaypoint)
}

export function headingAtShuttleFrame(
  frames: SimulationTestShuttleFrame[],
  index: number,
  fallback = 0,
): number {
  if (!frames.length) return fallback
  const bounded = Math.max(0, Math.min(index, frames.length - 1))
  for (let cursor = bounded; cursor > 0; cursor -= 1) {
    const from = frameToShuttleWaypoint(frames[cursor - 1]).position
    const to = frameToShuttleWaypoint(frames[cursor]).position
    const kind = classifyShuttleTrackSegment(from, to)
    if (kind === 'row' || kind === 'column') return shuttleYawForSegment(fallback, from, to)
  }
  for (let cursor = bounded; cursor < frames.length - 1; cursor += 1) {
    const from = frameToShuttleWaypoint(frames[cursor]).position
    const to = frameToShuttleWaypoint(frames[cursor + 1]).position
    const kind = classifyShuttleTrackSegment(from, to)
    if (kind === 'row' || kind === 'column') return shuttleYawForSegment(fallback, from, to)
  }
  return fallback
}

export function shuttleDirectionLabel(kind: ShuttleTrackSegmentKind, yaw: number): string {
  if (kind === 'lift') return 'lift'
  if (kind === 'stationary') return 'stopped'
  if (!isLegalShuttleTrackSegment(kind)) return kind
  const normalized = ((yaw % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2)
  if (Math.abs(normalized - 0) < 0.01 || Math.abs(normalized - Math.PI * 2) < 0.01) return '-row'
  if (Math.abs(normalized - Math.PI / 2) < 0.01) return '+col'
  if (Math.abs(normalized - Math.PI) < 0.01) return '+row'
  return '-col'
}
