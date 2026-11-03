import { memo, useCallback, useEffect, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Html, Line, OrbitControls } from '@react-three/drei'
import { Group, MOUSE, Mesh, Vector3 } from 'three'
import { BatchedTopologyMeshes, GoodsBoxes, SlabView, TowerView, ZoomToCursor } from './SceneMeshes'
import { TransportLayoutPreview } from './TransportLayoutPreview'
import { PickingArmHandoff, type PickingArmCargo } from './PickingArmHandoff'
import { ShuttleCartModel } from './ShuttleCartModel'
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
  buildGroundBeltLookup,
  shuttleGroundSnapPosition,
  shuttleTrackWorldPosition,
  type GroundBeltSnap,
  worldToCell,
  worldX,
  worldZ,
  type SceneCylinder,
} from '../../lib/topology3d'
import {
  conveyorAnchorOf,
  conveyorInputSides,
  conveyorDecksOf,
  conveyorDirectionFromSide,
  parseCellId,
  traceConveyorDeckRoute,
  type ConveyorRouteStatus,
} from '../../lib/topology'
import { advanceInterpolationElapsed, interpolateScalar, interpolationProgress } from '../../lib/simulationMotion'
import type {
  GoodsSlot,
  ShuttleContract,
  SimulationRobot,
  SimulationState,
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
  layoutPlan?: TransportLayoutPlan
  layoutPreviewVisible?: boolean
  layoutOptionId?: string
  selectedLayoutItemId?: string
  onSelectLayoutItem?: (itemId: string) => void
  pickingArmPreviewVisible?: boolean
  pickingArmPreviewCycleToken?: string
  pickingArmResetRequest?: number
  pickingArmCargo?: PickingArmCargo
  /** 仿真运行时的机械臂状态（state.pickingArm）：有任务时机械臂按真实货箱做取放动作 */
  armRuntime?: SimulationState['pickingArm']
  /** 机械臂已放到输送线带面的货箱（state.armBeltBoxes）：渲染在放货点带面高度 */
  armBeltBoxes?: SimulationState['armBeltBoxes']
  /** 仿真是否进行中（running/paused）：运行时机械臂常驻显示，空闲时保持待机姿态 */
  simulationActive?: boolean
  speedMultiplier?: 1 | 2 | 4
}

const CTU_GOODS_COLOR = '#fde047'
const CTU_GOODS_EMISSIVE = '#ca8a04'
const CONVEYOR_CARGO_SIZE: [number, number, number] = [0.5, 0.3, 0.42]
const EMPTY_HIGHLIGHTS: ReadonlySet<string> = new Set()
const ignoreInteraction = () => undefined

/** 仿真运行中但机械臂空闲：全部权威信号为假，机械臂保持待机姿态（不播预览动画）。 */
const ARM_IDLE_SIGNALS = {
  cargoToken: null,
  pickedGoodsReady: false,
  outputPositionAvailable: false,
  legalPickupNode: true,
  legalDropoffNode: true,
  authoritativeHandoffCompleted: false,
}

/** 平滑跟随：机器人组的实际位置朝目标格指数逼近，接近后贴死，避免逐 tick 跳格。
 * 拐角走直角：目标垂直转向时先沿当前行进轴对齐、再转垂直轴（L 形路径按距离插值），
 * 避免直线插值斜切拐角（看起来像拐弯时走直线/漂移）。 */
