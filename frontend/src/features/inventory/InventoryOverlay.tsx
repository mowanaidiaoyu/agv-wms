import { useMemo } from 'react'
import { DoubleSide } from 'three'
import { floorPlaneY, worldX, worldZ } from '../../lib/topology3d'
import { floorsOf } from '../../lib/topology'
import type { TopologyPayload } from '../../types'

interface InventoryOverlayProps {
  topology: TopologyPayload
  activeFloor: string
  selectedStorageNodeId: string | null
  stockedStorageNodeIds: ReadonlySet<string>
}

/** Lightweight scene markers: no permanent text and no duplicate rack geometry. */
export function InventoryOverlay({
  topology,
  activeFloor,
  selectedStorageNodeId,
  stockedStorageNodeIds,
}: InventoryOverlayProps) {
  const floorY = floorPlaneY(Math.max(0, floorsOf(topology).indexOf(activeFloor)))
  const markers = useMemo(
    () => topology.nodes.filter(
      (node) => node.type === 'storage'
        && (node.floor ?? '1F') === activeFloor
        && (stockedStorageNodeIds.has(node.id) || node.id === selectedStorageNodeId),
    ),
    [activeFloor, selectedStorageNodeId, stockedStorageNodeIds, topology.nodes],
  )

  return (
    <group>
      {markers.map((node) => {
        const selected = node.id === selectedStorageNodeId
        const x = worldX(node.col, topology.grid.cols)
        const z = worldZ(node.row, topology.grid.rows)
        return (
          <group key={`inventory-${node.id}`} position={[x, floorY + 0.72, z]}>
            <mesh>
              <sphereGeometry args={[selected ? 0.15 : 0.09, 10, 8]} />
              <meshBasicMaterial color={selected ? '#facc15' : '#34d399'} depthTest={false} />
            </mesh>
            {selected ? (
              <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.66, 0]}>
                <ringGeometry args={[0.42, 0.55, 28]} />
                <meshBasicMaterial color="#facc15" transparent opacity={0.85} side={DoubleSide} depthWrite={false} />
              </mesh>
            ) : null}
          </group>
        )
      })}
    </group>
  )
}
