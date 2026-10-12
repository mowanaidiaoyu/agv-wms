import { DemoVehicle } from './DemoVehicle'
import { PathTrajectory } from './PathTrajectory'
import type { TrajectoryPoint, TrajectorySample } from './types'

export function PathDemo3DOverlay({
  trajectory,
  sample,
}: {
  trajectory: TrajectoryPoint[]
  sample: TrajectorySample | null
}) {
  return (
    <group>
      <PathTrajectory trajectory={trajectory} />
      {sample ? <DemoVehicle sample={sample} /> : null}
    </group>
  )
}
