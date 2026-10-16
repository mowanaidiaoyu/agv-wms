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

      {/* Low-profile industrial chassis. The front of the vehicle points toward +Z. */}
      <mesh position={[0, -0.1, 0]} castShadow receiveShadow>
        <boxGeometry args={[0.68, 0.16, 0.94]} />
        <meshStandardMaterial color="#172033" metalness={0.7} roughness={0.38} />
      </mesh>

      {/* Main colored shell and the raised service cover keep the body visibly lower than it is wide. */}
      <mesh position={[0, 0.07, -0.025]} castShadow>
        <boxGeometry args={[0.58, 0.24, 0.72]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.2} metalness={0.45} roughness={0.32} />
      </mesh>
      <mesh position={[0, 0.235, -0.07]} castShadow>
        <boxGeometry args={[0.44, 0.09, 0.46]} />
        <meshStandardMaterial color="#dbe4ee" metalness={0.55} roughness={0.28} />
      </mesh>

      {/* A dark front face, light bar, and roof arrow make travel direction unambiguous. */}
      <mesh position={[0, 0.035, 0.465]} castShadow>
        <boxGeometry args={[0.58, 0.18, 0.045]} />
        <meshStandardMaterial color="#0f172a" metalness={0.6} roughness={0.3} />
      </mesh>
      <mesh position={[0, 0.07, 0.49]}>
        <boxGeometry args={[0.36, 0.055, 0.018]} />
        <meshStandardMaterial color="#f8fafc" emissive={color} emissiveIntensity={1.8} toneMapped={false} />
      </mesh>
      <mesh position={[0, 0.292, 0.13]} rotation={[Math.PI / 2, 0, 0]}>
        <coneGeometry args={[0.085, 0.2, 3]} />
        <meshStandardMaterial color="#f8fafc" emissive={color} emissiveIntensity={0.8} />
      </mesh>

      {/* Four exposed tires and contrasting hubs remain legible from the usual oblique camera angles. */}
      {[-0.23, 0.23].flatMap((x) =>
        [-0.3, 0.3].map((z) => {
          const wheelX = x < 0 ? -0.355 : 0.355
          const outerHubX = x < 0 ? -0.407 : 0.407

          return (
            <group key={`${x}:${z}`}>
              <mesh position={[wheelX, -0.13, z]} rotation={[0, 0, Math.PI / 2]} castShadow>
                <cylinderGeometry args={[0.105, 0.105, 0.09, 12]} />
                <meshStandardMaterial color="#090d14" metalness={0.15} roughness={0.82} />
              </mesh>
              <mesh position={[outerHubX, -0.13, z]} rotation={[0, 0, Math.PI / 2]}>
                <cylinderGeometry args={[0.045, 0.045, 0.012, 12]} />
                <meshStandardMaterial color="#94a3b8" metalness={0.8} roughness={0.3} />
              </mesh>
            </group>
          )
        }),
      )}

      {/* Compact roof beacon / lidar puck. */}
      <mesh position={[0, 0.325, -0.1]} castShadow>
        <cylinderGeometry args={[0.07, 0.08, 0.08, 12]} />
        <meshStandardMaterial color="#1e293b" metalness={0.7} roughness={0.3} />
      </mesh>
      <mesh position={[0, 0.385, -0.1]}>
        <cylinderGeometry args={[0.045, 0.055, 0.055, 12]} />
        <meshStandardMaterial color="#fbbf24" emissive="#f59e0b" emissiveIntensity={1.4} toneMapped={false} />
      </mesh>

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
