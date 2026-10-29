import { Html } from '@react-three/drei'
import { useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Group, Mesh } from 'three'
import {
  advancePickingArm,
  createInitialPickingArmState,
  pickingArmPose,
  resetPickingArmState,
  toteStatusFor,
  type PickingArmPhase,
  type PickingArmSignals,
} from '../../lib/pickingArmMotion'
import { validateServerPickingArmPath } from '../../lib/pickingArmConveyor'
import {
  findPickingArmCollisions,
  pickingArmJointPoints,
  solvePickingArmJoints,
  type CollisionBox,
} from '../../lib/pickingArmSafety'
import { worldX, worldZ } from '../../lib/topology3d'
import type { PickingArmLayout, TopologyPayload } from '../../types'

export interface PickingArmCargo {
  toteId: string
  orderId: string
  skuId: string
  pickedQuantity: number
}

export interface PickingArmHandoffProps {
  topology: TopologyPayload
  layout: PickingArmLayout
  cargo: PickingArmCargo
  preview: boolean
  previewCycleToken: string
  paused: boolean
  speedMultiplier: 1 | 2 | 4
  resetRequest: number
  authoritativeSignals?: Partial<PickingArmSignals>
}

const TOTE_HEIGHT = 0.34

function OrderTote({ cargo, status }: { cargo: PickingArmCargo; status: string }) {
  return (
    <group>
      <mesh castShadow receiveShadow>
        <boxGeometry args={[0.58, TOTE_HEIGHT, 0.46]} />
        <meshStandardMaterial color="#f3f4f6" roughness={0.58} metalness={0.08} />
      </mesh>
      <mesh position={[0, 0.04, 0.237]}>
        <boxGeometry args={[0.48, 0.16, 0.012]} />
        <meshStandardMaterial color="#2563eb" emissive="#1d4ed8" emissiveIntensity={0.12} />
      </mesh>
      <Html position={[0, 0.62, 0]} center distanceFactor={8} zIndexRange={[24, 18]}>
        <div className="picking-arm-tote-label">
          <strong>{cargo.orderId}</strong>
          <span>{cargo.skuId}</span>
          <span>已拣选 {cargo.pickedQuantity} 件</span>
          <small>{status}</small>
        </div>
      </Html>
    </group>
  )
}

