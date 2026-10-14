import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { SkuInventoryLab } from './SkuInventoryLab'
import type { ABCResponse, HeatPredictionResponse } from './types'

vi.mock('../../lib/api', () => ({
  api: { getSkuAbc: vi.fn(), predictSkuHeat: vi.fn() },
}))

const abcResponse: ABCResponse = {
  algorithm_version: 'abc-v1.0.0',
  data_source: 'synthetic_demo',
  as_of_date: '2026-03-31',
  lookback_start_date: '2026-03-02',
  lookback_end_date: '2026-03-31',
  config: {
    lookback_days: 30,
    frequency_weight: 0.4,
    quantity_weight: 0.4,
    order_line_weight: 0.2,
    a_cumulative_share: 0.7,
    b_cumulative_share: 0.9,
  },
  items: [
    { sku_id: 'SKU-A', abc_class: 'A', score: 88, outbound_frequency: 12, outbound_quantity: 100, order_line_count: 14, frequency_normalized: 1, quantity_normalized: 1, order_line_normalized: 1, updated_at: '2026-03-31T00:00:00Z' },
    { sku_id: 'SKU-B', abc_class: 'B', score: 44, outbound_frequency: 6, outbound_quantity: 50, order_line_count: 7, frequency_normalized: 0.5, quantity_normalized: 0.5, order_line_normalized: 0.5, updated_at: '2026-03-31T00:00:00Z' },
    { sku_id: 'SKU-C', abc_class: 'C', score: 0, outbound_frequency: 0, outbound_quantity: 0, order_line_count: 0, frequency_normalized: 0, quantity_normalized: 0, order_line_normalized: 0, updated_at: '2026-03-31T00:00:00Z' },
  ],
}

function heatResponse(overrides: Partial<HeatPredictionResponse> = {}): HeatPredictionResponse {
  return {
    algorithm_version: 'heat-baseline-v1.0.0',
    data_source: 'synthetic_demo',
    as_of_date: '2026-03-31',
    forecast_date: '2026-04-01',
    lookback_start_date: '2026-03-25',
    lookback_end_date: '2026-03-31',
    method: 'weighted_moving_average',
    lookback_days: 7,
    horizon_hours: 24,
    predictions: [
      { sku_id: 'SKU-A', predicted_heat: 12.5 },
      { sku_id: 'SKU-B', predicted_heat: 4.25 },
      { sku_id: 'SKU-C', predicted_heat: 0 },
    ],
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolver) => { resolve = resolver })
  return { promise, resolve }
}

async function runAbc(response: ABCResponse = abcResponse) {
  vi.mocked(api.getSkuAbc).mockResolvedValueOnce(response)
  fireEvent.click(screen.getByRole('button', { name: '执行 ABC 分类' }))
  await waitFor(() => expect(api.getSkuAbc).toHaveBeenCalled())
  await screen.findByText(new RegExp(response.algorithm_version.replaceAll('.', '\\.'), 'i'))
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.predictSkuHeat).mockResolvedValue(heatResponse())
})

