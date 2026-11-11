import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, formatApiErrorPayload } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('formatApiErrorPayload', () => {
  it('formats FastAPI validation details without object string coercion', () => {
    expect(formatApiErrorPayload({
      detail: [{
        type: 'greater_than',
        loc: ['body', 'transfers', 0, 'quantity'],
        msg: 'Input should be greater than 0',
      }],
    }, '422 Unprocessable Entity')).toBe(
      'transfers.0.quantity: Input should be greater than 0',
    )
  })

  it('supports string detail, object detail, and message responses', () => {
    expect(formatApiErrorPayload({ detail: 'Topology missing' }, 'fallback')).toBe('Topology missing')
    expect(formatApiErrorPayload({ detail: { message: 'Invalid transfer' } }, 'fallback')).toBe('Invalid transfer')
    expect(formatApiErrorPayload({ message: 'Request failed' }, 'fallback')).toBe('Request failed')
  })
})

describe('simulation physics contract request', () => {
  it('omits motionPhysicsContract for legacy starts', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ runId: 'run-1', status: 'running' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await api.startSimulation('topology-1')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(init.body))).toEqual({ topologyId: 'topology-1', orders: [], transfers: [] })
  })

  it('serializes the complete enabled contract without changing other start fields', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ runId: 'run-2', status: 'running' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const contract = {
      physicsContractVersion: 'motion-physics-v1',
      coordinateContractVersion: 'topology-grid-v1',
      metersPerCell: 0.8,
      shuttleLevelHeightMeters: 3,
      floorHeightMeters: 4,
      source: 'USER_DEFINED_SIMULATION' as const,
      calibrationStatus: 'UNVERIFIED' as const,
    }
    await api.startSimulation('topology-1', [], [], contract)
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(init.body))).toEqual({ topologyId: 'topology-1', orders: [], transfers: [], motionPhysicsContract: contract })
  })
})
