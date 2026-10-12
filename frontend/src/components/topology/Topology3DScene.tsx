import { useEffect, useMemo, useState } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import type { ThreeEvent } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { MOUSE, Plane, Raycaster, Vector2, Vector3 } from 'three'
import type { ColorRepresentation } from 'three'
import {
  CELL,
  buildSceneSpec,
  floorPlaneY,
  shuttleSlotEntityId,
  worldToCell,
  worldX,
  worldZ,
  type SceneBox,
  type SceneEntity,
  type SceneEntityKind,
  type SceneRack,
  type SceneSlab,
  type SceneSpec,
  type SceneTower,
} from '../../lib/topology3d'
import type { CellBox } from '../../lib/topology'
import type { EditorObjectType, TopologyPayload } from '../../types'
import type { TopologySelection } from '../../store/useTopologyEditorStore'

export type Topology3DSceneProps = {
  topology: TopologyPayload
  activeFloor: string
  activeTool: EditorObjectType
  marquee: CellBox | null
  selection: TopologySelection
  onCellDown: (row: number, col: number) => void
  onCellEnter: (row: number, col: number) => void
  onEntityDown: (kind: SceneEntityKind, id: string, floor: string) => void
  onMoveCell: (row: number, col: number) => void
}

const HIGHLIGHT = '#fde047'
const HOVER = '#22d3ee'

type PointerHandlers = {
  onPointerDown?: (e: ThreeEvent<PointerEvent>) => void
  onPointerMove?: (e: ThreeEvent<PointerEvent>) => void
  onPointerOver?: (e: ThreeEvent<PointerEvent>) => void
  onPointerOut?: (e: ThreeEvent<PointerEvent>) => void
}

function isLeftButton(e: ThreeEvent<PointerEvent>): boolean {
  return e.nativeEvent.button === 0
}

function makeSelectProps(
  canSelect: boolean,
  entity: SceneEntity | undefined,
  onEntityDown: (kind: SceneEntityKind, id: string, floor: string) => void,
  setHoverEntity: (id: string | null) => void,
): PointerHandlers {
  if (!canSelect || !entity) return {}
  return {
    onPointerDown: (e) => {
      if (!isLeftButton(e)) return
      e.stopPropagation()
      onEntityDown(entity.kind, entity.id, entity.floor)
    },
    onPointerOver: () => setHoverEntity(entity.id),
    onPointerOut: () => setHoverEntity(null),
  }
}

function RackView({
  rack,
  highlighted,
  hovered,
  selectProps,
}: {
  rack: SceneRack
  highlighted: boolean
  hovered: boolean
  selectProps: PointerHandlers
}) {
  const { posts, shelves } = useMemo(() => {
    const rackHeight = rack.levels * rack.levelHeight
    const step = rack.slotSize || 1
    const left = rack.x - rack.cellsW / 2
    const top = rack.z + rack.cellsD / 2
    const bottom = rack.z - rack.cellsD / 2

    const xs: number[] = []
    for (let i = 0; i <= rack.cellsW; i += step) xs.push(left + i)
    if (xs[xs.length - 1] !== left + rack.cellsW) xs.push(left + rack.cellsW)

    const zs: number[] = []
    for (let j = 0; j <= rack.cellsD; j += step) zs.push(bottom + j)
    if (zs[zs.length - 1] !== top) zs.push(top)

    const posts: { key: string; x: number; z: number }[] = []
    xs.forEach((x, xi) => {
      zs.forEach((z, zi) => {
        posts.push({ key: `p-${rack.key}-${xi}-${zi}`, x, z })
      })
    })

    const shelves: { level: number; y: number }[] = []
    for (let level = 1; level <= rack.levels; level += 1) {
      shelves.push({ level, y: rack.y + level * rack.levelHeight - 0.05 })
    }
    return { posts, shelves, rackHeight }
  }, [rack])

  const rackHeight = rack.levels * rack.levelHeight
  const color = highlighted ? HIGHLIGHT : hovered ? HOVER : rack.color
  const shelfOpacity = highlighted || hovered ? 0.6 : 0.42

  return (
    <group {...selectProps}>
      {posts.map((post) => (
        <mesh key={post.key} position={[post.x, rack.y + rackHeight / 2, post.z]}>
          <boxGeometry args={[0.08, rackHeight, 0.08]} />
          <meshStandardMaterial color={color} roughness={0.7} metalness={0.25} />
        </mesh>
      ))}
      {shelves.map(({ level, y }) => {
        const shelfColor = highlighted || hovered ? color : (rack.levelColors?.[level - 1] ?? rack.color)
        return (
          <mesh key={`s-${rack.key}-${y}`} position={[rack.x, y, rack.z]}>
            <boxGeometry args={[rack.cellsW * 0.96, 0.06, rack.cellsD * 0.96]} />
            <meshStandardMaterial color={shelfColor} transparent opacity={shelfOpacity} roughness={0.85} />
          </mesh>
        )
      })}
    </group>
  )
}

