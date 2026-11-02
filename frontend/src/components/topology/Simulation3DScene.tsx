import { memo, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Html, Line, OrbitControls } from '@react-three/drei'
import { Group, MOUSE, Mesh, Vector3 } from 'three'
import { BatchedTopologyMeshes, GoodsBoxes, SlabView, TowerView, ZoomToCursor } from './SceneMeshes'
import { PickingArmHandoff, type PickingArmCargo } from './PickingArmHandoff'
import {
  CELL,
  CONVEYOR_DECK_BASE,
  CONVEYOR_DECK_STEP,
  CONVEYOR_GROUND_BELT_Y,
  CTU_ROBOT_LEVELS,
  DIRECTION_VEC3,
  FLOOR_HEIGHT,
  GOODS_CATEGORY_COLORS,
  GOODS_DEFAULT_COLOR,
  SHUTTLE_RACK_BASE_Y,
  ZONE_LEVEL_HEIGHT,
  buildGoodsSceneBoxes,
  buildSceneSpec,
  robotWorldPosition,
  shuttleTrackWorldPosition,
  worldToCell,
  worldX,
  worldZ,
  type SceneCylinder,
} from '../../lib/topology3d'
import { conveyorAnchorOf, conveyorDecksOf, conveyorDirectionFromSide, parseCellId } from '../../lib/topology'
import { advanceInterpolationElapsed, interpolateScalar, interpolationProgress } from '../../lib/simulationMotion'
import {
  advanceSimulationRollerAngle,
  boundedSimulationTransitionDuration,
  interpolateSimulationPoint,
  isSimulationConveyorMotion,
  SIMULATION_CARGO_BODY_HEIGHT,
  SIMULATION_CARGO_CENTER_OFFSET_Y,
  shouldRenderSimulationCargoIndependently,
  simulationMotionYaw,
} from '../../lib/simulationTestVisualization'
import {
  classifyShuttleTrackSegment,
  collectConfirmedShuttleWaypoints,
  frameToShuttleWaypoint,
  headingAtShuttleFrame,
  isLegalShuttleTrackSegment,
  shuttleDirectionLabel,
  shuttleYawForSegment,
  type ShuttleTrackSegmentKind,
  type ShuttleVisualWaypoint,
} from '../../lib/shuttleTrackMotion'
import {
  boundedConveyorCatchUp,
  collectConfirmedConveyorWaypoints,
  conveyorSegmentKind,
  conveyorYawForSegment,
  groundConveyorWaypoints,
  upperConveyorWaypoints,
  validateConveyorWaypointPath,
  type ConveyorVisualWaypoint,
} from '../../lib/conveyorTrackMotion'
import type {
  GoodsSlot,
  FulfillmentTestScenarioAudit,
  ShuttleContract,
  SimulationRobot,
  SimulationTestCargoState,
  SimulationTestLeftFlowState,
  SimulationTestRunState,
  SimulationTask,
  TopologyPayload,
  TransportLayoutPlan,
  TransferInventoryBalance,
} from '../../types'

export type Simulation3DSceneProps = {
  topology: TopologyPayload
  goods?: GoodsSlot[]
  robots: SimulationRobot[]
  tasks: SimulationTask[]
  activeFloor: string
  focusRequest?: number
  transferInventory?: TransferInventoryBalance[]
  shuttleLifts?: ShuttleContract['lifts']
  selectedRobotId?: string
  followSelectedRobot?: boolean
  onSelectRobot?: (robotId: string) => void
  paused?: boolean
  transitionDuration?: number
  pickingArmLayoutPlan?: TransportLayoutPlan
  simulationTestHandoff?: FulfillmentTestScenarioAudit['rightHandoff']
  simulationTestCargo?: SimulationTestCargoState[]
  simulationTestState?: SimulationTestRunState
  showSimulationDebugLabels?: boolean
  pickingArmFocused?: boolean
  pickingArmFocusRequest?: number
  simulationMotionEpoch?: string
}

export type PickingArmPreviewLayerProps = {
  topology: TopologyPayload
  activeFloor: string
  layoutPlan?: TransportLayoutPlan
  visible: boolean
  cycleToken: string
  resetRequest: number
  paused: boolean
  speedMultiplier: 1 | 2 | 4
  cargo: PickingArmCargo
}

/**
 * Kept as a small exported layer so module-loading tests exercise the real
 * Simulation3DScene import graph while mounting the arm without WebGL.
 */
export function PickingArmPreviewLayer({
  topology,
  activeFloor,
  layoutPlan,
  visible,
  cycleToken,
  resetRequest,
  paused,
  speedMultiplier,
  cargo,
}: PickingArmPreviewLayerProps) {
  if (!visible || !layoutPlan?.pickingArm || activeFloor !== '1F') return null
  return (
    <PickingArmHandoff
      topology={topology}
      layout={layoutPlan.pickingArm}
      cargo={cargo}
      preview
      previewCycleToken={cycleToken}
      paused={paused}
      speedMultiplier={speedMultiplier}
      resetRequest={resetRequest}
    />
  )
}

export function PickingArmAuthoritativeLayer({
  topology,
  activeFloor,
  layoutPlan,
  simulationTest,
  paused,
  transitionDuration = 0.3,
  highlighted = false,
}: {
  topology: TopologyPayload
  activeFloor: string
  layoutPlan?: TransportLayoutPlan
  simulationTest?: SimulationTestRunState
  paused: boolean
  transitionDuration?: number
  highlighted?: boolean
}) {
  if (!layoutPlan?.pickingArm || activeFloor !== '1F') return null
  const activeCargo = simulationTest?.cargo.find(
    (cargo) => cargo.cargoId === simulationTest.arm.activeCargoId,
  )
  const cargo: PickingArmCargo = activeCargo
    ? {
        toteId: activeCargo.cargoId,
        orderId: activeCargo.orderId,
        skuId: activeCargo.skuId,
        pickedQuantity: activeCargo.quantityBase,
      }
    : {
        toteId: 'simulation-test-arm-idle',
        orderId: simulationTest?.order.orderId ?? 'NO-ACTIVE-RUN',
        skuId: simulationTest?.cargo[0]?.skuId ?? '-',
        pickedQuantity: 0,
      }
  return (
    <PickingArmHandoff
      topology={topology}
      layout={layoutPlan.pickingArm}
      cargo={cargo}
      preview={false}
      previewCycleToken="persistent-picking-arm"
      paused={paused}
      speedMultiplier={1}
      resetRequest={simulationTest?.resetGeneration ?? 0}
      authoritativeState={simulationTest?.arm}
      authoritativeTransitionSeconds={transitionDuration}
      highlighted={highlighted}
    />
  )
}

export function SimulationOnlyHandoffLayer({
  handoff,
  showLabels = false,
}: {
  handoff?: FulfillmentTestScenarioAudit['rightHandoff']
  showLabels?: boolean
}) {
  if (!handoff || handoff.simulationRouteStatus !== 'PASS_SIMULATION_ONLY') return null
  return (
    <group name="simulation-only-right-handoff">
      {handoff.facilities.map((facility) => {
        const points = facility.geometry.worldPoints.map((point) => [
          point.x,
          point.y + 0.12,
          point.z,
        ] as [number, number, number])
        const marker = points[Math.floor(points.length / 2)]
        return (
          <group key={facility.deviceId}>
            <Line
              points={points}
              color="#f59e0b"
              lineWidth={4}
              dashed
              dashScale={3}
              transparent
              opacity={0.95}
              depthTest={false}
              renderOrder={15}
            />
            <mesh position={marker}>
              <boxGeometry args={[0.34, 0.18, 0.34]} />
              <meshStandardMaterial color="#f59e0b" emissive="#92400e" emissiveIntensity={0.65} />
              {showLabels ? (
                <Html center distanceFactor={20} position={[0, 0.55, 0]}>
                  <div style={{ color: '#fef3c7', background: 'rgba(120,53,15,.92)', padding: '3px 6px', borderRadius: 5, whiteSpace: 'nowrap', fontSize: 10 }}>
                    SIMULATION ONLY · {facility.deviceId}
                  </div>
                </Html>
              ) : null}
            </mesh>
          </group>
        )
      })}
    </group>
  )
}

export function SimulationTestBreakPackLayer({
  leftFlow,
  showLabel = false,
}: {
  leftFlow?: SimulationTestLeftFlowState
  showLabel?: boolean
}) {
  if (!leftFlow) return null
  const station = leftFlow.station
  const ownerIsCtu = Boolean(leftFlow.ctu.activeCargoId)
  const visualCargo = ownerIsCtu
    ? null
    : leftFlow.status === 'returned'
      ? leftFlow.partialCase
      : station.occupiedCargoIds.length
        ? (leftFlow.partialCase ?? leftFlow.cargo)
        : leftFlow.cargo.status === 'waiting_pick' || leftFlow.cargo.status === 'ctu_picking'
          ? leftFlow.cargo
          : null
  const cargoPosition = visualCargo?.currentPosition
  const cargoAtStation = Boolean(
    visualCargo && station.occupiedCargoIds.includes(visualCargo.cargoId),
  )
  return (
    <group name="simulation-test-left-break-pack">
      <group position={[station.position.worldX, station.position.worldY, station.position.worldZ]}>
        <mesh position={[0, 0.18, 0]}>
          <boxGeometry args={[0.9, 0.34, 0.9]} />
          <meshStandardMaterial color="#0f766e" emissive="#134e4a" emissiveIntensity={0.35} roughness={0.55} />
        </mesh>
        <mesh position={[0, 0.62, 0]}>
          <boxGeometry args={[0.72, 0.08, 0.72]} />
          <meshStandardMaterial color="#5eead4" emissive="#0f766e" emissiveIntensity={0.25} />
        </mesh>
        {showLabel ? (
          <Html center distanceFactor={18} position={[0, 1.15, 0]}>
            <div style={{ color: '#ccfbf1', background: 'rgba(15,118,110,.92)', padding: '3px 6px', borderRadius: 5, whiteSpace: 'nowrap', fontSize: 10 }}>
              simulation_test 拆零工位 · {station.phase}
            </div>
          </Html>
        ) : null}
      </group>
      {visualCargo && cargoPosition ? (
        <mesh position={[cargoPosition.worldX, cargoPosition.worldY + (cargoAtStation ? 0.98 : 0.38), cargoPosition.worldZ]}>
          <boxGeometry args={[0.62, 0.58, 0.62]} />
          <meshStandardMaterial color={visualCargo.caseType === 'partial_case' ? '#f59e0b' : '#14b8a6'} emissive="#0f766e" emissiveIntensity={0.2} roughness={0.55} />
        </mesh>
      ) : null}
    </group>
  )
}

const SHUTTLE_COLOR = '#c084fc'
const SHUTTLE_EMISSIVE = '#6d28d9'
const EMPTY_HIGHLIGHTS: ReadonlySet<string> = new Set()
const ignoreInteraction = () => undefined

/** 平滑跟随：机器人组的实际位置朝目标格指数逼近，接近后贴死，避免逐 tick 跳格。 */
function RobotGroup({
  position,
  children,
  onClick,
  paused = false,
  transitionDuration = 0.3,
  orientToMotion = false,
  activeBlockKeys = [],
  activeBlocks,
}: {
  position: [number, number, number]
  children: ReactNode
  onClick?: () => void
  paused?: boolean
  transitionDuration?: number
  orientToMotion?: boolean
  activeBlockKeys?: string[]
  activeBlocks?: { current: Map<string, number> }
}) {
  const groupRef = useRef<Group>(null)
  const current = useRef(new Vector3(position[0], position[1], position[2]))
  const start = useRef(new Vector3(position[0], position[1], position[2]))
  const target = useRef(new Vector3(position[0], position[1], position[2]))
  const elapsed = useRef(transitionDuration)
  const durationRef = useRef(transitionDuration)
  const currentYaw = useRef(0)
  const startYaw = useRef(0)
  const targetYaw = useRef(0)
  const invalidate = useThree((state) => state.invalidate)
  const [x, y, z] = position

  useEffect(() => {
    const targetChanged = target.current.x !== x || target.current.y !== y || target.current.z !== z
    if (targetChanged) {
      start.current.copy(current.current)
      elapsed.current = 0
      if (orientToMotion) {
        const dx = x - current.current.x
        const dz = z - current.current.z
        if (Math.hypot(dx, dz) > 0.0001) {
          startYaw.current = currentYaw.current
          targetYaw.current = simulationMotionYaw(
            startYaw.current,
            [current.current.x, current.current.y, current.current.z],
            [x, y, z],
          )
        }
      }
    } else if (durationRef.current !== transitionDuration && elapsed.current < durationRef.current) {
      start.current.copy(current.current)
      elapsed.current = 0
    }
    durationRef.current = transitionDuration
    target.current.set(x, y, z)
    // Reset restores a paused snapshot. If the backend position changes while
    // paused, snap to that snapshot instead of leaving the mesh at the old run
    // position forever. A normal pause does not change x/y/z and still freezes.
    if (paused && targetChanged) {
      current.current.copy(target.current)
      start.current.copy(target.current)
      elapsed.current = transitionDuration
      groupRef.current?.position.copy(current.current)
    }
    invalidate()
  }, [invalidate, orientToMotion, paused, transitionDuration, x, y, z])

  useFrame((_, delta) => {
    if (paused) return
    elapsed.current = advanceInterpolationElapsed(elapsed.current, delta, transitionDuration, false)
    const progress = interpolationProgress(elapsed.current, transitionDuration)
    const cur = interpolateSimulationPoint(
      [start.current.x, start.current.y, start.current.z],
      [target.current.x, target.current.y, target.current.z],
      progress,
    )
    current.current.set(...cur)
    if (orientToMotion) {
      currentYaw.current = startYaw.current + (targetYaw.current - startYaw.current) * progress
    }
    if (progress < 1 && activeBlocks) {
      const now = performance.now()
      for (const key of activeBlockKeys) activeBlocks.current.set(key, now)
    }
    if (progress < 1) invalidate()
    if (groupRef.current) {
      groupRef.current.position.copy(current.current)
      if (orientToMotion) groupRef.current.rotation.y = currentYaw.current
    }
  })

  return <group ref={groupRef} position={current.current} onClick={(event) => { event.stopPropagation(); onClick?.() }}>{children}</group>
}

