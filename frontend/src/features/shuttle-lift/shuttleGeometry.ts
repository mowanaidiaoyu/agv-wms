import { SHUTTLE_TRACK_DECK_THICKNESS } from '../../lib/topology3d'

/** Local ShuttleVehicle geometry bounds relative to its group origin. */
export const SHUTTLE_VEHICLE_BOTTOM_BELOW_CENTER = 0.23
export const SHUTTLE_VEHICLE_TOP_ABOVE_CENTER = 0.31
export const SHUTTLE_VEHICLE_CENTER_HEIGHT = SHUTTLE_VEHICLE_BOTTOM_BELOW_CENTER
export const SHUTTLE_PATH_LINE_OFFSET = 0.08
export const SHUTTLE_ENDPOINT_MARKER_OFFSET = 0.1

export function shuttleTrackMeshCenterY(trackSurfaceY: number): number {
  return trackSurfaceY - SHUTTLE_TRACK_DECK_THICKNESS / 2
}

export function shuttleVehicleBounds(trackSurfaceY: number) {
  const centerY = trackSurfaceY + SHUTTLE_VEHICLE_CENTER_HEIGHT
  return {
    bottomY: centerY - SHUTTLE_VEHICLE_BOTTOM_BELOW_CENTER,
    centerY,
    topY: centerY + SHUTTLE_VEHICLE_TOP_ABOVE_CENTER,
  }
}

export function shuttleLiftShaftBounds(levelSurfaceYs: number[]) {
  if (!levelSurfaceYs.length || levelSurfaceYs.some((value) => !Number.isFinite(value))) return null
  const lowestSurfaceY = Math.min(...levelSurfaceYs)
  const highestSurfaceY = Math.max(...levelSurfaceYs)
  return {
    bottomY: lowestSurfaceY - 0.15,
    topY: shuttleVehicleBounds(highestSurfaceY).topY + 0.05,
  }
}
