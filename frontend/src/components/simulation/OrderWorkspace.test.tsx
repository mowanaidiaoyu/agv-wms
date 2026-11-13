import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { GoodsCatalogSku, SimulationOrder, SimulationTask } from '../../types'
import { OrderComposer, OrderOrchestrationPanel } from './OrderWorkspace'

const catalog: GoodsCatalogSku[] = [
  { skuId: 'SKU-001', name: '演示商品一', spec: 'BOX-A' },
  { skuId: 'SKU-002', name: '演示商品二', spec: 'BOX-B' },
]

describe('OrderComposer', () => {
  it('opens the shared Run settings without owning a second configuration state', () => {
    const onOpenRunConfig = vi.fn()
    const { rerender } = render(<OrderComposer open catalog={catalog} runActive={false} onOpenRunConfig={onOpenRunConfig} onClose={vi.fn()} onSubmit={vi.fn()} />)
    expect(screen.getByText(/待下一次 Run 启动时提交/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('本次仿真运行设置'))
    fireEvent.click(screen.getByRole('button', { name: '打开运行配置' }))
    expect(onOpenRunConfig).toHaveBeenCalledOnce()
    rerender(<OrderComposer open catalog={catalog} runActive onOpenRunConfig={onOpenRunConfig} onClose={vi.fn()} onSubmit={vi.fn()} />)
    expect(screen.getByText(/立即插入当前 Run/)).toBeInTheDocument()
    expect(screen.getByText(/添加订单不会修改这些配置/)).toBeInTheDocument()
  })

  it('adds the selected SKU, updates totals, and submits it to the queue callback', () => {
    const onSubmit = vi.fn()
    render(<OrderComposer open catalog={catalog} onClose={vi.fn()} onSubmit={onSubmit} />)

    fireEvent.change(screen.getByRole('combobox', { name: '商品' }), { target: { value: 'SKU-002' } })
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    // 「演示商品二」同时出现在下拉选项和新增明细行里，这里只断言明细行本身。
    expect(screen.getByText('演示商品二', { selector: 'b' })).toBeInTheDocument()
    expect(screen.getByText('× 5 箱')).toBeInTheDocument()
    expect(screen.getByText(/1 种SKU · 5 件/)).toBeInTheDocument()

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
    expect(screen.getByText(/7 种SKU · 14 件 · 并行优先/)).toBeInTheDocument()
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

describe('OrderOrchestrationPanel', () => {
  it('restores an API-mojibake port label and preserves full task context at a narrow viewport', () => {
    const originalWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 600 })
    const order = {
      id: 'ORD-中文-001',
      priority: 'normal',
      dispatchMode: 'balanced',
      targetStation: 'station-a',
      createdAt: '2026-11-12T00:00:00Z',
      lines: [{ id: 'line-1', skuId: 'sku-1', skuName: '测试商品', quantity: 1 }],
      status: 'executing',
      statusLabel: '执行中',
      taskIds: ['task-ORD-中文-001-1'],
      totalTasks: 1,
      completedTasks: 0,
      progress: 0,
    } as SimulationOrder
    const task = {
      id: 'task-ORD-中文-001-1',
      orderId: order.id,
      assignedRobotId: 'ctu-01',
      assignedPortLabel: '\u00e8\u00be\u0093\u00e9\u0080\u0081\u00e7\u00ba\u00bf\u00e5\u0087\u00ba\u00e5\u0085\u00a5\u00e5\u008f\u00a3 4',
      sourceGroupLabel: '1F A区库位',
      portQueuePosition: 2,
      phaseLabel: '等待调度',
      taskIndex: 1,
      lines: [{ nodeId: 'rack-a', cellId: 'cell-r1-c1', layer: 1, skuId: 'sku-1', skuName: '测试商品' }],
    } as SimulationTask

    const { unmount } = render(<OrderOrchestrationPanel order={order} tasks={[task]} />)

    const taskCard = screen.getAllByTitle(task.id).find((element) => element.tagName === 'BUTTON')
    expect(taskCard).toBeDefined()
    expect(taskCard).toHaveClass('simulation-order-task-card')
    expect(screen.getByText('输送线出入口 4')).toHaveClass('simulation-order-task-port')
    expect(screen.getByText('ctu-01')).toHaveClass('simulation-order-task-robot')
    expect(screen.getByText(/1F A区库位 · 出口队列 2/)).toHaveAttribute('title', '1F A区库位')
    expect(screen.queryByText(task.assignedPortLabel as string)).not.toBeInTheDocument()

    unmount()
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
  })

  it('keeps an already-correct Chinese port label unchanged', () => {
    const order = {
      id: 'ORD-002', priority: 'normal', targetStation: 'station-a', createdAt: '2026-11-12T00:00:00Z', lines: [],
      status: 'waiting_dispatch', statusLabel: '等待调度', taskIds: ['task-2'], totalTasks: 1,
      completedTasks: 0, progress: 0,
    } as SimulationOrder
    const task = {
      id: 'task-2', orderId: order.id, assignedRobotId: null, assignedPortLabel: '输送线出入口 3',
      robotType: 'ctu', mode: 'direct', status: 'pending', phase: 'pending', phaseLabel: '等待分配',
      sourceCellId: 'cell-r1-c1', sourceLayer: 1, targetCellId: 'cell-r2-c2', targetLayer: 1,
      deliveryCellId: 'cell-r2-c2', lines: [], currentLineIndex: 0, picked: 0, priority: 2,
      etaTicks: 0, urgent: false, remainingTicks: 0, history: [], createdTick: 0,
      startedTick: null, completedTick: null,
    } as SimulationTask

    render(<OrderOrchestrationPanel order={order} tasks={[task]} />)
    expect(screen.getByText('输送线出入口 3')).toBeInTheDocument()
    expect(screen.getAllByText('等待分配')).toHaveLength(2)
  })
})