type ShuttleTrackMotion = {
  kind: 'turn' | 'move'
  elapsed: number
  duration: number
  start: Vector3
  target: Vector3
  startYaw: number
  targetYaw: number
  waypoint: ShuttleVisualWaypoint
  segmentKind: ShuttleTrackSegmentKind
}

/**
 * Shuttle-only visual player. It consumes every confirmed ShuttleGraph frame
 * in order, turns at the shared node, then starts the next cardinal edge.
 * Missing websocket snapshots therefore add queued waypoints instead of a
 * straight-line shortcut across a corner.
 */
function ShuttleTrackGroup({
  position,
  frames,
  routeIndex,
  routeKey,
  children,
  onClick,
  paused,
  transitionDuration,
  debug = false,
  cargoOwner = 'none',
  liftId = 'none',
}: {
  position: [number, number, number]
  frames: NonNullable<SimulationTestRunState['shuttle']['outboundFrames']>
  routeIndex: number
  routeKey: string
  children: ReactNode
  onClick?: () => void
  paused: boolean
  transitionDuration: number
  debug?: boolean
  cargoOwner?: string
  liftId?: string
}) {
  const groupRef = useRef<Group>(null)
  const current = useRef(new Vector3(...position))
  const currentYaw = useRef(headingAtShuttleFrame(frames, routeIndex))
  const queue = useRef<ShuttleVisualWaypoint[]>([])
  const motion = useRef<ShuttleTrackMotion | null>(null)
  const authority = useRef<{ routeKey: string; routeIndex: number } | null>(null)
  const debugWorldRef = useRef<HTMLSpanElement>(null)
  const debugYawRef = useRef<HTMLSpanElement>(null)
  const invalidate = useThree((state) => state.invalidate)
  const [debugState, setDebugState] = useState({
    fromNodeId: frames[routeIndex]?.nodeId ?? 'unknown',
    toNodeId: frames[routeIndex]?.nodeId ?? 'unknown',
    direction: 'stopped',
    yaw: currentYaw.current,
    segmentKind: 'stationary' as ShuttleTrackSegmentKind,
  })

  useEffect(() => {
    const previous = authority.current
    if (!previous) {
      authority.current = { routeKey, routeIndex }
      current.current.set(...position)
      currentYaw.current = headingAtShuttleFrame(frames, routeIndex, currentYaw.current)
      groupRef.current?.position.copy(current.current)
      groupRef.current?.rotation.set(0, currentYaw.current, 0)
      invalidate()
      return
    }
    if (previous.routeKey !== routeKey) {
      // Finish every already-confirmed waypoint from the old route before
      // entering the return/outbound route. Clearing here would reconnect the
      // current mid-edge position directly to a later node and recreate the
      // diagonal shortcut this component exists to prevent.
      queue.current.push(...collectConfirmedShuttleWaypoints(frames, -1, routeIndex))
    } else if (routeIndex < previous.routeIndex) {
      // A real reset remounts this component through simulationMotionEpoch.
      // A same-epoch index rollback is stale/out-of-order data: keep the
      // current confirmed queue and wait for a monotonic snapshot.
      authority.current = { routeKey, routeIndex: previous.routeIndex }
      return
    } else if (routeIndex > previous.routeIndex) {
      queue.current.push(...collectConfirmedShuttleWaypoints(
        frames, previous.routeIndex, routeIndex,
      ))
    }
    authority.current = { routeKey, routeIndex }
    invalidate()
  }, [frames, invalidate, position, routeIndex, routeKey])

  useFrame((_, delta) => {
    if (paused) return
    let active = motion.current
    if (!active) {
      let waypoint = queue.current[0]
      while (waypoint && current.current.distanceToSquared(new Vector3(...waypoint.position)) < 1e-10) {
        queue.current.shift()
        waypoint = queue.current[0]
      }
      if (!waypoint) return
      const from: [number, number, number] = [current.current.x, current.current.y, current.current.z]
      const segmentKind = classifyShuttleTrackSegment(from, waypoint.position)
      if (!isLegalShuttleTrackSegment(segmentKind)) {
        queue.current = []
        setDebugState({
          fromNodeId: debugState.toNodeId,
          toNodeId: waypoint.nodeId,
          direction: segmentKind,
          yaw: currentYaw.current,
          segmentKind,
        })
        invalidate()
        return
      }
      const targetYaw = shuttleYawForSegment(currentYaw.current, from, waypoint.position)
      const turnDelta = Math.abs(targetYaw - currentYaw.current)
      if ((segmentKind === 'row' || segmentKind === 'column') && turnDelta > 0.001) {
        active = {
          kind: 'turn', elapsed: 0,
          duration: Math.max(0.08, Math.min(0.24, transitionDuration * 0.35)),
          start: current.current.clone(), target: current.current.clone(),
          startYaw: currentYaw.current, targetYaw, waypoint, segmentKind,
        }
      } else {
        queue.current.shift()
        active = {
          kind: 'move', elapsed: 0, duration: transitionDuration,
          start: current.current.clone(), target: new Vector3(...waypoint.position),
          startYaw: currentYaw.current, targetYaw, waypoint, segmentKind,
        }
      }
      motion.current = active
      setDebugState({
        fromNodeId: debugState.toNodeId,
        toNodeId: waypoint.nodeId,
        direction: shuttleDirectionLabel(segmentKind, targetYaw),
        yaw: targetYaw,
        segmentKind,
      })
    }

    active.elapsed = Math.min(active.duration, active.elapsed + delta)
    const progress = interpolationProgress(active.elapsed, active.duration)
    if (active.kind === 'turn') {
      currentYaw.current = active.startYaw + (active.targetYaw - active.startYaw) * progress
    } else {
      const next = interpolateSimulationPoint(
        [active.start.x, active.start.y, active.start.z],
        [active.target.x, active.target.y, active.target.z],
        progress,
      )
      current.current.set(...next)
      currentYaw.current = active.targetYaw
    }
    if (groupRef.current) {
      groupRef.current.position.copy(current.current)
      // A Shuttle may yaw only. Explicitly clear pitch/roll every frame.
      groupRef.current.rotation.set(0, currentYaw.current, 0)
    }
    if (debugWorldRef.current) {
      debugWorldRef.current.textContent = `world (${current.current.x.toFixed(2)}, ${current.current.y.toFixed(2)}, ${current.current.z.toFixed(2)})`
    }
    if (debugYawRef.current) {
      debugYawRef.current.textContent = `${debugState.direction} · yaw ${(currentYaw.current * 180 / Math.PI).toFixed(0)}° · pitch 0° · roll 0°`
    }
    invalidate()
    if (progress >= 1) motion.current = null
  })

  const debugPoints = useMemo(
    () => frames.map((frame) => frameToShuttleWaypoint(frame).position),
    [frames],
  )
  return (
    <>
      {debug && debugPoints.length > 1 ? (
        <Line points={debugPoints} color="#38bdf8" lineWidth={1.2} transparent opacity={0.75} />
      ) : null}
      <group ref={groupRef} position={current.current} rotation={[0, currentYaw.current, 0]} onClick={(event) => { event.stopPropagation(); onClick?.() }}>
        {children}
        {debug ? (
          <Html center distanceFactor={16} position={[0, 1.7, 0]}>
            <div className="simulation-scene-label">
              <b>{debugState.fromNodeId} → {debugState.toNodeId}</b>
              <span ref={debugYawRef}>{debugState.direction} · yaw {(debugState.yaw * 180 / Math.PI).toFixed(0)}° · pitch 0° · roll 0°</span>
              <span ref={debugWorldRef}>world ({current.current.x.toFixed(2)}, {current.current.y.toFixed(2)}, {current.current.z.toFixed(2)})</span>
              <span>level {frames[routeIndex]?.position.level ?? '-'} · lift {liftId}</span>
              <span>cargo owner {cargoOwner}</span>
            </div>
          </Html>
        ) : null}
      </group>
    </>
  )
}

type ConveyorTrackMotion = {
  kind: 'turn' | 'move'
  elapsed: number
  duration: number
  start: Vector3
  target: Vector3
  startYaw: number
  targetYaw: number
  waypoint: ConveyorVisualWaypoint
  fromNodeId: string
}

const MAX_CONVEYOR_VISUAL_QUEUE = 96

/** Visual-only player for an already-authorized Conveyor path/index. */
function ConveyorCargoTrackGroup({
  position,
  path,
  pathIndex,
  routeKey,
  children,
  paused,
  transitionDuration,
  conveyorBlockByNode,
  activeBlocks,
  debug = false,
}: {
  position: [number, number, number]
  path: ConveyorVisualWaypoint[]
  pathIndex: number
  routeKey: string
  children: ReactNode
  paused: boolean
  transitionDuration: number
  conveyorBlockByNode: Map<string, string>
  activeBlocks?: { current: Map<string, number> }
  debug?: boolean
}) {
  const groupRef = useRef<Group>(null)
  const current = useRef(new Vector3(...position))
  const currentYaw = useRef(0)
  const currentNodeId = useRef(path[pathIndex]?.nodeId ?? 'unknown')
  const queue = useRef<ConveyorVisualWaypoint[]>([])
  const motion = useRef<ConveyorTrackMotion | null>(null)
  const durationRef = useRef(transitionDuration)
  const authority = useRef<{ routeKey: string; pathIndex: number } | null>(null)
  const invalidate = useThree((state) => state.invalidate)
  const validation = useMemo(() => validateConveyorWaypointPath(path), [path])

  useEffect(() => {
    const active = motion.current
    if (active && durationRef.current !== transitionDuration) {
      const progress = active.duration <= 0 ? 1 : active.elapsed / active.duration
      const nextDuration = active.kind === 'turn'
        ? Math.max(0.06, Math.min(0.18, transitionDuration * 0.28))
        : Math.max(0.06, transitionDuration / boundedConveyorCatchUp(queue.current.length))
      active.duration = nextDuration
      active.elapsed = Math.min(nextDuration, progress * nextDuration)
    }
    durationRef.current = transitionDuration
    invalidate()
  }, [invalidate, transitionDuration])

  useEffect(() => {
    const previous = authority.current
    if (!previous) {
      authority.current = { routeKey, pathIndex }
      current.current.set(...position)
      const from = path[Math.max(0, pathIndex - 1)]?.position
      const to = path[pathIndex]?.position
      if (from && to) currentYaw.current = conveyorYawForSegment(0, from, to)
      groupRef.current?.position.copy(current.current)
      groupRef.current?.rotation.set(0, currentYaw.current, 0)
      invalidate()
      return
    }
    if (validation.status === 'BLOCKED') return
    if (previous.routeKey !== routeKey) {
      queue.current.push(...collectConfirmedConveyorWaypoints(path, -1, pathIndex))
    } else if (pathIndex < previous.pathIndex) {
      // resetGeneration remounts this component; a same-epoch rollback is stale.
      authority.current = { routeKey, pathIndex: previous.pathIndex }
      return
    } else if (pathIndex > previous.pathIndex) {
      queue.current.push(...collectConfirmedConveyorWaypoints(
        path, previous.pathIndex, pathIndex,
      ))
    }
    if (queue.current.length > MAX_CONVEYOR_VISUAL_QUEUE) {
      // Never drop an intermediate corner. Freeze at the last legal point and
      // wait for a reset/new Run rather than teleporting to catch up.
      queue.current = queue.current.slice(0, MAX_CONVEYOR_VISUAL_QUEUE)
    }
    authority.current = { routeKey, pathIndex }
    invalidate()
  }, [invalidate, path, pathIndex, position, routeKey, validation.status])

  useFrame((_, delta) => {
    if (paused || validation.status === 'BLOCKED') return
    let active = motion.current
    if (!active) {
      let waypoint = queue.current[0]
      while (waypoint && current.current.distanceToSquared(new Vector3(...waypoint.position)) < 1e-10) {
        queue.current.shift()
        waypoint = queue.current[0]
      }
      if (!waypoint) return
      const from: [number, number, number] = [current.current.x, current.current.y, current.current.z]
      const kind = conveyorSegmentKind(from, waypoint.position)
      if (!isLegalShuttleTrackSegment(kind) || kind === 'lift') {
        queue.current = []
        return
      }
      const targetYaw = conveyorYawForSegment(currentYaw.current, from, waypoint.position)
      const turnDelta = Math.abs(targetYaw - currentYaw.current)
      if ((kind === 'row' || kind === 'column') && turnDelta > 0.001) {
        active = {
          kind: 'turn', elapsed: 0,
          duration: Math.max(0.06, Math.min(0.18, transitionDuration * 0.28)),
          start: current.current.clone(), target: current.current.clone(),
          startYaw: currentYaw.current, targetYaw, waypoint,
          fromNodeId: currentNodeId.current,
        }
      } else {
        queue.current.shift()
        const catchUp = boundedConveyorCatchUp(queue.current.length)
        active = {
          kind: 'move', elapsed: 0,
          duration: Math.max(0.06, transitionDuration / catchUp),
          start: current.current.clone(), target: new Vector3(...waypoint.position),
          startYaw: currentYaw.current, targetYaw, waypoint,
          fromNodeId: currentNodeId.current,
        }
      }
      motion.current = active
    }
    active.elapsed = Math.min(active.duration, active.elapsed + delta)
    const progress = interpolationProgress(active.elapsed, active.duration)
    if (activeBlocks) {
      const now = performance.now()
      const fromBlock = conveyorBlockByNode.get(active.fromNodeId)
      const toBlock = conveyorBlockByNode.get(active.waypoint.nodeId)
      if (fromBlock) activeBlocks.current.set(fromBlock, now)
      if (toBlock) activeBlocks.current.set(toBlock, now)
    }
    if (active.kind === 'turn') {
      currentYaw.current = active.startYaw + (active.targetYaw - active.startYaw) * progress
    } else {
      const next = interpolateSimulationPoint(
        [active.start.x, active.start.y, active.start.z],
        [active.target.x, active.target.y, active.target.z],
        progress,
      )
      current.current.set(...next)
      currentYaw.current = active.targetYaw
    }
    if (groupRef.current) {
      groupRef.current.position.copy(current.current)
      groupRef.current.rotation.set(0, currentYaw.current, 0)
    }
    invalidate()
    if (progress >= 1) {
      if (active.kind === 'move') currentNodeId.current = active.waypoint.nodeId
      motion.current = null
    }
  })

  return (
    <>
      {debug && path.length > 1 ? (
        <Line points={path.map((item) => item.position)} color="#22d3ee" lineWidth={1} transparent opacity={0.55} />
      ) : null}
      <group ref={groupRef} position={current.current} rotation={[0, currentYaw.current, 0]}>
        {children}
      </group>
    </>
  )
}

