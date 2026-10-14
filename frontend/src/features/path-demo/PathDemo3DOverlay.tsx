import { DemoVehicle } from './DemoVehicle'
import { ConflictNodeMarker } from './ConflictNodeMarker'
import { PathTrajectory } from './PathTrajectory'
import { VehicleEndpointMarkers } from './VehicleEndpointMarkers'
import type { MultiPathConflictEvent, PathDemoEndpointMarker, PathDemoVehicleView, TrajectoryPoint, TrajectorySample } from './types'

export function PathDemo3DOverlay({
  trajectory,
  sample,
  vehicles = [],
  endpointMarkers = [],
  selectedVehicleId,
  conflictEvent,
  conflictWorld,
}: {
  trajectory: TrajectoryPoint[]
  sample: TrajectorySample | null
  vehicles?: PathDemoVehicleView[]
  endpointMarkers?: PathDemoEndpointMarker[]
  selectedVehicleId?: string
  conflictEvent?: MultiPathConflictEvent | null
  conflictWorld?: [number, number, number] | null
}) {
  if (vehicles.length) {
    return (
      <group>
        {conflictEvent && conflictWorld ? <ConflictNodeMarker event={conflictEvent} world={conflictWorld} /> : null}
        {vehicles.map((vehicle) => (
          <group key={vehicle.vehicleId}>
            <PathTrajectory trajectory={vehicle.trajectory} color={vehicle.color} />
            {vehicle.sample ? (
              <DemoVehicle
                sample={vehicle.sample}
                label={vehicle.label}
                color={vehicle.color}
                selected={vehicle.vehicleId === selectedVehicleId}
                statusText={vehicle.statusText}
              />
            ) : null}
          </group>
        ))}
        <VehicleEndpointMarkers markers={endpointMarkers} />
      </group>
    )
  }
  return (
    <group>
      {conflictEvent && conflictWorld ? <ConflictNodeMarker event={conflictEvent} world={conflictWorld} /> : null}
      <PathTrajectory trajectory={trajectory} />
      {sample ? <DemoVehicle sample={sample} /> : null}
      <VehicleEndpointMarkers markers={endpointMarkers} />
    </group>
  )
}
