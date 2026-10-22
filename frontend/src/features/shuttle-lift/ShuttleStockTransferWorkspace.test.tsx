import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { StoredRecord, TopologyPayload } from '../../types'
import { api } from '../../lib/api'
import { PathDemoModeSelect } from '../path-demo/PathDemoModeSelect'
import { ShuttleDemoWorkspace } from './ShuttleDemoWorkspace'
import { ShuttleStockTransferWorkspace } from './ShuttleStockTransferWorkspace'
import type {
  ShuttleDemoLocationInventory,
  ShuttlePlanResponse,
  ShuttleTrackNode,
  ShuttleTransferTask,
} from './types'

const playback = vi.hoisted(() => ({
  state: { status: 'idle' as const, elapsedSeconds: 0, playbackRate: 1 as const },
  start: vi.fn(), pause: vi.fn(), resume: vi.fn(), reset: vi.fn(), setRate: vi.fn(),
}))

vi.mock('../../lib/api', () => ({ api: {
  getShuttleTracks: vi.fn(), getShuttleLifts: vi.fn(), listShuttleDemoSkus: vi.fn(),
  listShuttleDemoInventoryLocations: vi.fn(), initializeShuttleDemoInventory: vi.fn(),
  createShuttleDemoTransfer: vi.fn(), planStartShuttleDemoTransfer: vi.fn(),
  advanceShuttleDemoTransfer: vi.fn(), cancelShuttleDemoTransfer: vi.fn(),
} }))
vi.mock('../path-demo/pathPlayback', () => ({ usePathPlayback: () => playback }))
vi.mock('../path-demo/PathPlaybackController', () => ({
  PathPlaybackController: ({ canPlay }: { canPlay: boolean }) => <div data-testid="playback" data-can-play={String(canPlay)} />,
}))
vi.mock('../path-demo/PathDemo3DScene', () => ({
  PathDemo3DScene: ({ shuttleVisualization, cameraActionRequest, shuttleRackXray }: any) => <div
    data-testid="transfer-scene"
    data-transfer-status={shuttleVisualization?.transfer?.status ?? ''}
    data-transfer-load={shuttleVisualization?.transfer?.vehicleLoadQuantity ?? ''}
    data-plan={shuttleVisualization?.plan?.result_status ?? ''}
    data-camera-action={cameraActionRequest?.type ?? ''}
    data-camera-target={cameraActionRequest?.targetWorld?.join(',') ?? ''}
    data-xray={String(shuttleRackXray)}
  />,
}))

