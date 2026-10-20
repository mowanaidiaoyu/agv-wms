export interface InventorySku {
  sku_id: string
  sku_name: string
  category: string
  specification: string
  unit: string
  weight_kg: number | null
  material: string | null
  source: string
}

export interface InventoryBalance {
  topology_id: string
  zone_id: string | null
  storage_node_id: string
  storage_layer: number
  sku: InventorySku
  quantity_on_hand: number
  quantity_reserved: number
  quantity_available: number
  source: string
  updated_at: string
}

export interface StorageAccessMapping {
  topology_id: string
  storage_node_id: string
  access_cell_id: string | null
  status: 'configured' | 'unconfigured' | 'topology_version_mismatch' | 'invalid'
  source: string | null
  validated_at: string | null
  validation_message?: string | null
}

export interface StorageAccessCandidate {
  topology_id: string
  topology_version: string
  storage_node_id: string
  access_cell_id: string
  floor: string
  row: number | null
  col: number | null
  zone_id: string | null
  distance_cells: number | null
  status: 'eligible' | 'review_required' | 'rejected'
  reason_codes: string[]
  reasons: string[]
}

export interface StorageAccessCandidateList {
  topology_id: string
  topology_version: string
  storage_node_id: string
  eligible_count: number
  review_required_count: number
  rejected_count: number
  candidates: StorageAccessCandidate[]
}

export interface StorageAccessAudit {
  topology_id: string
  topology_version: string
  storage_location_count: number
  eligible_location_count: number
  review_required_location_count: number
  unmappable_location_count: number
  configured_mapping_count: number
  invalid_mapping_count: number
  reason_counts: Record<string, number>
}

export interface StorageAccessPathResult {
  topology_id: string
  topology_version: string
  storage_node_id: string
  access_cell_id: string
  algorithm: 'astar' | 'dijkstra'
  path_cells: Array<{ id: string; floor?: string; row: number; col: number; type: string }>
  total_distance: number
  total_cost: number
  visited_nodes: number
  execution_status: 'planning_preview_only'
}

export interface StorageInventoryLocation {
  topology_id: string
  topology_version: string
  storage_node_id: string
  label: string
  zone_id: string | null
  zone_label: string | null
  zone_type: string | null
  floor: string
  row: number
  col: number
  rack_level: number | null
  balances: InventoryBalance[]
  access_mapping: StorageAccessMapping
  data_sources: string[]
}

export interface InventorySummary {
  topology_id: string
  storage_location_count: number
  stocked_location_count: number
  positive_inventory_location_count: number
  unconfigured_location_count: number
  zero_inventory_location_count: number
  sku_count: number
  quantity_on_hand: number
  quantity_reserved: number
  quantity_available: number
  data_sources: string[]
}

export interface InventoryZoneSummary {
  topology_id: string
  zone_id: string
  zone_label: string
  zone_type: string
  storage_location_count: number
  stocked_location_count: number
  positive_inventory_location_count: number
  unconfigured_location_count: number
  zero_inventory_location_count: number
  sku_count: number
  quantity_on_hand: number
  quantity_reserved: number
  quantity_available: number
  data_sources: string[]
}

export interface SyntheticInventorySeedResult {
  topology_id: string
  seed: number
  inserted_skus: number
  inserted_balances: number
  source: 'synthetic_demo'
}

export interface SyntheticInventorySupplementPreview {
  topology_id: string
  eligible_location_count: number
  requested_location_count: number
  estimated_insert_count: number
  fill_all: boolean
  before: InventorySummary
}

export interface SyntheticInventorySupplementResult {
  topology_id: string
  seed: number
  fill_all: boolean
  requested_location_count: number
  eligible_before: number
  inserted_locations: number
  inserted_balances: number
  skipped_existing_locations: number
  skipped_due_to_limit: number
  source: 'synthetic_demo'
  before: InventorySummary
  after: InventorySummary
}
