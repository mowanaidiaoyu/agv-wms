import type {
  MultiPathDemoScenariosResponse,
  MultiPathPlan,
  MultiPathVehicleRequest,
  PathDemoAlgorithm,
  PathDemoPlan,
  PathDemoTopologyOptions,
} from './types'

const API_BASE = 'http://127.0.0.1:8000/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const payload = (await response.json()) as { detail?: unknown }
      if (typeof payload.detail === 'string') message = payload.detail
      else if (payload.detail) message = JSON.stringify(payload.detail)
    } catch {
      // Preserve the HTTP status when a non-JSON error is returned.
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}
export const pathDemoApi = {
  options(topologyId: string) {
    return request<PathDemoTopologyOptions>(`/path-demo/topologies/${topologyId}/options`)
  },
  plan(topologyId: string, startCellId: string, goalCellId: string, algorithm: PathDemoAlgorithm) {
    return request<PathDemoPlan>('/path-demo/plan', {
      method: 'POST',
      body: JSON.stringify({
        topology_id: topologyId,
        start_cell_id: startCellId,
        goal_cell_id: goalCellId,
        algorithm,
      }),
    })
  },
  multiScenarios(topologyId: string) {
    return request<MultiPathDemoScenariosResponse>(`/path-demo/topologies/${topologyId}/multi-scenarios`)
  },
  planMulti(topologyId: string, vehicles: MultiPathVehicleRequest[], algorithm: PathDemoAlgorithm) {
    return request<MultiPathPlan>('/path-demo/plan-multi', {
      method: 'POST',
      body: JSON.stringify({
        topology_id: topologyId,
        vehicles,
        algorithm,
        planning_config: {
          time_step_seconds: 0.25,
          speed_cells_per_second: 4,
          max_time_steps: 512,
          max_expanded_states: 200000,
          vehicle_width_cells: 0.58,
          vehicle_length_cells: 0.72,
          safety_margin_cells: 0.05,
        },
      }),
    })
  },
}