function SlabView({
  slab,
  isActive,
  cols,
  rows,
  onCellDown,
  onCellEnter,
  setHoverCell,
}: {
  slab: SceneSlab
  isActive: boolean
  cols: number
  rows: number
  onCellDown: (row: number, col: number) => void
  onCellEnter: (row: number, col: number) => void
  setHoverCell: (cell: { row: number; col: number } | null) => void
}) {
  const w = slab.width
  const d = slab.depth
  const y = slab.y
  const halfW = w / 2
  const halfD = d / 2
  const lineColor = isActive ? '#4a6fa5' : '#273349'

  const cellFromEvent = (e: ThreeEvent<PointerEvent>) => worldToCell(e.point.x, e.point.z, cols, rows)

  const interactive: PointerHandlers = isActive
    ? {
        onPointerDown: (e) => {
          if (!isLeftButton(e)) return
          const { row, col } = cellFromEvent(e)
          onCellDown(row, col)
        },
        onPointerMove: (e) => {
          const { row, col } = cellFromEvent(e)
          onCellEnter(row, col)
          setHoverCell({ row, col })
        },
        onPointerOut: () => setHoverCell(null),
      }
    : {}

  return (
    <group {...interactive}>
      <mesh position={[0, y + 0.09, 0]}>
        <boxGeometry args={[w, 0.18, d]} />
        <meshStandardMaterial color={isActive ? '#1e2a40' : '#141a28'} roughness={0.92} metalness={0.08} />
      </mesh>
      <mesh position={[0, y + 0.2, halfD - 0.02]}>
        <boxGeometry args={[w, 0.015, 0.06]} />
        <meshBasicMaterial color={lineColor} />
      </mesh>
      <mesh position={[0, y + 0.2, -halfD + 0.02]}>
        <boxGeometry args={[w, 0.015, 0.06]} />
        <meshBasicMaterial color={lineColor} />
      </mesh>
      <mesh position={[halfW - 0.02, y + 0.2, 0]}>
        <boxGeometry args={[0.06, 0.015, d]} />
        <meshBasicMaterial color={lineColor} />
      </mesh>
      <mesh position={[-halfW + 0.02, y + 0.2, 0]}>
        <boxGeometry args={[0.06, 0.015, d]} />
        <meshBasicMaterial color={lineColor} />
      </mesh>
    </group>
  )
}

function TowerView({
  tower,
  highlighted,
  hovered,
  selectProps,
}: {
  tower: SceneTower
  highlighted: boolean
  hovered: boolean
  selectProps: PointerHandlers
}) {
  const color = highlighted ? HIGHLIGHT : hovered ? HOVER : tower.color
  return (
    <mesh position={[tower.x, tower.y + tower.height / 2, tower.z]} {...selectProps}>
      <boxGeometry args={[0.6, tower.height, 0.6]} />
      <meshStandardMaterial
        color={color}
        emissive={color}
        emissiveIntensity={highlighted || hovered ? 0.7 : 0.3}
        roughness={0.5}
        metalness={0.2}
      />
    </mesh>
  )
}

