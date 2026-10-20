import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TopologyPage } from './TopologyPage'

vi.mock('../lib/api', () => ({
  api: {
    listTopologies: vi.fn(async () => []),
    getTopology: vi.fn(async () => {
      throw new Error('unused')
    }),
    saveTopology: vi.fn(async () => ({})),
    deleteTopology: vi.fn(async () => ({})),
    validateTopology: vi.fn(async () => ({ valid: true, errors: [], warnings: [] })),
    getInventorySummary: vi.fn(async () => ({
      topology_id: 'topology-new', storage_location_count: 1, stocked_location_count: 1,
      positive_inventory_location_count: 1, unconfigured_location_count: 0, zero_inventory_location_count: 0,
      sku_count: 1, quantity_on_hand: 12, quantity_reserved: 2, quantity_available: 10,
      data_sources: ['synthetic_demo'],
    })),
    listInventoryLocations: vi.fn(async () => [{
      topology_id: 'topology-new', topology_version: '2.0.0', storage_node_id: 'storage-a',
      label: '库位 A', zone_id: 'zone-ctu', zone_label: 'CTU 库区', zone_type: 'ctu',
      floor: '1F', row: 1, col: 2, rack_level: null,
      balances: [{
        topology_id: 'topology-new', zone_id: 'zone-ctu', storage_node_id: 'storage-a',
        storage_layer: 1, quantity_on_hand: 12, quantity_reserved: 2, quantity_available: 10,
        source: 'synthetic_demo', updated_at: '2026-10-19T00:00:00Z',
        sku: { sku_id: 'SKU-DEMO-001', sku_name: '标准紧固件套装', category: 'fastener', specification: 'M6', unit: 'box', weight_kg: 2.4, material: 'steel', source: 'synthetic_demo' },
      }],
      access_mapping: { topology_id: 'topology-new', storage_node_id: 'storage-a', access_cell_id: null, status: 'unconfigured', source: null, validated_at: null },
      data_sources: ['synthetic_demo'],
    }]),
    getInventoryLocation: vi.fn(async () => ({
      topology_id: 'topology-new', topology_version: '2.0.0', storage_node_id: 'storage-a',
      label: '库位 A', zone_id: 'zone-ctu', zone_label: 'CTU 库区', zone_type: 'ctu',
      floor: '1F', row: 1, col: 2, rack_level: null,
      balances: [{
        topology_id: 'topology-new', zone_id: 'zone-ctu', storage_node_id: 'storage-a',
        storage_layer: 1, quantity_on_hand: 12, quantity_reserved: 2, quantity_available: 10,
        source: 'synthetic_demo', updated_at: '2026-10-19T00:00:00Z',
        sku: { sku_id: 'SKU-DEMO-001', sku_name: '标准紧固件套装', category: 'fastener', specification: 'M6', unit: 'box', weight_kg: 2.4, material: 'steel', source: 'synthetic_demo' },
      }],
      access_mapping: { topology_id: 'topology-new', storage_node_id: 'storage-a', access_cell_id: null, status: 'unconfigured', source: null, validated_at: null },
      data_sources: ['synthetic_demo'],
    })),
    initializeSyntheticInventory: vi.fn(async () => ({ topology_id: 'topology-new', seed: 42, inserted_skus: 0, inserted_balances: 0, source: 'synthetic_demo' })),
    previewSyntheticInventorySupplement: vi.fn(async () => ({
      topology_id: 'topology-new', eligible_location_count: 0, requested_location_count: 100,
      estimated_insert_count: 0, fill_all: false,
      before: { topology_id: 'topology-new', storage_location_count: 1, stocked_location_count: 1, positive_inventory_location_count: 1, unconfigured_location_count: 0, zero_inventory_location_count: 0, sku_count: 1, quantity_on_hand: 12, quantity_reserved: 2, quantity_available: 10, data_sources: ['synthetic_demo'] },
    })),
    supplementSyntheticInventory: vi.fn(),
  },
}))

// jsdom 无 WebGL，three/r3f 无法渲染，测试用桩替代 3D 场景
vi.mock('../components/topology/Topology3DScene', () => ({
  default: ({ onInventoryNodeSelect }: { onInventoryNodeSelect?: (id: string) => void }) => (
    <div data-testid="topology-3d">
      <button type="button" onClick={() => onInventoryNodeSelect?.('storage-a')}>选择测试库位</button>
    </div>
  ),
}))

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <TopologyPage />
    </QueryClientProvider>,
  )
}

describe('TopologyPage', () => {
  it('renders the v1 3D editor shell (no legacy iframe, no 2D board)', async () => {
    const { container } = renderPage()

    // 工具栏与画布骨架
    expect(screen.getByLabelText('拓扑名称')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /保存拓扑/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /新建/ })).toBeInTheDocument()
    // 右侧检查器选项卡
    expect(screen.getByRole('tab', { name: '属性' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '校验' })).toBeInTheDocument()

    // 左侧对象调色板分组标题
    expect(screen.getByText('节点')).toBeInTheDocument()
    expect(screen.getByText('区域（拉矩形）')).toBeInTheDocument()

    // 右侧属性面板默认空态
    expect(screen.getByText('未选择对象')).toBeInTheDocument()

    // 不再渲染旧 iframe
    expect(container.querySelector('iframe')).toBeNull()
    expect(screen.queryByTitle('Legacy topology editor')).not.toBeInTheDocument()

    // 画布是 3D 场景，而非 2D 棋盘（无「适配 / 100%」控件）
    expect(await screen.findByTestId('topology-3d')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /适配全图|适配/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /100%/ })).not.toBeInTheDocument()
  })

  it('opens inventory mode and shows the location selected from the 3D scene', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('tab', { name: '库存查看' }))
    expect(await screen.findByText(/数据来源：确定性演示数据/)).toBeInTheDocument()

    await user.click(await screen.findByRole('button', { name: '选择测试库位' }))
    expect(await screen.findByText('标准紧固件套装')).toBeInTheDocument()
    expect(screen.getByText('尚未配置合法 CTU 取货停靠点，不能生成取货路径。')).toBeInTheDocument()
  })
})
