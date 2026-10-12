import { Line } from '@react-three/drei'
import type { TrajectoryPoint } from './types'

export function PathTrajectory({ trajectory, color = '#fde047' }: { trajectory: TrajectoryPoint[]; color?: string }) {
  if (!trajectory.length) return null
  const linePoints = trajectory.map((point) => [point.world[0], point.world[1] - 0.23, point.world[2]] as [number, number, number])
  const start = linePoints[0]
  const goal = linePoints[linePoints.length - 1]
  return (
    <group>
      {linePoints.length > 1 ? <Line points={linePoints} color={color} lineWidth={3} /> : null}
      <mesh position={start}>
        <sphereGeometry args={[0.26, 20, 12]} />
        <meshStandardMaterial color="#22c55e" emissive="#16a34a" emissiveIntensity={0.7} />
      </mesh>
      <mesh position={goal}>
        <sphereGeometry args={[0.26, 20, 12]} />
        <meshStandardMaterial color="#f43f5e" emissive="#be123c" emissiveIntensity={0.7} />
      </mesh>
    </group>
  )
}
