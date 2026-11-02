import {
  CONVEYOR_DECK_BASE,
  CONVEYOR_DECK_STEP,
  CONVEYOR_GROUND_BELT_Y,
} from './topology3d'
import { conveyorDecksOf, conveyorDirectionFromSide } from './topology'
import type {
  ConveyorDirection,
  PickingArmConveyorStep,
  RightWarehouseMergePoint,
  TopologyCell,
  TopologyNode,
  TopologyPayload,
} from '../types'

export type ConveyorPreviewStopCode =
  | 'ENDPOINT_REACHED'
  | 'INVALID_START'
  | 'MISSING_NEXT_CELL'
  | 'LEVEL_MISMATCH'
  | 'TURN_DEVICE_MISSING'
  | 'DIRECTION_MISMATCH'
  | 'CYCLE_DETECTED'
  | 'PATH_GUARD_REACHED'

export interface ConveyorPreviewPath {
  status: 'PASS' | 'BLOCKED'
  steps: PickingArmConveyorStep[]
  endpointId: string | null
  stopCode: ConveyorPreviewStopCode
  stopReason: string
}

const DELTA: Record<ConveyorDirection, [number, number]> = {
  right: [0, 1],
  left: [0, -1],
  up: [-1, 0],
  down: [1, 0],
}

function floorOf(cell: TopologyCell): string {
  return cell.floor ?? '1F'
}

function beltHeightAt(cell: TopologyCell, level: number): number | null {
  if (cell.conveyorGround) return level === 1 ? CONVEYOR_GROUND_BELT_Y : null
  if (Math.max(1, cell.levels ?? 1) < level) return null
  return CONVEYOR_DECK_BASE + Math.max(0, level - 1) * CONVEYOR_DECK_STEP
}

function directionAt(
  topology: TopologyPayload,
  cell: TopologyCell,
  level: number,
): ConveyorDirection | null {
  const decks = conveyorDecksOf(topology, cell)
  const deck = level === 1 ? decks.lower : decks.upper
  if (!deck?.enabled) return null
  return conveyorDirectionFromSide(deck.outputSide)
}

function configuredTurn(
  nodes: TopologyNode[],
  level: number,
  incoming: ConveyorDirection,
  outgoing: ConveyorDirection,
): TopologyNode | undefined {
  const inputSide = {
    right: 'west', left: 'east', up: 'south', down: 'north',
  }[incoming]
  const outputSide = {
    right: 'east', left: 'west', up: 'north', down: 'south',
  }[outgoing]
  const deck = level === 1 ? 'lower' : 'upper'
  return nodes.find((node) => (
    node.type === 'turn_transfer'
    && (node.transferDeck ?? deck) === deck
    && node.transferInputSide === inputSide
    && node.transferOutputSide === outputSide
  ))
}

