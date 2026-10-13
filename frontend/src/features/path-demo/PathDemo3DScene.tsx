import { Canvas } from '@react-three/fiber'
import { useMemo } from 'react'
import { buildSceneSpec, type SceneItem, type SceneRack } from '../../lib/topology3d'
import type { TopologyPayload } from '../../types'
import { CameraFollowController } from './CameraFollowController'
import { PathDemo3DOverlay } from './PathDemo3DOverlay'
import { RoadCellSelectionLayer } from './RoadCellSelectionLayer'
import type { CameraActionRequest, CameraViewMode } from './cameraControls'
import type {
  PathDemoCell,
  PathDemoEndpointMarker,
  PathDemoVehicleView,
  PlaybackStatus,
  TrajectoryPoint,
  TrajectorySample,
} from './types'

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
  playbackStatus,
  cameraViewMode,
  cameraActionRequest,
  vehicles = [],
  followedVehicleSample,
  roadCells = [],
  roadSelectionEnabled = false,
  endpointMarkers = [],
  selectedVehicleId,
  onRoadCellSelect,
  onInvalidRoadSelection,
}: {
  topology: TopologyPayload
  activeFloor: string
  trajectory: TrajectoryPoint[]
  sample: TrajectorySample | null
  playbackStatus: PlaybackStatus
  cameraViewMode: CameraViewMode
  cameraActionRequest: CameraActionRequest | null
  vehicles?: PathDemoVehicleView[]
  followedVehicleSample?: TrajectorySample | null
  roadCells?: PathDemoCell[]
  roadSelectionEnabled?: boolean
  endpointMarkers?: PathDemoEndpointMarker[]
  selectedVehicleId?: string
  onRoadCellSelect?: (cell: PathDemoCell) => void
  onInvalidRoadSelection?: () => void
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
      <PathDemo3DOverlay
        trajectory={trajectory}
        sample={sample}
        vehicles={vehicles}
        endpointMarkers={endpointMarkers}
        selectedVehicleId={selectedVehicleId}
      />
      <RoadCellSelectionLayer
        topology={topology}
        activeFloor={activeFloor}
        roadCells={roadCells}
        enabled={roadSelectionEnabled}
        onSelect={onRoadCellSelect}
        onInvalidSelection={onInvalidRoadSelection}
      />
      <CameraFollowController
        initialPosition={spec.camera.position}
        initialTarget={spec.camera.target}
        sample={followedVehicleSample === undefined ? sample : followedVehicleSample}
        playbackStatus={playbackStatus}
        viewMode={cameraViewMode}
        actionRequest={cameraActionRequest}
      />
    </Canvas>
  )
}
