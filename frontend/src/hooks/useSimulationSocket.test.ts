import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePlatformStore } from '../store/usePlatformStore'
import type { SimulationState } from '../types'
import {
  mergeSimulationTick,
  parseSimulationSocketEvent,
  selectSimulationStateSource,
  useSimulationSocket,
} from './useSimulationSocket'

const legacyState: SimulationState = {
  id: 'run-legacy',
  status: 'running',
  tick: 1,
  topologyId: 'topology-test',
  robots: [],
  tasks: [],
  inventory: [],
  alerts: [],
  queues: { conveyor: [], station: [], buffer: [] },
  createdAt: '2026-10-21T00:00:00Z',
  updatedAt: '2026-10-21T00:00:00Z',
}

describe('simulation socket compatibility', () => {
  beforeEach(() => {
    usePlatformStore.getState().resetRun()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('ignores malformed messages instead of throwing in the socket callback', () => {
    expect(parseSimulationSocketEvent('{broken')).toBeNull()
    expect(parseSimulationSocketEvent(JSON.stringify({ type: 'tick' }))).toBeNull()
  })

  it('preserves legacy state and supplies missing cross-level collections', () => {
    expect(mergeSimulationTick(legacyState, { tick: 2 })).toMatchObject({
      id: 'run-legacy',
      tick: 2,
      transferInventory: [],
      transferEvents: [],
    })
  })

  it('does not create an invalid state from a partial first tick', () => {
    expect(mergeSimulationTick(null, { tick: 1 })).toBeNull()
  })

  it('applies consecutive tick frames instead of freezing at the first tick', () => {
    const tick2 = mergeSimulationTick(legacyState, { tick: 2, updatedAt: 'tick-2' })
    const tick3 = mergeSimulationTick(tick2, { tick: 3, updatedAt: 'tick-3' })

    expect(tick3).toMatchObject({ tick: 3, updatedAt: 'tick-3', inventory: [] })
  })

  it('uses polled state after disconnect so stale live state cannot mask later ticks', () => {
    const polled = { ...legacyState, tick: 8, updatedAt: 'tick-8' }

    expect(selectSimulationStateSource(legacyState, polled, true)?.tick).toBe(1)
    expect(selectSimulationStateSource(legacyState, polled, false)?.tick).toBe(8)
  })

  it('updates the store for consecutive WebSocket ticks and a terminal runtime error', () => {
    const sockets: SocketMock[] = []

    class SocketMock {
      onopen: (() => void) | null = null
      onclose: (() => void) | null = null
      onerror: (() => void) | null = null
      onmessage: ((message: { data: string }) => void) | null = null

      constructor() {
        sockets.push(this)
      }

      close() {
        this.onclose?.()
      }
    }

    vi.stubGlobal('WebSocket', SocketMock)
    const { result, unmount } = renderHook(() => useSimulationSocket('run-legacy'))

    act(() => sockets[0].onopen?.())
    expect(result.current).toBe(true)

    act(() => sockets[0].onmessage?.({
      data: JSON.stringify({ type: 'tick', tick: 1, payload: legacyState }),
    }))
    act(() => sockets[0].onmessage?.({
      data: JSON.stringify({ type: 'tick', tick: 2, payload: { tick: 2, updatedAt: 'tick-2' } }),
    }))
    act(() => sockets[0].onmessage?.({
      data: JSON.stringify({ type: 'tick', tick: 3, payload: { tick: 3, updatedAt: 'tick-3' } }),
    }))

    expect(usePlatformStore.getState().liveState?.tick).toBe(3)

    act(() => sockets[0].onmessage?.({
      data: JSON.stringify({
        type: 'simulation_error',
        tick: 4,
        payload: {
          tick: 4,
          status: 'stopped',
          stopReason: 'runtime_error',
          runtimeError: { type: 'RuntimeError', message: 'boom', tick: 4 },
        },
      }),
    }))

    expect(usePlatformStore.getState().liveState).toMatchObject({
      tick: 4,
      status: 'stopped',
      stopReason: 'runtime_error',
    })
    unmount()
  })
})
