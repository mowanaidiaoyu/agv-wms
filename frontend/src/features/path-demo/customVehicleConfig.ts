import { MULTI_VEHICLE_STYLES } from './multiPathPlayback'
import type { MultiPathVehicleRequest, PathDemoCell } from './types'

function firstUnusedVehicleId(vehicles: MultiPathVehicleRequest[]): string | null {
  const ids = new Set(vehicles.map((vehicle) => vehicle.vehicle_id))
  for (let index = 1; index <= 6; index += 1) {
    const candidate = `CTU-${String(index).padStart(2, '0')}`
    if (!ids.has(candidate)) return candidate
  }
  return null
}

function pickUnusedCell(cells: PathDemoCell[], used: Set<string>, preferredIndex: number): string {
  if (!cells.length) return ''
  for (let offset = 0; offset < cells.length; offset += 1) {
    const cell = cells[(preferredIndex + offset) % cells.length]
    if (!used.has(cell.id)) return cell.id
  }
  return ''
}

export function createCustomVehicle(
  vehicles: MultiPathVehicleRequest[],
  roadCells: PathDemoCell[],
): MultiPathVehicleRequest | null {
  const vehicleId = firstUnusedVehicleId(vehicles)
  if (!vehicleId || vehicles.length >= 6) return null
  const vehicleNumber = Number(vehicleId.slice(-2))
  const usedStarts = new Set(vehicles.map((vehicle) => vehicle.start_cell_id).filter(Boolean))
  const usedGoals = new Set(vehicles.map((vehicle) => vehicle.goal_cell_id).filter(Boolean))
  const startIndex = Math.floor((vehicleNumber * roadCells.length) / 7)
  const goalIndex = Math.floor(((7 - vehicleNumber) * roadCells.length) / 7)
  const startCellId = pickUnusedCell(roadCells, usedStarts, startIndex)
  usedGoals.add(startCellId)
  return {
    vehicle_id: vehicleId,
    vehicle_type: 'ctu',
    start_cell_id: startCellId,
    goal_cell_id: pickUnusedCell(roadCells, usedGoals, goalIndex),
    priority: vehicleNumber,
    display_color: MULTI_VEHICLE_STYLES[vehicleNumber - 1]?.color ?? '#e2e8f0',
  }
}

export function validateMultiVehicleConfiguration(
  vehicles: MultiPathVehicleRequest[],
  roadCells: PathDemoCell[],
): string {
  if (vehicles.length < 1 || vehicles.length > 6) return '车辆数量必须在 1–6 辆之间。'
  if (new Set(vehicles.map((vehicle) => vehicle.vehicle_id)).size !== vehicles.length) return '车辆 ID 必须唯一。'
  if (vehicles.some((vehicle) => !vehicle.start_cell_id || !vehicle.goal_cell_id)) return '每辆车都必须配置起点和终点。'
  if (new Set(vehicles.map((vehicle) => vehicle.start_cell_id)).size !== vehicles.length) return '多辆车不能使用同一个起点。'
  if (new Set(vehicles.map((vehicle) => vehicle.goal_cell_id)).size !== vehicles.length) return '多辆车不能使用同一个终点。'
  const cellsById = new Map(roadCells.map((cell) => [cell.id, cell]))
  for (const vehicle of vehicles) {
    const start = cellsById.get(vehicle.start_cell_id)
    const goal = cellsById.get(vehicle.goal_cell_id)
    if (!start || start.type !== 'road') return `${vehicle.vehicle_id} 的起点不是可行驶道路。`
    if (!goal || goal.type !== 'road') return `${vehicle.vehicle_id} 的终点不是可行驶道路。`
    if (start.component_id !== goal.component_id) return `${vehicle.vehicle_id} 的起终点不在同一道路连通分量。`
  }
  return ''
}