function RobotGroup({ position, children, onClick, paused = false, transitionDuration = 0.3 }: { position: [number, number, number]; children: ReactNode; onClick?: () => void; paused?: boolean; transitionDuration?: number }) {
  const groupRef = useRef<Group>(null)
  const current = useRef(new Vector3(position[0], position[1], position[2]))
  const start = useRef(new Vector3(position[0], position[1], position[2]))
  const target = useRef(new Vector3(position[0], position[1], position[2]))
  const elapsed = useRef(transitionDuration)
  const durationRef = useRef(transitionDuration)
  // 当前行进轴（'x'/'z'）；'y' 为电梯升降，不参与拐角
  const heading = useRef<'x' | 'z'>('x')
  // L 形拐角：先走的轴与对齐值（null = 目标与本段起点同轴，直线插值即可）
  const corner = useRef<{ axis: 'x' | 'z'; value: number } | null>(null)
  const invalidate = useThree((state) => state.invalidate)
  const [x, y, z] = position

  useEffect(() => {
    const targetChanged = target.current.x !== x || target.current.y !== y || target.current.z !== z
    if (targetChanged) {
      start.current.copy(current.current)
      elapsed.current = 0
      const dx = x - start.current.x
      const dz = z - start.current.z
      if (Math.abs(dx) > 1e-6 && Math.abs(dz) > 1e-6) {
        // 斜向目标 = 拐角：先沿当前行进轴对齐目标坐标，再转另一轴
        const first = heading.current
        corner.current = { axis: first, value: first === 'x' ? x : z }
        heading.current = first === 'x' ? 'z' : 'x'
      } else {
        corner.current = null
        if (Math.abs(dx) > 1e-6) heading.current = 'x'
        else if (Math.abs(dz) > 1e-6) heading.current = 'z'
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
      corner.current = null
      groupRef.current?.position.copy(current.current)
    }
    invalidate()
  }, [invalidate, paused, transitionDuration, x, y, z])

  useFrame((_, delta) => {
    if (paused) return
    elapsed.current = advanceInterpolationElapsed(elapsed.current, delta, transitionDuration, false)
    const progress = interpolationProgress(elapsed.current, transitionDuration)
    const cur = current.current
    const c = corner.current
    if (c) {
      // L 形路径按距离插值：leg1 沿拐角轴走到对齐点，leg2 转垂直轴到目标
      const sx = start.current.x
      const sz = start.current.z
      const leg1 = Math.abs(c.value - (c.axis === 'x' ? sx : sz))
      const leg2 = c.axis === 'x' ? Math.abs(z - sz) : Math.abs(x - sx)
      const total = leg1 + leg2 || 1
      const d = progress * total
      const yLerp = start.current.y + (y - start.current.y) * progress
      if (d <= leg1) {
        const sign = c.axis === 'x' ? Math.sign(x - sx) : Math.sign(z - sz)
        if (c.axis === 'x') cur.set(sx + sign * d, yLerp, sz)
        else cur.set(sx, yLerp, sz + sign * d)
      } else {
        const sign = c.axis === 'x' ? Math.sign(z - sz) : Math.sign(x - sx)
        const rem = d - leg1
        if (c.axis === 'x') cur.set(x, yLerp, sz + sign * rem)
        else cur.set(sx + sign * rem, yLerp, z)
      }
    } else {
      cur.lerpVectors(start.current, target.current, progress)
    }
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
  const delivering = action?.type === 'deliver'
  const carrySlotY = carryStartY + Math.max(0, Math.min(4, action?.slotIndex ?? 0)) * carryGap
  const targetLiftY = delivering
    ? action?.stage === 'lifting' ? carrySlotY : homeLiftY
    : action?.stage === 'stowing'
      ? carrySlotY
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

  const visibleCarrying = delivering && action
    ? (carrying ?? []).slice(0, -1)
    : action?.stage === 'stowing'
    ? (carrying ?? []).slice(0, -1)
    : (carrying ?? [])
  const showTransferBox = delivering
    ? Boolean(action)
    : action?.stage === 'extending' || action?.stage === 'stowing'
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
          <meshStandardMaterial color={CTU_GOODS_COLOR} emissive={CTU_GOODS_EMISSIVE} emissiveIntensity={0.3} roughness={0.52} />
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

const DELIVERY_RISE_SECONDS = 0.8
// 与机器人 1 格/0.6s 的节奏匹配，保证出站动画在后端进入回程阶段前走完，避免货箱瞬移。
const DELIVERY_SPEED = 1.8
const DELIVERY_DWELL_SECONDS = 1.2
const DELIVERY_FADE_SECONDS = 0.3
const LIFT_ENTRY_DWELL_SECONDS = 0.35
const LIFT_EXIT_DWELL_SECONDS = 0.4
const LIFT_SPEED = 0.9

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
  smoothFollow = false,
  travelSpeed = DELIVERY_SPEED,
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
  /** 后端逐块位置更新时平滑跟随，避免跨模块中心瞬移。 */
  smoothFollow?: boolean
  /** 沿完整路径播放的世界单位/秒；机械臂输送按仿真 tick 对齐。 */
  travelSpeed?: number
}) {
  const ref = useRef<Group>(null)
  const platformRef = useRef<Group>(null)
  const startRef = useRef<number | null>(null)
  const route = useMemo(() => {
    const segments: {
      from: [number, number, number]
      to: [number, number, number]
      length: number
      startTime: number
      duration: number
      vertical: boolean
      liftMoveSeconds: number
    }[] = []
    let totalDuration = 0
    for (let index = 1; index < path.length; index += 1) {
      const from = path[index - 1]
      const to = path[index]
      const length = Math.hypot(to[0] - from[0], to[1] - from[1], to[2] - from[2])
      if (length <= 0.001) continue
      const vertical = Math.abs(to[0] - from[0]) < 0.001
        && Math.abs(to[2] - from[2]) < 0.001
        && Math.abs(to[1] - from[1]) > 0.05
      const liftMoveSeconds = vertical ? length / LIFT_SPEED : 0
      const duration = vertical
        ? LIFT_ENTRY_DWELL_SECONDS + liftMoveSeconds + LIFT_EXIT_DWELL_SECONDS
        : length / Math.max(0.01, travelSpeed)
      segments.push({ from, to, length, startTime: totalDuration, duration, vertical, liftMoveSeconds })
      totalDuration += duration
    }
    return { segments, totalDuration }
  }, [path, travelSpeed])
  const travelEnd = (skipRise ? 0 : DELIVERY_RISE_SECONDS) + route.totalDuration
  const fadeStart = travelEnd + DELIVERY_DWELL_SECONDS

  useFrame(({ clock, invalidate }, delta) => {
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
    if (platformRef.current) platformRef.current.visible = false
    if (elapsed > riseSeconds) {
      const routeElapsed = Math.min(route.totalDuration, elapsed - riseSeconds)
      let found = false
      for (const segment of route.segments) {
        if (routeElapsed <= segment.startTime + segment.duration + 0.0001) {
          const localElapsed = Math.max(0, routeElapsed - segment.startTime)
          let t = segment.duration ? Math.min(1, localElapsed / segment.duration) : 0
          if (segment.vertical) {
            const liftProgress = Math.min(1, Math.max(
              0,
              (localElapsed - LIFT_ENTRY_DWELL_SECONDS) / Math.max(0.001, segment.liftMoveSeconds),
            ))
            // 升降平台起步、到站均缓速，货箱不会在层间突然跳动。
            t = liftProgress * liftProgress * (3 - 2 * liftProgress)
            if (platformRef.current) platformRef.current.visible = true
          }
          x = segment.from[0] + (segment.to[0] - segment.from[0]) * t
          y = segment.from[1] + (segment.to[1] - segment.from[1]) * t
          z = segment.from[2] + (segment.to[2] - segment.from[2]) * t
          found = true
          break
        }
      }
      if (!found && path.length) {
        ;[x, y, z] = path[path.length - 1]
      }
      if (!hold) {
        const fade = Math.min(1, Math.max(0, (elapsed - fadeStart) / DELIVERY_FADE_SECONDS))
        group.scale.setScalar(Math.max(0.001, 1 - fade))
        group.visible = fade < 1
      }
    }
    if (smoothFollow) {
      const alpha = 1 - Math.exp(-Math.min(delta, 0.1) * 7)
      x = group.position.x + (x - group.position.x) * alpha
      y = group.position.y + (y - group.position.y) * alpha
      z = group.position.z + (z - group.position.z) * alpha
      if (Math.hypot(x - position[0], y - position[1], z - position[2]) > 0.002) invalidate()
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
        <boxGeometry args={CONVEYOR_CARGO_SIZE} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={0.18}
          roughness={0.55}
          metalness={0.12}
        />
      </mesh>
      {/* 升降移载机的随动载货台：进站停稳、抬升、出站停稳期间与货箱一起运动。 */}
      <group ref={platformRef} position={[0, -0.2, 0]} visible={false}>
        <mesh>
          <boxGeometry args={[0.92, 0.08, 0.92]} />
          <meshStandardMaterial color="#2563eb" emissive="#1e40af" emissiveIntensity={0.24} roughness={0.42} metalness={0.42} />
        </mesh>
        {[-0.3, -0.15, 0, 0.15, 0.3].map((offset) => (
          <mesh key={offset} position={[0, 0.065, offset]} rotation={[0, 0, Math.PI / 2]}>
            <cylinderGeometry args={[0.035, 0.035, 0.74, 10]} />
            <meshStandardMaterial color="#cbd5e1" roughness={0.28} metalness={0.75} />
          </mesh>
        ))}
      </group>
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
    ground: boolean
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
  layoutPlan,
  layoutPreviewVisible = false,
  layoutOptionId = '',
  selectedLayoutItemId = '',
  onSelectLayoutItem,
  pickingArmPreviewVisible = false,
  pickingArmPreviewCycleToken = 'arm-preview-0',
  pickingArmResetRequest = 0,
  pickingArmCargo = {
    toteId: 'preview-tote-sku-a',
    orderId: 'ORDER-PREVIEW',
    skuId: 'SKU-A',
    pickedQuantity: 10,
  },
  armRuntime = null,
  armBeltBoxes = [],
  simulationActive = false,
  speedMultiplier = 1,
}: Simulation3DSceneProps) {
  // 拓扑层与高频动态状态彻底分离：机器人 tick 不再重建数万件货架构件。
  const spec = useMemo(() => buildSceneSpec(topology), [topology])

  // 四向车 2×2 车体吸附表：地面输送块 / 出货位闸口 / 交接处 / 巷道槽位都是 2×2 结构。
  // 闸口与巷道槽位转成伪输送块参与走向判定（锚点优先取真实格的 conveyorAnchor），
  // 使车体在所有 2×2 结构上保持完整占位。
  const groundBeltLookup = useMemo(() => {
    const corners = [[0, 0], [0, 1], [1, 0], [1, 1]] as const
    type PseudoCell = {
      row: number
      col: number
      type?: string
      conveyorGround?: boolean | null
      conveyorAnchor?: { row: number; col: number } | null
    }
    // 真实地面格的锚点映射：闸口/交接处节点若落在输送格上，沿用其真实块锚点。
    const anchorByPos = new Map<string, { row: number; col: number }>()
    for (const cell of topology.cells) {
      if (cell.type !== 'conveyor' || !cell.conveyorGround || !cell.conveyorAnchor) continue
      anchorByPos.set(`${Number(cell.row)}:${Number(cell.col)}`, {
        row: Number(cell.conveyorAnchor.row),
        col: Number(cell.conveyorAnchor.col),
      })
    }
    const pseudoCells: PseudoCell[] = []
    for (const node of topology.nodes) {
      if (node.type !== 'shuttle_dock' && node.type !== 'picking_handoff') continue
      const anchor = anchorByPos.get(`${Number(node.row)}:${Number(node.col)}`)
        ?? { row: Number(node.row), col: Number(node.col) }
      for (const [dRow, dCol] of corners) {
        pseudoCells.push({
          row: anchor.row + dRow,
          col: anchor.col + dCol,
          type: 'conveyor',
          conveyorGround: true,
          conveyorAnchor: anchor,
        })
      }
    }
    for (const zone of topology.zones) {
      if (zone.type !== 'shuttle' || !zone.slotAisles) continue
      for (const key of Object.keys(zone.slotAisles)) {
        const [anchorRow, anchorCol] = key.split(',').map(Number)
        if (Number.isNaN(anchorRow) || Number.isNaN(anchorCol)) continue
        for (const [dRow, dCol] of corners) {
          pseudoCells.push({
            row: anchorRow + dRow,
            col: anchorCol + dCol,
            type: 'conveyor',
            conveyorGround: true,
            conveyorAnchor: { row: anchorRow, col: anchorCol },
          })
        }
      }
    }
    // 真实格最后写入：真实 conveyorAnchor 永远优先于节点/槽位推断。
    for (const cell of topology.cells) {
      if (cell.type !== 'conveyor' || !cell.conveyorGround) continue
      pseudoCells.push({
        row: Number(cell.row),
        col: Number(cell.col),
        type: 'conveyor',
        conveyorGround: true,
        conveyorAnchor: cell.conveyorAnchor ?? { row: Number(cell.row), col: Number(cell.col) },
      })
    }
    return buildGroundBeltLookup(pseudoCells)
  }, [topology])

  const shuttleVehiclePosition = useCallback((robot: SimulationRobot): [number, number, number] => {
    const row = Number(robot.currentRow)
    const col = Number(robot.currentCol)
    const snap: GroundBeltSnap | undefined = groundBeltLookup.get(`${row},${col}`)
    if (snap) {
      return shuttleGroundSnapPosition(row, col, snap, spec.cols, spec.rows, Math.max(1, Number(robot.currentLayer ?? 1)))
    }
    return shuttleTrackWorldPosition(row, col, Math.max(1, Number(robot.currentLayer ?? 1)), spec.cols, spec.rows)
  }, [groundBeltLookup, spec.cols, spec.rows])

  // 机械臂运行时：仿真状态驱动机械臂常驻显示与取放动作，不再依赖「机械臂预览」开关。
  const armLive = Boolean(armRuntime?.taskId)
  const armNode = useMemo(() => topology.nodes.find((node) => node.type === 'picking_arm'), [topology])
  const armHandoffNode = useMemo(() => topology.nodes.find((node) => node.type === 'picking_handoff'), [topology])
  // 机械臂底座节点（picking_arm）覆盖布局计划的安装位：编辑器里把底座放哪里，机械臂就装在哪里；
  // 拣货位节点（picking_handoff）覆盖取货点：四向车实际停在该节点所在格（后端 handoffCellId 同源）。
  const armLayout = useMemo(() => {
    const base = layoutPlan?.pickingArm
    if (!base) return undefined
    const installation = armNode
      ? { ...base.installation, floor: '1F', row: Number(armNode.row), col: Number(armNode.col) }
      : base.installation
    const pickup = armHandoffNode
      ? {
          ...base.pickup,
          position: { ...base.pickup.position, floor: '1F', row: Number(armHandoffNode.row), col: Number(armHandoffNode.col) },
        }
      : base.pickup
    return { ...base, installation, pickup }
  }, [armHandoffNode, armNode, layoutPlan])
  // 取货点世界坐标：与四向车停靠位置同一套 2×2 吸附算法，保证机械臂抓的是车上的货。
  const armPickupWorld = useMemo(() => {
    if (!armHandoffNode) return undefined
    const row = Number(armHandoffNode.row)
    const col = Number(armHandoffNode.col)
    const snap = groundBeltLookup.get(`${row},${col}`)
    const [x, , z] = snap
      ? shuttleGroundSnapPosition(row, col, snap, spec.cols, spec.rows, 1)
      : shuttleTrackWorldPosition(row, col, 1, spec.cols, spec.rows)
    return [x, z] as [number, number]
  }, [armHandoffNode, groundBeltLookup, spec.cols, spec.rows])
  const armLiveCargo = useMemo<PickingArmCargo | undefined>(() => {
    if (!armRuntime?.taskId) return undefined
    const task = tasks.find((item) => item.id === armRuntime.taskId)
    if (!task) return undefined
    const line = task.lines[Math.max(0, task.picked - 1)] ?? task.lines[0]
    return {
      toteId: task.id,
      orderId: task.orderId || task.id,
      skuId: line?.skuId || 'SKU',
      pickedQuantity: task.picked || 1,
    }
  }, [armRuntime?.taskId, tasks])
  const armDisplayedCargo = armLive
    ? armLiveCargo ?? {
        toteId: armRuntime?.taskId ?? 'live-arm-cargo',
        orderId: armRuntime?.taskId ?? '机械臂输送任务',
        skuId: '在途货物',
        pickedQuantity: 1,
      }
    : pickingArmCargo
  const armAuthoritativeSignals = useMemo(() => ({
    cargoToken: armRuntime?.taskId ?? null,
    pickedGoodsReady: true,
    outputPositionAvailable: true,
    legalPickupNode: (layoutPlan?.pickingArm?.pickup.validationStatus ?? 'PASS') !== 'BLOCKED',
    legalDropoffNode: (layoutPlan?.pickingArm?.dropoff.validationStatus ?? 'PASS') !== 'BLOCKED',
    authoritativeHandoffCompleted: false,
  }), [armRuntime?.taskId, layoutPlan])
  // The arm is part of the installed warehouse equipment, so keep it visible
  // even before a simulation starts. Preview mode is only for the explicit
  // preview control; otherwise the installed arm remains in its idle pose.
  const armPreviewing = pickingArmPreviewVisible && !simulationActive && !armLive
  const armPlaybackKey = armLive
    ? `live:${armRuntime?.taskId ?? 'unknown'}`
    : armPreviewing
      ? `preview:${pickingArmPreviewCycleToken}`
      : 'idle'
  // 机械臂已放到输送线的货箱：最近 ≤4 箱画在放货点带面高度（数据来自后端 armBeltBoxes）。
  const armBeltVisuals = useMemo(() => {
    // 机械臂预览必须是隔离演示：只显示 ORDER-PREVIEW 这一只从机械臂
    // 放到输送线并运行到终点的货箱，不能叠加历史真实任务留下的黄色
    // 终点快照，否则到站时会看起来像突然多出一箱货。
    if (pickingArmPreviewVisible) return []
    const uniqueBoxes = [...new Map((armBeltBoxes ?? []).map((box, index) => {
      const taskKey = typeof box.taskId === 'string' ? box.taskId : `legacy-${box.placedTick ?? index}`
      return [taskKey, box] as const
    })).entries()].slice(-4)
    const endpointSlots: Array<[number, number]> = [
      [-0.24, -0.2],
      [0.24, -0.2],
      [-0.24, 0.2],
      [0.24, 0.2],
    ]
    return uniqueBoxes.map(([taskKey, box], index) => {
      const parsed = parseCellId(String(box.cellId))
      if (!parsed) return null
      const level = Math.max(1, Number(box.level ?? 2))
      const beltBase = Number(
        box.beltHeight ?? (level > 1 ? CONVEYOR_DECK_BASE + (level - 1) * CONVEYOR_DECK_STEP : CONVEYOR_GROUND_BELT_Y),
      )
      const [x, , z] = shuttleTrackWorldPosition(parsed.row, parsed.col, level, spec.cols, spec.rows)
      const [offsetX, offsetZ] = endpointSlots[index] ?? [0, 0]
      return {
        key: `arm-belt-${taskKey}`,
        position: [x + offsetX, beltBase + 0.16, z + offsetZ] as [number, number, number],
        // 与运输中的 DeliveredBox 使用相同尺寸，完成瞬间只切换数据所有权，
        // 不会像在终点又突然生成一只更大的黄色方块。
        size: CONVEYOR_CARGO_SIZE,
        color: CTU_GOODS_COLOR,
        emissive: CTU_GOODS_EMISSIVE,
      }
    }).filter((box): box is NonNullable<typeof box> => box !== null)
  }, [armBeltBoxes, pickingArmPreviewVisible, spec.cols, spec.rows])
  // 正在输送货箱的 2×2 输送块（键 → 最近活跃时间戳），驱动局部滚筒旋转。
  const activeBlocksRef = useRef(new Map<string, number>())

  // 实时机器人替换静态设备节点（robot.id == 拓扑 device node id），避免重复绘制。
  const liveRobotIdSignature = robots.map((robot) => robot.id).sort().join('|')
  const liveRobotIds = useMemo(() => new Set(liveRobotIdSignature ? liveRobotIdSignature.split('|') : []), [liveRobotIdSignature])
  const shuttleRobotNodeIds = useMemo(
    () => new Set(topology.nodes.filter((node) => node.type === 'shuttle_robot').map((node) => node.id)),
    [topology.nodes],
  )
  const idleShuttleNodes = useMemo(
    () => topology.nodes.filter((node) => (
      node.type === 'shuttle_robot'
      && (node.floor ?? '1F') === activeFloor
      && !liveRobotIds.has(node.id)
    )),
    [activeFloor, liveRobotIds, topology.nodes],
  )

  const layers = useMemo(() => {
    const boxes = [] as Extract<(typeof spec.items)[number], { kind: 'box' }>[]
    const racks = [] as Extract<(typeof spec.items)[number], { kind: 'rack' }>[]
    const cylinders: SceneCylinder[] = []
    const slabs = [] as Extract<(typeof spec.items)[number], { kind: 'slab' }>[]
    const towers = [] as Extract<(typeof spec.items)[number], { kind: 'tower' }>[]
    for (const item of spec.items) {
      if (
        item.entity?.kind === 'node'
        && (liveRobotIds.has(item.entity.id) || shuttleRobotNodeIds.has(item.entity.id))
      ) continue
      if (item.kind === 'box') boxes.push(item)
      else if (item.kind === 'rack') racks.push(item)
      else if (item.kind === 'cyl') cylinders.push(item)
      else if (item.kind === 'slab') slabs.push(item)
      else towers.push(item)
    }
    return { boxes, cylinders, racks, slabs, towers }
  }, [liveRobotIds, shuttleRobotNodeIds, spec])

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
      status: ConveyorRouteStatus
      message?: string
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
      smoothFollow?: boolean
      travelSpeed?: number
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

    // 按 2×2 模块生成「入口 → 中心 → 出口」路径。断路和回环会在块级追踪时
    // 被截断，货箱停在最后一个有效模块，不再执行 400 次单格循环后消失。
    const conveyorPath = (
      start: (typeof cells)[number],
      lowerY: number,
      groundBeltY: number,
      deck: 'lower' | 'upper' = 'lower',
    ) => {
      const points: [number, number, number][] = []
      const beltCells: (typeof cells)[number][] = []
      const trace = traceConveyorDeckRoute(topology, start, deck)
      const pushPoint = (point: [number, number, number]) => {
        const previous = points[points.length - 1]
        if (!previous || Math.hypot(point[0] - previous[0], point[1] - previous[1], point[2] - previous[2]) > 0.001) {
          points.push(point)
        }
      }
      for (let blockIndex = 0; blockIndex < trace.blocks.length; blockIndex += 1) {
        const block = trace.blocks[blockIndex]
        const anchorCell = block.anchorCell
        const decks = conveyorDecksOf(topology, anchorCell)
        const config = decks[deck] ?? decks.lower
        const y = anchorCell.conveyorGround ? groundBeltY : lowerY + (deck === 'upper' ? CONVEYOR_DECK_STEP : 0)
        const cx = worldX(block.col, cols) + CELL / 2
        const cz = worldZ(block.row, rows) - CELL / 2
        const previousBlock = trace.blocks[blockIndex - 1]
        const actualInputSide = previousBlock
          ? previousBlock.row < block.row
            ? 'north'
            : previousBlock.row > block.row
              ? 'south'
              : previousBlock.col < block.col
                ? 'east'
                : 'west'
          : conveyorInputSides(config)[0]
        const input = DIRECTION_VEC3[conveyorDirectionFromSide(actualInputSide)]
        const output = DIRECTION_VEC3[conveyorDirectionFromSide(config.outputSide)]
        pushPoint([cx + input.dx * CELL, y, cz + input.dz * CELL])
        pushPoint([cx, y, cz])
        pushPoint([cx + output.dx * CELL, y, cz + output.dz * CELL])
        beltCells.push(anchorCell)
        const blockKey = block.key
        // 2×2 块的四个格都映射到同一个块，货箱在块内任何位置都会触发滚筒旋转。
        for (let dr = 0; dr < 2; dr += 1) {
          for (let dc = 0; dc < 2; dc += 1) {
            blockLookup.set(`${block.row + dr}:${block.col + dc}`, blockKey)
          }
        }
        if (!blockMap.has(blockKey)) {
          blockMap.set(blockKey, {
            key: blockKey,
            row: block.row,
            col: block.col,
            floor: block.floor,
            direction: [output.dx, output.dz],
            levels: Math.max(1, anchorCell.levels ?? 1),
            ground: Boolean(anchorCell.conveyorGround),
          })
        }
      }
      const lastBlock = trace.blocks[trace.blocks.length - 1]
      if (trace.status === 'endpoint' && deck === 'lower' && lastBlock) {
        const decks = conveyorDecksOf(topology, lastBlock.anchorCell)
        const output = DIRECTION_VEC3[conveyorDirectionFromSide(decks.lower.outputSide)]
        const nextRow = lastBlock.row - output.dz * 2
        const nextCol = lastBlock.col + output.dx * 2
        const lift = liftByPosition.get(`${lastBlock.floor}:${nextRow}:${nextCol}`)
        const levels = Math.max(1, lastBlock.anchorCell.levels ?? 1)
        if (lift && levels > 1 && points.length) {
          let y = points[points.length - 1][1]
          const liftX = worldX(lift.col, cols) + CELL / 2
          const liftZ = worldZ(lift.row, rows) - CELL / 2
          pushPoint([liftX, y, liftZ])
          y += CONVEYOR_DECK_STEP
          pushPoint([liftX, y, liftZ])
        }
      }
      return { points, beltCells, status: trace.status, message: trace.message }
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
      const activeConveyorTransport = task.conveyorTransport?.phase === task.phase
      if (!activeConveyorTransport) {
        // 机械臂输送任务完成后由 armBeltBoxes 在真实终点保留货箱快照，
        // 不再重复播放四向车的旧交付动画。
        if (task.armConveyorCompleted) continue
        if (hasPickStation && robot?.type !== 'shuttle') {
          if (!pickFlowPhases.has(task.phase)) continue
        } else {
          // 没有拣货位、或四向车任务（整箱走地面输送线，交付后即完成）：
          // 任务完成即播放；机器人已停在出入口的交付阶段也提前生成，
          // 避免第一辆车交付瞬间因阶段推进而错过动画。
          const atPort = robot?.currentCellId === task.deliveryCellId
          if (task.phase !== 'completed' && !(task.phase === 'delivery_to_port' && atPort)) continue
        }
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
      // 机械臂放箱后，任务会立即与四向车解绑，task.deliveryCellId 仍然是
      // 原四向车交付点（例如 92,48），并不是货箱实际所在的输送模块。
      // 必须优先使用后端 conveyorTransport 的当前块来选择输送线，否则
      // nearestConveyor 会因为旧交付点距离过远而返回 null，整只运输货箱
      // 会在这里被 continue 掉，造成“机械臂有放箱动作但看不到货物”。
      const activeTransport = task.conveyorTransport?.phase === task.phase
        ? task.conveyorTransport
        : null
      const transportBlockParts = activeTransport?.currentBlockId?.split(':') ?? null
      const transportFloor = transportBlockParts && transportBlockParts.length >= 4
        ? transportBlockParts[transportBlockParts.length - 4]
        : null
      const transportRow = transportBlockParts && transportBlockParts.length >= 4
        ? Number(transportBlockParts[transportBlockParts.length - 3])
        : Number.NaN
      const transportCol = transportBlockParts && transportBlockParts.length >= 4
        ? Number(transportBlockParts[transportBlockParts.length - 2])
        : Number.NaN
      const transportDeck = transportBlockParts && transportBlockParts.length >= 4
        ? transportBlockParts[transportBlockParts.length - 1]
        : null
      const transportConveyor = transportFloor && Number.isFinite(transportRow) && Number.isFinite(transportCol)
        ? cellByPosition.get(`${transportFloor}:${transportRow}:${transportCol}`) ?? null
        : null
      const bindingParts = portZone?.conveyorBinding?.blockId.split(':')
      const boundConveyor = bindingParts && bindingParts.length >= 3
        ? cellByPosition.get(
            `${bindingParts[bindingParts.length - 3]}:${Number(bindingParts[bindingParts.length - 2])}:${Number(bindingParts[bindingParts.length - 1])}`,
          )
        : null
      const conveyor = transportConveyor
        ?? boundConveyor
        ?? nearestConveyor(entryRow, entryCol, entryFloor, robot?.type === 'shuttle')
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
      let outboundRouteStatus: ConveyorRouteStatus = routeResult.status
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
      const upperStart = beltCells[beltCells.length - 1]
      const upperRouteResult = routeDecks.upper?.enabled && upperStart
        ? conveyorPath(upperStart, lowerY, groundBeltY, 'upper')
        : null
      const upperRoute = upperRouteResult?.points ?? []
      if (explicitUpperDeck && stationRaw && upperRoute.length) {
        // 新模型：下层只负责去程到升降端；上层有自己的有向路线，专门把空箱送回 CTU 取箱口。
        const upperStartPoint = upperRoute[0] ?? liftRoute[liftRoute.length - 1] ?? portPoint
        const stationPoint = stationRaw
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
      } else if (lifted && upperRoute.length) {
        // 没有人工拣货台的双层 CTU 线：下层把实箱送入升降机，平台抬升后
        // 货箱必须继续沿反向的上层输送带运行，不能在升降机顶部直接消失。
        outboundLeg = [...liftRoute, ...upperRoute]
        outboundRouteStatus = upperRouteResult?.status ?? outboundRouteStatus
      }
      const stationRoute = outboundLeg
      const returnPath = returnLeg
      const transportPoint: [number, number, number] | null = activeTransport
        && Number.isFinite(transportRow)
        && Number.isFinite(transportCol)
        ? [
            worldX(transportCol, cols) + CELL / 2,
            lowerY + (transportDeck === 'upper' ? CONVEYOR_DECK_STEP : 0),
            worldZ(transportRow, rows) - CELL / 2,
          ]
        : null
      // 机械臂货箱与 CTU 货箱共用完整路径播放器。后端 route 只保存
      // 2×2 模块锚点，因此在相邻模块中心之间补入共同边界点，形成
      // “模块中心 → 模块出口/下一模块入口 → 下一模块中心”的连续路径。
      const armTransportCenters = task.phase === 'arm_conveyor_delivery'
        ? (activeTransport?.route ?? []).flatMap((blockId) => {
            const parts = blockId.split(':')
            if (parts.length < 4) return []
            const floor = parts[parts.length - 4]
            const row = Number(parts[parts.length - 3])
            const col = Number(parts[parts.length - 2])
            const deck = parts[parts.length - 1]
            if (!Number.isFinite(row) || !Number.isFinite(col)) return []
            const routeFloorIndex = Math.max(0, spec.floors.indexOf(floor))
            const y = routeFloorIndex * FLOOR_HEIGHT
              + CONVEYOR_DECK_BASE
              + 0.16
              + (deck === 'upper' ? CONVEYOR_DECK_STEP : 0)
            return [[
              worldX(col, cols) + CELL / 2,
              y,
              worldZ(row, rows) - CELL / 2,
            ] as [number, number, number]]
          })
        : []
      const armTransportPath = armTransportCenters.flatMap((center, index) => {
        if (index === 0) return [center]
        const previous = armTransportCenters[index - 1]
        const boundary: [number, number, number] = [
          (previous[0] + center[0]) / 2,
          (previous[1] + center[1]) / 2,
          (previous[2] + center[2]) / 2,
        ]
        return [boundary, center]
      })
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
        const boxColor = task.phase === 'arm_conveyor_delivery' || Boolean(task.armPlacedCellId)
          ? CTU_GOODS_COLOR
          : task.robotType === 'ctu' || robot?.type === 'ctu'
          ? CTU_GOODS_COLOR
          : GOODS_CATEGORY_COLORS[line.largeCategory ?? ''] ?? GOODS_DEFAULT_COLOR
        if (transportPoint) {
          const synchronizedPoint: [number, number, number] = [
            transportPoint[0] + (lineIndex % 2) * 0.12 - 0.06,
            transportPoint[1] + Math.floor(lineIndex / 2) * 0.14,
            transportPoint[2] + (lineIndex % 2) * 0.12 - 0.06,
          ]
          if (task.phase === 'arm_conveyor_delivery' && armTransportPath.length > 1) {
            const offsetX = (lineIndex % 2) * 0.12 - 0.06
            const offsetY = Math.floor(lineIndex / 2) * 0.14
            const offsetZ = (lineIndex % 2) * 0.12 - 0.06
            const continuousPath = armTransportPath.map((point) => [
              point[0] + offsetX,
              point[1] + offsetY,
              point[2] + offsetZ,
            ] as [number, number, number])
            boxes.push({
              key: `${task.id}-${lineIndex}-transport`,
              position: continuousPath[0],
              path: continuousPath,
              color: boxColor,
              hold: true,
              skipRise: true,
              smoothFollow: false,
              // 与 CTU 货箱使用完全相同的固定输送速度，不再随 1×/2×/4×
              // 仿真 tick 被成倍放大。
              travelSpeed: DELIVERY_SPEED,
            })
            return
          }
          boxes.push({
            // 块变化时保持同一个 React 实例，避免每跨一块都重新播放“从下方升起”。
            key: `${task.id}-${lineIndex}-transport`,
            position: synchronizedPoint,
            path: [synchronizedPoint],
            color: boxColor,
            hold: true,
            skipRise: true,
            smoothFollow: true,
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
            color: boxColor,
            hold: true,
            skipRise: true,
          })
        } else if (waitingAtPort) {
          boxes.push({
            key: `${task.id}-${lineIndex}-port`,
            position: returnPath[returnPath.length - 1] ?? startPoint,
            path: [returnPath[returnPath.length - 1] ?? startPoint],
            color: boxColor,
            hold: true,
            skipRise: true,
          })
        } else {
          boxes.push({
            key: `${task.id}-${lineIndex}`,
            position: startPoint,
            // 先沿带面进入，再沿输送线走；到升降移栽机处抬升到第二层。
            path: outboundPath,
            color: boxColor,
            // 配置断路、禁用或形成循环时停在最后一个有效模块，禁止淡出伪装成已送达。
            hold: hold || outboundRouteStatus !== 'endpoint',
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
          smoothFollow={box.smoothFollow}
          travelSpeed={box.travelSpeed}
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

      {layoutPreviewVisible && layoutPlan && layoutOptionId ? (
        <TransportLayoutPreview
          plan={layoutPlan}
          optionId={layoutOptionId}
          cols={spec.cols}
          rows={spec.rows}
          selectedItemId={selectedLayoutItemId}
          onSelectItem={onSelectLayoutItem}
        />
      ) : null}

      {armLayout && activeFloor === '1F' ? (
        <PickingArmHandoff
          key={armPlaybackKey}
          topology={topology}
          layout={armLayout}
          cargo={armDisplayedCargo}
          preview={armPreviewing}
          previewCycleToken={pickingArmPreviewCycleToken}
          paused={paused}
          speedMultiplier={speedMultiplier}
          resetRequest={pickingArmResetRequest}
          authoritativeSignals={armLive ? armAuthoritativeSignals : ARM_IDLE_SIGNALS}
          pickupOverrideXZ={armPickupWorld}
          variant={armLive ? 'live' : armPreviewing ? 'preview' : 'idle'}
        />
      ) : null}

      {armBeltVisuals.map((box) => (
        <mesh key={box.key} position={box.position} castShadow>
          <boxGeometry args={box.size} />
          <meshStandardMaterial color={box.color} emissive={box.emissive} emissiveIntensity={0.14} roughness={0.5} metalness={0.1} />
        </mesh>
      ))}

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

      {/* 仿真尚未启动时没有 robots 状态，仍使用与运行中完全相同的四向车模型。
          一旦同 ID 的实时机器人出现，idleShuttleNodes 会自动移除静态车，由动态车接管。 */}
      {idleShuttleNodes.map((node) => {
        const position = shuttleTrackWorldPosition(
          Number(node.row),
          Number(node.col),
          1,
          spec.cols,
          spec.rows,
        )
        return (
          <group key={`idle-shuttle-${node.id}`} position={position} onClick={() => onSelectRobot?.(node.id)}>
            <ShuttleCartModel paused />
          </group>
        )
      })}

      {robots.map((robot) => {
        const position: [number, number, number] = robot.type === 'shuttle'
          ? shuttleVehiclePosition(robot)
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
            ) : <ShuttleCartModel load={robot.load} carrying={robot.carrying} paused={paused} />}
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
            const [x, y, z] = shuttleVehiclePosition(selected)
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
