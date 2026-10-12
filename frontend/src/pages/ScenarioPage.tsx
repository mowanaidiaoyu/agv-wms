import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { emptyScenario } from '../data/defaults'
import { Surface } from '../components/Surface'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'
import type { RobotConfig, RobotType, ScenarioPayload, TopologyNode, TopologyPayload } from '../types'

type DeviceNode = TopologyNode & { type: 'ctu_robot' | 'shuttle_robot' }

const robotTypeLabels: Record<RobotType, string> = {
  ctu: 'CTU',
  shuttle: '四向车',
}

function isDeviceNode(node: TopologyNode): node is DeviceNode {
  return node.type === 'ctu_robot' || node.type === 'shuttle_robot'
}

function robotTypeFromNode(node: DeviceNode): RobotType {
  return node.type === 'ctu_robot' ? 'ctu' : 'shuttle'
}

function defaultRobotSpeed(type: RobotType) {
  return type === 'ctu' ? 1.4 : 1.1
}

function syncScenarioWithTopology(draft: ScenarioPayload, topology?: TopologyPayload | null): ScenarioPayload {
  if (!topology) return draft

  const deviceNodes = topology.nodes.filter(isDeviceNode)
  const existingRobots = new Map(draft.robots.map((robot) => [robot.id, robot]))
  const robots: RobotConfig[] = deviceNodes.map((node) => {
    const existing = existingRobots.get(node.id)
    const type = robotTypeFromNode(node)
    return {
      id: node.id,
      type,
      startNode: node.id,
      speed: existing?.speed ?? defaultRobotSpeed(type),
      battery: existing?.battery ?? 100,
      enabled: existing?.enabled ?? true,
    }
  })

  const robotIds = new Set(robots.map((robot) => robot.id))
  const nextFailureRobotId =
    draft.faultInjection.robotFailureId && robotIds.has(draft.faultInjection.robotFailureId)
      ? draft.faultInjection.robotFailureId
      : robots[0]?.id ?? null

  return {
    ...draft,
    topologyId: topology.id,
    robots,
    faultInjection: {
      ...draft.faultInjection,
      robotFailureId: nextFailureRobotId,
    },
  }
}

function sameRobotConfig(left: RobotConfig[], right: RobotConfig[]) {
  if (left.length !== right.length) return false
  return left.every((robot, index) => {
    const other = right[index]
    return (
      robot.id === other.id &&
      robot.type === other.type &&
      robot.startNode === other.startNode &&
      robot.speed === other.speed &&
      robot.battery === other.battery &&
      robot.enabled === other.enabled
    )
  })
}