export function AuthoritativeSimulationCargoLayer({
  cargo,
  armActiveCargoId,
  paused,
  transitionDuration,
  motionEpoch = 'simulation-test',
  conveyorBlockByNode = new Map(),
  activeBlocks,
  groundConveyorPath = [],
  upperConveyorPath = [],
  showTrackDebug = false,
}: {
  cargo: SimulationTestCargoState[]
  armActiveCargoId?: string | null
  paused: boolean
  transitionDuration: number
  motionEpoch?: string
  conveyorBlockByNode?: Map<string, string>
  activeBlocks?: { current: Map<string, number> }
  groundConveyorPath?: ConveyorVisualWaypoint[]
  upperConveyorPath?: ConveyorVisualWaypoint[]
  showTrackDebug?: boolean
}) {
  return (
    <group name="authoritative-simulation-test-cargo">
      {cargo.filter((item) => shouldRenderSimulationCargoIndependently(item, armActiveCargoId)).map((item, index) => {
        const position: [number, number, number] = [
          item.currentPosition.worldX,
          item.currentPosition.worldY,
          item.currentPosition.worldZ,
        ]
        const color = ['#38bdf8', '#22d3ee', '#2dd4bf', '#a3e635', '#facc15'][index % 5]
        const cargoVisual = (
          <>
            <mesh position={[0, SIMULATION_CARGO_CENTER_OFFSET_Y, 0]}>
              <boxGeometry args={[0.62, SIMULATION_CARGO_BODY_HEIGHT, 0.62]} />
              <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.16} roughness={0.55} />
            </mesh>
            {item.status === 'waiting_arm' ? (
              <Html center distanceFactor={18} position={[0, 0.95, 0]}>
                <div style={{ color: '#dbeafe', background: 'rgba(3,105,161,.9)', padding: '2px 5px', borderRadius: 4, whiteSpace: 'nowrap', fontSize: 9 }}>
                  {item.cargoId} · waiting_arm
                </div>
              </Html>
            ) : null}
          </>
        )
        const groundIndex = item.status === 'conveyor_transporting' ? item.conveyorIndex : null
        const upperIndex = item.status === 'merge_conveyor_transporting' ? item.mergeConveyorIndex : null
        if (typeof groundIndex === 'number' && groundConveyorPath.length) {
          return (
            <ConveyorCargoTrackGroup
              key={`${motionEpoch}:${item.cargoId}:ground`}
              position={position}
              path={groundConveyorPath}
              pathIndex={groundIndex}
              routeKey={`${motionEpoch}:${item.cargoId}:ground`}
              paused={paused}
              transitionDuration={transitionDuration}
              conveyorBlockByNode={conveyorBlockByNode}
              activeBlocks={activeBlocks}
              debug={showTrackDebug}
            >
              {cargoVisual}
            </ConveyorCargoTrackGroup>
          )
        }
        if (typeof upperIndex === 'number' && upperConveyorPath.length) {
          return (
            <ConveyorCargoTrackGroup
              key={`${motionEpoch}:${item.cargoId}:upper`}
              position={position}
              path={upperConveyorPath}
              pathIndex={upperIndex}
              routeKey={`${motionEpoch}:${item.cargoId}:upper`}
              paused={paused}
              transitionDuration={transitionDuration}
              conveyorBlockByNode={conveyorBlockByNode}
              activeBlocks={activeBlocks}
              debug={showTrackDebug}
            >
              {cargoVisual}
            </ConveyorCargoTrackGroup>
          )
        }
        return (
          <RobotGroup
            key={`${motionEpoch}:${item.cargoId}:free`}
            position={position}
            paused={paused}
            transitionDuration={transitionDuration}
            orientToMotion={isSimulationConveyorMotion(item.status)}
          >
            {cargoVisual}
          </RobotGroup>
        )
      })}
    </group>
  )
}

/**
 * PQ-50 风格 CTU：17 层高行程门架、升降取箱台和 5 个独立载箱位。
 * 货架层数决定升降高度，载箱位数量决定单趟容量，两者不混用。
 */
function CtuCart({
  load,
  carrying,
  action,
  paused = false,
}: {
  load: number
  carrying?: GoodsSlot[]
  action?: SimulationRobot['handlingAction']
  paused?: boolean
}) {
  const liftRef = useRef<Group>(null)
  const forkRef = useRef<Group>(null)
  const invalidate = useThree((state) => state.invalidate)
  const levelHeight = ZONE_LEVEL_HEIGHT.ctu
  const mastHeight = 17 * levelHeight
  const baseTop = 0.2
  const homeLiftY = 0.42
  const carryStartY = 0.72
  const carryGap = 0.52
  const targetLayerY = (Math.max(1, Math.min(17, action?.targetLayer ?? 1)) - 0.5) * levelHeight
  const targetLiftY = action?.stage === 'stowing'
    ? carryStartY + Math.max(0, Math.min(4, action.slotIndex)) * carryGap
    : action
      ? targetLayerY
      : homeLiftY
  const targetForkZ = action?.stage === 'extending' ? -0.68 : 0
  const liftY = useRef(homeLiftY)
  const forkZ = useRef(0)
  const mechanismTarget = useRef({ liftY: homeLiftY, forkZ: 0 })
  const actionSignature = `${action?.stage ?? 'idle'}:${action?.targetLayer ?? 1}:${action?.slotIndex ?? 0}`

  useEffect(() => {
    const targetChanged = mechanismTarget.current.liftY !== targetLiftY
      || mechanismTarget.current.forkZ !== targetForkZ
    mechanismTarget.current = { liftY: targetLiftY, forkZ: targetForkZ }
    // As with vehicle translation, a reset can change the mechanism target
    // while the restored run is paused. Apply only that state change directly.
    if (paused && targetChanged) {
      liftY.current = targetLiftY
      forkZ.current = targetForkZ
      if (liftRef.current) liftRef.current.position.y = targetLiftY
      if (forkRef.current) forkRef.current.position.z = targetForkZ
    }
    invalidate()
  }, [actionSignature, invalidate, paused, targetForkZ, targetLiftY])
  useFrame((_, delta) => {
    if (paused) return
    const step = Math.min(1, delta * 7)
    liftY.current += (targetLiftY - liftY.current) * step
    forkZ.current += (targetForkZ - forkZ.current) * step
    if (Math.abs(liftY.current - targetLiftY) < 0.005) liftY.current = targetLiftY
    if (Math.abs(forkZ.current - targetForkZ) < 0.005) forkZ.current = targetForkZ
    if (liftRef.current) liftRef.current.position.y = liftY.current
    if (forkRef.current) forkRef.current.position.z = forkZ.current
    if (liftY.current !== targetLiftY || forkZ.current !== targetForkZ) invalidate()
  })

  const visibleCarrying = action?.stage === 'stowing'
    ? (carrying ?? []).slice(0, -1)
    : (carrying ?? [])
  const showTransferBox = action?.stage === 'extending' || action?.stage === 'stowing'
  const rotationY = action?.approachDirection === 'north'
    ? Math.PI
    : action?.approachDirection === 'east'
      ? -Math.PI / 2
      : action?.approachDirection === 'west'
        ? Math.PI / 2
        : 0

  return (
    <group rotation={[0, rotationY, 0]}>
      {/* 红色移动底盘和黑色前保险杠，对应参考设备的主要外形。 */}
      <mesh position={[0, 0.11, 0]}>
        <boxGeometry args={[0.82, 0.2, 0.94]} />
        <meshStandardMaterial color="#dc2626" emissive="#7f1d1d" emissiveIntensity={0.2} roughness={0.48} metalness={0.25} />
      </mesh>
      <mesh position={[0, 0.2, -0.43]}>
        <boxGeometry args={[0.68, 0.24, 0.14]} />
        <meshStandardMaterial color="#111827" roughness={0.36} metalness={0.55} />
      </mesh>
      <mesh position={[0, 0.25, 0.34]}>
        <boxGeometry args={[0.58, 0.38, 0.28]} />
        <meshStandardMaterial color="#b91c1c" emissive="#7f1d1d" emissiveIntensity={0.16} roughness={0.5} />
      </mesh>

      {/* 17 层行程门架。 */}
      {[-0.28, 0.28].map((x) => (
        <mesh key={`mast-${x}`} position={[x, baseTop + mastHeight / 2, 0.18]}>
          <boxGeometry args={[0.075, mastHeight, 0.075]} />
          <meshStandardMaterial color="#cbd5e1" roughness={0.32} metalness={0.75} />
        </mesh>
      ))}
      <mesh position={[0, baseTop + mastHeight, 0.18]}>
        <boxGeometry args={[0.64, 0.1, 0.12]} />
        <meshStandardMaterial color="#1f2937" roughness={0.38} metalness={0.55} />
      </mesh>
      <mesh position={[0, baseTop + mastHeight / 2, 0.2]}>
        <boxGeometry args={[0.035, mastHeight - 0.18, 0.035]} />
        <meshStandardMaterial color="#fbbf24" emissive="#92400e" emissiveIntensity={0.16} metalness={0.55} />
      </mesh>

      {/* 五个载箱位位于门架后方。 */}
      {Array.from({ length: CTU_ROBOT_LEVELS }, (_, index) => (
        <group key={`carry-slot-${index}`}>
          <mesh position={[0, carryStartY + index * carryGap - 0.2, 0.25]}>
            <boxGeometry args={[0.62, 0.055, 0.54]} />
            <meshStandardMaterial
              color={index < load ? '#ef4444' : '#334155'}
              emissive={index < load ? '#7f1d1d' : '#0f172a'}
              emissiveIntensity={index < load ? 0.22 : 0.05}
              roughness={0.5}
              metalness={0.35}
            />
          </mesh>
        </group>
      ))}
      {visibleCarrying.slice(0, CTU_ROBOT_LEVELS).map((slot, index) => (
        <mesh key={`carry-${slot.nodeId}-${slot.layer}`} position={[0, carryStartY + index * carryGap, 0.25]}>
          <boxGeometry args={[0.5, 0.3, 0.42]} />
          <meshStandardMaterial color="#34d399" emissive="#047857" emissiveIntensity={0.26} roughness={0.58} />
        </mesh>
      ))}

      {/* 升降台与伸缩双叉。 */}
      <group ref={liftRef} position={[0, homeLiftY, -0.02]}>
        <mesh>
          <boxGeometry args={[0.6, 0.12, 0.28]} />
          <meshStandardMaterial color="#f97316" emissive="#9a3412" emissiveIntensity={0.28} roughness={0.4} metalness={0.42} />
        </mesh>
        <group ref={forkRef}>
          {[-0.17, 0.17].map((x) => (
            <mesh key={`fork-${x}`} position={[x, 0, -0.32]}>
              <boxGeometry args={[0.07, 0.055, 0.72]} />
              <meshStandardMaterial color="#e5e7eb" roughness={0.28} metalness={0.8} />
            </mesh>
          ))}
          {showTransferBox ? (
            <mesh position={[0, 0.2, -0.3]}>
              <boxGeometry args={[0.5, 0.3, 0.42]} />
              <meshStandardMaterial color="#fde047" emissive="#ca8a04" emissiveIntensity={0.48} roughness={0.5} />
            </mesh>
          ) : null}
        </group>
      </group>
    </group>
  )
}

