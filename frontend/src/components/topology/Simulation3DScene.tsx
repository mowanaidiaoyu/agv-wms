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

const CTU_COLOR = '#22d3ee'
const CTU_EMISSIVE = '#0e7490'
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

/** CTU 车：底座 + 4 立柱 + 5 层载货搁板，前 `load` 层点亮（0/5→5/5），并渲染实际装载的货物箱。局部坐标，原点=格中心贴地。 */
function CtuCart({ load, carrying }: { load: number; carrying?: GoodsSlot[] }) {
  const levelHeight = ZONE_LEVEL_HEIGHT.ctu
  const w = CELL * 0.7
  const d = CELL * 0.7
  const halfW = w / 2
  const halfD = d / 2
  const baseH = 0.12
  const baseY = 0.08
  const baseTop = baseY + baseH / 2
  const shelfH = 0.08
  const rackHeight = CTU_ROBOT_LEVELS * levelHeight
  const postCenterY = baseTop + rackHeight / 2
  const postOffsets: [number, number][] = [
    [-halfW + 0.08, -halfD + 0.08],
    [halfW - 0.08, -halfD + 0.08],
    [-halfW + 0.08, halfD - 0.08],
    [halfW - 0.08, halfD - 0.08],
  ]
  const boxW = w * 0.55
  const boxD = d * 0.55
  const boxH = levelHeight * 0.5

  return (
    <group>
      <mesh position={[0, baseY, 0]}>
        <boxGeometry args={[w, baseH, d]} />
        <meshStandardMaterial color={CTU_COLOR} emissive={CTU_EMISSIVE} emissiveIntensity={0.3} roughness={0.5} />
      </mesh>
      {postOffsets.map(([px, pz], i) => (
        <mesh key={`post-${i}`} position={[px, postCenterY, pz]}>
          <boxGeometry args={[0.07, rackHeight, 0.07]} />
          <meshStandardMaterial color={CTU_COLOR} emissive={CTU_EMISSIVE} emissiveIntensity={0.3} roughness={0.5} />
        </mesh>
      ))}
      {Array.from({ length: CTU_ROBOT_LEVELS }, (_, i) => {
        const filled = i < load
        return (
          <mesh key={`shelf-${i}`} position={[0, baseTop + i * levelHeight + shelfH / 2, 0]}>
            <boxGeometry args={[w, shelfH, d]} />
            <meshStandardMaterial
              color={filled ? CTU_COLOR : '#1e293b'}
              emissive={filled ? CTU_EMISSIVE : '#0b1220'}
              emissiveIntensity={filled ? 0.35 : 0}
              roughness={0.5}
            />
          </mesh>
        )
      })}
      {(carrying ?? []).slice(0, CTU_ROBOT_LEVELS).map((_, i) => {
        const color = '#34d399'
        return (
          <mesh key={`carry-${i}`} position={[0, baseTop + i * levelHeight + shelfH + boxH / 2, 0]}>
            <boxGeometry args={[boxW, boxH, boxD]} />
            <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.25} roughness={0.6} />
          </mesh>
        )
      })}
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
      <mesh position={[0, 3.3, 0]}>
        <coneGeometry args={[0.26, 0.72, 16]} />
        <meshBasicMaterial color="#22d3ee" transparent opacity={0.9} depthTest={false} />
      </mesh>
      <Html position={[0, 4.05, 0]} center zIndexRange={[29, 19]}>
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
    const slabs = [] as Extract<(typeof spec.items)[number], { kind: 'slab' }>[]
    const towers = [] as Extract<(typeof spec.items)[number], { kind: 'tower' }>[]
    for (const item of spec.items) {
      if (item.entity?.kind === 'node' && liveRobotIds.has(item.entity.id)) continue
      if (item.kind === 'box') boxes.push(item)
      else if (item.kind === 'rack') racks.push(item)
      else if (item.kind === 'slab') slabs.push(item)
      else towers.push(item)
    }
    return { boxes, racks, slabs, towers }
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
  const focusLine = isPicking
    ? focusTask?.lines?.[Math.min(focusTask.currentLineIndex, Math.max(0, focusTask.lines.length - 1))]
    : undefined
  const focusTargetCellId = focusLine?.cellId || focusTask?.deliveryCellId
  const focusTargetLayer = focusLine?.layer ?? 1
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
            {robot.type === 'ctu' ? <CtuCart load={robot.load} carrying={robot.carrying} /> : <ShuttleCart />}
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
