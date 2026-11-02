import { describe, expect, it } from 'vitest'
import type { PickingArmKinematics } from '../types'
import {
  findPickingArmCollisions,
  pickingArmJointPoints,
  solvePickingArmJoints,
} from './pickingArmSafety'

const kinematics: PickingArmKinematics = {
  upperArmLength: 4,
  forearmLength: 3.9,
  shoulderHeight: 4,
  baseRadius: 0.68,
  upperArmThickness: 0.42,
  forearmThickness: 0.38,
  jointRadius: 0.32,
  gripperScale: 1.45,
  safeTravelHeight: 6.3,
  linkCollisionRadius: 0.24,
}

describe('picking arm safety geometry', () => {
  it('reaches both topology-derived endpoints with the enlarged model', () => {
    expect(solvePickingArmJoints([-6, 1.12, -1], kinematics).reachable).toBe(true)
    expect(solvePickingArmJoints([-4, 3.52, 5], kinematics).reachable).toBe(true)
  })

  it('uses a raised elbow at the safe travel height', () => {
    const points = pickingArmJointPoints(
      solvePickingArmJoints([-2.2, 6.3, 2.75], kinematics),
      kinematics,
    )
    expect(points.elbow[1]).toBeGreaterThan(3)
    expect(points.wrist[1]).toBeCloseTo(6.3, 3)
  })

  it('blocks a link that would pass through a conveyor deck', () => {
    const points = pickingArmJointPoints(
      solvePickingArmJoints([6, 1.2, 0], kinematics),
      kinematics,
    )
    expect(findPickingArmCollisions(points, [{
      id: 'conveyor:L2',
      min: [-0.2, 3.8, -0.2],
      max: [0.2, 4.2, 0.2],
    }], kinematics.linkCollisionRadius)).not.toEqual([])
  })

  it('keeps the tuned waypoints clear of nearby L2 conveyors', () => {
    const obstacles = [
      { id: 'cell-r87-c54:L2', min: [-0.48, 2.8, 3.52] as [number, number, number], max: [0.48, 3.12, 4.48] as [number, number, number] },
      { id: 'cell-r91-c60:L2', min: [5.52, 2.8, -0.48] as [number, number, number], max: [6.48, 3.12, 0.48] as [number, number, number] },
      { id: 'cell-r86-c50:L2', min: [-4.48, 2.8, 4.52] as [number, number, number], max: [-3.52, 3.12, 5.48] as [number, number, number] },
    ]
    const waypoints: Array<[number, number, number]> = [
      [1.4, 6, 0.25], [-3.3, 6.3, -0.55], [-6, 1.12, -1],
      [-3.3, 6.3, -0.55], [-2.2, 6.3, 2.75], [-4, 3.52, 5],
      [-2.2, 6.3, 2.75], [1.4, 6, 0.25],
    ]
    for (let segment = 1; segment < waypoints.length; segment += 1) {
      for (let sample = 0; sample <= 25; sample += 1) {
        const progress = sample / 25
        const target = waypoints[segment - 1].map((value, axis) => (
          value + (waypoints[segment][axis] - value) * progress
        )) as [number, number, number]
        const points = pickingArmJointPoints(solvePickingArmJoints(target, kinematics), kinematics)
        expect(findPickingArmCollisions(points, obstacles, kinematics.linkCollisionRadius)).toEqual([])
      }
    }
  })
})
