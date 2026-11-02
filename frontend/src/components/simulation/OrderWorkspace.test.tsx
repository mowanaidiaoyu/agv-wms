import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { FulfillmentTestScenarioAudit, GoodsCatalogSku, SimulationInventoryProfile } from '../../types'
import { OrderComposer } from './OrderWorkspace'

const catalog: GoodsCatalogSku[] = [
  { skuId: 'SKU-001', name: '演示商品一', spec: 'BOX-A', unitsPerCase: 12, packagingSource: 'verified-test', verificationStatus: 'verified' },
  { skuId: 'SKU-002', name: '演示商品二', spec: 'BOX-B', unitsPerCase: 20, packagingSource: 'verified-test', verificationStatus: 'verified' },
]

const testScenario = {
  scenarioId: 'right-left-110-case-test',
  status: 'PASS',
  dataSource: 'simulation_test',
  rightHandoff: {
    formalRouteStatus: 'BLOCKED',
    simulationRouteStatus: 'PASS_SIMULATION_ONLY',
    cargo: Array.from({ length: 5 }, (_, index) => ({ cargoId: `cargo-${index + 1}` })),
  },
} as unknown as FulfillmentTestScenarioAudit

describe('OrderComposer', () => {
  it('adds the selected SKU, updates totals, and submits it to the queue callback', () => {
    const onSubmit = vi.fn()
    render(<OrderComposer open catalog={catalog} onClose={vi.fn()} onSubmit={onSubmit} />)

    fireEvent.change(screen.getByRole('combobox', { name: '商品' }), { target: { value: 'SKU-002' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '订单基础件数' }), { target: { value: '110' } })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    // 「演示商品二」同时出现在下拉选项和新增明细行里，这里只断言明细行本身。
    expect(screen.getByText('演示商品二', { selector: 'b' })).toBeInTheDocument()
    expect(screen.getByText('110 件 · 20 件/箱')).toBeInTheDocument()
    expect(screen.getByText(/1 种SKU · 110 件/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '提交订单' }))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      priority: 'normal',
      dispatchMode: 'balanced',
      maxParallelCtu: 7,
      lines: [expect.objectContaining({ skuId: 'SKU-002', quantityBase: 110, unitsPerCase: 20 })],
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

  it('rejects base-unit splitting when the selected SKU has no verified packaging', () => {
    render(
      <OrderComposer
        open
        catalog={[{ skuId: 'SKU-NO-PACK', name: '未维护包装商品' }]}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    )
    expect(screen.getByText('包装规格：未验证，禁止自动拆分')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '添加' }))
    expect(screen.getByRole('alert')).toHaveTextContent('缺少经过验证的每箱件数')
  })

  it('does not expose simulation-test packaging as a formal order specification', () => {
    render(
      <OrderComposer
        open
        catalog={[{
          skuId: 'SKU-TEST-ONLY', name: '测试场景商品', unitsPerCase: 20,
          packagingSource: 'scenario-contract', verificationStatus: 'simulation_test_confirmed',
          packagingScope: 'simulation_test',
        }]}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    )
    expect(screen.getByText('包装规格：未验证，禁止自动拆分')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '添加' }))
    expect(screen.getByRole('alert')).toHaveTextContent('缺少经过验证的每箱件数')
  })

  it('selects and displays the isolated 110-unit simulation-test contract', () => {
    const onSelectTestScenario = vi.fn()
    const { rerender } = render(
      <OrderComposer
        open
        catalog={catalog}
        onSelectTestScenario={onSelectTestScenario}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /110件订单仿真测试/ }))
    expect(onSelectTestScenario).toHaveBeenCalledOnce()

    rerender(
      <OrderComposer
        open
        catalog={catalog}
        testScenario={testScenario}
        onSelectTestScenario={onSelectTestScenario}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    )
    const card = screen.getByLabelText('110件订单仿真测试场景')
    expect(card).toHaveTextContent('右侧5箱 / 100件')
    expect(card).toHaveTextContent('左侧1箱 / 拆零10件 / 回库10件')
    expect(card).toHaveTextContent('simulation_test')
    expect(card).toHaveTextContent('BLOCKED')
    expect(card).toHaveTextContent('5 个独立 cargoId')
  })

  it('shows editable run-local simulation inventory and accepts explicit simulation packaging', () => {
    const onInventoryOverride = vi.fn()
    const profile = {
      profileId: 'general-order-simulation',
      name: '通用订单仿真库存',
      dataSource: 'simulation',
      isolationMode: 'run_local_snapshot',
      inventory: [{
        inventoryRecordId: 'sim-right-sku',
        sourceWarehouse: 'right',
        sourceLocationId: 'SIM-BIN-L2-r96-c31',
        locationType: 'shuttle_storage',
        skuId: 'SKU-SIM',
        quantityCases: 8,
        dataSource: 'simulation',
        verificationStatus: 'scenario_verified',
        topologyMapping: {},
      }],
      packaging: [],
    } as unknown as SimulationInventoryProfile
    render(
      <OrderComposer
        open
        catalog={[{
          skuId: 'SKU-SIM', name: '仿真 SKU', unitsPerCase: 6,
          packagingSource: 'explicit-simulation-config', verificationStatus: 'simulation_configured',
          packagingScope: 'simulation',
        }]}
        simulationProfile={profile}
        inventoryOverrides={[{ inventoryRecordId: 'sim-right-sku', quantityCases: 8 }]}
        onInventoryOverride={onInventoryOverride}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    )
    expect(screen.getByText('通用订单仿真配置')).toBeInTheDocument()
    expect(screen.getByText(/Run-local snapshot/)).toBeInTheDocument()
    const input = screen.getByRole('spinbutton', { name: 'sim-right-sku 仿真库存箱数' })
    fireEvent.change(input, { target: { value: '3' } })
    expect(onInventoryOverride).toHaveBeenCalledWith('sim-right-sku', 3)
    fireEvent.click(screen.getByRole('button', { name: /\u6dfb\u52a0/ }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
