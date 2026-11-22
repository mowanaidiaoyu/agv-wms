import { describe, expect, it } from 'vitest'
import { conveyorPositionOnRoute } from './simulationMotion'

describe('conveyorPositionOnRoute', () => {
  it('follows the upper belt around the outlet corner when a tick is skipped', () => {
    const blocks: [number, number, number][] = [
      [11, 3.16, 7],
      [11, 3.16, 5],
      [13, 3.16, 5],
    ]
    expect(conveyorPositionOnRoute(blocks, 0.75)).toEqual([11, 3.16, 5.5])
    expect(conveyorPositionOnRoute(blocks, 1)).toEqual([11, 3.16, 5])
    expect(conveyorPositionOnRoute(blocks, 1.75)).toEqual([12.5, 3.16, 5])
    expect(conveyorPositionOnRoute(blocks, 2.5)).toEqual([13, 3.16, 5])
  })
})
