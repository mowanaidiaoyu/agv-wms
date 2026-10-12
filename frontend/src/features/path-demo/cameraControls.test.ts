import { MOUSE, Vector3 } from 'three'
import { describe, expect, it } from 'vitest'
import { constrainCameraAboveGround, MIN_CAMERA_HEIGHT, PATH_DEMO_ORBIT_SETTINGS } from './cameraControls'

describe('path demo camera controls', () => {
  it('binds left rotate, wheel dolly, and right pan explicitly', () => {
    expect(PATH_DEMO_ORBIT_SETTINGS).toMatchObject({
      enableRotate: true,
      enableZoom: true,
      enablePan: true,
      mouseButtons: {
        LEFT: MOUSE.ROTATE,
        MIDDLE: MOUSE.DOLLY,
        RIGHT: MOUSE.PAN,
      },
    })
  })

  it('keeps the camera and orbit target above the warehouse floor', () => {
    const position = new Vector3(3, -2, 4)
    const target = new Vector3(0, -1, 0)
    expect(constrainCameraAboveGround(position, target)).toBe(true)
    expect(target.y).toBe(0)
    expect(position.y).toBe(MIN_CAMERA_HEIGHT)
    expect(constrainCameraAboveGround(position, target)).toBe(false)
  })
})