function BoxView({
  box,
  highlighted,
  hovered,
  selectProps,
}: {
  box: SceneBox
  highlighted: boolean
  hovered: boolean
  selectProps: PointerHandlers
}) {
  const color = highlighted ? HIGHLIGHT : hovered ? HOVER : box.color
  const emissive: ColorRepresentation | undefined = highlighted
    ? '#b45309'
    : hovered
      ? '#0e7490'
      : box.emissive
  const material = box.wire ? (
    <meshBasicMaterial color={color} wireframe transparent opacity={0.9} />
  ) : (
    <meshStandardMaterial
      color={color}
      emissive={emissive}
      emissiveIntensity={highlighted || hovered ? 0.85 : 0.25}
      transparent={typeof box.opacity === 'number'}
      opacity={box.opacity ?? 1}
      roughness={0.7}
      metalness={0.15}
    />
  )
  return (
    <mesh position={box.position} {...selectProps}>
      <boxGeometry args={box.size} />
      {material}
    </mesh>
  )
}

function buildGridPositions(cols: number, rows: number): Float32Array {
  const halfW = cols / 2
  const halfD = rows / 2
  const segments: number[] = []
  for (let i = 0; i <= cols; i += 1) {
    const x = -halfW + i
    segments.push(x, 0, -halfD, x, 0, halfD)
  }
  for (let j = 0; j <= rows; j += 1) {
    const z = halfD - j
    segments.push(-halfW, 0, z, halfW, 0, z)
  }
  return new Float32Array(segments)
}

