import { describe, expect, it } from 'vitest'
import {
  PICKING_ARM_PHASES,
  advancePickingArm,
  canDeclarePickingArmDelivery,
  createInitialPickingArmState,
  pickingArmPose,
  resetPickingArmState,
  type PickingArmSignals,
  type PickingArmState,
} from './pickingArmMotion'

const readySignals: PickingArmSignals = {
  cargoToken: 'order-1:sku-a:10',
  pickedGoodsReady: true,
  outputPositionAvailable: true,
  legalPickupNode: true,
  legalDropoffNode: true,
  authoritativeHandoffCompleted: false,
}

const points = {
  home: [0, 2, 0] as [number, number, number],
  pickup: [-1, 3.2, 1] as [number, number, number],
  pickupLift: [-1, 3.6, 1] as [number, number, number],
  dropLift: [-1, 3.6, -1] as [number, number, number],
  dropoff: [-1, 2, -1] as [number, number, number],
}

function step(
  state: PickingArmState,
  deltaSeconds: number,
  signals = readySignals,
  speedMultiplier: 1 | 2 | 4 = 1,
  paused = false,
  conveyorPathLength = 0,
) {
  return advancePickingArm(state, {
    deltaSeconds,
    signals,
    speedMultiplier,
    paused,
    conveyorPathLength,
    conveyorSpeed: 1,
  })
}

describe('picking arm handoff motion', () => {
  it('starts idle with no duplicated tote', () => {
    expect(createInitialPickingArmState()).toMatchObject({
      phase: 'idle',
      totePlacement: 'none',
      completedCycles: 0,
    })
  })

  it('uses the required ordered animation phases', () => {
    let state = createInitialPickingArmState(readySignals)
    const visited = ['idle']
    for (let index = 0; index < 200 && state.completedCycles === 0; index += 1) {
      const previous = state.phase
      state = step(state, 0.08)
      if (state.phase !== previous) visited.push(state.phase)
    }
    expect(visited).toEqual([...PICKING_ARM_PHASES, 'idle'])
  })

  it('does not approach or grip without picked-goods-ready', () => {
    const state = step(createInitialPickingArmState(), 10, {
      ...readySignals,
      pickedGoodsReady: false,
    })
    expect(state.phase).toBe('idle')
    expect(state.totePlacement).toBe('none')
  })

  it('attaches exactly one tote to the gripper after grip', () => {
    const state = step(createInitialPickingArmState(readySignals), 1.7)
    expect(state.phase).toBe('lift')
    expect(state.totePlacement).toBe('gripper')
  })

  it('places the gripper at the declared dropoff point', () => {
    const state: PickingArmState = {
      ...createInitialPickingArmState(readySignals),
      phase: 'release',
      phaseElapsed: 0,
      totePlacement: 'gripper',
    }
    expect(pickingArmPose(state, points).target).toEqual(points.dropoff)
  })

  it('raises to the safe waypoint before approaching or returning at low height', () => {
    const approaching: PickingArmState = {
      ...createInitialPickingArmState(readySignals),
      phase: 'approach',
      phaseElapsed: 0.72,
    }
    expect(pickingArmPose(approaching, points).target[1]).toBeGreaterThan(3.4)
    const returning: PickingArmState = {
      ...approaching,
      phase: 'return_home',
      phaseElapsed: 0.48,
    }
    expect(pickingArmPose(returning, points).target[1]).toBeGreaterThan(points.dropoff[1])
  })

  it('waits in place until the output conveyor position is available', () => {
    const state = step(createInitialPickingArmState(readySignals), 20, {
      ...readySignals,
      outputPositionAvailable: false,
    })
    expect(state.phase).toBe('place')
    expect(state.totePlacement).toBe('gripper')
  })

  it('cannot place the same cargo token twice', () => {
    let state = step(createInitialPickingArmState(readySignals), 20)
    expect(state.completedCycles).toBe(1)
    state = step(state, 20)
    expect(state.completedCycles).toBe(1)
    expect(state.consumedCargoToken).toBe(readySignals.cargoToken)
  })

  it('hands the single tote to conveyor_moving after release', () => {
    const released = step(createInitialPickingArmState(readySignals), 5.5, readySignals, 1, false, 4)
    expect(released.totePlacement).toBe('output')
    expect(released.cargoPlaybackState).toBe('conveyor_moving')
    expect(released.conveyorDistance).toBe(0)
  })

  it('moves continuously from the drop cell to the next conveyor node', () => {
    const released = step(createInitialPickingArmState(readySignals), 5.5, readySignals, 1, false, 4)
    const between = step(released, 0.5, readySignals, 1, false, 4)
    expect(between.conveyorDistance).toBeCloseTo(0.5)
    const nextCell = step(between, 0.5, readySignals, 1, false, 4)
    expect(nextCell.conveyorDistance).toBeCloseTo(1)
  })

  it('reaches the proved preview endpoint without declaring order delivery', () => {
    const released = step(createInitialPickingArmState(readySignals), 5.5, readySignals, 1, false, 3)
    const endpoint = step(released, 4, readySignals, 1, false, 3)
    expect(endpoint.conveyorDistance).toBe(3)
    expect(endpoint.cargoPlaybackState).toBe('preview_endpoint_reached')
    expect(canDeclarePickingArmDelivery(endpoint, readySignals)).toBe(false)
  })

  it('freezes phase and progress while paused', () => {
    const moving = step(createInitialPickingArmState(readySignals), 0.4)
    expect(step(moving, 4, readySignals, 4, true)).toEqual(moving)
  })

  it('resumes from the frozen progress', () => {
    const moving = step(createInitialPickingArmState(readySignals), 0.4)
    const resumed = step(moving, 0.2)
    expect(resumed.phaseElapsed).toBeGreaterThan(moving.phaseElapsed)
  })

  it('reset restores home and consumes the current token to prevent duplication', () => {
    const moving = step(createInitialPickingArmState(readySignals), 2)
    const reset = resetPickingArmState(moving, readySignals)
    expect(reset).toMatchObject({
      phase: 'idle',
      totePlacement: 'pickup',
      consumedCargoToken: readySignals.cargoToken,
      completedCycles: 0,
    })
    expect(step(reset, 10).phase).toBe('idle')
    expect(reset.conveyorDistance).toBe(0)
    expect(reset.cargoPlaybackState).toBe('waiting_for_release')
  })

  it('advances visual progress at 1x, 2x and 4x without another business tick', () => {
    const initial = createInitialPickingArmState(readySignals)
    const one = step(initial, 0.1, readySignals, 1)
    const two = step(initial, 0.1, readySignals, 2)
    const four = step(initial, 0.1, readySignals, 4)
    expect(one.phaseElapsed).toBeCloseTo(0.1)
    expect(two.phaseElapsed).toBeCloseTo(0.2)
    expect(four.phaseElapsed).toBeCloseTo(0.4)
  })

  it('cannot start or declare delivery without legal handoff nodes and authoritative completion', () => {
    const invalidSignals = { ...readySignals, legalDropoffNode: false }
    const idle = step(createInitialPickingArmState(invalidSignals), 20, invalidSignals)
    expect(idle.phase).toBe('idle')
    expect(canDeclarePickingArmDelivery(idle, invalidSignals)).toBe(false)
    const placed: PickingArmState = {
      ...idle,
      totePlacement: 'output',
      handoffRequested: true,
    }
    expect(canDeclarePickingArmDelivery(placed, readySignals)).toBe(false)
    expect(canDeclarePickingArmDelivery(placed, {
      ...readySignals,
      authoritativeHandoffCompleted: true,
    })).toBe(true)
  })
})
