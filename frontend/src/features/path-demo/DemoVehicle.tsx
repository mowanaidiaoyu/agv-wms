import { Html } from '@react-three/drei'
import type { TrajectorySample } from './types'

export function DemoVehicle({ sample }: { sample: TrajectorySample }) {
  return (
    <group position={sample.world} rotation={[0, sample.headingRadians, 0]}>
      <mesh position={[0, -0.29, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.38, 0.55, 32]} />
        <meshBasicMaterial color="#facc15" transparent opacity={0.9} depthWrite={false} />
      </mesh>
      <mesh castShadow>
        <boxGeometry args={[0.58, 0.42, 0.72]} />
        <meshStandardMaterial color="#22d3ee" emissive="#0e7490" emissiveIntensity={0.45} metalness={0.45} roughness={0.35} />
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
        <div className="path-demo-vehicle-label" aria-hidden="true">
          <span>CTU-01</span>
        </div>
      </Html>
    </group>
  )
}
