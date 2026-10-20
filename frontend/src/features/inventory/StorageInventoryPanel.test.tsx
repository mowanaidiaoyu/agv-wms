import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import type { InventorySummary, StorageAccessAudit, StorageAccessCandidateList, StorageInventoryLocation } from './types'
import { StorageInventoryPanel } from './StorageInventoryPanel'

vi.mock('../../lib/api', () => ({
  api: {
    getInventorySummary: vi.fn(),
    listInventoryLocations: vi.fn(),
    getInventoryLocation: vi.fn(),
    initializeSyntheticInventory: vi.fn(),
    previewSyntheticInventorySupplement: vi.fn(),
    supplementSyntheticInventory: vi.fn(),
    configureStorageAccessMapping: vi.fn(),
  },
}))

const summary: InventorySummary = {
  topology_id: 'topology-a', storage_location_count: 3, stocked_location_count: 2,
  positive_inventory_location_count: 1, unconfigured_location_count: 1, zero_inventory_location_count: 1,
  sku_count: 1, quantity_on_hand: 8, quantity_reserved: 3, quantity_available: 5,
  data_sources: ['synthetic_demo'],
}

function location(
  id: string,
  zoneType: 'ctu' | 'shuttle',
  onHand: number | null,
): StorageInventoryLocation {
  return {
    topology_id: 'topology-a', topology_version: '2.0.0', storage_node_id: id, label: id,
    zone_id: `zone-${zoneType}`, zone_label: zoneType === 'ctu' ? 'CTU 库区' : '四向车库区', zone_type: zoneType,
    floor: '1F', row: 1, col: id === 'storage-a' ? 2 : 3, rack_level: null,
    balances: onHand === null ? [] : [{
      topology_id: 'topology-a', zone_id: `zone-${zoneType}`, storage_node_id: id, storage_layer: 1,
      sku: { sku_id: 'SKU-001', sku_name: '标准紧固件', category: 'fastener', specification: 'M6', unit: 'box', weight_kg: 1, material: 'steel', source: 'synthetic_demo' },
      quantity_on_hand: onHand, quantity_reserved: onHand ? 3 : 0, quantity_available: onHand ? onHand - 3 : 0,
      source: 'synthetic_demo', updated_at: '2026-10-20T00:00:00Z',
    }],
    access_mapping: {
      topology_id: 'topology-a', storage_node_id: id,
      access_cell_id: id === 'storage-a' ? 'cell-r1-c1' : null,
      status: id === 'storage-a' ? 'configured' : 'unconfigured', source: id === 'storage-a' ? 'manual_verified' : null,
      validated_at: id === 'storage-a' ? '2026-10-20T00:00:00Z' : null,
    },
    data_sources: onHand === null ? [] : ['synthetic_demo'],
  }
}

const positive = location('storage-a', 'ctu', 8)
const zero = location('storage-zero', 'ctu', 0)
const empty = location('storage-empty', 'shuttle', null)

function renderPanel(
  selectedStorageNodeId: string | null = null,
  access?: { audit: StorageAccessAudit; candidates: StorageAccessCandidateList; selectedAccessCellId: string },
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const onSelectStorageNode = vi.fn()
  const onLocateStorageNode = vi.fn()
  const onLocateAccessCell = vi.fn()
  const view = render(
    <QueryClientProvider client={queryClient}>
      <StorageInventoryPanel
        topologyId="topology-a"
        selectedStorageNodeId={selectedStorageNodeId}
        onSelectStorageNode={onSelectStorageNode}
        onLocateStorageNode={onLocateStorageNode}
        onLocateAccessCell={onLocateAccessCell}
        accessAudit={access?.audit}
        accessCandidates={access?.candidates}
        selectedAccessCellId={access?.selectedAccessCellId}
        onSelectAccessCandidate={vi.fn()}
      />
    </QueryClientProvider>,
  )
  return { ...view, onSelectStorageNode, onLocateStorageNode, onLocateAccessCell }
}

