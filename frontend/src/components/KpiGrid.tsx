import type { SimulationMetrics } from '../types'

const fallback: SimulationMetrics = {
  throughput: 0,
  completedTasks: 0,
  pendingTasks: 0,
  activeRobots: 0,
  blockedRobots: 0,
  conveyorUtilization: 0,
  deadlocksDetected: 0,
  congestionEvents: 0,
  urgentOrdersHandled: 0,
  avgEtaTicks: 0,
  avgCompletionTicks: 0,
  stationQueueDepth: 0,
  conveyorQueueDepth: 0,
  bufferOccupancy: 0,
}

export function KpiGrid({ metrics }: { metrics?: Partial<SimulationMetrics> | null }) {
  const merged = { ...fallback, ...(metrics ?? {}) }
  const cards = [
    ['吞吐量', merged.throughput],
    ['已完成任务', merged.completedTasks],
    ['待处理任务', merged.pendingTasks],
    ['活跃设备', merged.activeRobots],
    ['阻塞设备', merged.blockedRobots],
    ['输送线利用率', `${Math.round(merged.conveyorUtilization * 100)}%`],
    ['死锁检测次数', merged.deadlocksDetected],
    ['拥堵事件次数', merged.congestionEvents],
    ['紧急插单完成数', merged.urgentOrdersHandled],
    ['平均 ETA', merged.avgEtaTicks],
    ['平均完成时长', merged.avgCompletionTicks],
    ['缓存位占用', merged.bufferOccupancy],
  ] as const

  return (
    <div className="kpi-grid">
      {cards.map(([label, value]) => (
        <article key={label} className="kpi-card">
          <p>{label}</p>
          <h3>{value}</h3>
        </article>
      ))}
    </div>
  )
}