describe('SkuInventoryLab', () => {
  it('renders an explicit synthetic-data boundary and a valid demo default date', () => {
    render(<SkuInventoryLab />)
    expect(screen.getByText('库存预测与优化')).toBeInTheDocument()
    expect(screen.getByText('合成演示数据')).toBeInTheDocument()
    expect(screen.getByText(/不代表实际仓储订单、库存或库位热度/)).toBeInTheDocument()
    expect(screen.getByLabelText('ABC 分类截止日期')).toHaveValue('2026-03-31')
  })

  it('calls the real ABC endpoint contract and shows counts, ratios, score and raw features separately', async () => {
    render(<SkuInventoryLab />)
    await runAbc()
    expect(api.getSkuAbc).toHaveBeenCalledWith('2026-03-31', 30, expect.any(AbortSignal))
    const overview = screen.getByLabelText('ABC 分类概览')
    expect(within(overview).getByText('A 类 SKU')).toBeInTheDocument()
    expect(within(overview).getAllByText('33.3%')).toHaveLength(3)
    const table = screen.getByRole('table', { name: '动态 ABC 分类明细' })
    expect(within(table).getByText('ABC score')).toBeInTheDocument()
    expect(within(table).getByText('88.00')).toBeInTheDocument()
    expect(within(table).getByText('100')).toBeInTheDocument()
    expect(within(table).getByText('A')).toBeInTheDocument()
    expect(screen.getByText(/权重：0.4 \/ 0.4 \/ 0.2/)).toBeInTheDocument()
  })

  it('supports SKU search and ABC class filtering without recomputing the server result', async () => {
    render(<SkuInventoryLab />)
    await runAbc()
    fireEvent.change(screen.getByLabelText('ABC SKU 搜索'), { target: { value: 'SKU-B' } })
    const table = screen.getByRole('table', { name: '动态 ABC 分类明细' })
    expect(within(table).getByText('SKU-B')).toBeInTheDocument()
    expect(within(table).queryByText('SKU-A')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('ABC SKU 搜索'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('ABC 分类筛选'), { target: { value: 'C' } })
    expect(within(table).getByText('SKU-C')).toBeInTheDocument()
    expect(within(table).queryByText('SKU-B')).not.toBeInTheDocument()
    expect(api.getSkuAbc).toHaveBeenCalledTimes(1)
  })

  it('sends SMA and the selected 14-day window, then displays forecast date and quantity units', async () => {
    render(<SkuInventoryLab />)
    await runAbc()
    fireEvent.click(screen.getByRole('tab', { name: 'SKU 次日热度预测' }))
    fireEvent.change(screen.getByLabelText('热度预测方法'), { target: { value: 'moving_average' } })
    fireEvent.change(screen.getByLabelText('热度预测窗口'), { target: { value: '14' } })
    vi.mocked(api.predictSkuHeat).mockResolvedValueOnce(heatResponse({ method: 'moving_average', lookback_days: 14 }))
    fireEvent.click(screen.getByRole('button', { name: '运行热度预测' }))
    await waitFor(() => expect(api.predictSkuHeat).toHaveBeenCalledWith({
      sku_ids: ['SKU-A', 'SKU-B', 'SKU-C'],
      as_of_date: '2026-03-31',
      method: 'moving_average',
      lookback_days: 14,
      horizon_hours: 24,
    }, expect.any(AbortSignal)))
    expect((await screen.findAllByText('2026-04-01')).length).toBeGreaterThan(0)
    expect(screen.getByText('12.50（预计出库数量）')).toBeInTheDocument()
    expect(screen.getByText(/不是概率、ABC score、实时库存量/)).toBeInTheDocument()
  })

  it.each([7, 14, 30] as const)('sends the supported %d-day WMA window', async (window) => {
    render(<SkuInventoryLab />)
    await runAbc()
    fireEvent.click(screen.getByRole('tab', { name: 'SKU 次日热度预测' }))
    fireEvent.change(screen.getByLabelText('热度预测窗口'), { target: { value: String(window) } })
    vi.mocked(api.predictSkuHeat).mockResolvedValueOnce(heatResponse({ lookback_days: window }))
    fireEvent.click(screen.getByRole('button', { name: '运行热度预测' }))
    await waitFor(() => expect(api.predictSkuHeat).toHaveBeenCalledWith(expect.objectContaining({
      method: 'weighted_moving_average', lookback_days: window,
    }), expect.any(AbortSignal)))
  })

  it('handles an empty ABC SKU set without inventing prediction options', async () => {
    render(<SkuInventoryLab />)
    await runAbc({ ...abcResponse, items: [] })
    fireEvent.click(screen.getByRole('tab', { name: 'SKU 次日热度预测' }))
    expect(screen.getByText('ABC 接口未返回可预测 SKU。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '运行热度预测' })).toBeDisabled()
  })

  it('shows readable network and unknown-SKU errors without retaining stale results', async () => {
    vi.mocked(api.getSkuAbc).mockRejectedValueOnce(new Error('Network unavailable'))
    render(<SkuInventoryLab />)
    fireEvent.click(screen.getByRole('button', { name: '执行 ABC 分类' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable')

    await runAbc()
    fireEvent.click(screen.getByRole('tab', { name: 'SKU 次日热度预测' }))
    vi.mocked(api.predictSkuHeat).mockRejectedValueOnce(new Error('unknown sku_ids: SKU-A'))
    fireEvent.click(screen.getByRole('button', { name: '运行热度预测' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('unknown sku_ids: SKU-A')
    expect(screen.queryByRole('table', { name: 'SKU 次日热度预测结果' })).not.toBeInTheDocument()
  })

  it('does not let an obsolete heat response overwrite the latest configuration', async () => {
    render(<SkuInventoryLab />)
    await runAbc()
    fireEvent.click(screen.getByRole('tab', { name: 'SKU 次日热度预测' }))
    const first = deferred<HeatPredictionResponse>()
    const second = deferred<HeatPredictionResponse>()
    vi.mocked(api.predictSkuHeat).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    fireEvent.click(screen.getByRole('button', { name: '运行热度预测' }))
    expect(screen.getByRole('button', { name: '正在预测…' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('热度预测窗口'), { target: { value: '14' } })
    fireEvent.click(screen.getByRole('button', { name: '运行热度预测' }))
    await act(async () => second.resolve(heatResponse({ lookback_days: 14, predictions: [{ sku_id: 'SKU-A', predicted_heat: 22 }] })))
    expect(await screen.findByText('22.00（预计出库数量）')).toBeInTheDocument()
    await act(async () => first.resolve(heatResponse({ predictions: [{ sku_id: 'SKU-A', predicted_heat: 11 }] })))
    expect(screen.queryByText('11.00（预计出库数量）')).not.toBeInTheDocument()
    expect(screen.getByText('22.00（预计出库数量）')).toBeInTheDocument()
  })
})
