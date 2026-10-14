import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { MultiPathComparisonChart } from '../../components/charts/MultiPathComparisonChart'
import { Surface } from '../../components/Surface'
import { api } from '../../lib/api'
import { pathDemoApi } from './pathDemoApi'

const PAGE_SIZE = 10
const algorithmLabel = { astar: '时空 A*', dijkstra: '时空 Dijkstra' }
const statusLabel = { success: '成功', partial_failure: '部分失败', failed: '失败' }

function formatCreatedAt(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

export function MultiPathEvaluationReports({ selectedExperimentId }: { selectedExperimentId?: string }) {
  const [offset, setOffset] = useState(0)
  const detailId = selectedExperimentId ?? ''
  const [exportError, setExportError] = useState('')
  const historyQuery = useQuery({
    queryKey: ['multi-path-evaluations', offset],
    queryFn: () => pathDemoApi.evaluations({ limit: PAGE_SIZE, offset }),
  })
  const detailQuery = useQuery({
    queryKey: ['multi-path-evaluation', detailId],
    queryFn: () => pathDemoApi.evaluation(detailId),
    enabled: Boolean(detailId),
  })
  const topologiesQuery = useQuery({
    queryKey: ['topologies'],
    queryFn: api.listTopologies,
    enabled: Boolean(detailQuery.data),
  })

  const evaluation = detailQuery.data
  const currentTopology = topologiesQuery.data?.find((item) => item.id === evaluation?.input_snapshot.topology_id)
  const canReplay = Boolean(
    evaluation
      && currentTopology
      && currentTopology.payload.meta.version === evaluation.input_snapshot.topology_version,
  )
  const topologyMismatch = Boolean(evaluation && topologiesQuery.isSuccess && !canReplay)
  const topologyLookupFailed = Boolean(evaluation && topologiesQuery.isError)

  const exportSnapshot = async () => {
    if (!detailId) return
    setExportError('')
    try {
      const report = await pathDemoApi.evaluationExport(detailId)
      const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `multi-path-evaluation-${detailId}.json`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      setExportError(error instanceof Error ? error.message : '实验报告导出失败')
    }
  }

  return (
    <>
      <Surface
        className="multi-path-report-history"
        title="多车路径算法实验记录"
        subtitle="服务器持久化的离线多车路径规划评测；与正式仿真运行记录分开管理。"
      >
        {historyQuery.isLoading ? <p className="muted-copy">正在读取实验记录…</p> : null}
        {historyQuery.isError ? <p className="multi-path-lab-error" role="alert">无法读取多车实验记录：{historyQuery.error.message}</p> : null}
        {historyQuery.data?.items.length ? (
          <div className="table-wrap">
            <table aria-label="多车路径算法实验记录">
              <thead><tr><th>实验 ID</th><th>创建时间</th><th>拓扑/版本</th><th>车辆</th><th>算法</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                {historyQuery.data.items.map((item) => (
                  <tr key={item.experiment_id}>
                    <td><code>{item.experiment_id}</code></td>
                    <td>{formatCreatedAt(item.created_at)}</td>
                    <td>{item.topology_name} / {item.topology_version}</td>
                    <td>{item.vehicle_count}</td>
                    <td>{item.algorithms.map((algorithm) => algorithmLabel[algorithm]).join(' / ')}</td>
                    <td>{statusLabel[item.status]}</td>
                    <td><Link to={`/reports/multi-path/${item.experiment_id}`}>查看报告</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : historyQuery.isSuccess ? <p className="muted-copy">暂无持久化的多车路径实验。</p> : null}
        {historyQuery.data ? (
          <div className="multi-path-report-pagination">
            <button className="ghost-button" type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>上一页</button>
            <span>{historyQuery.data.total ? offset + 1 : 0}–{Math.min(offset + PAGE_SIZE, historyQuery.data.total)} / {historyQuery.data.total}</span>
            <button className="ghost-button" type="button" disabled={offset + PAGE_SIZE >= historyQuery.data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>下一页</button>
          </div>
        ) : null}
      </Surface>

      {detailId ? (
        <Surface
          className="multi-path-report-detail"
          title="多车路径实验详情"
          subtitle="指标、失败原因和轨迹均来自保存时的同一不可变结果快照，不会重新规划。"
          action={<button className="ghost-button" type="button" disabled={!evaluation} onClick={() => void exportSnapshot()}>导出实验 JSON</button>}
        >
          {detailQuery.isLoading ? <p className="muted-copy">正在读取实验快照…</p> : null}
          {detailQuery.isError ? <p className="multi-path-lab-error" role="alert">无法读取实验快照：{detailQuery.error.message}</p> : null}
          {exportError ? <p className="multi-path-lab-error" role="alert">导出失败：{exportError}</p> : null}
          {evaluation ? (
            <>
              <dl className="multi-path-report-meta">
                <div><dt>实验 ID</dt><dd>{evaluation.experiment_id}</dd></div>
                <div><dt>创建时间</dt><dd>{formatCreatedAt(evaluation.created_at)}</dd></div>
                <div><dt>拓扑</dt><dd>{evaluation.input_snapshot.topology_name}</dd></div>
                <div><dt>拓扑版本</dt><dd>{evaluation.input_snapshot.topology_version}</dd></div>
                <div><dt>车辆数量</dt><dd>{evaluation.input_snapshot.vehicles.length}</dd></div>
                <div><dt>实验状态</dt><dd>{statusLabel[evaluation.status]}</dd></div>
              </dl>
              <p className="multi-path-report-disclaimer">离线多车路径规划实验，不代表真实设备运行，也不代表完整仓储任务仿真。</p>
              {topologyMismatch ? (
                <p className="multi-path-report-warning" role="alert">
                  当前系统不存在该拓扑版本（快照版本：{evaluation.input_snapshot.topology_version}）。指标仍可查看，但因本 Gate 未归档完整拓扑，已拒绝 3D 回放。
                </p>
              ) : null}
              {topologyLookupFailed ? (
                <p className="multi-path-report-warning" role="alert">
                  无法验证当前拓扑版本：{topologiesQuery.error?.message}。指标仍可查看，但已拒绝 3D 回放。
                </p>
              ) : null}
              <div className="table-wrap">
                <table aria-label="持久化多车算法指标">
                  <thead><tr><th>算法</th><th>状态/原因</th><th>总路径（格）</th><th>规划耗时（ms）</th><th>Makespan（s）</th><th>总等待（s）</th><th>检测/解决/残留冲突</th><th>轨迹</th></tr></thead>
                  <tbody>
                    {evaluation.results.map((result) => (
                      <tr key={result.algorithm}>
                        <td>{algorithmLabel[result.algorithm]}</td>
                        <td>{result.planning_status === 'success' ? '成功' : `失败：${result.error_reason}`}</td>
                        <td>{result.metrics?.total_path_distance.toFixed(2) ?? '—'}</td>
                        <td>{result.planning_time_ms.toFixed(3)}</td>
                        <td>{result.metrics?.makespan_seconds.toFixed(2) ?? '—'}</td>
                        <td>{result.metrics?.total_wait_time_seconds.toFixed(2) ?? '—'}</td>
                        <td>{result.metrics ? `${result.metrics.detected_conflicts}/${result.metrics.resolved_conflicts}/${result.metrics.remaining_conflicts}` : '—'}</td>
                        <td>{result.plan && canReplay ? <Link to={`/lab/multi-path-result/${evaluation.experiment_id}/${result.algorithm}`}>查看 3D 轨迹</Link> : '不可回放'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <MultiPathComparisonChart results={evaluation.results} />
              <div className="multi-path-vehicle-metrics">
                {evaluation.results.map((result) => (
                  <section key={result.algorithm}>
                    <h3>{algorithmLabel[result.algorithm]} 各车辆统计</h3>
                    {result.metrics ? (
                      <div className="table-wrap">
                        <table aria-label={`${algorithmLabel[result.algorithm]} 历史车辆统计`}>
                          <thead><tr><th>车辆</th><th>距离（格）</th><th>运行时间（s）</th><th>等待（s）</th><th>避让次数</th><th>访问状态</th></tr></thead>
                          <tbody>{result.metrics.vehicles.map((vehicle) => (
                            <tr key={vehicle.vehicle_id}>
                              <td>{vehicle.vehicle_id}</td><td>{vehicle.total_distance.toFixed(2)}</td>
                              <td>{vehicle.estimated_duration_seconds.toFixed(2)}</td><td>{vehicle.wait_time_seconds.toFixed(2)}</td>
                              <td>{vehicle.avoidance_count}</td><td>{vehicle.visited_states}</td>
                            </tr>
                          ))}</tbody>
                        </table>
                      </div>
                    ) : <p className="multi-path-lab-error">{result.error_reason}</p>}
                  </section>
                ))}
              </div>
            </>
          ) : null}
        </Surface>
      ) : null}
    </>
  )
}
