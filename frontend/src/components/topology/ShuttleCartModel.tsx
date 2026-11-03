import { RoundedBox } from '@react-three/drei'
import { useFrame, useThree } from '@react-three/fiber'
import { useEffect, useRef } from 'react'
import type { Group } from 'three'
import type { GoodsSlot } from '../../types'

const GOODS_COLOR = '#fde047'
const GOODS_EMISSIVE = '#ca8a04'

/** 拓扑编辑器与仿真工作台共用的工业四向车模型。 */
export function ShuttleCartModel({
  load = 0,
  carrying,
  paused = false,
  highlighted = false,
}: {
  load?: number
  carrying?: GoodsSlot[]
  paused?: boolean
  highlighted?: boolean
}) {
  const platformRef = useRef<Group>(null)
  const invalidate = useThree((state) => state.invalidate)
  const loaded = load > 0 || Boolean(carrying?.length)
  const targetPlatformY = loaded ? 0.61 : 0.48
  const platformY = useRef(targetPlatformY)

  useEffect(() => {
    if (paused && platformRef.current) {
      platformY.current = targetPlatformY
      platformRef.current.position.y = targetPlatformY
    }
    invalidate()
  }, [invalidate, paused, targetPlatformY])

  useFrame((_, delta) => {
    if (paused) return
    const alpha = 1 - Math.exp(-Math.min(delta, 0.1) * 8)
    platformY.current += (targetPlatformY - platformY.current) * alpha
    if (Math.abs(targetPlatformY - platformY.current) < 0.002) platformY.current = targetPlatformY
    if (platformRef.current) platformRef.current.position.y = platformY.current
    if (platformY.current !== targetPlatformY) invalidate()
  })

  const longitudinalWheels = [-0.62, 0.62].flatMap((x) => [-0.42, 0.42].map((z) => [x, z] as const))
  const lateralWheels = [-0.42, 0.42].flatMap((x) => [-0.62, 0.62].map((z) => [x, z] as const))
  const chassisColor = highlighted ? '#0369a1' : '#1e3a5f'

  return (
    <group>
      {longitudinalWheels.map(([x, z]) => (
        <mesh key={`long-wheel-${x}-${z}`} position={[x, 0.17, z]} rotation={[0, 0, Math.PI / 2]} castShadow>
          <cylinderGeometry args={[0.16, 0.16, 0.16, 18]} />
          <meshStandardMaterial color="#111827" roughness={0.48} metalness={0.72} />
        </mesh>
      ))}
      {lateralWheels.map(([x, z]) => (
        <mesh key={`side-wheel-${x}-${z}`} position={[x, 0.14, z]} rotation={[Math.PI / 2, 0, 0]} castShadow>
          <cylinderGeometry args={[0.13, 0.13, 0.14, 18]} />
          <meshStandardMaterial color="#334155" roughness={0.45} metalness={0.7} />
        </mesh>
      ))}

      <RoundedBox args={[1.54, 0.34, 1.54]} radius={0.12} smoothness={4} position={[0, 0.31, 0]} castShadow receiveShadow>
        <meshStandardMaterial
          color={chassisColor}
          emissive={highlighted ? '#0ea5e9' : '#0f172a'}
          emissiveIntensity={highlighted ? 0.26 : 0.04}
          roughness={0.4}
          metalness={0.62}
        />
      </RoundedBox>
      {[
        [0, 0.34, -0.78, 1.34, 0.22, 0.08],
        [0, 0.34, 0.78, 1.34, 0.22, 0.08],
        [-0.78, 0.34, 0, 0.08, 0.22, 1.34],
        [0.78, 0.34, 0, 0.08, 0.22, 1.34],
      ].map(([x, y, z, sx, sy, sz], index) => (
        <mesh key={`bumper-${index}`} position={[x, y, z]} castShadow>
          <boxGeometry args={[sx, sy, sz]} />
          <meshStandardMaterial color="#f97316" emissive="#9a3412" emissiveIntensity={0.12} roughness={0.44} metalness={0.4} />
        </mesh>
      ))}

      <group ref={platformRef} position={[0, targetPlatformY, 0]}>
        <mesh position={[0, -0.12, 0]} castShadow>
          <cylinderGeometry args={[0.42, 0.5, 0.18, 24]} />
          <meshStandardMaterial color="#64748b" roughness={0.34} metalness={0.78} />
        </mesh>
        <RoundedBox args={[1.08, 0.13, 1.08]} radius={0.06} smoothness={3} castShadow>
          <meshStandardMaterial color="#cbd5e1" roughness={0.32} metalness={0.72} />
        </RoundedBox>
        {loaded ? (
          <RoundedBox args={[0.88, 0.48, 0.82]} radius={0.07} smoothness={3} position={[0, 0.3, 0]} castShadow>
            <meshStandardMaterial color={GOODS_COLOR} emissive={GOODS_EMISSIVE} emissiveIntensity={0.22} roughness={0.52} />
          </RoundedBox>
        ) : null}
      </group>

      <mesh position={[0, 0.47, -0.78]}>
        <boxGeometry args={[0.64, 0.09, 0.025]} />
        <meshStandardMaterial color="#e2e8f0" emissive="#38bdf8" emissiveIntensity={0.28} roughness={0.35} />
      </mesh>
      <mesh position={[0, 0.5, 0.7]}>
        <boxGeometry args={[0.34, 0.08, 0.04]} />
        <meshStandardMaterial color={loaded ? '#22c55e' : '#38bdf8'} emissive={loaded ? '#16a34a' : '#0284c7'} emissiveIntensity={0.55} />
      </mesh>
      {[[-0.68, -0.68], [0.68, -0.68], [-0.68, 0.68], [0.68, 0.68]].map(([x, z]) => (
        <mesh key={`sensor-${x}-${z}`} position={[x, 0.5, z]}>
          <cylinderGeometry args={[0.055, 0.055, 0.12, 12]} />
          <meshStandardMaterial color="#0f172a" emissive="#06b6d4" emissiveIntensity={0.32} metalness={0.7} />
        </mesh>
      ))}
    </group>
  )
}
