import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { addEdge, Background, Controls, MiniMap, ReactFlow, useEdgesState, useNodesState } from '@xyflow/react'
import type { Connection, Edge, Node } from '@xyflow/react'
import { Plus, Save, ShieldCheck } from 'lucide-react'
import { emptyTopology } from '../data/defaults'
import { api } from '../lib/api'
import { Surface } from '../components/Surface'
import { usePlatformStore } from '../store/usePlatformStore'
import type { Resource, TopologyEdge, TopologyNode, TopologyPayload, TopologyValidationResult, Zone } from '../types'

function toFlowNodes(nodes: TopologyNode[]): Node[] {
  return nodes.map((node) => ({
    id: node.id,
    position: { x: node.x, y: node.y },
    data: { label: node.label, type: node.type, zoneId: node.zoneId, capacity: node.capacity },
    type: 'default',
  }))
}

function toFlowEdges(edges: TopologyEdge[]): Edge[] {
  return edges.map((edge) => ({
    id: edge.id,
    source: edge.from,
    target: edge.to,
    label: edge.type,
    data: edge as unknown as Record<string, unknown>,
    animated: edge.type === 'conveyor',
  }))
}

export function TopologyPage() {
  const queryClient = useQueryClient()
  const selectedTopologyId = usePlatformStore((state) => state.selectedTopologyId)
  const setSelectedTopologyId = usePlatformStore((state) => state.setSelectedTopologyId)
  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies })
  const [draft, setDraft] = useState<TopologyPayload>(emptyTopology())
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selectedNodeId, setSelectedNodeId] = useState('')
  const [selectedEdgeId, setSelectedEdgeId] = useState('')
  const [selectedResourceId, setSelectedResourceId] = useState('')
  const [validation, setValidation] = useState<TopologyValidationResult | null>(null)

  useEffect(() => {
    const current = topologiesQuery.data?.find((item) => item.id === selectedTopologyId) ?? topologiesQuery.data?.[0]
    if (current) {
      setSelectedTopologyId(current.id)
      setDraft(current.payload)
      setNodes(toFlowNodes(current.payload.nodes))
      setEdges(toFlowEdges(current.payload.edges))
      setSelectedNodeId(current.payload.nodes[0]?.id ?? '')
      setSelectedEdgeId(current.payload.edges[0]?.id ?? '')
      setSelectedResourceId(current.payload.resources[0]?.id ?? '')
      setValidation(null)
    }
  }, [selectedTopologyId, setEdges, setNodes, setSelectedTopologyId, topologiesQuery.data])

  const saveMutation = useMutation({
    mutationFn: async (payload: TopologyPayload) => {
      const existing = topologiesQuery.data?.some((item) => item.id === payload.id)
      return existing ? api.updateTopology(payload) : api.saveTopology(payload)
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ['topologies'] })
      setSelectedTopologyId(saved.id)
    },
  })

  const validateMutation = useMutation({
    mutationFn: api.validateTopology,
    onSuccess: (result) => setValidation(result),
  })

  const selectedNode = draft.nodes.find((node) => node.id === selectedNodeId)
  const selectedEdge = draft.edges.find((edge) => edge.id === selectedEdgeId)
  const selectedResource = draft.resources.find((resource) => resource.id === selectedResourceId)
  const zoneOptions = draft.zones

  const resourceTypeOptions = useMemo(
    () => [
      { value: 'conveyor_segment', label: '输送线' },
      { value: 'buffer_slot', label: '缓存位' },
      { value: 'station', label: '站台' },
      { value: 'lift', label: '提升机' },
    ],
    [],
  )

  function syncDraft(nextNodes = nodes, nextEdges = edges, nextZones = draft.zones, nextResources = draft.resources) {
    const syncedNodes = nextNodes.map((node) => ({
      id: node.id,
      type: (node.data?.type as TopologyNode['type']) ?? 'storage',
      x: Number(node.position.x.toFixed(2)),
      y: Number(node.position.y.toFixed(2)),
      zoneId: (node.data?.zoneId as string | undefined) ?? null,
      capacity: Number(node.data?.capacity ?? 1),
      label: String(node.data?.label ?? node.id),
    }))
    const syncedEdges = nextEdges.map((edge) => {
      const edgeData = (edge.data ?? {}) as Partial<TopologyEdge>
      return {
        id: edge.id,
        from: edge.source,
        to: edge.target,
        type: (edgeData.type ?? 'aisle') as TopologyEdge['type'],
        length: Number(edgeData.length ?? 1),
        bidirectional: Boolean(edgeData.bidirectional ?? true),
        capacity: Number(edgeData.capacity ?? 1),
        speedLimit: Number(edgeData.speedLimit ?? 1),
      }
    })
    setDraft((prev) => ({
      ...prev,
      zones: nextZones,
      nodes: syncedNodes,
      edges: syncedEdges,
      resources: nextResources,
    }))
  }

  function handleConnect(connection: Connection) {
    const next = addEdge(
      {
        ...connection,
        id: `edge-${crypto.randomUUID().slice(0, 8)}`,
        label: 'aisle',
        data: {
          type: 'aisle',
          length: 1,
          bidirectional: true,
          capacity: 1,
          speedLimit: 1,
        },
      },
      edges,
    )
    setEdges(next)
    syncDraft(nodes, next)
  }

  function addNode() {
    const newNode: Node = {
      id: `node-${crypto.randomUUID().slice(0, 6)}`,
      position: { x: 220, y: 220 },
      data: { label: '新节点', type: 'storage', zoneId: draft.zones[0]?.id ?? null, capacity: 1 },
      type: 'default',
    }
    const nextNodes = [...nodes, newNode]
    setNodes(nextNodes)
    syncDraft(nextNodes)
    setSelectedNodeId(newNode.id)
  }

  function addZone() {
    const zone: Zone = {
      id: `zone-${crypto.randomUUID().slice(0, 6)}`,
      label: '新建库区',
      type: 'mixed',
      x: 120,
      y: 120,
      width: 220,
      height: 160,
    }
    const nextZones = [...draft.zones, zone]
    syncDraft(nodes, edges, nextZones)
  }

  function addResource() {
    const resource: Resource = {
      id: `res-${crypto.randomUUID().slice(0, 6)}`,
      label: '新资源',
      type: 'buffer_slot',
      nodeIds: [],
      edgeIds: [],
      capacity: 1,
      meta: {},
    }
    const nextResources = [...draft.resources, resource]
    syncDraft(nodes, edges, draft.zones, nextResources)
    setSelectedResourceId(resource.id)
  }

  function saveTopology() {
    const payload: TopologyPayload = {
      ...draft,
      nodes: nodes.map((node) => ({
        id: node.id,
        type: (node.data?.type as TopologyNode['type']) ?? 'storage',
        x: Number(node.position.x.toFixed(2)),
        y: Number(node.position.y.toFixed(2)),
        zoneId: (node.data?.zoneId as string | undefined) ?? null,
        capacity: Number(node.data?.capacity ?? 1),
        label: String(node.data?.label ?? node.id),
      })),
      edges: edges.map((edge) => {
        const edgeData = (edge.data ?? {}) as Partial<TopologyEdge>
        return {
          id: edge.id,
          from: edge.source,
          to: edge.target,
          type: (edgeData.type ?? 'aisle') as TopologyEdge['type'],
          length: Number(edgeData.length ?? 1),
          bidirectional: Boolean(edgeData.bidirectional ?? true),
          capacity: Number(edgeData.capacity ?? 1),
          speedLimit: Number(edgeData.speedLimit ?? 1),
        }
      }),
    }
    setDraft(payload)
    saveMutation.mutate(payload)
  }

  function updateSelectedEdge(next: Partial<TopologyEdge>) {
    const nextEdges = edges.map((edge) =>
      edge.id === selectedEdgeId
        ? {
            ...edge,
            label: String(next.type ?? (edge.data as Partial<TopologyEdge> | undefined)?.type ?? 'aisle'),
            animated: (next.type ?? (edge.data as Partial<TopologyEdge> | undefined)?.type) === 'conveyor',
            data: { ...(edge.data as Record<string, unknown>), ...next },
          }
        : edge,
    )
    setEdges(nextEdges)
    syncDraft(nodes, nextEdges)
  }

  function updateSelectedResource(next: Partial<Resource>) {
    const nextResources = draft.resources.map((resource) => (resource.id === selectedResourceId ? { ...resource, ...next } : resource))
    syncDraft(nodes, edges, draft.zones, nextResources)
  }

  return (
    <div className="page-grid topology-grid">
      <Surface
        title="拓扑编辑器"
        subtitle="先用自绘拓扑搭平台，再逐步补齐站台、交接点、缓存位与输送线资源。"
        action={
          <div className="surface-actions">
            <button className="ghost-button" onClick={addZone}>
              <Plus size={16} />
              添加库区
            </button>
            <button className="ghost-button" onClick={addResource}>
              <Plus size={16} />
              添加资源
            </button>
            <button className="accent-button" onClick={addNode}>
              <Plus size={16} />
              添加节点
            </button>
            <button className="ghost-button" onClick={() => validateMutation.mutate(draft)}>
              <ShieldCheck size={16} />
              校验拓扑
            </button>
            <button className="accent-button" onClick={saveTopology}>
              <Save size={16} />
              保存拓扑
            </button>
          </div>
        }
        className="topology-canvas-card"
      >
        <div className="editor-toolbar">
          <select value={selectedTopologyId} onChange={(event) => setSelectedTopologyId(event.target.value)}>
            {topologiesQuery.data?.map((topology) => (
              <option key={topology.id} value={topology.id}>
                {topology.payload.name}
              </option>
            ))}
          </select>
          <input
            value={draft.name}
            onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))}
            placeholder="拓扑名称"
          />
        </div>
        <div className="topology-editor">
          <div className="zone-overlay">
            {draft.zones.map((zone) => (
              <div key={zone.id} className={`zone-box zone-${zone.type}`} style={{ left: zone.x, top: zone.y, width: zone.width, height: zone.height }}>
                {zone.label}
              </div>
            ))}
          </div>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={(changes) => {
              onNodesChange(changes)
              queueMicrotask(() => syncDraft())
            }}
            onEdgesChange={(changes) => {
              onEdgesChange(changes)
              queueMicrotask(() => syncDraft())
            }}
            onConnect={handleConnect}
            onNodeClick={(_, node) => {
              setSelectedNodeId(node.id)
              setSelectedEdgeId('')
            }}
            onEdgeClick={(_, edge) => {
              setSelectedEdgeId(edge.id)
              setSelectedNodeId('')
            }}
            fitView
          >
            <Background />
            <Controls />
            <MiniMap />
          </ReactFlow>
        </div>
      </Surface>

      <Surface title="拓扑校验" subtitle="优先检查站台、交接点、输送线资源与缓存位建模是否完整。">
        {validation ? (
          <div className="validation-panel">
            <div className={validation.valid ? 'validation-chip valid' : 'validation-chip invalid'}>
              {validation.valid ? '可用于建场景' : '存在必须修复的问题'}
            </div>
            <div className="field-stack">
              <div>
                <strong>错误</strong>
                <div className="stack-list">
                  {validation.errors.length ? validation.errors.map((item) => <div key={item} className="list-item"><span>{item}</span></div>) : <p className="muted-copy">无错误。</p>}
                </div>
              </div>
              <div>
                <strong>警告</strong>
                <div className="stack-list">
                  {validation.warnings.length ? validation.warnings.map((item) => <div key={item} className="list-item"><span>{item}</span></div>) : <p className="muted-copy">无警告。</p>}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <p className="muted-copy">点击“校验拓扑”后，这里会显示缺站台、缺交接点、资源绑定缺失等问题。</p>
        )}
      </Surface>

      <Surface title="节点属性" subtitle="编辑节点类型、库区和容量。">
        {selectedNode ? (
          <div className="field-stack">
            <label>
              <span>节点标签</span>
              <input
                value={selectedNode.label}
                onChange={(event) => {
                  const nextNodes = nodes.map((node) =>
                    node.id === selectedNode.id ? { ...node, data: { ...node.data, label: event.target.value } } : node,
                  )
                  setNodes(nextNodes)
                  syncDraft(nextNodes)
                }}
              />
            </label>
            <label>
              <span>节点类型</span>
              <select
                value={selectedNode.type}
                onChange={(event) => {
                  const nextNodes = nodes.map((node) =>
                    node.id === selectedNode.id ? { ...node, data: { ...node.data, type: event.target.value } } : node,
                  )
                  setNodes(nextNodes)
                  syncDraft(nextNodes)
                }}
              >
                <option value="intersection">intersection</option>
                <option value="storage">storage</option>
                <option value="buffer">buffer</option>
                <option value="station">station</option>
                <option value="lift">lift</option>
                <option value="entry">entry</option>
                <option value="conveyor_gate">conveyor_gate</option>
              </select>
            </label>
            <label>
              <span>所属库区</span>
              <select
                value={selectedNode.zoneId ?? ''}
                onChange={(event) => {
                  const nextNodes = nodes.map((node) =>
                    node.id === selectedNode.id ? { ...node, data: { ...node.data, zoneId: event.target.value } } : node,
                  )
                  setNodes(nextNodes)
                  syncDraft(nextNodes)
                }}
              >
                {zoneOptions.map((zone) => (
                  <option key={zone.id} value={zone.id}>
                    {zone.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>容量</span>
              <input
                type="number"
                value={selectedNode.capacity}
                onChange={(event) => {
                  const nextNodes = nodes.map((node) =>
                    node.id === selectedNode.id ? { ...node, data: { ...node.data, capacity: Number(event.target.value) } } : node,
                  )
                  setNodes(nextNodes)
                  syncDraft(nextNodes)
                }}
              />
            </label>
          </div>
        ) : (
          <p className="muted-copy">点击画布中的节点即可编辑。</p>
        )}
      </Surface>

      <Surface title="边属性" subtitle="补足通道类型、长度、方向与速度限制。">
        {selectedEdge ? (
          <div className="field-stack">
            <label>
              <span>边类型</span>
              <select value={selectedEdge.type} onChange={(event) => updateSelectedEdge({ type: event.target.value as TopologyEdge['type'] })}>
                <option value="aisle">aisle</option>
                <option value="conveyor">conveyor</option>
                <option value="cross_zone">cross_zone</option>
                <option value="lift">lift</option>
              </select>
            </label>
            <label>
              <span>长度</span>
              <input type="number" value={selectedEdge.length} onChange={(event) => updateSelectedEdge({ length: Number(event.target.value) })} />
            </label>
            <label>
              <span>容量</span>
              <input type="number" value={selectedEdge.capacity} onChange={(event) => updateSelectedEdge({ capacity: Number(event.target.value) })} />
            </label>
            <label>
              <span>速度上限</span>
              <input type="number" value={selectedEdge.speedLimit} onChange={(event) => updateSelectedEdge({ speedLimit: Number(event.target.value) })} />
            </label>
            <label>
              <span>是否双向</span>
              <select value={String(selectedEdge.bidirectional)} onChange={(event) => updateSelectedEdge({ bidirectional: event.target.value === 'true' })}>
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            </label>
          </div>
        ) : (
          <p className="muted-copy">点击连边即可编辑边属性。</p>
        )}
      </Surface>

      <Surface
        title="资源面板"
        subtitle="把输送线、缓存位、站台和提升机从“能画”升级到“能建场景”。"
      >
        <div className="stack-list">
          {draft.resources.map((resource) => (
            <button key={resource.id} className={`resource-list-item ${selectedResourceId === resource.id ? 'resource-list-item-active' : ''}`} onClick={() => setSelectedResourceId(resource.id)}>
              <div>
                <strong>{resource.label}</strong>
                <p>{resource.type}</p>
              </div>
              <span>{resource.capacity}</span>
            </button>
          ))}
        </div>
        {selectedResource ? (
          <div className="field-stack">
            <label>
              <span>资源名称</span>
              <input value={selectedResource.label} onChange={(event) => updateSelectedResource({ label: event.target.value })} />
            </label>
            <label>
              <span>资源类型</span>
              <select value={selectedResource.type} onChange={(event) => updateSelectedResource({ type: event.target.value as Resource['type'] })}>
                {resourceTypeOptions.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>容量</span>
              <input type="number" value={selectedResource.capacity} onChange={(event) => updateSelectedResource({ capacity: Number(event.target.value) })} />
            </label>
            <label>
              <span>绑定节点（逗号分隔）</span>
              <input
                value={selectedResource.nodeIds.join(', ')}
                onChange={(event) => updateSelectedResource({ nodeIds: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) })}
              />
            </label>
            <label>
              <span>绑定边（逗号分隔）</span>
              <input
                value={selectedResource.edgeIds.join(', ')}
                onChange={(event) => updateSelectedResource({ edgeIds: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) })}
              />
            </label>
          </div>
        ) : (
          <p className="muted-copy">先选择一个资源，或者点击“添加资源”。</p>
        )}
      </Surface>

      <Surface title="库区列表" subtitle="当前草图中的库区信息。">
        <div className="stack-list">
          {draft.zones.map((zone) => (
            <div key={zone.id} className="list-item">
              <div>
                <strong>{zone.label}</strong>
                <p>{zone.type}</p>
              </div>
              <span>
                {Math.round(zone.width)} x {Math.round(zone.height)}
              </span>
            </div>
          ))}
        </div>
      </Surface>
    </div>
  )
}
