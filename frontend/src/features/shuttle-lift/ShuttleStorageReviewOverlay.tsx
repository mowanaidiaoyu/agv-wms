import { Html } from '@react-three/drei'
import type { ThreeEvent } from '@react-three/fiber'
import type { ShuttleDemoDropoffRecord, ShuttleStorageCandidate, ShuttleTrackNode } from './types'

interface ShuttleStorageReviewOverlayProps {
  candidates: ShuttleStorageCandidate[]
  selectedLocationId: string | null
  selectedAccessTrackNodeId: string | null
  onCandidateSelect?: (locationId: string) => void
  onAccessTrackSelect?: (trackNodeId: string) => void
  dropoffs?: ShuttleDemoDropoffRecord[]
  tracks?: ShuttleTrackNode[]
}

export function ShuttleStorageReviewOverlay({
  candidates,
  selectedLocationId,
  selectedAccessTrackNodeId,
  onCandidateSelect,
  onAccessTrackSelect,
  dropoffs = [],
  tracks = [],
}: ShuttleStorageReviewOverlayProps) {
  const selected = candidates.find((candidate) => candidate.location_id === selectedLocationId) ?? null
  const selectedAccess = selected?.access_options.find(
    (option) => option.track_node_id === selectedAccessTrackNodeId,
  ) ?? null
  const trackLookup = new Map(tracks.map((track) => [track.track_node_id, track]))
  return (
    <group>
      {candidates.map((candidate) => {
        const active = candidate.location_id === selectedLocationId
        const handleSelect = (event: ThreeEvent<PointerEvent>) => {
          event.stopPropagation()
          onCandidateSelect?.(candidate.location_id)
        }
        return (
          <group key={candidate.location_id} position={[candidate.world_x, candidate.world_y + 0.18, candidate.world_z]}>
            <mesh onPointerDown={handleSelect}>
              <boxGeometry args={[1.7, 0.28, 1.7]} />
              <meshStandardMaterial
                color={active ? '#facc15' : '#fb923c'}
                emissive={active ? '#eab308' : '#9a3412'}
                emissiveIntensity={active ? 0.9 : 0.38}
                transparent
                opacity={active ? 0.82 : 0.34}
                wireframe={!active}
              />
            </mesh>
            {active ? (
              <Html position={[0, 0.8, 0]} center distanceFactor={16} style={{ pointerEvents: 'none' }}>
                <span className="shuttle-storage-marker-label">{candidate.location_id}</span>
              </Html>
            ) : null}
          </group>
        )
      })}
      {selected?.access_options.map((option) => {
        const active = option.track_node_id === selectedAccessTrackNodeId
        return (
          <mesh
            key={option.track_node_id}
            position={[option.world_x, option.world_y + 0.16, option.world_z]}
            onPointerDown={(event) => {
              event.stopPropagation()
              onAccessTrackSelect?.(option.track_node_id)
            }}
          >
            <cylinderGeometry args={[active ? 0.46 : 0.32, active ? 0.46 : 0.32, 0.12, 24]} />
            <meshStandardMaterial
              color={active ? '#22c55e' : '#38bdf8'}
              emissive={active ? '#16a34a' : '#0369a1'}
              emissiveIntensity={0.75}
              transparent
              opacity={0.86}
            />
          </mesh>
        )
      })}
      {selectedAccess ? (
        <group
          position={[selectedAccess.world_x, selectedAccess.world_y + 0.23, selectedAccess.world_z]}
          rotation={[0, selectedAccess.required_heading_radians, 0]}
        >
          <mesh>
            <boxGeometry args={[0.92, 0.54, 0.82]} />
            <meshStandardMaterial color="#22d3ee" wireframe transparent opacity={0.8} />
          </mesh>
          <Html position={[0, 0.7, 0]} center distanceFactor={14} style={{ pointerEvents: 'none' }}>
            <span className="shuttle-storage-marker-label">停车包围盒预览</span>
          </Html>
        </group>
      ) : null}
      {dropoffs.map((dropoff) => {
        const track = trackLookup.get(dropoff.track_node_id)
        if (!track) return null
        return (
          <group key={dropoff.dropoff_id} position={[track.world_x, track.world_y + 0.18, track.world_z]}>
            <mesh rotation={[-Math.PI / 2, 0, 0]}>
              <ringGeometry args={[0.46, 0.62, 28]} />
              <meshBasicMaterial color={dropoff.execution_allowed ? '#22c55e' : '#f59e0b'} transparent opacity={0.92} />
            </mesh>
            <Html position={[0, 0.72, 0]} center distanceFactor={16} style={{ pointerEvents: 'none' }}>
              <span className="shuttle-storage-marker-label">{dropoff.label} · {dropoff.status}</span>
            </Html>
          </group>
        )
      })}
    </group>
  )
}
