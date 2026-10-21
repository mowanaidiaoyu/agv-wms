import { useEffect, useMemo, useRef, useState, lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, PanelRightOpen, Plus, SlidersHorizontal, Trash2, X } from 'lucide-react'
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

function EditorContextBar({
  showGoods,
  goodsLoading,
  onToggleGoods,
}: {
  showGoods: boolean
  goodsLoading: boolean
  onToggleGoods: () => void
}) {
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
        <button
          type="button"
          className={`topology-floor-chip ${showGoods ? 'topology-floor-chip-active' : ''}`}
          aria-pressed={showGoods}
          title={showGoods ? '隐藏商品箱以提升编辑性能' : '按需加载并显示商品箱'}
          onClick={onToggleGoods}
        >
          {goodsLoading ? '商品加载中…' : showGoods ? '商品：显示' : '商品：隐藏'}
        </button>
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
  const [showGoods, setShowGoods] = useState(false)
  const [toolsCollapsed, setToolsCollapsed] = useState(true)
  const [inspectorOpen, setInspectorOpen] = useState(false)

  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies })
  const records = useMemo(() => topologiesQuery.data ?? [], [topologiesQuery.data])
  const initializedRef = useRef(false)

  // 库内商品（按拓扑派生的合成库存）：CTU 库货架逐槽显示「商品 + 属性」彩色货箱。
  const goodsQuery = useQuery({
    queryKey: ['goods', 'inventory', draft.id],
    queryFn: () => api.getGoodsInventory(draft.id),
    enabled: showGoods && Boolean(draft.id),
  })
  const goods = useMemo(() => (showGoods ? goodsQuery.data?.slots ?? [] : []), [goodsQuery.data, showGoods])

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

  useEffect(() => {
    if (selection) setInspectorOpen(true)
  }, [selection])

  return (
    <div className="page-grid topology-editor-page">
      <Surface>
        <TopologyEditorToolbar />
        <div className="topo-workspace">
          <ObjectPalette collapsed={toolsCollapsed} onToggle={() => setToolsCollapsed((collapsed) => !collapsed)} />
          <div className="topo-center-column">
            <EditorContextBar
              showGoods={showGoods}
              goodsLoading={showGoods && goodsQuery.isLoading}
              onToggleGoods={() => setShowGoods((visible) => !visible)}
            />
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
              <button
                type="button"
                className={`topo-inspector-trigger ${selection ? 'topo-inspector-trigger-active' : ''}`}
                onClick={() => setInspectorOpen((open) => !open)}
                aria-expanded={inspectorOpen}
                title={inspectorOpen ? '收起属性面板' : '打开属性面板'}
              >
                {inspectorOpen ? <SlidersHorizontal size={17} /> : <PanelRightOpen size={17} />}
                <span>{selection ? '编辑属性' : '属性'}</span>
              </button>
              <aside className={`topo-inspector topo-inspector-drawer ${inspectorOpen ? 'topo-inspector-drawer-open' : ''}`}>
                <div className="topo-inspector-header">
                  <div>
                    <strong>对象属性</strong>
                    <span>{selection ? '编辑当前选中对象' : '选择对象后显示可编辑内容'}</span>
                  </div>
                  <button type="button" onClick={() => setInspectorOpen(false)} aria-label="关闭属性面板" title="关闭属性面板">
                    <X size={17} />
                  </button>
                </div>
                <div className="topo-inspector-body">
                  <TopologyPropertyPanel />
                </div>
              </aside>
            </div>
          </div>
        </div>
      </Surface>
    </div>
  )
}