/** 四向车：2×2 车体。局部坐标，原点=格中心贴地。 */
function ShuttleCart({ carrying }: { carrying?: GoodsSlot & { cargoId?: string } }) {
  const w = CELL * 2 * 0.88
  return (
    <group position={[0, 0.42, 0]}>
      <mesh>
        <boxGeometry args={[w, 0.8, w]} />
        <meshStandardMaterial color={SHUTTLE_COLOR} emissive={SHUTTLE_EMISSIVE} emissiveIntensity={0.5} roughness={0.42} />
      </mesh>
      <mesh position={[0, 0.46, 0]}>
        <boxGeometry args={[w * 0.58, 0.12, w * 0.58]} />
        <meshStandardMaterial color="#94a3b8" emissive="#334155" emissiveIntensity={0.35} roughness={0.3} />
      </mesh>
      <mesh position={[0, 0.03, w / 2 + 0.035]} name="shuttle-local-forward-indicator">
        <boxGeometry args={[w * 0.52, 0.16, 0.07]} />
        <meshStandardMaterial color="#38bdf8" emissive="#0369a1" emissiveIntensity={0.8} roughness={0.35} />
      </mesh>
      {carrying ? (
        <group name={`shuttle-bound-cargo-${carrying.cargoId ?? carrying.skuId}`} position={[0, 0.92, 0]}>
          <mesh>
            <boxGeometry args={[0.64, 0.5, 0.58]} />
            <meshStandardMaterial color="#e2e8f0" emissive="#1d4ed8" emissiveIntensity={0.18} roughness={0.55} />
          </mesh>
          <mesh position={[0, 0.02, 0.296]}>
            <boxGeometry args={[0.5, 0.16, 0.012]} />
            <meshStandardMaterial color="#2563eb" emissive="#1d4ed8" emissiveIntensity={0.22} />
          </mesh>
        </group>
      ) : null}
      <pointLight color="#e879f9" intensity={1.8} distance={7} decay={2} />
    </group>
  )
}

const DELIVERY_RISE_SECONDS = 0.8
// 与机器人 1 格/0.6s 的节奏匹配，保证出站动画在后端进入回程阶段前走完，避免货箱瞬移。
const DELIVERY_SPEED = 1.8
const DELIVERY_DWELL_SECONDS = 1.2
const DELIVERY_FADE_SECONDS = 0.3

/**
 * 交付到输送线出入口的货箱：从带面下方升到滚筒上，模拟「货物从输送线下方进入」，
 * 升到位后沿输送方向移动；遇到升降移栽机时抬到第二层，停留后缩小消失。
 */
function DeliveredBox({
  position,
  color,
  path,
  blockLookup,
  activeBlocks,
  cols,
  rows,
  hold = false,
  skipRise = false,
}: {
  position: [number, number, number]
  color: string
  path: [number, number, number][]
  blockLookup: Map<string, string>
  activeBlocks: { current: Map<string, number> }
  cols: number
  rows: number
  /** 到拣货位后停住等待人工拣货（不淡出）；任务进入取回/回库阶段后由上层移除组件。 */
  hold?: boolean
  /** 直接从目标位置出现（用于已在出入口等待的货箱） */
  skipRise?: boolean
}) {
  const ref = useRef<Group>(null)
  const platformRef = useRef<Mesh>(null)
  const startRef = useRef<number | null>(null)
  const route = useMemo(() => {
    const segments: {
      from: [number, number, number]
      to: [number, number, number]
      length: number
      start: number
    }[] = []
    let total = 0
    for (let index = 1; index < path.length; index += 1) {
      const from = path[index - 1]
      const to = path[index]
      const length = Math.hypot(to[0] - from[0], to[1] - from[1], to[2] - from[2])
      if (length <= 0.001) continue
      segments.push({ from, to, length, start: total })
      total += length
    }
    return { segments, total }
  }, [path])
  const travelSeconds = route.total / DELIVERY_SPEED
  const travelEnd = (skipRise ? 0 : DELIVERY_RISE_SECONDS) + travelSeconds
  const fadeStart = travelEnd + DELIVERY_DWELL_SECONDS

  useFrame(({ clock, invalidate }) => {
    const group = ref.current
    if (!group) return
    if (startRef.current === null) startRef.current = clock.elapsedTime
    const elapsed = clock.elapsedTime - startRef.current
    const riseSeconds = skipRise ? 0 : DELIVERY_RISE_SECONDS
    const rise = riseSeconds ? Math.min(1, elapsed / riseSeconds) : 1
    const eased = 1 - Math.pow(1 - rise, 3)
    let x = position[0]
    let y = position[1] - (skipRise ? 0 : 0.9 * (1 - eased))
    let z = position[2]
    if (elapsed > riseSeconds) {
      const distance = Math.min(route.total, (elapsed - riseSeconds) * DELIVERY_SPEED)
      let found = false
      let vertical = false
      for (const segment of route.segments) {
        if (distance <= segment.start + segment.length + 0.0001) {
          const t = segment.length ? Math.max(0, distance - segment.start) / segment.length : 0
          x = segment.from[0] + (segment.to[0] - segment.from[0]) * t
          y = segment.from[1] + (segment.to[1] - segment.from[1]) * t
          z = segment.from[2] + (segment.to[2] - segment.from[2]) * t
          const dx = Math.abs(segment.to[0] - segment.from[0])
          const dz = Math.abs(segment.to[2] - segment.from[2])
          const dy = Math.abs(segment.to[1] - segment.from[1])
          vertical = dx < 0.001 && dz < 0.001 && dy > 0.05
          found = true
          break
        }
      }
      if (platformRef.current) platformRef.current.visible = vertical
      if (!found && path.length) {
        ;[x, y, z] = path[path.length - 1]
      }
      if (!hold) {
        const fade = Math.min(1, Math.max(0, (elapsed - fadeStart) / DELIVERY_FADE_SECONDS))
        group.scale.setScalar(Math.max(0.001, 1 - fade))
        group.visible = fade < 1
      }
    }
    group.position.set(x, y, z)
    // 标记货箱当前所在的 2×2 输送块，该块的滚筒才会转动。
    const cell = worldToCell(x, z, cols, rows)
    const blockKey = blockLookup.get(`${cell.row}:${cell.col}`)
    if (blockKey) activeBlocks.current.set(blockKey, performance.now())
    if (hold ? elapsed < travelEnd + 0.05 : elapsed < fadeStart + DELIVERY_FADE_SECONDS) invalidate()
  })

  return (
    <group ref={ref} position={[position[0], position[1] - (skipRise ? 0 : 0.9), position[2]]}>
      <mesh>
        <boxGeometry args={[0.34, 0.3, 0.34]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={0.18}
          roughness={0.55}
          metalness={0.12}
        />
      </mesh>
      {/* 升降移栽机的随动载货平台：只在垂直升降段显示 */}
      <mesh ref={platformRef} position={[0, -0.19, 0]} visible={false}>
        <boxGeometry args={[0.9, 0.06, 0.9]} />
        <meshStandardMaterial color="#2563eb" emissive="#1e40af" emissiveIntensity={0.2} roughness={0.5} metalness={0.35} />
      </mesh>
    </group>
  )
}

const ROLLER_SPIN_SPEED = 9
const ROLLER_ACTIVE_WINDOW_MS = 140

/** 只在有货箱经过的 2×2 输送块上叠加旋转滚筒，其余块保持静态合批。 */
function SpinningRollerBlock({
  block,
  cols,
  rows,
  floorY,
  activeBlocks,
  paused = false,
  speedMultiplier = 1,
}: {
  block: {
    key: string
    row: number
    col: number
    floor: string
    direction: [number, number]
    levels: number
    ground: boolean
  }
  cols: number
  rows: number
  floorY: number
  activeBlocks: { current: Map<string, number> }
  paused?: boolean
  speedMultiplier?: number
}) {
  const groupRef = useRef<Group>(null)
  const rollerRefs = useRef<(Group | null)[]>([])
  const angleRef = useRef(0)
  const invalidate = useThree((state) => state.invalidate)
  const beltW = CELL * 2 * 0.98
  const rollerR = 0.048
  const pitch = beltW / 16
  const alongX = block.direction[0] !== 0
  const cx = worldX(block.col, cols) + CELL / 2
  const cz = worldZ(block.row, rows) - CELL / 2
  const rollers = useMemo(() => {
    const result: { position: [number, number, number]; axis: 'x' | 'z' }[] = []
    const count = 16
    // 地面输送线贴地单层；架空线按层数铺滚筒。
    const deckCount = block.ground ? 1 : Math.min(block.levels, 2)
    const beltBase = block.ground ? CONVEYOR_GROUND_BELT_Y : CONVEYOR_DECK_BASE
    for (let level = 0; level < deckCount; level += 1) {
      const y = floorY + beltBase + level * CONVEYOR_DECK_STEP - 0.045
      for (let index = 0; index < count; index += 1) {
        const t = -beltW / 2 + pitch * (index + 0.5)
        result.push({
          position: alongX ? [cx + t, y, cz] : [cx, y, cz + t],
          axis: alongX ? 'z' : 'x',
        })
      }
    }
    return result
  }, [alongX, beltW, block.ground, block.levels, cx, cz, floorY, pitch])

  useFrame((_, delta) => {
    const group = groupRef.current
    if (!group) return
    const lastActive = activeBlocks.current.get(block.key) ?? 0
    const active = performance.now() - lastActive < ROLLER_ACTIVE_WINDOW_MS
    if (!paused) group.visible = active
    if (!active || paused) return
    angleRef.current = advanceSimulationRollerAngle(
      angleRef.current, delta, block.direction, active, paused, ROLLER_SPIN_SPEED * speedMultiplier,
    )
    for (const roller of rollerRefs.current) {
      if (roller) roller.rotation.y = angleRef.current
    }
    invalidate()
  })

  return (
    <group ref={groupRef} visible={false}>
      {rollers.map((roller, index) => (
        <group
          key={`${block.key}-${index}`}
          position={roller.position}
          rotation={roller.axis === 'x' ? [0, 0, Math.PI / 2] : [Math.PI / 2, 0, 0]}
        >
          <group ref={(group) => { rollerRefs.current[index] = group }}>
            <mesh>
              <cylinderGeometry args={[rollerR, rollerR, beltW - 0.18, 12]} />
              <meshStandardMaterial color="#b8c4d0" roughness={0.35} metalness={0.65} />
            </mesh>
            <mesh position={[rollerR * 0.82, 0, 0]}>
              <boxGeometry args={[rollerR * 0.22, beltW - 0.2, rollerR * 0.22]} />
              <meshStandardMaterial color="#0f172a" roughness={0.5} metalness={0.3} />
            </mesh>
          </group>
        </group>
      ))}
    </group>
  )
}

function TaskTargetMarker({
  position,
  location,
}: {
  position: [number, number, number]
  location: string
}) {
  return (
    <group position={position}>
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.58, 0.09, 12, 40]} />
        <meshBasicMaterial color="#fde047" depthTest={false} />
      </mesh>
      <mesh position={[0, 2.6, 0]}>
        <cylinderGeometry args={[0.05, 0.2, 5.2, 12]} />
        <meshBasicMaterial color="#fde047" transparent opacity={0.48} depthWrite={false} />
      </mesh>
      <pointLight color="#fde047" intensity={2.2} distance={5} decay={2} />
      <Html position={[0, 1.25, 0]} center zIndexRange={[30, 20]}>
        <div className="simulation-scene-label simulation-scene-target-label">
          <b>当前取货点</b>
          <span>{location}</span>
        </div>
      </Html>
    </group>
  )
}

function RobotFocusMarker({ robotId, phase }: { robotId: string; phase: string }) {
  return (
    <group>
      <mesh position={[0, 0.04, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.7, 0.08, 12, 36]} />
        <meshBasicMaterial color="#22d3ee" depthTest={false} />
      </mesh>
      <mesh position={[0, 9.15, 0]}>
        <coneGeometry args={[0.26, 0.72, 16]} />
        <meshBasicMaterial color="#22d3ee" transparent opacity={0.9} depthTest={false} />
      </mesh>
      <Html position={[0, 9.8, 0]} center zIndexRange={[29, 19]}>
        <div className="simulation-scene-label simulation-scene-robot-label">
          <b>{robotId}</b>
          <span>{phase}</span>
        </div>
      </Html>
    </group>
  )
}