export function ScenarioPage() {
  const queryClient = useQueryClient()
  const selectedTopologyId = usePlatformStore((state) => state.selectedTopologyId)
  const selectedScenarioId = usePlatformStore((state) => state.selectedScenarioId)
  const setSelectedTopologyId = usePlatformStore((state) => state.setSelectedTopologyId)
  const setSelectedScenarioId = usePlatformStore((state) => state.setSelectedScenarioId)
  const scenariosQuery = useQuery({ queryKey: ['scenarios'], queryFn: api.listScenarios })
  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies })
  const [draft, setDraft] = useState<ScenarioPayload>(emptyScenario(selectedTopologyId))

  const topologyOptions = topologiesQuery.data ?? []
  const scenarioOptions = scenariosQuery.data ?? []
  const currentTopology = useMemo(
    () => topologyOptions.find((item) => item.id === draft.topologyId)?.payload ?? null,
    [draft.topologyId, topologyOptions],
  )
  const topologyDeviceNodes = useMemo(() => currentTopology?.nodes.filter(isDeviceNode) ?? [], [currentTopology])
  const topologyNodeLookup = useMemo(() => new Map((currentTopology?.nodes ?? []).map((node) => [node.id, node])), [currentTopology])
  const canSaveScenario = Boolean(draft.topologyId) && draft.robots.length > 0

  useEffect(() => {
    if (!topologyOptions.length) return

    const selectedScenario = scenarioOptions.find((item) => item.id === selectedScenarioId)
    const resolvedTopologyId = selectedTopologyId || selectedScenario?.payload.topologyId || topologyOptions[0]?.id || ''
    const matchedScenario =
      selectedScenario && selectedScenario.payload.topologyId === resolvedTopologyId
        ? selectedScenario
        : scenarioOptions.find((item) => item.payload.topologyId === resolvedTopologyId)
    const topology = topologyOptions.find((item) => item.id === resolvedTopologyId)?.payload ?? null

    if (resolvedTopologyId && resolvedTopologyId !== selectedTopologyId) {
      setSelectedTopologyId(resolvedTopologyId)
    }

    if (matchedScenario) {
      if (matchedScenario.id !== selectedScenarioId) {
        setSelectedScenarioId(matchedScenario.id)
      }
      setDraft(syncScenarioWithTopology(matchedScenario.payload, topology))
      return
    }

    if (selectedScenarioId) {
      setSelectedScenarioId('')
    }
    setDraft(syncScenarioWithTopology(emptyScenario(resolvedTopologyId), topology))
  }, [
    scenarioOptions,
    selectedScenarioId,
    selectedTopologyId,
    setSelectedScenarioId,
    setSelectedTopologyId,
    topologyOptions,
  ])

  useEffect(() => {
    if (!currentTopology) return
    setDraft((prev) => {
      const next = syncScenarioWithTopology(prev, currentTopology)
      if (
        prev.topologyId === next.topologyId &&
        prev.faultInjection.robotFailureId === next.faultInjection.robotFailureId &&
        sameRobotConfig(prev.robots, next.robots)
      ) {
        return prev
      }
      return next
    })
  }, [currentTopology])

  const saveMutation = useMutation({
    mutationFn: api.saveScenario,
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ['scenarios'] })
      setSelectedScenarioId(saved.id)
    },
  })

  function updateRobot(index: number, next: Partial<RobotConfig>) {
    setDraft((prev) => ({
      ...prev,
      robots: prev.robots.map((robot, robotIndex) => (robotIndex === index ? { ...robot, ...next } : robot)),
    }))
  }

  function handleTopologyChange(topologyId: string) {
    setSelectedTopologyId(topologyId)
    const matchedScenario = scenarioOptions.find((item) => item.payload.topologyId === topologyId)
    const topology = topologyOptions.find((item) => item.id === topologyId)?.payload ?? null

    if (matchedScenario) {
      setSelectedScenarioId(matchedScenario.id)
      setDraft(syncScenarioWithTopology(matchedScenario.payload, topology))
      return
    }

    setSelectedScenarioId('')
    setDraft(syncScenarioWithTopology(emptyScenario(topologyId), topology))
  }

  return (
    <div className="page-grid scenario-grid">
      <Surface
        title="场景配置台"
        subtitle="场景页不再手工建模设备；CTU 和四向车直接来自拓扑，只在这里配置运行参数与仿真策略。"
        action={
          <button className="accent-button" onClick={() => saveMutation.mutate(draft)} disabled={!canSaveScenario} type="button">
            保存场景
          </button>
        }
      >
        {!draft.robots.length ? (
          <div className="alert-card alert-warning scenario-warning-banner">
            <div>
              <strong>当前拓扑还没有设备节点</strong>
              <p>请先回到拓扑编辑器放置 CTU 或四向车，再回来配置场景并启动仿真。</p>
            </div>
          </div>
        ) : null}

        <div className="field-grid">
          <label>
            <span>场景名称</span>
            <input value={draft.name} onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))} />
          </label>
          <label>
            <span>关联拓扑</span>
            <select value={draft.topologyId} onChange={(event) => handleTopologyChange(event.target.value)}>
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
              onChange={(event) =>
                setDraft((prev) => ({
                  ...prev,
                  dispatchPolicy: event.target.value as ScenarioPayload['dispatchPolicy'],
                }))
              }
            >
              <option value="greedy">greedy</option>
              <option value="hungarian">hungarian</option>
            </select>
          </label>
          <label>
            <span>路径策略</span>
            <select
              value={draft.pathPolicy}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...prev,
                  pathPolicy: event.target.value as ScenarioPayload['pathPolicy'],
                }))
              }
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
                  orderGenerator: {
                    ...prev.orderGenerator,
                    ratePerMinute: Number(event.target.value),
                  },
                }))
              }
            />
          </label>
          <label>
            <span>运行 Tick 数</span>
            <input type="number" value={draft.durationTicks} onChange={(event) => setDraft((prev) => ({ ...prev, durationTicks: Number(event.target.value) }))} />
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
                  conveyorPolicy: {
                    ...prev.conveyorPolicy,
                    releaseThreshold: Number(event.target.value),
                  },
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
                  orderGenerator: {
                    ...prev.orderGenerator,
                    urgentInsertionTick: Number(event.target.value),
                  },
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
                  faultInjection: {
                    ...prev.faultInjection,
                    robotFailureTick: Number(event.target.value),
                  },
                }))
              }
            />
          </label>
          <label>
            <span>故障设备</span>
            <select
              value={draft.faultInjection.robotFailureId ?? ''}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...prev,
                  faultInjection: {
                    ...prev.faultInjection,
                    robotFailureId: event.target.value || null,
                  },
                }))
              }
            >
              <option value="">自动选择首台设备</option>
              {draft.robots.map((robot) => (
                <option key={robot.id} value={robot.id}>
                  {robot.id}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>输送线拥堵 Tick</span>
            <input
              type="number"
              value={draft.faultInjection.conveyorJammedTick ?? 0}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...prev,
                  faultInjection: {
                    ...prev.faultInjection,
                    conveyorJammedTick: Number(event.target.value),
                  },
                }))
              }
            />
          </label>
          <label>
            <span>拥堵输送线格</span>
            <input
              list="conveyor-cell-options"
              value={draft.faultInjection.conveyorCellId ?? ''}
              onChange={(event) =>
                setDraft((prev) => ({
                  ...prev,
                  faultInjection: {
                    ...prev.faultInjection,
                    conveyorCellId: event.target.value || null,
                  },
                }))
              }
              placeholder="例如 cell-r5-c9"
            />
            <datalist id="conveyor-cell-options">
              {currentTopology?.cells.filter((cell) => cell.type === 'conveyor').map((cell) => <option key={cell.id} value={cell.id} />)}
            </datalist>
          </label>
        </div>
      </Surface>

      <Surface
        title="设备列表"
        subtitle="下面的设备全部来自拓扑中的 CTU / 四向车 节点。删除或新增设备，请回到拓扑编辑器操作。"
      >
        <div className="robot-grid">
          {draft.robots.map((robot, index) => {
            const node = topologyNodeLookup.get(robot.id)
            return (
              <div key={robot.id} className="robot-card">
                <label>
                  <span>设备 ID</span>
                  <input value={robot.id} readOnly />
                </label>
                <label>
                  <span>设备类型</span>
                  <input value={robotTypeLabels[robot.type]} readOnly />
                </label>
                <label>
                  <span>起始节点</span>
                  <input value={robot.startNode} readOnly />
                </label>
                <label>
                  <span>速度</span>
                  <input type="number" value={robot.speed} onChange={(event) => updateRobot(index, { speed: Number(event.target.value) })} />
                </label>
                <label>
                  <span>电量</span>
                  <input type="number" value={robot.battery} onChange={(event) => updateRobot(index, { battery: Number(event.target.value) })} />
                </label>
                <label>
                  <span>启用</span>
                  <select value={String(robot.enabled)} onChange={(event) => updateRobot(index, { enabled: event.target.value === 'true' })}>
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                </label>
                {node ? (
                  <div className="topology-property-note">
                    <strong>拓扑来源</strong>
                    <p>
                      标签 {node.label} / 坐标 ({node.row}, {node.col})
                      {node.floor ? ` / ${node.floor}` : ''}
                    </p>
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>

        {!topologyDeviceNodes.length ? <p className="muted-copy">当前拓扑没有可派生的设备节点。</p> : null}
      </Surface>
    </div>
  )
}
