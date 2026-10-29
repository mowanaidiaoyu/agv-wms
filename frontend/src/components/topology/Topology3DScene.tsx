import { useMemo, useRef, useState } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import type { ThreeEvent } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { MOUSE, Vector3 } from 'three'
import {
  BatchedTopologyMeshes,
  GoodsBoxes,
  HOVER,
  SlabView,
  TowerView,
  ZoomToCursor,
  type PointerHandlers,
} from './SceneMeshes'
import {
  CELL,
  buildGoodsSceneBoxes,
  buildSceneSpec,
  floorPlaneY,
  shuttleSlotEntityId,
  worldX,
  worldZ,
  type SceneBox,
  type SceneCylinder,
  type SceneEntity,
  type SceneEntityKind,
  type SceneRack,
  type SceneSlab,
  type SceneSpec,
  type SceneTower,
} from '../../lib/topology3d'
import { conveyorBlockCellIds, type CellBox } from '../../lib/topology'
import type { EditorObjectType, GoodsSlot, TopologyPayload, ZoneType } from '../../types'
import type { TopologySelection } from '../../store/useTopologyEditorStore'

export type Topology3DSceneProps = {
  topology: TopologyPayload
  goods?: GoodsSlot[]
  activeFloor: string
  activeTool: EditorObjectType
  activeZoneType: ZoneType | null
  marquee: CellBox | null
  selection: TopologySelection
  onCellDown: (row: number, col: number) => void
  onCellEnter: (row: number, col: number) => void
  onEntityDown: (kind: SceneEntityKind, id: string, floor: string) => void
  onMoveCell: (row: number, col: number) => void
  onCompassAngleChange?: (angle: number) => void
}

