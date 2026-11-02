import { describe, expect, it } from 'vitest'
import {
  advanceSimulationRollerAngle,
  boundedSimulationTransitionDuration,
  interpolateSimulationPoint,
  isSimulationConveyorMotion,
  rollerRotationDirection,
  shouldRenderSimulationCargoIndependently,
  simulationCargoBottomY,
  simulationMotionYaw,
} from './simulationTestVisualization'

describe('simulation_test authoritative visualization', () => {
  it('keeps interpolation inside the latest confirmed Conveyor edge', () => {
    const from = [10, 0.6, 20] as const
    const to = [12, 0.6, 20] as const

    expect(interpolateSimulationPoint(from, to, -1)).toEqual([10, 0.6, 20])
    expect(interpolateSimulationPoint(from, to, 0.5)).toEqual([11, 0.6, 20])
    expect(interpolateSimulationPoint(from, to, 2)).toEqual([12, 0.6, 20])
  })

  it('turns via the confirmed segment tangent without a long reverse spin', () => {
    const east = simulationMotionYaw(0, [0, 0.6, 0], [1, 0.6, 0])
    const south = simulationMotionYaw(east, [1, 0.6, 0], [1, 0.6, 1])

    expect(east).toBeCloseTo(Math.PI / 2)
    expect(south).toBeCloseTo(0)
    expect(Math.abs(south - east)).toBeLessThanOrEqual(Math.PI)
  })

  it('aligns roller rotation with positive and negative route directions', () => {
    expect(rollerRotationDirection([1, 0])).toBe(1)
    expect(rollerRotationDirection([0, 1])).toBe(1)
    expect(rollerRotationDirection([-1, 0])).toBe(-1)
    expect(rollerRotationDirection([0, -1])).toBe(-1)
    expect(advanceSimulationRollerAngle(2, 0.5, [1, 0], true, false)).toBe(6.5)
    expect(advanceSimulationRollerAngle(2, 0.5, [-1, 0], true, false)).toBe(-2.5)
    expect(advanceSimulationRollerAngle(2, 0.5, [1, 0], true, true)).toBe(2)
    expect(advanceSimulationRollerAngle(2, 0.5, [1, 0], false, false)).toBe(2)
  })

  it('keeps cargo bottoms on the authoritative belt surface', () => {
    expect(simulationCargoBottomY(0.6)).toBeCloseTo(0.61)
    expect(simulationCargoBottomY(3)).toBeCloseTo(3.01)
  })

  it('uses bounded timing and never extrapolates indefinitely', () => {
    expect(boundedSimulationTransitionDuration(0)).toBe(0.08)
    expect(boundedSimulationTransitionDuration(0.6)).toBeCloseTo(0.648)
    expect(boundedSimulationTransitionDuration(4)).toBe(1.2)
  })

  it('assigns exactly one visual owner across Shuttle, Conveyor and arm states', () => {
    expect(shouldRenderSimulationCargoIndependently(
      { cargoId: 'case-1', status: 'shuttle_transporting' },
    )).toBe(false)
    expect(shouldRenderSimulationCargoIndependently(
      { cargoId: 'case-1', status: 'conveyor_transporting' },
    )).toBe(true)
    expect(shouldRenderSimulationCargoIndependently(
      { cargoId: 'case-1', status: 'waiting_arm' },
      'case-1',
    )).toBe(false)
    expect(shouldRenderSimulationCargoIndependently(
      { cargoId: 'case-1', status: 'arm_processing' },
      'case-1',
    )).toBe(false)
    expect(isSimulationConveyorMotion('conveyor_transporting')).toBe(true)
    expect(isSimulationConveyorMotion('merge_conveyor_transporting')).toBe(true)
    expect(isSimulationConveyorMotion('waiting_arm')).toBe(false)
  })
})
