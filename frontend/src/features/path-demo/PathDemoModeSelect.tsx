export type PathDemoMode =
  | 'shuttle_single'
  | 'shuttle_multi_preset'
  | 'shuttle_multi_custom'
  | 'shuttle_stock_transfer'
  | 'ctu_single'
  | 'ctu_multi_preset'
  | 'ctu_multi_custom'

const SHUTTLE_MODE_OPTIONS: Array<{ value: PathDemoMode; label: string }> = [
  { value: 'shuttle_single', label: '四向车单车轨迹（同层 / 跨层）' },
  { value: 'shuttle_multi_preset', label: '四向车预设多车（A/B/C）' },
  { value: 'shuttle_multi_custom', label: '四向车自定义多车（2–4 辆）' },
  { value: 'shuttle_stock_transfer', label: 'SKU 货物搬运（单车 / 多车）' },
]

const CTU_MODE_OPTIONS: Array<{ value: PathDemoMode; label: string }> = [
  { value: 'ctu_single', label: 'CTU 单车轨迹' },
  { value: 'ctu_multi_preset', label: 'CTU 预设多车场景（A/B/C）' },
  { value: 'ctu_multi_custom', label: 'CTU 自定义多车（1–6 辆）' },
]

export function PathDemoModeSelect({
  value,
  onChange,
}: {
  value: PathDemoMode
  onChange: (mode: PathDemoMode) => void
}) {
  const options = value.startsWith('shuttle_') ? SHUTTLE_MODE_OPTIONS : CTU_MODE_OPTIONS
  return (
    <label>
      演示模式
      <select
        aria-label="演示模式"
        value={value}
        onChange={(event) => onChange(event.target.value as PathDemoMode)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </label>
  )
}
