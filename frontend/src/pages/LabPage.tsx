import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { MultiPathComparisonChart } from '../components/charts/MultiPathComparisonChart'
import { PolicyRadar } from '../components/charts/PolicyRadar'
import { Surface } from '../components/Surface'
import { validateMultiVehicleConfiguration } from '../features/path-demo/customVehicleConfig'
import { useMultiPathExperimentStore } from '../features/path-demo/multiPathExperimentStore'
import { DEFAULT_MULTI_PATH_PLANNING_CONFIG, pathDemoApi } from '../features/path-demo/pathDemoApi'
import type { MultiPathDemoScenario, MultiPathVehicleRequest } from '../features/path-demo/types'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'
import './LabPage.css'

type ExperimentMode = 'preset' | 'custom'
type ExperimentStatus = 'idle' | 'running' | 'success' | 'failed'

const algorithmLabel = { astar: '时空 A*', dijkstra: '时空 Dijkstra' }

export function LabPage() {
  const navigate = useNavigate()
  const selectedTopologyId = usePlatformStore((state) => state.selectedTopologyId)
  const selectedScenarioId = usePlatformStore((state) => state.selectedScenarioId)
  const evaluation = usePlatformStore((state) => state.evaluation)
  const setEvaluation = usePlatformStore((state) => state.setEvaluation)
  const customDraft = useMultiPathExperimentStore((state) => state.customDraft)
  const multiEvaluation = useMultiPathExperimentStore((state) => state.evaluation)
  const setMultiEvaluation = useMultiPathExperimentStore((state) => state.setEvaluation)
  const setViewedAlgorithm = useMultiPathExperimentStore((state) => state.setViewedAlgorithm)
  const scenariosQuery = useQuery({ queryKey: ['scenarios'], queryFn: api.listScenarios })
  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies })
  const [multiTopologyId, setMultiTopologyId] = useState('')
  const [experimentMode, setExperimentMode] = useState<ExperimentMode>('preset')
  const [presetScenarioId, setPresetScenarioId] = useState<MultiPathDemoScenario['id']>('parallel')
  const [experimentStatus, setExperimentStatus] = useState<ExperimentStatus>('idle')
  const [experimentError, setExperimentError] = useState('')
  const requestSequenceRef = useRef(0)
  const inputSignatureRef = useRef('')

  const evaluateMutation = useMutation({
    mutationFn: () => api.evaluateAlgorithms(selectedTopologyId, selectedScenarioId),
    onSuccess: (result) => setEvaluation(result.results),
  })

  useEffect(() => {
    if (multiTopologyId || !topologiesQuery.data?.length) return
    const preferred = topologiesQuery.data.find((item) => item.name === '666') ?? topologiesQuery.data[0]
    setMultiTopologyId(preferred.id)
  }, [multiTopologyId, topologiesQuery.data])

  const multiScenariosQuery = useQuery({
    queryKey: ['path-demo-multi-scenarios', multiTopologyId],
    queryFn: () => pathDemoApi.multiScenarios(multiTopologyId),
    enabled: Boolean(multiTopologyId),
  })
  const selectedPreset = multiScenariosQuery.data?.scenarios.find((item) => item.id === presetScenarioId)
  const experimentVehicles = useMemo<MultiPathVehicleRequest[]>(
    () => experimentMode === 'preset' ? (selectedPreset?.vehicles ?? []) : (customDraft?.vehicles ?? []),
    [customDraft?.vehicles, experimentMode, selectedPreset?.vehicles],
  )
  const selectedTopologyVersion = topologiesQuery.data?.find((item) => item.id === multiTopologyId)?.payload.meta.version
  const experimentPlanningConfig = experimentMode === 'custom'
    ? (customDraft?.planningConfig ?? DEFAULT_MULTI_PATH_PLANNING_CONFIG)
    : DEFAULT_MULTI_PATH_PLANNING_CONFIG
  const customDraftMatches = Boolean(
    customDraft
      && customDraft.topologyId === multiTopologyId
      && customDraft.topologyVersion === selectedTopologyVersion,
  )
  const customConfigurationError = customDraft && customDraftMatches && multiScenariosQuery.data
    ? validateMultiVehicleConfiguration(customDraft.vehicles, multiScenariosQuery.data.road_cells)
    : ''
  const experimentInputError = experimentMode === 'custom'
    ? !customDraft
      ? '尚无自定义车辆配置，请先在路径演示页面配置。'
      : !customDraftMatches
        ? '自定义配置所属拓扑或版本与当前选择不一致。'
        : !customDraft.vehicles.length
          ? '自定义车辆配置为空。'
          : !multiScenariosQuery.data
            ? '正在读取可行驶道路数据。'
            : customConfigurationError
    : !selectedPreset ? '正在读取预设冲突场景。' : ''

  const inputSignature = JSON.stringify({
    multiTopologyId,
    experimentMode,
    presetScenarioId: experimentMode === 'preset' ? presetScenarioId : null,
    customDraft: experimentMode === 'custom' ? customDraft : null,
  })
  useEffect(() => {
    if (!multiTopologyId) return
    if (!inputSignatureRef.current) {
      inputSignatureRef.current = inputSignature
      return
    }
    if (inputSignatureRef.current === inputSignature) return
    inputSignatureRef.current = inputSignature
    requestSequenceRef.current += 1
    setExperimentStatus('idle')
    setExperimentError('')
    setMultiEvaluation(null)
  }, [inputSignature, multiTopologyId, setMultiEvaluation])

  const runMultiEvaluation = async () => {
    if (!multiTopologyId || experimentInputError) return
    const requestId = requestSequenceRef.current + 1
    requestSequenceRef.current = requestId
    setExperimentStatus('running')
    setExperimentError('')
    setMultiEvaluation(null)
    try {
      const result = await pathDemoApi.evaluateMulti({
        topologyId: multiTopologyId,
        mode: experimentMode,
        scenarioId: experimentMode === 'preset' ? presetScenarioId : undefined,
        vehicles: experimentMode === 'custom' ? experimentVehicles.map((vehicle) => ({ ...vehicle })) : [],
        planningConfig: experimentMode === 'custom' ? customDraft?.planningConfig : undefined,
      })
      if (requestSequenceRef.current !== requestId) return
      setMultiEvaluation(result)
      setExperimentStatus('success')
    } catch (error) {
      if (requestSequenceRef.current !== requestId) return
      setExperimentStatus('failed')
      setExperimentError(error instanceof Error ? error.message : '多车算法评测失败')
    }
  }

  const openTrajectory = (algorithm: 'astar' | 'dijkstra') => {
    setViewedAlgorithm(algorithm)
    navigate(`/lab/multi-path-result/${algorithm}`)
  }

  const dispatchCases = evaluation.filter((item) => item.category === 'dispatch')
  const pathCases = evaluation.filter((item) => item.category === 'path')
  const conveyorCases = evaluation.filter((item) => item.category === 'conveyor')
  const radarData = dispatchCases.length >= 2
    ? [
        { metric: '吞吐量', primary: dispatchCases[0].throughput, secondary: dispatchCases[1].throughput },
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
        className="multi-path-lab"
        title="多车路径规划实验"
        subtitle="在相同拓扑、车辆与规划约束下真实对比时空 A* 和时空 Dijkstra；这是离线路径规划评测，不是完整仓储任务仿真。"
        action={
          <button
            className="accent-button"
            onClick={() => void runMultiEvaluation()}
            disabled={!multiTopologyId || Boolean(experimentInputError) || experimentStatus === 'running'}
            type="button"
          >
            {experimentStatus === 'running' ? '真实评测中…' : '运行 A* / Dijkstra 对比'}
          </button>
        }
      >
        <div className="multi-path-lab-config">
          <label>
            <span>仓库拓扑</span>
            <select value={multiTopologyId} onChange={(event) => setMultiTopologyId(event.target.value)}>
              {(topologiesQuery.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>
            <span>实验模式</span>
            <select value={experimentMode} onChange={(event) => setExperimentMode(event.target.value as ExperimentMode)}>
              <option value="preset">预设冲突场景 A/B/C</option>
              <option value="custom">路径演示页当前自定义配置</option>
            </select>
          </label>
          {experimentMode === 'preset' ? (
            <label>
              <span>预设场景</span>
              <select value={presetScenarioId} onChange={(event) => setPresetScenarioId(event.target.value as MultiPathDemoScenario['id'])}>
                {(multiScenariosQuery.data?.scenarios ?? []).map((scenario) => (
                  <option key={scenario.id} value={scenario.id}>{scenario.name}</option>
                ))}
              </select>
            </label>
          ) : (
            <div className="multi-path-lab-draft">
              <span>自定义配置</span>
              <strong>{customDraftMatches ? `${experimentVehicles.length} 辆 CTU` : '不可用'}</strong>
              <Link to="/path-demo">前往路径演示配置</Link>
            </div>
          )}
          <fieldset className="multi-path-algorithms">
            <legend>算法选择</legend>
            <label><input type="checkbox" checked disabled readOnly /> 时空 A*</label>
            <label><input type="checkbox" checked disabled readOnly /> 时空 Dijkstra</label>
          </fieldset>
        </div>
        <div className="multi-path-contract">
          <span>车辆：{experimentVehicles.length}</span>
          <span>时间片：{experimentPlanningConfig.time_step_seconds} s</span>
          <span>演示速度：{experimentPlanningConfig.speed_cells_per_second} 格/s</span>
          <span>安全距离由车辆尺寸与安全边距计算</span>
        </div>
        {selectedPreset?.description && experimentMode === 'preset' ? <p className="muted-copy">{selectedPreset.description}</p> : null}
        {experimentInputError ? <p className="multi-path-lab-error" role="alert">{experimentInputError}</p> : null}
        {experimentError ? <p className="multi-path-lab-error" role="alert">{experimentError}</p> : null}
        {multiEvaluation ? (
          <>
            <p className="multi-path-snapshot-note">
              实验快照 {multiEvaluation.experiment_id} · 拓扑版本 {multiEvaluation.input_snapshot.topology_version} · 内存生命周期，刷新页面后失效。
            </p>
            <div className="table-wrap">
              <table aria-label="多车路径算法对比结果">
                <thead>
                  <tr>
                    <th>算法</th><th>状态</th><th>总路径（格）</th><th>规划耗时（ms）</th><th>Makespan（s）</th>
                    <th>总等待（s）</th><th>平均等待（s/车）</th><th>避让次数</th><th>检测/解决/残留冲突</th><th>轨迹</th>
                  </tr>
                </thead>
                <tbody>
                  {multiEvaluation.results.map((result) => (
                    <tr key={result.algorithm}>
                      <td>{algorithmLabel[result.algorithm]}</td>
                      <td>{result.planning_status === 'success' ? '成功' : `失败：${result.error_reason}`}</td>
                      <td>{result.metrics ? result.metrics.total_path_distance.toFixed(2) : '—'}</td>
                      <td>{result.planning_time_ms.toFixed(3)}</td>
                      <td>{result.metrics ? result.metrics.makespan_seconds.toFixed(2) : '—'}</td>
                      <td>{result.metrics ? result.metrics.total_wait_time_seconds.toFixed(2) : '—'}</td>
                      <td>{result.metrics ? result.metrics.average_wait_time_seconds.toFixed(2) : '—'}</td>
                      <td>{result.metrics ? result.metrics.avoidance_count : '—'}</td>
                      <td>{result.metrics ? `${result.metrics.detected_conflicts}/${result.metrics.resolved_conflicts}/${result.metrics.remaining_conflicts}` : '—'}</td>
                      <td><button className="ghost-button" disabled={!result.plan} onClick={() => openTrajectory(result.algorithm)} type="button">查看 3D 轨迹</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <MultiPathComparisonChart results={multiEvaluation.results} />
            <div className="multi-path-vehicle-metrics">
              {multiEvaluation.results.map((result) => (
                <section key={result.algorithm}>
                  <h3>{algorithmLabel[result.algorithm]} 各车辆统计</h3>
                  {result.metrics ? (
                    <div className="table-wrap">
                      <table aria-label={`${algorithmLabel[result.algorithm]} 各车辆统计`}>
                        <thead><tr><th>车辆</th><th>距离（格）</th><th>运行时间（s）</th><th>等待（s）</th><th>避让次数</th><th>访问状态</th></tr></thead>
                        <tbody>
                          {result.metrics.vehicles.map((vehicle) => (
                            <tr key={vehicle.vehicle_id}>
                              <td>{vehicle.vehicle_id}</td><td>{vehicle.total_distance.toFixed(2)}</td>
                              <td>{vehicle.estimated_duration_seconds.toFixed(2)}</td><td>{vehicle.wait_time_seconds.toFixed(2)}</td>
                              <td>{vehicle.avoidance_count}</td><td>{vehicle.visited_states}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : <p className="multi-path-lab-error">{result.error_reason}</p>}
                </section>
              ))}
            </div>
          </>
        ) : null}
      </Surface>

      <Surface
        title="算法实验室"
        subtitle="围绕调度、路径和输送线放行策略做可比较、可回放的实验。"
        action={
          <button className="accent-button" onClick={() => evaluateMutation.mutate()} disabled={!selectedTopologyId || !selectedScenarioId} type="button">
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
            <thead><tr><th>类别</th><th>策略</th><th>吞吐量</th><th>平均完成时长</th><th>输送线利用率</th><th>拥堵次数</th><th>死锁次数</th><th>紧急插单完成</th></tr></thead>
            <tbody>
              {evaluation.map((item) => (
                <tr key={`${item.category}-${item.label}`}>
                  <td>{item.category}</td><td>{item.label}</td><td>{item.throughput}</td><td>{item.avgCompletionTicks}</td>
                  <td>{Math.round(item.conveyorUtilization * 100)}%</td><td>{item.congestionEvents}</td><td>{item.deadlocksDetected}</td><td>{item.urgentOrdersHandled}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Surface>

      <Surface title="调度策略雷达" subtitle="优先比较两组任务分配策略的整体表现。">
        {radarData.length ? (
          <PolicyRadar data={radarData} series={[
            { key: 'primary', name: dispatchCases[0].label, color: '#64748B' },
            { key: 'secondary', name: dispatchCases[1].label, color: '#F97316' },
          ]} />
        ) : <p className="muted-copy">先运行一次算法评估。</p>}
      </Surface>

      <Surface title="分类摘要" subtitle="把实验室从单次对比升级成真正可用的试验台。">
        <div className="stack-list">
          <div className="list-item"><div><strong>任务分配策略</strong><p>{dispatchCases.map((item) => item.label).join(' / ') || '未评估'}</p></div><span>{dispatchCases.length} 组</span></div>
          <div className="list-item"><div><strong>路径策略</strong><p>{pathCases.map((item) => item.label).join(' / ') || '未评估'}</p></div><span>{pathCases.length} 组</span></div>
          <div className="list-item"><div><strong>输送线放行策略</strong><p>{conveyorCases.map((item) => item.label).join(' / ') || '未评估'}</p></div><span>{conveyorCases.length} 组</span></div>
        </div>
      </Surface>
    </div>
  )
}
