import {
  CONVEYOR_DECK_BASE,
  CONVEYOR_DECK_STEP,
  CONVEYOR_GROUND_BELT_Y,
} from './topology3d'
import type { ConveyorDirection, PickingArmConveyorStep, TopologyCell, TopologyPayload } from '../types'

export type ConveyorPreviewStopCode =
  | 'INVALID_START'
  | 'MISSING_NEXT_CELL'
  | 'LEVEL_MISMATCH'
  | 'TURN_DEVICE_MISSING'
  | 'CYCLE_DETECTED'
  | 'UNSUPPORTED_DIRECTION'
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

export function buildPickingArmConveyorPath(
  topology: TopologyPayload,
  startCellId: string,
  level: number,
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
  for (let guard = 0; guard < 500; guard += 1) {
    if (visited.has(current.id)) {
      return {
        status: 'BLOCKED', steps, endpointId: current.id,
        stopCode: 'CYCLE_DETECTED', stopReason: `Cycle detected at ${current.id}`,
      }
    }
    visited.add(current.id)
    const floor = floorOf(current)
    const direction = current.conveyorDirection ?? 'right'
    const beltHeight = beltHeightAt(current, level)
    if (beltHeight === null) {
      return {
        status: 'BLOCKED', steps, endpointId: steps.at(-1)?.cellId ?? null,
        stopCode: 'LEVEL_MISMATCH', stopReason: `L${level} is unavailable at ${current.id}`,
      }
    }
    const currentKey = `${floor}:${current.row}:${current.col}`
    steps.push({
      cellId: current.id,
      position: { floor, row: current.row, col: current.col, level },
      direction,
      beltHeight,
      deviceIds: (nodesByPosition.get(currentKey) ?? []).map((node) => node.id),
      transitionToNext: 'conveyor',
      transferDeviceId: null,
    })
    const delta = DELTA[direction]
    if (!delta) {
      return {
        status: 'BLOCKED', steps, endpointId: current.id,
        stopCode: 'UNSUPPORTED_DIRECTION', stopReason: `Unsupported direction ${direction}`,
      }
    }
    const nextKey = `${floor}:${current.row + delta[0]}:${current.col + delta[1]}`
    const next = byPosition.get(nextKey)
    if (!next) {
      return {
        status: steps.length >= 2 ? 'PASS' : 'BLOCKED',
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
        stopReason: `No transfer device declares L${level} continuity from ${current.id} to ${next.id}`,
      }
    }
    const nextDirection = next.conveyorDirection ?? 'right'
    if (nextDirection !== direction) {
      const transferNodes = [
        ...(nodesByPosition.get(currentKey) ?? []),
        ...(nodesByPosition.get(nextKey) ?? []),
      ].filter((node) => node.type === 'turn_transfer')
      if (!transferNodes.length) {
        return {
          status: 'BLOCKED', steps, endpointId: current.id,
          stopCode: 'TURN_DEVICE_MISSING',
          stopReason: `Direction changes ${direction}→${nextDirection} without turn_transfer`,
        }
      }
    }
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
): ConveyorPreviewPath {
  if (!serverSteps.length || serverSteps[0].cellId !== startCellId) {
    return {
      status: 'BLOCKED',
      steps: [], endpointId: null, stopCode: 'INVALID_START',
      stopReason: `Server path does not start at ${startCellId}`,
    }
  }
  const cells = new Map(topology.cells.map((cell) => [cell.id, cell]))
  const nodes = new Map(topology.nodes.map((node) => [node.id, node]))
  for (let index = 0; index < serverSteps.length; index += 1) {
    const step = serverSteps[index]
    const cell = cells.get(step.cellId)
    if (!cell || cell.type !== 'conveyor' || beltHeightAt(cell, level) !== step.beltHeight) {
      return {
        status: 'BLOCKED', steps: serverSteps.slice(0, index), endpointId: serverSteps[index - 1]?.cellId ?? null,
        stopCode: 'LEVEL_MISMATCH', stopReason: `Server step ${step.cellId} is not a matching L${level} conveyor`,
      }
    }
    const next = serverSteps[index + 1]
    if (!next) continue
    const manhattan = Math.abs(step.position.row - next.position.row)
      + Math.abs(step.position.col - next.position.col)
    if (manhattan !== 1 || step.position.floor !== next.position.floor || step.position.level !== next.position.level) {
      return {
        status: 'BLOCKED', steps: serverSteps.slice(0, index + 1), endpointId: step.cellId,
        stopCode: 'LEVEL_MISMATCH', stopReason: `Non-adjacent or cross-level edge ${step.cellId}→${next.cellId}`,
      }
    }
    if (step.transitionToNext === 'device_transfer') {
      const device = step.transferDeviceId ? nodes.get(step.transferDeviceId) : undefined
      if (!device || device.type !== 'turn_transfer') {
        return {
          status: 'BLOCKED', steps: serverSteps.slice(0, index + 1), endpointId: step.cellId,
          stopCode: 'TURN_DEVICE_MISSING', stopReason: `Missing transfer device for ${step.cellId}→${next.cellId}`,
        }
      }
      continue
    }
    const direction = cell.conveyorDirection ?? 'right'
    const delta = DELTA[direction]
    if (cell.row + delta[0] !== next.position.row || cell.col + delta[1] !== next.position.col) {
      return {
        status: 'BLOCKED', steps: serverSteps.slice(0, index + 1), endpointId: step.cellId,
        stopCode: 'TURN_DEVICE_MISSING', stopReason: `Directed edge mismatch at ${step.cellId}`,
      }
    }
  }
  return {
    status: 'PASS',
    steps: serverSteps,
    endpointId: serverSteps[serverSteps.length - 1].cellId,
    stopCode: 'MISSING_NEXT_CELL',
    stopReason: `Validated server preview path ends at ${serverSteps[serverSteps.length - 1].cellId}`,
  }
}
