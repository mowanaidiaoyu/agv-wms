import type { ThreeEvent } from '@react-three/fiber'
import { useRef } from 'react'
import { floorPlaneY } from '../../lib/topology3d'
import type { TopologyPayload } from '../../types'
import { findRoadCellAtWorldPoint, isClickGesture, type PointerPosition } from './roadSelection'
import type { PathDemoCell } from './types'

export function RoadCellSelectionLayer({
  topology,
  activeFloor,
  roadCells,
  enabled,
  onSelect,
  onInvalidSelection,
}: {
  topology: TopologyPayload
  activeFloor: string
  roadCells: PathDemoCell[]
  enabled: boolean
  onSelect?: (cell: PathDemoCell) => void
  onInvalidSelection?: () => void
}) {
  const pointerDownRef = useRef<PointerPosition | null>(null)
  const floors = topology.meta.floors?.length ? topology.meta.floors : ['1F']
  const floorIndex = Math.max(0, floors.indexOf(activeFloor))

  if (!enabled) return null

  const pointerPosition = (event: ThreeEvent<PointerEvent>): PointerPosition => ({
    x: event.nativeEvent.clientX,
    y: event.nativeEvent.clientY,
  })

  return (
    <mesh
      position={[0, floorPlaneY(floorIndex) + 0.205, 0]}
      rotation={[-Math.PI / 2, 0, 0]}
      onPointerDown={(event) => {
        if (event.nativeEvent.button === 0) pointerDownRef.current = pointerPosition(event)
      }}
      onPointerUp={(event) => {
        const startedAt = pointerDownRef.current
        pointerDownRef.current = null
        if (event.nativeEvent.button !== 0 || !startedAt || !isClickGesture(startedAt, pointerPosition(event))) return
        const cell = findRoadCellAtWorldPoint(event.point.x, event.point.z, topology, roadCells, activeFloor)
        if (cell) onSelect?.(cell)
        else onInvalidSelection?.()
      }}
    >
      <planeGeometry args={[topology.grid.cols, topology.grid.rows]} />
      <meshBasicMaterial transparent opacity={0} depthWrite={false} />
    </mesh>
  )
}
