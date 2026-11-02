import type { SimulationTestPosition, SimulationTestRunState } from '../types'
import {
  classifyShuttleTrackSegment,
  isLegalShuttleTrackSegment,
  shuttleYawForSegment,
  type ShuttleTrackSegmentKind,
} from './shuttleTrackMotion'
import type { SimulationPoint } from './simulationTestVisualization'

export interface ConveyorVisualWaypoint {
  nodeId: string
  position: [number, number, number]
}

export function groundConveyorWaypoints(
  conveyor: SimulationTestRunState['conveyor'],
): ConveyorVisualWaypoint[] {
  const beltHeight = conveyor.beltHeight ?? 0.6
  return conveyor.path.map((nodeId) => {
    const [, rowText, colText] = nodeId.split(':')
    const row = Number(rowText)
    const col = Number(colText)
    return {
      nodeId,
      position: [
        col + 1 - conveyor.gridCols / 2,
        beltHeight,
        conveyor.gridRows / 2 - (row + 1),
      ],
    }
  })
}

export function upperConveyorWaypoints(
  path: SimulationTestRunState['mergeConveyor']['path'],
): ConveyorVisualWaypoint[] {
  return path.map((step: SimulationTestPosition) => ({
    nodeId: step.nodeId,
    position: [step.worldX, step.worldY, step.worldZ],
  }))
}

export function collectConfirmedConveyorWaypoints(
  path: ConveyorVisualWaypoint[],
  previousIndex: number,
  confirmedIndex: number,
): ConveyorVisualWaypoint[] {
  if (!path.length || confirmedIndex <= previousIndex) return []
  const start = Math.max(0, previousIndex + 1)
  const end = Math.min(confirmedIndex, path.length - 1)
  return path.slice(start, end + 1)
}

export function validateConveyorWaypointPath(
  path: ConveyorVisualWaypoint[],
): { status: 'PASS' | 'BLOCKED'; stopCode?: string; index?: number } {
  for (let index = 1; index < path.length; index += 1) {
    const previous = path[index - 1].position
    const current = path[index].position
    const kind = classifyShuttleTrackSegment(previous, current)
    if (!isLegalShuttleTrackSegment(kind) || kind === 'lift') {
      return { status: 'BLOCKED', stopCode: kind, index }
    }
  }
  return { status: 'PASS' }
}

export function conveyorYawForSegment(
  currentYaw: number,
  from: SimulationPoint,
  to: SimulationPoint,
): number {
  return shuttleYawForSegment(currentYaw, from, to)
}

export function conveyorSegmentKind(
  from: SimulationPoint,
  to: SimulationPoint,
): ShuttleTrackSegmentKind {
  return classifyShuttleTrackSegment(from, to)
}

export function boundedConveyorCatchUp(backlog: number): number {
  return Math.min(1.5, 1 + Math.max(0, backlog - 2) * 0.08)
}
