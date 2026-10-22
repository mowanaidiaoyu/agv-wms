import { memo, useEffect, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Html, Line, OrbitControls } from '@react-three/drei'
import { Group, MOUSE, Vector3 } from 'three'
import { BatchedTopologyMeshes, GoodsBoxes, SlabView, TowerView, ZoomToCursor } from './SceneMeshes'
import {
  CELL,
  CTU_ROBOT_LEVELS,
  FLOOR_HEIGHT,
  ZONE_LEVEL_HEIGHT,
  buildGoodsSceneBoxes,
  buildSceneSpec,
  robotWorldPosition,
  worldX,
  worldZ,
  type SceneCylinder,
} from '../../lib/topology3d'
import { parseCellId } from '../../lib/topology'
import type { GoodsSlot, SimulationRobot, SimulationTask, TopologyPayload } from '../../types'

export type Simulation3DSceneProps = {
  topology: TopologyPayload
  goods?: GoodsSlot[]
  robots: SimulationRobot[]
  tasks: SimulationTask[]
  activeFloor: string
  focusRequest?: number
}

const SHUTTLE_COLOR = '#c084fc'
const SHUTTLE_EMISSIVE = '#6d28d9'
const EMPTY_HIGHLIGHTS: ReadonlySet<string> = new Set()
const ignoreInteraction = () => undefined

/** 平滑跟随：机器人组的实际位置朝目标格指数逼近，接近后贴死，避免逐 tick 跳格。 */
function RobotGroup({ position, children }: { position: [number, number, number]; children: ReactNode }) {
  const groupRef = useRef<Group>(null)
  const current = useRef(new Vector3(position[0], position[1], position[2]))
  const target = useRef(new Vector3(position[0], position[1], position[2]))
  const invalidate = useThree((state) => state.invalidate)
  const [x, y, z] = position

  useEffect(() => {
    target.current.set(x, y, z)
    invalidate()
  }, [invalidate, x, y, z])

  useFrame((_, delta) => {
    const cur = current.current
    const tgt = target.current
    if (cur.distanceTo(tgt) < 0.02) {
      cur.copy(tgt)
    } else {
      // 后端每 0.6 秒推进一格；提高插值收敛速度，避免上一格动画尚未结束就收到下一格位置。
      cur.lerp(tgt, Math.min(1, delta * 10))
      invalidate()
    }
    if (groupRef.current) groupRef.current.position.copy(cur)
  })

  return <group ref={groupRef}>{children}</group>
}

/**
 * PQ-50 风格 CTU：17 层高行程门架、升降取箱台和 5 个独立载箱位。
 * 货架层数决定升降高度，载箱位数量决定单趟容量，两者不混用。
 */
function CtuCart({
  load,
  carrying,
  action,
}: {
  load: number
  carrying?: GoodsSlot[]
  action?: SimulationRobot['handlingAction']
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
  const actionSignature = `${action?.stage ?? 'idle'}:${action?.targetLayer ?? 1}:${action?.slotIndex ?? 0}`

  useEffect(() => invalidate(), [actionSignature, invalidate])
  useFrame((_, delta) => {
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
    <mesh position={[CELL / 2, 0.42, -CELL / 2]}>
      <boxGeometry args={[w, 0.8, w]} />
      <meshStandardMaterial color={SHUTTLE_COLOR} emissive={SHUTTLE_EMISSIVE} emissiveIntensity={0.3} roughness={0.5} />
    </mesh>
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

function cellWorldPosition(
  cellId: string,
  layer: number,
  floors: string[],
  cols: number,
  rows: number,
): [number, number, number] | null {
  const cell = parseCellId(cellId)
  if (!cell) return null
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
}: Simulation3DSceneProps) {
  // 拓扑层与高频动态状态彻底分离：机器人 tick 不再重建数万件货架构件。
  const spec = useMemo(() => buildSceneSpec(topology), [topology])

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

  const focusTask = tasks.find((task) => task.status === 'assigned')
    ?? tasks.find((task) => task.status !== 'completed')
  const focusRobot = focusTask?.assignedRobotId
    ? robots.find((robot) => robot.id === focusTask.assignedRobotId)
    : undefined
  const isPicking = focusTask?.phase === 'pickup_from_ctu' || focusTask?.phase === 'pickup_from_shuttle'
  const handlingAction = focusRobot?.handlingAction
  const handlingLine = handlingAction
    ? focusTask?.lines.find((line) => line.nodeId === handlingAction.nodeId && line.layer === handlingAction.targetLayer)
    : undefined
  const focusLine = isPicking
    ? handlingLine ?? focusTask?.lines?.[Math.min(focusTask.currentLineIndex, Math.max(0, focusTask.lines.length - 1))]
    : undefined
  const focusTargetCellId = handlingAction?.cellId || focusLine?.cellId || focusTask?.deliveryCellId
  const focusTargetLayer = handlingAction?.targetLayer ?? focusLine?.layer ?? 1
  const focusTargetPosition = useMemo(
    () => focusTargetCellId
      ? cellWorldPosition(focusTargetCellId, focusTargetLayer, spec.floors, spec.cols, spec.rows)
      : null,
    [focusTargetCellId, focusTargetLayer, spec.cols, spec.floors, spec.rows],
  )
  const routePoints = useMemo(() => {
    if (!focusRobot?.path?.length) return [] as [number, number, number][]
    return focusRobot.path.flatMap((cellId, index) => {
      const parsed = parseCellId(cellId)
      if (!parsed) return []
      const floorIndex = Math.max(0, spec.floors.indexOf(parsed.floor))
      const layer = focusRobot.pathLayers?.[index] ?? 1
      return [[
        worldX(parsed.col, spec.cols),
        floorIndex * FLOOR_HEIGHT + Math.max(0.18, (layer - 1) * ZONE_LEVEL_HEIGHT.ctu + 0.18),
        worldZ(parsed.row, spec.rows),
      ] as [number, number, number]]
    })
  }, [focusRobot?.path, focusRobot?.pathLayers, spec.cols, spec.floors, spec.rows])

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
          location={focusLine ? `${focusLine.nodeId} · 第 ${focusLine.layer} 层` : '出库交付点'}
        />
      ) : null}

      {robots.map((robot) => {
        const position = robotWorldPosition(
          robot.currentFloor ?? '1F',
          robot.currentRow,
          robot.currentCol,
          spec.floors,
          spec.cols,
          spec.rows,
        )
        return (
          <RobotGroup key={robot.id} position={position}>
            {robot.type === 'ctu' ? (
              <CtuCart load={robot.load} carrying={robot.carrying} action={robot.handlingAction} />
            ) : <ShuttleCart />}
            {robot.id === focusRobot?.id ? (
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
      <ZoomToCursor />
    </Canvas>
  )
}

export default memo(Simulation3DScene)
