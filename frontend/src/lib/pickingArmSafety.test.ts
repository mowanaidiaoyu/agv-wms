import { describe, expect, it } from 'vitest'
import type { PickingArmKinematics } from '../types'
import {
  findPickingArmCollisions,
  pickingArmJointPoints,
  solvePickingArmJoints,
} from './pickingArmSafety'

const kinematics: PickingArmKinematics = {
  upperArmLength: 5.3,
  forearmLength: 5.2,
  shoulderHeight: 4,
  baseRadius: 0.78,
  upperArmThickness: 0.52,
  forearmThickness: 0.48,
  jointRadius: 0.4,
  gripperScale: 1.6,
  safeTravelHeight: 7,
  linkCollisionRadius: 0.28,
}

describe('picking arm safety geometry', () => {
  it('solves the enlarged arm IK for both topology-derived endpoints', () => {
    expect(solvePickingArmJoints([-9, 1.12, -4], kinematics).reachable).toBe(true)
    expect(solvePickingArmJoints([3, 3.52, 4], kinematics).reachable).toBe(true)
  })

  it('uses the raised-elbow solution for safe travel', () => {
    const joints = solvePickingArmJoints([1.65, 7, 2.2], kinematics)
    const points = pickingArmJointPoints(joints, kinematics)
    expect(points.elbow[1]).toBeGreaterThan(3)
    expect(points.wrist[1]).toBeCloseTo(7, 3)
  })

  it('detects a major link crossing an expanded conveyor bounding box', () => {
    const points = pickingArmJointPoints(
      solvePickingArmJoints([6, 1.2, 0], kinematics),
      kinematics,
    )
    const collisions = findPickingArmCollisions(points, [{
      id: 'cell-r-test-c-test:L1',
      min: [-0.2, 3.8, -0.2],
      max: [0.2, 4.2, 0.2],
    }], kinematics.linkCollisionRadius)
    expect(collisions.length).toBeGreaterThan(0)
  })

  it('does not report a high safe-travel pose against the low deck', () => {
    const points = pickingArmJointPoints(
      solvePickingArmJoints([1.65, 7, 2.2], kinematics),
      kinematics,
    )
    expect(findPickingArmCollisions(points, [{
      id: 'low-conveyor',
      min: [-0.5, 0.45, -0.5],
      max: [0.5, 0.75, 0.5],
    }], kinematics.linkCollisionRadius)).toEqual([])
  })

  it('keeps the enlarged high-base arm clear of the critical nearby L2 conveyors', () => {
    const obstacles = [
      { id: 'cell-r87-c52:L2', min: [-5.48, 2.8, 0.52], max: [-4.52, 3.12, 1.48] },
      { id: 'cell-r86-c52:L2', min: [-5.48, 2.8, 1.52], max: [-4.52, 3.12, 2.48] },
      { id: 'cell-r86-c56:L2', min: [-1.48, 2.8, 1.52], max: [-0.52, 3.12, 2.48] },
    ]
    const waypoints: Array<[number, number, number]> = [
      [1.6, 6.4, 0.25],
      [-4.95, 7, -2.2],
      [-9, 1.12, -4],
      [-4.95, 7, -2.2],
      [1.65, 7, 2.2],
      [3, 3.52, 4],
      [1.65, 7, 2.2],
      [1.6, 6.4, 0.25],
    ]
    for (let segment = 1; segment < waypoints.length; segment += 1) {
      for (let sample = 0; sample <= 100; sample += 1) {
        const progress = sample / 100
        const target = waypoints[segment - 1].map((value, axis) => (
          value + (waypoints[segment][axis] - value) * progress
        )) as [number, number, number]
        const points = pickingArmJointPoints(solvePickingArmJoints(target, kinematics), kinematics)
        expect(findPickingArmCollisions(
          points,
          obstacles,
          kinematics.linkCollisionRadius,
        )).toEqual([])
      }
    }
  })
})
