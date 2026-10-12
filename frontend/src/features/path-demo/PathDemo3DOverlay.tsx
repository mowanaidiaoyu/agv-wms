import { DemoVehicle } from './DemoVehicle'
import { PathTrajectory } from './PathTrajectory'
import type { PathDemoVehicleView, TrajectoryPoint, TrajectorySample } from './types'

export function PathDemo3DOverlay({
  trajectory,
  sample,
  vehicles = [],
}: {
  trajectory: TrajectoryPoint[]
  sample: TrajectorySample | null
  vehicles?: PathDemoVehicleView[]
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
      </group>
    )
  }
  return (
    <group>
      <PathTrajectory trajectory={trajectory} />
      {sample ? <DemoVehicle sample={sample} /> : null}
    </group>
  )
}
