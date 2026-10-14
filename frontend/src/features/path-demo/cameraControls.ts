import { MOUSE, Vector3 } from 'three'
import type { PlaybackStatus } from './types'

export type CameraViewMode = 'free' | 'follow'
export type CameraAction = 'locate' | 'overview' | 'switch-follow-target' | 'focus-point'

export interface CameraActionRequest {
  type: CameraAction
  sequence: number
  targetWorld?: [number, number, number]
}

export const PATH_DEMO_ORBIT_SETTINGS = {
  enableRotate: true,
  enableZoom: true,
  enablePan: true,
  mouseButtons: {
    LEFT: MOUSE.ROTATE,
    MIDDLE: MOUSE.DOLLY,
    RIGHT: MOUSE.PAN,
  },
} as const

export const MIN_CAMERA_HEIGHT = 0.25
export const VEHICLE_TARGET_HEIGHT = 0.2
export const CAMERA_TRANSITION_RATE = 5
export const CAMERA_FOLLOW_RATE = 8
export const DEFAULT_VEHICLE_FOCUS_OFFSET: [number, number, number] = [6, 5, 8]
export const FOLLOW_ENTRY_DISTANCE = 12

export function createPreservedFollowOffset(cameraPosition: Vector3, target: Vector3): Vector3 {
  const offset = cameraPosition.clone().sub(target)
  if (!isFiniteVector(offset) || offset.lengthSq() < 0.0001) {
    return new Vector3(...DEFAULT_VEHICLE_FOCUS_OFFSET)
  }
  return offset
}

export function smoothingAlpha(rate: number, deltaSeconds: number): number {
  if (!Number.isFinite(rate) || !Number.isFinite(deltaSeconds) || rate <= 0 || deltaSeconds <= 0) return 0
  return 1 - Math.exp(-rate * deltaSeconds)
}

export function isFiniteVector(vector: Vector3): boolean {
  return Number.isFinite(vector.x) && Number.isFinite(vector.y) && Number.isFinite(vector.z)
}

export function shouldFollowVehicle(
  viewMode: CameraViewMode,
  playbackStatus: PlaybackStatus,
  hasVehiclePosition: boolean,
): boolean {
  return viewMode === 'follow' && hasVehiclePosition && playbackStatus !== 'paused'
}

export function moveCameraAndTarget(
  cameraPosition: Vector3,
  target: Vector3,
  desiredCameraPosition: Vector3,
  desiredTarget: Vector3,
  deltaSeconds: number,
  rate = CAMERA_TRANSITION_RATE,
): boolean {
  if (!isFiniteVector(desiredCameraPosition) || !isFiniteVector(desiredTarget)) return true
  const alpha = smoothingAlpha(rate, deltaSeconds)
  cameraPosition.lerp(desiredCameraPosition, alpha)
  target.lerp(desiredTarget, alpha)
  const done = cameraPosition.distanceToSquared(desiredCameraPosition) < 0.0004
    && target.distanceToSquared(desiredTarget) < 0.0004
  if (done) {
    cameraPosition.copy(desiredCameraPosition)
    target.copy(desiredTarget)
  }
  return done
}

export function followVehicleTarget(
  cameraPosition: Vector3,
  target: Vector3,
  vehicleTarget: Vector3,
  deltaSeconds: number,
): void {
  if (!isFiniteVector(vehicleTarget)) return
  const alpha = smoothingAlpha(CAMERA_FOLLOW_RATE, deltaSeconds)
  const x = (vehicleTarget.x - target.x) * alpha
  const y = (vehicleTarget.y - target.y) * alpha
  const z = (vehicleTarget.z - target.z) * alpha
  cameraPosition.set(cameraPosition.x + x, cameraPosition.y + y, cameraPosition.z + z)
  target.set(target.x + x, target.y + y, target.z + z)
}

export function createVehicleFocusPose(vehicleWorld: readonly [number, number, number]): {
  cameraPosition: Vector3
  target: Vector3
} {
  const target = new Vector3(...vehicleWorld)
  target.y += VEHICLE_TARGET_HEIGHT
  const cameraPosition = target.clone().add(new Vector3(...DEFAULT_VEHICLE_FOCUS_OFFSET))
  return { cameraPosition, target }
}

export function constrainCameraAboveGround(cameraPosition: Vector3, target: Vector3): boolean {
  let changed = false
  if (target.y < 0) {
    cameraPosition.y -= target.y
    target.y = 0
    changed = true
  }
  if (cameraPosition.y < MIN_CAMERA_HEIGHT) {
    cameraPosition.y = MIN_CAMERA_HEIGHT
    changed = true
  }
  return changed
}
