import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import * as topologyRuntime from '../../lib/topology'
import {
  isSimulationCargoRenderedByDevice,
  shouldRenderSimulationCargoIndependently,
} from '../../lib/simulationTestVisualization'
import type { SimulationTestLeftFlowState, SimulationTestRunState, TopologyPayload, TransportLayoutPlan } from '../../types'
import {
  PickingArmAuthoritativeLayer,
  PickingArmPreviewLayer,
  SimulationTestBreakPackLayer,
} from './Simulation3DScene'

const armMount = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
}))

vi.mock('./PickingArmHandoff', () => ({
  PickingArmHandoff: (props: Record<string, unknown>) => {
    armMount.props = props
    return <div data-testid="real-picking-arm-layer" />
  },
}))

function realTopologyPath(): string {
  const checkedPaths: string[] = []
  let directory = resolve(process.cwd())

  while (true) {
    const candidate = join(directory, 'data', 'topology_666.json')
    checkedPaths.push(candidate)
    if (existsSync(candidate)) return candidate

    const parent = dirname(directory)
    if (parent === directory) break
    directory = parent
  }

  throw new Error(
    `Real topology file data/topology_666.json was not found. Checked:\n${checkedPaths.join('\n')}`,
  )
}

function loadRealTopology(): TopologyPayload {
  return JSON.parse(readFileSync(realTopologyPath(), 'utf8')) as TopologyPayload
}

function armPlan(): TransportLayoutPlan {
  return {
    planId: 'runtime-import-regression',
    schemaVersion: '1',
    topologyId: 'topology-6ceb4920',
    topologyRevision: '2.0.0:68518f43957197c1',
    overallStatus: 'PARTIAL',
    pickingArm: {
      armId: 'proposed-picking-arm-r91-c54',
      status: 'proposed',
      validationStatus: 'PARTIAL',
      installation: { floor: '1F', row: 91, col: 54, level: 1 },
      mountHeight: 0,
      workRadius: 7.9,
      kinematics: {
        upperArmLength: 4,
        forearmLength: 3.9,
        shoulderHeight: 4,
        baseRadius: 0.68,
        upperArmThickness: 0.42,
        forearmThickness: 0.38,
        jointRadius: 0.32,
        gripperScale: 1.45,
        safeTravelHeight: 6.3,
        linkCollisionRadius: 0.24,
      },
      pickup: {
        nodeId: 'existing-ground-conveyor-cell-r92-c48',
        topologyCellId: 'cell-r92-c48',
        position: { floor: '1F', row: 92, col: 48, level: 1 },
        beltHeight: 0.6,
        deckIndex: 1,
        direction: 'right',
        provenance: 'topology',
        validationStatus: 'PASS',
        evidence: [],
      },
      dropoff: {
        nodeId: 'existing-conveyor-cell-r86-c50',
        topologyCellId: 'cell-r86-c50',
        position: { floor: '1F', row: 86, col: 50, level: 2 },
        beltHeight: 3,
        deckIndex: 2,
        direction: 'right',
        provenance: 'topology',
        validationStatus: 'PASS',
        evidence: [],
      },
      liftBId: 'shuttle-lift-b',
      liftBPosition: { floor: '1F', row: 98, col: 49, level: 1 },
      liftBClearanceCells: 8.6,
      downstreamCellId: 'cell-r86-c51',
      downstreamStatus: 'PASS',
      previewConveyorPath: [
        {
          cellId: 'cell-r86-c50',
          position: { floor: '1F', row: 86, col: 50, level: 2 },
          direction: 'right',
          beltHeight: 3,
          deviceIds: [],
          transitionToNext: 'conveyor',
          directionSource: 'topology',
        },
        {
          cellId: 'cell-r86-c51',
          position: { floor: '1F', row: 86, col: 51, level: 2 },
          direction: 'right',
          beltHeight: 3,
          deviceIds: [],
          transitionToNext: 'endpoint',
          directionSource: 'topology',
        },
      ],
      previewEndpointId: 'cell-r86-c51',
      previewPathStatus: 'PASS',
      previewStopReason: 'test endpoint',
      mergePoint: {
        mergePointId: 'right-order-merge-r108-c68',
        topologyNodeId: 'lift_transfer-09',
        row: 108,
        col: 68,
        level: 2,
        capacity: 1,
        occupiedCargoIds: [],
        acceptedCargoIds: [],
        status: 'available',
        acceptedWarehouse: 'right',
        cargoOrderIds: {},
      },
      actionSequence: ['idle', 'approach', 'grip', 'lift', 'rotate', 'place', 'release', 'return_home'],
      requiredAuthoritativeSignals: ['pickedGoodsReady', 'outputPositionAvailable', 'handoffCompleted'],
      canDeclareDelivery: false,
      evidence: [],
    },
    summary: {
      previewOnly: true,
      inventoryMutated: false,
      ordersCompleted: false,
      formalTopologyMutated: false,
      conveyorNodeCount: 2,
    },
  }
}

