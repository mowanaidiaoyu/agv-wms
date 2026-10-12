import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Surface } from '../components/Surface'
import { HeatmapGrid } from '../components/charts/HeatmapGrid'
import { TrendChart } from '../components/charts/TrendChart'
import { api } from '../lib/api'

export function ReportsPage() {
  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: api.listSimulationRuns, refetchInterval: 4000 })
  const [runId, setRunId] = useState('')

  useEffect(() => {
    if (!runId && runsQuery.data?.[0]?.id) {
      setRunId(runsQuery.data[0].id)
    }
  }, [runId, runsQuery.data])

  const eventsQuery = useQuery({
    queryKey: ['events', runId],
    queryFn: () => api.getSimulationEvents(runId),
    enabled: Boolean(runId),
  })
  const metricsQuery = useQuery({
    queryKey: ['metrics', runId],
    queryFn: () => api.getSimulationMetrics(runId),
    enabled: Boolean(runId),
  })

  const trendData = (eventsQuery.data ?? [])
    .filter((event) => (event.event_type ?? event.type) === 'metrics_snapshot')
    .map((event) => ({
      tick: event.tick,
      conveyor: Number(event.payload.conveyorUtilization ?? 0),
      stationQueue: Number(event.payload.stationQueueDepth ?? 0),
    }))

  const heatmapCells = ['robot_position', 'task_state_changed', 'alert', 'deadlock_detected', 'resource_occupied'].map((type) => ({
    label: type,
    value: (eventsQuery.data ?? []).filter((event) => (event.event_type ?? event.type) === type).length,
  }))

  const incidentEvents = (eventsQuery.data ?? []).filter((event) => {
    const type = event.event_type ?? event.type
    return type === 'alert' || type === 'deadlock_detected'
  })

  return (
    <div className="page-grid reports-grid">
      <Surface title="回放与报告" subtitle="从事件时间轴、热力图和指标面板复盘一次完整协同调度运行。">
        <div className="editor-toolbar">
          <select value={runId} onChange={(event) => setRunId(event.target.value)}>
            {runsQuery.data?.map((run) => (
              <option key={run.id} value={run.id}>
                {run.id}
              </option>
            ))}
          </select>
          <button
            className="ghost-button"
            type="button"
            onClick={() => {
              const payload = {
                runId,
                metrics: metricsQuery.data,
                events: eventsQuery.data,
              }
              const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
              const url = URL.createObjectURL(blob)
              const anchor = document.createElement('a')
              anchor.href = url
              anchor.download = `${runId || 'report'}.json`
              anchor.click()
              URL.revokeObjectURL(url)
            }}
          >
            导出 JSON
          </button>
        </div>
      </Surface>

      <Surface title="指标摘要" subtitle="最终运行结果快照。">
        <div className="metrics-stack">
          <div>
            <span>吞吐量</span>
            <strong>{metricsQuery.data?.throughput ?? 0}</strong>
          </div>
          <div>
            <span>平均完成时长</span>
            <strong>{metricsQuery.data?.avgCompletionTicks ?? 0}</strong>
          </div>
          <div>
            <span>拥堵次数</span>
            <strong>{metricsQuery.data?.congestionEvents ?? 0}</strong>
          </div>
          <div>
            <span>死锁次数</span>
            <strong>{metricsQuery.data?.deadlocksDetected ?? 0}</strong>
          </div>
        </div>
      </Surface>

      <Surface title="输送线利用率趋势" subtitle="基于 `metrics_snapshot` 事件回放运行过程。">
        <TrendChart data={trendData} dataKey="conveyor" color="#64748B" />
      </Surface>

      <Surface title="事件热力图" subtitle="按事件类型观察系统最繁忙的信号通道。">
        <HeatmapGrid cells={heatmapCells} />
      </Surface>

      <Surface title="异常时间轴" subtitle="追踪死锁、拥堵和故障的原因与建议。">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Tick</th>
                <th>事件</th>
                <th>摘要</th>
                <th>建议</th>
              </tr>
            </thead>
            <tbody>
              {incidentEvents.map((event, index) => (
                <tr key={`${event.tick}-${index}`}>
                  <td>{event.tick}</td>
                  <td>{event.event_type ?? event.type}</td>
                  <td>{String(event.payload.message ?? event.payload.category ?? '-')}</td>
                  <td>{String(event.payload.suggestion ?? '-')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Surface>
    </div>
  )
}
