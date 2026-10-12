import { MOUSE, Vector3 } from 'three'
import { describe, expect, it } from 'vitest'
import {
  constrainCameraAboveGround,
  createVehicleFocusPose,
  followVehicleTarget,
  MIN_CAMERA_HEIGHT,
  moveCameraAndTarget,
  PATH_DEMO_ORBIT_SETTINGS,
  shouldFollowVehicle,
} from './cameraControls'

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

  it('creates the locate pose from the authoritative rendered vehicle coordinate', () => {
    const pose = createVehicleFocusPose([31, 0.5, -17])
    expect(pose.target.toArray()).toEqual([31, 0.7, -17])
    expect(pose.cameraPosition.toArray()).toEqual([37, 5.7, -9])
  })

  it('follows vehicle movement without replacing the user camera offset', () => {
    const cameraPosition = new Vector3(8, 6, 10)
    const target = new Vector3(2, 1, 2)
    const originalOffset = cameraPosition.clone().sub(target)
    followVehicleTarget(cameraPosition, target, new Vector3(7, 1, 2), 1 / 60)
    cameraPosition.clone().sub(target).toArray().forEach((value, index) => {
      expect(value).toBeCloseTo(originalOffset.toArray()[index])
    })
    expect(target.x).toBeGreaterThan(2)
    expect(target.x).toBeLessThan(7)
  })

  it('uses delta-time smoothing and reaches a stable overview pose', () => {
    const cameraPosition = new Vector3(10, 10, 10)
    const target = new Vector3(5, 0, 5)
    const destinationCamera = new Vector3(20, 30, 20)
    const destinationTarget = new Vector3(0, 0, 0)
    expect(moveCameraAndTarget(cameraPosition, target, destinationCamera, destinationTarget, 0)).toBe(false)
    expect(cameraPosition.toArray()).toEqual([10, 10, 10])
    expect(moveCameraAndTarget(cameraPosition, target, destinationCamera, destinationTarget, 10)).toBe(true)
    expect(cameraPosition.toArray()).toEqual(destinationCamera.toArray())
    expect(target.toArray()).toEqual(destinationTarget.toArray())
  })

  it('freezes follow while paused and resumes without requiring a new mode', () => {
    expect(shouldFollowVehicle('follow', 'running', true)).toBe(true)
    expect(shouldFollowVehicle('follow', 'paused', true)).toBe(false)
    expect(shouldFollowVehicle('follow', 'completed', true)).toBe(true)
    expect(shouldFollowVehicle('free', 'running', true)).toBe(false)
    expect(shouldFollowVehicle('follow', 'running', false)).toBe(false)
  })
})
