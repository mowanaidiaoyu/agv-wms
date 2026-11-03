import { Html, Line } from '@react-three/drei'
import type { ThreeEvent } from '@react-three/fiber'
import {
  CELL,
  CONVEYOR_DECK_BASE,
  CONVEYOR_DECK_STEP,
  CONVEYOR_GROUND_BELT_Y,
  SHUTTLE_RACK_BASE_Y,
  SHUTTLE_TRACK_SURFACE_OFFSET,
  ZONE_LEVEL_HEIGHT,
  worldX,
  worldZ,
} from '../../lib/topology3d'
import { selectLayoutPreview } from '../../lib/transportLayout'
import type {
  LayoutPosition,
  ProposedLayoutConnection,
  ProposedLayoutFacility,
  TransportLayoutPlan,
} from '../../types'


type Props = {
  plan: TransportLayoutPlan
  optionId: string
  cols: number
  rows: number
  selectedItemId?: string
  onSelectItem?: (itemId: string) => void
}

function yFor(position: LayoutPosition, networkType: string): number {
  if (networkType.includes('shuttle')) {
    return SHUTTLE_RACK_BASE_Y
      + SHUTTLE_TRACK_SURFACE_OFFSET
      + (position.level - 1) * ZONE_LEVEL_HEIGHT.shuttle
      + 0.08
  }
  if (position.level > 1) {
    return CONVEYOR_DECK_BASE + (position.level - 1) * CONVEYOR_DECK_STEP + 0.12
  }
  if (networkType.includes('ground_conveyor') || networkType === 'port') {
    return CONVEYOR_GROUND_BELT_Y + 0.12
  }
  return 0.38
}

function worldPosition(
  position: LayoutPosition,
  networkType: string,
  cols: number,
  rows: number,
): [number, number, number] {
  return [
    worldX(position.col, cols) + CELL / 2,
    yFor(position, networkType),
    worldZ(position.row, rows) - CELL / 2,
  ]
}

function connectionColor(connection: ProposedLayoutConnection): string {
  if (connection.status === 'BLOCKED') return '#ef4444'
  if (connection.edgeType === 'shuttle_graph' || connection.evidence.some((item) => item.includes('real node'))) {
    return '#22c55e'
  }
  if (connection.networkType.includes('shuttle')) return '#f97316'
  if (connection.networkType.includes('ctu') || connection.networkType.includes('human_station')) return '#38bdf8'
  return '#facc15'
}

function facilityColor(facility: ProposedLayoutFacility): string {
  if (facility.validationStatus === 'BLOCKED') return '#ef4444'
  if (facility.kind.includes('merge') || facility.kind === 'outbound') return '#a855f7'
  return '#facc15'
}

function stopAndSelect(event: ThreeEvent<MouseEvent>, itemId: string, callback?: (itemId: string) => void) {
  event.stopPropagation()
  callback?.(itemId)
}

export function TransportLayoutPreview({
  plan,
  optionId,
  cols,
  rows,
  selectedItemId = '',
  onSelectItem,
}: Props) {
  const preview = selectLayoutPreview(plan, optionId)
  return (
    <group name="transport-layout-preview">
      {preview.references.map((reference) => {
        const position = worldPosition(reference.position, reference.networkType, cols, rows)
        const selected = selectedItemId === reference.referenceId
        return (
          <group key={reference.referenceId} position={position}>
            <mesh
              onClick={(event) => stopAndSelect(event, reference.referenceId, onSelectItem)}
              renderOrder={18}
            >
              <sphereGeometry args={[selected ? 0.22 : 0.14, 12, 8]} />
              <meshBasicMaterial color="#22c55e" wireframe={!selected} depthTest={false} />
            </mesh>
            {selected ? (
              <Html position={[0, 0.62, 0]} center zIndexRange={[35, 25]}>
                <div className="simulation-layout-label simulation-layout-label-existing">
                  <b>{reference.referenceId}</b>
                  <span>{reference.sourceId}</span>
                  <small>existing · r{reference.position.row},c{reference.position.col},L{reference.position.level}</small>
                </div>
              </Html>
            ) : null}
          </group>
        )
      })}

      {preview.connections.map((connection) => {
        const points = connection.points.map((point) => (
          worldPosition(point, connection.networkType, cols, rows)
        ))
        const color = connectionColor(connection)
        return (
          <Line
            key={connection.connectionId}
            points={points}
            color={color}
            lineWidth={selectedItemId === connection.connectionId ? 5 : 2.5}
            transparent
            opacity={connection.status === 'BLOCKED' ? 0.9 : 0.78}
            depthTest={false}
            renderOrder={17}
            onClick={(event) => stopAndSelect(event, connection.connectionId, onSelectItem)}
          />
        )
      })}

      {preview.facilities.map((facility) => {
        const position = worldPosition(facility.position, facility.networkType, cols, rows)
        const selected = selectedItemId === facility.facilityId
        const color = facilityColor(facility)
        return (
          <group key={facility.facilityId} position={position}>
            <mesh
              onClick={(event) => stopAndSelect(event, facility.facilityId, onSelectItem)}
              renderOrder={19}
            >
              <boxGeometry args={[
                selected ? 0.62 : 0.48,
                facility.spansLevels.length > 1 ? 1.1 : 0.42,
                selected ? 0.62 : 0.48,
              ]} />
              <meshStandardMaterial
                color={color}
                emissive={color}
                emissiveIntensity={selected ? 0.75 : 0.35}
                transparent
                opacity={0.78}
                depthTest={false}
              />
            </mesh>
            {selected ? (
              <Html position={[0, 0.82, 0]} center zIndexRange={[36, 26]}>
                <div className="simulation-layout-label simulation-layout-label-proposed">
                  <b>{facility.facilityId}</b>
                  <span>{facility.kind} · proposed</span>
                  <small>r{facility.position.row},c{facility.position.col},L{facility.position.level} · capacity {facility.capacity}</small>
                </div>
              </Html>
            ) : null}
          </group>
        )
      })}
    </group>
  )
}
