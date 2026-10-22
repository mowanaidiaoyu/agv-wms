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
import type {
  ABCResponse,
  HeatPredictionRequest,
  HeatPredictionResponse,
} from '../features/sku-heat/types'
import type {
  CtuRoadSemanticAudit,
  CtuTrialDraft,
  InventorySummary,
  InventoryZoneSummary,
  StorageAccessAudit,
  StorageAccessCandidateList,
  StorageAccessMapping,
  StorageAccessPathResult,
  StorageInventoryLocation,
  SyntheticInventorySeedResult,
  SyntheticInventorySupplementPreview,
  SyntheticInventorySupplementResult,
} from '../features/inventory/types'
import type {
  ShuttleLiftsResponse,
  ShuttleMultiPlanRequest,
  ShuttleMultiPlanResponse,
  ShuttleMultiScenariosResponse,
  ShuttlePlanRequest,
  ShuttlePlanResponse,
  ShuttleStorageAuditResponse,
  ShuttleStorageCandidate,
  ShuttleStorageCandidatePage,
  ShuttleStorageLocationRecord,
  ShuttleStorageReviewHistory,
  ShuttleStorageTrialArea,
  ShuttleDemoDropoffRecord,
  ShuttlePickFace,
  ShuttleTopologyLevelsResponse,
  ShuttleTracksResponse,
  ShuttleDemoInventoryInitializeResult,
  ShuttleDemoLocationInventory,
  ShuttleDemoLocationPage,
  ShuttleDemoSkuSummary,
  ShuttleTransferTask,
  ShuttleMultiTransferBatch,
  ShuttleMultiTransferVehicleRequest,
} from '../features/shuttle-lift/types'

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
  getSkuAbc(asOfDate: string, lookbackDays: number, signal?: AbortSignal) {
    const query = new URLSearchParams({ as_of_date: asOfDate, lookback_days: String(lookbackDays) })
    return request<ABCResponse>(`/algorithm/sku/abc?${query}`, { signal })
  },
  predictSkuHeat(payload: HeatPredictionRequest, signal?: AbortSignal) {
    return request<HeatPredictionResponse>('/algorithm/sku/heat/predict', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal,
    })
  },
  getInventorySummary(topologyId: string) {
    const query = new URLSearchParams({ topology_id: topologyId })
    return request<InventorySummary>(`/inventory/summary?${query}`)
  },
  listInventoryZones(topologyId: string) {
    const query = new URLSearchParams({ topology_id: topologyId })
    return request<InventoryZoneSummary[]>(`/inventory/zones?${query}`)
  },
  listInventoryLocations(topologyId: string, zoneId?: string, skuId?: string, search?: string) {
    const query = new URLSearchParams({ topology_id: topologyId })
    if (zoneId) query.set('zone_id', zoneId)
    if (skuId) query.set('sku_id', skuId)
    if (search) query.set('search', search)
    return request<StorageInventoryLocation[]>(`/inventory/locations?${query}`)
  },
  getInventoryLocation(topologyId: string, storageNodeId: string) {
    const query = new URLSearchParams({ topology_id: topologyId })
    return request<StorageInventoryLocation>(
      `/inventory/locations/${encodeURIComponent(storageNodeId)}?${query}`,
    )
  },
  getInventorySku(topologyId: string, skuId: string) {
    const query = new URLSearchParams({ topology_id: topologyId })
    return request(`/inventory/sku/${encodeURIComponent(skuId)}?${query}`)
  },
  initializeSyntheticInventory(topologyId: string, seed = 42) {
    return request<SyntheticInventorySeedResult>('/inventory/synthetic-demo', {
      method: 'POST',
      body: JSON.stringify({ topology_id: topologyId, seed }),
    })
  },
  previewSyntheticInventorySupplement(topologyId: string, count: number | null, fillAll: boolean) {
    const query = new URLSearchParams({ topology_id: topologyId, fill_all: String(fillAll) })
    if (count !== null) query.set('count', String(count))
    return request<SyntheticInventorySupplementPreview>(`/inventory/synthetic-demo/supplement/preview?${query}`)
  },
  supplementSyntheticInventory(topologyId: string, count: number | null, fillAll: boolean, seed = 42) {
    return request<SyntheticInventorySupplementResult>('/inventory/synthetic-demo/supplement', {
      method: 'POST',
      body: JSON.stringify({ topology_id: topologyId, count, fill_all: fillAll, seed }),
    })
  },
  getStorageAccessAudit(topologyId: string) {
    return request<StorageAccessAudit>(`/inventory/access-mappings/audit?topology_id=${encodeURIComponent(topologyId)}`)
  },
  getCtuRoadSemanticAudit(topologyId: string) {
    return request<CtuRoadSemanticAudit>(`/inventory/ctu-road-audit?topology_id=${encodeURIComponent(topologyId)}`)
  },
  listCtuRoadDrafts(topologyId: string) {
    return request<CtuTrialDraft[]>(`/inventory/ctu-road-drafts?topology_id=${encodeURIComponent(topologyId)}`)
  },
  createCtuRoadDraft(topologyId: string, note: string | null = null) {
    return request<CtuTrialDraft>('/inventory/ctu-road-drafts', {
      method: 'POST',
      body: JSON.stringify({ topology_id: topologyId, note }),
    })
  },
  getStorageAccessCandidates(topologyId: string, storageNodeId: string, limit = 12) {
    const query = new URLSearchParams({ topology_id: topologyId, limit: String(limit) })
    return request<StorageAccessCandidateList>(
      `/inventory/access-mappings/${encodeURIComponent(storageNodeId)}/candidates?${query}`,
    )
  },
  configureStorageAccessMapping(topologyId: string, storageNodeId: string, accessCellId: string) {
    return request<StorageAccessMapping>(`/inventory/access-mappings/${encodeURIComponent(storageNodeId)}`, {
      method: 'PUT',
      body: JSON.stringify({
        topology_id: topologyId,
        access_cell_id: accessCellId,
        source: 'manual_verified',
        reviewed: true,
      }),
    })
  },
  planStorageAccessPath(
    topologyId: string,
    storageNodeId: string,
    startCellId: string,
    algorithm: 'astar' | 'dijkstra',
  ) {
    return request<StorageAccessPathResult>('/inventory/access-path/plan', {
      method: 'POST',
      body: JSON.stringify({
        topology_id: topologyId,
        storage_node_id: storageNodeId,
        start_cell_id: startCellId,
        algorithm,
      }),
    })
  },
  getShuttleLevels(topologyId: string) {
    return request<ShuttleTopologyLevelsResponse>(`/shuttle/topologies/${encodeURIComponent(topologyId)}/levels`)
  },
  getShuttleTracks(topologyId: string, level?: 1 | 2 | 3 | 4) {
    const query = level ? `?level=${level}` : ''
    return request<ShuttleTracksResponse>(`/shuttle/topologies/${encodeURIComponent(topologyId)}/tracks${query}`)
  },
  getShuttleLifts(topologyId: string) {
    return request<ShuttleLiftsResponse>(`/shuttle/topologies/${encodeURIComponent(topologyId)}/lifts`)
  },
  getShuttleStorageAudit(topologyId: string) {
    return request<ShuttleStorageAuditResponse>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/storage-audit`,
    )
  },
  getShuttleStorageCandidates(
    topologyId: string,
    options: { level?: number; search?: string; hasAdjacentTrack?: boolean; limit?: number } = {},
  ) {
    const query = new URLSearchParams()
    if (options.level) query.set('level', String(options.level))
    if (options.search) query.set('search', options.search)
    if (options.hasAdjacentTrack !== undefined) query.set('has_adjacent_track', String(options.hasAdjacentTrack))
    query.set('limit', String(options.limit ?? 100))
    return request<ShuttleStorageCandidatePage>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/storage-candidates?${query}`,
    )
  },
  getShuttleStorageCandidate(topologyId: string, locationId: string) {
    return request<ShuttleStorageCandidate>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/storage-candidates/${encodeURIComponent(locationId)}`,
    )
  },
  getShuttleStorageTrialArea(topologyId: string) {
    return request<ShuttleStorageTrialArea>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/storage-trial-area`,
    )
  },
  listShuttleStorageMappings(topologyId: string) {
    return request<ShuttleStorageLocationRecord[]>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/storage-mappings`,
    )
  },
  saveShuttleStorageDraft(
    topologyId: string,
    payload: { location_id: string; pick_face: ShuttlePickFace; access_track_node_id: string; reviewer: string; review_note?: string },
  ) {
    return request<ShuttleStorageLocationRecord>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/storage-mappings/drafts`,
      { method: 'POST', body: JSON.stringify(payload) },
    )
  },
  reviewShuttleStorageMapping(
    topologyId: string,
    locationId: string,
    payload: { decision: 'simulation_approved' | 'rejected'; reviewer: string; review_note?: string; explicit_confirmation: boolean },
  ) {
    return request<ShuttleStorageLocationRecord>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/storage-mappings/${encodeURIComponent(locationId)}/review`,
      { method: 'POST', body: JSON.stringify(payload) },
    )
  },
  getShuttleStorageReviewHistory(topologyId: string, locationId: string) {
    return request<ShuttleStorageReviewHistory[]>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/storage-mappings/${encodeURIComponent(locationId)}/history`,
    )
  },
  listShuttleDemoDropoffs(topologyId: string) {
    return request<ShuttleDemoDropoffRecord[]>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/demo-dropoffs`,
    )
  },
  saveShuttleDemoDropoffDraft(
    topologyId: string,
    payload: { track_node_id: string; label: string; reviewer: string; review_note?: string },
  ) {
    return request<ShuttleDemoDropoffRecord>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/demo-dropoffs/drafts`,
      { method: 'POST', body: JSON.stringify(payload) },
    )
  },
  reviewShuttleDemoDropoff(
    topologyId: string,
    dropoffId: string,
    payload: { decision: 'simulation_approved' | 'rejected'; reviewer: string; review_note?: string; explicit_confirmation: boolean },
  ) {
    return request<ShuttleDemoDropoffRecord>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/demo-dropoffs/${encodeURIComponent(dropoffId)}/review`,
      { method: 'POST', body: JSON.stringify(payload) },
    )
  },
  planShuttlePath(payload: ShuttlePlanRequest) {
    return request<ShuttlePlanResponse>('/shuttle/plan', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
  getShuttleMultiScenarios(topologyId: string) {
    return request<ShuttleMultiScenariosResponse>(
      `/shuttle/multi-scenarios?topology_id=${encodeURIComponent(topologyId)}`,
    )
  },
  planShuttleMulti(payload: ShuttleMultiPlanRequest) {
    return request<ShuttleMultiPlanResponse>('/shuttle/plan-multi', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
  initializeShuttleDemoInventory(topologyId: string) {
    return request<ShuttleDemoInventoryInitializeResult>('/shuttle/demo-inventory/initialize', {
      method: 'POST',
      body: JSON.stringify({ topology_id: topologyId }),
    })
  },
  listShuttleDemoSkus(topologyId: string, search?: string) {
    const query = new URLSearchParams()
    if (search) query.set('search', search)
    const suffix = query.size ? `?${query}` : ''
    return request<ShuttleDemoSkuSummary[]>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/demo-inventory/skus${suffix}`,
    )
  },
  listShuttleDemoInventoryLocations(
    topologyId: string,
    options: { level?: number; skuId?: string; search?: string; transferEligible?: boolean; limit?: number } = {},
  ) {
    const query = new URLSearchParams({ limit: String(options.limit ?? 5000) })
    if (options.level) query.set('level', String(options.level))
    if (options.skuId) query.set('sku_id', options.skuId)
    if (options.search) query.set('search', options.search)
    if (options.transferEligible !== undefined) query.set('transfer_eligible', String(options.transferEligible))
    return request<ShuttleDemoLocationPage>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/demo-inventory/locations?${query}`,
    )
  },
  getShuttleDemoInventoryLocation(topologyId: string, locationId: string) {
    return request<ShuttleDemoLocationInventory>(
      `/shuttle/topologies/${encodeURIComponent(topologyId)}/demo-inventory/locations/${encodeURIComponent(locationId)}`,
    )
  },
  createShuttleDemoTransfer(payload: {
    topology_id: string
    source_location_id: string
    destination_location_id: string
    sku_id: string
    quantity: number
    algorithm: 'astar' | 'dijkstra'
    vehicle_id: string
    current_track_node_id?: string
  }) {
    return request<ShuttleTransferTask>('/shuttle/demo-transfers', {
      method: 'POST', body: JSON.stringify(payload),
    })
  },
  planStartShuttleDemoTransfer(taskId: string) {
    return request<ShuttleTransferTask>(`/shuttle/demo-transfers/${encodeURIComponent(taskId)}/plan-start`, { method: 'POST' })
  },
  getShuttleDemoTransfer(taskId: string) {
    return request<ShuttleTransferTask>(`/shuttle/demo-transfers/${encodeURIComponent(taskId)}`)
  },
  advanceShuttleDemoTransfer(taskId: string, elapsedSeconds: number) {
    return request<ShuttleTransferTask>(`/shuttle/demo-transfers/${encodeURIComponent(taskId)}/advance`, {
      method: 'POST', body: JSON.stringify({ elapsed_seconds: elapsedSeconds }),
    })
  },
  cancelShuttleDemoTransfer(taskId: string) {
    return request<ShuttleTransferTask>(`/shuttle/demo-transfers/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' })
  },
  createShuttleDemoTransferBatch(payload: {
    topology_id: string
    idempotency_key: string
    vehicle_requests: ShuttleMultiTransferVehicleRequest[]
    planning_options?: Record<string, unknown>
  }) {
    return request<ShuttleMultiTransferBatch>('/shuttle/demo-transfer-batches', {
      method: 'POST', body: JSON.stringify(payload),
    })
  },
  getShuttleDemoTransferBatch(batchId: string) {
    return request<ShuttleMultiTransferBatch>(`/shuttle/demo-transfer-batches/${encodeURIComponent(batchId)}`)
  },
  advanceShuttleDemoTransferBatch(batchId: string, elapsedSeconds: number) {
    return request<ShuttleMultiTransferBatch>(`/shuttle/demo-transfer-batches/${encodeURIComponent(batchId)}/advance`, {
      method: 'POST', body: JSON.stringify({ elapsed_seconds: elapsedSeconds }),
    })
  },
  cancelShuttleDemoTransferBatch(batchId: string) {
    return request<ShuttleMultiTransferBatch>(`/shuttle/demo-transfer-batches/${encodeURIComponent(batchId)}/cancel`, { method: 'POST' })
  },
  failShuttleDemoTransferBatch(batchId: string, reason: string) {
    return request<ShuttleMultiTransferBatch>(`/shuttle/demo-transfer-batches/${encodeURIComponent(batchId)}/fail`, {
      method: 'POST', body: JSON.stringify({ reason }),
    })
  },
}
