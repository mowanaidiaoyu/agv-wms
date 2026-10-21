import { useEffect, useMemo, useRef, useState, lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Plus, Trash2 } from 'lucide-react'
import { Surface } from '../components/Surface'
import { ObjectPalette } from '../components/topology/ObjectPalette'
import { TopologyEditorToolbar } from '../components/topology/TopologyEditorToolbar'
import { TopologyPropertyPanel } from '../components/topology/TopologyPropertyPanel'
import { api } from '../lib/api'
import { BATCH_PLACE_TYPES, floorsOf, OBJECT_LABELS } from '../lib/topology'
import { usePlatformStore } from '../store/usePlatformStore'
import { useTopologyEditorStore } from '../store/useTopologyEditorStore'
import type { EditorObjectType } from '../types'

const Topology3DScene = lazy(() => import('../components/topology/Topology3DScene'))

function EditorContextBar() {
  const draft = useTopologyEditorStore((state) => state.draft)
  const activeFloor = useTopologyEditorStore((state) => state.activeFloor)
  const setActiveFloor = useTopologyEditorStore((state) => state.setActiveFloor)
  const activeShuttleLevel = useTopologyEditorStore((state) => state.activeShuttleLevel)
  const setActiveShuttleLevel = useTopologyEditorStore((state) => state.setActiveShuttleLevel)
  const activeTool = useTopologyEditorStore((state) => state.activeTool)
  const batchAction = useTopologyEditorStore((state) => state.batchAction)
  const batchPlaceType = useTopologyEditorStore((state) => state.batchPlaceType)
  const setBatchAction = useTopologyEditorStore((state) => state.setBatchAction)
  const setBatchPlaceType = useTopologyEditorStore((state) => state.setBatchPlaceType)
  const applyGridSize = useTopologyEditorStore((state) => state.applyGridSize)
  const addFloor = useTopologyEditorStore((state) => state.addFloor)
  const removeCurrentFloor = useTopologyEditorStore((state) => state.removeCurrentFloor)

  const floors = floorsOf(draft)
  const hasShuttleZone = draft.zones.some((zone) => zone.type === 'shuttle')
  const [rows, setRows] = useState(draft.grid.rows)
  const [cols, setCols] = useState(draft.grid.cols)

  useEffect(() => {
    setRows(draft.grid.rows)
    setCols(draft.grid.cols)
  }, [draft.grid.rows, draft.grid.cols])

  const canShrinkRows = rows <= draft.grid.rows
  const canShrinkCols = cols <= draft.grid.cols

  return (
    <div className="topo-context-bar">
      <div className="topo-context-group">
        <span className="topo-context-label">楼层</span>
        <div className="topology-floor-switcher">
          {floors.map((floor) => (
            <button
              key={floor}
              type="button"
              className={`topology-floor-chip ${floor === activeFloor ? 'topology-floor-chip-active' : ''}`}
              onClick={() => setActiveFloor(floor)}
            >
              {floor}
            </button>
          ))}
          <button type="button" className="ghost-button topo-mini-button" title="新增楼层" onClick={addFloor}>
            <Plus size={13} />
          </button>
          {activeFloor !== '1F' ? (
            <button type="button" className="ghost-button topo-mini-button" title="删除当前楼层" onClick={removeCurrentFloor}>
              <Trash2 size={13} />
            </button>
          ) : null}
        </div>
      </div>

      {activeTool === 'batch' ? (
        <div className="topo-context-group">
          <span className="topo-context-label">批量</span>
          <div className="topology-floor-switcher">
            <button
              type="button"
              className={`topology-floor-chip ${batchAction === 'select' ? 'topology-floor-chip-active' : ''}`}
              onClick={() => setBatchAction('select')}
            >
              选择
            </button>
            <button
              type="button"
              className={`topology-floor-chip ${batchAction === 'place' ? 'topology-floor-chip-active' : ''}`}
              onClick={() => setBatchAction('place')}
            >
              放置
            </button>
          </div>
          {batchAction === 'place' ? (
            <select
              className="topo-batch-place-select"
              value={batchPlaceType}
              onChange={(event) => setBatchPlaceType(event.target.value as EditorObjectType)}
            >
              {BATCH_PLACE_TYPES.map((type) => (
                <option key={type} value={type}>
                  {OBJECT_LABELS[type]}
                </option>
              ))}
            </select>
          ) : null}
        </div>
      ) : null}

      {hasShuttleZone ? (
        <div className="topo-context-group">
          <span className="topo-context-label">货架层</span>
          <div className="topology-floor-switcher">
            {[1, 2, 3, 4].map((level) => (
              <button
                key={level}
                type="button"
                className={`topology-floor-chip ${level === activeShuttleLevel ? 'topology-floor-chip-active' : ''}`}
                onClick={() => setActiveShuttleLevel(level)}
              >
                第{level}层
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="topo-context-group">
        <span className="topo-context-label">画布</span>
        <label className="topo-context-number" title={`行数（当前 ${draft.grid.rows}）${canShrinkRows ? '' : '缩小会丢弃越界内容'}`}>
          <span>行</span>
          <input type="number" min={1} value={rows} onChange={(event) => setRows(Math.max(1, Math.floor(Number(event.target.value) || 1)))} onBlur={() => applyGridSize(rows, draft.grid.cols)} />
        </label>
        <label className="topo-context-number" title={`列数（当前 ${draft.grid.cols}）${canShrinkCols ? '' : '缩小会丢弃越界内容'}`}>
          <span>列</span>
          <input type="number" min={1} value={cols} onChange={(event) => setCols(Math.max(1, Math.floor(Number(event.target.value) || 1)))} onBlur={() => applyGridSize(draft.grid.rows, cols)} />
        </label>
      </div>

      <div className="topo-context-hints">
        <span>左键：编辑 / 点选</span>
        <span>右键拖动：平移视图</span>
        <span>滚轮：缩放（指向光标） · 中键拖动：旋转视角</span>
      </div>
    </div>
  )
}

export function TopologyPage() {
  const draft = useTopologyEditorStore((state) => state.draft)
  const selection = useTopologyEditorStore((state) => state.selection)
  const activeFloor = useTopologyEditorStore((state) => state.activeFloor)
  const activeTool = useTopologyEditorStore((state) => state.activeTool)
  const activeZoneType = useTopologyEditorStore((state) => state.activeZoneType)
  const marquee = useTopologyEditorStore((state) => state.marquee)
  const loading = useTopologyEditorStore((state) => state.loading)
  const pointerDownAt = useTopologyEditorStore((state) => state.pointerDownAt)
  const pointerEnterAt = useTopologyEditorStore((state) => state.pointerEnterAt)
  const entityPointerDown = useTopologyEditorStore((state) => state.entityPointerDown)
  const moveCell = useTopologyEditorStore((state) => state.moveCell)
  const setLoaded = useTopologyEditorStore((state) => state.setLoaded)
  const setSelectedTopologyId = usePlatformStore((state) => state.setSelectedTopologyId)
  const selectedTopologyId = usePlatformStore((state) => state.selectedTopologyId)

  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies })
  const records = useMemo(() => topologiesQuery.data ?? [], [topologiesQuery.data])
  const initializedRef = useRef(false)

  // 库内商品（按拓扑派生的合成库存）：CTU 库货架逐槽显示「商品 + 属性」彩色货箱。
  const goodsQuery = useQuery({
    queryKey: ['goods', 'inventory', draft.id],
    queryFn: () => api.getGoodsInventory(draft.id),
    enabled: Boolean(draft.id),
  })
  const goods = useMemo(() => goodsQuery.data?.slots ?? [], [goodsQuery.data])

  useEffect(() => {
    if (topologiesQuery.isLoading || initializedRef.current) return
    initializedRef.current = true
    const { loadedId, dirty } = useTopologyEditorStore.getState()
    if (loadedId || dirty) return
    const target =
      records.find((record) => record.id === selectedTopologyId) ?? records[0]
    if (target) {
      setLoaded(target.payload, target.id)
      setSelectedTopologyId(target.id)
    }
  }, [records, selectedTopologyId, setLoaded, setSelectedTopologyId, topologiesQuery.isLoading])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.code === 'Escape') {
        useTopologyEditorStore.getState().cancelGesture()
        useTopologyEditorStore.getState().clearSelection()
      }
    }
    function handlePointerUp() {
      useTopologyEditorStore.getState().pointerUpAt()
    }
    function handlePointerCancel() {
      useTopologyEditorStore.getState().cancelGesture()
    }
    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('pointerup', handlePointerUp)
    window.addEventListener('pointercancel', handlePointerCancel)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('pointerup', handlePointerUp)
      window.removeEventListener('pointercancel', handlePointerCancel)
    }
  }, [])

  return (
    <div className="page-grid topology-editor-page">
      <Surface
        title="拓扑编辑器"
        subtitle="v1 模型 · 3D 编辑器 · 与场景/仿真闭环。在 3D 中直接增删改查道路、输送带、区域与节点，保存后即可在场景页派生设备。"
      >
        <TopologyEditorToolbar />
        <div className="topo-workspace">
          <ObjectPalette />
          <div className="topo-center-column">
            <EditorContextBar />
            <div className="topo-canvas-panel topology-editor-canvas">
              {loading ? (
                <div className="topo-canvas-loading">
                  <Loader2 className="spin" size={26} />
                  <p className="muted-copy">正在载入拓扑…</p>
                </div>
              ) : (
                <Suspense
                  fallback={
                    <div className="topo-canvas-loading">
                      <Loader2 className="spin" size={26} />
                      <p className="muted-copy">正在装配 3D 场景…</p>
                    </div>
                  }
                >
                  <Topology3DScene
                    topology={draft}
                    goods={goods}
                    activeFloor={activeFloor}
                    activeTool={activeTool}
                    activeZoneType={activeZoneType}
                    marquee={marquee}
                    selection={selection}
                    onCellDown={pointerDownAt}
                    onCellEnter={pointerEnterAt}
                    onEntityDown={entityPointerDown}
                    onMoveCell={moveCell}
                  />
                </Suspense>
              )}
            </div>
          </div>

          <aside className="topo-inspector">
            <div className="topo-inspector-body">
              <TopologyPropertyPanel />
            </div>
          </aside>
        </div>
      </Surface>
    </div>
  )
}
