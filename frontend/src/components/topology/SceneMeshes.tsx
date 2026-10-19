import { useEffect, useMemo, useRef } from 'react'
import { useThree } from '@react-three/fiber'
import type { ThreeEvent } from '@react-three/fiber'
import { Color, Object3D, Plane, Raycaster, Vector2, Vector3 } from 'three'
import type { ColorRepresentation, InstancedMesh } from 'three'
import {
  worldToCell,
  type SceneBox,
  type SceneRack,
  type SceneSlab,
  type SceneTower,
} from '../../lib/topology3d'

export const HIGHLIGHT = '#fde047'
export const HOVER = '#22d3ee'

export type PointerHandlers = {
  onPointerDown?: (e: ThreeEvent<PointerEvent>) => void
  onPointerMove?: (e: ThreeEvent<PointerEvent>) => void
  onPointerOver?: (e: ThreeEvent<PointerEvent>) => void
  onPointerOut?: (e: ThreeEvent<PointerEvent>) => void
}

export function RackView({
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

export function SlabView({
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
  onCellDown?: (row: number, col: number) => void
  onCellEnter?: (row: number, col: number) => void
  setHoverCell?: (cell: { row: number; col: number } | null) => void
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
          if (e.nativeEvent.button !== 0) return
          const { row, col } = cellFromEvent(e)
          onCellDown?.(row, col)
        },
        onPointerMove: (e) => {
          const { row, col } = cellFromEvent(e)
          onCellEnter?.(row, col)
          setHoverCell?.({ row, col })
        },
        onPointerOut: () => setHoverCell?.(null),
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

export function TowerView({
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

export function BoxView({
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

/**
 * 商品箱批量渲染：一个库位槽一个实例，全部塞进单个 InstancedMesh，
 * 避免 666 拓扑全量库位（2.5k 列 × 17 层 ≈ 4.2 万箱）逐槽 <mesh> 拖垮场景。
 * 纯展示不可选中，取货高亮通过 highlightKeys 把对应实例染成高亮色。
 */
export function GoodsBoxes({
  boxes,
  highlightKeys,
}: {
  boxes: SceneBox[]
  highlightKeys?: ReadonlySet<string>
}) {
  const meshRef = useRef<InstancedMesh>(null)

  useEffect(() => {
    const mesh = meshRef.current
    if (!mesh) return
    const dummy = new Object3D()
    const highlight = new Color(HIGHLIGHT)
    const color = new Color()
    boxes.forEach((box, i) => {
      dummy.position.set(box.position[0], box.position[1], box.position[2])
      dummy.scale.set(box.size[0], box.size[1], box.size[2])
      dummy.updateMatrix()
      mesh.setMatrixAt(i, dummy.matrix)
      mesh.setColorAt(i, highlightKeys?.has(box.key) ? highlight : color.set(box.color))
    })
    mesh.instanceMatrix.needsUpdate = true
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
  }, [boxes, highlightKeys])

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, boxes.length]} frustumCulled={false}>
      <boxGeometry args={[1, 1, 1]} />
      <meshStandardMaterial roughness={0.7} metalness={0.15} />
    </instancedMesh>
  )
}

/**
 * 滚轮缩放改为「指向光标」缩放：让光标下的世界点保持不动，替代 OrbitControls
 * 默认的「沿视线朝 target（场景中心）推进」——这样大拓扑里能直接对某块区域放大。
 * 原生滚轮 zoom 已在 OrbitControls 上关闭（enableZoom=false），由本监听接管。
 */
export function ZoomToCursor() {
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