function CameraTaskFocus({
  target,
  request,
}: {
  target: [number, number, number] | null
  request: number
}) {
  const camera = useThree((state) => state.camera)
  const controls = useThree((state) => state.controls)
  const invalidate = useThree((state) => state.invalidate)
  const animation = useRef<{
    progress: number
    fromPosition: Vector3
    toPosition: Vector3
    fromTarget: Vector3
    toTarget: Vector3
  } | null>(null)

  useEffect(() => {
    if (!target || !request || !controls) return
    const orbit = controls as unknown as { target: Vector3; update: () => void }
    const toTarget = new Vector3(target[0], Math.max(0.8, target[1]), target[2])
    const direction = camera.position.clone().sub(orbit.target)
    direction.y = Math.max(direction.y, 0.35)
    if (direction.lengthSq() < 0.01) direction.set(1, 0.8, 1)
    direction.normalize()
    animation.current = {
      progress: 0,
      fromPosition: camera.position.clone(),
      toPosition: toTarget.clone().add(direction.multiplyScalar(20)),
      fromTarget: orbit.target.clone(),
      toTarget,
    }
    invalidate()
  }, [camera, controls, invalidate, request, target])

  useFrame((_, delta) => {
    const motion = animation.current
    if (!motion || !controls) return
    motion.progress = Math.min(1, motion.progress + delta * 1.8)
    const eased = 1 - Math.pow(1 - motion.progress, 3)
    camera.position.lerpVectors(motion.fromPosition, motion.toPosition, eased)
    const orbit = controls as unknown as { target: Vector3; update: () => void }
    orbit.target.lerpVectors(motion.fromTarget, motion.toTarget, eased)
    orbit.update()
    invalidate()
    if (motion.progress >= 1) animation.current = null
  })

  return null
}

function CameraRobotFollow({ target, enabled, paused, transitionDuration }: { target: [number, number, number] | null; enabled: boolean; paused: boolean; transitionDuration: number }) {
  const camera = useThree((state) => state.camera)
  const controls = useThree((state) => state.controls)
  const invalidate = useThree((state) => state.invalidate)
  const previous = useRef<Vector3 | null>(null)
  const motion = useRef<{
    elapsed: number
    fromPosition: Vector3
    toPosition: Vector3
    fromTarget: Vector3
    toTarget: Vector3
  } | null>(null)

  useEffect(() => {
    if (!motion.current || !controls) return
    const orbit = controls as unknown as { target: Vector3 }
    motion.current.fromPosition.copy(camera.position)
    motion.current.fromTarget.copy(orbit.target)
    motion.current.elapsed = 0
    invalidate()
  }, [camera, controls, invalidate, transitionDuration])

  useEffect(() => {
    if (!enabled || !target || !controls) {
      previous.current = null
      motion.current = null
      return
    }
    const next = new Vector3(target[0], target[1] + 0.5, target[2])
    const orbit = controls as unknown as { target: Vector3; update: () => void }
    if (!previous.current) {
      previous.current = next
      orbit.target.copy(next)
      orbit.update()
      invalidate()
      return
    }
    if (previous.current.distanceToSquared(next) < 0.000001) return
    const translation = next.clone().sub(previous.current)
    motion.current = {
      elapsed: 0,
      fromPosition: camera.position.clone(),
      toPosition: camera.position.clone().add(translation),
      fromTarget: orbit.target.clone(),
      toTarget: next.clone(),
    }
    previous.current = next
    invalidate()
  }, [camera, controls, enabled, invalidate, target])

  useFrame((_, delta) => {
    if (paused || !motion.current || !controls) return
    const current = motion.current
    current.elapsed = advanceInterpolationElapsed(current.elapsed, delta, transitionDuration, false)
    const progress = interpolationProgress(current.elapsed, transitionDuration)
    camera.position.lerpVectors(current.fromPosition, current.toPosition, progress)
    const orbit = controls as unknown as { target: Vector3; update: () => void }
    orbit.target.lerpVectors(current.fromTarget, current.toTarget, progress)
    orbit.update()
    if (progress < 1) invalidate()
    else motion.current = null
  })
  return null
}

function TransferInventoryMarker({ balance }: { balance: TransferInventoryBalance }) {
  return (
    <group position={[balance.worldX, balance.worldY + 0.28, balance.worldZ]}>
      <mesh>
        <boxGeometry args={[0.72, 0.5, 0.72]} />
        <meshStandardMaterial color="#fb923c" emissive="#9a3412" emissiveIntensity={0.28} roughness={0.55} />
      </mesh>
      <Html position={[0, 0.72, 0]} center zIndexRange={[28, 18]}>
        <div className="simulation-scene-label transfer-inventory-label">
          <b>{balance.skuId} · {balance.quantityOnHand} 箱</b>
          <span>L{balance.level} · {balance.locationId}</span>
        </div>
      </Html>
    </group>
  )
}

function SmoothLiftCab({
  y,
  paused,
  transitionDuration,
  children,
}: {
  y: number
  paused: boolean
  transitionDuration: number
  children: ReactNode
}) {
  const groupRef = useRef<Group>(null)
  const current = useRef(y)
  const start = useRef(y)
  const target = useRef(y)
  const elapsed = useRef(transitionDuration)
  const durationRef = useRef(transitionDuration)
  const invalidate = useThree((state) => state.invalidate)

  useEffect(() => {
    if (target.current === y) {
      if (durationRef.current !== transitionDuration && elapsed.current < durationRef.current) {
        start.current = current.current
        elapsed.current = 0
        durationRef.current = transitionDuration
        invalidate()
      }
      return
    }
    start.current = current.current
    target.current = y
    elapsed.current = 0
    durationRef.current = transitionDuration
    if (paused) {
      current.current = y
      start.current = y
      elapsed.current = transitionDuration
      if (groupRef.current) groupRef.current.position.y = y
    }
    invalidate()
  }, [invalidate, paused, transitionDuration, y])

  useFrame((_, delta) => {
    if (paused) return
    elapsed.current = advanceInterpolationElapsed(elapsed.current, delta, transitionDuration, false)
    const progress = interpolationProgress(elapsed.current, transitionDuration)
    current.current = interpolateScalar(start.current, target.current, progress)
    if (groupRef.current) groupRef.current.position.y = current.current
    if (progress < 1) invalidate()
  })

  return <group ref={groupRef} position={[0, current.current, 0]}>{children}</group>
}

function ShuttleLiftView({
  lift,
  cols,
  rows,
  activeRobot,
  activeTask,
  simulationTestActive = false,
  simulationTestPhase,
  paused,
  transitionDuration,
}: {
  lift: ShuttleContract['lifts'][number]
  cols: number
  rows: number
  activeRobot?: SimulationRobot
  activeTask?: SimulationTask
  simulationTestActive?: boolean
  simulationTestPhase?: string
  paused: boolean
  transitionDuration: number
}) {
  const height = ZONE_LEVEL_HEIGHT.shuttle * 4
  const active = simulationTestActive || (
    activeTask?.plan?.chosen_lift === lift.lift_id
      && ['enter_lift', 'lift_moving', 'exit_lift'].includes(activeTask.phase)
  )
  const [x, trackY, z] = shuttleTrackWorldPosition(
    lift.row,
    lift.col,
    Number(activeRobot?.currentLayer ?? 1),
    cols,
    rows,
  )
  const cabY = trackY - 0.07

  return (
    <group position={[x, 0, z]}>
      {/* 井道线框随车库基座抬升；轿厢 trackY 已含基座高度 */}
      <mesh position={[0, SHUTTLE_RACK_BASE_Y + height / 2, 0]}>
        <boxGeometry args={[CELL * 1.9, height, CELL * 1.9]} />
        <meshBasicMaterial color={active ? '#fde047' : '#22c55e'} wireframe transparent opacity={active ? 0.95 : 0.55} />
      </mesh>
      <SmoothLiftCab y={cabY} paused={paused} transitionDuration={transitionDuration}>
        <mesh>
          <boxGeometry args={[CELL * 1.72, 0.14, CELL * 1.72]} />
          <meshStandardMaterial
            color={active ? '#fde047' : '#16a34a'}
            emissive={active ? '#ca8a04' : '#14532d'}
            emissiveIntensity={active ? 0.75 : 0.35}
          />
        </mesh>
      </SmoothLiftCab>
      <Html position={[0, SHUTTLE_RACK_BASE_Y + height + 0.75, 0]} center zIndexRange={[27, 17]}>
        <div className={`simulation-scene-label shuttle-lift-label ${active ? 'active' : ''}`}>
          <b>{lift.label}</b>
          <span>{simulationTestActive ? `simulation_test · ${simulationTestPhase}` : active ? activeTask?.phaseLabel : 'L1～L4'}</span>
        </div>
      </Html>
    </group>
  )
}

function cellWorldPosition(
  cellId: string,
  layer: number,
  floors: string[],
  cols: number,
  rows: number,
  robotType: SimulationRobot['type'] = 'ctu',
): [number, number, number] | null {
  const cell = parseCellId(cellId)
  if (!cell) return null
  if (robotType === 'shuttle') {
    return shuttleTrackWorldPosition(cell.row, cell.col, layer, cols, rows)
  }
  const floorIndex = Math.max(0, floors.indexOf(cell.floor))
  return [
    worldX(cell.col, cols),
    floorIndex * FLOOR_HEIGHT + Math.max(0.16, (Math.max(1, layer) - 0.5) * ZONE_LEVEL_HEIGHT.ctu),
    worldZ(cell.row, rows),
  ]
}

