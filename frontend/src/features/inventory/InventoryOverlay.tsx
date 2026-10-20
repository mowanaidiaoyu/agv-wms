import { useLayoutEffect, useMemo, useRef } from 'react'
import { DoubleSide, Object3D, type InstancedMesh } from 'three'
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
  const selectedNode = markers.find((node) => node.id === selectedStorageNodeId) ?? null
  const stockedNodes = markers.filter((node) => node.id !== selectedStorageNodeId)
  const instancesRef = useRef<InstancedMesh>(null)
  const matrixObject = useMemo(() => new Object3D(), [])

  useLayoutEffect(() => {
    if (!instancesRef.current) return
    stockedNodes.forEach((node, index) => {
      matrixObject.position.set(
        worldX(node.col, topology.grid.cols),
        floorY + 0.72,
        worldZ(node.row, topology.grid.rows),
      )
      matrixObject.updateMatrix()
      instancesRef.current!.setMatrixAt(index, matrixObject.matrix)
    })
    instancesRef.current.instanceMatrix.needsUpdate = true
  }, [floorY, matrixObject, stockedNodes, topology.grid.cols, topology.grid.rows])

  return (
    <group>
      {stockedNodes.length ? (
        <instancedMesh ref={instancesRef} args={[undefined, undefined, stockedNodes.length]} raycast={() => null}>
          <sphereGeometry args={[0.09, 10, 8]} />
          <meshBasicMaterial color="#34d399" depthTest={false} />
        </instancedMesh>
      ) : null}
      {selectedNode ? (
        <group
          position={[
            worldX(selectedNode.col, topology.grid.cols),
            floorY + 0.72,
            worldZ(selectedNode.row, topology.grid.rows),
          ]}
        >
          <mesh raycast={() => null}>
            <sphereGeometry args={[0.15, 10, 8]} />
            <meshBasicMaterial color="#facc15" depthTest={false} />
          </mesh>
          <mesh raycast={() => null} rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.66, 0]}>
            <ringGeometry args={[0.42, 0.55, 28]} />
            <meshBasicMaterial color="#facc15" transparent opacity={0.85} side={DoubleSide} depthWrite={false} />
          </mesh>
        </group>
      ) : null}
    </group>
  )
}
