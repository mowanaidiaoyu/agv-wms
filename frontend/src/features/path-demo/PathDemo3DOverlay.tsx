import { DemoVehicle } from './DemoVehicle'
import { PathTrajectory } from './PathTrajectory'
import { VehicleEndpointMarkers } from './VehicleEndpointMarkers'
import type { PathDemoEndpointMarker, PathDemoVehicleView, TrajectoryPoint, TrajectorySample } from './types'

export function PathDemo3DOverlay({
  trajectory,
  sample,
  vehicles = [],
  endpointMarkers = [],
}: {
  trajectory: TrajectoryPoint[]
  sample: TrajectorySample | null
  vehicles?: PathDemoVehicleView[]
  endpointMarkers?: PathDemoEndpointMarker[]
}) {
  if (vehicles.length) {
    return (
      <group>
        {vehicles.map((vehicle) => (
          <group key={vehicle.vehicleId}>
            <PathTrajectory trajectory={vehicle.trajectory} color={vehicle.color} />
            {vehicle.sample ? <DemoVehicle sample={vehicle.sample} label={vehicle.label} color={vehicle.color} /> : null}
          </group>
        ))}
        <VehicleEndpointMarkers markers={endpointMarkers} />
      </group>
    )
  }
  return (
    <group>
      <PathTrajectory trajectory={trajectory} />
      {sample ? <DemoVehicle sample={sample} /> : null}
      <VehicleEndpointMarkers markers={endpointMarkers} />
    </group>
  )
}
