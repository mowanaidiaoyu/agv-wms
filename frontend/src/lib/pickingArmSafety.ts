import type { PickingArmKinematics } from '../types'

export type Point3 = [number, number, number]

export interface PickingArmJoints {
  yaw: number
  shoulder: number
  elbow: number
  reachable: boolean
}

export interface ArmJointPoints {
  shoulder: Point3
  elbow: Point3
  wrist: Point3
}

export interface CollisionBox {
  id: string
  min: Point3
  max: Point3
}

export interface ArmCollision {
  link: 'upper_arm' | 'forearm'
  obstacleId: string
}

export function solvePickingArmJoints(
  target: Point3,
  kinematics: PickingArmKinematics,
): PickingArmJoints {
  const [x, y, z] = target
  const radius = Math.hypot(x, z)
  const vertical = y - kinematics.shoulderHeight
  const distance = Math.hypot(radius, vertical)
  const maximum = kinematics.upperArmLength + kinematics.forearmLength
  const minimum = Math.abs(kinematics.upperArmLength - kinematics.forearmLength)
  const solvedDistance = Math.min(maximum - 1e-4, Math.max(minimum + 1e-4, distance))
  const elbowCos = Math.max(-1, Math.min(1, (
    solvedDistance ** 2
      - kinematics.upperArmLength ** 2
      - kinematics.forearmLength ** 2
  ) / (2 * kinematics.upperArmLength * kinematics.forearmLength)))
  // Negative elbow chooses the raised-elbow solution so the links clear the conveyor deck.
  const elbow = -Math.acos(elbowCos)
  const shoulder = Math.atan2(vertical, radius) - Math.atan2(
    kinematics.forearmLength * Math.sin(elbow),
    kinematics.upperArmLength + kinematics.forearmLength * Math.cos(elbow),
  )
  return {
    yaw: Math.atan2(-z, x),
    shoulder,
    elbow,
    reachable: distance <= maximum + 1e-6 && distance >= minimum - 1e-6,
  }
}

export function pickingArmJointPoints(
  joints: PickingArmJoints,
  kinematics: PickingArmKinematics,
): ArmJointPoints {
  const radialToPoint = (radial: number, height: number): Point3 => [
    Math.cos(joints.yaw) * radial,
    height,
    -Math.sin(joints.yaw) * radial,
  ]
  const elbowRadial = Math.cos(joints.shoulder) * kinematics.upperArmLength
  const elbowHeight = kinematics.shoulderHeight
    + Math.sin(joints.shoulder) * kinematics.upperArmLength
  const wristAngle = joints.shoulder + joints.elbow
  const wristRadial = elbowRadial + Math.cos(wristAngle) * kinematics.forearmLength
  const wristHeight = elbowHeight + Math.sin(wristAngle) * kinematics.forearmLength
  return {
    shoulder: [0, kinematics.shoulderHeight, 0],
    elbow: radialToPoint(elbowRadial, elbowHeight),
    wrist: radialToPoint(wristRadial, wristHeight),
  }
}

function segmentIntersectsBox(from: Point3, to: Point3, box: CollisionBox, radius: number): boolean {
  let near = 0
  let far = 1
  for (let axis = 0; axis < 3; axis += 1) {
    const delta = to[axis] - from[axis]
    const minimum = box.min[axis] - radius
    const maximum = box.max[axis] + radius
    if (Math.abs(delta) < 1e-9) {
      if (from[axis] < minimum || from[axis] > maximum) return false
      continue
    }
    const first = (minimum - from[axis]) / delta
    const second = (maximum - from[axis]) / delta
    near = Math.max(near, Math.min(first, second))
    far = Math.min(far, Math.max(first, second))
    if (near > far) return false
  }
  return true
}

export function findPickingArmCollisions(
  points: ArmJointPoints,
  obstacles: CollisionBox[],
  linkRadius: number,
): ArmCollision[] {
  const links = [
    { link: 'upper_arm' as const, from: points.shoulder, to: points.elbow },
    { link: 'forearm' as const, from: points.elbow, to: points.wrist },
  ]
  return links.flatMap(({ link, from, to }) => obstacles
    .filter((obstacle) => segmentIntersectsBox(from, to, obstacle, linkRadius))
    .map((obstacle) => ({ link, obstacleId: obstacle.id })))
}
