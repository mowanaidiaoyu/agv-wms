import { create } from 'zustand'
import { emptyTopology } from '../data/topologyDefaults'
import {
  CELL_BRUSH_TOOLS,
  CELL_LABELS,
  cellsInBox,
  conveyorBlockCellIds,
  createZone,
  eraseAt,
  fillNodes,
  findCell,
  findNodeAt,
  floorsOf,
  GROUND_FLOOR,
  isPointInBox,
  interpolateGridCells,
  moveNode as moveNodeInTopology,
  moveZone as moveZoneInTopology,
  NODE_LABELS,
  normalizeSelectionBox,
  paintCell,
  paintStroke,
  removeFloor,
  removeZone,
  resizeGrid,
  setShuttleSlotAisle,
  setShuttleSlotLift,
  setShuttleSlotRemoved,
  shuttleSlotsInBox,
  syncZoneMembership,
  updateZoneGeometry,
  updateConveyorBlockDeck,
  type CellBox,
  type GridPoint,
} from '../lib/topology'
import type {
  CellType,
  ConveyorDeckConfig,
  ConveyorDeckKey,
  ConveyorDirection,
  EditorObjectType,
  NodeType,
  TopologyCell,
  TopologyNode,
  TopologyPayload,
  ZoneType,
} from '../types'
import { parseShuttleSlotEntityId, type SceneEntityKind } from '../lib/topology3d'

export type TopologySelection =
  | { kind: 'cell'; id: string }
  | { kind: 'node'; id: string }
  | { kind: 'zone'; id: string }
  | { kind: 'storage-batch'; ids: string[]; box: CellBox }
  | { kind: 'conveyor-batch'; ids: string[]; box: CellBox }
  | { kind: 'shuttle-batch'; slots: { row: number; col: number }[]; box: CellBox }
  | { kind: 'shuttle-slot'; zoneId: string; row: number; col: number; floor: string }
  | null

export type DragMode = 'none' | 'select' | 'zone' | 'paint' | 'node' | 'move'

export type BatchAction = 'select' | 'place'

export type EditorNoticeKind = 'info' | 'warn' | 'error'

export interface EditorNotice {
  kind: EditorNoticeKind
  text: string
}

const NODE_TOOL_OF: Partial<Record<EditorObjectType, NodeType>> = {
  ctu_robot: 'ctu_robot',
  shuttle_robot: 'shuttle_robot',
  lift_transfer: 'lift_transfer',
  turn_transfer: 'turn_transfer',
  shuttle_dock: 'shuttle_dock',
}

interface DragState {
  mode: DragMode
  origin: GridPoint | null
  current: GridPoint | null
  moved: boolean
  entity?: { kind: 'node' | 'zone'; id: string; floor: string } | null
}

function cloneGridPoint(point: GridPoint): GridPoint {
  return { row: point.row, col: point.col }
}

function isValidSelectionForTopology(selection: TopologySelection, topology: TopologyPayload): TopologySelection {
  if (!selection) return null
  if (selection.kind === 'cell' && !topology.cells.some((cell) => cell.id === selection.id)) return null
  if (selection.kind === 'node' && !topology.nodes.some((node) => node.id === selection.id)) return null
  if (selection.kind === 'zone' && !topology.zones.some((zone) => zone.id === selection.id)) return null
  if (selection.kind === 'storage-batch') {
    const ids = selection.ids.filter((id) => topology.nodes.some((node) => node.id === id && node.type === 'storage'))
    return ids.length ? { ...selection, ids } : null
  }
  if (selection.kind === 'conveyor-batch') {
    const ids = selection.ids.filter((id) => topology.cells.some((cell) => cell.id === id && cell.type === 'conveyor'))
    return ids.length ? { ...selection, ids } : null
  }
  return selection
}

interface TopologyEditorStore {
  draft: TopologyPayload
  savedSnapshot: TopologyPayload | null
  loadedId: string | null
  loading: boolean
  dirty: boolean
  notice: EditorNotice | null

  activeFloor: string
  activeShuttleLevel: number
  activeTool: EditorObjectType
  activeZoneType: ZoneType | null
  batchAction: BatchAction
  batchPlaceType: EditorObjectType
  batchOrigin: GridPoint | null
  panModifierActive: boolean
  selection: TopologySelection
  marquee: CellBox | null

  drag: DragState
  pointerDown: boolean
  lastPaint: GridPoint | null

