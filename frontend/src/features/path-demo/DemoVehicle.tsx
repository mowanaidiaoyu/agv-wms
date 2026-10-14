import { Html } from '@react-three/drei'
import type { TrajectorySample } from './types'

export function DemoVehicle({
  sample,
  label = 'CTU-01',
  color = '#22d3ee',
  selected = false,
  statusText,
}: {
  sample: TrajectorySample
  label?: string
  color?: string
  selected?: boolean
  statusText?: string
}) {
  return (
    <group position={sample.world} rotation={[0, sample.headingRadians, 0]}>
      <mesh position={[0, -0.29, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.38, 0.55, 32]} />
        <meshBasicMaterial color={color} transparent opacity={0.9} depthWrite={false} />
      </mesh>
      {selected ? (
        <mesh position={[0, -0.285, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.62, 0.72, 32]} />
          <meshBasicMaterial color="#f8fafc" transparent opacity={0.95} depthWrite={false} />
        </mesh>
      ) : null}
      <mesh castShadow>
        <boxGeometry args={[0.58, 0.42, 0.72]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.35} metalness={0.45} roughness={0.35} />
      </mesh>
      <mesh position={[0, 0.08, 0.29]}>
        <boxGeometry args={[0.3, 0.16, 0.12]} />
        <meshStandardMaterial color="#f8fafc" emissive="#bae6fd" emissiveIntensity={0.8} />
      </mesh>
      {[-0.23, 0.23].flatMap((x) =>
        [-0.23, 0.23].map((z) => (
          <mesh key={`${x}:${z}`} position={[x, -0.18, z]} rotation={[0, 0, Math.PI / 2]}>
            <cylinderGeometry args={[0.09, 0.09, 0.08, 16]} />
            <meshStandardMaterial color="#111827" roughness={0.9} />
          </mesh>
        )),
      )}
      <Html position={[0, 1.15, 0]} center distanceFactor={10} style={{ pointerEvents: 'none' }}>
        <div
          className={`path-demo-vehicle-label${selected ? ' is-selected' : ''}`}
          style={{ backgroundColor: color, borderColor: color, boxShadow: `0 0 16px ${color}` }}
          aria-hidden="true"
        >
          <span>{label}</span>
          {statusText ? <small>{statusText}</small> : null}
        </div>
      </Html>
    </group>
  )
}