describe('Simulation3DScene runtime module graph', () => {
  it('exposes conveyorDecksOf as a real runtime function', () => {
    expect(Object.prototype.hasOwnProperty.call(topologyRuntime, 'conveyorDecksOf')).toBe(true)
    expect(topologyRuntime.conveyorDecksOf).toBeTypeOf('function')
  })

  it('loads the real Simulation3DScene module without a missing-export exception', async () => {
    const sceneModule = await import('./Simulation3DScene')
    expect(sceneModule.default).toBeDefined()
    expect(sceneModule.PickingArmPreviewLayer).toBeTypeOf('function')
  })

  it('mounts the simulation-only left break-pack station without creating another canvas', () => {
    const position = {
      nodeId: 'cell-r72-c66', row: 72, col: 66, level: 1,
      worldX: 16.5, worldY: 0, worldZ: 35.5,
    }
    const frame = {
      cellId: 'cell-r72-c65', nodeId: 'cell-r72-c65', level: 1,
      stage: 'track' as const, position: { ...position, nodeId: 'cell-r72-c65', col: 65 }, index: 0,
    }
    const leftFlow: SimulationTestLeftFlowState = {
      status: 'break_pack_opening', taskId: 'task-left', sourceLocationId: 'storage-01:L1',
      sourceCellId: 'cell-r0-c6', sourceStopCellId: 'cell-r0-c7', sourcePosition: { ...position, nodeId: 'cell-r0-c6', row: 0, col: 6 },
      cargo: {
        cargoId: 'left-case', orderId: 'order-110', taskId: 'task-left', skuId: 'sku-test',
        quantityCases: 1, quantityBase: 20, sourceLocationId: 'storage-01:L1',
        currentNodeId: position.nodeId, currentDeviceId: 'simtest-left-break-pack-r72-c66',
        status: 'break_pack_opening', caseType: 'full_case', dataSource: 'simulation_test',
        authoritative: false, preview: false, simulationStateAuthoritative: true,
        currentPosition: position, previousPosition: position,
      },
      partialCase: null,
      ctu: {
        deviceId: 'ctu-07', capacity: 1, phase: 'waiting_break_pack', phaseTicks: 0,
        activeCargoId: null, routeName: 'to_station', routeIndex: 0,
        toSourceFrames: [frame], toStationFrames: [frame], toStorageFrames: [frame],
        currentPosition: frame.position, dataSource: 'simulation_test',
      },
      station: {
        deviceId: 'simtest-left-break-pack-r72-c66', deviceType: 'break_pack_station',
        positionCellId: position.nodeId, inputNodeId: 'cell-r72-c65', outputNodeId: 'cell-r72-c65',
        capacity: 1, reservedCargoIds: [], occupiedCargoIds: ['left-case'], phase: 'opening', phaseTicks: 1,
        position, dataSource: 'simulation_test', validationStatus: 'simulation_only',
      },
      ledger: {
        initialSourceQuantityBase: 20, sourceQuantityBase: 0, ctuQuantityBase: 0,
        stationQuantityBase: 20, pickedQuantityBase: 0, returnedQuantityBase: 0,
        returnApplied: false,
      },
      quantityAudit: { sourceConserved: false, orderQuantityAccounted: false },
    }
    const layer = SimulationTestBreakPackLayer({ leftFlow }) as {
      type: string
      props: { name: string; children: unknown[] }
    }
    expect(layer.type).toBe('group')
    expect(layer.props.name).toBe('simulation-test-left-break-pack')
    expect(layer.props.children).toHaveLength(2)
  })

  it('parses real dual-deck Conveyor data through the runtime export', () => {
    const topology = loadRealTopology()
    const cell = topology.cells.find((item) => item.id === 'cell-r86-c50')
    expect(cell).toBeDefined()
    const decks = topologyRuntime.conveyorDecksOf(topology, cell!)

    expect(decks.lower).toMatchObject({
      enabled: true,
      inputSide: 'west',
      outputSide: 'east',
      role: 'outbound',
    })
    expect(decks.upper).toMatchObject({
      enabled: true,
      inputSide: 'west',
      outputSide: 'east',
      role: 'outbound',
    })
  })

  it('exposes both configured turns and the real lift endpoint to the scene', () => {
    const topology = loadRealTopology()
    const nodes = new Map(topology.nodes.map((node) => [node.id, node]))
    const cells = new Map(topology.cells.map((cell) => [cell.id, cell]))

    expect(nodes.get('turn_transfer-15')).toMatchObject({
      row: 86, col: 60, transferDeck: 'upper',
      transferInputSide: 'west', transferOutputSide: 'south',
    })
    expect(nodes.get('turn_transfer-13')).toMatchObject({
      row: 108, col: 60, transferDeck: 'upper',
      transferInputSide: 'north', transferOutputSide: 'east',
    })
    expect(cells.get('cell-r108-c68')).toMatchObject({ row: 108, col: 68, type: 'road' })
    expect(nodes.get('lift_transfer-09')).toMatchObject({
      row: 108, col: 68, type: 'lift_transfer', capacity: 1,
    })
  })

  it('mounts the picking-arm layer from the real scene module', () => {
    const topology = loadRealTopology()
    const plan = armPlan()
    render(
      <PickingArmPreviewLayer
        topology={topology}
        activeFloor="1F"
        layoutPlan={plan}
        visible
        cycleToken="arm-runtime-1"
        resetRequest={0}
        paused={false}
        speedMultiplier={2}
        cargo={{ toteId: 'tote-1', orderId: 'ORDER-1', skuId: 'SKU-1', pickedQuantity: 1 }}
      />,
    )

    expect(screen.getByTestId('real-picking-arm-layer')).toBeInTheDocument()
    expect(armMount.props).toMatchObject({
      topology,
      layout: plan.pickingArm,
      preview: true,
      previewCycleToken: 'arm-runtime-1',
      paused: false,
      speedMultiplier: 2,
    })
  })

  it('keeps one idle picking arm mounted without a Run or preview toggle', () => {
    const topology = loadRealTopology()
    const plan = armPlan()
    render(
      <PickingArmAuthoritativeLayer
        topology={topology}
        activeFloor="1F"
        layoutPlan={plan}
        paused={false}
      />,
    )

    expect(screen.getByTestId('real-picking-arm-layer')).toBeInTheDocument()
    expect(armMount.props).toMatchObject({
      topology,
      layout: plan.pickingArm,
      preview: false,
      previewCycleToken: 'persistent-picking-arm',
      authoritativeState: undefined,
      cargo: {
        toteId: 'simulation-test-arm-idle',
        orderId: 'NO-ACTIVE-RUN',
        pickedQuantity: 0,
      },
    })
  })

  it('renders Shuttle/Lift cargo only inside the device model', () => {
    for (const status of [
      'shuttle_picking', 'shuttle_transporting', 'lift_waiting',
      'lift_moving', 'shuttle_to_handoff', 'arm_processing',
    ] as const) {
      expect(isSimulationCargoRenderedByDevice(status)).toBe(true)
    }
    for (const status of [
      'waiting_pick', 'waiting_dock', 'dock_transferring',
      'conveyor_transporting', 'waiting_arm', 'merge_conveyor_transporting',
      'merge_arrived',
    ] as const) {
      expect(isSimulationCargoRenderedByDevice(status)).toBe(false)
    }
    expect(shouldRenderSimulationCargoIndependently(
      { cargoId: 'cargo-at-arm', status: 'waiting_arm' },
      'cargo-at-arm',
    )).toBe(false)
    expect(shouldRenderSimulationCargoIndependently(
      { cargoId: 'cargo-upstream', status: 'waiting_arm' },
      'cargo-at-arm',
    )).toBe(true)
  })

  it('mounts the arm from the backend-authoritative simulation_test phase', () => {
    const topology = loadRealTopology()
    const plan = armPlan()
    const simulationTest = {
      order: { orderId: 'ORDER-TEST' },
      cargo: [{
        cargoId: 'cargo-001', orderId: 'ORDER-TEST', skuId: 'SKU-1', quantityBase: 20,
      }],
      arm: {
        activeCargoId: 'cargo-001', phase: 'rotate', phaseProgress: 0.5,
        cargoPlacement: 'gripper', completedCargoIds: [],
      },
      resetGeneration: 0,
    } as unknown as SimulationTestRunState

    render(
      <PickingArmAuthoritativeLayer
        topology={topology}
        activeFloor="1F"
        layoutPlan={plan}
        simulationTest={simulationTest}
        paused={false}
      />,
    )

    expect(screen.getByTestId('real-picking-arm-layer')).toBeInTheDocument()
    expect(armMount.props).toMatchObject({
      preview: false,
      authoritativeState: simulationTest.arm,
      cargo: {
        toteId: 'cargo-001', orderId: 'ORDER-TEST', skuId: 'SKU-1', pickedQuantity: 20,
      },
    })
  })
})
