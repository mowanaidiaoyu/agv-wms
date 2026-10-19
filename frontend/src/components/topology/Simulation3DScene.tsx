import { useEffect, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { Group, MOUSE, Vector3 } from 'three'
import { BoxView, GoodsBoxes, RackView, SlabView, TowerView, ZoomToCursor } from './SceneMeshes'
import {
  CELL,
  CTU_ROBOT_LEVELS,
  GOODS_CATEGORY_COLORS,
  GOODS_DEFAULT_COLOR,
  ZONE_LEVEL_HEIGHT,
  buildSceneSpec,
  robotWorldPosition,
  type SceneBox,
} from '../../lib/topology3d'
import type { GoodsSlot, SimulationRobot, SimulationTask, TopologyPayload } from '../../types'

export type Simulation3DSceneProps = {
  topology: TopologyPayload
  goods?: GoodsSlot[]
  robots: SimulationRobot[]
  tasks: SimulationTask[]
  activeFloor: string
}

const CTU_COLOR = '#22d3ee'
const CTU_EMISSIVE = '#0e7490'
const SHUTTLE_COLOR = '#c084fc'
const SHUTTLE_EMISSIVE = '#6d28d9'

/** 平滑跟随：机器人组的实际位置朝目标格指数逼近，接近后贴死，避免逐 tick 跳格。 */
function RobotGroup({ position, children }: { position: [number, number, number]; children: ReactNode }) {
  const groupRef = useRef<Group>(null)
  const current = useRef(new Vector3(position[0], position[1], position[2]))
  const target = useRef(new Vector3(position[0], position[1], position[2]))

  useEffect(() => {
    target.current.set(position[0], position[1], position[2])
  }, [position[0], position[1], position[2]])

  useFrame((_, delta) => {
    const cur = current.current
    const tgt = target.current
    if (cur.distanceTo(tgt) < 0.02) {
      cur.copy(tgt)
    } else {
      cur.lerp(tgt, Math.min(1, delta * 6))
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
      {(carrying ?? []).slice(0, CTU_ROBOT_LEVELS).map((slot, i) => {
        const color = GOODS_CATEGORY_COLORS[slot.largeCategory ?? ''] ?? GOODS_DEFAULT_COLOR
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

export default function Simulation3DScene({
  topology,
  goods,
  robots,
  tasks,
  activeFloor,
}: Simulation3DSceneProps) {
  const spec = useMemo(() => buildSceneSpec(topology, goods), [topology, goods])

  // 实时机器人替换静态设备节点（robot.id == 拓扑 device node id），避免重复绘制。
  const liveRobotIds = useMemo(() => new Set(robots.map((robot) => robot.id)), [robots])

  // 商品箱单独走 InstancedMesh 批量绘制（含取货高亮按 key 染色）。
  const goodsBoxes = useMemo(
    () => spec.items.filter((item): item is SceneBox => item.kind === 'box' && item.key.startsWith('goods-')),
    [spec],
  )

  // 取货高亮：机器人当前处于 CTU 取货阶段时，高亮其当前取货行对应的商品箱。
  const highlightKeys = useMemo(() => {
    const keys = new Set<string>()
    for (const robot of robots) {
      if (!robot.currentTaskId) continue
      const task = tasks.find((item) => item.id === robot.currentTaskId)
      if (!task || task.phase !== 'pickup_from_ctu') continue
      const line = task.lines?.[task.currentLineIndex]
      if (line) keys.add(`goods-${line.nodeId}-${line.layer}`)
    }
    return keys
  }, [robots, tasks])

  return (
    <Canvas
      dpr={[1, 2]}
      gl={{ antialias: true, alpha: true }}
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

      {spec.items.map((item) => {
        // 隐藏被实时机器人替代的静态设备节点
        if (item.entity?.kind === 'node' && liveRobotIds.has(item.entity.id)) return null
        // 商品箱走批量 InstancedMesh
        if (item.kind === 'box' && item.key.startsWith('goods-')) return null
        switch (item.kind) {
          case 'slab':
            return (
              <SlabView
                key={item.key}
                slab={item}
                isActive={item.label === activeFloor}
                cols={spec.cols}
                rows={spec.rows}
              />
            )
          case 'rack':
            return <RackView key={item.key} rack={item} highlighted={false} hovered={false} selectProps={{}} />
          case 'tower':
            return <TowerView key={item.key} tower={item} highlighted={false} hovered={false} selectProps={{}} />
          case 'box':
            return (
              <BoxView
                key={item.key}
                box={item}
                highlighted={highlightKeys.has(item.key)}
                hovered={false}
                selectProps={{}}
              />
            )
        }
      })}

      <GoodsBoxes boxes={goodsBoxes} highlightKeys={highlightKeys} />

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
      <ZoomToCursor />
    </Canvas>
  )
}