export function PickingArmHandoff({
  topology,
  layout,
  cargo,
  preview,
  previewCycleToken,
  paused,
  speedMultiplier,
  resetRequest,
  authoritativeSignals,
}: PickingArmHandoffProps) {
  const waistRef = useRef<Group>(null)
  const shoulderRef = useRef<Group>(null)
  const elbowRef = useRef<Group>(null)
  const wristRef = useRef<Group>(null)
  const leftFingerRef = useRef<Mesh>(null)
  const rightFingerRef = useRef<Mesh>(null)
  const toteRef = useRef<Group>(null)
  const stateRef = useRef(createInitialPickingArmState())
  const [phase, setPhase] = useState<PickingArmPhase>('idle')
  const [cargoPlaybackState, setCargoPlaybackState] = useState('waiting_for_release')
  const [collisionMessage, setCollisionMessage] = useState<string | null>(null)
  const collisionMessageRef = useRef<string | null>(null)
  const invalidate = useThree((state) => state.invalidate)
  const { rows, cols } = topology.grid
  const base = useMemo<[number, number, number]>(() => [
    worldX(layout.installation.col, cols),
    layout.mountHeight,
    worldZ(layout.installation.row, rows),
  ], [cols, layout.installation.col, layout.installation.row, layout.mountHeight, rows])
  const pickup = useMemo<[number, number, number]>(() => [
    worldX(layout.pickup.position.col, cols) - base[0],
    layout.pickup.beltHeight + TOTE_HEIGHT + 0.18,
    worldZ(layout.pickup.position.row, rows) - base[2],
  ], [base, cols, layout.pickup.beltHeight, layout.pickup.position.col, layout.pickup.position.row, rows])
  const dropoff = useMemo<[number, number, number]>(() => [
    worldX(layout.dropoff.position.col, cols) - base[0],
    layout.dropoff.beltHeight + TOTE_HEIGHT + 0.18,
    worldZ(layout.dropoff.position.row, rows) - base[2],
  ], [base, cols, layout.dropoff.beltHeight, layout.dropoff.position.col, layout.dropoff.position.row, rows])
  const points = useMemo(() => ({
    home: [1.4, 6.0, 0.25] as [number, number, number],
    pickup,
    pickupLift: [pickup[0] * 0.55, layout.kinematics.safeTravelHeight, pickup[2] * 0.55] as [number, number, number],
    dropLift: [dropoff[0] * 0.55, layout.kinematics.safeTravelHeight, dropoff[2] * 0.55] as [number, number, number],
    dropoff,
  }), [dropoff, layout.kinematics.safeTravelHeight, pickup])
  const collisionObstacles = useMemo<CollisionBox[]>(() => topology.cells.flatMap((cell) => {
    if (cell.type !== 'conveyor') return []
    const x = worldX(cell.col, cols) - base[0]
    const z = worldZ(cell.row, rows) - base[2]
    if (Math.hypot(x, z) > layout.workRadius + 1.5) return []
    const levels = cell.conveyorGround ? [1] : Array.from(
      { length: Math.max(1, cell.levels ?? 1) },
      (_, index) => index + 1,
    )
    return levels.map((level) => {
      const beltHeight = cell.conveyorGround ? 0.6 : 1.5 + (level - 1) * 1.5
      return {
        id: `${cell.id}:L${level}`,
        min: [x - 0.48, beltHeight - 0.2, z - 0.48],
        max: [x + 0.48, beltHeight + 0.12, z + 0.48],
      } as CollisionBox
    })
  }), [base, cols, layout.workRadius, rows, topology.cells])
  const conveyorRoute = useMemo(() => {
    const verified = validateServerPickingArmPath(
      topology,
      layout.dropoff.topologyCellId ?? '',
      layout.dropoff.position.level,
      layout.previewConveyorPath,
    )
    const routePoints = verified.steps.map((step) => [
      worldX(step.position.col, cols) - base[0],
      step.beltHeight + TOTE_HEIGHT / 2,
      worldZ(step.position.row, rows) - base[2],
    ] as [number, number, number])
    const segments: Array<{
      from: [number, number, number]
      to: [number, number, number]
      start: number
      length: number
    }> = []
    let total = 0
    for (let index = 1; index < routePoints.length; index += 1) {
      const from = routePoints[index - 1]
      const to = routePoints[index]
      const length = Math.hypot(to[0] - from[0], to[1] - from[1], to[2] - from[2])
      segments.push({ from, to, start: total, length })
      total += length
    }
    return { verified, routePoints, segments, total }
  }, [base, cols, layout.dropoff.position.level, layout.dropoff.topologyCellId, layout.previewConveyorPath, rows, topology])
  const signals = useMemo<PickingArmSignals>(() => preview
    ? {
        cargoToken: previewCycleToken,
        pickedGoodsReady: true,
        outputPositionAvailable: true,
        legalPickupNode: layout.pickup.validationStatus !== 'BLOCKED',
        legalDropoffNode: layout.dropoff.validationStatus !== 'BLOCKED',
        authoritativeHandoffCompleted: false,
      }
    : {
        cargoToken: authoritativeSignals?.cargoToken ?? null,
        pickedGoodsReady: authoritativeSignals?.pickedGoodsReady ?? false,
        outputPositionAvailable: authoritativeSignals?.outputPositionAvailable ?? false,
        legalPickupNode: authoritativeSignals?.legalPickupNode ?? false,
        legalDropoffNode: authoritativeSignals?.legalDropoffNode ?? false,
        authoritativeHandoffCompleted: authoritativeSignals?.authoritativeHandoffCompleted ?? false,
      }, [authoritativeSignals, layout.dropoff.validationStatus, layout.pickup.validationStatus, preview, previewCycleToken])

  useEffect(() => {
    stateRef.current = resetPickingArmState(stateRef.current, signals)
    setPhase('idle')
    setCargoPlaybackState('waiting_for_release')
    collisionMessageRef.current = null
    setCollisionMessage(null)
    invalidate()
  }, [invalidate, resetRequest])

  useEffect(() => {
    invalidate()
  }, [invalidate, paused, previewCycleToken, speedMultiplier])

  useFrame((_, delta) => {
    const previousState = stateRef.current
    const previousPhase = stateRef.current.phase
    const previousCargoState = stateRef.current.cargoPlaybackState
    const candidateState = advancePickingArm(stateRef.current, {
      deltaSeconds: delta,
      speedMultiplier,
      paused,
      signals,
      conveyorPathLength: conveyorRoute.verified.status === 'PASS' ? conveyorRoute.total : 0,
      conveyorSpeed: 1.35,
    })
    let pose = pickingArmPose(candidateState, points)
    let joints = solvePickingArmJoints(pose.target, layout.kinematics)
    const candidateReachable = joints.reachable
    const collisions = findPickingArmCollisions(
      pickingArmJointPoints(joints, layout.kinematics),
      collisionObstacles,
      layout.kinematics.linkCollisionRadius,
    )
    const movingPhase = !['idle', 'grip', 'release'].includes(candidateState.phase)
    if (movingPhase && (!candidateReachable || collisions.length > 0)) {
      stateRef.current = previousState
      pose = pickingArmPose(previousState, points)
      joints = solvePickingArmJoints(pose.target, layout.kinematics)
      const message = !candidateReachable
        ? 'IK_BLOCKED'
        : `${collisions[0].link}:${collisions[0].obstacleId}`
      if (collisionMessageRef.current !== message) {
        collisionMessageRef.current = message
        setCollisionMessage(message)
      }
    } else {
      stateRef.current = candidateState
      if (collisionMessageRef.current !== null) {
        collisionMessageRef.current = null
        setCollisionMessage(null)
      }
    }
    if (stateRef.current.phase !== previousPhase) setPhase(stateRef.current.phase)
    if (stateRef.current.cargoPlaybackState !== previousCargoState) {
      setCargoPlaybackState(stateRef.current.cargoPlaybackState)
    }
    if (waistRef.current) waistRef.current.rotation.y = joints.yaw
    if (shoulderRef.current) shoulderRef.current.rotation.z = joints.shoulder
    if (elbowRef.current) elbowRef.current.rotation.z = joints.elbow
    if (wristRef.current) wristRef.current.rotation.z = -(joints.shoulder + joints.elbow)
    if (leftFingerRef.current) leftFingerRef.current.position.y = pose.gripperGap
    if (rightFingerRef.current) rightFingerRef.current.position.y = -pose.gripperGap

    const tote = toteRef.current
    if (tote) {
      tote.visible = stateRef.current.totePlacement !== 'none'
      if (stateRef.current.totePlacement === 'pickup') {
        tote.position.set(pickup[0], layout.pickup.beltHeight + TOTE_HEIGHT / 2, pickup[2])
      } else if (stateRef.current.totePlacement === 'gripper') {
        tote.position.set(pose.target[0], pose.target[1] - 0.36, pose.target[2])
      } else if (stateRef.current.totePlacement === 'output') {
        const distance = stateRef.current.conveyorDistance
        const segment = conveyorRoute.segments.find((item) => distance <= item.start + item.length + 1e-9)
        if (segment && segment.length > 0) {
          const progress = Math.max(0, Math.min(1, (distance - segment.start) / segment.length))
          tote.position.set(
            segment.from[0] + (segment.to[0] - segment.from[0]) * progress,
            segment.from[1] + (segment.to[1] - segment.from[1]) * progress,
            segment.from[2] + (segment.to[2] - segment.from[2]) * progress,
          )
        } else {
          const endpoint = conveyorRoute.routePoints[conveyorRoute.routePoints.length - 1] ?? dropoff
          tote.position.set(endpoint[0], endpoint[1], endpoint[2])
        }
      }
    }
    if (!paused && (
      stateRef.current.phase !== 'idle'
      || stateRef.current.cargoPlaybackState === 'conveyor_moving'
    )) invalidate()
  })

  return (
    <group position={base}>
      <mesh position={[0, 0.035, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[layout.workRadius, 48]} />
        <meshBasicMaterial color="#f59e0b" transparent opacity={0.055} depthWrite={false} />
      </mesh>
      <mesh position={[0, 0.13, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[layout.kinematics.baseRadius, layout.kinematics.baseRadius * 1.14, 0.34, 28]} />
        <meshStandardMaterial color="#1f2937" roughness={0.42} metalness={0.65} />
      </mesh>
      <mesh position={[0, layout.kinematics.shoulderHeight / 2, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[
          layout.kinematics.baseRadius * 0.56,
          layout.kinematics.baseRadius * 0.72,
          Math.max(0.5, layout.kinematics.shoulderHeight - 0.28),
          24,
        ]} />
        <meshStandardMaterial color="#d97706" roughness={0.42} metalness={0.48} />
      </mesh>
      <group ref={waistRef} position={[0, layout.kinematics.shoulderHeight, 0]}>
        <mesh position={[0, -0.2, 0]} castShadow>
          <cylinderGeometry args={[layout.kinematics.jointRadius, layout.kinematics.jointRadius * 1.18, 0.56, 24]} />
          <meshStandardMaterial color="#f59e0b" emissive="#92400e" emissiveIntensity={0.12} roughness={0.38} metalness={0.42} />
        </mesh>
        <group ref={shoulderRef}>
          <mesh rotation={[Math.PI / 2, 0, 0]} castShadow>
            <cylinderGeometry args={[layout.kinematics.jointRadius, layout.kinematics.jointRadius, 0.62, 20]} />
            <meshStandardMaterial color="#111827" roughness={0.32} metalness={0.7} />
          </mesh>
          <mesh position={[layout.kinematics.upperArmLength / 2, 0, 0]} castShadow>
            <boxGeometry args={[layout.kinematics.upperArmLength, layout.kinematics.upperArmThickness, layout.kinematics.upperArmThickness * 1.08]} />
            <meshStandardMaterial color="#fbbf24" emissive="#92400e" emissiveIntensity={0.08} roughness={0.4} metalness={0.35} />
          </mesh>
          <group ref={elbowRef} position={[layout.kinematics.upperArmLength, 0, 0]}>
            <mesh rotation={[Math.PI / 2, 0, 0]} castShadow>
              <cylinderGeometry args={[layout.kinematics.jointRadius * 0.92, layout.kinematics.jointRadius * 0.92, 0.56, 20]} />
              <meshStandardMaterial color="#111827" roughness={0.32} metalness={0.7} />
            </mesh>
            <mesh position={[layout.kinematics.forearmLength / 2, 0, 0]} castShadow>
              <boxGeometry args={[layout.kinematics.forearmLength, layout.kinematics.forearmThickness, layout.kinematics.forearmThickness * 1.08]} />
              <meshStandardMaterial color="#f59e0b" emissive="#92400e" emissiveIntensity={0.08} roughness={0.4} metalness={0.38} />
            </mesh>
            <group ref={wristRef} position={[layout.kinematics.forearmLength, 0, 0]} scale={layout.kinematics.gripperScale}>
              <mesh rotation={[Math.PI / 2, 0, 0]} castShadow>
                <cylinderGeometry args={[0.16, 0.16, 0.3, 18]} />
                <meshStandardMaterial color="#374151" roughness={0.3} metalness={0.75} />
              </mesh>
              <mesh position={[0.18, 0, 0]} castShadow>
                <boxGeometry args={[0.36, 0.18, 0.28]} />
                <meshStandardMaterial color="#111827" roughness={0.32} metalness={0.7} />
              </mesh>
              <mesh ref={leftFingerRef} position={[0.35, 0.24, 0]} castShadow>
                <boxGeometry args={[0.28, 0.08, 0.08]} />
                <meshStandardMaterial color="#9ca3af" metalness={0.82} roughness={0.25} />
              </mesh>
              <mesh ref={rightFingerRef} position={[0.35, -0.24, 0]} castShadow>
                <boxGeometry args={[0.28, 0.08, 0.08]} />
                <meshStandardMaterial color="#9ca3af" metalness={0.82} roughness={0.25} />
              </mesh>
            </group>
          </group>
        </group>
      </group>
      <group ref={toteRef} visible={false}>
        <OrderTote cargo={cargo} status={toteStatusFor(stateRef.current)} />
      </group>
      <Html position={[0, 4.15, 0]} center distanceFactor={11} zIndexRange={[22, 16]}>
        <div className="picking-arm-device-label">
          <strong>{layout.armId}</strong>
          <span>{preview ? `预览 · ${phase}` : `权威模式 · ${phase}`}</span>
          <span>{cargoPlaybackState}</span>
          {collisionMessage ? <span className="danger">碰撞阻止：{collisionMessage}</span> : null}
          <small>proposed / 不代表已交付</small>
        </div>
      </Html>
    </group>
  )
}
