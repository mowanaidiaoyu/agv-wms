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
  home: [0, 6, 0] as [number, number, number],
  pickup: [-6, 1.12, -1] as [number, number, number],
  pickupLift: [-3.3, 6.3, -0.55] as [number, number, number],
  dropLift: [-2.2, 6.3, 2.75] as [number, number, number],
  dropoff: [-4, 3.52, 5] as [number, number, number],
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

describe('picking arm preview motion', () => {
  it('stays idle and creates no tote without cargo', () => {
    const state = step(createInitialPickingArmState(), 10, {
      ...readySignals,
      cargoToken: null,
      pickedGoodsReady: false,
    })
    expect(state).toMatchObject({ phase: 'idle', totePlacement: 'none', completedCycles: 0 })
  })

  it('runs the complete required phase sequence in order', () => {
    let state = createInitialPickingArmState(readySignals)
    const visited = ['idle']
    for (let index = 0; index < 200 && state.completedCycles === 0; index += 1) {
      const previous = state.phase
      state = step(state, 0.08)
      if (state.phase !== previous) visited.push(state.phase)
    }
    expect(visited).toEqual([...PICKING_ARM_PHASES, 'idle'])
  })

  it('attaches the single tote to the moving gripper after grip', () => {
    const state = step(createInitialPickingArmState(readySignals), 1.7)
    expect(state.phase).toBe('lift')
    expect(state.totePlacement).toBe('gripper')
    expect(pickingArmPose(state, points).target[1]).toBeGreaterThan(points.pickup[1])
  })

  it('places the gripper at the declared dropoff before release', () => {
    const state: PickingArmState = {
      ...createInitialPickingArmState(readySignals),
      phase: 'release',
      phaseElapsed: 0,
      totePlacement: 'gripper',
    }
    expect(pickingArmPose(state, points).target).toEqual(points.dropoff)
  })

  it('waits with the tote on the gripper while the output position is unavailable', () => {
    const state = step(createInitialPickingArmState(readySignals), 20, {
      ...readySignals,
      outputPositionAvailable: false,
    })
    expect(state.phase).toBe('place')
    expect(state.totePlacement).toBe('gripper')
  })

  it('does not execute the same cargo token twice', () => {
    let state = step(createInitialPickingArmState(readySignals), 20)
    expect(state.completedCycles).toBe(1)
    state = step(state, 20)
    expect(state.completedCycles).toBe(1)
    expect(state.consumedCargoToken).toBe(readySignals.cargoToken)
  })

  it('hands the same tote to the conveyor and stops at the preview endpoint', () => {
    const released = step(createInitialPickingArmState(readySignals), 5.5, readySignals, 1, false, 3)
    expect(released).toMatchObject({ totePlacement: 'output', cargoPlaybackState: 'conveyor_moving' })
    const between = step(released, 0.5, readySignals, 1, false, 3)
    expect(between.conveyorDistance).toBeCloseTo(0.5)
    const endpoint = step(between, 3, readySignals, 1, false, 3)
    expect(endpoint).toMatchObject({
      totePlacement: 'output',
      conveyorDistance: 3,
      cargoPlaybackState: 'preview_endpoint_reached',
    })
    expect(canDeclarePickingArmDelivery(endpoint, readySignals)).toBe(false)
  })

  it('freezes, resumes and resets without duplicating cargo', () => {
    const moving = step(createInitialPickingArmState(readySignals), 0.4)
    expect(step(moving, 4, readySignals, 4, true)).toEqual(moving)
    const resumed = step(moving, 0.2)
    expect(resumed.phaseElapsed).toBeGreaterThan(moving.phaseElapsed)
    const reset = resetPickingArmState(resumed, readySignals)
    expect(reset).toMatchObject({
      phase: 'idle',
      totePlacement: 'pickup',
      consumedCargoToken: readySignals.cargoToken,
      conveyorDistance: 0,
    })
    expect(step(reset, 10).phase).toBe('idle')
  })

  it('scales visual time at 1x, 2x and 4x without a business tick', () => {
    const initial = createInitialPickingArmState(readySignals)
    expect(step(initial, 0.1, readySignals, 1).phaseElapsed).toBeCloseTo(0.1)
    expect(step(initial, 0.1, readySignals, 2).phaseElapsed).toBeCloseTo(0.2)
    expect(step(initial, 0.1, readySignals, 4).phaseElapsed).toBeCloseTo(0.4)
  })

  it('changes conveyor progress speed without changing route node order', () => {
    const routeNodeIds = Array.from({ length: 11 }, (_, index) => `cell-route-${index}`)
    const moving: PickingArmState = {
      ...createInitialPickingArmState(readySignals),
      phase: 'idle',
      totePlacement: 'output',
      consumedCargoToken: readySignals.cargoToken,
      cargoPlaybackState: 'conveyor_moving',
    }
    const visit = (speedMultiplier: 1 | 2 | 4) => {
      let state = moving
      const visited: string[] = []
      for (let index = 0; index < 10; index += 1) {
        state = step(state, 0.25, readySignals, speedMultiplier, false, 10)
        const nodeId = routeNodeIds[Math.min(10, Math.floor(state.conveyorDistance))]
        if (visited.at(-1) !== nodeId) visited.push(nodeId)
      }
      return visited
    }
    for (const speed of [1, 2, 4] as const) {
      const visited = visit(speed).map((nodeId) => routeNodeIds.indexOf(nodeId))
      expect(visited).toEqual([...visited].sort((left, right) => left - right))
    }
    expect(routeNodeIds).toEqual(Array.from({ length: 11 }, (_, index) => `cell-route-${index}`))
  })

  it('cannot start on illegal endpoints or declare a preview as delivered', () => {
    const illegal = { ...readySignals, legalDropoffNode: false }
    const state = step(createInitialPickingArmState(illegal), 20, illegal)
    expect(state.phase).toBe('idle')
    expect(canDeclarePickingArmDelivery({
      ...state,
      totePlacement: 'output',
      handoffRequested: true,
    }, readySignals)).toBe(false)
  })
})
