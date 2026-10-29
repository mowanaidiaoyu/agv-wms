import type {
  GoodsInventory,
  GoodsCatalog,
  SimulationOrderInput,
  ShuttleContract,
  ShuttlePlan,
  ShuttleTransferInput,
  SimulationMetrics,
  SimulationState,
  SimulationTask,
  StoredRecord,
  TopologyPayload,
} from '../types'

const API_BASE = 'http://127.0.0.1:8000/api/v1'
const WS_BASE = 'ws://127.0.0.1:8000/ws'

type ApiValidationIssue = {
  loc?: Array<string | number>
  msg?: string
  type?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function formatValidationIssue(issue: unknown): string {
  if (typeof issue === 'string') return issue
  if (!isRecord(issue)) return JSON.stringify(issue)
  const typed = issue as ApiValidationIssue
  const location = Array.isArray(typed.loc)
    ? typed.loc.filter((part) => part !== 'body').join('.')
    : ''
  const message = typeof typed.msg === 'string'
    ? typed.msg
    : typeof issue.message === 'string'
      ? issue.message
      : JSON.stringify(issue)
  return location ? `${location}: ${message}` : message
}

export function formatApiErrorPayload(payload: unknown, fallback: string): string {
  if (!isRecord(payload)) return fallback
  const detail = payload.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(formatValidationIssue).join('；')
  if (detail !== undefined) return formatValidationIssue(detail)
  if (typeof payload.message === 'string') return payload.message
  return fallback
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      message = formatApiErrorPayload(await response.json(), message)
    } catch {
      // Ignore JSON parsing failures and keep the HTTP status message.
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const api = {
  wsUrl(runId: string) {
    return `${WS_BASE}/simulation/${runId}`
  },
  health() {
    return request<{ status: string }>('/health')
  },
  listTopologies() {
    return request<Array<StoredRecord<TopologyPayload>>>('/topologies')
  },
  getTopology(topologyId: string) {
    return request<StoredRecord<TopologyPayload>>(`/topologies/${topologyId}`)
  },
  saveTopology(payload: TopologyPayload) {
    return request<StoredRecord<TopologyPayload>>(`/topologies${payload.id ? '' : ''}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
  deleteTopology(topologyId: string) {
    return request<StoredRecord<TopologyPayload>>(`/topologies/${topologyId}`, {
      method: 'DELETE',
    })
  },
  startSimulation(
    topologyId: string,
    orders: SimulationOrderInput[] = [],
    transfers: ShuttleTransferInput[] = [],
  ) {
    return request<{ runId: string; status: string }>('/simulations/start', {
      method: 'POST',
      body: JSON.stringify({ topologyId, orders, transfers }),
    })
  },
  insertSimulationOrder(runId: string, order: SimulationOrderInput) {
    return request<SimulationState>(`/simulations/${runId}/orders`, {
      method: 'POST',
      body: JSON.stringify(order),
    })
  },
  cancelSimulationOrder(runId: string, orderId: string, reason = '用户取消') {
    return request<SimulationState>(`/simulations/${runId}/orders/${orderId}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    })
  },
  pauseSimulation(runId: string) {
    return request<{ runId: string; status: string }>(`/simulations/${runId}/pause`, { method: 'POST' })
  },
  resumeSimulation(runId: string) {
    return request<{ runId: string; status: string }>(`/simulations/${runId}/resume`, { method: 'POST' })
  },
  stopSimulation(runId: string) {
    return request<{ runId: string; status: string }>(`/simulations/${runId}/stop`, { method: 'POST' })
  },
  completeTaskPicking(runId: string, taskId: string) {
    return request<SimulationState>(`/simulations/${runId}/tasks/${taskId}/complete-picking`, { method: 'POST' })
  },
  resetSimulation(runId: string) {
    return request<{ runId: string; status: string }>(`/simulations/${runId}/reset`, { method: 'POST' })
  },
  setSimulationSpeed(runId: string, multiplier: 1 | 2 | 4) {
    return request<{ runId: string; status: string; speedMultiplier: 1 | 2 | 4 }>(`/simulations/${runId}/speed`, {
      method: 'POST',
      body: JSON.stringify({ multiplier }),
    })
  },
  getShuttleContract(topologyId: string) {
    return request<ShuttleContract>(`/shuttle/topologies/${topologyId}/contract`)
  },
  planShuttleTransfer(
    topologyId: string,
    startTrackNode: string,
    endTrackNode: string,
    algorithm: 'astar' | 'dijkstra',
  ) {
    return request<ShuttlePlan>('/shuttle/plan', {
      method: 'POST',
      body: JSON.stringify({ topologyId, startTrackNode, endTrackNode, algorithm }),
    })
  },
  cancelShuttleTransfer(runId: string, taskId: string) {
    return request<SimulationTask>(`/simulations/${runId}/transfers/${taskId}/cancel`, { method: 'POST' })
  },
  retryShuttleTransfer(runId: string, taskId: string) {
    return request<SimulationTask>(`/simulations/${runId}/transfers/${taskId}/retry`, { method: 'POST' })
  },
  getSimulationState(runId: string) {
    return request<SimulationState>(`/simulations/${runId}/state`)
  },
  getSimulationMetrics(runId: string) {
    return request<SimulationMetrics>(`/simulations/${runId}/metrics`)
  },
  getGoodsInventory(topologyId: string) {
    return request<GoodsInventory>(`/goods/inventory/${topologyId}`)
  },
  getGoodsCatalog() {
    return request<GoodsCatalog>('/goods/catalog')
  },
  updateShuttleDock(
    runId: string,
    dockId: string,
    patch: {
      skuId?: string | null
      skuName?: string | null
      largeCategory?: string | null
      quantity?: number
      priority?: 'normal' | 'urgent'
      vehicleId?: string | null
      loop?: boolean
      enabled?: boolean
    },
  ) {
    return request<{ runId: string; dockId: string }>(
      `/simulations/${runId}/shuttle-docks/${dockId}`,
      { method: 'PUT', body: JSON.stringify(patch) },
    )
  },
}
