import { Html } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { useRef } from 'react'
import type { Group } from 'three'
import type { MultiPathConflictEvent } from './types'

export function ConflictNodeMarker({
  event,
  world,
}: {
  event: MultiPathConflictEvent
  world: [number, number, number]
}) {
  const pulseRef = useRef<Group>(null)
  useFrame(({ clock }) => {
    const pulse = pulseRef.current
    if (!pulse) return
    const scale = 1 + (Math.sin(clock.elapsedTime * 4) + 1) * 0.12
    pulse.scale.setScalar(scale)
  })
  return (
    <group position={world}>
      <group ref={pulseRef} rotation={[-Math.PI / 2, 0, 0]}>
        <mesh>
          <ringGeometry args={[0.55, 0.78, 48]} />
          <meshBasicMaterial color="#ef4444" transparent opacity={0.9} depthWrite={false} />
        </mesh>
      </group>
      <mesh position={[0, 0.015, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[0.32, 40]} />
        <meshBasicMaterial color="#ef4444" transparent opacity={0.28} depthWrite={false} />
      </mesh>
      <Html position={[0, 1.35, 0]} center distanceFactor={11} style={{ pointerEvents: 'none' }}>
        <div className="path-demo-conflict-label" aria-hidden="true">
          <strong>{event.status === 'resolved' ? '潜在冲突 · 已避让' : '最终残留冲突'}</strong>
          <span>{event.cell.id}</span>
          <small>{event.involved_vehicle_ids.join(' / ')}</small>
        </div>
      </Html>
    </group>
  )
}
