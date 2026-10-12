import { MOUSE, type Vector3 } from 'three'

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
