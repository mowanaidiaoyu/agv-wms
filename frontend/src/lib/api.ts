import type {
  AlgorithmEvaluationResult,
  ScenarioPayload,
  SimulationEvent,
  SimulationMetrics,
  SimulationRun,
  SimulationState,
  StoredRecord,
  TopologyPayload,
  TopologyValidationResult,
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
    throw new Error(`${response.status} ${response.statusText}`)
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
  validateTopology(payload: TopologyPayload) {
    return request<TopologyValidationResult>('/topologies/validate', {
      method: 'POST',
      body: JSON.stringify(payload),
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
}
