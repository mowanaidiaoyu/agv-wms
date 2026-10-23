import { describe, expect, it } from 'vitest'
import { shuttleTrackWorldPosition } from './topology3d'

describe('shuttleTrackWorldPosition', () => {
  it('matches the backend shuttle-internal-levels-v1 coordinate contract', () => {
    const liftA = shuttleTrackWorldPosition(100, 19, 1, 129, 216)
    const liftB = shuttleTrackWorldPosition(188, 39, 4, 129, 216)

    expect(liftA[0]).toBe(-44.5)
    expect(liftA[1]).toBeCloseTo(0.35)
    expect(liftA[2]).toBe(7)
    expect(liftB[0]).toBe(-24.5)
    expect(liftB[1]).toBeCloseTo(4.55)
    expect(liftB[2]).toBe(-81)
  })
})
