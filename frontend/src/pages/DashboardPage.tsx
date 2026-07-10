import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Surface } from '../components/Surface'
import { KpiGrid } from '../components/KpiGrid'
import { TrendChart } from '../components/charts/TrendChart'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'

export function DashboardPage() {
  const navigate = useNavigate()
  const liveMetrics = usePlatformStore((state) => state.liveMetrics)
  const selectedTopologyId = usePlatformStore((state) => state.selectedTopologyId)
  const selectedScenarioId = usePlatformStore((state) => state.selectedScenarioId)
  const setSelectedTopologyId = usePlatformStore((state) => state.setSelectedTopologyId)
  const setSelectedScenarioId = usePlatformStore((state) => state.setSelectedScenarioId)
  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: api.listSimulationRuns, refetchInterval: 3000 })
  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies })
  const scenariosQuery = useQuery({ queryKey: ['scenarios'], queryFn: api.listScenarios })

  useEffect(() => {
    const firstTopology = topologiesQuery.data?.[0]
    const firstScenario = scenariosQuery.data?.find((item) => item.payload.topologyId === firstTopology?.id) ?? scenariosQuery.data?.[0]
    if (!selectedTopologyId && firstTopology) {
      setSelectedTopologyId(firstTopology.id)
    }
    if (!selectedScenarioId && firstScenario) {
      setSelectedScenarioId(firstScenario.id)
    }
  }, [scenariosQuery.data, selectedScenarioId, selectedTopologyId, setSelectedScenarioId, setSelectedTopologyId, topologiesQuery.data])

  const runs = runsQuery.data ?? []
  const trendData = runs
    .slice(0, 8)
    .reverse()
    .map((run, index) => ({
      tick: index + 1,
      throughput: run.metrics.throughput,
      deadlocks: run.metrics.deadlocksDetected,
    }))

  return (
    <div className="page-grid">
      <Surface
        title="系统总览"
        subtitle="围绕四向车、CTU 与输送线协同调度构建的本地演示驾驶舱。"
        className="hero-surface"
      >
        <div className="hero-strip">
          <div>
            <p className="eyebrow">Topology</p>
            <h2>{topologiesQuery.data?.length ?? 0}</h2>
            <span>已建拓扑</span>
          </div>
          <div>
            <p className="eyebrow">Scenarios</p>
            <h2>{scenariosQuery.data?.length ?? 0}</h2>
            <span>场景模板</span>
          </div>
          <div>
            <p className="eyebrow">Runs</p>
            <h2>{runs.length}</h2>
            <span>仿真运行记录</span>
          </div>
          <div>
            <p className="eyebrow">Status</p>
            <h2>{runs[0]?.status ?? 'IDLE'}</h2>
            <span>最近运行状态</span>
          </div>
        </div>
      </Surface>

      <Surface
        title="演示模式"
        subtitle="默认样例已自动注入数据库，建议从这里直接进入闭环演示。"
        action={
          <div className="surface-actions">
            <button className="ghost-button" onClick={() => navigate('/topology')}>
              查看拓扑
            </button>
            <button className="ghost-button" onClick={() => navigate('/scenario')}>
              检查场景
            </button>
            <button className="accent-button" onClick={() => navigate('/simulation')}>
              直接进入仿真
            </button>
          </div>
        }
      >
        <div className="demo-banner">
          <div>
            <strong>当前默认拓扑</strong>
            <p>{topologiesQuery.data?.find((item) => item.id === selectedTopologyId)?.payload.name ?? '未选择'}</p>
          </div>
          <div>
            <strong>当前默认场景</strong>
            <p>{scenariosQuery.data?.find((item) => item.id === selectedScenarioId)?.payload.name ?? '未选择'}</p>
          </div>
        </div>
      </Surface>

      <Surface title="关键指标" subtitle="展示当前运行或最近一次运行的核心观测值。">
        <KpiGrid metrics={liveMetrics ?? runs[0]?.metrics} />
      </Surface>

      <Surface title="吞吐趋势" subtitle="最近运行记录中的吞吐与死锁情况。">
        <TrendChart data={trendData} dataKey="throughput" />
      </Surface>

      <Surface title="最近运行" subtitle="快速回到问题现场，定位拥堵、阻塞和死锁。">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>运行 ID</th>
                <th>状态</th>
                <th>吞吐</th>
                <th>死锁</th>
                <th>输送线利用率</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>{run.id}</td>
                  <td>{run.status}</td>
                  <td>{run.metrics.throughput}</td>
                  <td>{run.metrics.deadlocksDetected}</td>
                  <td>{Math.round(run.metrics.conveyorUtilization * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Surface>
    </div>
  )
}
