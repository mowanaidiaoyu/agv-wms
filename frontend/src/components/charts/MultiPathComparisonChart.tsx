import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { MultiPathEvaluationAlgorithmResult } from '../../features/path-demo/types'

export function MultiPathComparisonChart({ results }: { results: MultiPathEvaluationAlgorithmResult[] }) {
  const data = results
    .filter((result) => result.metrics)
    .map((result) => ({
      algorithm: result.algorithm === 'astar' ? '时空 A*' : '时空 Dijkstra',
      makespan: result.metrics?.makespan_seconds ?? 0,
      wait: result.metrics?.total_wait_time_seconds ?? 0,
    }))

  if (!data.length) return <p className="muted-copy">没有可绘制的成功评测结果。</p>

  return (
    <div className="chart-frame" aria-label="多车算法时间指标图">
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#c8d3df" />
          <XAxis dataKey="algorithm" tick={{ fill: '#334155', fontSize: 12 }} />
          <YAxis unit=" s" tick={{ fill: '#64748b', fontSize: 12 }} />
          <Tooltip />
          <Legend />
          <Bar dataKey="makespan" name="Makespan（秒）" fill="#0ea5e9" radius={[6, 6, 0, 0]} />
          <Bar dataKey="wait" name="总等待（秒）" fill="#f97316" radius={[6, 6, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
