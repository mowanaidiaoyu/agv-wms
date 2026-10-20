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

export interface CtuRoadSemanticCell {
  road_cell_id: string
  floor: string
  row: number
  col: number
  zone_id: string | null
  road_class: 'ctu' | 'shuttle' | 'unassigned' | 'other' | 'invalid'
  traversability: 'walkable' | 'blocked'
  component_id: string
  component_size: number
  eligible_for_ctu: boolean
}

export interface CtuTrialArea {
  draft_key: string
  status: 'UNVERIFIED'
  review_status: 'draft'
  vehicle_type: 'ctu'
  floor: string
  zone_ids: string[]
  road_class: string
  road_cell_ids: string[]
  road_component_id: string
  vehicle_start_cell_id: string
  storage_node_ids: string[]
  pick_face: 'unknown'
  access_cell_id: null
  mapping_source: 'topology_audit_draft'
  eligible_for_planning: false
  reasons: string[]
}

export interface CtuRoadSemanticAudit {
  topology_id: string
  topology_revision: string
  vehicle_type: 'ctu'
  render_contract: {
    road_cell_color: string
    ctu_zone_geometry_color: string
    shuttle_aisle_color: string
    color_grants_traversability: boolean
  }
  road_counts: Record<'total' | 'ctu' | 'shuttle' | 'unassigned' | 'other' | 'invalid', number>
  road_components: Record<string, number>
  road_cells: CtuRoadSemanticCell[]
  ctu_road_overlap_storage_count: number
  ctu_road_eligible_count: number
  storage_node_count: number
  ctu_storage_node_count: number
  stable_ctu_storage_geometry_count: number
  pick_face_declared_count: number
  ctu_vehicle_footprint_declared: boolean
  formal_access_cell_count: number
  trial_area: CtuTrialArea | null
  warnings: string[]
  saved_draft_count: number
}

export interface CtuTrialDraft {
  contract_version: 'ctu-road-pick-contract/v1'
  draft_id: string
  topology_id: string
  topology_revision: string
  zone_id: string | null
  zone_ids: string[]
  road_cell_ids: string[]
  vehicle_type: 'ctu'
  floor: string
  traversability: 'unverified'
  road_connectivity: { component_id: string; cell_count: number }
  storage_node_ids: string[]
  pick_face: 'unknown'
  access_cell_id: null
  vehicle_start_cell_id: string
  mapping_source: 'topology_audit_draft'
  review_status: 'draft' | 'verified' | 'rejected'
  eligible_for_planning: false
  reviewer_required: 'researcher_b'
  note: string | null
  reasons: string[]
  created_at: string
  updated_at: string
  version_status: 'current' | 'topology_version_mismatch'
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
