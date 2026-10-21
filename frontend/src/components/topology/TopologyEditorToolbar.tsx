import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Save, Trash2, FilePlus2, PlayCircle } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../lib/api'
import { usePlatformStore } from '../../store/usePlatformStore'
import { useTopologyEditorStore } from '../../store/useTopologyEditorStore'
import type { StoredRecord, TopologyPayload } from '../../types'

function recordLabel(record: StoredRecord<TopologyPayload>): string {
  return `${record.name}（${record.id}）`
}

export function TopologyEditorToolbar() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const enterSimulationRef = useRef(false)
  const [savingForSimulation, setSavingForSimulation] = useState(false)
  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies })
  const records = topologiesQuery.data ?? []

  const draft = useTopologyEditorStore((state) => state.draft)
  const dirty = useTopologyEditorStore((state) => state.dirty)
  const loadedId = useTopologyEditorStore((state) => state.loadedId)
  const notice = useTopologyEditorStore((state) => state.notice)
  const renameDraft = useTopologyEditorStore((state) => state.renameDraft)
  const beginNew = useTopologyEditorStore((state) => state.beginNew)
  const setLoaded = useTopologyEditorStore((state) => state.setLoaded)
  const setSaved = useTopologyEditorStore((state) => state.setSaved)
  const setLoading = useTopologyEditorStore((state) => state.setLoading)
  const setNotice = useTopologyEditorStore((state) => state.setNotice)
  const setSelectedTopologyId = usePlatformStore((state) => state.setSelectedTopologyId)
  const resetRun = usePlatformStore((state) => state.resetRun)

  function openSimulation(topologyId: string) {
    setSelectedTopologyId(topologyId)
    resetRun()
    navigate(`/simulation?topologyId=${encodeURIComponent(topologyId)}`)
  }

  const saveMutation = useMutation({
    mutationFn: (payload: TopologyPayload) => api.saveTopology(payload),
    onSuccess: async (saved) => {
      setSaved(saved.payload, saved.id)
      setSelectedTopologyId(saved.id)
      await queryClient.invalidateQueries({ queryKey: ['topologies'] })
      // 库内商品由后端按拓扑派生：保存后要重新拉取，否则增删货架后货物仍按旧库存显示。
      await queryClient.invalidateQueries({ queryKey: ['goods', 'inventory'] })
      if (enterSimulationRef.current) {
        enterSimulationRef.current = false
        setSavingForSimulation(false)
        openSimulation(saved.id)
      }
    },
    onError: (error) => {
      enterSimulationRef.current = false
      setSavingForSimulation(false)
      setNotice({ kind: 'error', text: `保存失败：${String(error)}` })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (topologyId: string) => api.deleteTopology(topologyId),
    onSuccess: async (deleted) => {
      await queryClient.invalidateQueries({ queryKey: ['topologies'] })
      beginNew()
      setNotice({ kind: 'info', text: `已删除拓扑 ${deleted.id}。` })
    },
    onError: (error) => setNotice({ kind: 'error', text: `删除失败：${error instanceof Error ? error.message : String(error)}` }),
  })

  async function handleLoad(topologyId: string) {
    if (!topologyId) return
    setLoading(true)
    setNotice(null)
    try {
      const record = await api.getTopology(topologyId)
      setLoaded(record.payload, record.id)
      setSelectedTopologyId(record.id)
    } catch (error) {
      setNotice({ kind: 'error', text: `载入失败：${String(error)}` })
    } finally {
      setLoading(false)
    }
  }

  const busy = saveMutation.isPending || deleteMutation.isPending || useTopologyEditorStore.getState().loading
  const currentRecord = records.find((record) => record.id === loadedId)

  function enterSimulation() {
    if (dirty || !loadedId) {
      enterSimulationRef.current = true
      setSavingForSimulation(true)
      saveMutation.mutate(draft)
      return
    }
    openSimulation(loadedId)
  }

  return (
    <div className="topo-toolbar">
      <div className="topo-toolbar-heading">
        <strong>拓扑编辑器</strong>
        <span>维护道路、输送带、区域与设备，保存后直接进入仿真。</span>
      </div>
      <div className="topo-toolbar-group topo-toolbar-meta">
        <label className="topo-name-field">
          <span>拓扑名称</span>
          <input
            value={draft.name}
            onChange={(event) => renameDraft(event.target.value)}
            placeholder="未命名拓扑"
            aria-label="拓扑名称"
          />
        </label>
        <label className="topo-record-field">
          <span>已有拓扑</span>
          <select
            value={currentRecord?.id ?? ''}
            onChange={(event) => void handleLoad(event.target.value)}
            aria-label="载入拓扑"
          >
            <option value="">载入一个已有拓扑…</option>
            {records.map((record) => (
              <option key={record.id} value={record.id}>
                {recordLabel(record)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="topo-toolbar-group topo-toolbar-actions">
        <button
          className="ghost-button"
          type="button"
          disabled={busy}
          onClick={beginNew}
          title="丢弃当前草稿并新建空白拓扑"
        >
          <FilePlus2 size={15} />
          新建
        </button>
        <button
          className="accent-button"
          type="button"
          disabled={busy}
          onClick={() => {
            enterSimulationRef.current = false
            setSavingForSimulation(false)
            saveMutation.mutate(draft)
          }}
          title={dirty ? '草稿有未保存改动' : '内容已是最新'}
        >
          {saveMutation.isPending ? <Loader2 className="spin" size={15} /> : <Save size={15} />}
          保存拓扑
        </button>
        <button
          className="simulation-entry-button"
          type="button"
          disabled={busy}
          onClick={enterSimulation}
          title={dirty || !loadedId ? '先保存当前拓扑，再进入仿真工作台' : '使用当前拓扑进入仿真工作台'}
        >
          {savingForSimulation ? <Loader2 className="spin" size={16} /> : <PlayCircle size={16} />}
          {dirty || !loadedId ? '保存并仿真' : '进入仿真'}
        </button>
        <button
          className="ghost-button topo-danger-button"
          type="button"
          disabled={busy || !loadedId}
          onClick={() => {
            if (!loadedId) return
            if (!window.confirm(`确定删除拓扑「${draft.name || loadedId}」吗？删除后不可恢复。`)) return
            deleteMutation.mutate(loadedId)
          }}
          title={!loadedId ? '当前未载入任何拓扑' : '删除当前拓扑（被场景引用时会拒绝）'}
        >
          <Trash2 size={15} />
          删除
        </button>
      </div>

      <div className="topo-toolbar-status">
        {dirty ? <span className="topo-badge topo-badge-dirty">未保存修改</span> : null}
        {!loadedId ? <span className="topo-badge topo-badge-new">新建中</span> : null}
        {notice ? (
          <span className={`topo-notice topo-notice-${notice.kind}`} role="status">
            {notice.text}
          </span>
        ) : null}
      </div>
    </div>
  )
}
