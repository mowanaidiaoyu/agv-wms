import { Canvas, useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { useCallback, useEffect, useMemo, useRef } from 'react'
import type { ElementRef } from 'react'
import { buildSceneSpec, type SceneItem, type SceneRack } from '../../lib/topology3d'
import type { TopologyPayload } from '../../types'
import { PathDemo3DOverlay } from './PathDemo3DOverlay'
import { constrainCameraAboveGround, PATH_DEMO_ORBIT_SETTINGS } from './cameraControls'
import type { TrajectoryPoint, TrajectorySample } from './types'

function PathDemoCameraControls({
  initialPosition,
  initialTarget,
  resetToken,
}: {
  initialPosition: [number, number, number]
  initialTarget: [number, number, number]
  resetToken: number
}) {
  const camera = useThree((state) => state.camera)
  const controlsRef = useRef<ElementRef<typeof OrbitControls>>(null)

  useEffect(() => {
    const controls = controlsRef.current
    camera.position.set(...initialPosition)
    camera.up.set(0, 1, 0)
    controls?.target.set(...initialTarget)
    controls?.update()
    controls?.saveState()
  }, [camera, initialPosition, initialTarget, resetToken])

  const keepCameraAboveGround = useCallback(() => {
    const controls = controlsRef.current
    if (controls && constrainCameraAboveGround(camera.position, controls.target)) controls.update()
  }, [camera])

  return (
    <OrbitControls
      ref={controlsRef}
      makeDefault
      target={initialTarget}
      enableDamping
      dampingFactor={0.12}
      enableRotate={PATH_DEMO_ORBIT_SETTINGS.enableRotate}
      enableZoom={PATH_DEMO_ORBIT_SETTINGS.enableZoom}
      enablePan={PATH_DEMO_ORBIT_SETTINGS.enablePan}
      mouseButtons={PATH_DEMO_ORBIT_SETTINGS.mouseButtons}
      minDistance={1}
      maxDistance={2000}
      minPolarAngle={0.03}
      maxPolarAngle={Math.PI / 2 - 0.02}
      screenSpacePanning
      onChange={keepCameraAboveGround}
    />
  )
}

function RackModel({ rack }: { rack: SceneRack }) {
  const height = rack.levels * rack.levelHeight
  const halfWidth = rack.cellsW / 2
  const halfDepth = rack.cellsD / 2
  const posts = [
    [-halfWidth, -halfDepth],
    [-halfWidth, halfDepth],
    [halfWidth, -halfDepth],
    [halfWidth, halfDepth],
  ]
  return (
    <group>
      {posts.map(([x, z]) => (
        <mesh key={`${x}:${z}`} position={[rack.x + x, rack.y + height / 2, rack.z + z]}>
          <boxGeometry args={[0.08, height, 0.08]} />
          <meshStandardMaterial color={rack.color} metalness={0.25} roughness={0.75} />
        </mesh>
      ))}
      {Array.from({ length: rack.levels }, (_, index) => (
        <mesh key={index} position={[rack.x, rack.y + (index + 1) * rack.levelHeight - 0.05, rack.z]}>
          <boxGeometry args={[rack.cellsW * 0.96, 0.06, rack.cellsD * 0.96]} />
          <meshStandardMaterial color={rack.levelColors?.[index] ?? rack.color} transparent opacity={0.48} roughness={0.85} />
        </mesh>
      ))}
    </group>
  )
}
function StaticSceneItem({ item }: { item: SceneItem }) {
  if (item.kind === 'slab') {
    return (
      <mesh position={[item.x, item.y + 0.09, item.z]} receiveShadow>
        <boxGeometry args={[item.width, 0.18, item.depth]} />
        <meshStandardMaterial color="#141a28" roughness={0.92} />
      </mesh>
    )
  }
  if (item.kind === 'rack') return <RackModel rack={item} />
  if (item.kind === 'tower') {
    return (
      <mesh position={[item.x, item.y + item.height / 2, item.z]}>
        <boxGeometry args={[0.6, item.height, 0.6]} />
        <meshStandardMaterial color={item.color} emissive={item.color} emissiveIntensity={0.25} />
      </mesh>
    )
  }
  return (
    <mesh position={item.position} castShadow={item.size[1] > 0.2}>
      <boxGeometry args={item.size} />
      {item.wire ? (
        <meshBasicMaterial color={item.color} wireframe transparent opacity={0.9} />
      ) : (
        <meshStandardMaterial
          color={item.color}
          emissive={item.emissive}
          emissiveIntensity={0.25}
          transparent={typeof item.opacity === 'number'}
          opacity={item.opacity ?? 1}
          roughness={0.72}
          metalness={0.12}
        />
      )}
    </mesh>
  )
}
export function PathDemo3DScene({
  topology,
  activeFloor,
  trajectory,
  sample,
  cameraResetToken,
}: {
  topology: TopologyPayload
  activeFloor: string
  trajectory: TrajectoryPoint[]
  sample: TrajectorySample | null
  cameraResetToken: number
}) {
  const spec = useMemo(() => buildSceneSpec(topology), [topology])
  const visibleItems = useMemo(
    () => spec.items.filter((item) => (item.kind === 'slab' ? item.label === activeFloor : item.floor === activeFloor)),
    [activeFloor, spec.items],
  )
  return (
    <Canvas
      dpr={[1, 1.5]}
      camera={{ position: spec.camera.position, fov: 42, near: 0.1, far: 4000 }}
      shadows
      style={{ background: '#0a0f19' }}
    >
      <fog attach="fog" args={['#0a0f19', 500, 1600]} />
      <ambientLight intensity={0.7} />
      <hemisphereLight intensity={0.5} color="#d7dee9" groundColor="#0b101a" />
      <directionalLight position={[14, 24, 12]} intensity={1.5} castShadow />
      {visibleItems.map((item) => <StaticSceneItem key={item.key} item={item} />)}
      <PathDemo3DOverlay trajectory={trajectory} sample={sample} />
      <PathDemoCameraControls
        initialPosition={spec.camera.position}
        initialTarget={spec.camera.target}
        resetToken={cameraResetToken}
      />
    </Canvas>
  )
}
