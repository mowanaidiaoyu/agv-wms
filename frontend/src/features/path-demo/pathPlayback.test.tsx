import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { TopologyPayload } from '../../types'
import {
  advancePlayback,
  buildTrajectory,
  initialPlaybackState,
  pausePlayback,
  resetPlayback,
  resumePlayback,
  sampleTrajectory,
  seekPlayback,
  startPlayback,
  stepPlaybackTime,
  usePathPlayback,
} from './pathPlayback'
import type { PathDemoCell } from './types'

const topology: TopologyPayload = {
  id: 'topology-test',
  name: 'Test',
  meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 10, cols: 12, cellSize: 32, originX: 0, originY: 0 },
  zones: [],
  cells: [],
  nodes: [],
  resources: [],
}

const path: PathDemoCell[] = [
  { id: 'cell-r2-c3', floor: '1F', row: 2, col: 3, type: 'road', component_id: 'component-1' },
  { id: 'cell-r2-c4', floor: '1F', row: 2, col: 4, type: 'road', component_id: 'component-1' },
  { id: 'cell-r3-c4', floor: '1F', row: 3, col: 4, type: 'road', component_id: 'component-1' },
]

afterEach(() => vi.unstubAllGlobals())

describe('path demo trajectory', () => {
  it('reuses grid-to-Three.js coordinates and derives duration from distance', () => {
    const trajectory = buildTrajectory(path, topology, 2)
    expect(trajectory[0].world).toEqual([-2.5, 0.5, 2.5])
    expect(trajectory[1].world).toEqual([-1.5, 0.5, 2.5])
    expect(trajectory.map((point) => point.arrivalSeconds)).toEqual([0, 0.5, 1])
  })

  it('interpolates continuously along one legal segment and updates heading', () => {
    const trajectory = buildTrajectory(path, topology, 1)
    const sample = sampleTrajectory(trajectory, 0.5)
    expect(sample?.row).toBeCloseTo(2)
    expect(sample?.col).toBeCloseTo(3.5)
    expect(sample?.world[0]).toBeCloseTo(-2)
    expect(sample?.headingRadians).toBeCloseTo(Math.PI / 2)
  })

  it('supports start, pause, resume, reset, rate and terminal stop', () => {
    let state = initialPlaybackState(2)
    state = startPlayback(state, 2)
    state = advancePlayback(state, 0.25, 2)
    expect(state.elapsedSeconds).toBe(0.5)
    state = pausePlayback(state)
    expect(advancePlayback(state, 1, 2).elapsedSeconds).toBe(0.5)
    state = resumePlayback(state)
    state = advancePlayback(state, 1, 2)
    expect(state.status).toBe('completed')
    expect(state.elapsedSeconds).toBe(2)
    expect(resetPlayback(state)).toEqual({ status: 'idle', elapsedSeconds: 0, rate: 2 })
  })

  it('returns no vehicle sample when no planned path exists', () => {
    expect(sampleTrajectory([], 1)).toBeNull()
  })

  it('seeks with clamping and pauses without changing playback rate', () => {
    const running = { status: 'running', elapsedSeconds: 0.5, rate: 4 } as const
    expect(seekPlayback(running, 1.25, 2)).toEqual({ status: 'paused', elapsedSeconds: 1.25, rate: 4 })
    expect(seekPlayback(running, -3, 2).elapsedSeconds).toBe(0)
    expect(seekPlayback(running, 20, 2)).toEqual({ status: 'completed', elapsedSeconds: 2, rate: 4 })
  })

  it('steps on integer timeline ticks without floating point drift and clamps boundaries', () => {
    expect(stepPlaybackTime(0, -1, 0.25, 1)).toBe(0)
    expect(stepPlaybackTime(0, 1, 0.25, 1)).toBe(0.25)
    expect(stepPlaybackTime(0.25, 1, 0.25, 1)).toBe(0.5)
    expect(stepPlaybackTime(0.5, -1, 0.25, 1)).toBe(0.25)
    expect(stepPlaybackTime(0.99, 1, 0.25, 1)).toBe(1)
    expect(stepPlaybackTime(1, 1, 0.25, 1)).toBe(1)
    expect(stepPlaybackTime(0.5, 1, Number.NaN, 1)).toBe(0.5)
  })

  it('resets elapsed time when the historical timeline identity changes', () => {
    const { result, rerender } = renderHook(
      ({ timelineKey }) => usePathPlayback(10, timelineKey),
      { initialProps: { timelineKey: 'experiment-a:astar' } },
    )
    act(() => result.current.seek(4))
    expect(result.current.state.elapsedSeconds).toBe(4)
    rerender({ timelineKey: 'experiment-a:dijkstra' })
    expect(result.current.state).toMatchObject({ status: 'idle', elapsedSeconds: 0 })
  })

  it('cancels its animation frame when the playback hook unmounts', () => {
    let nextId = 0
    const cancel = vi.fn()
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => ++nextId))
    vi.stubGlobal('cancelAnimationFrame', cancel)
    const { result, unmount } = renderHook(() => usePathPlayback(10))
    act(() => result.current.start())
    expect(requestAnimationFrame).toHaveBeenCalled()
    unmount()
    expect(cancel).toHaveBeenCalledWith(1)
  })
})
