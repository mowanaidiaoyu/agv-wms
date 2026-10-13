import { worldToCell } from '../../lib/topology3d'
import type { TopologyPayload } from '../../types'
import type { PathDemoCell } from './types'

export interface PointerPosition {
  x: number
  y: number
}

export function isClickGesture(start: PointerPosition, end: PointerPosition, thresholdPixels = 5): boolean {
  return Math.hypot(end.x - start.x, end.y - start.y) <= thresholdPixels
}

export function findRoadCellAtWorldPoint(
  x: number,
  z: number,
  topology: TopologyPayload,
  roadCells: PathDemoCell[],
  activeFloor: string,
): PathDemoCell | null {
  if (!Number.isFinite(x) || !Number.isFinite(z)) return null
  const { row, col } = worldToCell(x, z, topology.grid.cols, topology.grid.rows)
  return roadCells.find((cell) => (
    cell.floor === activeFloor
    && cell.row === row
    && cell.col === col
    && cell.type === 'road'
  )) ?? null
}