function SceneContent({
  spec,
  activeFloor,
  activeTool,
  marquee,
  selection,
  onCellDown,
  onCellEnter,
  onEntityDown,
  onMoveCell,
}: {
  spec: SceneSpec
  activeFloor: string
  activeTool: EditorObjectType
  marquee: CellBox | null
  selection: TopologySelection
  onCellDown: (row: number, col: number) => void
  onCellEnter: (row: number, col: number) => void
  onEntityDown: (kind: SceneEntityKind, id: string, floor: string) => void
  onMoveCell: (row: number, col: number) => void
}) {
  const [hoverCell, setHoverCell] = useState<{ row: number; col: number } | null>(null)
  const [hoverEntity, setHoverEntity] = useState<string | null>(null)

  const canSelect = activeTool === 'select'
  const activeFloorIndex = Math.max(0, spec.floors.indexOf(activeFloor))
  const activeFloorY = floorPlaneY(activeFloorIndex)
  const gridPositions = useMemo(() => buildGridPositions(spec.cols, spec.rows), [spec.cols, spec.rows])

  const highlightIds = useMemo(() => {
    const ids = new Set<string>()
    if (!selection) return ids
    if (selection.kind === 'cell' || selection.kind === 'node') {
      ids.add(selection.id)
    } else if (selection.kind === 'zone') {
      for (const item of spec.items) {
        if (item.entity?.kind === 'zone' && item.entity.id === selection.id) ids.add(item.key)
      }
    } else if (selection.kind === 'storage-batch' || selection.kind === 'conveyor-batch') {
      for (const id of selection.ids) ids.add(id)
    } else if (selection.kind === 'shuttle-batch') {
      const slotKey = (r: number, c: number) => `${r}:${c}`
      const slotSet = new Set(selection.slots.map((s) => slotKey(s.row, s.col)))
      for (const item of spec.items) {
        if (
          (item.kind === 'rack' || item.kind === 'box') &&
          item.slotRow != null &&
          item.slotCol != null &&
          slotSet.has(slotKey(item.slotRow, item.slotCol))
        ) {
          ids.add(item.key)
        }
      }
    } else if (selection.kind === 'shuttle-slot') {
      const slotId = shuttleSlotEntityId(selection.zoneId, selection.row, selection.col)
      for (const item of spec.items) {
        if (item.entity?.kind === 'shuttle-slot' && item.entity.id === slotId) ids.add(item.key)
      }
    }
    return ids
  }, [selection, spec])

  const marqueeCenter =
    marquee && marquee.row1 >= marquee.row0 && marquee.col1 >= marquee.col0
      ? {
          x: worldX((marquee.col0 + marquee.col1) / 2, spec.cols),
          z: worldZ((marquee.row0 + marquee.row1) / 2, spec.rows),
          w: (marquee.col1 - marquee.col0 + 1) * CELL,
          d: (marquee.row1 - marquee.row0 + 1) * CELL,
        }
      : null

  // 框选方框的矩形描边（4 条边，8 个顶点，lineSegments）
  const marqueeFramePoints = useMemo(() => {
    if (!marqueeCenter) return null
    const hW = marqueeCenter.w / 2
    const hD = marqueeCenter.d / 2
    return new Float32Array([
      -hW, 0, -hD, hW, 0, -hD,
      hW, 0, -hD, hW, 0, hD,
      hW, 0, hD, -hW, 0, hD,
      -hW, 0, hD, -hW, 0, -hD,
    ])
  }, [marqueeCenter])

  const handleMovePointer = (event: ThreeEvent<PointerEvent>) => {
    const { row, col } = worldToCell(event.point.x, event.point.z, spec.cols, spec.rows)
    onMoveCell(row, col)
  }

  return (
    <group onPointerMove={handleMovePointer}>
      <ambientLight intensity={0.65} />
      <hemisphereLight intensity={0.45} color="#d7dee9" groundColor="#0b101a" />
      <directionalLight position={[14, 24, 12]} intensity={1.5} color="#fff4e6" />
      <directionalLight position={[-12, 8, -14]} intensity={0.35} color="#7dd3fc" />
      <directionalLight position={[0, 30, -4]} intensity={0.4} color="#e2e8f0" />

      {spec.items.map((item) => {
        switch (item.kind) {
          case 'slab':
            return (
              <SlabView
                key={item.key}
                slab={item}
                isActive={item.label === activeFloor}
                cols={spec.cols}
                rows={spec.rows}
                onCellDown={onCellDown}
                onCellEnter={onCellEnter}
                setHoverCell={setHoverCell}
              />
            )
          case 'rack':
            return (
              <RackView
                key={item.key}
                rack={item}
                highlighted={highlightIds.has(item.key)}
                hovered={hoverEntity === item.entity?.id}
                selectProps={makeSelectProps(canSelect, item.entity, onEntityDown, setHoverEntity)}
              />
            )
          case 'tower':
            return (
              <TowerView
                key={item.key}
                tower={item}
                highlighted={highlightIds.has(item.key)}
                hovered={hoverEntity === item.entity?.id}
                selectProps={makeSelectProps(canSelect, item.entity, onEntityDown, setHoverEntity)}
              />
            )
          case 'box':
            return (
              <BoxView
                key={item.key}
                box={item}
                highlighted={highlightIds.has(item.key)}
                hovered={hoverEntity === item.entity?.id}
                selectProps={makeSelectProps(canSelect, item.entity, onEntityDown, setHoverEntity)}
              />
            )
        }
      })}

      {/* 活动层网格线（落位辅助） */}
      <lineSegments position={[0, activeFloorY + 0.24, 0]}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[gridPositions, 3]} />
        </bufferGeometry>
        <lineBasicMaterial color="#33415e" transparent opacity={0.55} />
      </lineSegments>

      {/* 悬停格标记 */}
      {hoverCell ? (
        <mesh position={[worldX(hoverCell.col, spec.cols), activeFloorY + 0.05, worldZ(hoverCell.row, spec.rows)]}>
          <boxGeometry args={[CELL * 0.98, 0.04, CELL * 0.98]} />
          <meshBasicMaterial color={HOVER} transparent opacity={0.35} depthWrite={false} />
        </mesh>
      ) : null}

      {/* 框选 / 拖区域预览：半透明填充 + 矩形描边（浮在场景上方，正在框选时清晰可见） */}
      {marqueeCenter && marqueeFramePoints ? (
        <group position={[marqueeCenter.x, spec.maxY + 0.9, marqueeCenter.z]}>
          <mesh>
            <boxGeometry args={[marqueeCenter.w, 0.06, marqueeCenter.d]} />
            <meshBasicMaterial color="#22d3ee" transparent opacity={0.25} depthWrite={false} />
          </mesh>
          <lineSegments>
            <bufferGeometry>
              <bufferAttribute attach="attributes-position" args={[marqueeFramePoints, 3]} />
            </bufferGeometry>
            <lineBasicMaterial color="#67e8f9" />
          </lineSegments>
        </group>
      ) : null}
    </group>
  )
}

