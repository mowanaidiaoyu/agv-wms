export type ABCClass = 'A' | 'B' | 'C'
export type HeatForecastMethod = 'moving_average' | 'weighted_moving_average'

export interface ABCConfig {
  lookback_days: number
  frequency_weight: number
  quantity_weight: number
  order_line_weight: number
  a_cumulative_share: number
  b_cumulative_share: number
}

export interface ABCResultItem {
  sku_id: string
  outbound_frequency: number
  outbound_quantity: number
  order_line_count: number
  frequency_normalized: number
  quantity_normalized: number
  order_line_normalized: number
  score: number
  abc_class: ABCClass
  updated_at: string
}

export interface ABCResponse {
  algorithm_version: string
  data_source: 'synthetic_demo'
  as_of_date: string
  lookback_start_date: string
  lookback_end_date: string
  config: ABCConfig
  items: ABCResultItem[]
}

export interface HeatPredictionRequest {
  sku_ids: string[]
  as_of_date: string
  method: HeatForecastMethod
  lookback_days: 7 | 14 | 30
  horizon_hours: 24
}

export interface HeatPredictionItem {
  sku_id: string
  predicted_heat: number
}

export interface HeatPredictionResponse {
  algorithm_version: string
  data_source: 'synthetic_demo'
  as_of_date: string
  forecast_date: string
  lookback_start_date: string | null
  lookback_end_date: string | null
  method: HeatForecastMethod
  lookback_days: number
  horizon_hours: 24
  predictions: HeatPredictionItem[]
}

export const SYNTHETIC_DEMO_START_DATE = '2026-01-01'
export const SYNTHETIC_DEMO_END_DATE = '2026-03-31'
