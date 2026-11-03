import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CrossLevelTransferPanel } from './CrossLevelTransferPanel'
import type { ShuttleContract, SimulationState } from '../../types'

vi.mock('../../lib/api', () => ({
  api: {
    planShuttleTransfer: vi.fn(),
    retryShuttleTransfer: vi.fn(),
    cancelShuttleTransfer: vi.fn(),
  },
}))

const contract: ShuttleContract = {
  topologyId: 'topology-6ceb4920',
  topologyRevision: '2.0.0:7fdde35b3ef6f522',
  configRevision: 'shuttle-4l-lift-pyc-v1',
  coordinateContractRevision: 'shuttle-internal-levels-v1',
  verificationStatus: 'program_verified_pending_human',
  planningEnabled: true,
  warnings: [],
  lifts: [
    { lift_id: 'shuttle-lift-a', label: 'Lift-A', row: 100, col: 19, verification_status: 'program_verified_pending_human', timing_source: 'synthetic_demo' },
    { lift_id: 'shuttle-lift-b', label: 'Lift-B', row: 188, col: 39, verification_status: 'program_verified_pending_human', timing_source: 'synthetic_demo' },
  ],
  tracks: [
    { topology_id: 'topology-6ceb4920', zone_id: 'zone-shuttle-3', track_node_id: 'L1-r100-c7', shuttle_level: 1, row: 100, col: 7, world_x: -56.5, world_y: 0.35, world_z: 7, track_type: 'straight', traversable: true, neighbors: [] },
    { topology_id: 'topology-6ceb4920', zone_id: 'zone-shuttle-3', track_node_id: 'L4-r100-c51', shuttle_level: 4, row: 100, col: 51, world_x: -12.5, world_y: 4.55, world_z: 7, track_type: 'straight', traversable: true, neighbors: [] },
  ],
}

const simulation = {
  id: 'run-test', status: 'paused', tick: 3, topologyId: 'topology-6ceb4920',
  robots: [], orders: [], inventory: [], alerts: [], queues: { conveyor: [], station: [], buffer: [] },
  createdAt: '', updatedAt: '',
  tasks: [{
    id: 'transfer-1', orderId: null, robotType: 'shuttle', mode: 'shuttle_transfer', status: 'assigned',
    phase: 'lift_moving', phaseLabel: 'Lift-A 升降中', sourceCellId: 'cell-r100-c7', sourceLayer: 1,
    targetCellId: 'cell-r100-c51', targetLayer: 4, deliveryCellId: 'cell-r100-c51', lines: [],
    currentLineIndex: 0, picked: 2, priority: 1, etaTicks: 4, urgent: false,
    assignedRobotId: 'SHUTTLE-UNIFIED-01', remainingTicks: 0, history: [], createdTick: 0,
    startedTick: 1, completedTick: null, skuId: 'SKU-001', quantity: 2, inTransitQuantity: 2,
    plan: { topologyId: 'topology-6ceb4920', topologyRevision: '', configRevision: '', coordinateContractRevision: '', algorithm: 'astar', chosen_lift: 'shuttle-lift-a', path_nodes: [], path_edges: [], lift_transitions: [], estimated_duration_seconds: 10, result_status: 'success', timing_source: 'synthetic_demo' },
  }],
  transferInventory: [
    { locationId: 'SIM-BIN-L1', trackNodeId: 'L1-r100-c7', level: 1, row: 100, col: 7, worldX: -56.5, worldY: 0.35, worldZ: 7, skuId: 'SKU-001', skuName: 'SKU-001', quantityOnHand: 8, quantityReserved: 0, quantityAvailable: 8, dataSource: 'run_isolated_simulation_snapshot' },
    { locationId: 'SIM-BIN-L4', trackNodeId: 'L4-r100-c51', level: 4, row: 100, col: 51, worldX: -12.5, worldY: 4.55, worldZ: 7, skuId: 'SKU-001', skuName: 'SKU-001', quantityOnHand: 5, quantityReserved: 0, quantityAvailable: 5, dataSource: 'run_isolated_simulation_snapshot' },
  ],
  transferEvents: [],
} satisfies SimulationState

describe('CrossLevelTransferPanel', () => {
  it('shows runtime stage and does not duplicate the inventory details panel', () => {
    render(
      <CrossLevelTransferPanel
        topologyId="topology-6ceb4920"
        contract={contract}
        pendingTransfers={[]}
        simulation={simulation}
        selectedRunId="run-test"
        onAdd={vi.fn()}
        onRemove={vi.fn()}
        onFocusVehicle={vi.fn()}
        onRuntimeChanged={vi.fn(async () => undefined)}
      />,
    )

    expect(screen.getByText('Lift-A 升降中')).toBeInTheDocument()
    expect(screen.getByText('升降')).toHaveClass('active')
    expect(screen.queryByText('库存快照')).not.toBeInTheDocument()
  })
})
