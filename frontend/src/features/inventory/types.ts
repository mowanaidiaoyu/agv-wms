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
  status: 'configured' | 'unconfigured' | 'topology_version_mismatch'
  source: string | null
  validated_at: string | null
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
