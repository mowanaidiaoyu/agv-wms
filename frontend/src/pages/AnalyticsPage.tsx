import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { Activity, ArrowLeft, BarChart3, Clock3, Database, FlaskConical, GitCompareArrows, Info, Route, TimerReset, TriangleAlert } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'
import type {
  AssignmentContentionMetrics,
  AlgorithmComparisonResult,
  AlgorithmExperimentRunSummary,
  ContractMetric,
  ConveyorCoordinationMetrics,
  DeadlockHotspot,
  DeadlockLifecycleMetric,
  DeadlockLifecycleMetrics,
  MetricHistoryPoint,
  MetricHistoryQuality,
  MotionDistanceMetricValue,
  MotionDistanceMetrics,
  RunPerformanceMetrics,
  PathAggregateGroup,
  PathPlanningMetrics,
  PathQuantileGroup,
  SyntheticAbcHeatResult,
  SyntheticEtaPredictionResult,
  SyntheticWaveOptimizationResult,
  TaskMetricSummary,
  ThroughputComparisonResult,
  ThroughputTrendPoint,
} from '../types'
import './AnalyticsPage.css'

type MetricEnvelope = { runId: string; metrics: ContractMetric[] }
type DisplayMetric = ContractMetric | DeadlockLifecycleMetric
type TrendKey = 'completedTasks' | 'pendingTasks' | 'blockedRobots' | 'conveyorQueueDepth' | 'bufferOccupancy' | 'conveyorUtilization'
type TrendSeries = { key: TrendKey; name: string; color: string }
type VehicleAssociation = { robotId: string; taskCount: number }

const reasonLabels: Record<string, string> = {
  NO_SAMPLES: '无有效样本',
  NO_VALID_DURATION_SAMPLES: '无有效耗时样本',
  NO_VALID_ASSIGNMENT_WAIT_SAMPLES: '无有效首次分配等待样本',
  INSUFFICIENT_SAMPLE: '样本不足',
  MISSING_SOURCE: '数据未采集',
  TASKS_SOURCE_MISSING: '任务数据源缺失',
  FIELD_NOT_PRESENT: '字段未采集',
  FIELD_MISSING: '字段未采集',
  NO_METRICS_SNAPSHOTS: '没有历史快照',
  EVENT_COLLECTION_UNCONFIRMED: '采集完整性未确认',
  COLLECTION_COMPLETENESS_UNCONFIRMED: '采集完整性未确认',
  SUCCESS_ONLY_SAMPLE_BIAS: '仅成功规划样本',
  VERSION_METADATA_MISSING: '版本元数据不完整',
  CONVEYOR_GRAPH_NODES_MISSING: '缺少输送节点总数数据',
  INVALID_CONVEYOR_GRAPH_NODES: '输送节点总数数据非法',
  NO_CONVEYOR_GRAPH: '当前 Run 没有可用输送图',
  CONVEYOR_UTILIZATION_MISSING: '未采集输送节点占用率',
}

function toMap(data: MetricEnvelope | undefined, runId: string): Map<string, ContractMetric> {
  if (!data || data.runId !== runId) return new Map()
  return new Map(data.metrics.map((item) => [item.metricId, item]))
}

function getMetric<T = unknown>(map: Map<string, ContractMetric>, id: string): ContractMetric<T> | undefined {
  return map.get(id) as ContractMetric<T> | undefined
}

function deadlockMetricMap(data: DeadlockLifecycleMetrics | undefined, runId: string): Map<string, DeadlockLifecycleMetric> {
  if (!data || data.runId !== runId) return new Map()
  return new Map(data.metrics.map((item) => [item.metricId, item]))
}

function getDeadlockMetric(map: Map<string, DeadlockLifecycleMetric>, id: string): DeadlockLifecycleMetric | undefined {
  return map.get(id)
}

function hasMetricValue(metric: DisplayMetric | undefined): metric is DisplayMetric {
  if (!metric || metric.value === null || metric.value === undefined) return false
  if ('implementationStatus' in metric && metric.implementationStatus === 'UNAVAILABLE') return false
  return typeof metric.value !== 'number' || Number.isFinite(metric.value)
}

function hasSampledMetricValue(metric: DisplayMetric | undefined): metric is DisplayMetric {
  return hasMetricValue(metric) && typeof metric.sampleCount === 'number' && metric.sampleCount > 0
}