const topology: TopologyPayload = {
  id: 'topology-666', name: '666', meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 216, cols: 129, cellSize: 32, originX: 0, originY: 0 }, zones: [], cells: [], nodes: [], resources: [],
}
const record: StoredRecord<TopologyPayload> = { id: topology.id, name: topology.name, payload: topology }
const otherRecord: StoredRecord<TopologyPayload> = { ...record, id: 'topology-other', name: '其他拓扑', payload: { ...topology, id: 'topology-other', name: '其他拓扑' } }
const node = (level: 1 | 2 | 3 | 4, col: number): ShuttleTrackNode => ({
  topology_id: topology.id, zone_id: 'zone-shuttle', track_node_id: `L${level}-r100-c${col}`,
  shuttle_level: level, row: 100, col, world_x: col, world_y: level, world_z: 7,
  track_type: 'straight', traversable: true, neighbors: [],
})
const tracks = [node(1, 7), node(1, 9), node(4, 49), node(4, 51)]
const balance = (quantity: number) => ({
  sku_id: 'SKU-001', sku_name: '演示商品 001', unit: '箱', quantity_on_hand: quantity,
  quantity_reserved: 0, quantity_available: quantity,
})
const source: ShuttleDemoLocationInventory = {
  topology_id: topology.id, location_id: 'shuttle-location:zone:L1:r100:c11', zone_id: 'zone-shuttle',
  level: 1, row: 100, col: 11, world_x: 11, world_y: 1, world_z: 7, transfer_eligible: true,
  access_track_node_id: 'L1-r100-c9', eligibility_reason_codes: ['real_shuttle_track'],
  mapping_source: 'synthetic_demo_adjacency_assumption', physical_review_status: 'unverified',
  balances: [balance(40)], data_source: 'synthetic_demo',
}
const destination: ShuttleDemoLocationInventory = {
  ...source, location_id: 'shuttle-location:zone:L4:r100:c53', level: 4, row: 100, col: 53,
  world_x: 53, world_y: 4, access_track_node_id: 'L4-r100-c51', balances: [balance(5)],
}
const plan: ShuttlePlanResponse = {
  topology_id: topology.id, topology_revision: '2.0.0:hash', config_revision: 'shuttle-4l-lift-v2',
  coordinate_contract_revision: 'shuttle-internal-levels-v1', algorithm: 'astar', start: tracks[0], destination: tracks[3],
  chosen_lift: 'shuttle-lift-a',
  path_nodes: [{ ...tracks[0], arrival_seconds: 0, phase: 'start' }, { ...tracks[3], arrival_seconds: 12, phase: 'track' }],
  path_edges: [{ from_track_node: tracks[0].track_node_id, to_track_node: tracks[3].track_node_id, edge_type: 'track', lift_id: null, duration_seconds: 12, start_seconds: 0, motion_start_seconds: 0, motion_end_seconds: 12, end_seconds: 12, distance_cells: 12, turn_time_seconds: 0 }],
  lift_transitions: [], horizontal_distance_cells: 12, estimated_duration_seconds: 12, visited_states: 4,
  planning_time_ms: 1, result_status: 'success', failure_reason: null, timing_source: 'synthetic_demo', cost_contract: { time_unit: 'seconds' },
}
const makeTask = (status: ShuttleTransferTask['status'] = 'reserved'): ShuttleTransferTask => ({
  task_id: 'transfer-1', topology_id: topology.id, topology_revision: '2.0.0:hash', shuttle_config_revision: 'shuttle-4l-lift-v2',
  coordinate_contract_revision: 'shuttle-internal-levels-v1', vehicle_id: 'SHUTTLE-01', current_track_node_id: tracks[0].track_node_id,
  source_location_id: source.location_id, source_access_track_node_id: source.access_track_node_id!,
  destination_location_id: destination.location_id, destination_access_track_node_id: destination.access_track_node_id!,
  sku_id: 'SKU-001', quantity: 3, vehicle_capacity_units: 100,
  vehicle_load_quantity: status === 'picked' || status === 'moving_to_dropoff' ? 3 : 0,
  algorithm: 'astar', status, plan: status === 'reserved' ? null : plan, pickup_at_seconds: status === 'reserved' ? null : 5,
  dropoff_at_seconds: status === 'reserved' ? null : 12, simulation_elapsed_seconds: 0,
  pick_event_id: 'transfer-1:pick', drop_event_id: 'transfer-1:drop', failure_reason: null, data_source: 'synthetic_demo',
  source_inventory: { ...balance(40), quantity_reserved: status === 'reserved' ? 3 : 0, quantity_available: status === 'reserved' ? 37 : 40 },
  destination_inventory: balance(5), ledger: [], created_at: '2026-10-22T00:00:00Z', updated_at: '2026-10-22T00:00:00Z',
})

function renderWorkspace(options: { onTopologyChange?: ReturnType<typeof vi.fn>; onWorkspaceModeChange?: ReturnType<typeof vi.fn> } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const onTopologyChange = options.onTopologyChange ?? vi.fn()
  const onWorkspaceModeChange = options.onWorkspaceModeChange ?? vi.fn()
  render(<QueryClientProvider client={client}><ShuttleStockTransferWorkspace
    topologyRecord={record} topologies={[record, otherRecord]}
    onTopologyChange={onTopologyChange} onWorkspaceModeChange={onWorkspaceModeChange}
  /></QueryClientProvider>)
  return { onTopologyChange, onWorkspaceModeChange }
}

