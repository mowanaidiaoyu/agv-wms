import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('SKU heat API client', () => {
  it('uses the C-M2A ABC route and query schema', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await api.getSkuAbc('2026-03-31', 30)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/algorithm/sku/abc?as_of_date=2026-03-31&lookback_days=30',
      expect.objectContaining({ headers: expect.objectContaining({ 'Content-Type': 'application/json' }) }),
    )
  })

  it('posts the exact C-M2A heat request without a mock endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ predictions: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const payload = {
      sku_ids: ['SKU-A'],
      as_of_date: '2026-03-31',
      method: 'weighted_moving_average' as const,
      lookback_days: 7 as const,
      horizon_hours: 24 as const,
    }
    await api.predictSkuHeat(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/v1/algorithm/sku/heat/predict',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(payload) }),
    )
  })

  it('surfaces a backend 422 detail instead of returning stale data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: 'unknown sku_ids: SKU-X' }),
      { status: 422, statusText: 'Unprocessable Entity' },
    )))
    await expect(api.predictSkuHeat({
      sku_ids: ['SKU-X'], as_of_date: '2026-03-31', method: 'moving_average', lookback_days: 7, horizon_hours: 24,
    })).rejects.toThrow('unknown sku_ids: SKU-X')
  })
})
