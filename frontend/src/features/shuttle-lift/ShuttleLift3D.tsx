import { Html, Line } from '@react-three/drei'
import { useLayoutEffect, useMemo, useRef } from 'react'
import { InstancedMesh, Object3D } from 'three'
import { SHUTTLE_TRACK_DECK_THICKNESS } from '../../lib/topology3d'
import type {
  ShuttleLift,
  ShuttleLiftPlaybackState,
  ShuttleMultiPlaybackSample,
  ShuttlePlaybackSample,
  ShuttleTrackNode,
  ShuttleVisualizationState,
} from './types'
import { shuttleLiftPlatformY } from './shuttlePlayback'
import { SHUTTLE_MULTI_COLORS } from './shuttleMultiPlayback'
import {
  SHUTTLE_ENDPOINT_MARKER_OFFSET,
  SHUTTLE_PATH_LINE_OFFSET,
  shuttleLiftShaftBounds,
  shuttleTrackMeshCenterY,
} from './shuttleGeometry'

const SHUTTLE_LEVELS = [1, 2, 3, 4] as const
const LEVEL_COLORS = ['#38bdf8', '#22d3ee', '#818cf8', '#c084fc'] as const

function ShuttleTrackInstances({ state }: { state: ShuttleVisualizationState }) {
  const level1Ref = useRef<InstancedMesh>(null)
  const level2Ref = useRef<InstancedMesh>(null)
  const level3Ref = useRef<InstancedMesh>(null)
  const level4Ref = useRef<InstancedMesh>(null)
  const grouped = useMemo(
    () => SHUTTLE_LEVELS.map((level) => state.tracks.filter((node) => node.shuttle_level === level)),
    [state.tracks],
  )
  useLayoutEffect(() => {
    const object = new Object3D()
    const meshes = [level1Ref.current, level2Ref.current, level3Ref.current, level4Ref.current]
    grouped.forEach((nodes, levelIndex) => {
      const mesh = meshes[levelIndex]
      if (!mesh) return
      nodes.forEach((node, index) => {
        object.position.set(node.world_x, shuttleTrackMeshCenterY(node.world_y), node.world_z)
        object.updateMatrix()
        mesh.setMatrixAt(index, object.matrix)
      })
      mesh.instanceMatrix.needsUpdate = true
    })
  }, [grouped])
  return (
    <group>
      {grouped.map((nodes, index) => (
        <instancedMesh
          key={index}
          ref={index === 0 ? level1Ref : index === 1 ? level2Ref : index === 2 ? level3Ref : level4Ref}
          args={[undefined, undefined, nodes.length]}
          frustumCulled={false}
        >
          <boxGeometry args={[1.58, SHUTTLE_TRACK_DECK_THICKNESS, 1.58]} />
          <meshStandardMaterial
            color={LEVEL_COLORS[index]}
            emissive={LEVEL_COLORS[index]}
            emissiveIntensity={0.34}
            transparent
            opacity={0.52}
            roughness={0.65}
          />
        </instancedMesh>
      ))}
    </group>
  )
}
function LiftModel({
  lift,
  entrances,
  sample,
  playbackState,
  selected,
}: {
  lift: ShuttleLift
  entrances: ShuttleTrackNode[]
  sample: ShuttlePlaybackSample | null
  playbackState?: ShuttleLiftPlaybackState
  selected: boolean
}) {
  const entranceNodes = Object.values(lift.level_entrances)
  const active = playbackState
    ? playbackState.status !== 'idle'
    : sample?.liftId === lift.lift_id && ['entering_lift', 'moving_up', 'moving_down', 'exiting_lift'].includes(sample.phase)
  const levelYs = entrances.map((node) => node.world_y)
  const baseY = Math.min(...levelYs)
  const topY = Math.max(...levelYs)
  const platformY = playbackState?.platformY ?? shuttleLiftPlatformY(sample, lift.lift_id, baseY)
  const shaft = shuttleLiftShaftBounds(levelYs)
  const entrance = entrances[0]
  if (!entrance || !shaft || !Number.isFinite(baseY) || !Number.isFinite(topY)) return null
  const x = entrance.world_x
  const z = entrance.world_z
  const color = selected ? '#facc15' : '#f59e0b'
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, (shaft.bottomY + shaft.topY) / 2, 0]}>
        <boxGeometry args={[1.72, shaft.topY - shaft.bottomY, 1.72]} />
        <meshStandardMaterial color={color} wireframe transparent opacity={selected ? 0.9 : 0.55} />
      </mesh>
      <mesh position={[0, platformY, 0]}>
        <boxGeometry args={[1.5, SHUTTLE_TRACK_DECK_THICKNESS, 1.5]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={active ? 0.75 : 0.28} metalness={0.42} />
      </mesh>
      <Html position={[0, topY + 0.85, 0]} center distanceFactor={18} style={{ pointerEvents: 'none' }}>
        <span className={`shuttle-lift-label ${active ? 'is-active' : ''}`}>
          {lift.label} · {playbackState ? `L${playbackState.currentLevel}` : `${entranceNodes.length} 层`}
          {playbackState?.status === 'repositioning_empty' ? ' · 空载回程' : ''}
          {playbackState?.occupantVehicleId ? ` · ${playbackState.occupantVehicleId}` : ''}
        </span>
      </Html>
    </group>
  )
}

