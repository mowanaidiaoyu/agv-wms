import type {
  AlgorithmEvaluationResult,
  GoodsCatalog,
  GoodsInventory,
  ScenarioPayload,
  SimulationEvent,
  SimulationMetrics,
  SimulationRun,
  SimulationState,
  StoredRecord,
  TopologyPayload,
} from '../types'

const API_BASE = 'http://127.0.0.1:8000/api/v1'
const WS_BASE = 'ws://127.0.0.1:8000/ws'

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
      const payload = (await response.json()) as { detail?: string | string[] | Record<string, unknown> }
      if (typeof payload.detail === 'string') {
        message = payload.detail
      } else if (Array.isArray(payload.detail)) {
        message = payload.detail.join(' ')
      } else if (payload.detail) {
        message = JSON.stringify(payload.detail)
      }
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
  updateTopology(payload: TopologyPayload) {
    return request<StoredRecord<TopologyPayload>>(`/topologies/${payload.id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  },
  deleteTopology(topologyId: string) {
    return request<StoredRecord<TopologyPayload>>(`/topologies/${topologyId}`, {
      method: 'DELETE',
    })
  },
  listScenarios() {
    return request<Array<StoredRecord<ScenarioPayload>>>('/scenarios')
  },
  getScenario(id: string) {
    return request<StoredRecord<ScenarioPayload>>(`/scenarios/${id}`)
  },
  saveScenario(payload: ScenarioPayload) {
    return request<StoredRecord<ScenarioPayload>>('/scenarios', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
  startSimulation(topologyId: string, scenarioId: string) {
    return request<{ runId: string; status: string }>('/simulations/start', {
      method: 'POST',
      body: JSON.stringify({ topologyId, scenarioId }),
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
  getSimulationState(runId: string) {
    return request<SimulationState>(`/simulations/${runId}/state`)
  },
  getSimulationMetrics(runId: string) {
    return request<SimulationMetrics>(`/simulations/${runId}/metrics`)
  },
  getSimulationEvents(runId: string) {
    return request<SimulationEvent[]>(`/simulations/${runId}/events`)
  },
  listSimulationRuns() {
    return request<SimulationRun[]>('/simulations')
  },
  evaluateAlgorithms(topologyId: string, scenarioId: string) {
    return request<{ results: AlgorithmEvaluationResult[] }>('/algorithms/evaluate', {
      method: 'POST',
      body: JSON.stringify({ topologyId, scenarioId }),
    })
  },
  getGoodsCatalog() {
    return request<GoodsCatalog>('/goods/catalog')
  },
  getGoodsInventory(topologyId: string) {
    return request<GoodsInventory>(`/goods/inventory/${topologyId}`)
  },
}
