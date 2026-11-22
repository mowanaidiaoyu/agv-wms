export const PALLET_LAYER_HEIGHT = 0.14
export const SHUTTLE_PALLET_BASE_Y = 0.68

/** A single transport stack contains at most six empty pallets. */
export function PalletStackModel({ count }: { count: number }) {
  return Array.from({ length: Math.min(6, Math.max(0, count)) }, (_, index) => (
    <mesh key={index} position={[0, index * PALLET_LAYER_HEIGHT + 0.055, 0]} castShadow receiveShadow>
      <boxGeometry args={[1.1, 0.11, 1.1]} />
      <meshStandardMaterial color="#b45309" roughness={0.72} metalness={0.16} />
    </mesh>
  ))
}
