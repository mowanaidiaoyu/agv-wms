import { Canvas } from '@react-three/fiber'
import type { ThreeEvent } from '@react-three/fiber'
import { useMemo } from 'react'
import { buildSceneSpec, floorPlaneY, rackDeckCenterY, SHUTTLE_TRACK_DECK_THICKNESS, worldX, worldZ, type SceneItem, type SceneRack } from '../../lib/topology3d'
import type { TopologyPayload } from '../../types'
import { InventoryOverlay } from '../inventory/InventoryOverlay'
import type { CtuRoadSemanticCell, StorageAccessCandidate } from '../inventory/types'
import { ShuttleLift3D } from '../shuttle-lift/ShuttleLift3D'
import type { ShuttleVisualizationState } from '../shuttle-lift/types'
import { CameraFollowController } from './CameraFollowController'
import { PathDemo3DOverlay } from './PathDemo3DOverlay'
import { RoadCellSelectionLayer } from './RoadCellSelectionLayer'
import type { CameraActionRequest, CameraViewMode } from './cameraControls'
import type {
  PathDemoCell,
  PathDemoEndpointMarker,
  MultiPathConflictEvent,
  PathDemoVehicleView,
  PlaybackStatus,
  TrajectoryPoint,
  TrajectorySample,
} from './types'