/**
 * 滚轮缩放改为「指向光标」缩放：让光标下的世界点保持不动，替代 OrbitControls
 * 默认的「沿视线朝 target（场景中心）推进」——这样大拓扑里能直接对某块区域放大。
 * 原生滚轮 zoom 已在 OrbitControls 上关闭（enableZoom=false），由本监听接管。
 */
function ZoomToCursor() {
  const camera = useThree((state) => state.camera)
  const gl = useThree((state) => state.gl)
  const controls = useThree((state) => state.controls)

  useEffect(() => {
    if (!controls) return
    const orbit = controls as unknown as { target: Vector3; update: () => void }
    const el = gl.domElement
    const ndc = new Vector2()
    const raycaster = new Raycaster()
    const plane = new Plane()
    const hit = new Vector3()
    const camDir = new Vector3()

    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const rect = el.getBoundingClientRect()
      ndc.set(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1,
      )
      camera.getWorldDirection(camDir)
      plane.setFromNormalAndCoplanarPoint(camDir, orbit.target)
      raycaster.setFromCamera(ndc, camera)
      if (!raycaster.ray.intersectPlane(plane, hit)) return
      const scale = Math.pow(0.95, event.deltaY > 0 ? 1 : -1)
      // 先基于“缩放前”的位置算偏移，再落位，避免 copy(hit) 先改了 position 导致 clone 拿到的已是新值
      const camOffset = camera.position.clone().sub(hit).multiplyScalar(scale)
      const targetOffset = orbit.target.clone().sub(hit).multiplyScalar(scale)
      camera.position.copy(hit).add(camOffset)
      orbit.target.copy(hit).add(targetOffset)
      orbit.update()
    }

    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [camera, gl, controls])

  return null
}

export default function Topology3DScene({
  topology,
  activeFloor,
  activeTool,
  marquee,
  selection,
  onCellDown,
  onCellEnter,
  onEntityDown,
  onMoveCell,
}: Topology3DSceneProps) {
  const spec = useMemo(() => buildSceneSpec(topology), [topology])

  return (
    <Canvas
      dpr={[1, 2]}
      gl={{ antialias: true, alpha: true }}
      camera={{ position: spec.camera.position, fov: 42, near: 0.1, far: 4000 }}
      style={{ background: 'transparent' }}
    >
      <color attach="background" args={['#0a0f19']} />
      <fog attach="fog" args={['#0a0f19', 500, 1600]} />
      <SceneContent
        spec={spec}
        activeFloor={activeFloor}
        activeTool={activeTool}
        marquee={marquee}
        selection={selection}
        onCellDown={onCellDown}
        onCellEnter={onCellEnter}
        onEntityDown={onEntityDown}
        onMoveCell={onMoveCell}
      />
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
