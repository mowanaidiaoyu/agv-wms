import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { emptyScenario } from '../data/defaults'
import { api } from '../lib/api'
import { Surface } from '../components/Surface'
import { usePlatformStore } from '../store/usePlatformStore'
import type { RobotConfig, ScenarioPayload } from '../types'

export function ScenarioPage() {
  const queryClient = useQueryClient()
  const selectedTopologyId = usePlatformStore((state) => state.selectedTopologyId)
  const selectedScenarioId = usePlatformStore((state) => state.selectedScenarioId)
  const setSelectedTopologyId = usePlatformStore((state) => state.setSelectedTopologyId)
  const setSelectedScenarioId = usePlatformStore((state) => state.setSelectedScenarioId)
  const scenariosQuery = useQuery({ queryKey: ['scenarios'], queryFn: api.listScenarios })
  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies })
  const [draft, setDraft] = useState<ScenarioPayload>(emptyScenario(selectedTopologyId))

  useEffect(() => {
    const current =
      scenariosQuery.data?.find((item) => item.id === selectedScenarioId) ??
      scenariosQuery.data?.find((item) => item.payload.topologyId === selectedTopologyId) ??
      scenariosQuery.data?.[0]
    if (current) {
      setSelectedScenarioId(current.id)
      setSelectedTopologyId(current.payload.topologyId)
      setDraft(current.payload)
    } else if (selectedTopologyId) {
      setDraft(emptyScenario(selectedTopologyId))
    }
  }, [scenariosQuery.data, selectedScenarioId, selectedTopologyId, setSelectedScenarioId, setSelectedTopologyId])

  const saveMutation = useMutation({
    mutationFn: api.saveScenario,
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ['scenarios'] })
      setSelectedScenarioId(saved.id)
    },
  })

  const topologyOptions = topologiesQuery.data ?? []
  const nodeSuggestions = useMemo(() => {
    const topology = topologyOptions.find((item) => item.id === draft.topologyId)?.payload
    return topology?.nodes.map((node) => node.id) ?? []
  }, [draft.topologyId, topologyOptions])

  function updateRobot(index: number, next: Partial<RobotConfig>) {
    setDraft((prev) => ({
      ...prev,
      robots: prev.robots.map((robot, robotIndex) => (robotIndex === index ? { ...robot, ...next } : robot)),
    }))
  }

  return (
    <div className="page-grid scenario-grid">
      <Surface
        title="场景配置台"
        subtitle="配置设备、订单强度、输送线放行阈值和故障注入参数。"
        action={
          <button className="accent-button" onClick={() => saveMutation.mutate(draft)}>
            保存场景
          </button>
        }
      >
        <div className="field-grid">
          <label>
            <span>场景名称</span>
            <input value={draft.name} onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))} />
          </label>
          <label>
            <span>关联拓扑</span>
            <select
              value={draft.topologyId}
              onChange={(event) => {
                setSelectedTopologyId(event.target.value)
                setDraft((prev) => ({ ...prev, topologyId: event.target.value }))
              }}
            >
              {topologyOptions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.payload.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>调度策略</span>
            <select
              value={draft.dispatchPolicy}
              onChange={(event) => setDraft((prev) => ({ ...prev, dispatchPolicy: event.target.value as ScenarioPayload['dispatchPolicy'] }))}
            >
              <option value="greedy">greedy</option>
              <option value="hungarian">hungarian</option>
            </select>
          </label>
          <label>
            <span>路径策略</span>
            <select
              value={draft.pathPolicy}
              onChange={(event) => setDraft((prev) => ({ ...prev, pathPolicy: event.target.value as ScenarioPayload['pathPolicy'] }))}
            >
              <option value="astar">astar</option>
              <option value="dijkstra">dijkstra</option>
            </select>
          </label>
          <label>
            <span>订单速率 / 分钟</span>
            <input
              type="number"
              value={draft.orderGenerator.ratePerMinute}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...prev,
                  orderGenerator: { ...prev.orderGenerator, ratePerMinute: Number(event.target.value) },
                }))
              }
            />
          </label>
          <label>
            <span>运行 Tick 数</span>
            <input
              type="number"
              value={draft.durationTicks}
              onChange={(event) => setDraft((prev) => ({ ...prev, durationTicks: Number(event.target.value) }))}
            />
          </label>
          <label>
            <span>输送线放行阈值</span>
            <input
              type="number"
              step="0.05"
              value={draft.conveyorPolicy.releaseThreshold}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...prev,
                  conveyorPolicy: { ...prev.conveyorPolicy, releaseThreshold: Number(event.target.value) },
                }))
              }
            />
          </label>
          <label>
            <span>紧急插单 Tick</span>
            <input
              type="number"
              value={draft.orderGenerator.urgentInsertionTick ?? 0}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...prev,
                  orderGenerator: { ...prev.orderGenerator, urgentInsertionTick: Number(event.target.value) },
                }))
              }
            />
          </label>
          <label>
            <span>设备故障 Tick</span>
            <input
              type="number"
              value={draft.faultInjection.robotFailureTick ?? 0}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...prev,
                  faultInjection: { ...prev.faultInjection, robotFailureTick: Number(event.target.value) },
                }))
              }
            />
          </label>
          <label>
            <span>输送线拥堵 Tick</span>
            <input
              type="number"
              value={draft.faultInjection.conveyorJammedTick ?? 0}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...prev,
                  faultInjection: { ...prev.faultInjection, conveyorJammedTick: Number(event.target.value) },
                }))
              }
            />
          </label>
        </div>
      </Surface>

      <Surface
        title="设备列表"
        subtitle="配置 CTU 和四向车的起点、速度与电量。"
        action={
          <button
            className="ghost-button"
            onClick={() =>
              setDraft((prev) => ({
                ...prev,
                robots: [
                  ...prev.robots,
                  {
                    id: `robot-${crypto.randomUUID().slice(0, 4)}`,
                    type: 'ctu',
                    startNode: nodeSuggestions[0] ?? '',
                    speed: 1,
                    battery: 100,
                    enabled: true,
                  },
                ],
              }))
            }
          >
            <Plus size={16} />
            添加设备
          </button>
        }
      >
        <div className="robot-grid">
          {draft.robots.map((robot, index) => (
            <div key={robot.id} className="robot-card">
              <label>
                <span>设备 ID</span>
                <input value={robot.id} onChange={(event) => updateRobot(index, { id: event.target.value })} />
              </label>
              <label>
                <span>设备类型</span>
                <select value={robot.type} onChange={(event) => updateRobot(index, { type: event.target.value as RobotConfig['type'] })}>
                  <option value="ctu">ctu</option>
                  <option value="shuttle">shuttle</option>
                </select>
              </label>
              <label>
                <span>起始节点</span>
                <input list={`node-options-${index}`} value={robot.startNode} onChange={(event) => updateRobot(index, { startNode: event.target.value })} />
                <datalist id={`node-options-${index}`}>
                  {nodeSuggestions.map((nodeId) => (
                    <option key={nodeId} value={nodeId} />
                  ))}
                </datalist>
              </label>
              <label>
                <span>速度</span>
                <input type="number" value={robot.speed} onChange={(event) => updateRobot(index, { speed: Number(event.target.value) })} />
              </label>
              <label>
                <span>电量</span>
                <input type="number" value={robot.battery} onChange={(event) => updateRobot(index, { battery: Number(event.target.value) })} />
              </label>
            </div>
          ))}
        </div>
      </Surface>
    </div>
  )
}
