import { Html } from '@react-three/drei'
import type { PathDemoEndpointMarker } from './types'

export function VehicleEndpointMarkers({ markers }: { markers: PathDemoEndpointMarker[] }) {
  return (
    <group>
      {markers.map((marker) => (
        <group key={`${marker.vehicleId}:${marker.kind}`} position={marker.world}>
          <mesh rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={marker.kind === 'start' ? [0.34, 0.49, 32] : [0.22, 0.49, 4]} />
            <meshBasicMaterial color={marker.color} transparent opacity={0.9} depthWrite={false} />
          </mesh>
          <Html position={[0, 0.55, 0]} center distanceFactor={18} style={{ pointerEvents: 'none' }}>
            <span className="path-demo-endpoint-label" style={{ borderColor: marker.color, color: marker.color }}>
              {marker.label}
            </span>
          </Html>
        </group>
      ))}
    </group>
  )
}