export function buildPickingArmConveyorPath(
  topology: TopologyPayload,
  startCellId: string,
  level: number,
  mergePoint?: RightWarehouseMergePoint,
): ConveyorPreviewPath {
  const conveyors = topology.cells.filter((cell) => cell.type === 'conveyor')
  const byId = new Map(conveyors.map((cell) => [cell.id, cell]))
  const byPosition = new Map(
    conveyors.map((cell) => [`${floorOf(cell)}:${cell.row}:${cell.col}`, cell]),
  )
  const nodesByPosition = new Map<string, typeof topology.nodes>()
  for (const node of topology.nodes) {
    const key = `${node.floor ?? '1F'}:${node.row}:${node.col}`
    const nodes = nodesByPosition.get(key) ?? []
    nodes.push(node)
    nodesByPosition.set(key, nodes)
  }
  let current = byId.get(startCellId)
  if (!current || beltHeightAt(current, level) === null) {
    return {
      status: 'BLOCKED',
      steps: [],
      endpointId: null,
      stopCode: 'INVALID_START',
      stopReason: `${startCellId} is not a conveyor with physical deck L${level}`,
    }
  }

  const steps: PickingArmConveyorStep[] = []
  const visited = new Set<string>()
  let incomingDirection: ConveyorDirection | null = null
  for (let guard = 0; guard < 500; guard += 1) {
    if (visited.has(current.id)) {
      return {
        status: 'BLOCKED', steps, endpointId: current.id,
        stopCode: 'CYCLE_DETECTED', stopReason: `Cycle detected at ${current.id}`,
      }
    }
    visited.add(current.id)
    const floor = floorOf(current)
    const direction = directionAt(topology, current, level)
    if (!direction) {
      return {
        status: 'BLOCKED', steps, endpointId: steps.at(-1)?.cellId ?? null,
        stopCode: 'LEVEL_MISMATCH', stopReason: `L${level} deck is unavailable at ${current.id}`,
      }
    }
    const beltHeight = beltHeightAt(current, level)
    if (beltHeight === null) {
      return {
        status: 'BLOCKED', steps, endpointId: steps.at(-1)?.cellId ?? null,
        stopCode: 'LEVEL_MISMATCH', stopReason: `L${level} is unavailable at ${current.id}`,
      }
    }
    const currentKey = `${floor}:${current.row}:${current.col}`
    const devices = nodesByPosition.get(currentKey) ?? []
    const turn = incomingDirection && incomingDirection !== direction
      ? configuredTurn(devices, level, incomingDirection, direction)
      : undefined
    if (incomingDirection && incomingDirection !== direction && !turn) {
      return {
        status: 'BLOCKED', steps, endpointId: current.id,
        stopCode: 'TURN_DEVICE_MISSING',
        stopReason: `Direction changes ${incomingDirection}->${direction} at ${current.id} without configured turn_transfer`,
      }
    }
    const step: PickingArmConveyorStep = {
      cellId: current.id,
      position: { floor, row: current.row, col: current.col, level },
      direction,
      beltHeight,
      deviceIds: devices.map((node) => node.id),
      transitionToNext: turn ? 'device_transfer' : 'conveyor',
      transferDeviceId: turn?.id,
      directionSource: 'topology',
    }
    steps.push(step)
    const delta = DELTA[direction]
    const nextKey = `${floor}:${current.row + delta[0]}:${current.col + delta[1]}`
    if (mergePoint && current.row + delta[0] === mergePoint.row && current.col + delta[1] === mergePoint.col) {
      const endpointDevices = nodesByPosition.get(nextKey) ?? []
      if (!endpointDevices.some((node) => node.id === mergePoint.topologyNodeId)) {
        return {
          status: 'BLOCKED', steps, endpointId: current.id,
          stopCode: 'MISSING_NEXT_CELL', stopReason: `Merge point ${mergePoint.topologyNodeId} is missing at ${nextKey}`,
        }
      }
      steps.push({
        cellId: `cell-r${mergePoint.row}-c${mergePoint.col}`,
        position: { floor, row: mergePoint.row, col: mergePoint.col, level },
        direction,
        beltHeight,
        deviceIds: endpointDevices.map((node) => node.id),
        transitionToNext: 'endpoint',
        directionSource: 'topology',
      })
      return {
        status: 'PASS', steps, endpointId: steps.at(-1)!.cellId,
        stopCode: 'ENDPOINT_REACHED', stopReason: `Reached ${mergePoint.mergePointId}`,
      }
    }
    const next = byPosition.get(nextKey)
    if (!next) {
      steps[steps.length - 1] = { ...step, transitionToNext: 'endpoint' }
      return {
        status: !mergePoint && steps.length >= 2 ? 'PASS' : 'BLOCKED',
        steps,
        endpointId: current.id,
        stopCode: 'MISSING_NEXT_CELL',
        stopReason: `Preview stops before missing conveyor at r${current.row + delta[0]},c${current.col + delta[1]}`,
      }
    }
    const nextHeight = beltHeightAt(next, level)
    if (nextHeight === null || Math.abs(nextHeight - beltHeight) > 1e-9) {
      return {
        status: 'BLOCKED', steps, endpointId: current.id,
        stopCode: 'LEVEL_MISMATCH',
        stopReason: `No L${level} continuity from ${current.id} to ${next.id}`,
      }
    }
    incomingDirection = direction
    current = next
  }
  return {
    status: 'BLOCKED', steps, endpointId: steps.at(-1)?.cellId ?? null,
    stopCode: 'PATH_GUARD_REACHED', stopReason: 'Conveyor path guard reached',
  }
}