function Simulation3DScene({
  topology,
  goods,
  robots,
  tasks,
  activeFloor,
  focusRequest = 0,
  transferInventory = [],
  shuttleLifts = [],
  selectedRobotId = '',
  followSelectedRobot = false,
  onSelectRobot,
  paused = false,
  transitionDuration = 0.3,
  pickingArmLayoutPlan,
  simulationTestHandoff,
  simulationTestCargo = [],
  simulationTestState,
  showSimulationDebugLabels = false,
  pickingArmFocused = false,
  pickingArmFocusRequest = 0,
  simulationMotionEpoch = 'simulation-test-idle',
}: Simulation3DSceneProps) {
  // 拓扑层与高频动态状态彻底分离：机器人 tick 不再重建数万件货架构件。
  const spec = useMemo(() => buildSceneSpec(topology), [topology])
  // 正在输送货箱的 2×2 输送块（键 → 最近活跃时间戳），驱动局部滚筒旋转。
  const activeBlocksRef = useRef(new Map<string, number>())
  const simulationConveyorVisuals = useMemo(() => {
    const blocks = new Map<string, {
      key: string
      row: number
      col: number
      floor: string
      direction: [number, number]
      levels: number
      ground: boolean
    }>()
    const blockByNode = new Map<string, string>()
    const groundPath = simulationTestState?.conveyor.path ?? []
    const parseGround = (nodeId: string) => {
      const [floor, row, col] = nodeId.split(':')
      return { floor, row: Number(row), col: Number(col) }
    }
    groundPath.forEach((nodeId, index) => {
      const current = parseGround(nodeId)
      const adjacent = parseGround(groundPath[Math.min(index + 1, groundPath.length - 1)] ?? nodeId)
      const fallback = parseGround(groundPath[Math.max(0, index - 1)] ?? nodeId)
      const dx = adjacent.col - current.col || current.col - fallback.col
      const dz = adjacent.row - current.row || current.row - fallback.row
      const key = `${current.floor}:${current.row}:${current.col}:lower`
      blocks.set(key, {
        key, row: current.row, col: current.col, floor: current.floor,
        direction: [Math.sign(dx), Math.sign(dz)], levels: 1, ground: true,
      })
      blockByNode.set(nodeId, key)
    })

    const upperPath = simulationTestState?.mergeConveyor.path ?? []
    upperPath.forEach((step, index) => {
      const cell = topology.cells.find((item) => item.id === step.nodeId)
      if (!cell) return
      const anchor = conveyorAnchorOf(cell)
      const next = upperPath[Math.min(index + 1, upperPath.length - 1)] ?? step
      const previous = upperPath[Math.max(0, index - 1)] ?? step
      const dx = Number(next.col) - Number(step.col) || Number(step.col) - Number(previous.col)
      const dz = Number(next.row) - Number(step.row) || Number(step.row) - Number(previous.row)
      const floor = cell.floor ?? '1F'
      const key = `${floor}:${anchor.row}:${anchor.col}:upper`
      if (!blocks.has(key)) {
        blocks.set(key, {
          key, row: anchor.row, col: anchor.col, floor,
          direction: [Math.sign(dx), Math.sign(dz)],
          levels: Math.max(2, cell.levels ?? 2), ground: false,
        })
      }
      blockByNode.set(step.nodeId, key)
    })
    return { blocks: [...blocks.values()], blockByNode }
  }, [simulationTestState?.conveyor.path, simulationTestState?.mergeConveyor.path, topology])
  const simulationConveyorPaths = useMemo(() => ({
    ground: simulationTestState ? groundConveyorWaypoints(simulationTestState.conveyor) : [],
    upper: simulationTestState ? upperConveyorWaypoints(simulationTestState.mergeConveyor.path) : [],
  }), [simulationTestState])
  const visualTransitionDuration = boundedSimulationTransitionDuration(transitionDuration)
  const visualSpeedMultiplier = Math.min(4, Math.max(1, 0.6 / Math.max(0.001, transitionDuration)))

  // 实时机器人替换静态设备节点（robot.id == 拓扑 device node id），避免重复绘制。
  const liveRobotIdSignature = robots.map((robot) => robot.id).sort().join('|')
  const liveRobotIds = useMemo(() => new Set(liveRobotIdSignature ? liveRobotIdSignature.split('|') : []), [liveRobotIdSignature])

  const layers = useMemo(() => {
    const boxes = [] as Extract<(typeof spec.items)[number], { kind: 'box' }>[]
    const racks = [] as Extract<(typeof spec.items)[number], { kind: 'rack' }>[]
    const cylinders: SceneCylinder[] = []
    const slabs = [] as Extract<(typeof spec.items)[number], { kind: 'slab' }>[]
    const towers = [] as Extract<(typeof spec.items)[number], { kind: 'tower' }>[]
    for (const item of spec.items) {
      if (item.entity?.kind === 'node' && liveRobotIds.has(item.entity.id)) continue
      if (item.kind === 'box') boxes.push(item)
      else if (item.kind === 'rack') racks.push(item)
      else if (item.kind === 'cyl') cylinders.push(item)
      else if (item.kind === 'slab') slabs.push(item)
      else towers.push(item)
    }
    return { boxes, cylinders, racks, slabs, towers }
  }, [liveRobotIds, spec])

  // 库存引用仅在货物真正变化时改变，因此商品实例层不会随每个 tick 重建。
  const goodsBoxes = useMemo(
    () => buildGoodsSceneBoxes(topology, goods ?? [], spec.floors),
    [goods, spec.floors, topology],
  )

  // 已交付货箱：任务完成时，货箱出现在出入口旁边的输送带端部，从带面下方升入，
  // 随后沿输送方向被带走，到输送线末端消失。
  const deliveryStage = useMemo(() => {
    const { rows, cols } = topology.grid
    const cells = topology.cells
    const cellById = new Map(cells.map((cell) => [cell.id, cell]))
    const cellByPosition = new Map(
      cells.map((cell) => [`${cell.floor ?? '1F'}:${cell.row}:${cell.col}`, cell]),
    )
    const counters = new Map<string, number>()
    const pathCache = new Map<string, {
      points: [number, number, number][]
      beltCells: (typeof cells)[number][]
    }>()
    const blockMap = new Map<
      string,
      { key: string; row: number; col: number; floor: string; direction: [number, number]; levels: number; ground: boolean }
    >()
    const blockLookup = new Map<string, string>()
    const liftByPosition = new Map(
      topology.nodes
        .filter((node) => node.type === 'lift_transfer')
        .map((node) => [`${node.floor ?? '1F'}:${node.row}:${node.col}`, node]),
    )
    const boxes: {
      key: string
      position: [number, number, number]
      color: string
      path: [number, number, number][]
      hold: boolean
      skipRise: boolean
    }[] = []

    const nearestConveyor = (row: number, col: number, floor: string, preferGround = false) => {
      // 按 port 归属的输送线形式优先取同类输送块；周围没有同类时回退任意输送块，
      // 保证只画了一种线的旧拓扑动画不受影响。
      let best: (typeof cells)[number] | null = null
      let bestDistance = Number.POSITIVE_INFINITY
      let fallback: (typeof cells)[number] | null = null
      let fallbackDistance = Number.POSITIVE_INFINITY
      for (const cell of cells) {
        if (cell.type !== 'conveyor' || (cell.floor ?? '1F') !== floor) continue
        const distance = Math.abs(cell.row - row) + Math.abs(cell.col - col)
        if (distance > 5) continue
        const isGround = Boolean(cell.conveyorGround)
        if (isGround === preferGround) {
          if (distance < bestDistance) {
            bestDistance = distance
            best = cell
          }
        } else if (distance < fallbackDistance) {
          fallbackDistance = distance
          fallback = cell
        }
      }
      return best ?? fallback
    }

    // 从落货块出发，沿 conveyorDirection 逐格走到输送线末端，生成货箱的行走折线；
    // 末端若是升降移栽机，则在移栽机上从下层升到上层。
    // lowerY 为架空线带面高度，groundBeltY 为地面线带面高度；逐格按所在块取对应高度。
    const conveyorPath = (
      start: (typeof cells)[number],
      lowerY: number,
      groundBeltY: number,
      deck: 'lower' | 'upper' = 'lower',
    ) => {
      const points: [number, number, number][] = []
      // 途经输送格：上层带面与下层是同一批格子，出站/回程沿这批格子行走，避免直线漂移。
      const beltCells: (typeof cells)[number][] = []
      let y = lowerY
      let cell: (typeof cells)[number] | undefined = start
      let guard = 0
      while (cell && cell.type === 'conveyor' && guard < 400) {
        guard += 1
        y = cell.conveyorGround ? groundBeltY : lowerY + (deck === 'upper' ? CONVEYOR_DECK_STEP : 0)
        points.push([worldX(cell.col, cols) + CELL / 2, y, worldZ(cell.row, rows) - CELL / 2])
        beltCells.push(cell)
        const blockAnchor = conveyorAnchorOf(cell)
        const blockKey = `${cell.floor ?? '1F'}:${blockAnchor.row}:${blockAnchor.col}`
        // 2×2 块的四个格都映射到同一个块，货箱在块内任何位置都会触发滚筒旋转。
        for (let dr = 0; dr < 2; dr += 1) {
          for (let dc = 0; dc < 2; dc += 1) {
            blockLookup.set(`${blockAnchor.row + dr}:${blockAnchor.col + dc}`, blockKey)
          }
        }
        if (!blockMap.has(blockKey)) {
          const anchorCell = cellByPosition.get(
            `${cell.floor ?? '1F'}:${blockAnchor.row}:${blockAnchor.col}`,
          )
          const anchorDecks = conveyorDecksOf(topology, anchorCell ?? cell)
          const blockDirection = DIRECTION_VEC3[conveyorDirectionFromSide(
            (anchorDecks[deck] ?? anchorDecks.lower).outputSide,
          )]
          blockMap.set(blockKey, {
            key: blockKey,
            row: blockAnchor.row,
            col: blockAnchor.col,
            floor: cell.floor ?? '1F',
            direction: [blockDirection.dx, blockDirection.dz],
            levels: Math.max(1, anchorCell?.levels ?? cell.levels ?? 1),
            ground: Boolean(anchorCell?.conveyorGround ?? cell.conveyorGround),
          })
        }
        const anchorCell = cellByPosition.get(
          `${cell.floor ?? '1F'}:${blockAnchor.row}:${blockAnchor.col}`,
        ) ?? cell
        const decks = conveyorDecksOf(topology, anchorCell)
        const direction = DIRECTION_VEC3[conveyorDirectionFromSide((decks[deck] ?? decks.lower).outputSide)]
        const nextRow = cell.row - direction.dz
        const nextCol = cell.col + direction.dx
        const next = cellByPosition.get(`${cell.floor ?? '1F'}:${nextRow}:${nextCol}`)
        if (next?.type === 'conveyor') {
          cell = next
          continue
        }
        const lift = liftByPosition.get(`${cell.floor ?? '1F'}:${nextRow}:${nextCol}`)
        const levels = Math.max(1, cell.levels ?? 1)
        if (lift && levels > 1) {
          const liftX = worldX(lift.col, cols) + CELL / 2
          const liftZ = worldZ(lift.row, rows) - CELL / 2
          points.push([liftX, y, liftZ])
          y += CONVEYOR_DECK_STEP
          points.push([liftX, y, liftZ])
        }
        break
      }
      return { points, beltCells }
    }

    // 拓扑存在人工拣货位时：货箱在下层输送/升降/上层输送/拣货/放置/回程/出入口等待阶段都要可见；
    // CTU 在出入口取走（return_to_storage）后才消失。
    const hasPickStation = topology.zones.some((zone) => zone.type === 'pick_station')
    const pickFlowPhases = new Set([
      'conveyor_to_lift',
      'lift_to_upper',
      'conveyor_to_pick',
      'picking',
      'place_goods',
      'conveyor_return',
      'pickup_from_port',
    ])
    // 放置区货物：place_goods 及之后阶段（含 completed）常驻渲染。
    const dropGoodsPhases = new Set([
      'place_goods',
      'conveyor_return',
      'pickup_from_port',
      'return_to_storage',
      'completed',
    ])
    const dropZones = topology.zones.filter((zone) => zone.type === 'drop_zone')
    const dropGoods: { key: string; position: [number, number, number]; color: string }[] = []
    const robotsById = new Map(robots.map((robot) => [robot.id, robot]))

    for (const task of tasks) {
      if (!task.lines.length) continue

      // 拣出货物渲染到放置区：优先用 task.dropZoneCellId，解析失败回退到最近 drop_zone 的底边中点。
      if (dropGoodsPhases.has(task.phase)) {
        const goods = task.placedGoods?.length ? task.placedGoods : task.lines
        const parsedDrop = task.dropZoneCellId ? parseCellId(task.dropZoneCellId) : null
        let dropRow: number | null = null
        let dropCol: number | null = null
        if (parsedDrop) {
          dropRow = parsedDrop.row
          dropCol = parsedDrop.col
        } else if (dropZones.length) {
          const anchor = task.pickStationCellId ? parseCellId(task.pickStationCellId) : null
          const entryOf = (zone: (typeof dropZones)[number]) => ({
            row: zone.y + zone.height - 1,
            col: zone.x + Math.floor(zone.width / 2),
          })
          const nearest = anchor
            ? dropZones.reduce((best, zone) => {
                const a = entryOf(best)
                const b = entryOf(zone)
                const bestDist = Math.abs(a.row - anchor.row) + Math.abs(a.col - anchor.col)
                const dist = Math.abs(b.row - anchor.row) + Math.abs(b.col - anchor.col)
                return dist < bestDist ? zone : best
              })
            : dropZones[0]
          const entry = entryOf(nearest)
          dropRow = entry.row
          dropCol = entry.col
        }
        if (dropRow !== null && dropCol !== null) {
          goods.forEach((line, index) => {
            const offsetX = ((index % 2) - 0.5) * 0.44
            const offsetZ = ((Math.floor(index / 2) % 2) - 0.5) * 0.44
            dropGoods.push({
              key: `${task.id}-drop-${index}`,
              position: [
                worldX(dropCol as number, cols) + CELL / 2 + offsetX,
                0.16 + Math.floor(index / 4) * 0.3,
                worldZ(dropRow as number, rows) - CELL / 2 + offsetZ,
              ],
              color: GOODS_CATEGORY_COLORS[line.largeCategory ?? ''] ?? GOODS_DEFAULT_COLOR,
            })
          })
        }
      }

      const robot = task.assignedRobotId ? robotsById.get(task.assignedRobotId) : undefined
      if (hasPickStation && robot?.type !== 'shuttle') {
        if (!pickFlowPhases.has(task.phase)) continue
      } else {
        // 没有拣货位、或四向车任务（整箱走地面输送线，交付后即完成）：
        // 任务完成即播放；机器人已停在出入口的交付阶段也提前生成，
        // 避免第一辆车交付瞬间因阶段推进而错过动画。
        const atPort = robot?.currentCellId === task.deliveryCellId
        if (task.phase !== 'completed' && !(task.phase === 'delivery_to_port' && atPort)) continue
      }
      // 按车上实际载货箱数生成，而不是按订单行数。
      const slots = task.transitGoods?.length
        ? task.transitGoods
        : task.deliveredGoods?.length
          ? task.deliveredGoods
          : task.lines
      if (!slots.length) continue
      // 出入口入口格可能不在 cells 里（只是空白格），用 cellId 解析出的行列兜底。
      const entryCell = cellById.get(task.deliveryCellId)
      let entryRow: number
      let entryCol: number
      let entryFloor = '1F'
      if (entryCell) {
        entryRow = entryCell.row
        entryCol = entryCell.col
        entryFloor = entryCell.floor ?? '1F'
      } else {
        const parsed = parseCellId(task.deliveryCellId)
        if (!parsed) continue
        entryRow = parsed.row
        entryCol = parsed.col
        entryFloor = parsed.floor ?? '1F'
      }
      const portZone = task.assignedPortId
        ? topology.zones.find((zone) => zone.id === task.assignedPortId)
        : null
      const bindingParts = portZone?.conveyorBinding?.blockId.split(':')
      const boundConveyor = bindingParts && bindingParts.length >= 3
        ? cellByPosition.get(
            `${bindingParts[bindingParts.length - 3]}:${Number(bindingParts[bindingParts.length - 2])}:${Number(bindingParts[bindingParts.length - 1])}`,
          )
        : null
      const conveyor = boundConveyor ?? nearestConveyor(entryRow, entryCol, entryFloor, robot?.type === 'shuttle')
      if (!conveyor) continue
      const anchor = conveyorAnchorOf(conveyor)
      const anchorCell = cells.find(
        (cell) =>
          cell.row === anchor.row &&
          cell.col === anchor.col &&
          (cell.floor ?? '1F') === (conveyor.floor ?? '1F'),
      ) ?? conveyor
      const anchorKey = `${conveyor.floor ?? '1F'}:${anchor.row}:${anchor.col}`
      const floorIndex = Math.max(0, spec.floors.indexOf(conveyor.floor ?? '1F'))
      // 货箱从最低一层带面进入（多层线也是先上底层）；地面输送线（整箱）整段贴地。
      const groundRun = Boolean(anchorCell.conveyorGround ?? conveyor.conveyorGround)
      const deckY = floorIndex * FLOOR_HEIGHT + CONVEYOR_DECK_BASE
      const lowerY = deckY + 0.16
      const groundBeltY = floorIndex * FLOOR_HEIGHT + CONVEYOR_GROUND_BELT_Y
      const baseY = groundRun ? groundBeltY : lowerY
      const routeKey = `${anchorKey}:${groundRun ? 'g' : 'e'}:lower`
      let routeResult = pathCache.get(routeKey)
      if (!routeResult) {
        routeResult = conveyorPath(anchorCell, lowerY, groundBeltY, 'lower')
        pathCache.set(routeKey, routeResult)
      }
      const { points: route, beltCells } = routeResult
      const stationCell = task.pickStationCellId ? parseCellId(task.pickStationCellId) : null
      // 地面输送线不衔接人工拣货台（整箱直接沿带面走到输送线末端）。
      const hold = Boolean(hasPickStation && stationCell && !groundRun)
      const upperY = lowerY + CONVEYOR_DECK_STEP
      // 路线末端是否真的经过升降机抬升（末点明显高于前一点）；单层线没有升降段。
      const lifted = route.length >= 2 && route[route.length - 1][1] > route[route.length - 2][1] + 0.01
      const stationY = lifted ? upperY : baseY
      const stationRaw: [number, number, number] | null = stationCell && !groundRun
        ? [
            worldX(stationCell.col, cols) + CELL / 2,
            stationY,
            worldZ(stationCell.row, rows) - CELL / 2,
          ]
        : null
      // 保证升降是「先停到设备位置，再垂直上升」，避免斜线漂移。
      const liftRoute =
        lifted
          ? route
          : route.length && stationY > baseY + 0.01
            ? [
                ...route,
                [
                  route[route.length - 1][0],
                  stationY,
                  route[route.length - 1][2],
                ] as [number, number, number],
              ]
            : route
      const routeDecks = conveyorDecksOf(topology, anchorCell)
      const direction = DIRECTION_VEC3[conveyorDirectionFromSide(routeDecks.lower.outputSide)]
      const perp: [number, number] = [-direction.dz, direction.dx]
      const cx = worldX(anchor.col, cols) + CELL / 2
      const cz = worldZ(anchor.row, rows) - CELL / 2
      const entryX = worldX(entryCol, cols) + CELL / 2
      const entryZ = worldZ(entryRow, rows) - CELL / 2
      // port 在输送带哪一侧，货箱就从那一侧的端部进入。
      const entrySide = Math.sign((entryX - cx) * direction.dx + (entryZ - cz) * direction.dz) || -1
      const edge = CELL - 0.22
      const portPoint: [number, number, number] = [entryX, stationY, entryZ]
      // 上层带面各格坐标（与下层同一批格子，抬到上层带面高度）。
      const beltUpperPoints = beltCells.map(
        (cell) =>
          [
            worldX(cell.col, cols) + CELL / 2,
            stationY,
            worldZ(cell.row, rows) - CELL / 2,
          ] as [number, number, number],
      )
      // 出站/回程都沿上层带面行走，只允许 ≤1 格的短跳上下拣货台，禁止长距离直线漂移。
      let outboundLeg: [number, number, number][] = liftRoute
      let returnLeg: [number, number, number][] = liftRoute
      const explicitUpperDeck = anchorCell.conveyorDecks?.upper?.enabled
      if (explicitUpperDeck && beltCells.length) {
        // 新模型：下层只负责去程到升降端；上层有自己的有向路线，专门把空箱送回 CTU 取箱口。
        const upperStart = beltCells[beltCells.length - 1]
        const upperRoute = conveyorPath(upperStart, lowerY, groundBeltY, 'upper').points
        const upperStartPoint = upperRoute[0] ?? liftRoute[liftRoute.length - 1] ?? portPoint
        const stationPoint = stationRaw ?? upperStartPoint
        outboundLeg = stationPoint === upperStartPoint ? liftRoute : [...liftRoute, stationPoint]
        returnLeg = [stationPoint, ...upperRoute, portPoint]
      } else if (stationRaw) {
        // 旧拓扑兼容：没有分层路由时沿用历史反向数组动画。
        let bestIdx = -1
        let bestDist = Number.POSITIVE_INFINITY
        beltCells.forEach((cell, idx) => {
          const distance =
            Math.abs(worldX(cell.col, cols) + CELL / 2 - stationRaw[0]) +
            Math.abs(worldZ(cell.row, rows) - CELL / 2 - stationRaw[2])
          if (distance < bestDist) {
            bestDist = distance
            bestIdx = idx
          }
        })
        const stationPoint: [number, number, number] | null =
          bestIdx >= 0 && bestDist <= 3 ? stationRaw : null
        if (stationPoint) {
          // 出站：升降机顶部 → 沿上层带面反向走到最近格 → 短跳上拣货台。
          outboundLeg = [...liftRoute, ...beltUpperPoints.slice(bestIdx).reverse(), stationPoint]
          // 回程：拣货台 → 最近格 → 沿上层带面走到出入口端 → 出入口上方等待 CTU。
          returnLeg = [stationPoint, ...beltUpperPoints.slice(0, bestIdx + 1).reverse(), portPoint]
        }
      }
      const stationRoute = outboundLeg
      const returnPath = returnLeg
      const transport = task.conveyorTransport
      const transportBlockId = transport?.phase === task.phase ? transport.currentBlockId : null
      const transportBlock = transportBlockId
        ? transportBlockId.split(':')
        : null
      const transportPoint: [number, number, number] | null = transportBlock && transportBlock.length >= 4
        ? (() => {
            const transportRow = Number(transportBlock[transportBlock.length - 3])
            const transportCol = Number(transportBlock[transportBlock.length - 2])
            const transportDeck = transportBlock[transportBlock.length - 1]
            if (!Number.isFinite(transportRow) || !Number.isFinite(transportCol)) return null
            return [
              worldX(transportCol, cols) + CELL / 2,
              lowerY + (transportDeck === 'upper' ? CONVEYOR_DECK_STEP : 0),
              worldZ(transportRow, rows) - CELL / 2,
            ]
          })()
        : null

      slots.forEach((line, lineIndex) => {
        const index = counters.get(anchorKey) ?? 0
        counters.set(anchorKey, index + 1)
        const slot = index % 4
        const stack = Math.floor(index / 4)
        const along = entrySide * edge - entrySide * slot * 0.32
        const side = ((stack % 2) - 0.5) * 0.45
        const x = cx + direction.dx * along + perp[0] * side
        const z = cz + direction.dz * along + perp[1] * side
        const startPoint: [number, number, number] = [
          x,
          baseY + Math.floor(stack / 2) * 0.32,
          z,
        ]
        if (transportPoint) {
          const synchronizedPoint: [number, number, number] = [
            transportPoint[0] + (lineIndex % 2) * 0.12 - 0.06,
            transportPoint[1] + Math.floor(lineIndex / 2) * 0.14,
            transportPoint[2] + (lineIndex % 2) * 0.12 - 0.06,
          ]
          boxes.push({
            key: `${task.id}-${lineIndex}-${transportBlockId}`,
            position: synchronizedPoint,
            path: [synchronizedPoint],
            color: GOODS_CATEGORY_COLORS[line.largeCategory ?? ''] ?? GOODS_DEFAULT_COLOR,
            hold: true,
            skipRise: true,
          })
          return
        }
        const outboundPath = [startPoint, ...stationRoute]
        const returning = task.phase === 'conveyor_return'
        const waitingAtPort = task.phase === 'pickup_from_port'
        if (returning) {
          boxes.push({
            key: `${task.id}-${lineIndex}-return`,
            position: returnPath[0] ?? startPoint,
            path: returnPath,
            color: GOODS_CATEGORY_COLORS[line.largeCategory ?? ''] ?? GOODS_DEFAULT_COLOR,
            hold: true,
            skipRise: true,
          })
        } else if (waitingAtPort) {
          boxes.push({
            key: `${task.id}-${lineIndex}-port`,
            position: returnPath[returnPath.length - 1] ?? startPoint,
            path: [returnPath[returnPath.length - 1] ?? startPoint],
            color: GOODS_CATEGORY_COLORS[line.largeCategory ?? ''] ?? GOODS_DEFAULT_COLOR,
            hold: true,
            skipRise: true,
          })
        } else {
          boxes.push({
            key: `${task.id}-${lineIndex}`,
            position: startPoint,
            // 先沿带面进入，再沿输送线走；到升降移栽机处抬升到第二层。
            path: outboundPath,
            color: GOODS_CATEGORY_COLORS[line.largeCategory ?? ''] ?? GOODS_DEFAULT_COLOR,
            hold,
            skipRise: false,
          })
        }
      })
    }
    return { boxes, blocks: [...blockMap.values()], blockLookup, dropGoods }
  }, [robots, spec, tasks, topology])


  // 取货高亮：机器人当前处于 CTU 取货阶段时，高亮其当前取货行对应的商品箱。
  const highlightKeySignature = useMemo(() => {
    const keys: string[] = []
    const taskById = new Map(tasks.map((task) => [task.id, task]))
    for (const robot of robots) {
      if (!robot.currentTaskId) continue
      const task = taskById.get(robot.currentTaskId)
      if (!task || task.phase !== 'pickup_from_ctu') continue
      if (robot.handlingAction?.nodeId) {
        keys.push(`goods-${robot.handlingAction.nodeId}-${robot.handlingAction.targetLayer}`)
        continue
      }
      const line = task.lines?.[task.currentLineIndex]
      if (line) keys.push(`goods-${line.nodeId}-${line.layer}`)
    }
    return keys.sort().join('|')
  }, [robots, tasks])
  const highlightKeys = useMemo(
    () => new Set(highlightKeySignature ? highlightKeySignature.split('|') : []),
    [highlightKeySignature],
  )

  // 当前订单所有目标货箱用统一青色显示；当前正在处理的箱子仍用黄色覆盖。
  const focusKeySignature = useMemo(() => tasks
    .flatMap((task) => task.lines ?? [])
    .map((line) => `goods-${line.nodeId}-${line.layer}`)
    .sort()
    .join('|'), [tasks])
  const focusKeys = useMemo(
    () => new Set(focusKeySignature ? focusKeySignature.split('|') : []),
    [focusKeySignature],
  )

  const selectedRobot = robots.find((robot) => robot.id === selectedRobotId)
  const focusTask = (selectedRobot?.currentTaskId
    ? tasks.find((task) => task.id === selectedRobot.currentTaskId)
    : undefined)
    ?? tasks.find((task) => task.status === 'assigned')
    ?? tasks.find((task) => !['completed', 'cancelled', 'failed'].includes(task.status))
  const focusRobot = selectedRobot
    ?? (focusTask?.assignedRobotId ? robots.find((robot) => robot.id === focusTask.assignedRobotId) : undefined)
  const isPicking = focusTask?.phase === 'pickup_from_ctu' || focusTask?.phase === 'pickup_from_shuttle'
  const handlingAction = focusRobot?.handlingAction
  const handlingLine = handlingAction
    ? focusTask?.lines.find((line) => line.nodeId === handlingAction.nodeId && line.layer === handlingAction.targetLayer)
    : undefined
  const focusLine = isPicking
    ? handlingLine ?? focusTask?.lines?.[Math.min(focusTask.currentLineIndex, Math.max(0, focusTask.lines.length - 1))]
    : undefined
  const focusTargetCellId = handlingAction?.cellId || focusLine?.cellId || focusTask?.deliveryCellId
  const focusTargetLayer = handlingAction?.targetLayer ?? focusLine?.layer ?? focusTask?.targetLayer ?? 1
  const focusTargetPosition = useMemo(
    () => focusTargetCellId
      ? cellWorldPosition(focusTargetCellId, focusTargetLayer, spec.floors, spec.cols, spec.rows, focusRobot?.type)
      : null,
    [focusRobot?.type, focusTargetCellId, focusTargetLayer, spec.cols, spec.floors, spec.rows],
  )
  const routePoints = useMemo(() => {
    if (!focusRobot?.path?.length) return [] as [number, number, number][]
    return focusRobot.path.flatMap((cellId, index) => {
      const parsed = parseCellId(cellId)
      if (!parsed) return []
      const layer = focusRobot.pathLayers?.[index] ?? 1
      const isShuttle = focusRobot.type === 'shuttle'
      if (isShuttle) {
        const [x, y, z] = shuttleTrackWorldPosition(parsed.row, parsed.col, layer, spec.cols, spec.rows)
        return [[x, y + 0.05, z] as [number, number, number]]
      }
      const floorIndex = Math.max(0, spec.floors.indexOf(parsed.floor))
      return [[
        worldX(parsed.col, spec.cols),
        floorIndex * FLOOR_HEIGHT + Math.max(0.18, (layer - 1) * ZONE_LEVEL_HEIGHT.ctu + 0.18),
        worldZ(parsed.row, spec.rows),
      ] as [number, number, number]]
    })
  }, [focusRobot?.path, focusRobot?.pathLayers, focusRobot?.type, spec.cols, spec.floors, spec.rows])
  const transferTaskByLift = useMemo(() => {
    const result = new Map<string, SimulationTask>()
    for (const task of tasks) {
      if (task.mode === 'shuttle_transfer' && task.plan?.chosen_lift) result.set(task.plan.chosen_lift, task)
    }
    return result
  }, [tasks])

  return (
    <Canvas
      dpr={1}
      frameloop="demand"
      gl={{ antialias: false, alpha: true }}
      camera={{ position: spec.camera.position, fov: 42, near: 0.1, far: 4000 }}
      style={{ background: 'transparent' }}
    >
      <color attach="background" args={['#0a0f19']} />
      <fog attach="fog" args={['#0a0f19', 500, 1600]} />
      <ambientLight intensity={0.65} />
      <hemisphereLight intensity={0.45} color="#d7dee9" groundColor="#0b101a" />
      <directionalLight position={[14, 24, 12]} intensity={1.5} color="#fff4e6" />
      <directionalLight position={[-12, 8, -14]} intensity={0.35} color="#7dd3fc" />
      <directionalLight position={[0, 30, -4]} intensity={0.4} color="#e2e8f0" />

      {layers.slabs.map((slab) => (
        <SlabView key={slab.key} slab={slab} isActive={slab.label === activeFloor} cols={spec.cols} rows={spec.rows} />
      ))}

      <BatchedTopologyMeshes
        boxes={layers.boxes}
        racks={layers.racks}
        cylinders={layers.cylinders}
        highlightKeys={EMPTY_HIGHLIGHTS}
        hoveredEntityId={null}
        canSelect={false}
        onEntityDown={ignoreInteraction}
        setHoverEntity={ignoreInteraction}
      />

      {layers.towers.map((tower) => (
        <TowerView key={tower.key} tower={tower} highlighted={false} hovered={false} selectProps={{}} />
      ))}

      <GoodsBoxes
        boxes={goodsBoxes}
        highlightKeys={highlightKeys}
        focusKeys={focusKeys}
        dimUnfocused={focusKeys.size > 0}
      />

      {deliveryStage.blocks.map((block) => (
        <SpinningRollerBlock
          key={block.key}
          block={block}
          cols={topology.grid.cols}
          rows={topology.grid.rows}
          floorY={Math.max(0, spec.floors.indexOf(block.floor)) * FLOOR_HEIGHT}
          activeBlocks={activeBlocksRef}
          paused={paused}
          speedMultiplier={visualSpeedMultiplier}
        />
      ))}

      {simulationConveyorVisuals.blocks.map((block) => (
        <SpinningRollerBlock
          key={`simulation-test-${block.key}`}
          block={block}
          cols={topology.grid.cols}
          rows={topology.grid.rows}
          floorY={Math.max(0, spec.floors.indexOf(block.floor)) * FLOOR_HEIGHT}
          activeBlocks={activeBlocksRef}
          paused={paused}
          speedMultiplier={visualSpeedMultiplier}
        />
      ))}

      {deliveryStage.boxes.map((box) => (
        <DeliveredBox
          key={box.key}
          position={box.position}
          color={box.color}
          path={box.path}
          blockLookup={deliveryStage.blockLookup}
          activeBlocks={activeBlocksRef}
          cols={topology.grid.cols}
          rows={topology.grid.rows}
          hold={box.hold}
          skipRise={box.skipRise}
        />
      ))}

      {deliveryStage.dropGoods.map((item) => (
        <mesh key={item.key} position={item.position}>
          <boxGeometry args={[0.28, 0.28, 0.28]} />
          <meshStandardMaterial
            color={item.color}
            emissive={item.color}
            emissiveIntensity={0.18}
            roughness={0.55}
            metalness={0.12}
          />
        </mesh>
      ))}

      {transferInventory.map((balance) => (
        <TransferInventoryMarker key={`${balance.locationId}-${balance.skuId}`} balance={balance} />
      ))}

      {shuttleLifts.map((lift) => {
        const task = transferTaskByLift.get(lift.lift_id)
        const robot = task?.assignedRobotId ? robots.find((item) => item.id === task.assignedRobotId) : undefined
        const simulationTestLiftActive = simulationTestState?.lift.deviceId === lift.lift_id
          && simulationTestState.lift.occupiedCargoIds.length > 0
        const simulationTestRobot = simulationTestLiftActive
          ? robots.find((item) => item.id === simulationTestState.shuttle.deviceId)
          : undefined
        return (
          <ShuttleLiftView
            key={lift.lift_id}
            lift={lift}
            cols={spec.cols}
            rows={spec.rows}
            activeTask={task}
            activeRobot={simulationTestRobot ?? robot}
            simulationTestActive={simulationTestLiftActive}
            simulationTestPhase={simulationTestState?.cargo.find(
              (cargo) => cargo.cargoId === simulationTestState.lift.occupiedCargoIds[0],
            )?.status}
            paused={paused}
            transitionDuration={visualTransitionDuration}
          />
        )
      })}

      <PickingArmAuthoritativeLayer
        topology={topology}
        activeFloor={activeFloor}
        layoutPlan={pickingArmLayoutPlan}
        simulationTest={simulationTestState}
        paused={paused}
        transitionDuration={visualTransitionDuration}
        highlighted={pickingArmFocused}
      />

      <SimulationOnlyHandoffLayer
        handoff={simulationTestHandoff}
        showLabels={showSimulationDebugLabels}
      />

      <SimulationTestBreakPackLayer
        leftFlow={simulationTestState?.leftFlow}
        showLabel={showSimulationDebugLabels}
      />

      <AuthoritativeSimulationCargoLayer
        cargo={simulationTestCargo}
        armActiveCargoId={simulationTestState?.arm.activeCargoId}
        paused={paused}
        transitionDuration={visualTransitionDuration}
        motionEpoch={simulationMotionEpoch}
        conveyorBlockByNode={simulationConveyorVisuals.blockByNode}
        activeBlocks={activeBlocksRef}
        groundConveyorPath={simulationConveyorPaths.ground}
        upperConveyorPath={simulationConveyorPaths.upper}
        showTrackDebug={showSimulationDebugLabels}
      />

      {routePoints.length > 1 ? (
        <Line
          points={routePoints}
          color="#fde047"
          lineWidth={3.2}
          transparent
          opacity={0.95}
          depthTest={false}
          renderOrder={12}
        />
      ) : null}
      {focusTargetPosition && focusTask ? (
        <TaskTargetMarker
          position={focusTargetPosition}
          location={focusLine
            ? `${focusLine.nodeId} · 第 ${focusLine.layer} 层`
            : focusTask.mode === 'shuttle_transfer'
              ? `${focusTask.destinationLocationId ?? focusTask.deliveryCellId} · L${focusTask.targetLayer}`
              : '出库交付点'}
        />
      ) : null}

      {robots.map((robot) => {
        const position: [number, number, number] = robot.type === 'shuttle'
          ? shuttleTrackWorldPosition(
            robot.currentRow,
            robot.currentCol,
            Number(robot.currentLayer ?? 1),
            spec.cols,
            spec.rows,
          )
          : robotWorldPosition(
            robot.currentFloor ?? '1F',
            robot.currentRow,
            robot.currentCol,
            spec.floors,
            spec.cols,
            spec.rows,
          )
        const robotVisual = (
          <>
            {robot.type === 'ctu' ? (
              <CtuCart load={robot.load} carrying={robot.carrying} action={robot.handlingAction} paused={paused} />
            ) : <ShuttleCart carrying={robot.carrying?.[0]} />}
            {robot.id === (selectedRobotId || focusRobot?.id) ? (
              <RobotFocusMarker
                robotId={robot.id}
                phase={`${robot.phaseLabel || focusTask?.phaseLabel || '执行任务'} · ${robot.load}/${robot.capacity} 箱`}
              />
            ) : null}
          </>
        )
        const isAuthoritativeTestShuttle = robot.type === 'shuttle'
          && simulationTestState?.shuttle.deviceId === robot.id
          && simulationTestState.shuttle.outboundFrames?.length
          && simulationTestState.shuttle.returnFrames?.length
        if (isAuthoritativeTestShuttle && simulationTestState) {
          const returning = simulationTestState.shuttle.phase === 'returning'
          const frames = returning
            ? simulationTestState.shuttle.returnFrames!
            : simulationTestState.shuttle.outboundFrames!
          return (
            <ShuttleTrackGroup
              key={`${simulationMotionEpoch}:${robot.id}`}
              position={position}
              frames={frames}
              routeIndex={simulationTestState.shuttle.routeIndex ?? 0}
              routeKey={`${simulationMotionEpoch}:${returning ? 'return' : 'outbound'}`}
              paused={paused}
              transitionDuration={Math.min(1.2, Math.max(0.08, transitionDuration))}
              onClick={() => onSelectRobot?.(robot.id)}
              debug={showSimulationDebugLabels}
              cargoOwner={(robot.carrying?.[0] as GoodsSlot & { cargoId?: string } | undefined)?.cargoId ?? 'none'}
              liftId={simulationTestState.lift.occupiedCargoIds.length
                ? simulationTestState.lift.deviceId
                : 'none'}
            >
              {robotVisual}
            </ShuttleTrackGroup>
          )
        }
        const leftCtu = simulationTestState?.leftFlow?.ctu
        const isAuthoritativeTestCtu = robot.type === 'ctu'
          && leftCtu?.deviceId === robot.id
        if (isAuthoritativeTestCtu && leftCtu) {
          const returning = leftCtu.routeName === 'to_storage'
          const toStation = leftCtu.routeName === 'to_station'
          const frames = returning
            ? leftCtu.toStorageFrames
            : toStation
              ? leftCtu.toStationFrames
              : leftCtu.toSourceFrames
          return (
            <ShuttleTrackGroup
              key={`${simulationMotionEpoch}:${robot.id}`}
              position={position}
              frames={frames}
              routeIndex={leftCtu.routeIndex}
              routeKey={`${simulationMotionEpoch}:ctu:${leftCtu.routeName}`}
              paused={paused}
              transitionDuration={Math.min(1.2, Math.max(0.08, transitionDuration))}
              onClick={() => onSelectRobot?.(robot.id)}
              debug={showSimulationDebugLabels}
              cargoOwner={leftCtu.activeCargoId ?? 'none'}
            >
              <group rotation={[0, Math.PI, 0]}>{robotVisual}</group>
            </ShuttleTrackGroup>
          )
        }
        return (
          <RobotGroup
            key={`${simulationMotionEpoch}:${robot.id}`}
            position={position}
            onClick={() => onSelectRobot?.(robot.id)}
            paused={paused}
            transitionDuration={visualTransitionDuration}
            orientToMotion={robot.type === 'shuttle'}
          >
            {robotVisual}
          </RobotGroup>
        )
      })}

      <OrbitControls
        makeDefault
        target={spec.camera.target}
        enableDamping
        dampingFactor={0.12}
        minDistance={2}
        maxDistance={2000}
        screenSpacePanning
        enableZoom={false}
        mouseButtons={{ LEFT: undefined, MIDDLE: MOUSE.ROTATE, RIGHT: MOUSE.PAN }}
      />
      <CameraTaskFocus target={focusTargetPosition} request={focusRequest} />
      <CameraTaskFocus
        target={pickingArmLayoutPlan?.pickingArm && activeFloor === '1F'
          ? [
              worldX(pickingArmLayoutPlan.pickingArm.installation.col, spec.cols),
              3.1,
              worldZ(pickingArmLayoutPlan.pickingArm.installation.row, spec.rows),
            ]
          : null}
        request={pickingArmFocusRequest}
      />
      <CameraRobotFollow
        enabled={followSelectedRobot}
        paused={paused}
        transitionDuration={transitionDuration}
        target={(() => {
          const selected = robots.find((robot) => robot.id === selectedRobotId)
          if (!selected) return null
          if (selected.type === 'shuttle') {
            const [x, y, z] = shuttleTrackWorldPosition(
              selected.currentRow,
              selected.currentCol,
              Number(selected.currentLayer ?? 1),
              spec.cols,
              spec.rows,
            )
            return [x, y + 0.42, z] as [number, number, number]
          }
          const base = robotWorldPosition(
            selected.currentFloor ?? '1F', selected.currentRow, selected.currentCol,
            spec.floors, spec.cols, spec.rows,
          )
          return base
        })()}
      />
      <ZoomToCursor />
    </Canvas>
  )
}

export default memo(Simulation3DScene)