function ShuttleVehicle({
  sample,
  color = '#14b8a6',
  selected = false,
  loadLabel,
}: {
  sample: ShuttlePlaybackSample | ShuttleMultiPlaybackSample
  color?: string
  selected?: boolean
  loadLabel?: string
}) {
  return (
    <group position={sample.world} rotation={[0, sample.headingRadians, 0]}>
      <mesh castShadow>
        <boxGeometry args={[0.92, 0.22, 0.82]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={selected ? 0.72 : 0.4} metalness={0.38} roughness={0.4} />
      </mesh>
      <mesh position={[0, 0.16, 0]} castShadow>
        <boxGeometry args={[0.66, 0.12, 0.56]} />
        <meshStandardMaterial color="#dbeafe" metalness={0.55} roughness={0.3} />
      </mesh>
      <mesh position={[0, 0.25, 0]}>
        <cylinderGeometry args={[0.08, 0.08, 0.12, 12]} />
        <meshStandardMaterial color="#facc15" emissive="#f59e0b" emissiveIntensity={0.8} />
      </mesh>
      {[-0.34, 0.34].flatMap((x) => [-0.3, 0.3].map((z) => (
        <mesh key={`${x}:${z}`} position={[x, -0.13, z]} rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[0.1, 0.1, 0.1, 10]} />
          <meshStandardMaterial color="#111827" roughness={0.82} />
        </mesh>
      )))}
      <mesh position={[0, -0.2, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.48, 0.6, 28]} />
        <meshBasicMaterial color={selected ? '#f8fafc' : color} transparent opacity={selected ? 0.95 : 0.7} />
      </mesh>
      <Html position={[0, 0.72, 0]} center distanceFactor={14} style={{ pointerEvents: 'none' }}>
        <span className={`shuttle-vehicle-label ${selected ? 'is-selected' : ''}`}>
          {sample.vehicleId} · L{sample.shuttleLevel}{loadLabel ? <small>{loadLabel}</small> : null}
        </span>
      </Html>
    </group>
  )
}

function ShuttleTransferMarkers({ state }: { state: ShuttleVisualizationState }) {
  const transfer = state.transfer
  if (!transfer) return null
  const markers = [
    { key: 'pickup', location: transfer.source, color: '#f59e0b', label: '取货位' },
    { key: 'dropoff', location: transfer.destination, color: '#22c55e', label: '目标货位' },
  ] as const
  return (
    <group>
      {markers.map(({ key, location, color, label }) => (
        <group key={key} position={[location.world_x, location.world_y + 0.22, location.world_z]}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[0.58, 0.82, 32]} />
            <meshBasicMaterial color={color} transparent opacity={0.95} />
          </mesh>
          <mesh position={[0, 0.3, 0]}>
            <boxGeometry args={[1.72, 0.42, 1.72]} />
            <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.75} wireframe transparent opacity={0.75} />
          </mesh>
          <Html position={[0, 1.05, 0]} center distanceFactor={16} style={{ pointerEvents: 'none' }}>
            <span className="shuttle-transfer-marker-label">
              {label} · {transfer.skuId} × {transfer.quantity}
            </span>
          </Html>
        </group>
      ))}
    </group>
  )
}