function RackModel({ rack, xray = false }: { rack: SceneRack; xray?: boolean }) {
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
          <meshStandardMaterial color={rack.color} transparent={xray} opacity={xray ? 0.16 : 1} metalness={0.25} roughness={0.75} />
        </mesh>
      ))}
      {Array.from({ length: rack.levels }, (_, index) => (
        <mesh key={index} position={[rack.x, rackDeckCenterY(rack, index), rack.z]}>
          <boxGeometry args={[rack.cellsW * 0.96, rack.zoneType === 'shuttle' ? SHUTTLE_TRACK_DECK_THICKNESS : 0.06, rack.cellsD * 0.96]} />
          <meshStandardMaterial color={rack.levelColors?.[index] ?? rack.color} transparent opacity={xray ? 0.12 : 0.48} roughness={0.85} />
        </mesh>
      ))}
      {rack.zoneType === 'shuttle' ? (
        <mesh position={[rack.x, rack.y + height - 0.05, rack.z]}>
          <boxGeometry args={[rack.cellsW * 0.96, 0.06, rack.cellsD * 0.96]} />
          <meshStandardMaterial color={rack.color} transparent opacity={xray ? 0.1 : 0.48} roughness={0.85} />
        </mesh>
      ) : null}
    </group>
  )
}
function StaticSceneItem({
  item,
  inventoryMode,
  selectedInventoryNodeId,
  storageNodeIds,
  onInventoryNodeSelect,
  shuttleRackXray,
}: {
  item: SceneItem
  inventoryMode: boolean
  selectedInventoryNodeId: string | null
  storageNodeIds: ReadonlySet<string>
  onInventoryNodeSelect?: (storageNodeId: string) => void
  shuttleRackXray: boolean
}) {
  if (item.kind === 'slab') {
    return (
      <mesh position={[item.x, item.y + 0.09, item.z]} receiveShadow>
        <boxGeometry args={[item.width, 0.18, item.depth]} />
        <meshStandardMaterial color="#141a28" roughness={0.92} />
      </mesh>
    )
  }
  if (item.kind === 'rack') return <RackModel rack={item} xray={shuttleRackXray && item.zoneType === 'shuttle'} />
  if (item.kind === 'tower') {
    return (
      <mesh position={[item.x, item.y + item.height / 2, item.z]}>
        <boxGeometry args={[0.6, item.height, 0.6]} />
        <meshStandardMaterial color={item.color} emissive={item.color} emissiveIntensity={0.25} />
      </mesh>
    )
  }
  const inventorySelectable = inventoryMode
    && item.entity?.kind === 'node'
    && storageNodeIds.has(item.entity.id)
  const selected = inventorySelectable && item.entity?.id === selectedInventoryNodeId
  const handleInventorySelect = (event: ThreeEvent<PointerEvent>) => {
    if (!inventorySelectable || !item.entity || !onInventoryNodeSelect) return
    event.stopPropagation()
    onInventoryNodeSelect(item.entity.id)
  }
  return (
    <mesh
      position={item.position}
      castShadow={item.size[1] > 0.2}
      onPointerDown={inventorySelectable ? handleInventorySelect : undefined}
    >
      <boxGeometry args={item.size} />
      {item.wire ? (
        <meshBasicMaterial color={item.color} wireframe transparent opacity={0.9} />
      ) : (
        <meshStandardMaterial
          color={selected ? '#facc15' : item.color}
          emissive={selected ? '#a16207' : item.emissive}
          emissiveIntensity={selected ? 0.8 : 0.25}
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
  conflictEvent,
  inventoryMode = false,
  selectedInventoryNodeId = null,
  stockedInventoryNodeIds = new Set<string>(),
  onInventoryNodeSelect,
  accessCandidates = [],
  selectedAccessCellId = null,
  onAccessCandidateSelect,
  roadSemanticCells = [],
  shuttleVisualization,
  shuttleRackXray = false,
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
  conflictEvent?: MultiPathConflictEvent | null
  inventoryMode?: boolean
  selectedInventoryNodeId?: string | null
  stockedInventoryNodeIds?: ReadonlySet<string>
  onInventoryNodeSelect?: (storageNodeId: string) => void
  accessCandidates?: StorageAccessCandidate[]
  selectedAccessCellId?: string | null
  onAccessCandidateSelect?: (accessCellId: string) => void
  roadSemanticCells?: CtuRoadSemanticCell[]
  shuttleVisualization?: ShuttleVisualizationState
  shuttleRackXray?: boolean
}) {
  const spec = useMemo(() => buildSceneSpec(topology), [topology])
  const storageNodeIds = useMemo(
    () => new Set(topology.nodes.filter((node) => node.type === 'storage').map((node) => node.id)),
    [topology.nodes],
  )
  const visibleItems = useMemo(
    () => spec.items.filter((item) => (item.kind === 'slab' ? item.label === activeFloor : item.floor === activeFloor)),
    [activeFloor, spec.items],
  )
  const conflictWorld = useMemo<[number, number, number] | null>(() => {
    if (!conflictEvent || conflictEvent.cell.floor !== activeFloor) return null
    const floors = topology.meta.floors?.length ? topology.meta.floors : ['1F']
    const floorIndex = Math.max(0, floors.indexOf(conflictEvent.cell.floor))
    return [
      worldX(conflictEvent.cell.col, topology.grid.cols),
      floorPlaneY(floorIndex) + 0.22,
      worldZ(conflictEvent.cell.row, topology.grid.rows),
    ]
  }, [activeFloor, conflictEvent, topology])
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
      {visibleItems.map((item) => (
        <StaticSceneItem
          key={item.key}
          item={item}
          inventoryMode={inventoryMode}
          selectedInventoryNodeId={selectedInventoryNodeId}
          storageNodeIds={storageNodeIds}
          onInventoryNodeSelect={onInventoryNodeSelect}
          shuttleRackXray={shuttleRackXray}
        />
      ))}
      {inventoryMode ? (
        <InventoryOverlay
          topology={topology}
          activeFloor={activeFloor}
          selectedStorageNodeId={selectedInventoryNodeId}
          stockedStorageNodeIds={stockedInventoryNodeIds}
          accessCandidates={accessCandidates}
          selectedAccessCellId={selectedAccessCellId}
          onAccessCandidateSelect={onAccessCandidateSelect}
          roadSemanticCells={roadSemanticCells}
        />
      ) : null}
      <PathDemo3DOverlay
        trajectory={trajectory}
        sample={sample}
        vehicles={vehicles}
        endpointMarkers={endpointMarkers}
        selectedVehicleId={selectedVehicleId}
        conflictEvent={conflictEvent}
        conflictWorld={conflictWorld}
      />
      {shuttleVisualization ? <ShuttleLift3D state={shuttleVisualization} /> : null}
      <RoadCellSelectionLayer
        topology={topology}
        activeFloor={activeFloor}
        roadCells={roadCells}
        enabled={roadSelectionEnabled && !inventoryMode}
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