export function validateServerPickingArmPath(
  topology: TopologyPayload,
  startCellId: string,
  level: number,
  serverSteps: PickingArmConveyorStep[],
  mergePoint?: RightWarehouseMergePoint,
): ConveyorPreviewPath {
  if (!serverSteps.length || serverSteps[0].cellId !== startCellId) {
    return {
      status: 'BLOCKED', steps: [], endpointId: null,
      stopCode: 'INVALID_START', stopReason: `Server path does not start at ${startCellId}`,
    }
  }
  const cells = new Map(topology.cells.map((cell) => [cell.id, cell]))
  const nodes = new Map(topology.nodes.map((node) => [node.id, node]))
  const nodesByPosition = new Map<string, TopologyNode[]>()
  for (const node of topology.nodes) {
    const key = `${node.floor ?? '1F'}:${node.row}:${node.col}`
    nodesByPosition.set(key, [...(nodesByPosition.get(key) ?? []), node])
  }
  for (let index = 0; index < serverSteps.length; index += 1) {
    const step = serverSteps[index]
    const cell = cells.get(step.cellId)
    const isFinal = index === serverSteps.length - 1
    const isMergeEndpoint = Boolean(
      isFinal
      && mergePoint
      && step.position.row === mergePoint.row
      && step.position.col === mergePoint.col
      && step.position.level === mergePoint.level
      && step.deviceIds.includes(mergePoint.topologyNodeId),
    )
    if (isMergeEndpoint) {
      const device = nodes.get(mergePoint!.topologyNodeId)
      const previous = serverSteps[index - 1]
      if (
        !cell
        || !device
        || device.type !== 'lift_transfer'
        || !previous
        || step.direction !== previous.direction
        || step.directionSource !== 'topology'
        || step.beltHeight !== previous.beltHeight
        || floorOf(cell) !== step.position.floor
        || cell.row !== step.position.row
        || cell.col !== step.position.col
        || step.transitionToNext !== 'endpoint'
      ) {
        return {
          status: 'BLOCKED', steps: serverSteps.slice(0, index),
          endpointId: serverSteps[index - 1]?.cellId ?? null,
          stopCode: 'MISSING_NEXT_CELL', stopReason: `Invalid merge-point endpoint ${step.cellId}`,
        }
      }
      continue
    }
    const expectedDirection = cell ? directionAt(topology, cell, level) : null
    if (
      !cell
      || cell.type !== 'conveyor'
      || !expectedDirection
      || beltHeightAt(cell, level) !== step.beltHeight
      || floorOf(cell) !== step.position.floor
      || cell.row !== step.position.row
      || cell.col !== step.position.col
      || step.position.level !== level
    ) {
      return {
        status: 'BLOCKED', steps: serverSteps.slice(0, index),
        endpointId: serverSteps[index - 1]?.cellId ?? null,
        stopCode: 'LEVEL_MISMATCH',
        stopReason: `Server step ${step.cellId} does not match a physical L${level} conveyor`,
      }
    }
    if (step.direction !== expectedDirection || step.directionSource !== 'topology') {
      return {
        status: 'BLOCKED', steps: serverSteps.slice(0, index + 1), endpointId: step.cellId,
        stopCode: 'DIRECTION_MISMATCH',
        stopReason: `Server direction at ${step.cellId} does not match topology`,
      }
    }
    const previous = serverSteps[index - 1]
    if (previous && previous.direction !== expectedDirection) {
      const localNodes = nodesByPosition.get(`${step.position.floor}:${step.position.row}:${step.position.col}`) ?? []
      const turn = configuredTurn(localNodes, level, previous.direction, expectedDirection)
      if (!turn || step.transitionToNext !== 'device_transfer' || step.transferDeviceId !== turn.id) {
        return {
          status: 'BLOCKED', steps: serverSteps.slice(0, index + 1), endpointId: step.cellId,
          stopCode: 'TURN_DEVICE_MISSING', stopReason: `Missing configured turn device at ${step.cellId}`,
        }
      }
    } else if (step.transitionToNext === 'device_transfer') {
      return {
        status: 'BLOCKED', steps: serverSteps.slice(0, index + 1), endpointId: step.cellId,
        stopCode: 'TURN_DEVICE_MISSING', stopReason: `Unexpected turn device at ${step.cellId}`,
      }
    }
    const next = serverSteps[index + 1]
    if (!next) {
      if (step.transitionToNext !== 'endpoint') {
        return {
          status: 'BLOCKED', steps: serverSteps, endpointId: step.cellId,
          stopCode: 'DIRECTION_MISMATCH', stopReason: 'Final step is not an endpoint',
        }
      }
      continue
    }
    const delta = DELTA[expectedDirection]
    if (
      step.position.floor !== next.position.floor
      || step.position.row + delta[0] !== next.position.row
      || step.position.col + delta[1] !== next.position.col
      || next.position.level !== level
    ) {
      return {
        status: 'BLOCKED', steps: serverSteps.slice(0, index + 1), endpointId: step.cellId,
        stopCode: 'DIRECTION_MISMATCH', stopReason: `Illegal directed edge ${step.cellId}->${next.cellId}`,
      }
    }
    const isTurnStep = previous && previous.direction !== expectedDirection
    if (!isTurnStep && step.transitionToNext !== 'conveyor') {
      return {
        status: 'BLOCKED', steps: serverSteps.slice(0, index + 1), endpointId: step.cellId,
        stopCode: 'DIRECTION_MISMATCH', stopReason: `Invalid transition at ${step.cellId}`,
      }
    }
  }
  return {
    status: 'PASS',
    steps: serverSteps,
    endpointId: serverSteps[serverSteps.length - 1].cellId,
    stopCode: 'ENDPOINT_REACHED',
    stopReason: `Validated topology path ends at ${serverSteps[serverSteps.length - 1].cellId}`,
  }
}