beforeEach(() => {
  vi.mocked(api.getInventorySummary).mockResolvedValue(summary)
  vi.mocked(api.listInventoryLocations).mockResolvedValue([positive, zero, empty])
  vi.mocked(api.getInventoryLocation).mockImplementation(async (_topologyId, nodeId) => (
    [positive, zero, empty].find((item) => item.storage_node_id === nodeId)!
  ))
  vi.mocked(api.previewSyntheticInventorySupplement).mockResolvedValue({
    topology_id: 'topology-a', eligible_location_count: 1, requested_location_count: 100,
    estimated_insert_count: 1, fill_all: false, before: summary,
  })
  vi.mocked(api.supplementSyntheticInventory).mockResolvedValue({
    topology_id: 'topology-a', seed: 42, fill_all: false, requested_location_count: 100,
    eligible_before: 1, inserted_locations: 1, inserted_balances: 1,
    skipped_existing_locations: 2, skipped_due_to_limit: 0, source: 'synthetic_demo',
    before: summary, after: { ...summary, stocked_location_count: 3, positive_inventory_location_count: 2, unconfigured_location_count: 0, quantity_on_hand: 20, quantity_available: 17 },
  })
  vi.mocked(api.configureStorageAccessMapping).mockResolvedValue({
    topology_id: 'topology-a', storage_node_id: 'storage-a', access_cell_id: 'cell-r1-c1',
    status: 'configured', source: 'manual_verified', validated_at: '2026-10-20T00:00:00Z',
  })
})

describe('StorageInventoryPanel', () => {
  it('filters real locations by SKU search and warehouse zone and locates a selected location', async () => {
    const callbacks = renderPanel()
    const list = await screen.findByRole('region', { name: '库位库存' })
    await waitFor(() => expect(within(list).getByText('storage-a')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('搜索 SKU 或库位'), { target: { value: 'SKU-001' } })
    expect(screen.queryByText('storage-empty')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('搜索 SKU 或库位'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '四向车库区' }))
    expect(screen.getByText('storage-empty')).toBeInTheDocument()
    expect(screen.queryByText('storage-a')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('storage-empty'))
    expect(callbacks.onSelectStorageNode).toHaveBeenCalledWith('storage-empty')
    expect(callbacks.onLocateStorageNode).toHaveBeenCalledWith(empty)
  })

  it('distinguishes a zero balance from a location with no inventory record', async () => {
    const { unmount } = renderPanel('storage-zero')
    expect(await screen.findByText('现有 0')).toBeInTheDocument()
    expect(screen.getByText('预留 0')).toBeInTheDocument()
    unmount()

    renderPanel('storage-empty')
    const detail = await screen.findByText('尚未配置合法 CTU 取货停靠点，不能生成取货路径。')
    expect(detail.parentElement).toHaveTextContent('暂无库存记录')
    expect(detail.parentElement).not.toHaveTextContent('现有 0')
  })

  it('previews and submits a deterministic supplement without hiding coverage statistics', async () => {
    renderPanel()
    expect(await screen.findByText(/当前可补充 1 个/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '补充演示库存' }))
    await waitFor(() => expect(api.supplementSyntheticInventory).toHaveBeenCalledWith('topology-a', 100, false, 42))
    expect(await screen.findByRole('status')).toHaveTextContent('已新增 1 个库位')
  })

  it('requires explicit review before saving a backend-eligible real road candidate', async () => {
    const audit: StorageAccessAudit = {
      topology_id: 'topology-a', topology_version: '2.0.0', storage_location_count: 3,
      eligible_location_count: 1, review_required_location_count: 0, unmappable_location_count: 2,
      configured_mapping_count: 0, invalid_mapping_count: 0, reason_counts: { NO_ADJACENT_REAL_ROAD: 2 },
    }
    const candidates: StorageAccessCandidateList = {
      topology_id: 'topology-a', topology_version: '2.0.0', storage_node_id: 'storage-a',
      eligible_count: 1, review_required_count: 0, rejected_count: 0,
      candidates: [{
        topology_id: 'topology-a', topology_version: '2.0.0', storage_node_id: 'storage-a',
        access_cell_id: 'cell-r1-c1', floor: '1F', row: 1, col: 1, zone_id: 'zone-ctu',
        distance_cells: 1, status: 'eligible', reason_codes: ['REAL_CTU_ROAD'], reasons: ['真实可通行 CTU road Cell。'],
      }],
    }
    renderPanel('storage-a', { audit, candidates, selectedAccessCellId: 'cell-r1-c1' })

    expect(await screen.findByText('可验证 1')).toBeInTheDocument()
    const save = screen.getByRole('button', { name: '审核并保存正式映射' })
    expect(save).toBeDisabled()
    fireEvent.click(screen.getByLabelText('我已核对货架取货面、道路归属和现场通行空间'))
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() => expect(api.configureStorageAccessMapping).toHaveBeenCalledWith('topology-a', 'storage-a', 'cell-r1-c1'))
  })
})
