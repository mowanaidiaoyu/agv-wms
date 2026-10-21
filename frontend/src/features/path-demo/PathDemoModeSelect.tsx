export type PathDemoMode =
  | 'shuttle_single'
  | 'ctu_single'
  | 'ctu_multi_preset'
  | 'ctu_multi_custom'

const PATH_DEMO_MODE_OPTIONS: Array<{ value: PathDemoMode; label: string }> = [
  { value: 'shuttle_single', label: '四向车轨迹（同层 / 跨层）' },
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
  return (
    <label>
      演示模式
      <select
        aria-label="演示模式"
        value={value}
        onChange={(event) => onChange(event.target.value as PathDemoMode)}
      >
        {PATH_DEMO_MODE_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </label>
  )
}
