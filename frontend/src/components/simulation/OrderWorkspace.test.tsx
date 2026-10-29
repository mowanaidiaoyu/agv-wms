import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { GoodsCatalogSku } from '../../types'
import { OrderComposer } from './OrderWorkspace'

const catalog: GoodsCatalogSku[] = [
  { skuId: 'SKU-001', name: '演示商品一', spec: 'BOX-A' },
  { skuId: 'SKU-002', name: '演示商品二', spec: 'BOX-B' },
]

describe('OrderComposer', () => {
  it('adds the selected SKU, updates totals, and submits it to the queue callback', () => {
    const onSubmit = vi.fn()
    render(<OrderComposer open catalog={catalog} onClose={vi.fn()} onSubmit={onSubmit} />)

    fireEvent.change(screen.getByRole('combobox', { name: '商品' }), { target: { value: 'SKU-002' } })
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    // 「演示商品二」同时出现在下拉选项和新增明细行里，这里只断言明细行本身。
    expect(screen.getByText('演示商品二', { selector: 'b' })).toBeInTheDocument()
    expect(screen.getByText('× 5')).toBeInTheDocument()
    expect(screen.getByText(/1 种SKU · 5 箱/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '提交订单' }))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      priority: 'normal',
      dispatchMode: 'balanced',
      maxParallelCtu: 7,
      lines: [expect.objectContaining({ skuId: 'SKU-002', quantity: 5 })],
    }))
  })

  it('creates one parallel order from the multi-vehicle collaboration template', () => {
    const onSubmit = vi.fn()
    const fleetCatalog = Array.from({ length: 7 }, (_, index) => ({
      skuId: `SKU-${index + 1}`,
      name: `协同商品${index + 1}`,
      spec: `BOX-${index + 1}`,
    }))
    render(<OrderComposer open catalog={fleetCatalog} onClose={vi.fn()} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByRole('button', { name: /多车协同订单/ }))
    expect(screen.getByText(/7 种SKU · 14 箱 · 并行优先/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '提交订单' }))

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      dispatchMode: 'parallel',
      maxParallelCtu: 7,
      lines: expect.arrayContaining([expect.objectContaining({ quantity: 2 })]),
    }))
    expect(onSubmit.mock.calls[0][0].lines).toHaveLength(7)
  })

  it('shows the actual reason and retry entry when the catalog fails', () => {
    const onRetryCatalog = vi.fn()
    render(
      <OrderComposer
        open
        catalog={[]}
        catalogError="503 Service Unavailable"
        onRetryCatalog={onRetryCatalog}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    )
    expect(screen.getByRole('combobox', { name: '商品' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '添加' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('503 Service Unavailable')
    fireEvent.click(screen.getByRole('button', { name: '重试商品目录' }))
    expect(onRetryCatalog).toHaveBeenCalledOnce()
  })
})