function hasNumericValue(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function hasTrendData(points: MetricHistoryPoint[], keys: TrendKey[]): boolean {
  return points.reduce((count, point) => count + (keys.some((key) => hasNumericValue(point[key])) ? 1 : 0), 0) >= 2
}

function reasonText(reason: string | null | undefined): string {
  if (!reason) return '—'
  return `${reasonLabels[reason] ?? reason}（${reason}）`
}

function formatNumber(value: number, decimals = 0): string {
  if (!Number.isFinite(value)) return '—'
  return value.toLocaleString('zh-CN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

function metricDisplay(metric: DisplayMetric | undefined, decimals = 0): string {
  if (!metric) return '—'
  if ('implementationStatus' in metric && metric.implementationStatus === 'UNAVAILABLE') return '未实现'
  if (metric.value === null) return reasonLabels[metric.reason ?? ''] ?? '无可计算值'
  if (typeof metric.value === 'number') return formatNumber(metric.value, decimals)
  if (typeof metric.value === 'string') return metric.value
  return '结构化数据'
}

function MetricInfo({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details className="analytics-metric-info">
      <summary tabIndex={0} aria-label={`查看${label}说明`} title={`${label}说明`}><Info size={13} aria-hidden="true" /></summary>
      <div role="note">{children}</div>
    </details>
  )
}

function MetricLabel({ label, info }: { label: string; info?: ReactNode }) {
  return <><span>{label}</span>{info ? <MetricInfo label={label}>{info}</MetricInfo> : null}</>
}

function hasQualityConcern(metric: DisplayMetric | MotionDistanceMetricValue | undefined): boolean {
  return Boolean(metric && (metric.reason || !['GOOD', 'COMPLETE'].includes(metric.dataQuality ?? '')))
}

function MetricCard({ label, metric, source, decimals = 0, suffix }: {
  label: string
  metric?: DisplayMetric
  source: string
  decimals?: number
  suffix?: string
}) {
  if (!hasMetricValue(metric)) return null
  const rendered = metricDisplay(metric, decimals)
  const value = suffix
    ? `${rendered} ${suffix}`
    : rendered
  return (
    <article className="analytics-kpi" data-metric-id={metric?.metricId}>
      <MetricLabel label={label} info={source} />
      <strong>{value}</strong>
      {hasQualityConcern(metric) ? <small className="analytics-quality-warning" title={metric?.reason ?? undefined}>数据质量：{metric?.dataQuality ?? '未确认'}{metric?.reason ? ` · ${reasonText(metric.reason)}` : ''}</small> : null}
    </article>
  )
}

function ValueCard({ label, value, source, suffix }: {
  label: string
  value: number | null | undefined
  source: string
  suffix?: string
}) {
  if (!hasNumericValue(value)) return null
  return (
    <article className="analytics-kpi">
      <MetricLabel label={label} info={source} />
      <strong>{formatNumber(value)}{suffix ? ` ${suffix}` : ''}</strong>
    </article>
  )
}

function MiniMetric({ label, metric, decimals = 0, suffix, info }: {
  label: string
  metric?: DisplayMetric
  decimals?: number
  suffix?: string
  info?: ReactNode
}) {
  if (!hasMetricValue(metric)) return null
  return <div><MetricLabel label={label} info={info} /><b>{metricDisplay(metric, decimals)}{suffix ? ` ${suffix}` : ''}</b>{hasQualityConcern(metric) ? <small className="analytics-quality-warning">数据质量：{metric.dataQuality}</small> : null}</div>
}

function MiniValue({ label, value, suffix, info }: {
  label: string
  value: number | null | undefined
  suffix?: string
  info?: ReactNode
}) {
  if (!hasNumericValue(value)) return null
  return <div><MetricLabel label={label} info={info} /><b>{formatNumber(value)}{suffix ? ` ${suffix}` : ''}</b></div>
}

function DistanceCard({ label, metric }: { label: string; metric?: MotionDistanceMetricValue }) {
  if (!hasNumericValue(metric?.value)) return null
  return (
    <article className="analytics-kpi analytics-distance-kpi">
      <MetricLabel label={label} info={`来自真实 Run 的 ${metric.sampleCount} 个有效运动段。`} />
      <strong>{formatNumber(metric.value, 3)} m</strong>
      {hasQualityConcern(metric) ? <small className="analytics-quality-warning">数据质量：{metric.dataQuality}</small> : null}
    </article>
  )
}

function DeadlockHotspotTable({ hotspots }: { hotspots: DeadlockHotspot[] }) {
  const validHotspots = hotspots.filter((item) => (
    hasNumericValue(item.row)
    && hasNumericValue(item.col)
    && hasNumericValue(item.observedEpisodeCount)
    && item.observedEpisodeCount >= 0
    && hasNumericValue(item.involvedRobotCount)
    && item.sampleCount > 0
  ))
  if (!validHotspots.length) return null
  return (
    <div className="analytics-deadlock-hotspots">
      <h4 className="analytics-subhead"><TriangleAlert size={15} /> 阻塞热点</h4>
      <div className="analytics-table-wrap">
        <table>
          <thead><tr><th>二维网格坐标</th><th>楼层</th><th>持续 Episode</th><th>涉及车辆</th><th>质量</th></tr></thead>
          <tbody>{validHotspots.map((item) => <tr key={item.cellId}>
            <td>row {item.row} · col {item.col}</td>
            <td>{typeof item.floor === 'string' && item.floor.trim() ? item.floor : '未记录'}</td>
            <td>{formatNumber(item.observedEpisodeCount)}</td>
            <td>{formatNumber(item.involvedRobotCount)}</td>
            <td>{item.dataQuality}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </div>
  )
}

function TrendChart({ title, points, series, unit }: {
  title: string
  points: MetricHistoryPoint[]
  series: TrendSeries[]
  unit: string
}) {
  const values = points.flatMap((point) => series.flatMap(({ key }) => {
    const value = point[key]
    return typeof value === 'number' && Number.isFinite(value) ? [value] : []
  }))
  if (points.length < 2 || values.length < 2) {
    return null
  }
  const minValue = Math.min(0, ...values)
  const maxValue = Math.max(...values, minValue + 1)
  const x = (index: number) => 42 + (index / Math.max(1, points.length - 1)) * 676
  const y = (value: number) => 180 - ((value - minValue) / (maxValue - minValue)) * 144
  const seriesPath = (key: TrendKey) => {
    let drawing = false
    return points.map((point, index) => {
      const value = point[key]
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        drawing = false
        return ''
      }
      const command = drawing ? 'L' : 'M'
      drawing = true
      return `${command}${x(index)},${y(value)}`
    }).filter(Boolean).join(' ')
  }
  return (
    <div className="analytics-chart-card">
      <h3>{title}</h3>
      <svg viewBox="0 0 760 218" className="analytics-chart-svg" role="img" aria-label={`${title}，横轴为仿真 Tick`}>
        {[0, 0.5, 1].map((fraction) => {
          const value = minValue + (maxValue - minValue) * fraction
          const chartY = y(value)
          return <g key={fraction}><line x1="42" x2="718" y1={chartY} y2={chartY} stroke="#e0e7ef" strokeDasharray="3 5" /><text x="36" y={chartY + 4} textAnchor="end" fontSize="12" fill="#64748b">{Number(value.toFixed(1))}</text></g>
        })}
        {series.map(({ key, name, color }) => <path key={key} d={seriesPath(key)} fill="none" stroke={color} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" aria-label={name} />)}
        <text x="42" y="205" fontSize="12" fill="#64748b">Tick {points[0].tick}</text>
        <text x="718" y="205" textAnchor="end" fontSize="12" fill="#64748b">Tick {points[points.length - 1].tick}</text>
        <text x="718" y="16" textAnchor="end" fontSize="12" fill="#64748b">{unit}</text>
      </svg>
      <div className="analytics-chart-legend">{series.map(({ name, color }) => <span key={name}><i style={{ background: color }} />{name}</span>)}</div>
    </div>
  )
}

function AssignmentBars({ values }: { values: VehicleAssociation[] }) {
  if (!values.length) return null
  const max = Math.max(1, ...values.map((item) => item.taskCount))
  return (
    <div className="analytics-chart-card">
      <h3 className="analytics-heading-with-info">各 CTU 历史关联任务数 <MetricInfo label="CTU历史关联任务数">按车辆与任务的唯一关联统计，不代表设备利用率。</MetricInfo></h3>
      <div className="analytics-bars">{values.map((item) => (
          <div className="analytics-bar-row" key={item.robotId}>
            <span title={item.robotId}>{item.robotId}</span>
            <div className="analytics-bar-track"><div style={{ width: `${(item.taskCount / max) * 100}%` }} /></div>
            <b>{item.taskCount}</b>
          </div>
        ))}</div>
    </div>
  )
}

function QueryError({ name, error, retry }: { name: string; error: Error | null; retry: () => void }) {
  return (
    <div className="analytics-notice analytics-error" role="alert">
      <TriangleAlert size={18} />
      <span>{name}读取失败：{error?.message ?? '未知错误'}。未使用旧数据或 0 填补。</span>
      <button type="button" onClick={retry}>重试该查询</button>
    </div>
  )
}

function QualityBadge({ quality, reason }: { quality?: string; reason?: string | null }) {
  return <span className={`analytics-quality analytics-quality-${(quality ?? 'unknown').toLowerCase()}`}>{quality ?? '—'}{reason ? ` · ${reason}` : ''}</span>
}

function QuantilePanel({ groups }: { groups: PathQuantileGroup[] }) {
  if (!groups.length) return null
  return <div className="analytics-quantile-list">{groups.map((entry, index) => (
    <div className="analytics-quantile-group" key={`${entry.group.algorithm}-${entry.group.topologyRevision ?? 'unknown'}-${index}`}>
      <h5>{entry.group.algorithm.toUpperCase()} · {entry.sampleCount} 个成功样本</h5>
      <div className="analytics-quantiles">{(['p50', 'p90', 'p99'] as const).map((key) => {
        const item = entry.quantiles[key]
        return <div key={key}><span>{key.toUpperCase()}</span><strong>{item.value === null ? `样本不足 ${item.actualSampleCount}/${item.requiredSampleCount}` : `${formatNumber(item.value, 2)} ms`}</strong></div>
      })}</div>
      <QualityBadge quality={entry.dataQuality} reason={entry.reason} />
    </div>
  ))}</div>
}

function aggregateRows(metric: ContractMetric | undefined): PathAggregateGroup[] {
  return Array.isArray(metric?.value) ? metric.value as PathAggregateGroup[] : []
}

type SyntheticBarValue = { label: string; value: number | null; color: string }

function SyntheticBars({ title, values, unit, decimals = 2 }: {
  title: string
  values: SyntheticBarValue[]
  unit: string
  decimals?: number
}) {
  const finiteValues = values.flatMap((item) => typeof item.value === 'number' && Number.isFinite(item.value) ? [item.value] : [])
  const maximum = Math.max(1, ...finiteValues)
  return (
    <div className="analytics-chart-card synthetic-chart-card">
      <h3>{title}</h3>
      <div className="synthetic-comparison-bars">
        {values.map((item) => (
          <div className="synthetic-comparison-row" key={item.label}>
            <span>{item.label}</span>
            <div className="synthetic-comparison-track">
              {item.value === null ? null : <i style={{ width: `${Math.max(0, item.value / maximum) * 100}%`, background: item.color }} />}
            </div>
            <b>{item.value === null ? '—' : `${formatNumber(item.value, decimals)} ${unit}`}</b>
          </div>
        ))}
      </div>
      <small className="synthetic-chart-source">数据来源：静态实验 JSON 聚合字段；未根据预览样本重新计算</small>
    </div>
  )
}

function ForecastLineChart({ data }: { data: SyntheticAbcHeatResult['skuForecasts'][number] | undefined }) {
  if (!data?.predictions.length) return <div className="analytics-chart-card"><h3>单 SKU 14天滚动预测</h3><p className="analytics-empty-chart">无可展示预测样本</p></div>
  const values = data.predictions.flatMap((item) => [item.actual, item.sma7, item.wma7])
  const maximum = Math.max(1, ...values)
  const x = (index: number) => 42 + (index / Math.max(1, data.predictions.length - 1)) * 676
  const y = (value: number) => 178 - (value / maximum) * 142
  const path = (key: 'actual' | 'sma7' | 'wma7') => data.predictions.map((item, index) => `${index ? 'L' : 'M'}${x(index)},${y(item[key])}`).join(' ')
  return (
    <div className="analytics-chart-card synthetic-chart-card">
      <h3>{data.skuId} · 实际需求与逐日预测</h3>
      <svg viewBox="0 0 760 218" className="analytics-chart-svg" role="img" aria-label={`${data.skuId} 14天实际需求、SMA7与WMA7滚动预测折线图`}>
        {[0, 0.5, 1].map((fraction) => <g key={fraction}><line x1="42" x2="718" y1={y(maximum * fraction)} y2={y(maximum * fraction)} stroke="#e0e7ef" strokeDasharray="3 5" /><text x="36" y={y(maximum * fraction) + 4} textAnchor="end" fontSize="12" fill="#64748b">{formatNumber(maximum * fraction, 0)}</text></g>)}
        <path d={path('actual')} fill="none" stroke="#0f766e" strokeWidth="2.5" />
        <path d={path('sma7')} fill="none" stroke="#2563eb" strokeWidth="2" />
        <path d={path('wma7')} fill="none" stroke="#ea580c" strokeWidth="2" />
        <text x="42" y="205" fontSize="12" fill="#64748b">Day {data.predictions[0].dayIndex}</text>
        <text x="718" y="205" textAnchor="end" fontSize="12" fill="#64748b">Day {data.predictions.at(-1)?.dayIndex}</text>
      </svg>
      <div className="analytics-chart-legend"><span><i style={{ background: '#0f766e' }} />实际需求</span><span><i style={{ background: '#2563eb' }} />SMA7</span><span><i style={{ background: '#ea580c' }} />WMA7</span></div>
    </div>
  )
}

function EtaScatterChart({ predictions }: { predictions: SyntheticEtaPredictionResult['predictions'] }) {
  const validPredictions = predictions.filter((item) => Number.isFinite(item.actualDurationTicks) && Number.isFinite(item.modelPredictedTicks))
  if (!validPredictions.length) return <div className="analytics-chart-card"><h3>实际耗时与 Ridge 预测耗时</h3><p className="analytics-empty-chart">无合法测试样本</p></div>

  const plot = { left: 62, right: 722, top: 24, bottom: 246 }
  const dataMaximum = Math.max(1, ...validPredictions.flatMap((item) => [item.actualDurationTicks, item.modelPredictedTicks]))
  const axisMaximum = Math.ceil(dataMaximum / 10) * 10
  const x = (value: number) => plot.left + (value / axisMaximum) * (plot.right - plot.left)
  const y = (value: number) => plot.bottom - (value / axisMaximum) * (plot.bottom - plot.top)
  const ticks = [0, 0.25, 0.5, 0.75, 1]
  const coordinateCounts = new Map<string, number>()
  validPredictions.forEach((item) => {
    const key = `${item.actualDurationTicks}\u0000${item.modelPredictedTicks}`
    coordinateCounts.set(key, (coordinateCounts.get(key) ?? 0) + 1)
  })
  const overlappingSampleCount = [...coordinateCounts.values()].reduce((total, count) => total + Math.max(0, count - 1), 0)
  return (
    <div className="analytics-chart-card synthetic-chart-card">
      <h3>实际耗时与 Ridge 预测耗时 <small>n={validPredictions.length}</small></h3>
      <svg
        viewBox="0 0 760 300"
        preserveAspectRatio="xMidYMid meet"
        className="analytics-chart-svg eta-scatter-chart"
        role="img"
        aria-label={`ETA实际耗时与Ridge预测耗时散点图，单位Tick，n=${validPredictions.length}`}
        data-testid="eta-scatter-chart"
        data-axis-min="0"
        data-axis-max={axisMaximum}
      >
        {ticks.map((fraction) => {
          const value = axisMaximum * fraction
          const tickX = x(value)
          const tickY = y(value)
          return <g key={fraction}>
            <line x1={tickX} x2={tickX} y1={plot.top} y2={plot.bottom} stroke="#e2e8f0" strokeDasharray="3 5" />
            <line x1={plot.left} x2={plot.right} y1={tickY} y2={tickY} stroke="#e2e8f0" strokeDasharray="3 5" />
            <text x={tickX} y="267" textAnchor="middle" fontSize="11" fill="#64748b">{formatNumber(value, 0)}</text>
            <text x="54" y={tickY + 4} textAnchor="end" fontSize="11" fill="#64748b">{formatNumber(value, 0)}</text>
          </g>
        })}
        <line x1={plot.left} x2={plot.right} y1={plot.bottom} y2={plot.bottom} stroke="#64748b" />
        <line x1={plot.left} x2={plot.left} y1={plot.top} y2={plot.bottom} stroke="#64748b" />
        <line data-testid="eta-equality-line" x1={x(0)} y1={y(0)} x2={x(axisMaximum)} y2={y(axisMaximum)} stroke="#94a3b8" strokeWidth="1.5" strokeDasharray="5 5" />
        {validPredictions.map((item) => <circle
          key={item.taskId}
          className="eta-scatter-point"
          data-task-id={item.taskId}
          data-actual={item.actualDurationTicks}
          data-predicted={item.modelPredictedTicks}
          cx={x(item.actualDurationTicks)}
          cy={y(item.modelPredictedTicks)}
          r="4"
          fill="#0f766e"
          fillOpacity=".48"
          stroke="#0b5f59"
          strokeOpacity=".55"
          strokeWidth=".7"
          tabIndex={0}
        ><title>{item.taskId}：实际 {formatNumber(item.actualDurationTicks)} Tick，预测 {formatNumber(item.modelPredictedTicks, 2)} Tick</title></circle>)}
        <text x={(plot.left + plot.right) / 2} y="292" textAnchor="middle" fontSize="12" fill="#475569">实际耗时（Tick）</text>
        <text x="16" y={(plot.top + plot.bottom) / 2} textAnchor="middle" fontSize="12" fill="#475569" transform={`rotate(-90 16 ${(plot.top + plot.bottom) / 2})`}>Ridge 预测耗时（Tick）</text>
      </svg>
      <small className="synthetic-chart-source">全部 {validPredictions.length} 个合法点直接来自 predictions；虚线表示实际值=预测值。精确重合的额外样本：{overlappingSampleCount}。</small>
      <details className="eta-scatter-details">
        <summary>查看 {validPredictions.length} 条样本详情</summary>
        <div className="analytics-table-wrap"><table><thead><tr><th>taskId</th><th>taskType</th><th>实际耗时</th><th>Ridge 预测耗时</th><th>绝对误差</th></tr></thead><tbody>{validPredictions.map((item) => <tr key={item.taskId}><td>{item.taskId}</td><td>{item.taskType}</td><td>{formatNumber(item.actualDurationTicks, 2)} Tick</td><td>{formatNumber(item.modelPredictedTicks, 2)} Tick</td><td>{formatNumber(item.modelAbsoluteErrorTicks, 2)} Tick</td></tr>)}</tbody></table></div>
      </details>
    </div>
  )
}

function SyntheticError({ name, error, retry }: { name: string; error: Error | null; retry: () => void }) {
  return <div className="analytics-notice analytics-error" role="alert"><TriangleAlert size={18} /><span>{name}加载失败：{error?.message ?? '未知错误'}。未以 0 或硬编码参考值替代。</span><button type="button" onClick={retry}>重试</button></div>
}

function SyntheticAbcSection({ data }: { data: SyntheticAbcHeatResult }) {
  const latest = data.latestAbcSummary
  const forecast = data.forecastEvaluation
  const featuredSku = data.skuForecasts.find((item) => item.skuId === latest.topSkus[0]?.skuId) ?? data.skuForecasts[0]
  return (
    <section className="synthetic-section" aria-labelledby="synthetic-abc-title">
      <div className="analytics-section-head"><BarChart3 size={18} /><h2 id="synthetic-abc-title">6.1 动态 ABC 与次日热度预测</h2><small>{data.algorithmVersion}</small></div>
      <div className="analytics-mini-grid synthetic-summary-grid">
        {(['A', 'B', 'C'] as const).map((key) => <div key={key}><span>{key} 类 SKU</span><b>{latest.classCounts[key]} 个</b><small>历史需求价值占比 {formatNumber(latest.classHistoricalDemandValueShare[key] * 100, 4)}%</small></div>)}
        <div><span>滚动评测</span><b>{forecast.SMA7.evaluationWindow.days} 天</b><small>{forecast.SMA7.totalPredictionCount} 条预测；{forecast.SMA7.backtestStrategy}</small></div>
      </div>
      <div className="analytics-chart-grid">
        <SyntheticBars title="ABC 分类数量" unit="个" decimals={0} values={(['A', 'B', 'C'] as const).map((key, index) => ({ label: `${key} 类`, value: latest.classCounts[key], color: ['#dc2626', '#d97706', '#2563eb'][index] }))} />
        <SyntheticBars title="ABC 历史需求价值占比" unit="%" decimals={4} values={(['A', 'B', 'C'] as const).map((key, index) => ({ label: `${key} 类`, value: latest.classHistoricalDemandValueShare[key] * 100, color: ['#dc2626', '#d97706', '#2563eb'][index] }))} />
        <SyntheticBars title="预测 MAE（需求数量）" unit="" decimals={6} values={[{ label: 'SMA7', value: forecast.SMA7.mae, color: '#2563eb' }, { label: 'WMA7', value: forecast.WMA7.mae, color: '#ea580c' }]} />
        <SyntheticBars title="预测 MAPE（独立百分比尺度）" unit="%" decimals={6} values={[{ label: 'SMA7', value: forecast.SMA7.mape, color: '#2563eb' }, { label: 'WMA7', value: forecast.WMA7.mape, color: '#ea580c' }]} />
        <ForecastLineChart data={featuredSku} />
        <div className="analytics-chart-card synthetic-chart-card">
          <h3>动态分类快照变化</h3>
          <div className="synthetic-snapshot-list">{data.abcSnapshots.map((snapshot) => <div key={snapshot.asOfDayIndex}><b>Day {snapshot.asOfDayIndex}</b><span>A/B/C：{snapshot.classCounts.A}/{snapshot.classCounts.B}/{snapshot.classCounts.C}</span><small>{snapshot.changeFromPreviousSnapshot.changedSkuCount === null ? snapshot.changeFromPreviousSnapshot.reason : `较前次变化 ${snapshot.changeFromPreviousSnapshot.changedSkuCount} 个 SKU`}</small></div>)}</div>
        </div>
      </div>
      <h3 className="analytics-subhead">最新 ABC Top SKU（Day {latest.asOfDayIndex}）</h3>
      <div className="analytics-table-wrap"><table><thead><tr><th>SKU</th><th>分类</th><th>历史需求量</th><th>历史需求价值</th><th>贡献率</th></tr></thead><tbody>{latest.topSkus.map((sku) => <tr key={sku.skuId}><td>{sku.skuId}</td><td>{sku.abcClass}</td><td>{sku.historicalDemandUnits}</td><td>{formatNumber(sku.historicalDemandValue, 2)}</td><td>{formatNumber(sku.contributionRate * 100, 4)}%</td></tr>)}</tbody></table></div>
      <p className="analytics-data-note">Day 76 的 ABC 快照包含当天已观察需求；Day 76 的次日代理预测仅使用 Day 69–75 的历史。预测粒度为“次日需求数量（24小时代理）”，不表示小时级预测能力。</p>
    </section>
  )
}

function SyntheticWaveSection({ data }: { data: SyntheticWaveOptimizationResult }) {
  const input = data.dataQuality.input
  const details = [...data.waveDetails.baseline, ...data.waveDetails.optimized]
  return (
    <section className="synthetic-section" aria-labelledby="synthetic-wave-title">
      <div className="analytics-section-head"><GitCompareArrows size={18} /><h2 id="synthetic-wave-title">6.2 波次聚类与组合优化</h2><small>{data.algorithmVersion}</small></div>
      <div className="analytics-mini-grid synthetic-summary-grid">
        <div><span>输入订单</span><b>{input.orderCount} 笔</b></div><div><span>订单明细</span><b>{input.lineCount} 行</b></div><div><span>商品数量</span><b>{input.quantityCount} 件</b></div><div><span>代理距离改善率</span><b>{data.comparison.distanceImprovementRate === null ? '—' : `${formatNumber(data.comparison.distanceImprovementRate, 6)}%`}</b></div>
      </div>
      <div className="analytics-chart-grid">
        <SyntheticBars title="波次数比较" unit="波" decimals={0} values={[{ label: data.baseline.algorithm, value: data.baseline.waveCount, color: '#64748b' }, { label: data.optimized.algorithm, value: data.optimized.waveCount, color: '#0f766e' }]} />
        <SyntheticBars title="二维曼哈顿代理距离" unit="cell" decimals={0} values={[{ label: data.baseline.algorithm, value: data.baseline.totalDistanceCells, color: '#64748b' }, { label: data.optimized.algorithm, value: data.optimized.totalDistanceCells, color: '#0f766e' }]} />
      </div>
      <div className="analytics-table-wrap synthetic-table"><table><thead><tr><th>策略</th><th>订单容量利用率</th><th>明细容量利用率</th><th>件数容量利用率</th><th>订单守恒</th><th>明细守恒</th><th>数量守恒</th></tr></thead><tbody>{[data.baseline, data.optimized].map((summary) => <tr key={summary.algorithm}><td>{summary.algorithm}</td><td>{formatNumber(summary.capacityUtilization.ordersCapacityUsage * 100, 2)}%</td><td>{formatNumber(summary.capacityUtilization.linesCapacityUsage * 100, 2)}%</td><td>{formatNumber(summary.capacityUtilization.quantityCapacityUsage * 100, 2)}%</td><td>{summary.dataQuality.orderConservation ? 'PASS' : 'FAIL'}</td><td>{summary.dataQuality.lineConservation ? 'PASS' : 'FAIL'}</td><td>{summary.dataQuality.quantityConservation ? 'PASS' : 'FAIL'}</td></tr>)}</tbody></table></div>
      <h3 className="analytics-subhead">波次明细（JSON 全量 {details.length} 波）</h3>
      <div className="analytics-table-wrap synthetic-wave-table"><table><thead><tr><th>策略</th><th>Wave</th><th>优先级</th><th>订单</th><th>明细</th><th>件数</th><th>距离 cell</th><th>容量校验</th></tr></thead><tbody>{details.map((wave) => <tr key={`${wave.algorithm}-${wave.waveId}`}><td>{wave.algorithm}</td><td>{wave.waveId}</td><td>{wave.priority}</td><td>{wave.orderCount}</td><td>{wave.lineCount}</td><td>{wave.totalQuantity}</td><td>{wave.distanceCells}</td><td>{wave.capacityValidation}</td></tr>)}</tbody></table></div>
      <div className="analytics-notice synthetic-warning"><Info size={17} /><span><b>{data.distanceMetric.name}</b> 仅为二维网格代理距离，不是 AGV 真实运行距离；不包含动态障碍、死锁避让、跨层升降或设备行驶时间。{formatNumber(data.comparison.distanceImprovementRate ?? Number.NaN, 2)}% 不得称为真实 AGV 路径优化率。</span></div>
    </section>
  )
}

function SyntheticEtaSection({ data }: { data: SyntheticEtaPredictionResult }) {
  const evaluation = data.overallEvaluation
  const taskTypeRows = Object.entries(data.taskTypeEvaluation)
  return (
    <section className="synthetic-section" aria-labelledby="synthetic-eta-title">
      <div className="analytics-section-head"><TimerReset size={18} /><h2 id="synthetic-eta-title">6.5 ETA 预测与误差评估</h2><small>{data.modelVersion}</small></div>
      <div className="analytics-mini-grid synthetic-summary-grid">
        <div><span>总任务（分区合计）</span><b>{data.trainSampleCount + data.testSampleCount}</b></div><div><span>名义训练分区</span><b>{data.trainSampleCount}</b></div><div><span>严格截止实际拟合</span><b>{data.fitSampleCount}</b></div><div><span>截止时标签不可用</span><b>{data.trainingCutoff.excludedUnavailableTargetCount}</b></div><div><span>测试样本</span><b>{data.testSampleCount}</b></div><div><span>首条测试创建 Tick</span><b>{data.trainingCutoff.firstTestCreatedTick}</b></div>
      </div>
      <div className="analytics-notice synthetic-cutoff"><Clock3 size={17} /><span>严格截止规则：仅使用 <code>{data.trainingCutoff.comparison}</code> 的训练标签。名义训练 {data.trainingCutoff.nominalTrainSampleCount} 条中，实际拟合 {data.trainingCutoff.fitSampleCount} 条，排除 {data.trainingCutoff.excludedUnavailableTargetCount} 条；最终状态为 {data.trainingCutoff.deployabilityStatus}。</span></div>
      <p className="analytics-inline-data">基线：{data.baselineModel.modelVersion}　｜　模型：{data.ridgeModel.modelVersion}　｜　Ridge λ={data.ridgeModel.ridgeLambda}　｜　截距惩罚：{data.ridgeModel.interceptPenalized ? '是' : '否'}</p>
      <div className="analytics-chart-grid">
        <SyntheticBars title="两模型 MAE" unit="Tick" decimals={6} values={[{ label: '中位数基线', value: evaluation.baseline.maeTicks, color: '#64748b' }, { label: 'Ridge', value: evaluation.ridge.maeTicks, color: '#0f766e' }]} />
        <SyntheticBars title="两模型 MAPE（独立百分比尺度）" unit="%" decimals={6} values={[{ label: '中位数基线', value: evaluation.baseline.mapePercent, color: '#64748b' }, { label: 'Ridge', value: evaluation.ridge.mapePercent, color: '#0f766e' }]} />
        <SyntheticBars title="P90 绝对误差（最近秩法）" unit="Tick" decimals={6} values={[{ label: '中位数基线', value: evaluation.baseline.p90AbsoluteErrorTicks, color: '#64748b' }, { label: 'Ridge', value: evaluation.ridge.p90AbsoluteErrorTicks, color: '#0f766e' }]} />
        <EtaScatterChart predictions={data.predictions} />
      </div>
      <p className="analytics-inline-data">模拟测试集 MAE 改善率：{evaluation.maeImprovementPercent === null ? '—' : `${formatNumber(evaluation.maeImprovementPercent, 6)}%`}。全部时间单位均为 Tick；P90 是绝对误差分位数，不是预测区间覆盖率。</p>
      <h3 className="analytics-subhead">按 taskType 的误差</h3>
      <div className="analytics-table-wrap synthetic-table"><table><thead><tr><th>taskType</th><th>样本</th><th>基线 MAE</th><th>Ridge MAE</th><th>基线 MAPE</th><th>Ridge MAPE</th><th>基线 P90</th><th>Ridge P90</th></tr></thead><tbody>{taskTypeRows.map(([taskType, row]) => <tr key={taskType}><td>{taskType}</td><td>{row.sampleCount}</td><td>{row.baselineMAE === null ? '—' : `${formatNumber(row.baselineMAE, 4)} Tick`}</td><td>{row.modelMAE === null ? '—' : `${formatNumber(row.modelMAE, 4)} Tick`}</td><td>{row.baselineMAPE === null ? '—' : `${formatNumber(row.baselineMAPE, 4)}%`}</td><td>{row.modelMAPE === null ? '—' : `${formatNumber(row.modelMAPE, 4)}%`}</td><td>{row.baselineP90AbsoluteErrorTicks === null ? row.baselineP90Reason ?? '—' : `${formatNumber(row.baselineP90AbsoluteErrorTicks, 4)} Tick`}</td><td>{row.modelP90AbsoluteErrorTicks === null ? row.modelP90Reason ?? '—' : `${formatNumber(row.modelP90AbsoluteErrorTicks, 4)} Tick`}</td></tr>)}</tbody></table></div>
      <details className="synthetic-excluded"><summary>查看截止时不可用而排除的 {data.trainingCutoff.excludedUnavailableTargetCount} 条任务</summary><p>{data.trainingCutoff.excludedTaskIds.join('、')}</p></details>
      <div className="analytics-notice synthetic-warning"><TriangleAlert size={17} /><span>模拟生成器采用与预测模型相关的特征生成任务耗时，较高改善率不代表真实仓储 ETA 预测表现。queueLength 与 congestionLevel 在真实系统预测时点的可用性尚未验证。</span></div>
    </section>
  )
}

function SyntheticAnalyticsView() {
  const abcQuery = useQuery({ queryKey: ['analytics-synthetic', 'abc-heat', 42], queryFn: api.getSyntheticAbcHeatResult, retry: false, staleTime: Infinity })
  const waveQuery = useQuery({ queryKey: ['analytics-synthetic', 'wave-optimization', 42], queryFn: api.getSyntheticWaveOptimizationResult, retry: false, staleTime: Infinity })
  const etaQuery = useQuery({ queryKey: ['analytics-synthetic', 'eta-prediction', 42], queryFn: api.getSyntheticEtaPredictionResult, retry: false, staleTime: Infinity })
  const pending = abcQuery.isPending || waveQuery.isPending || etaQuery.isPending
  return (
    <div className="synthetic-view" data-source="SYNTHETIC">
      <div className="synthetic-banner"><FlaskConical size={22} /><div><strong>SYNTHETIC 模拟数据</strong><span>数据版本：analytics-synthetic-v1 · seed=42 · 不属于真实 Run · 不能用于正式验收</span></div></div>
      {pending ? <div className="analytics-notice" role="status">正在按需加载三份只读实验 JSON…</div> : null}
      {abcQuery.isError ? <SyntheticError name="6.1 ABC/热度实验" error={abcQuery.error} retry={() => { void abcQuery.refetch() }} /> : abcQuery.data ? <SyntheticAbcSection data={abcQuery.data} /> : null}
      {waveQuery.isError ? <SyntheticError name="6.2 波次优化实验" error={waveQuery.error} retry={() => { void waveQuery.refetch() }} /> : waveQuery.data ? <SyntheticWaveSection data={waveQuery.data} /> : null}
      {etaQuery.isError ? <SyntheticError name="6.5 ETA 实验" error={etaQuery.error} retry={() => { void etaQuery.refetch() }} /> : etaQuery.data ? <SyntheticEtaSection data={etaQuery.data} /> : null}
    </div>
  )
}

function ComparisonBars({
  title,
  baselineLabel,
  candidateLabel,
  baseline,
  candidate,
  unit,
}: {
  title: string
  baselineLabel: string
  candidateLabel: string
  baseline: number | null
  candidate: number | null
  unit: string
}) {
  const maximum = Math.max(baseline ?? 0, candidate ?? 0)
  const row = (label: string, value: number | null, tone: string) => <div className="analytics-compare-bar-row">
    <span>{label}</span>
    <div><i className={tone} style={{ width: value === null || maximum <= 0 ? '0%' : `${Math.max(1, value / maximum * 100)}%` }} /></div>
    <b>{value === null ? '—' : `${formatNumber(value, 3)} ${unit}`}</b>
  </div>
  return <div className="analytics-compare-chart"><h4>{title}</h4>{row(baselineLabel, baseline, 'baseline')}{row(candidateLabel, candidate, 'candidate')}</div>
}

function ThroughputTrendChart({
  baseline,
  candidate,
  baselineLabel,
  candidateLabel,
}: {
  baseline: ThroughputTrendPoint[]
  candidate: ThroughputTrendPoint[]
  baselineLabel: string
  candidateLabel: string
}) {
  const width = 720
  const height = 230
  const padding = 34
  const maxTick = Math.max(1, ...baseline.map((point) => point.tick), ...candidate.map((point) => point.tick))
  const maxPieces = Math.max(1, ...baseline.map((point) => point.cumulativeDeliveredPieces), ...candidate.map((point) => point.cumulativeDeliveredPieces))
  const points = (rows: ThroughputTrendPoint[]) => rows.map((point) => {
    const x = padding + point.tick / maxTick * (width - padding * 2)
    const y = height - padding - point.cumulativeDeliveredPieces / maxPieces * (height - padding * 2)
    return `${x},${y}`
  }).join(' ')
  return <div className="analytics-throughput-trend">
    <div className="analytics-throughput-legend"><span className="baseline">{baselineLabel}</span><span className="candidate">{candidateLabel}</span></div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="累计有效交付件数随仿真Tick变化">
      <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} />
      <line x1={padding} y1={padding} x2={padding} y2={height - padding} />
      <polyline className="baseline" points={points(baseline)} />
      <polyline className="candidate" points={points(candidate)} />
      <text x={padding} y={height - 8}>Tick 0</text>
      <text x={width - padding} y={height - 8} textAnchor="end">Tick {maxTick}</text>
      <text x={padding + 4} y={padding - 8}>{maxPieces} 件</text>
    </svg>
  </div>
}

function ThroughputComparisonPanel({ baselineRunId, candidateRunId }: { baselineRunId: string; candidateRunId: string }) {
  const enabled = Boolean(baselineRunId && candidateRunId && baselineRunId !== candidateRunId)
  const query = useQuery({
    queryKey: ['algorithm-throughput-comparison', baselineRunId, candidateRunId],
    queryFn: () => api.compareAlgorithmThroughput(baselineRunId, candidateRunId),
    enabled,
    retry: false,
  })
  const result: ThroughputComparisonResult | undefined = query.data
  if (!enabled) {
    return <div className="analytics-throughput-panel" id="throughput-comparison">
      <h4 className="analytics-heading-with-info">运输吞吐量对比 <MetricInfo label="运输吞吐量口径">仅统计侧向理货臂实际放入指定放置区的 SKU 件数；已拣件数、库存预留量和跨层移库数量不计入。</MetricInfo></h4>
      <p className="analytics-section-intro">选择两次固定观察窗口实验，比较相同时间内的有效交付量。</p>
    </div>
  }
  if (query.isError) {
    return <div className="analytics-throughput-panel" id="throughput-comparison"><QueryError name="运输吞吐量对比" error={query.error} retry={() => { void query.refetch() }} /></div>
  }
  if (!result) {
    return <div className="analytics-throughput-panel" id="throughput-comparison" role="status">正在读取固定窗口交付证据…</div>
  }
  const baselineLabel = `${result.baseline.algorithm.pathPolicy?.toUpperCase() ?? '—'} / ${result.baseline.algorithm.assignmentPolicy ?? '—'}`
  const candidateLabel = `${result.candidate.algorithm.pathPolicy?.toUpperCase() ?? '—'} / ${result.candidate.algorithm.assignmentPolicy ?? '—'}`
  return <div className="analytics-throughput-panel" id="throughput-comparison">
    <div className="analytics-throughput-title"><div><h4 className="analytics-heading-with-info">运输吞吐量对比 <MetricInfo label="运输吞吐量口径">货物实际进入指定放置区后按 SKU 件数累计。只有观察窗口、场景签名和交付证据均满足条件时才计算改善率。</MetricInfo></h4></div><b>{result.comparable ? '固定窗口可比' : '保留证据 · 不输出正式提升率'}</b></div>
    <div className="analytics-throughput-kpis">
      <div><span>基线运输件数</span><b>{result.baseline.deliveredPieces ?? '—'}</b><small>{baselineLabel}</small></div>
      <div><span>候选运输件数</span><b>{result.candidate.deliveredPieces ?? '—'}</b><small>{candidateLabel}</small></div>
      <div><span>增加运输件数</span><b>{result.comparable ? result.deliveredPiecesIncrease ?? '—' : '—'}</b><small>候选 - 基线</small></div>
      <div><span>吞吐量提升率</span><b>{result.comparable && result.throughputImprovementPercent !== null ? `${formatNumber(result.throughputImprovementPercent, 2)}%` : '—'}</b><small>{result.throughputImprovementReason ?? '件/仿真小时'}</small></div>
    </div>
    {!result.comparable ? <div className="analytics-notice"><Info size={17} /><span>不可正式比较：{result.comparisonReasons.join(' / ')}。原始交付量和趋势继续保留。</span></div> : null}
    <div className="analytics-throughput-visuals">
      <ComparisonBars title="相同观察时间内有效交付件数" baselineLabel={baselineLabel} candidateLabel={candidateLabel} baseline={result.baseline.deliveredPieces} candidate={result.candidate.deliveredPieces} unit="件" />
      <div className="analytics-throughput-rate"><span>基线吞吐率<b>{result.baseline.throughputPiecesPerSimulatedHour === null ? '—' : `${formatNumber(result.baseline.throughputPiecesPerSimulatedHour, 2)} 件/仿真小时`}</b></span><span>候选吞吐率<b>{result.candidate.throughputPiecesPerSimulatedHour === null ? '—' : `${formatNumber(result.candidate.throughputPiecesPerSimulatedHour, 2)} 件/仿真小时`}</b></span><small>窗口：{result.baseline.observationTick} Tick / {result.baseline.observationSeconds ?? '—'} 秒</small></div>
    </div>
    <h4 className="analytics-subhead">累计有效交付趋势</h4>
    <ThroughputTrendChart baseline={result.baseline.deliveryTrend} candidate={result.candidate.deliveryTrend} baselineLabel={baselineLabel} candidateLabel={candidateLabel} />
    <h4 className="analytics-subhead">效率与运行质量</h4>
    <div className="analytics-comparison-matrix analytics-throughput-matrix">
      <span>指标</span><b>基线</b><b>候选</b>
      <span>订单完成率</span><b>{result.baseline.orderCompletionRatePercent === null ? '—' : `${formatNumber(result.baseline.orderCompletionRatePercent, 2)}%`}</b><b>{result.candidate.orderCompletionRatePercent === null ? '—' : `${formatNumber(result.candidate.orderCompletionRatePercent, 2)}%`}</b>
      <span>物理行驶等待</span><b>{result.baseline.physicalTravelWaitRobotTicks === null ? '—' : `${result.baseline.physicalTravelWaitRobotTicks} robot·Tick`}</b><b>{result.candidate.physicalTravelWaitRobotTicks === null ? '—' : `${result.candidate.physicalTravelWaitRobotTicks} robot·Tick`}</b>
      <span>阻塞</span><b>{result.baseline.blockedRobotTicks === null ? '—' : `${result.baseline.blockedRobotTicks} robot·Tick`}</b><b>{result.candidate.blockedRobotTicks === null ? '—' : `${result.candidate.blockedRobotTicks} robot·Tick`}</b>
      <span>持续循环等待</span><b>{result.baseline.persistentCycleEpisodeCount ?? '—'}</b><b>{result.candidate.persistentCycleEpisodeCount ?? '—'}</b>
      <span>平均任务耗时</span><b>{result.baseline.averageTaskLifecycleSeconds === null ? '—' : `${formatNumber(result.baseline.averageTaskLifecycleSeconds, 2)} s`}</b><b>{result.candidate.averageTaskLifecycleSeconds === null ? '—' : `${formatNumber(result.candidate.averageTaskLifecycleSeconds, 2)} s`}</b>
      <span>数据质量</span><b>{result.baseline.dataQuality}</b><b>{result.candidate.dataQuality}</b>
    </div>
  </div>
}

function AlgorithmComparisonPanel({ onCreateExperiment }: { onCreateExperiment: () => void }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const baselineParam = searchParams.get('baselineRunId') ?? ''
  const candidateParam = searchParams.get('candidateRunId') ?? ''
  const [baselineRunId, setBaselineRunId] = useState(baselineParam)
  const [candidateRunId, setCandidateRunId] = useState(candidateParam)
  const [candidatePolicy, setCandidatePolicy] = useState<AlgorithmExperimentRunSummary['assignmentPolicy']>('hungarian')
  const runsQuery = useQuery({
    queryKey: ['algorithm-experiment-runs'],
    queryFn: api.getAlgorithmExperimentRuns,
    retry: false,
    staleTime: 15_000,
  })
  useEffect(() => setBaselineRunId(baselineParam), [baselineParam])
  useEffect(() => setCandidateRunId(candidateParam), [candidateParam])
  const updateRunSelection = (key: 'baselineRunId' | 'candidateRunId', value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next, { replace: true })
    if (key === 'baselineRunId') setBaselineRunId(value)
    else setCandidateRunId(value)
  }
  const comparisonQuery = useQuery({
    queryKey: ['algorithm-experiment-comparison', baselineRunId, candidateRunId],
    queryFn: () => api.compareAlgorithmExperimentRuns(baselineRunId, candidateRunId),
    enabled: Boolean(
      baselineRunId && candidateRunId && baselineRunId !== candidateRunId
      && runsQuery.data?.runs.some((run) => run.runId === baselineRunId)
      && runsQuery.data?.runs.some((run) => run.runId === candidateRunId)
    ),
    retry: false,
  })
  const runs: AlgorithmExperimentRunSummary[] = runsQuery.data?.runs ?? []
  const baselineRun = runs.find((run) => run.runId === baselineRunId)
  const missingBaseline = Boolean(baselineRunId && !runsQuery.isPending && !baselineRun)
  const missingCandidate = Boolean(candidateRunId && !runsQuery.isPending && !runs.some((run) => run.runId === candidateRunId))
  const candidateMutation = useMutation({
    mutationFn: () => api.createAlgorithmExperimentCandidate(baselineRunId, candidatePolicy),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ['algorithm-experiment-runs'] })
      updateRunSelection('candidateRunId', created.runId)
    },
  })
  const result: AlgorithmComparisonResult | undefined = comparisonQuery.data
  const formatAlgorithm = (run: AlgorithmExperimentRunSummary) => (
    `${run.runId} · ${run.pathPolicy.toUpperCase()} / ${run.assignmentPolicy} · ${run.scenarioId} · ${run.observationTicks ?? '完整Run'} · ${run.status}`
  )
  const visitedStates = (item: AlgorithmComparisonResult['baseline']) => (
    item.planningEvidence?.reduce((total, evidence) => total + (hasNumericValue(evidence.visitedStateCount) ? evidence.visitedStateCount : 0), 0) ?? 0
  )
  const baselineContract = result?.baseline.experimentContract
  const candidateContract = result?.candidate.experimentContract
  const signatureState = !baselineContract?.scenarioSignature || !candidateContract?.scenarioSignature
    ? '未提供'
    : baselineContract.scenarioSignature === candidateContract.scenarioSignature ? '一致' : '不一致'
  const topologyState = !baselineContract?.topologyRevision || !candidateContract?.topologyRevision
    ? '未提供'
    : baselineContract.topologyRevision === candidateContract.topologyRevision ? '一致' : '不一致'
  return <section className="analytics-module analytics-module-wide analytics-algorithm-comparison" id="algorithm-comparison">
    <div className="analytics-module-top"><div><h3 className="analytics-heading-with-info">算法对比 <MetricInfo label="算法对比数据范围">{runsQuery.data?.sourceDescription ?? '实验 Run 仅从当前后端数据库读取。'} 不会跨不同 SQLite 自动合并，也不会混入 SYNTHETIC 实验数据。</MetricInfo></h3></div><span className="analytics-state analytics-state-partial">真实隔离 Run</span></div>
    <p className="analytics-section-intro">选择同一场景下的两次实验，对比路径规划或任务分配算法效果。</p>
    <div className="analytics-experiment-guide" aria-label="A/B实验步骤"><span><b>1</b>选择同一场景</span><i /><span><b>2</b>选择两次实验</span><i /><span><b>3</b>查看算法差异</span></div>
    {runsQuery.isError ? <QueryError name="算法实验Run列表" error={runsQuery.error} retry={() => { void runsQuery.refetch() }} /> : null}
    {!runsQuery.isPending && runs.length === 0 ? <div className="analytics-comparison-empty"><Info size={18} /><div><strong>当前数据库没有实验 Run</strong><span>请先创建至少两次同场景实验，可对比路径规划或任务分配策略。</span></div><button type="button" onClick={onCreateExperiment}>新建实验</button></div> : null}
    {runs.length === 1 ? <div className="analytics-comparison-empty"><Info size={18} /><div><strong>目前只有一个实验 Run</strong><span>至少需要两个使用相同场景、但算法不同的终态 Run。已有：{formatAlgorithm(runs[0])}</span></div><button type="button" onClick={onCreateExperiment}>创建第二个实验</button></div> : null}
    {runs.length >= 2 ? <><h4 className="analytics-subhead">1. 实验选择</h4><div className="analytics-comparison-selectors">
      <label>基线 Run<select aria-label="基线实验Run" value={baselineRunId} onChange={(event) => updateRunSelection('baselineRunId', event.target.value)}><option value="">请选择</option>{runs.map((run) => <option key={run.runId} value={run.runId}>{formatAlgorithm(run)}</option>)}</select></label>
      <label>候选 Run<select aria-label="候选实验Run" value={candidateRunId} onChange={(event) => updateRunSelection('candidateRunId', event.target.value)}><option value="">请选择</option>{runs.map((run) => <option key={run.runId} value={run.runId}>{formatAlgorithm(run)}</option>)}</select></label>
    </div></> : null}
    {missingBaseline || missingCandidate ? <div className="analytics-notice" role="alert"><TriangleAlert size={17} /><span>URL 中的{missingBaseline ? '基线' : '候选'} Run 不存在于当前后端数据库，请重新选择。</span></div> : null}
    {baselineRunId ? <div className="analytics-replay-card">
      <div><strong>复制基线创建候选实验</strong><span>从权威冻结输入复刻订单、库存、车辆和资源条件，只切换 assignmentPolicy；原 Run 不会被修改。</span></div>
      <label>候选分配算法<select aria-label="候选分配算法" value={candidatePolicy} onChange={(event) => setCandidatePolicy(event.target.value as AlgorithmExperimentRunSummary['assignmentPolicy'])}><option value="greedy">Greedy</option><option value="hungarian">Hungarian</option><option value="throughput_aware">Throughput-Aware</option></select></label>
      <button type="button" disabled={!baselineRun?.replayEligible || baselineRun.assignmentPolicy === candidatePolicy || candidateMutation.isPending} onClick={() => candidateMutation.mutate()}>{candidateMutation.isPending ? '正在创建…' : '复制并启动候选 Run'}</button>
      {!baselineRun?.replayEligible ? <small>当前基线不可复制：{baselineRun?.replayIneligibilityReason ?? '未提供权威冻结重放证据'}。</small> : null}
      {candidateMutation.isError ? <small role="alert">创建失败：{candidateMutation.error instanceof Error ? candidateMutation.error.message : '未知错误'}</small> : null}
    </div> : null}
    {comparisonQuery.isError ? <QueryError name="算法实验对比" error={comparisonQuery.error} retry={() => { void comparisonQuery.refetch() }} /> : null}
    {result ? <>
      <h4 className="analytics-subhead">2. 场景可比性</h4>
      <div className="analytics-comparability-grid">
        <span>场景签名<b>{signatureState}</b><small>{baselineContract?.scenarioSignature?.slice(0, 12) ?? '未提供'}</small></span>
        <span>拓扑 Revision<b>{topologyState}</b><small>{baselineContract?.topologyRevision ?? '未提供'}</small></span>
        <span>任务完成范围<b>{result.baseline.completedTaskCount}/{result.baseline.totalTaskCount} · {result.candidate.completedTaskCount}/{result.candidate.totalTaskCount}</b><small>基线 · 候选</small></span>
        <span>米制与速度合同<b>{result.comparable ? '签名校验通过' : '需核对'}</b><small>由后端场景签名约束</small></span>
        <span>数据质量<b>{result.baseline.dataComplete && result.candidate.dataComplete ? '完整' : '不完整'}</b><small>{result.baseline.motionDataQuality ?? '—'} · {result.candidate.motionDataQuality ?? '—'}</small></span>
      </div>
      <div className="analytics-comparison-grid">
        {[result.baseline, result.candidate].map((item, index) => <div key={item.runId} className="analytics-comparison-run">
          <strong>{index === 0 ? '基线' : '候选'} · {item.pathPolicy.toUpperCase()} / {item.assignmentPolicy}</strong>
          <span>水平里程 {item.totalActualHorizontalMeters === null ? '—' : `${formatNumber(item.totalActualHorizontalMeters, 3)} m`}</span>
          <span>完成 {item.completedTaskCount}/{item.totalTaskCount} · {formatNumber(item.taskCompletionRatePercent, 2)}%</span>
          <span>模拟耗时 {item.simulatedSeconds === null ? '—' : `${formatNumber(item.simulatedSeconds, 2)} s`}</span>
          <span>规划 {formatNumber(item.planningDurationMs, 3)} ms / {item.planningCallCount} 次 / 重规划 {item.replanCount}</span>
          <span>阻塞 {item.blockedRobotTicks} robot·Tick · 持续循环 {item.persistentCycleEpisodeCount ?? '—'}</span>
        </div>)}
      </div>
      {result.comparable ? <>
        <div className="analytics-compare-visuals">
          <ComparisonBars title="3. 实际水平距离比较" baselineLabel={result.baseline.pathPolicy.toUpperCase()} candidateLabel={result.candidate.pathPolicy.toUpperCase()} baseline={result.baseline.totalActualHorizontalMeters} candidate={result.candidate.totalActualHorizontalMeters} unit="m" />
          <ComparisonBars title="4. 完成时间比较" baselineLabel={result.baseline.pathPolicy.toUpperCase()} candidateLabel={result.candidate.pathPolicy.toUpperCase()} baseline={result.baseline.simulatedSeconds} candidate={result.candidate.simulatedSeconds} unit="s" />
        </div>
        <div className="analytics-mini-grid analytics-improvement-grid">
          <MiniValue label="节省水平里程" value={result.distanceSavedMeters} suffix="m" />
          <MiniValue label="距离改善率" value={result.distanceImprovementPercent} suffix="%" />
          <MiniValue label="节省模拟秒数" value={result.simulatedSecondsSaved} suffix="s" />
          <MiniValue label="时间改善率" value={result.simulatedTimeImprovementPercent} suffix="%" />
        </div>
        <h4 className="analytics-subhead">5. 搜索效率比较</h4>
        <div className="analytics-comparison-matrix">
          <span>指标</span><b>基线</b><b>候选</b>
          <span>访问状态</span><b>{visitedStates(result.baseline)}</b><b>{visitedStates(result.candidate)}</b>
          <span>规划调用</span><b>{result.baseline.planningCallCount}</b><b>{result.candidate.planningCallCount}</b>
          <span>规划耗时</span><b>{formatNumber(result.baseline.planningDurationMs, 3)} ms</b><b>{formatNumber(result.candidate.planningDurationMs, 3)} ms</b>
          <span>重规划</span><b>{result.baseline.replanCount}</b><b>{result.candidate.replanCount}</b>
        </div>
        <h4 className="analytics-subhead">6. 运行质量</h4>
        <div className="analytics-mini-grid">
          <MiniValue label="基线阻塞" value={result.baseline.blockedRobotTicks} suffix="robot·Tick" />
          <MiniValue label="候选阻塞" value={result.candidate.blockedRobotTicks} suffix="robot·Tick" />
          <MiniValue label="阻塞变化" value={result.blockedRobotTicksChange} suffix="robot·Tick" />
          <MiniValue label="基线循环等待" value={result.baseline.persistentCycleEpisodeCount} suffix="Episode" />
          <MiniValue label="候选循环等待" value={result.candidate.persistentCycleEpisodeCount} suffix="Episode" />
          <MiniValue label="基线任务完成率" value={result.baseline.taskCompletionRatePercent} suffix="%" />
          <MiniValue label="候选任务完成率" value={result.candidate.taskCompletionRatePercent} suffix="%" />
        </div>
      </> : <div className="analytics-notice"><Info size={17} /><span>不可正式比较：{result.comparisonReasons.join(' / ')}。保留原始 Run 数据，但隐藏正式改善率。</span></div>}
    </> : null}
    {baselineRun && runs.some((run) => run.runId === candidateRunId)
      ? <ThroughputComparisonPanel baselineRunId={baselineRunId} candidateRunId={candidateRunId} />
      : null}
  </section>
}

export function AnalyticsPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const rememberedRunId = usePlatformStore((state) => state.selectedRunId)
  const rememberedTopologyId = usePlatformStore((state) => state.selectedTopologyId)
  const [viewMode, setViewMode] = useState<'run' | 'synthetic'>('run')
  const runId = searchParams.get('runId') || rememberedRunId
  const topologyId = searchParams.get('topologyId') || rememberedTopologyId
  const hasRunId = Boolean(runId.trim())
  const enabled = viewMode === 'run' && hasRunId

  const taskQuery = useQuery({ queryKey: ['analytics-task-metrics', runId], queryFn: () => api.getSimulationTaskMetrics(runId), enabled, retry: false, staleTime: 30_000 })
  const historyQuery = useQuery({ queryKey: ['analytics-history-quality', runId, 240], queryFn: () => api.getSimulationMetricsHistoryQuality(runId, 240), enabled, retry: false, staleTime: 30_000 })
  const contentionQuery = useQuery({ queryKey: ['analytics-ctu-contention', runId], queryFn: () => api.getSimulationAssignmentContentionMetrics(runId), enabled, retry: false, staleTime: 30_000 })
  const pathQuery = useQuery({ queryKey: ['analytics-path-planning', runId], queryFn: () => api.getSimulationPathPlanningMetrics(runId), enabled, retry: false, staleTime: 30_000 })
  const conveyorQuery = useQuery({ queryKey: ['analytics-conveyor-coordination', runId], queryFn: () => api.getSimulationConveyorCoordinationMetrics(runId), enabled, retry: false, staleTime: 30_000 })
  const deadlockQuery = useQuery({ queryKey: ['analytics-deadlock-lifecycle', runId], queryFn: () => api.getSimulationDeadlockLifecycleMetrics(runId), enabled, retry: false, staleTime: 30_000 })
  const motionGeneration: number | undefined = undefined
  const motionQuery = useQuery({
    queryKey: ['analytics-motion-distance', runId, motionGeneration ?? null],
    queryFn: () => api.getSimulationMotionDistanceMetrics(runId, motionGeneration),
    enabled,
    retry: false,
    staleTime: 30_000,
  })
  const performanceQuery = useQuery({
    queryKey: ['analytics-run-performance', runId],
    queryFn: () => api.getSimulationRunPerformanceMetrics(runId),
    enabled,
    retry: false,
    staleTime: 30_000,
  })

  const taskData: TaskMetricSummary | undefined = taskQuery.data?.runId === runId ? taskQuery.data : undefined
  const historyData: MetricHistoryQuality | undefined = historyQuery.data?.runId === runId ? historyQuery.data : undefined
  const contentionData: AssignmentContentionMetrics | undefined = contentionQuery.data?.runId === runId ? contentionQuery.data : undefined
  const pathData: PathPlanningMetrics | undefined = pathQuery.data?.runId === runId ? pathQuery.data : undefined
  const conveyorData: ConveyorCoordinationMetrics | undefined = conveyorQuery.data?.runId === runId ? conveyorQuery.data : undefined
  const deadlockData: DeadlockLifecycleMetrics | undefined = deadlockQuery.data?.runId === runId ? deadlockQuery.data : undefined
  const motionData: MotionDistanceMetrics | undefined = motionQuery.data?.runId === runId ? motionQuery.data : undefined
  const performanceData: RunPerformanceMetrics | undefined = performanceQuery.data?.runId === runId ? performanceQuery.data : undefined
  const taskMetrics = toMap(taskData, runId)
  const contentionMetrics = toMap(contentionData, runId)
  const pathMetrics = toMap(pathData, runId)
  const conveyorMetrics = toMap(conveyorData, runId)
  const deadlockMetrics = deadlockMetricMap(deadlockData, runId)

  const parentCompleted = getMetric(taskMetrics, 'PARENT_COMPLETED_TASK_COUNT')
  const parentLifetime = getMetric(taskMetrics, 'PARENT_CREATE_TO_COMPLETE_AVG_TICKS')
  const blockedCurrent = getMetric(contentionMetrics, 'BLOCKED_ROBOT_COUNT_CURRENT')
  const suspectedDeadlock = getMetric(contentionMetrics, 'SUSPECTED_DEADLOCK_ALERT_COUNT')
  const yieldEvents = getMetric(contentionMetrics, 'ROBOT_YIELD_EVENT_COUNT')
  const nodeOccupancy = getMetric(conveyorMetrics, 'CONVEYOR_NODE_OCCUPANCY_CURRENT')
  const ctuAssigned = getMetric(contentionMetrics, 'CTU_ASSIGNED_PARENT_TASK_COUNT')
  const waitAverage = getMetric(contentionMetrics, 'CTU_FIRST_ASSIGN_WAIT_AVG_TICKS')
  const waitMaximum = getMetric(contentionMetrics, 'CTU_FIRST_ASSIGN_WAIT_MAX_TICKS')
  const waitSamples = getMetric(contentionMetrics, 'CTU_FIRST_ASSIGN_WAIT_SAMPLE_COUNT')
  const associationMetric = getMetric<VehicleAssociation[]>(contentionMetrics, 'CTU_VEHICLE_TASK_ASSOCIATION_COUNT')
  const associations = Array.isArray(associationMetric?.value) ? associationMetric.value : []
  const pathSuccess = getMetric(pathMetrics, 'PATH_SUCCESS_SAMPLE_COUNT')
  const pathAlgorithm = getMetric<Array<{ algorithm: string; sampleCount: number }>>(pathMetrics, 'PATH_ALGORITHM')
  const pathTime = getMetric(pathMetrics, 'PATH_PLANNING_TIME_MS')
  const pathDistance = getMetric(pathMetrics, 'PATH_HORIZONTAL_DISTANCE_CELLS')
  const pathVisited = getMetric(pathMetrics, 'PATH_VISITED_STATE_COUNT')
  const pathQuantiles = getMetric<PathQuantileGroup[]>(pathMetrics, 'PATH_PLANNING_TIME_QUANTILES_MS')
  const quantileGroups = Array.isArray(pathQuantiles?.value) ? pathQuantiles.value : []
  const conveyorWait = getMetric(conveyorMetrics, 'CONVEYOR_WAIT_TASK_COUNT_CURRENT')
  const bufferWait = getMetric(conveyorMetrics, 'BUFFER_WAIT_TASK_COUNT_CURRENT')
  const stationQueue = getMetric(conveyorMetrics, 'STATION_QUEUE_TASK_COUNT_CURRENT')
  const cumulativeBlockedTicks = getDeadlockMetric(deadlockMetrics, 'CUMULATIVE_BLOCKED_ROBOT_TICKS')
  const blockEpisodeCount = getDeadlockMetric(deadlockMetrics, 'BLOCK_EPISODE_COUNT')
  const maxContinuousBlocked = getDeadlockMetric(deadlockMetrics, 'MAX_CONTINUOUS_BLOCKED_TICKS')
  const suspectedCycleCount = getDeadlockMetric(deadlockMetrics, 'SUSPECTED_CYCLE_EPISODE_COUNT')
  const persistentCycleCount = getDeadlockMetric(deadlockMetrics, 'PERSISTENT_WAIT_CYCLE_COUNT')
  const openCycleCount = getDeadlockMetric(deadlockMetrics, 'OPEN_CYCLE_EPISODE_COUNT')
  const confirmedResolvedCount = getDeadlockMetric(deadlockMetrics, 'CONFIRMED_RESOLVED_EPISODE_COUNT')
  const unresolvedAtEndCount = getDeadlockMetric(deadlockMetrics, 'UNRESOLVED_AT_RUN_END_COUNT')
  const endedUnverifiedCount = getDeadlockMetric(deadlockMetrics, 'ENDED_UNVERIFIED_EPISODE_COUNT')
  const averageResolutionTicks = getDeadlockMetric(deadlockMetrics, 'AVERAGE_CONFIRMED_RESOLUTION_TICKS')
  const confirmedResolutionRatio = getDeadlockMetric(deadlockMetrics, 'CONFIRMED_RESOLUTION_RATIO')
  const latestTick = historyData?.points.at(-1)?.tick
  const blockedStats = historyData?.statistics.blockedRobots
  const blockedPeak = blockedStats?.maximum
  const blockedPeakTick = blockedStats?.maximumTick
  const ctuParentTaskCount = contentionData?.quality.ctuParentTaskCount
  const historyPoints = historyData?.points ?? []
  const showTaskTrend = hasTrendData(historyPoints, ['completedTasks', 'pendingTasks'])
  const showBlockedTrend = hasTrendData(historyPoints, ['blockedRobots'])
  const showQueueTrend = hasTrendData(historyPoints, ['conveyorQueueDepth', 'bufferOccupancy'])
  const showOccupancyTrend = hasTrendData(historyPoints, ['conveyorUtilization'])
  const showHistory = Boolean(historyData && historyData.rawSampleCount > 0 && (showTaskTrend || showBlockedTrend || showQueueTrend || showOccupancyTrend))
  const showOverview = [parentCompleted, parentLifetime, blockedCurrent, waitAverage, waitMaximum, conveyorWait, bufferWait, stationQueue].some(hasMetricValue)
    || hasNumericValue(ctuParentTaskCount)
  const showCtu = hasNumericValue(ctuParentTaskCount)
    || [ctuAssigned, waitAverage, waitMaximum, waitSamples].some(hasMetricValue)
    || associations.length > 0
  const hasDeadlockHotspots = Boolean(deadlockData?.hotspots.some((item) => (
    hasNumericValue(item.row)
    && hasNumericValue(item.col)
    && hasNumericValue(item.observedEpisodeCount)
    && item.sampleCount > 0
  )))
  const showDeadlockLifecycle = [
    cumulativeBlockedTicks,
    blockEpisodeCount,
    maxContinuousBlocked,
    suspectedCycleCount,
    persistentCycleCount,
    openCycleCount,
    confirmedResolvedCount,
    unresolvedAtEndCount,
    endedUnverifiedCount,
  ].some(hasMetricValue)
    || hasSampledMetricValue(averageResolutionTicks)
    || hasMetricValue(confirmedResolutionRatio)
    || hasDeadlockHotspots
  const showLegacyContention = [blockedCurrent, suspectedDeadlock, yieldEvents].some(hasMetricValue)
    || hasNumericValue(blockedPeak)
  const showContention = showLegacyContention || showDeadlockLifecycle
  const showLifecycle = hasMetricValue(parentLifetime)
  const showPath = hasNumericValue(pathSuccess?.value) && pathSuccess.value > 0
  const ctuDistance = motionData?.totalMetrics.ctuHorizontalMeters
  const shuttleDistance = motionData?.totalMetrics.shuttleHorizontalMeters
  const totalHorizontalDistance = motionData?.totalMetrics.totalHorizontalMeters
  const liftVerticalDistance = motionData?.totalMetrics.shuttleLiftVerticalMeters
  const fixedWindowEvidence = motionData?.windowMotionEvidence?.scope === 'FIXED_OBSERVATION_WINDOW'
    ? motionData.windowMotionEvidence
    : undefined
  const showMotion = [ctuDistance, shuttleDistance, totalHorizontalDistance, liftVerticalDistance]
    .some((metric) => hasNumericValue(metric?.value))
  const showPathAndMotion = showPath || showMotion || motionQuery.isError
  const liftMovementMetric = motionData?.movementTypeMetrics.LIFT
  const hasValidLiftMovement = Boolean(
    liftMovementMetric
    && liftMovementMetric.movementCount > 0
    && hasNumericValue(liftMovementMetric.verticalMeters),
  )
  const movementRows = Object.entries(motionData?.movementTypeMetrics ?? {})
    .filter(([movementType, metric]) => movementType !== 'LIFT' && movementType !== 'UNKNOWN' && hasNumericValue(metric.horizontalMeters))
  const robotDistanceRows = (motionData?.perRobotMetrics ?? []).filter((row) => (
    hasNumericValue(row.horizontalMeters) || hasNumericValue(row.verticalMeters)
  )).slice(0, 20)
  const showConveyor = [nodeOccupancy, conveyorWait, bufferWait, stationQueue].some(hasMetricValue)
  const pathSummaries = [
    ['单次规划耗时（ms）', aggregateRows(pathTime)],
    ['计划路径长度（cell）', aggregateRows(pathDistance)],
    ['搜索访问状态数', aggregateRows(pathVisited)],
  ].filter((entry) => (entry[1] as PathAggregateGroup[]).length > 0) as Array<[string, PathAggregateGroup[]]>
  const showPerformance = Boolean(performanceData?.motionSpeedContract)
  const hasRealContent = showOverview || showHistory || showCtu || showContention || showLifecycle || showPathAndMotion || showConveyor || showPerformance
  const backUrl = `/simulation?${new URLSearchParams({
    ...(topologyId ? { topologyId } : {}),
    ...(runId ? { runId } : {}),
  }).toString()}`
  const zeroCurrentConveyor = [nodeOccupancy, conveyorWait, bufferWait, stationQueue].some((item) => item?.value === 0)

  const queryErrors = [
    ['父子任务指标', taskQuery],
    ['历史质量指标', historyQuery],
    ['CTU 与死锁事件指标', contentionQuery],
    ['路径规划指标', pathQuery],
    ['输送协同指标', conveyorQuery],
    ['死锁生命周期指标', deadlockQuery],
    ['物理速度与仿真时间指标', performanceQuery],
  ] as const

  return (
    <div className="analytics-page">
      <div className="analytics-header">
        <div>
          <button className="analytics-back" type="button" onClick={() => navigate(backUrl)}><ArrowLeft size={16} /> 返回仿真工作台</button>
          <h1><BarChart3 size={26} /> 算法性能分析中心</h1>
          <p>{viewMode === 'run' ? '仅展示当前 Run 可计算的真实指标 · null 与无样本不生成指标卡' : '独立模拟算法实验 · 静态 SYNTHETIC 数据 · 不绑定 Run，不用于正式验收'}</p>
        </div>
        <div className="analytics-run-meta">
          {viewMode === 'run' ? <>
            <span>拓扑 {topologyId || '—'}</span>
            <strong>Run {runId || '尚未选择'}</strong>
            <span>最新历史 Tick {latestTick ?? '—'}</span>
            <span>接口模式 只读 GET</span>
          </> : <>
            <span>数据源 SYNTHETIC</span>
            <strong>analytics-synthetic-v1</strong>
            <span>seed=42</span>
            <span>静态资源 只读 GET</span>
          </>}
        </div>
      </div>

      <div className="analytics-mode-switch" role="group" aria-label="分析数据模式">
        <button type="button" className={viewMode === 'run' ? 'is-active' : ''} aria-pressed={viewMode === 'run'} onClick={() => setViewMode('run')}>真实 Run 指标</button>
        <button type="button" className={viewMode === 'synthetic' ? 'is-active' : ''} aria-pressed={viewMode === 'synthetic'} onClick={() => setViewMode('synthetic')}>模拟算法实验</button>
      </div>
      {viewMode === 'run' ? <a className="analytics-algorithm-shortcut" href="#algorithm-comparison"><GitCompareArrows size={16} />直接查看算法对比</a> : null}

      {viewMode === 'synthetic' ? <SyntheticAnalyticsView /> : !hasRunId ? (
        <div className="analytics-notice analytics-empty-state"><Info size={18} /><div><strong>尚无选中的 Run</strong><span>请选择已有 Run 查看真实指标，或切换到“模拟算法实验”。当前不会发出真实指标请求。</span></div></div>
      ) : <>
      {queryErrors.map(([name, query]) => query.isError ? <QueryError key={name} name={name} error={query.error} retry={() => { void query.refetch() }} /> : null)}

      {showOverview ? <>
      <div className="analytics-section-head"><Activity size={18} /><h2>A. 运行概览</h2></div>
      <div className="analytics-kpi-grid">
        <MetricCard label="已完成父任务数量" metric={parentCompleted} source="M2-1 /metrics/tasks · 最终任务状态重算" />
        <MetricCard label="父任务平均生命周期" metric={parentLifetime} source="M2-1 completedTick - createdTick" decimals={1} suffix="Tick" />
        <MetricCard label="当前阻塞车辆数" metric={blockedCurrent} source="M2-3 最终机器人状态" />
        <ValueCard label="CTU 父任务数量" value={ctuParentTaskCount} source="M2-3 当前 Run 合法父任务" />
        <MetricCard label="首次分配平均等待" metric={waitAverage} source="M2-3 firstAssignmentTick - createdTick" decimals={1} suffix="Tick" />
        <MetricCard label="首次分配最长等待" metric={waitMaximum} source="M2-3 firstAssignmentTick - createdTick" suffix="Tick" />
        <MetricCard label="当前输送等待任务数" metric={conveyorWait} source="M2-5A 当前持久化状态" />
        <MetricCard label="当前缓存等待任务数" metric={bufferWait} source="M2-5A 当前持久化状态" />
        <MetricCard label="站台相关任务数" metric={stationQueue} source="按当前处于输送至拣货、拣货中、放货阶段的任务数统计，包含处理中任务，不等于纯等待队列。" />
      </div>
      </> : null}

      {showPerformance && performanceData?.motionSpeedContract ? <section className="analytics-module analytics-module-wide analytics-speed-summary">
        <div className="analytics-module-top"><div><h3 className="analytics-heading-with-info">物理速度与仿真时间 <MetricInfo label="物理速度与仿真时间">仿真耗时按冻结的 simTickSeconds × Tick 计算；播放倍率不参与物理统计。</MetricInfo></h3></div><span className="analytics-state analytics-state-warning">用户定义 · UNVERIFIED</span></div>
        <div className="analytics-mini-grid">
          <MiniValue label="CTU配置速度" value={performanceData.motionSpeedContract.ctuSpeedMetersPerSecond} suffix="m/s" />
          <MiniValue label="Shuttle配置速度" value={performanceData.motionSpeedContract.shuttleSpeedMetersPerSecond} suffix="m/s" />
          <MiniValue label="Lift配置速度" value={performanceData.motionSpeedContract.liftSpeedMetersPerSecond} suffix="m/s" />
          <MiniValue label="仿真时间步长" value={performanceData.motionSpeedContract.simTickSeconds} suffix="s/Tick" />
          <MiniValue label="仿真总耗时" value={performanceData.simulatedSeconds} suffix="s" />
          <MiniValue label="车辆运动时间" value={performanceData.movementRobotSeconds} suffix="robot·s" />
          <MiniValue label="车辆等待时间" value={performanceData.waitingRobotSeconds} suffix="robot·s" />
          <MiniValue label="平均有效水平速度" value={performanceData.averageEffectiveHorizontalSpeedMetersPerSecond} suffix="m/s" />
          <MiniValue label="任务完成率" value={performanceData.taskCompletionRatePercent} suffix="%" />
        </div>
      </section> : null}

      {showHistory ? <>
      <div className="analytics-section-head"><TimerReset size={18} /><h2 className="analytics-heading-with-info">B. 历史运行趋势 <MetricInfo label="历史趋势">横轴为仿真 Tick；缺失值不会绘制为 0。快照去重与抽样信息可在“指标说明”中查看。</MetricInfo></h2></div>
      <div className="analytics-chart-grid">
        {showTaskTrend ? <TrendChart title="任务进度趋势" points={historyPoints} series={[{ key: 'completedTasks', name: '已完成任务', color: '#0f766e' }, { key: 'pendingTasks', name: '待处理任务', color: '#ea580c' }]} unit="任务数" /> : null}
        {showBlockedTrend ? <TrendChart title="阻塞车辆趋势" points={historyPoints} series={[{ key: 'blockedRobots', name: '阻塞车辆', color: '#dc2626' }]} unit="车辆数" /> : null}
        {showQueueTrend ? <TrendChart title="输送与缓存等待趋势" points={historyPoints} series={[{ key: 'conveyorQueueDepth', name: '输送等待任务', color: '#d97706' }, { key: 'bufferOccupancy', name: '缓存相关任务', color: '#2563eb' }]} unit="任务数" /> : null}
        {showOccupancyTrend ? <TrendChart title="输送节点占用率趋势" points={historyPoints} series={[{ key: 'conveyorUtilization', name: '输送节点占用率', color: '#0f766e' }]} unit="%" /> : null}
      </div>
      </> : null}

      {(showCtu || showContention || showLifecycle || showPathAndMotion || showConveyor) ? <>
      <div className="analytics-section-head"><GitCompareArrows size={18} /><h2>C. 算法效果与运行诊断</h2><small>真实 Run</small></div>
      <div className="analytics-module-list">
        {showCtu ? <section className="analytics-module" id="section-6-3">
          <div className="analytics-module-top"><div><span className="analytics-module-no">6.3</span><h3>CTU 任务分配</h3></div><span className="analytics-state analytics-state-partial">真实 Run</span></div>
          <div className="analytics-mini-grid">
            <MiniValue label="CTU 父任务总数" value={ctuParentTaskCount} />
            <MiniMetric label="已观察分配任务数" metric={ctuAssigned} />
            <MiniMetric label="首次分配平均等待" metric={waitAverage} decimals={1} suffix="Tick" info="按首次有效分配 Tick 与任务创建 Tick 的差值统计。" />
            <MiniMetric label="首次分配最长等待" metric={waitMaximum} suffix="Tick" />
            <MiniMetric label="有效等待样本数" metric={waitSamples} />
          </div>
          <AssignmentBars values={associations} />
        </section> : null}

        {showContention ? <section className="analytics-module analytics-module-wide analytics-deadlock-module" id="section-6-4">
          <div className="analytics-module-top"><div><span className="analytics-module-no">6.4</span><h3>死锁与拥堵诊断</h3></div><span className="analytics-state analytics-state-partial">真实 Run 诊断</span></div>
          {[blockedCurrent, suspectedDeadlock, yieldEvents].some(hasMetricValue) || hasNumericValue(blockedPeak) ? <>
            <h4 className="analytics-subhead">A. 当前拥堵状态</h4>
            <div className="analytics-mini-grid">
              <MiniMetric label="当前阻塞车辆数" metric={blockedCurrent} />
              <MiniValue label="历史阻塞峰值" value={blockedPeak} suffix="辆" />
              <MiniMetric label="疑似死锁告警次数" metric={suspectedDeadlock} />
              <MiniMetric label="已观察主动让行次数" metric={yieldEvents} />
            </div>
          </> : null}
          {[cumulativeBlockedTicks, blockEpisodeCount, maxContinuousBlocked].some(hasMetricValue) ? <>
            <h4 className="analytics-subhead">B. 累计阻塞统计</h4>
            <div className="analytics-mini-grid">
              <MiniMetric label="累计阻塞车辆Tick" metric={cumulativeBlockedTicks} suffix="robot·Tick" />
              <MiniMetric label="阻塞事件次数" metric={blockEpisodeCount} />
              <MiniMetric label="最长连续阻塞Tick" metric={maxContinuousBlocked} suffix="Tick" />
              {hasMetricValue(cumulativeBlockedTicks) ? <MiniValue label="有效采样 Tick" value={cumulativeBlockedTicks.sampleCount} /> : null}
            </div>
          </> : null}
          {[suspectedCycleCount, persistentCycleCount, openCycleCount].some(hasMetricValue) ? <>
            <h4 className="analytics-subhead">C. 持续循环等待</h4>
            <div className="analytics-mini-grid">
              <MiniMetric label="疑似循环 Episode" metric={suspectedCycleCount} />
              <MiniMetric label="持续循环等待 Episode" metric={persistentCycleCount} />
              <MiniMetric label="当前开放 Episode" metric={openCycleCount} />
            </div>
          </> : null}
          {[confirmedResolvedCount, unresolvedAtEndCount, endedUnverifiedCount].some(hasMetricValue)
            || hasSampledMetricValue(averageResolutionTicks)
            || hasMetricValue(confirmedResolutionRatio) ? <>
            <h4 className="analytics-subhead">D. 恢复与未解除</h4>
            <div className="analytics-mini-grid">
              <MiniMetric label="已确认恢复事件" metric={confirmedResolvedCount} />
              <MiniMetric label="Run结束未解除事件" metric={unresolvedAtEndCount} />
              <MiniMetric label="无法确认事件" metric={endedUnverifiedCount} />
              {hasSampledMetricValue(averageResolutionTicks) ? <MiniMetric label="平均确认恢复耗时" metric={averageResolutionTicks} decimals={1} suffix="Tick" /> : null}
              <MiniMetric label="确认恢复比例（非正式成功率）" metric={confirmedResolutionRatio} decimals={1} suffix="%" />
            </div>
          </> : null}
          {deadlockData ? <DeadlockHotspotTable hotspots={deadlockData.hotspots} /> : null}
          {showDeadlockLifecycle && deadlockData && (deadlockData.observationCompleteness !== 'COMPLETE' || hasQualityConcern(deadlockData.metrics.find(hasQualityConcern))) ? <div className="analytics-quality-alert" role="status"><TriangleAlert size={16} /><span>死锁生命周期数据不完整：{deadlockData.observationCompleteness}。诊断结果仅代表已采集事件。</span></div> : null}
        </section> : null}

        {showLifecycle ? <section className="analytics-module" id="section-6-5">
          <div className="analytics-module-top"><div><span className="analytics-module-no">6.5</span><h3>任务生命周期</h3></div><span className="analytics-state analytics-state-auxiliary">真实完成任务</span></div>
          <MetricCard label="已完成父任务平均生命周期" metric={parentLifetime} source="按已完成父任务的 completedTick - createdTick 计算；不是 ETA 预测准确率，也不与模拟 ETA 实验合并。" decimals={1} suffix="Tick" />
        </section> : null}

        {showPathAndMotion ? <section className="analytics-module analytics-module-wide" id="section-6-6">
          <div className="analytics-module-top"><div><span className="analytics-module-no">6.6</span><h3>路径规划与车辆实际里程</h3></div><span className="analytics-state analytics-state-partial">仿真实际运动段</span></div>
          {motionQuery.isError ? <QueryError name="车辆实际里程指标" error={motionQuery.error} retry={() => { void motionQuery.refetch() }} /> : null}
          {showMotion ? <>
            {fixedWindowEvidence ? <div className={`analytics-window-evidence ${fixedWindowEvidence.evidenceComplete ? 'is-complete' : 'is-incomplete'}`} aria-label="固定观察窗口运动证据">
              <strong>{fixedWindowEvidence.evidenceComplete ? '固定观察窗口运动证据完整。' : '固定观察窗口运动证据不完整。'}</strong>
              <span>本Run按 {fixedWindowEvidence.configuredObservationTicks ?? '未声明'} Tick观察窗口结束，窗口内里程不代表完成全部订单所需里程。</span>
              <small>实际结束 Tick：{fixedWindowEvidence.actualEndTick ?? '未知'} · 停止原因：{fixedWindowEvidence.stopReason ?? '未知'} · 证据质量：{fixedWindowEvidence.evidenceQuality}</small>
            </div> : null}
            <h4 className="analytics-subhead analytics-heading-with-info">A. 水平行驶 <MetricInfo label="水平里程">来自 CTU 与 Shuttle 的实际运动段，不包含 Lift 垂直运输。</MetricInfo></h4>
            <div className="analytics-kpi-grid analytics-distance-grid">
              <DistanceCard label={fixedWindowEvidence ? '窗口内 CTU 水平距离' : 'CTU 总水平行驶距离'} metric={ctuDistance} />
              <DistanceCard label={fixedWindowEvidence ? '窗口内 Shuttle 水平距离' : 'Shuttle 总水平行驶距离'} metric={shuttleDistance} />
              <DistanceCard label={fixedWindowEvidence ? '窗口内总水平距离' : '总水平行驶距离'} metric={totalHorizontalDistance} />
            </div>
            {motionData && hasNumericValue(motionData.metersPerCell) ? <div className="analytics-motion-contract" aria-label="物理距离合同">
              <span>每格 <b>{formatNumber(motionData.metersPerCell, 3)} m</b></span>
              <span className="is-warning">{motionData.physicsContractSource === 'USER_DEFINED_SIMULATION' ? '用户定义' : '参数来源未声明'} · <b>{motionData.calibrationStatus ?? '未标定'}</b></span>
            </div> : null}
            <h4 className="analytics-subhead analytics-heading-with-info">B. 垂直运输 <MetricInfo label="垂直运输">Lift 位移单独统计，不与 CTU 或 Shuttle 水平里程相加为“AGV 行驶里程”。</MetricInfo></h4>
            {hasValidLiftMovement ? <div className="analytics-vertical-transport">
              <DistanceCard label={fixedWindowEvidence ? '窗口内 Lift 垂直位移' : 'Lift 垂直位移'} metric={liftVerticalDistance} />
              <div><span>有效 Lift 运动段</span><b>{liftMovementMetric?.movementCount}</b><small>垂直位移与车辆自主水平里程分别统计</small></div>
              <div><span>相关数据质量</span><b>{liftMovementMetric?.dataQuality}</b><small>{motionData?.observationCompleteness}</small></div>
            </div> : <div className="analytics-no-lift"><Info size={16} /><span>当前Run未记录有效Lift垂直运输段。</span></div>}
            {movementRows.length ? <>
              <h4 className="analytics-subhead">C. 运动行为</h4>
              <div className="analytics-mini-grid analytics-motion-types">
                {movementRows.map(([movementType, metric]) => <div key={movementType}>
                  <span>{{ NORMAL: '正常行驶', YIELD: '主动让行', BACKOFF: '回退', RETURN_TO_STANDBY: '返回待命', LIFT: 'Lift 垂直位移' }[movementType] ?? movementType}</span>
                  <b>{formatNumber(metric.horizontalMeters as number, 3)} m</b>
                  <small>{metric.movementCount} 个运动段{metric.dataQuality !== 'COMPLETE' ? ` · ${metric.dataQuality}` : ''}</small>
                </div>)}
              </div>
            </> : null}
            {robotDistanceRows.length ? <>
              <h4 className="analytics-subhead">D. 各车辆里程 Top20</h4>
              <div className="analytics-table-wrap analytics-motion-table"><table><thead><tr><th>排名</th><th>车辆ID</th><th>类型</th><th>水平里程</th><th>垂直位移</th><th>主动让行</th><th>回退</th><th>返回待命</th><th>数据质量</th></tr></thead><tbody>
                {robotDistanceRows.map((row, index) => <tr key={row.robotId}><td>{index + 1}</td><td>{row.robotId}</td><td>{row.robotType.toUpperCase()}</td><td>{hasNumericValue(row.horizontalMeters) ? `${formatNumber(row.horizontalMeters, 3)} m` : '—'}</td><td>{hasNumericValue(row.verticalMeters) ? `${formatNumber(row.verticalMeters, 3)} m` : '—'}</td><td>{hasNumericValue(row.yieldMeters) ? `${formatNumber(row.yieldMeters, 3)} m` : '—'}</td><td>{hasNumericValue(row.backoffMeters) ? `${formatNumber(row.backoffMeters, 3)} m` : '—'}</td><td>{hasNumericValue(row.returnToStandbyMeters) ? `${formatNumber(row.returnToStandbyMeters, 3)} m` : '—'}</td><td>{row.dataQuality}</td></tr>)}
              </tbody></table></div>
              {motionData && (motionData.perRobotMetricsTruncated || motionData.totalRobotCount > robotDistanceRows.length) ? <p className="analytics-data-note">当前显示前20辆，共{motionData.totalRobotCount}辆。</p> : null}
            </> : null}
          </> : null}
          {showPath ? <>
          <h4 className="analytics-subhead analytics-heading-with-info">E. 规划诊断 <MetricInfo label="规划诊断">计划路径以 cell 计量，仅统计成功的 Shuttle 初始规划，不等同于车辆实际行驶里程。</MetricInfo></h4>
          <div className="analytics-mini-grid">
            <MiniMetric label="成功初始规划样本数" metric={pathSuccess} />
            {Array.isArray(pathAlgorithm?.value) && pathAlgorithm.value.length ? <div><span>算法名称</span><b>{pathAlgorithm.value.map((item) => `${item.algorithm.toUpperCase()} · n=${item.sampleCount}`).join(' / ')}</b></div> : null}
            {hasNumericValue(pathTime?.sampleCount) ? <MiniValue label="规划耗时有效样本" value={pathTime?.sampleCount} /> : null}
            {hasNumericValue(pathDistance?.sampleCount) ? <MiniValue label="计划路径长度有效样本" value={pathDistance?.sampleCount} /> : null}
            {hasNumericValue(pathVisited?.sampleCount) ? <MiniValue label="访问状态有效样本" value={pathVisited?.sampleCount} /> : null}
          </div>
          {pathSummaries.length ? <div className="analytics-path-summary">{pathSummaries.map(([label, rows]) => <div key={label}><h5>{label}</h5>{rows.map((row, index) => <p key={index}>{row.group.algorithm.toUpperCase()}：平均 {formatNumber(row.average, 2)}，最小 {formatNumber(row.minimum, 2)}，最大 {formatNumber(row.maximum, 2)}，n={row.validSampleCount}</p>)}</div>)}</div> : null}
          {quantileGroups.length ? <><h4 className="analytics-subhead"><Route size={15} /> 规划耗时分位数</h4><QuantilePanel groups={quantileGroups} /></> : null}
          </> : null}
        </section> : null}

        {showConveyor ? <section className="analytics-module" id="section-6-7">
          <div className="analytics-module-top"><div><span className="analytics-module-no">6.7</span><h3>输送协同</h3></div><span className="analytics-state analytics-state-partial">真实 Run 当前值</span></div>
          <div className="analytics-mini-grid">
            <MiniMetric label="输送节点占用率" metric={nodeOccupancy} decimals={1} suffix="%" info="按输送图节点占用比例统计，不代表设备时间利用率。" />
            <MiniMetric label="当前输送等待任务数" metric={conveyorWait} />
            <MiniMetric label="当前缓存等待任务数" metric={bufferWait} />
            <MiniMetric label="站台相关任务数" metric={stationQueue} info="按当前处于输送至拣货、拣货中、放货阶段的任务数统计，包含处理中任务，不等于纯等待队列。" />
          </div>
          {zeroCurrentConveyor ? <p className="analytics-data-note">当前值为0；历史变化请查看上方趋势图。</p> : null}
        </section> : null}
      </div>
      </> : null}

      {hasRealContent ? <details className="analytics-quality-details">
      <summary><Database size={17} /><span>指标说明</span><small>来源、口径与数据质量</small></summary>
      <div className="analytics-quality-grid">
        {(hasMetricValue(parentCompleted) || hasMetricValue(parentLifetime)) ? <div><h3>任务状态与生命周期</h3><QualityBadge quality={parentCompleted?.dataQuality ?? parentLifetime?.dataQuality} reason={parentCompleted?.reason ?? parentLifetime?.reason} /><p>完成数按父任务最终状态统计；生命周期按 completedTick - createdTick 计算，仅代表已完成任务，不是 ETA 准确率。</p></div> : null}
        {showPerformance ? <div><h3>物理速度与仿真时间</h3><QualityBadge quality={performanceData?.motionSpeedContract?.calibrationStatus} /><p>仿真耗时按 simTickSeconds × Tick 计算，当前播放倍率 {performanceData?.playbackMultiplier ?? '—'}× 不进入物理统计。用户定义速度尚未现场标定。</p></div> : null}
        {showHistory ? <div><h3>历史运行趋势</h3><QualityBadge quality={historyData?.dataQuality} reason={historyData?.reason} /><p>横轴为仿真 Tick；同 Tick 快照只读去重，缺失值不补 0。原始 {historyData?.rawSampleCount} 条，去重后 {historyData?.uniqueTickCount} 个 Tick，输出 {historyData?.outputSampleCount} 点；完整性：{historyData?.collectionCompleteness}。</p>{hasNumericValue(blockedPeak) ? <p>全量阻塞峰值为 {blockedPeak}{hasNumericValue(blockedPeakTick) ? `，峰值 Tick 为 ${blockedPeakTick}` : ''}。</p> : null}</div> : null}
        {(showCtu || showLegacyContention) ? <div><h3>CTU 分配与争用</h3><QualityBadge quality={contentionData?.dataQuality} reason={contentionData?.reason} /><p>首次分配等待按 firstAssignmentTick - createdTick 计算；车辆历史关联任务数不是设备利用率。疑似死锁和让行仅统计已观察事件，完整性：{contentionData?.eventCollectionCompleteness}。</p></div> : null}
        {showDeadlockLifecycle && deadlockData ? <div><h3>死锁与拥堵诊断</h3><QualityBadge quality={deadlockData.dataQuality} reason={deadlockData.reason} /><p>Episode 表示达到检测条件的循环等待，不等同于不可恢复的物理死锁；疑似告警不与 Episode 数相加。观察完整性：{deadlockData.observationCompleteness}，generation：{deadlockData.generation ?? '未提供'}。</p><p>事件水位：ID {deadlockData.eventPersistenceWatermark.databaseEventId ?? '未提供'} / Tick {deadlockData.eventPersistenceWatermark.databaseEventTick ?? '未提供'}；快照确认水位：{deadlockData.eventPersistenceWatermark.snapshotLastAcknowledgedTransitionKey ?? '未提供'}。</p><p>唯一转换 {deadlockData.eventPersistenceWatermark.uniqueTransitionCount}；重复 {deadlockData.eventPersistenceWatermark.duplicateTransitionCount}；冲突 {deadlockData.eventPersistenceWatermark.conflictingTransitionCount}；待持久化 {deadlockData.eventPersistenceWatermark.snapshotPendingTransitionCount ?? '未知'}。BEST_EFFORT_NOT_EXACTLY_ONCE；检测阈值尚未现场标定；正式验收：{deadlockData.acceptanceEligible ? '是' : '否'}。</p></div> : null}
        {showPath ? <div><h3>路径规划</h3><QualityBadge quality={pathData?.dataQuality} reason={pathData?.reason} /><p>仅统计成功的 Shuttle 初始规划；计划路径以 cell 计量，不是车辆实际行驶里程。</p></div> : null}
        {showMotion && motionData ? <div><h3>车辆实际里程</h3><QualityBadge quality={motionData.dataQuality} reason={motionData.reason} /><p>水平里程来自 CTU 与 Shuttle 运动段；Lift 垂直位移单独统计。运动证据完整性：{motionData.observationCompleteness}；合同：{motionData.physicsContractVersion}；generation：{motionData.generation}；米制参数：{motionData.calibrationStatus ?? '未标定'}。</p>{fixedWindowEvidence ? <p>本 Run 按固定观察窗口结束；窗口证据与全 Run 完成语义分开，窗口内里程不代表完成全部订单所需里程。</p> : null}</div> : null}
        {showConveyor ? <div><h3>输送协同</h3><QualityBadge quality={conveyorData?.dataQuality} reason={conveyorData?.reason} /><p>输送与缓存数据为当前计数，不能替代全 Run 历史累计值。输送节点占用率按输送图节点占用比例统计，不代表设备时间利用率。</p><p>站台相关任务数包含输送至拣货、拣货中和放货阶段的处理中任务，不等于纯等待队列。</p></div> : null}
      </div>
      </details> : null}
      </>}
      {viewMode === 'run' ? <AlgorithmComparisonPanel onCreateExperiment={() => navigate(backUrl)} /> : null}
    </div>
  )
}

