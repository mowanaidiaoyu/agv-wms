import { memo, useEffect, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Html, Line, OrbitControls } from '@react-three/drei'
import { Group, MOUSE, Mesh, Vector3 } from 'three'
import { BatchedTopologyMeshes, GoodsBoxes, SlabView, TowerView, ZoomToCursor } from './SceneMeshes'
import {
  CELL,
  CONVEYOR_DECK_BASE,
  CONVEYOR_DECK_STEP,
  CTU_ROBOT_LEVELS,
  DIRECTION_VEC3,
  FLOOR_HEIGHT,
  GOODS_CATEGORY_COLORS,
  GOODS_DEFAULT_COLOR,
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
import { conveyorAnchorOf, parseCellId } from '../../lib/topology'
import { advanceInterpolationElapsed, interpolateScalar, interpolationProgress } from '../../lib/simulationMotion'
import type {
  GoodsSlot,
  ShuttleContract,
  SimulationRobot,
  SimulationTask,
  TopologyPayload,
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
}

const SHUTTLE_COLOR = '#c084fc'
const SHUTTLE_EMISSIVE = '#6d28d9'
const EMPTY_HIGHLIGHTS: ReadonlySet<string> = new Set()
const ignoreInteraction = () => undefined

/** 平滑跟随：机器人组的实际位置朝目标格指数逼近，接近后贴死，避免逐 tick 跳格。 */
function RobotGroup({ position, children, onClick, paused = false, transitionDuration = 0.3 }: { position: [number, number, number]; children: ReactNode; onClick?: () => void; paused?: boolean; transitionDuration?: number }) {
  const groupRef = useRef<Group>(null)
  const current = useRef(new Vector3(position[0], position[1], position[2]))
  const start = useRef(new Vector3(position[0], position[1], position[2]))
  const target = useRef(new Vector3(position[0], position[1], position[2]))
  const elapsed = useRef(transitionDuration)
  const durationRef = useRef(transitionDuration)
  const invalidate = useThree((state) => state.invalidate)
  const [x, y, z] = position

  useEffect(() => {
    const targetChanged = target.current.x !== x || target.current.y !== y || target.current.z !== z
    if (targetChanged) {
      start.current.copy(current.current)
      elapsed.current = 0
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
  }, [invalidate, paused, transitionDuration, x, y, z])

  useFrame((_, delta) => {
    if (paused) return
    elapsed.current = advanceInterpolationElapsed(elapsed.current, delta, transitionDuration, false)
    const progress = interpolationProgress(elapsed.current, transitionDuration)
    const cur = current.current.lerpVectors(start.current, target.current, progress)
    if (progress < 1) invalidate()
    if (groupRef.current) groupRef.current.position.copy(cur)
  })

  return <group ref={groupRef} position={current.current} onClick={(event) => { event.stopPropagation(); onClick?.() }}>{children}</group>
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
function ShuttleCart() {
  const w = CELL * 2 * 0.88
  return (
    <group position={[0, 0.42, 0]}>
      <mesh>
        <boxGeometry args={[w, 0.8, w]} />
        <meshStandardMaterial color={SHUTTLE_COLOR} emissive={SHUTTLE_EMISSIVE} emissiveIntensity={0.5} roughness={0.42} />
      </mesh>
      <mesh position={[0, 0.46, 0]}>
        <boxGeometry args={[w * 0.58, 0.12, w * 0.58]} />
        <meshStandardMaterial color="#f5d0fe" emissive="#c026d3" emissiveIntensity={0.8} roughness={0.3} />
      </mesh>
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
}: {
  block: {
    key: string
    row: number
    col: number
    floor: string
    direction: [number, number]
    levels: number
  }
  cols: number
  rows: number
  floorY: number
  activeBlocks: { current: Map<string, number> }
}) {
  const groupRef = useRef<Group>(null)
  const rollerRefs = useRef<(Mesh | null)[]>([])
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
    for (let level = 0; level < Math.min(block.levels, 2); level += 1) {
      const y = floorY + CONVEYOR_DECK_BASE + level * CONVEYOR_DECK_STEP - 0.045
      for (let index = 0; index < count; index += 1) {
        const t = -beltW / 2 + pitch * (index + 0.5)
        result.push({
          position: alongX ? [cx + t, y, cz] : [cx, y, cz + t],
          axis: alongX ? 'z' : 'x',
        })
      }
    }
    return result
  }, [alongX, beltW, block.levels, cx, cz, floorY, pitch])

  useFrame((_, delta) => {
    const group = groupRef.current
    if (!group) return
    const lastActive = activeBlocks.current.get(block.key) ?? 0
    const active = performance.now() - lastActive < ROLLER_ACTIVE_WINDOW_MS
    group.visible = active
    if (!active) return
    angleRef.current += delta * ROLLER_SPIN_SPEED
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
          <mesh
            ref={(mesh) => {
              rollerRefs.current[index] = mesh
            }}
          >
            <cylinderGeometry args={[rollerR, rollerR, beltW - 0.18, 12]} />
            <meshStandardMaterial color="#b8c4d0" roughness={0.35} metalness={0.65} />
          </mesh>
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
  paused,
  transitionDuration,
}: {
  lift: ShuttleContract['lifts'][number]
  cols: number
  rows: number
  activeRobot?: SimulationRobot
  activeTask?: SimulationTask
  paused: boolean
  transitionDuration: number
}) {
  const height = ZONE_LEVEL_HEIGHT.shuttle * 4
  const active = activeTask?.plan?.chosen_lift === lift.lift_id
    && ['enter_lift', 'lift_moving', 'exit_lift'].includes(activeTask.phase)
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
      <mesh position={[0, height / 2, 0]}>
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
      <Html position={[0, height + 0.75, 0]} center zIndexRange={[27, 17]}>
        <div className={`simulation-scene-label shuttle-lift-label ${active ? 'active' : ''}`}>
          <b>{lift.label}</b>
          <span>{active ? activeTask?.phaseLabel : 'L1～L4'}</span>
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
}: Simulation3DSceneProps) {
  // 拓扑层与高频动态状态彻底分离：机器人 tick 不再重建数万件货架构件。
  const spec = useMemo(() => buildSceneSpec(topology), [topology])
  // 正在输送货箱的 2×2 输送块（键 → 最近活跃时间戳），驱动局部滚筒旋转。
  const activeBlocksRef = useRef(new Map<string, number>())

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
      { key: string; row: number; col: number; floor: string; direction: [number, number]; levels: number }
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

    const nearestConveyor = (row: number, col: number, floor: string) => {
      let best: (typeof cells)[number] | null = null
      let bestDistance = Number.POSITIVE_INFINITY
      for (const cell of cells) {
        if (cell.type !== 'conveyor' || (cell.floor ?? '1F') !== floor) continue
        const distance = Math.abs(cell.row - row) + Math.abs(cell.col - col)
        if (distance < bestDistance && distance <= 5) {
          bestDistance = distance
          best = cell
        }
      }
      return best
    }

    // 从落货块出发，沿 conveyorDirection 逐格走到输送线末端，生成货箱的行走折线；
    // 末端若是升降移栽机，则在移栽机上从下层升到上层。
    const conveyorPath = (start: (typeof cells)[number], lowerY: number) => {
      const points: [number, number, number][] = []
      // 途经输送格：上层带面与下层是同一批格子，出站/回程沿这批格子行走，避免直线漂移。
      const beltCells: (typeof cells)[number][] = []
      let y = lowerY
      let cell: (typeof cells)[number] | undefined = start
      let guard = 0
      while (cell && cell.type === 'conveyor' && guard < 400) {
        guard += 1
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
          const blockDirection = DIRECTION_VEC3[
            anchorCell?.conveyorDirection ?? cell.conveyorDirection ?? 'right'
          ]
          blockMap.set(blockKey, {
            key: blockKey,
            row: blockAnchor.row,
            col: blockAnchor.col,
            floor: cell.floor ?? '1F',
            direction: [blockDirection.dx, blockDirection.dz],
            levels: Math.max(1, anchorCell?.levels ?? cell.levels ?? 1),
          })
        }
        const direction = DIRECTION_VEC3[cell.conveyorDirection ?? 'right']
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

      if (hasPickStation) {
        if (!pickFlowPhases.has(task.phase)) continue
      } else {
        // 没有拣货位时：任务完成即播放；CTU 已停在出入口的交付阶段也提前生成，
        // 避免第一辆车交付瞬间因阶段推进而错过动画。
        const robot = task.assignedRobotId ? robotsById.get(task.assignedRobotId) : undefined
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
      const conveyor = nearestConveyor(entryRow, entryCol, entryFloor)
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
      // 货箱从最低一层带面进入（多层线也是先上底层）。
      const deckY = floorIndex * FLOOR_HEIGHT + CONVEYOR_DECK_BASE
      const lowerY = deckY + 0.16
      let routeResult = pathCache.get(anchorKey)
      if (!routeResult) {
        routeResult = conveyorPath(anchorCell, lowerY)
        pathCache.set(anchorKey, routeResult)
      }
      const { points: route, beltCells } = routeResult
      const stationCell = task.pickStationCellId ? parseCellId(task.pickStationCellId) : null
      const hold = Boolean(hasPickStation && stationCell)
      const upperY = lowerY + CONVEYOR_DECK_STEP
      // 路线末端是否真的经过升降机抬升（末点明显高于前一点）；单层线没有升降段。
      const lifted = route.length >= 2 && route[route.length - 1][1] > route[route.length - 2][1] + 0.01
      const stationY = lifted ? upperY : lowerY
      const stationRaw: [number, number, number] | null = stationCell
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
          : route.length && stationY > lowerY + 0.01
            ? [
                ...route,
                [
                  route[route.length - 1][0],
                  stationY,
                  route[route.length - 1][2],
                ] as [number, number, number],
              ]
            : route
      const direction = DIRECTION_VEC3[anchorCell.conveyorDirection ?? conveyor.conveyorDirection ?? 'right']
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
      if (stationRaw) {
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
          lowerY + Math.floor(stack / 2) * 0.32,
          z,
        ]
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
  }, [robots, spec, tasks, topology.cells, topology.grid, topology.nodes, topology.zones])


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
        return (
          <ShuttleLiftView
            key={lift.lift_id}
            lift={lift}
            cols={spec.cols}
            rows={spec.rows}
            activeTask={task}
            activeRobot={robot}
            paused={paused}
            transitionDuration={transitionDuration}
          />
        )
      })}

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
        return (
          <RobotGroup key={robot.id} position={position} onClick={() => onSelectRobot?.(robot.id)} paused={paused} transitionDuration={transitionDuration}>
            {robot.type === 'ctu' ? (
              <CtuCart load={robot.load} carrying={robot.carrying} action={robot.handlingAction} paused={paused} />
            ) : <ShuttleCart />}
            {robot.id === (selectedRobotId || focusRobot?.id) ? (
              <RobotFocusMarker
                robotId={robot.id}
                phase={`${robot.phaseLabel || focusTask?.phaseLabel || '执行任务'} · ${robot.load}/${robot.capacity} 箱`}
              />
            ) : null}
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
