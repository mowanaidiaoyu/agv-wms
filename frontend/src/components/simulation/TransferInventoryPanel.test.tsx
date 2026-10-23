import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { SimulationState } from '../../types'
import { TransferInventoryPanel } from './TransferInventoryPanel'

const simulation = {
  id: 'run-transfer', status: 'running', tick: 12, topologyId: 'topology-6ceb4920',
  robots: [], orders: [], inventory: [], alerts: [], queues: { conveyor: [], station: [], buffer: [] },
  createdAt: '', updatedAt: '',
  tasks: [{
    id: 'transfer-1', orderId: null, robotType: 'shuttle', mode: 'shuttle_transfer', status: 'assigned',
    phase: 'track', phaseLabel: '四向车沿规划轨道行驶', sourceCellId: 'cell-r100-c7', sourceLayer: 1,
    targetCellId: 'cell-r100-c51', targetLayer: 4, deliveryCellId: 'cell-r100-c51', lines: [],
    currentLineIndex: 0, picked: 2, priority: 1, etaTicks: 4, urgent: false,
    assignedRobotId: 'SHUTTLE-UNIFIED-01', remainingTicks: 0, history: [], createdTick: 0,
    startedTick: 1, completedTick: null, skuId: 'SKU-001', quantity: 2, inTransitQuantity: 2,
    sourceLocationId: 'SIM-BIN-L1', destinationLocationId: 'SIM-BIN-L4',
  }],
  transferInventory: [
    { locationId: 'SIM-BIN-L1', trackNodeId: 'L1-r100-c7', level: 1, row: 100, col: 7, worldX: -56.5, worldY: 0.35, worldZ: 7, skuId: 'SKU-001', skuName: 'SKU-001', quantityOnHand: 8, quantityReserved: 0, quantityAvailable: 8, dataSource: 'run_isolated_simulation_snapshot' },
    { locationId: 'SIM-BIN-L4', trackNodeId: 'L4-r100-c51', level: 4, row: 100, col: 51, worldX: -12.5, worldY: 4.55, worldZ: 7, skuId: 'SKU-001', skuName: 'SKU-001', quantityOnHand: 5, quantityReserved: 0, quantityAvailable: 5, dataSource: 'run_isolated_simulation_snapshot' },
  ],
  transferEvents: [{
    eventId: 'transfer-1:pick', taskId: 'transfer-1', tick: 2, eventType: 'pick', locationId: 'SIM-BIN-L1',
    skuId: 'SKU-001', quantity: 2, onHandDelta: -2, reservedDelta: -2, inTransitDelta: 2,
    quantityOnHandAfter: 8, quantityReservedAfter: 0, inTransitAfter: 2,
  }],
} satisfies SimulationState

describe('TransferInventoryPanel', () => {
  it('renders the run snapshot, in-transit quantity, conserved total, and event ledger', () => {
    render(<TransferInventoryPanel simulation={simulation} />)

    expect(screen.getByText('源库位 · L1')).toBeInTheDocument()
    expect(screen.getByText('目标库位 · L4')).toBeInTheDocument()
    expect(screen.getByText('现存 8')).toBeInTheDocument()
    expect(screen.getByText('现存 5')).toBeInTheDocument()
    expect(screen.getByText('transfer-1')).toBeInTheDocument()
    expect(screen.getByText('pick · 2 箱')).toBeInTheDocument()
    expect(screen.getByText('15')).toBeInTheDocument()
  })

  it('does not invent inventory before a run exists', () => {
    render(<TransferInventoryPanel />)
    expect(screen.getByText(/不生成或猜测 WMS 库存/)).toBeInTheDocument()
  })
})