  // 生命周期 / 加载
  beginNew: () => void
  setLoaded: (payload: TopologyPayload, recordId: string) => void
  setSaved: (payload: TopologyPayload, recordId: string) => void
  setLoading: (loading: boolean) => void
  setNotice: (notice: EditorNotice | null) => void

  // 视图状态
  setActiveFloor: (floor: string) => void
  setActiveShuttleLevel: (level: number) => void
  setActiveTool: (tool: EditorObjectType) => void
  setActiveZoneType: (zoneType: ZoneType | null) => void
  setBatchAction: (action: BatchAction) => void
  setBatchPlaceType: (type: EditorObjectType) => void
  setPanModifierActive: (active: boolean) => void

  // 元数据与网格
  renameDraft: (name: string) => void
  applyGridSize: (rows: number, cols: number) => void
  applyCellSize: (cellSize: number) => void

  // 选择
  selectEntityAt: (row: number, col: number) => void
  selectCellById: (cellId: string) => void
  selectNode: (nodeId: string) => void
  selectZone: (zoneId: string) => void
  clearSelection: () => void
  selectStorageBatch: (ids: string[], box: CellBox) => void
  selectConveyorBatch: (ids: string[], box: CellBox) => void
  selectShuttleBatch: (slots: { row: number; col: number }[], box: CellBox) => void

  // 画布指针
  pointerDownAt: (row: number, col: number) => void
  pointerEnterAt: (row: number, col: number) => void
  pointerUpAt: () => void
  cancelGesture: () => void
  entityPointerDown: (kind: SceneEntityKind, id: string, floor: string) => void
  moveCell: (row: number, col: number) => void
  moveNode: (nodeId: string, row: number, col: number, floor: string) => void

  // 编辑动作（供属性面板调用）
  updateCell: (id: string, patch: Partial<TopologyCell>) => void
  updateNode: (id: string, patch: Partial<TopologyNode>) => void
  patchZone: (id: string, patch: Partial<TopologyPayload['zones'][number]>) => void
  deleteZone: (id: string) => void
  resyncZone: (id: string) => void
  batchAssignZone: (nodeIds: string[], zoneId: string | null) => void
  updateConveyorLevels: (ids: string[], levels: number) => void
  updateConveyorGround: (ids: string[], ground: boolean) => void
  updateConveyorBlockDeck: (cellId: string, deck: ConveyorDeckKey, patch: Partial<ConveyorDeckConfig>) => void
  applyShuttleAisle: (slots: { row: number; col: number }[], aisle: boolean, floor?: string) => void
  applyShuttleSlotRemove: (slots: { row: number; col: number }[], removed: boolean, floor?: string) => void
  applyShuttleLift: (slots: { row: number; col: number }[], lift: boolean, floor?: string) => void
  deleteSelectedNode: () => void
  deleteSelectedCell: () => void

  // 楼层
  addFloor: () => void
  removeCurrentFloor: () => void
}

