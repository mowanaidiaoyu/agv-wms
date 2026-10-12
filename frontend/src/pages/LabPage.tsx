import { useMutation, useQuery } from '@tanstack/react-query'
import { Surface } from '../components/Surface'
import { PolicyRadar } from '../components/charts/PolicyRadar'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'

export function LabPage() {
  const selectedTopologyId = usePlatformStore((state) => state.selectedTopologyId)
  const selectedScenarioId = usePlatformStore((state) => state.selectedScenarioId)
  const evaluation = usePlatformStore((state) => state.evaluation)
  const setEvaluation = usePlatformStore((state) => state.setEvaluation)
  const scenariosQuery = useQuery({ queryKey: ['scenarios'], queryFn: api.listScenarios })

  const evaluateMutation = useMutation({
    mutationFn: () => api.evaluateAlgorithms(selectedTopologyId, selectedScenarioId),
    onSuccess: (result) => setEvaluation(result.results),
  })

  const dispatchCases = evaluation.filter((item) => item.category === 'dispatch')
  const pathCases = evaluation.filter((item) => item.category === 'path')
  const conveyorCases = evaluation.filter((item) => item.category === 'conveyor')

  const radarData =
    dispatchCases.length >= 2
      ? [
          {
            metric: '吞吐量',
            primary: dispatchCases[0].throughput,
            secondary: dispatchCases[1].throughput,
          },
          {
            metric: '平均完成时长反向分',
            primary: Math.max(1, 100 - dispatchCases[0].avgCompletionTicks),
            secondary: Math.max(1, 100 - dispatchCases[1].avgCompletionTicks),
          },
          {
            metric: '拥堵控制反向分',
            primary: Math.max(1, 100 - dispatchCases[0].congestionEvents * 10),
            secondary: Math.max(1, 100 - dispatchCases[1].congestionEvents * 10),
          },
          {
            metric: '死锁控制反向分',
            primary: Math.max(1, 100 - dispatchCases[0].deadlocksDetected * 20),
            secondary: Math.max(1, 100 - dispatchCases[1].deadlocksDetected * 20),
          },
        ]
      : []

  return (
    <div className="page-grid lab-grid">
      <Surface
        title="算法实验室"
        subtitle="围绕调度、路径和输送线放行策略做可比较、可回放的实验。"
        action={
          <button
            className="accent-button"
            onClick={() => evaluateMutation.mutate()}
            disabled={!selectedTopologyId || !selectedScenarioId}
            type="button"
          >
            运行评估
          </button>
        }
      >
        <div className="experiment-summary">
          <p>当前场景: {scenariosQuery.data?.find((item) => item.id === selectedScenarioId)?.payload.name ?? '未选择'}</p>
          <p>当前拓扑: {selectedTopologyId || '未选择'}</p>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>类别</th>
                <th>策略</th>
                <th>吞吐量</th>
                <th>平均完成时长</th>
                <th>输送线利用率</th>
                <th>拥堵次数</th>
                <th>死锁次数</th>
                <th>紧急插单完成</th>
              </tr>
            </thead>
            <tbody>
              {evaluation.map((item) => (
                <tr key={`${item.category}-${item.label}`}>
                  <td>{item.category}</td>
                  <td>{item.label}</td>
                  <td>{item.throughput}</td>
                  <td>{item.avgCompletionTicks}</td>
                  <td>{Math.round(item.conveyorUtilization * 100)}%</td>
                  <td>{item.congestionEvents}</td>
                  <td>{item.deadlocksDetected}</td>
                  <td>{item.urgentOrdersHandled}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Surface>

      <Surface title="调度策略雷达" subtitle="优先比较两组任务分配策略的整体表现。">
        {radarData.length ? (
          <PolicyRadar
            data={radarData}
            series={[
              { key: 'primary', name: dispatchCases[0].label, color: '#64748B' },
              { key: 'secondary', name: dispatchCases[1].label, color: '#F97316' },
            ]}
          />
        ) : (
          <p className="muted-copy">先运行一次算法评估。</p>
        )}
      </Surface>

      <Surface title="分类摘要" subtitle="把实验室从单次对比升级成真正可用的试验台。">
        <div className="stack-list">
          <div className="list-item">
            <div>
              <strong>任务分配策略</strong>
              <p>{dispatchCases.map((item) => item.label).join(' / ') || '未评估'}</p>
            </div>
            <span>{dispatchCases.length} 组</span>
          </div>
          <div className="list-item">
            <div>
              <strong>路径策略</strong>
              <p>{pathCases.map((item) => item.label).join(' / ') || '未评估'}</p>
            </div>
            <span>{pathCases.length} 组</span>
          </div>
          <div className="list-item">
            <div>
              <strong>输送线放行策略</strong>
              <p>{conveyorCases.map((item) => item.label).join(' / ') || '未评估'}</p>
            </div>
            <span>{conveyorCases.length} 组</span>
          </div>
        </div>
      </Surface>
    </div>
  )
}