async function waitForReady() {
  await waitFor(() => {
    expect(screen.getByLabelText('起点货位')).toHaveValue(source.location_id)
    expect(screen.getByLabelText('目标货位')).toHaveValue(destination.location_id)
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  playback.state = { status: 'idle', elapsedSeconds: 0, playbackRate: 1 }
  vi.mocked(api.getShuttleTracks).mockImplementation(async (_id, level) => ({
    topology_id: topology.id, topology_revision: '2.0.0:hash', config_revision: 'shuttle-4l-lift-v2',
    coordinate_contract_revision: 'shuttle-internal-levels-v1', nodes: tracks.filter((item) => item.shuttle_level === level),
  }))
  vi.mocked(api.getShuttleLifts).mockResolvedValue({
    topology_id: topology.id, topology_revision: '2.0.0:hash', config_revision: 'shuttle-4l-lift-v2',
    coordinate_contract_revision: 'shuttle-internal-levels-v1', planning_enabled: true, lifts: [], warnings: [],
  })
  vi.mocked(api.listShuttleDemoSkus).mockResolvedValue([{ ...balance(45), stocked_location_count: 2 }])
  vi.mocked(api.listShuttleDemoInventoryLocations).mockImplementation(async (_id, options) => {
    const query = options ?? {}
    return {
      topology_id: topology.id, total: query.skuId ? 1 : 2, offset: 0, limit: query.limit ?? 1000,
      locations: query.skuId ? [source] : [source, destination],
    }
  })
  vi.mocked(api.initializeShuttleDemoInventory).mockResolvedValue({
    topology_id: topology.id, topology_revision: '2.0.0:hash', seed: 'shuttle-stock-demo-v1',
    candidate_location_count: 4238, eligible_location_count: 2204, inserted_sku_count: 8,
    inserted_balance_count: 8456, existing_balance_count: 0, data_source: 'synthetic_demo',
  })
  vi.mocked(api.createShuttleDemoTransfer).mockResolvedValue(makeTask('reserved'))
  vi.mocked(api.planStartShuttleDemoTransfer).mockResolvedValue(makeTask('moving_to_pickup'))
  vi.mocked(api.cancelShuttleDemoTransfer).mockResolvedValue(makeTask('cancelled'))
  vi.mocked(api.advanceShuttleDemoTransfer).mockResolvedValue(makeTask('delivered'))
})

describe('ShuttleStockTransferWorkspace', () => {
  it('exposes SKU transfer as a fourth Shuttle mode', () => {
    const onChange = vi.fn()
    render(<PathDemoModeSelect value="shuttle_single" onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('演示模式'), { target: { value: 'shuttle_stock_transfer' } })
    expect(onChange).toHaveBeenCalledWith('shuttle_stock_transfer')
    expect(screen.getByRole('option', { name: 'SKU 货物搬运（单车演示）' })).toBeInTheDocument()
  })

  it('routes the selected transfer mode through the existing Shuttle workspace', async () => {
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ShuttleDemoWorkspace
        topologyRecord={record} topologies={[record]} onTopologyChange={vi.fn()}
        onWorkspaceModeChange={vi.fn()} demoMode="shuttle_stock_transfer" onDemoModeChange={vi.fn()}
      />
    </QueryClientProvider>)
    expect(await screen.findByText('SKU 货物搬运')).toBeInTheDocument()
  })

  it('renders the dedicated synthetic demo workspace', async () => {
    renderWorkspace()
    expect(await screen.findByText('SKU 货物搬运')).toBeInTheDocument()
    expect(screen.getByText('synthetic_demo · 离线逻辑货物搬运')).toBeInTheDocument()
  })

  it('loads all four levels of real ShuttleGraph nodes', async () => {
    renderWorkspace()
    await waitForReady()
    expect(api.getShuttleTracks).toHaveBeenCalledTimes(4)
    expect(api.getShuttleTracks).toHaveBeenCalledWith(topology.id, 4)
  })

  it('shows only positive-stock locations as sources', async () => {
    renderWorkspace()
    await waitForReady()
    expect(api.listShuttleDemoInventoryLocations).toHaveBeenCalledWith(topology.id, expect.objectContaining({ skuId: 'SKU-001', transferEligible: true }))
  })

  it('allows a destination without pre-existing stock for the selected SKU', async () => {
    vi.mocked(api.listShuttleDemoInventoryLocations).mockImplementation(async (_id, options) => {
      const query = options ?? {}
      return {
        topology_id: topology.id, total: query.skuId ? 1 : 2, offset: 0, limit: 5000,
        locations: query.skuId ? [source] : [source, { ...destination, balances: [] }],
      }
    })
    renderWorkspace()
    await waitForReady()
    expect(screen.getByRole('option', { name: `${destination.location_id} · L4 · 当前 0 · 可模拟搬运` })).toBeInTheDocument()
  })

  it('searches SKU by id or product name through the backend contract', async () => {
    renderWorkspace()
    await screen.findByLabelText('搜索 SKU')
    fireEvent.change(screen.getByLabelText('搜索 SKU'), { target: { value: '演示商品' } })
    await waitFor(() => expect(api.listShuttleDemoSkus).toHaveBeenCalledWith(topology.id, '演示商品'))
  })

  it('initializes the deterministic inventory explicitly', async () => {
    renderWorkspace()
    fireEvent.click(await screen.findByRole('button', { name: '初始化模拟库存' }))
    await waitFor(() => expect(api.initializeShuttleDemoInventory).toHaveBeenCalledWith(topology.id))
  })

  it('reports an inventory initialization failure', async () => {
    vi.mocked(api.initializeShuttleDemoInventory).mockRejectedValueOnce(new Error('初始化失败'))
    renderWorkspace()
    fireEvent.click(await screen.findByRole('button', { name: '初始化模拟库存' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('初始化失败')
  })

  it('shows an explicit empty-inventory prompt', async () => {
    vi.mocked(api.listShuttleDemoSkus).mockResolvedValueOnce([])
    renderWorkspace()
    expect(await screen.findByText('尚无 Shuttle 模拟库存，请先初始化。')).toBeInTheDocument()
  })

  it('applies the selected level to source and destination queries', async () => {
    renderWorkspace()
    await waitForReady()
    fireEvent.change(screen.getByLabelText('货位楼层筛选'), { target: { value: '4' } })
    await waitFor(() => expect(api.listShuttleDemoInventoryLocations).toHaveBeenCalledWith(topology.id, expect.objectContaining({ level: 4 })))
  })

  it('rejects a zero transfer quantity before requesting a task', async () => {
    renderWorkspace()
    await waitForReady()
    fireEvent.change(screen.getByLabelText('搬运数量'), { target: { value: '0' } })
    expect(screen.getByRole('button', { name: '规划并开始模拟搬运' })).toBeDisabled()
  })

  it('shows insufficient available inventory beside the inputs', async () => {
    renderWorkspace()
    await waitForReady()
    fireEvent.change(screen.getByLabelText('搬运数量'), { target: { value: '41' } })
    expect(screen.getByRole('alert')).toHaveTextContent('当前可用 40')
  })

  it('enforces the explicit 100-unit demo capacity', async () => {
    renderWorkspace()
    await waitForReady()
    fireEvent.change(screen.getByLabelText('搬运数量'), { target: { value: '101' } })
    expect(screen.getByRole('button', { name: '规划并开始模拟搬运' })).toBeDisabled()
  })

  it('creates and starts one task with the exact selected inventory identities', async () => {
    renderWorkspace()
    await waitForReady()
    fireEvent.change(screen.getByLabelText('搬运数量'), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: '规划并开始模拟搬运' }))
    await waitFor(() => expect(api.createShuttleDemoTransfer).toHaveBeenCalledWith(expect.objectContaining({
      topology_id: topology.id, source_location_id: source.location_id, destination_location_id: destination.location_id,
      sku_id: 'SKU-001', quantity: 3, vehicle_id: 'SHUTTLE-01',
    })))
    expect(api.planStartShuttleDemoTransfer).toHaveBeenCalledWith('transfer-1')
  })

  it('passes Dijkstra without creating a second path implementation', async () => {
    renderWorkspace()
    await waitForReady()
    fireEvent.change(screen.getByLabelText('搬运路径算法'), { target: { value: 'dijkstra' } })
    fireEvent.click(screen.getByRole('button', { name: '规划并开始模拟搬运' }))
    await waitFor(() => expect(api.createShuttleDemoTransfer).toHaveBeenCalledWith(expect.objectContaining({ algorithm: 'dijkstra' })))
  })

  it('does not render a playable route when task creation fails', async () => {
    vi.mocked(api.createShuttleDemoTransfer).mockRejectedValueOnce(new Error('货位不可执行'))
    renderWorkspace()
    await waitForReady()
    fireEvent.click(screen.getByRole('button', { name: '规划并开始模拟搬运' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('货位不可执行')
    expect(screen.getByTestId('playback')).toHaveAttribute('data-can-play', 'false')
  })

  it('renders the backend plan and live task status in the existing 3D scene', async () => {
    renderWorkspace()
    await waitForReady()
    fireEvent.click(screen.getByRole('button', { name: '规划并开始模拟搬运' }))
    await waitFor(() => expect(screen.getByTestId('transfer-scene')).toHaveAttribute('data-plan', 'success'))
    expect(screen.getByTestId('transfer-scene')).toHaveAttribute('data-transfer-status', 'moving_to_pickup')
  })

  it('shows vehicle load and conservation values from the task snapshot', async () => {
    vi.mocked(api.planStartShuttleDemoTransfer).mockResolvedValueOnce(makeTask('picked'))
    renderWorkspace()
    await waitForReady()
    fireEvent.click(screen.getByRole('button', { name: '规划并开始模拟搬运' }))
    await waitFor(() => expect(screen.getByText('3 件')).toBeInTheDocument())
    expect(screen.getByTestId('transfer-scene')).toHaveAttribute('data-transfer-load', '3')
    expect(screen.getByText('45 → 48')).toBeInTheDocument()
  })

  it('cancels only the active transfer task and pauses playback', async () => {
    renderWorkspace()
    await waitForReady()
    fireEvent.click(screen.getByRole('button', { name: '规划并开始模拟搬运' }))
    fireEvent.click(await screen.findByRole('button', { name: '取消当前任务' }))
    await waitFor(() => expect(api.cancelShuttleDemoTransfer).toHaveBeenCalledWith('transfer-1'))
    expect(playback.pause).toHaveBeenCalled()
  })

  it('collapses and restores the configuration without losing the workspace', async () => {
    renderWorkspace()
    await screen.findByLabelText('搬运 SKU')
    fireEvent.click(screen.getByRole('button', { name: '收起搬运配置' }))
    expect(screen.queryByLabelText('搬运 SKU')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开搬运配置' }))
    expect(screen.getByLabelText('搬运 SKU')).toBeInTheDocument()
  })

  it('collapses and restores the sidebar without leaving an empty control', async () => {
    renderWorkspace()
    fireEvent.click(await screen.findByRole('button', { name: '收起侧栏' }))
    fireEvent.click(screen.getByRole('button', { name: '展开配置侧栏' }))
    expect(screen.getByText('SKU 货物搬运')).toBeInTheDocument()
  })

  it('focuses the exact source world coordinate', async () => {
    renderWorkspace()
    await waitForReady()
    fireEvent.click(screen.getByRole('button', { name: '查看取货位' }))
    expect(screen.getByTestId('transfer-scene')).toHaveAttribute('data-camera-action', 'focus-point')
    expect(screen.getByTestId('transfer-scene')).toHaveAttribute('data-camera-target', '11,1,7')
  })

  it('focuses the exact destination world coordinate', async () => {
    renderWorkspace()
    await waitForReady()
    fireEvent.click(screen.getByRole('button', { name: '查看目标货位' }))
    expect(screen.getByTestId('transfer-scene')).toHaveAttribute('data-camera-target', '53,4,7')
  })

  it('toggles visual-only rack xray without changing inventory', async () => {
    renderWorkspace()
    await waitForReady()
    fireEvent.click(screen.getByRole('button', { name: '透视货架：关' }))
    expect(screen.getByTestId('transfer-scene')).toHaveAttribute('data-xray', 'true')
    expect(api.createShuttleDemoTransfer).not.toHaveBeenCalled()
  })

  it('keeps advanced storage modeling available as a separate workspace', async () => {
    const { onWorkspaceModeChange } = renderWorkspace()
    fireEvent.click(await screen.findByRole('button', { name: '打开高级建模与审核' }))
    expect(onWorkspaceModeChange).toHaveBeenCalledWith('inventory')
  })

  it('changes topology through the parent-owned selection state', async () => {
    const { onTopologyChange } = renderWorkspace()
    fireEvent.change(await screen.findByLabelText('拓扑'), { target: { value: otherRecord.id } })
    expect(onTopologyChange).toHaveBeenCalledWith(otherRecord.id)
  })
})