export const useTopologyEditorStore = create<TopologyEditorStore>((set, get) => {
  function nextState(update: (draft: TopologyPayload) => TopologyPayload): Partial<TopologyEditorStore> {
    const { draft, dirty } = get()
    const next = update(draft)
    return { draft: next, dirty: dirty || next !== draft }
  }

  function commitSelectionChange(topology: TopologyPayload): Pick<TopologyEditorStore, 'selection'> | Record<string, never> {
    const current = get().selection
    const nextSelection = isValidSelectionForTopology(current, topology)
    return current !== nextSelection ? { selection: nextSelection } : {}
  }

  function withSelection(update: (draft: TopologyPayload) => TopologyPayload) {
    const base = nextState(update)
    const next = { ...base, ...commitSelectionChange(base.draft as TopologyPayload) }
    set(next)
  }

  // 框选矩形内的批量选择：输送带块优先，其次库位节点，最后四向车库槽位。
  function selectBatchAt(box: CellBox) {
    const { draft, activeFloor } = get()
    const conveyorCells = draft.cells.filter(
      (cell) =>
        cell.type === 'conveyor' &&
        (cell.floor ?? GROUND_FLOOR) === activeFloor &&
        isPointInBox(box, cell.row, cell.col),
    )
    if (conveyorCells.length) {
      const blockIds = new Set<string>()
      for (const cell of conveyorCells) {
        for (const id of conveyorBlockCellIds(draft, cell.id)) blockIds.add(id)
      }
      get().selectConveyorBatch([...blockIds], box)
      return
    }
    const ids = draft.nodes
      .filter((node) => node.type === 'storage' && node.floor === activeFloor && isPointInBox(box, node.row, node.col))
      .map((node) => node.id)
    if (ids.length) {
      get().selectStorageBatch(ids, box)
      return
    }
    const slots = shuttleSlotsInBox(draft, box)
    if (slots.length) {
      get().selectShuttleBatch(slots, box)
      return
    }
    set({ selection: null, notice: { kind: 'info', text: '框选区域没有输送带、库位节点或四向车库货架。' } })
  }

  // 「批量选择」工具两次点击定矩形后执行的动作（批量选择 / 批量放置）。
  function runBatch(box: CellBox) {
    const state = get()
    if (state.batchAction === 'select') {
      selectBatchAt(box)
      return
    }
    const placeType = state.batchPlaceType
    if (CELL_BRUSH_TOOLS.has(placeType)) {
      const isGround = placeType === 'ground_conveyor'
      const brush = (isGround ? 'conveyor' : placeType) as CellType
      const next = paintStroke(state.draft, brush, cellsInBox(box), state.activeFloor, { ground: isGround })
      withSelection(() => next)
      set({ notice: { kind: 'info', text: `已批量铺设 ${isGround ? '地面输送线' : CELL_LABELS[brush]}。` } })
      return
    }
    const nodeType = NODE_TOOL_OF[placeType]
    if (nodeType) {
      const result = fillNodes(state.draft, nodeType, box, state.activeFloor)
      withSelection(() => result.topology)
      set({
        notice: result.placed
          ? { kind: 'info', text: `已批量放置 ${result.placed} 个${NODE_LABELS[nodeType]}。` }
          : { kind: 'warn', text: result.message || '该区域没有可放置的格子。' },
      })
    }
  }

  return {
    draft: emptyTopology(),
    savedSnapshot: null,
    loadedId: null,
    loading: false,
    dirty: false,
    notice: null,
    activeFloor: GROUND_FLOOR,
    activeShuttleLevel: 1,
    activeTool: 'select',
    activeZoneType: null,
    batchAction: 'select',
    batchPlaceType: 'road',
    batchOrigin: null,
    panModifierActive: false,
    selection: null,
    marquee: null,
    drag: { mode: 'none', origin: null, current: null, moved: false },
    pointerDown: false,
    lastPaint: null,

    beginNew: () =>
      set({
        draft: emptyTopology(),
        savedSnapshot: null,
        loadedId: null,
        dirty: false,
        notice: { kind: 'info', text: '已新建空白拓扑，点“保存拓扑”落库。' },
        activeFloor: GROUND_FLOOR,
        activeTool: 'select',
        activeZoneType: null,
        batchOrigin: null,
        selection: null,
        marquee: null,
        drag: { mode: 'none', origin: null, current: null, moved: false },
        pointerDown: false,
        lastPaint: null,
      }),

    setLoaded: (payload, recordId) => {
      const floors = floorsOf(payload)
      const activeFloor = floors.includes(get().activeFloor) ? get().activeFloor : floors[0] ?? GROUND_FLOOR
      set({
        draft: payload,
        savedSnapshot: structuredClone(payload),
        loadedId: recordId,
        dirty: false,
        loading: false,
        activeFloor,
        selection: null,
        marquee: null,
        batchOrigin: null,
        notice: null,
        drag: { mode: 'none', origin: null, current: null, moved: false },
      })
    },

    setSaved: (payload, recordId) =>
      set({
        draft: payload,
        savedSnapshot: structuredClone(payload),
        loadedId: recordId,
        dirty: false,
        notice: { kind: 'info', text: '拓扑已保存。' },
      }),

    setLoading: (loading) => set({ loading }),
    setNotice: (notice) => set({ notice }),

    setActiveFloor: (activeFloor) => {
      const floors = floorsOf(get().draft)
      if (!floors.includes(activeFloor)) return
      set({ activeFloor, selection: null, marquee: null, batchOrigin: null, notice: null })
    },
    setActiveShuttleLevel: (activeShuttleLevel) =>
      set({
        activeShuttleLevel: Math.max(1, Math.min(4, Math.floor(activeShuttleLevel))),
        notice: null,
      }),
    setActiveTool: (activeTool) =>
      set({
        activeTool,
        activeZoneType: null,
        notice: null,
        marquee: null,
        batchOrigin: null,
        drag: { mode: 'none', origin: null, current: null, moved: false },
        pointerDown: false,
        lastPaint: null,
      }),
    setActiveZoneType: (zoneType) =>
      set({
        activeZoneType: zoneType,
        notice: null,
        marquee: null,
        drag: { mode: 'none', origin: null, current: null, moved: false },
      }),
    setBatchAction: (batchAction) => set({ batchAction, notice: null, marquee: null, batchOrigin: null }),
    setBatchPlaceType: (batchPlaceType) => set({ batchPlaceType, notice: null }),
    setPanModifierActive: (panModifierActive) => set({ panModifierActive }),

    renameDraft: (name) => withSelection((draft) => ({ ...draft, name })),

    applyGridSize: (rows, cols) =>
      withSelection((draft) => {
        const next = resizeGrid(draft, rows, cols)
        return { ...next, meta: { ...next.meta, viewport: { ...next.meta.viewport } } }
      }),

    applyCellSize: (cellSize) =>
      withSelection((draft) => ({
        ...draft,
        grid: { ...draft.grid, cellSize: Math.max(16, Math.min(96, Math.round(cellSize))) },
      })),

    selectEntityAt: (row, col) => {
      const { draft, activeFloor } = get()
      const node = findNodeAt(draft, row, col, activeFloor)
      if (node) {
        set({ selection: { kind: 'node', id: node.id }, marquee: null, notice: null })
        return
      }
      const cell = findCell(draft, row, col, activeFloor)
      if (cell) {
        set({ selection: { kind: 'cell', id: cell.id }, marquee: null, notice: null })
        return
      }
      set({ selection: null, marquee: null, notice: { kind: 'info', text: `(${row},${col}) 是空白格，可在左侧选择刷格工具铺设。` } })
    },

    selectNode: (nodeId) => set({ selection: { kind: 'node', id: nodeId }, notice: null }),

    selectCellById: (cellId) => {
      const { draft } = get()
      const cell = draft.cells.find((item) => item.id === cellId)
      if (!cell) {
        set({ notice: { kind: 'warn', text: `找不到格子 ${cellId}。` } })
        return
      }
      const floor = cell.floor ?? GROUND_FLOOR
      const floors = floorsOf(draft)
      set({
        selection: { kind: 'cell', id: cell.id },
        activeFloor: floors.includes(floor) ? floor : get().activeFloor,
        notice: null,
        marquee: null,
      })
    },

    selectZone: (zoneId) => set({ selection: { kind: 'zone', id: zoneId }, notice: null }),
    clearSelection: () => set({ selection: null, notice: null }),
    selectStorageBatch: (ids, box) => set({ selection: { kind: 'storage-batch', ids, box }, notice: null }),
    selectConveyorBatch: (ids, box) => set({ selection: { kind: 'conveyor-batch', ids, box }, notice: null }),
    selectShuttleBatch: (slots, box) => set({ selection: { kind: 'shuttle-batch', slots, box }, notice: null }),

    pointerDownAt: (row, col) => {
      const state = get()
      if (state.activeTool === 'batch') {
        if (state.batchOrigin) {
          const box = normalizeSelectionBox(state.batchOrigin, { row, col })
          set({ batchOrigin: null, marquee: null })
          runBatch(box)
        } else {
          set({
            batchOrigin: { row, col },
            marquee: normalizeSelectionBox({ row, col }, { row, col }),
            selection: null,
            notice: { kind: 'info', text: '已确定起点，请点击终点（或按 Esc 取消）。' },
          })
        }
        return
      }
      if (state.activeZoneType) {
        const origin = { row, col }
        set({
          drag: { mode: 'zone', origin, current: cloneGridPoint(origin), moved: false },
          marquee: normalizeSelectionBox(origin, origin),
          selection: null,
        })
        return
      }
      if (state.activeTool === 'select') {
        const origin = { row, col }
        set({
          drag: { mode: 'select', origin, current: cloneGridPoint(origin), moved: false },
          marquee: null,
          selection: null,
        })
        return
      }
      if (state.activeTool === 'erase') {
        const result = eraseAt(state.draft, row, col, state.activeFloor)
        withSelection(() => result.topology)
        set({ pointerDown: true, lastPaint: { row, col }, drag: { mode: 'paint', origin: { row, col }, current: { row, col }, moved: false } })
        return
      }
      if (CELL_BRUSH_TOOLS.has(state.activeTool)) {
        const isGround = state.activeTool === 'ground_conveyor'
        const brushType = (isGround ? 'conveyor' : state.activeTool) as CellType
        const painted = paintCell(state.draft, brushType, row, col, state.activeFloor, null, { ground: isGround })
        withSelection(() => painted.topology)
        set({ pointerDown: true, lastPaint: { row, col }, drag: { mode: 'paint', origin: { row, col }, current: { row, col }, moved: false } })
        return
      }
      const nodeType = NODE_TOOL_OF[state.activeTool]
      if (nodeType) {
        const origin = { row, col }
        set({
          drag: { mode: 'node', origin, current: cloneGridPoint(origin), moved: false },
          marquee: normalizeSelectionBox(origin, origin),
          selection: null,
          notice: null,
        })
        return
      }
    },

    pointerEnterAt: (row, col) => {
      const state = get()
      if (state.activeTool === 'batch' && state.batchOrigin) {
        set({ marquee: normalizeSelectionBox(state.batchOrigin, { row, col }) })
        return
      }
      if (!state.drag.mode) return

      if (state.drag.mode === 'zone') {
        if (!state.drag.origin) return
        const box = normalizeSelectionBox(state.drag.origin, { row, col })
        const moved = box.row1 !== box.row0 || box.col1 !== box.col0
        set({ marquee: box, drag: { ...state.drag, current: { row, col }, moved: state.drag.moved || moved } })
        return
      }

      if (state.drag.mode === 'select') {
        if (!state.drag.origin) return
        const box = normalizeSelectionBox(state.drag.origin, { row, col })
        const moved = box.row1 !== box.row0 || box.col1 !== box.col0
        set({
          marquee: box,
          drag: { ...state.drag, current: { row, col }, moved: state.drag.moved || moved },
        })
        return
      }

      if (state.drag.mode === 'node') {
        if (!state.drag.origin) return
        const box = normalizeSelectionBox(state.drag.origin, { row, col })
        const moved = box.row1 !== box.row0 || box.col1 !== box.col0
        set({
          marquee: box,
          drag: { ...state.drag, current: { row, col }, moved: state.drag.moved || moved },
        })
        return
      }

      // 连续刷格 / 擦除
      if (!state.pointerDown || !state.lastPaint) return
      const tool = state.activeTool
      if (tool === 'select' || tool === 'erase') {
        const cells = interpolateGridCells(state.lastPaint, { row, col }).slice(1)
        if (!cells.length) return
        let next = state.draft
        for (const cell of cells) {
          next = eraseAt(next, cell.row, cell.col, state.activeFloor).topology
        }
        withSelection(() => next)
        set({ lastPaint: { row, col } })
        return
      }
      if (CELL_BRUSH_TOOLS.has(tool)) {
        const isGround = tool === 'ground_conveyor'
        const brush = (isGround ? 'conveyor' : tool) as CellType
        const next = paintStroke(state.draft, brush, [state.lastPaint, { row, col }], state.activeFloor, { ground: isGround })
        withSelection(() => next)
        set({ lastPaint: { row, col } })
      }
    },

    pointerUpAt: () => {
      const state = get()
      const { drag } = state
      if (!drag.mode && !state.pointerDown) return

      if (drag.mode === 'zone') {
        const zoneType = state.activeZoneType
        if (zoneType && drag.origin && state.marquee) {
          const box = normalizeSelectionBox(drag.origin, drag.current ?? drag.origin)
          const area = (box.row1 - box.row0 + 1) * (box.col1 - box.col0 + 1)
          if (area >= 1) {
            const topology = createZone(state.draft, zoneType, box)
            withSelection(() => topology)
            const zone = topology.zones[topology.zones.length - 1]
            set({
              activeZoneType: null,
              selection: zone ? { kind: 'zone', id: zone.id } : state.selection,
              notice: zone ? { kind: 'info', text: `已创建区域 ${zone.id}` } : null,
            })
          }
        }
        set({ marquee: null, drag: { mode: 'none', origin: null, current: null, moved: false } })
        return
      }

      if (drag.mode === 'select') {
        if (!drag.origin) {
          set({ marquee: null, drag: { mode: 'none', origin: null, current: null, moved: false } })
          return
        }
        if (!drag.moved) {
          const { row, col } = drag.origin
          get().selectEntityAt(row, col)
        } else {
          selectBatchAt(normalizeSelectionBox(drag.origin, drag.current ?? drag.origin))
        }
        set({ marquee: null, drag: { mode: 'none', origin: null, current: null, moved: false } })
        return
      }

      if (drag.mode === 'node') {
        const nodeType = NODE_TOOL_OF[state.activeTool]
        if (nodeType && drag.origin) {
          const box = normalizeSelectionBox(drag.origin, drag.current ?? drag.origin)
          const { topology, placed, message } = fillNodes(state.draft, nodeType, box, state.activeFloor)
          withSelection(() => topology)
          set({
            marquee: null,
            notice: placed
              ? { kind: 'info', text: `已放置 ${placed} 个${NODE_LABELS[nodeType]}。` }
              : { kind: 'warn', text: message || '该区域没有可放置的格子。' },
          })
        }
        set({ marquee: null, drag: { mode: 'none', origin: null, current: null, moved: false } })
        return
      }

      if (drag.mode === 'move') {
        const entity = drag.entity
        if (entity && drag.origin && drag.current && drag.moved) {
          if (entity.kind === 'node') {
            const result = moveNodeInTopology(state.draft, entity.id, drag.current.row, drag.current.col, entity.floor)
            if (result) {
              if (result.message) set({ notice: { kind: 'warn', text: result.message } })
              else withSelection(() => result.topology)
            }
          } else {
            const zone = state.draft.zones.find((item) => item.id === entity.id)
            if (zone) {
              const dCol = drag.current.col - drag.origin.col
              const dRow = drag.current.row - drag.origin.row
              const moved = moveZoneInTopology(state.draft, entity.id, dRow, dCol)
              withSelection(() => moved)
            }
          }
        }
        set({ marquee: null, drag: { mode: 'none', origin: null, current: null, moved: false } })
        return
      }

      // 连续刷结束
      set({ pointerDown: false, lastPaint: null, drag: { mode: 'none', origin: null, current: null, moved: false } })
    },

    cancelGesture: () =>
      set({
        drag: { mode: 'none', origin: null, current: null, moved: false },
        marquee: null,
        batchOrigin: null,
        pointerDown: false,
        lastPaint: null,
      }),

    entityPointerDown: (kind, id, floor) => {
      const state = get()
      if (state.activeTool !== 'select') return
      if (kind === 'cell') {
        const cell = state.draft.cells.find((item) => item.id === id)
        // 输送线出入口区域常与输送带格重叠：点击落在带面/滚筒上时优先选中出入口区域，
        // 否则事件会被输送带格拦截，导致出入口怎么点都选不中。
        const portZone = cell
          ? state.draft.zones.find(
              (zone) =>
                zone.type === 'port' &&
                cell.row >= zone.y &&
                cell.row < zone.y + zone.height &&
                cell.col >= zone.x &&
                cell.col < zone.x + zone.width,
            )
          : undefined
        if (portZone) {
          set({ selection: { kind: 'zone', id: portZone.id }, notice: null, marquee: null })
          return
        }
        if (cell?.type === 'conveyor') {
          set({
            selection: { kind: 'cell', id },
            notice: null,
            marquee: null,
            drag: { mode: 'none', origin: null, current: null, moved: false },
          })
        } else {
          get().selectCellById(id)
        }
        return
      }
      if (kind === 'shuttle-slot') {
        const slot = parseShuttleSlotEntityId(id)
        if (!slot) return
        set({
          selection: { kind: 'shuttle-slot', zoneId: slot.zoneId, row: slot.row, col: slot.col, floor },
          notice: null,
          marquee: null,
        })
        return
      }
      if (kind !== 'node' && kind !== 'zone') return
      let anchor: GridPoint | null = null
      if (kind === 'node') {
        const node = state.draft.nodes.find((item) => item.id === id)
        if (!node) return
        anchor = { row: node.row, col: node.col }
        set({ selection: { kind: 'node', id }, notice: null })
      } else {
        const zone = state.draft.zones.find((item) => item.id === id)
        if (!zone) return
        anchor = { row: zone.y, col: zone.x }
        set({ selection: { kind: 'zone', id }, notice: null })
      }
      if (!anchor) return
      set({
        drag: { mode: 'move', origin: anchor, current: cloneGridPoint(anchor), moved: false, entity: { kind, id, floor } },
        marquee: null,
      })
    },

    moveCell: (row, col) => {
      const state = get()
      const drag = state.drag
      if (drag.mode !== 'move' || !drag.origin || !drag.entity) return
      const entity = drag.entity
      const moved = row !== drag.origin.row || col !== drag.origin.col
      let marquee: CellBox | null = null
      if (entity.kind === 'node') {
        marquee = { row0: row, col0: col, row1: row, col1: col }
      } else {
        const zone = state.draft.zones.find((item) => item.id === entity.id)
        if (zone) {
          const dCol = col - drag.origin.col
          const dRow = row - drag.origin.row
          const x = Math.max(0, Math.min(state.draft.grid.cols - zone.width, zone.x + dCol))
          const y = Math.max(0, Math.min(state.draft.grid.rows - zone.height, zone.y + dRow))
          marquee = { row0: y, col0: x, row1: y + zone.height - 1, col1: x + zone.width - 1 }
        }
      }
      set({ marquee, drag: { ...drag, current: { row, col }, moved: drag.moved || moved } })
    },

    moveNode: (nodeId, row, col, floor) => {
      const state = get()
      const result = moveNodeInTopology(state.draft, nodeId, row, col, floor || state.activeFloor)
      if (!result) return
      if (result.message) {
        set({ notice: { kind: 'warn', text: result.message } })
        return
      }
      withSelection(() => result.topology)
    },

    updateCell: (id, patch) =>
      withSelection((draft) => {
        const cell = draft.cells.find((item) => item.id === id)
        if (!cell) return draft
        // 输送带按 2×2 块联动：方向/层数/地面标记/介质类型变更同步到整块 4 格
        const touchesConveyorField =
          cell.type === 'conveyor' &&
          (patch.conveyorDirection !== undefined ||
            patch.levels !== undefined ||
            patch.conveyorGround !== undefined ||
            patch.type !== undefined)
        const ids = touchesConveyorField ? new Set(conveyorBlockCellIds(draft, id)) : new Set([id])
        const cells = draft.cells.map((item) => (ids.has(item.id) ? ({ ...item, ...patch } as TopologyCell) : item))
        return { ...draft, cells }
      }),

    updateNode: (id, patch) =>
      withSelection((draft) => {
        const node = draft.nodes.find((item) => item.id === id)
        if (!node) return draft
        const index = draft.nodes.findIndex((item) => item.id === id)
        const nodes = draft.nodes.slice()
        const updated = { ...node, ...patch } as TopologyNode
        nodes[index] = updated
        let next = { ...draft, nodes }
        const changesTurnDirection = node.type === 'turn_transfer' && (
          patch.transferDeck !== undefined
          || patch.transferInputSide !== undefined
          || patch.transferOutputSide !== undefined
        )
        if (changesTurnDirection) {
          const mount = next.cells.find((cell) =>
            cell.type === 'conveyor'
            && (cell.floor ?? GROUND_FLOOR) === (updated.floor ?? GROUND_FLOOR)
            && cell.row === updated.row
            && cell.col === updated.col,
          )
          const deck = updated.transferDeck ?? 'lower'
          if (mount && updated.transferInputSide && updated.transferOutputSide) {
            next = updateConveyorBlockDeck(next, mount.id, deck, {
              inputSide: updated.transferInputSide,
              outputSide: updated.transferOutputSide,
            })
          }
        }
        return next
      }),

    patchZone: (id, patch) =>
      withSelection((draft) => {
        const zone = draft.zones.find((item) => item.id === id)
        if (!zone) return draft
        const geometryChanged =
          patch.x !== undefined || patch.y !== undefined || patch.width !== undefined || patch.height !== undefined
        let next = { ...draft }
        const index = next.zones.findIndex((item) => item.id === id)
        const zones = next.zones.slice()
        zones[index] = { ...zone, ...patch }
        next = { ...next, zones }
        if (geometryChanged) next = updateZoneGeometry(next, id, { ...patch })
        return next
      }),

    deleteZone: (id) =>
      withSelection((draft) => {
        const next = removeZone(draft, id)
        return next
      }),

    resyncZone: (id) => withSelection((draft) => syncZoneMembership(draft, id)),

    batchAssignZone: (nodeIds, zoneId) =>
      withSelection((draft) => {
        const setIds = new Set(nodeIds)
        const nodes = draft.nodes.map((node) =>
          setIds.has(node.id) && node.type === 'storage' ? { ...node, zoneId } : node,
        )
        return { ...draft, nodes }
      }),

    updateConveyorLevels: (ids, levels) =>
      withSelection((draft) => {
        const setIds = new Set(ids)
        const cells = draft.cells.map((cell) => {
          if (!setIds.has(cell.id) || cell.type !== 'conveyor') return cell
          const isAnchor = cell.conveyorAnchor
            ? cell.row === cell.conveyorAnchor.row && cell.col === cell.conveyorAnchor.col
            : cell.row % 2 === 0 && cell.col % 2 === 0
          const decks = levels === 1 && isAnchor && cell.conveyorDecks
            ? { lower: cell.conveyorDecks.lower }
            : cell.conveyorDecks
          return {
            ...cell,
            levels,
            ...(isAnchor ? { conveyorDecks: decks } : {}),
          }
        })
        return { ...draft, cells }
      }),

    updateConveyorGround: (ids, ground) =>
      withSelection((draft) => {
        const setIds = new Set(ids)
        const cells = draft.cells.map((cell) =>
          setIds.has(cell.id) && cell.type === 'conveyor' ? { ...cell, conveyorGround: ground } : cell,
        )
        return { ...draft, cells }
      }),

    updateConveyorBlockDeck: (cellId, deck, patch) =>
      withSelection((draft) => updateConveyorBlockDeck(draft, cellId, deck, patch)),

    applyShuttleAisle: (slots, aisle, floor) =>
      withSelection((draft) =>
        setShuttleSlotAisle(draft, slots, aisle, floor ?? get().activeFloor, get().activeShuttleLevel),
      ),

    applyShuttleSlotRemove: (slots, removed, floor) =>
      withSelection((draft) => setShuttleSlotRemoved(draft, slots, removed, floor ?? get().activeFloor)),

    applyShuttleLift: (slots, lift, floor) =>
      withSelection((draft) => setShuttleSlotLift(draft, slots, lift, floor ?? get().activeFloor)),

    deleteSelectedNode: () => {
      const state = get()
      const selection = state.selection
      if (!selection || selection.kind !== 'node') return
      const node = state.draft.nodes.find((item) => item.id === selection.id)
      if (!node) return
      const result = eraseAt(state.draft, node.row, node.col, node.floor ?? state.activeFloor)
      withSelection(() => result.topology)
    },

    deleteSelectedCell: () => {
      const state = get()
      const selection = state.selection
      if (!selection || selection.kind !== 'cell') return
      const cell = state.draft.cells.find((item) => item.id === selection.id)
      if (!cell) return
      const result = eraseAt(state.draft, cell.row, cell.col, cell.floor ?? state.activeFloor)
      withSelection(() => result.topology)
    },

    addFloor: () =>
      withSelection((draft) => {
        const floors = floorsOf(draft)
        const numbered = floors
          .map((floor) => /^(\d+)F$/.exec(floor))
          .filter((match): match is RegExpExecArray => Boolean(match))
          .map((match) => Number(match[1]))
        const nextNumber = numbered.length ? Math.max(...numbered) + 1 : 2
        const label = `${nextNumber}F`
        return { ...draft, meta: { ...draft.meta, floors: [...floors, label] } }
      }),

    removeCurrentFloor: () => {
      const state = get()
      if (state.activeFloor === GROUND_FLOOR) {
        set({ notice: { kind: 'warn', text: '1F 是地面层，不能删除。' } })
        return
      }
      const removed = state.activeFloor
      const next = removeFloor(state.draft, removed)
      const floors = floorsOf(next)
      const activeFloor = floors.includes(state.activeFloor) ? state.activeFloor : floors[0] ?? GROUND_FLOOR
      set({ draft: next, dirty: state.dirty || next !== state.draft, activeFloor, notice: { kind: 'info', text: `已删除楼层 ${removed} 及其上的格子与节点。` } })
    },

  }
})

// 选择导出
export function selectionCellId(selection: TopologySelection): string | null {
  return selection?.kind === 'cell' ? selection.id : null
}
export function selectionNodeId(selection: TopologySelection): string | null {
  return selection?.kind === 'node' ? selection.id : null
}

export function conveyorDirectionCycle(direction: ConveyorDirection): ConveyorDirection {
  const order: ConveyorDirection[] = ['right', 'down', 'left', 'up']
  return order[(order.indexOf(direction) + 1) % order.length]
}