function CompassOrientationReporter({
  target,
  onChange,
}: {
  target: [number, number, number]
  onChange?: (angle: number) => void
}) {
  const lastAngle = useRef<number | null>(null)
  const origin = useMemo(() => new Vector3(), [])
  const north = useMemo(() => new Vector3(), [])

  useFrame(({ camera }) => {
    if (!onChange) return
    origin.set(target[0], target[1], target[2]).project(camera)
    north.set(target[0], target[1], target[2] + 5).project(camera)
    const dx = north.x - origin.x
    const dy = north.y - origin.y
    if (Math.abs(dx) + Math.abs(dy) < 0.0001) return
    const angle = Math.atan2(dx, dy)
    if (lastAngle.current !== null && Math.abs(angle - lastAngle.current) < 0.005) return
    lastAngle.current = angle
    onChange(angle)
  })
  return null
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

function useGoodsLayer(topology: TopologyPayload, goods: GoodsSlot[], floors: string[]): SceneBox[] {
  const cacheRef = useRef<{ signature: string; goods: GoodsSlot[]; boxes: SceneBox[] } | null>(null)
  const nodeSignature = topology.nodes
    .map((node) => `${node.id}:${node.row}:${node.col}:${node.floor ?? '1F'}`)
    .join('|')
  const signature = `${topology.grid.rows}:${topology.grid.cols}:${floors.join(',')}:${nodeSignature}`
  const cached = cacheRef.current
  if (!cached || cached.signature !== signature || cached.goods !== goods) {
    cacheRef.current = {
      signature,
      goods,
      boxes: buildGoodsSceneBoxes(topology, goods, floors),
    }
  }
  return cacheRef.current?.boxes ?? []
}

function SceneContent({
  topology,
  spec,
  goodsBoxes,
  activeFloor,
  activeTool,
  activeZoneType,
  marquee,
  selection,
  onCellDown,
  onCellEnter,
  onEntityDown,
  onMoveCell,
}: {
  topology: TopologyPayload
  spec: SceneSpec
  goodsBoxes: SceneBox[]
  activeFloor: string
  activeTool: EditorObjectType
  activeZoneType: ZoneType | null
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

  const layers = useMemo(() => {
    const boxes: SceneBox[] = []
    const racks: SceneRack[] = []
    const cylinders: SceneCylinder[] = []
    const slabs: SceneSlab[] = []
    const towers: SceneTower[] = []
    for (const item of spec.items) {
      if (item.kind === 'box') boxes.push(item)
      else if (item.kind === 'rack') racks.push(item)
      else if (item.kind === 'cyl') cylinders.push(item)
      else if (item.kind === 'slab') slabs.push(item)
      else towers.push(item)
    }
    return { boxes, cylinders, racks, slabs, towers }
  }, [spec])

  const highlightIds = useMemo(() => {
    const ids = new Set<string>()
    if (!selection) return ids
    if (selection.kind === 'cell') {
      const cell = topology.cells.find((item) => item.id === selection.id)
      if (cell?.type === 'conveyor') {
        for (const id of conveyorBlockCellIds(topology, selection.id)) ids.add(id)
      } else {
        ids.add(selection.id)
      }
    } else if (selection.kind === 'node') {
      // 节点可能由多个 box 组成（如 CTU 车 5 层载货位），按 entity 匹配整组高亮
      for (const item of spec.items) {
        if (item.entity?.kind === 'node' && item.entity.id === selection.id) ids.add(item.key)
      }
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

  const marqueeCenter = useMemo(
    () =>
      marquee && marquee.row1 >= marquee.row0 && marquee.col1 >= marquee.col0
        ? {
            x: worldX((marquee.col0 + marquee.col1) / 2, spec.cols),
            z: worldZ((marquee.row0 + marquee.row1) / 2, spec.rows),
            w: (marquee.col1 - marquee.col0 + 1) * CELL,
            d: (marquee.row1 - marquee.row0 + 1) * CELL,
          }
        : null,
    [marquee, spec.cols, spec.rows],
  )

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

  const handleCellEnter = (row: number, col: number) => {
    onCellEnter(row, col)
    onMoveCell(row, col)
  }

  return (
    <group>
      <ambientLight intensity={0.65} />
      <hemisphereLight intensity={0.45} color="#d7dee9" groundColor="#0b101a" />
      <directionalLight position={[14, 24, 12]} intensity={1.5} color="#fff4e6" />
      <directionalLight position={[-12, 8, -14]} intensity={0.35} color="#7dd3fc" />
      <directionalLight position={[0, 30, -4]} intensity={0.4} color="#e2e8f0" />

      {layers.slabs.map((slab) => (
        <SlabView
          key={slab.key}
          slab={slab}
          isActive={slab.label === activeFloor}
          cols={spec.cols}
          rows={spec.rows}
          onCellDown={onCellDown}
          onCellEnter={handleCellEnter}
          setHoverCell={setHoverCell}
        />
      ))}

      <BatchedTopologyMeshes
        boxes={layers.boxes}
        racks={layers.racks}
        cylinders={layers.cylinders}
        highlightKeys={highlightIds}
        hoveredEntityId={hoverEntity}
        canSelect={canSelect}
        onEntityDown={onEntityDown}
        setHoverEntity={setHoverEntity}
      />

      {layers.towers.map((tower) => (
        <TowerView
          key={tower.key}
          tower={tower}
          highlighted={highlightIds.has(tower.key)}
          hovered={hoverEntity === tower.entity?.id}
          selectProps={makeSelectProps(canSelect, tower.entity, onEntityDown, setHoverEntity)}
        />
      ))}

      <GoodsBoxes boxes={goodsBoxes} />

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

      {/* 框选 / 拖区域预览：
          - 批量框选：浮在场景上方，便于看清框住了哪些对象；
          - 拖拽安置区域：贴在当前楼层格面上（关闭深度测试，被货架挡住也能看见），
            以便按格子判断区域实际落在哪。 */}
      {marqueeCenter && marqueeFramePoints ? (
        activeZoneType ? (
          <group position={[marqueeCenter.x, activeFloorY + 0.06, marqueeCenter.z]}>
            <mesh renderOrder={10}>
              <boxGeometry args={[marqueeCenter.w, 0.04, marqueeCenter.d]} />
              <meshBasicMaterial color="#22d3ee" transparent opacity={0.3} depthWrite={false} depthTest={false} />
            </mesh>
            <lineSegments renderOrder={11}>
              <bufferGeometry>
                <bufferAttribute attach="attributes-position" args={[marqueeFramePoints, 3]} />
              </bufferGeometry>
              <lineBasicMaterial color="#67e8f9" depthTest={false} transparent opacity={0.95} />
            </lineSegments>
          </group>
        ) : (
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
        )
      ) : null}
    </group>
  )
}

export default function Topology3DScene({
  topology,
  goods,
  activeFloor,
  activeTool,
  activeZoneType,
  marquee,
  selection,
  onCellDown,
  onCellEnter,
  onEntityDown,
  onMoveCell,
  onCompassAngleChange,
}: Topology3DSceneProps) {
  const spec = useMemo(() => buildSceneSpec(topology), [topology])
  const goodsBoxes = useGoodsLayer(topology, goods ?? [], spec.floors)

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
      <SceneContent
        topology={topology}
        spec={spec}
        goodsBoxes={goodsBoxes}
        activeFloor={activeFloor}
        activeTool={activeTool}
        activeZoneType={activeZoneType}
        marquee={marquee}
        selection={selection}
        onCellDown={onCellDown}
        onCellEnter={onCellEnter}
        onEntityDown={onEntityDown}
        onMoveCell={onMoveCell}
      />
      <CompassOrientationReporter target={spec.camera.target} onChange={onCompassAngleChange} />
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