function ShuttleMultiPaths({ state }: { state: ShuttleVisualizationState }) {
  const lookup = useMemo(() => new Map(state.tracks.map((node) => [node.track_node_id, node])), [state.tracks])
  if (!state.multiPlan || state.multiPlan.status !== 'planning_success') return null
  return (
    <group>
      {state.multiPlan.vehicles.flatMap((vehicle, vehicleIndex) => vehicle.path.slice(1).map((nodeId, index) => {
        const from = lookup.get(vehicle.path[index])
        const to = lookup.get(nodeId)
        if (!from || !to) return null
        const vertical = from.shuttle_level !== to.shuttle_level
        return (
          <Line
            key={`${vehicle.vehicle_id}:${from.track_node_id}:${to.track_node_id}`}
            points={[
              [from.world_x, from.world_y + SHUTTLE_PATH_LINE_OFFSET, from.world_z],
              [to.world_x, to.world_y + SHUTTLE_PATH_LINE_OFFSET, to.world_z],
            ]}
            color={vertical ? '#facc15' : SHUTTLE_MULTI_COLORS[vehicleIndex % SHUTTLE_MULTI_COLORS.length]}
            lineWidth={vertical ? 4 : 2.4}
            dashed={vertical}
            dashSize={0.22}
            gapSize={0.12}
          />
        )
      }))}
      {state.multiPlan.vehicles.flatMap((vehicle, index) => [
        <mesh key={`${vehicle.vehicle_id}:start`} position={[vehicle.start.world_x, vehicle.start.world_y + SHUTTLE_ENDPOINT_MARKER_OFFSET, vehicle.start.world_z]}>
          <sphereGeometry args={[0.13, 12, 8]} />
          <meshBasicMaterial color={SHUTTLE_MULTI_COLORS[index % SHUTTLE_MULTI_COLORS.length]} />
        </mesh>,
        <mesh key={`${vehicle.vehicle_id}:goal`} position={[vehicle.destination.world_x, vehicle.destination.world_y + SHUTTLE_ENDPOINT_MARKER_OFFSET, vehicle.destination.world_z]}>
          <sphereGeometry args={[0.13, 12, 8]} />
          <meshBasicMaterial color="#ef4444" />
        </mesh>,
      ])}
    </group>
  )
}

function ShuttlePath({ state }: { state: ShuttleVisualizationState }) {
  const lookup = useMemo(() => new Map(state.tracks.map((node) => [node.track_node_id, node])), [state.tracks])
  if (!state.plan || state.plan.result_status !== 'success') return null
  return (
    <group>
      {state.plan.path_edges.map((edge, index) => {
        const from = lookup.get(edge.from_track_node)
        const to = lookup.get(edge.to_track_node)
        if (!from || !to) return null
        const vertical = edge.edge_type === 'lift'
        return (
          <Line
            key={`${edge.from_track_node}:${edge.to_track_node}:${index}`}
            points={[
              [from.world_x, from.world_y + SHUTTLE_PATH_LINE_OFFSET, from.world_z],
              [to.world_x, to.world_y + SHUTTLE_PATH_LINE_OFFSET, to.world_z],
            ]}
            color={vertical ? '#facc15' : '#f8fafc'}
            lineWidth={vertical ? 4 : 2.4}
            dashed={vertical}
            dashSize={0.22}
            gapSize={0.12}
          />
        )
      })}
      {state.plan.start ? (
        <mesh position={[state.plan.start.world_x, state.plan.start.world_y + SHUTTLE_ENDPOINT_MARKER_OFFSET, state.plan.start.world_z]}>
          <sphereGeometry args={[0.13, 12, 8]} />
          <meshBasicMaterial color="#22c55e" />
        </mesh>
      ) : null}
      {state.plan.destination ? (
        <mesh position={[state.plan.destination.world_x, state.plan.destination.world_y + SHUTTLE_ENDPOINT_MARKER_OFFSET, state.plan.destination.world_z]}>
          <sphereGeometry args={[0.13, 12, 8]} />
          <meshBasicMaterial color="#ef4444" />
        </mesh>
      ) : null}
    </group>
  )
}

export function ShuttleLift3D({ state }: { state: ShuttleVisualizationState }) {
  const trackLookup = useMemo(() => new Map(state.tracks.map((node) => [node.track_node_id, node])), [state.tracks])
  return (
    <group>
      <ShuttleTrackInstances state={state} />
      <ShuttleTransferMarkers state={state} />
      {state.multiPlan ? <ShuttleMultiPaths state={state} /> : <ShuttlePath state={state} />}
      {state.lifts.map((lift) => (
        <LiftModel
          key={lift.lift_id}
          lift={lift}
          entrances={Object.values(lift.level_entrances).map((nodeId) => trackLookup.get(nodeId)).filter((node): node is ShuttleTrackNode => Boolean(node))}
          sample={state.sample}
          playbackState={state.liftStates?.find((item) => item.liftId === lift.lift_id)}
          selected={state.selectedLiftId === lift.lift_id}
        />
      ))}
      {state.multiSamples?.length
        ? state.multiSamples.map((sample, index) => (
          <ShuttleVehicle
            key={sample.vehicleId}
            sample={sample}
            color={SHUTTLE_MULTI_COLORS[index % SHUTTLE_MULTI_COLORS.length]}
            selected={state.selectedShuttleId === sample.vehicleId}
          />
        ))
        : state.sample ? (
          <ShuttleVehicle
            sample={state.sample}
            selected
            loadLabel={state.transfer?.vehicleLoadQuantity
              ? `载货 ${state.transfer.skuId} × ${state.transfer.vehicleLoadQuantity}`
              : state.transfer ? '空载' : undefined}
          />
        ) : null}
    </group>
  )
}

// End of the offline shuttle visualization layer.
