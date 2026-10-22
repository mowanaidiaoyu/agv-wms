import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { PathPlaybackController } from '../features/path-demo/PathPlaybackController'
import { PathDemoModeSelect } from '../features/path-demo/PathDemoModeSelect'
import type { PathDemoMode } from '../features/path-demo/PathDemoModeSelect'
import { ConflictAvoidancePanel } from '../features/path-demo/ConflictAvoidancePanel'
import {
  conflictSeekTime,
  primaryNodeConflict,
  resolveConflictPlaybackRate,
} from '../features/path-demo/conflictVisualization'
import { createCustomVehicle, validateMultiVehicleConfiguration } from '../features/path-demo/customVehicleConfig'
import { useMultiPathExperimentStore } from '../features/path-demo/multiPathExperimentStore'
import { DEFAULT_MULTI_PATH_PLANNING_CONFIG, pathDemoApi } from '../features/path-demo/pathDemoApi'
import {
  buildConfiguredVehicleStartSample,
  buildEndpointMarkers,
  buildMultiVehicleViews,
  MULTI_VEHICLE_STYLES,
  sampleAllVehiclesAtTime,
} from '../features/path-demo/multiPathPlayback'
import { buildTrajectory, sampleTrajectory, usePathPlayback } from '../features/path-demo/pathPlayback'
import type { CameraActionRequest, CameraViewMode } from '../features/path-demo/cameraControls'
import type {
  MultiPathDemoScenario,
  MultiPathConflictEvent,
  MultiPathPlan,
  MultiPathVehicleRequest,
  PathDemoAlgorithm,
  PathDemoCell,
  PathDemoPlan,
  PlaybackRate,
  RoadSelectionKind,
} from '../features/path-demo/types'
import { api } from '../lib/api'
import { floorPlaneY, worldX, worldZ } from '../lib/topology3d'
import { ShuttleDemoWorkspace } from '../features/shuttle-lift/ShuttleDemoWorkspace'
import './PathDemoPage.css'

const PathDemo3DScene = lazy(() =>
  import('../features/path-demo/PathDemo3DScene').then((module) => ({ default: module.PathDemo3DScene })),
)

const statusLabel = {
  idle: '待运行',
  running: '运行中',
  paused: '已暂停',
  completed: '已到达',
}

const planningStatusLabel = {
  idle: '未规划',
  planning: '规划中',
  success: '规划成功',
  failed: '规划失败',
}

type DemoMode = 'single' | 'preset' | 'custom'
type VehicleType = 'ctu' | 'shuttle'
type PlanningStatus = keyof typeof planningStatusLabel
type RoadSelection = { vehicleId: string; kind: RoadSelectionKind } | null
type ShuttlePathDemoMode = Extract<PathDemoMode, `shuttle_${string}`>

const SHUTTLE_PATH_DEMO_MODES: readonly ShuttlePathDemoMode[] = [
  'shuttle_single',
  'shuttle_multi_preset',
  'shuttle_multi_custom',
  'shuttle_stock_transfer',
]

function normalizeShuttleMode(value: string | null): ShuttlePathDemoMode {
  return SHUTTLE_PATH_DEMO_MODES.includes(value as ShuttlePathDemoMode)
    ? value as ShuttlePathDemoMode
    : 'shuttle_single'
}

function liveVehicleStatusLabel(
  globalStatus: keyof typeof statusLabel,
  snapshot: ReturnType<typeof sampleAllVehiclesAtTime>[number] | undefined,
  plannedWaitSeconds: number,
) {
  if (globalStatus === 'idle') return '待运行'
  if (snapshot?.status === 'COMPLETED') return '已到达'
  if (snapshot?.status === 'WAITING') return `等待让行（${snapshot.accumulatedWaitSeconds.toFixed(2)} / ${plannedWaitSeconds.toFixed(2)} s）`
  if (globalStatus === 'paused') return '已暂停（移动中）'
  return '移动中'
}

function normalizeVehicles(vehicles: MultiPathVehicleRequest[]): MultiPathVehicleRequest[] {
  return vehicles.map((vehicle, index) => ({
    ...vehicle,
    vehicle_type: 'ctu',
    priority: vehicle.priority ?? index + 1,
    display_color: vehicle.display_color ?? MULTI_VEHICLE_STYLES[index]?.color ?? '#e2e8f0',
  }))
}

export function PathDemoPage({ initialVehicleType = 'shuttle' }: { initialVehicleType?: VehicleType } = {}) {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const topologiesQuery = useQuery({ queryKey: ['topologies'], queryFn: api.listTopologies })
  const [topologyId, setTopologyId] = useState('')
  const [demoMode, setDemoMode] = useState<DemoMode>('single')
  const [vehicleType, setVehicleType] = useState<VehicleType>(initialVehicleType)
  const requestedShuttleMode = searchParams.get('mode')
  const shuttleDemoMode = normalizeShuttleMode(requestedShuttleMode)
  const previousShuttlePlanningModeRef = useRef<ShuttlePathDemoMode>(
    shuttleDemoMode === 'shuttle_stock_transfer' ? 'shuttle_single' : shuttleDemoMode,
  )
  const [algorithm, setAlgorithm] = useState<PathDemoAlgorithm>('astar')
  const [startCellId, setStartCellId] = useState('')
  const [goalCellId, setGoalCellId] = useState('')
  const [plan, setPlan] = useState<PathDemoPlan | null>(null)
  const [multiPlan, setMultiPlan] = useState<MultiPathPlan | null>(null)
  const [presetScenarioId, setPresetScenarioId] = useState<MultiPathDemoScenario['id']>('parallel')
  const [presetVehicles, setPresetVehicles] = useState<MultiPathVehicleRequest[]>([])
  const [customVehicles, setCustomVehicles] = useState<MultiPathVehicleRequest[]>([])
  const [selectedVehicleId, setSelectedVehicleId] = useState('CTU-01')
  const [roadSelection, setRoadSelection] = useState<RoadSelection>(null)
  const [selectionError, setSelectionError] = useState('')
  const [planningStatus, setPlanningStatus] = useState<PlanningStatus>('idle')
  const [planningError, setPlanningError] = useState('')
  const [cameraViewMode, setCameraViewMode] = useState<CameraViewMode>('free')
  const [cameraActionRequest, setCameraActionRequest] = useState<CameraActionRequest | null>(null)
  const [preferredPlaybackRate, setPreferredPlaybackRate] = useState<PlaybackRate>(1)
  const [autoSlowConflict, setAutoSlowConflict] = useState(false)
  const [selectedConflictEventId, setSelectedConflictEventId] = useState('')
  const requestSequenceRef = useRef(0)
  const setCustomDraft = useMultiPathExperimentStore((state) => state.setCustomDraft)

  useEffect(() => {
    if (vehicleType !== 'shuttle' || requestedShuttleMode === shuttleDemoMode) return
    setSearchParams((current) => {
      const currentMode = current.get('mode')
      if (SHUTTLE_PATH_DEMO_MODES.includes(currentMode as ShuttlePathDemoMode)) return current
      const next = new URLSearchParams(current)
      next.set('mode', shuttleDemoMode)
      return next
    }, { replace: true })
  }, [requestedShuttleMode, setSearchParams, shuttleDemoMode, vehicleType])

  useEffect(() => {
    if (shuttleDemoMode !== 'shuttle_stock_transfer') {
      previousShuttlePlanningModeRef.current = shuttleDemoMode
    }
  }, [shuttleDemoMode])

  useEffect(() => {
    if (!topologiesQuery.data) return
    if (!topologiesQuery.data.length) {
      if (topologyId) setTopologyId('')
      return
    }
    if (topologiesQuery.data.some((item) => item.id === topologyId)) return
    const preferred = topologiesQuery.data.find((item) => item.name === '666') ?? topologiesQuery.data[0]
    setTopologyId(preferred.id)
  }, [topologiesQuery.data, topologyId])

  const topologyRecord = topologiesQuery.data?.find((item) => item.id === topologyId)
  const topologyList = topologiesQuery.data ?? []
  const topologyLoadFailed = topologiesQuery.isError && topologyList.length === 0
  const topologyListEmpty = topologiesQuery.isSuccess && topologyList.length === 0
  const topologyPlaceholder = topologiesQuery.isPending
    ? '正在加载拓扑…'
    : topologyLoadFailed
      ? '拓扑加载失败'
      : topologyListEmpty
        ? '没有可用拓扑'
        : '正在选择默认拓扑…'
  useEffect(() => {
    if (demoMode !== 'custom' || !topologyRecord) return
    setCustomDraft({
      topologyId,
      topologyVersion: topologyRecord.payload.meta.version,
      vehicles: customVehicles.map((vehicle) => ({ ...vehicle })),
      planningConfig: { ...DEFAULT_MULTI_PATH_PLANNING_CONFIG },
    })
  }, [customVehicles, demoMode, setCustomDraft, topologyId, topologyRecord])
  const optionsQuery = useQuery({
    queryKey: ['path-demo-options', topologyId],
    queryFn: () => pathDemoApi.options(topologyId),
    enabled: vehicleType === 'ctu' && Boolean(topologyId),
  })
  const multiScenariosQuery = useQuery({
    queryKey: ['path-demo-multi-scenarios', topologyId],
    queryFn: () => pathDemoApi.multiScenarios(topologyId),
    enabled: vehicleType === 'ctu' && Boolean(topologyId) && demoMode !== 'single',
  })
  const roadCells = useMemo(() => multiScenariosQuery.data?.road_cells ?? [], [multiScenariosQuery.data])
  const activeVehicles = useMemo(
    () => demoMode === 'preset' ? presetVehicles : demoMode === 'custom' ? customVehicles : [],
    [customVehicles, demoMode, presetVehicles],
  )

  const trajectory = useMemo(
    () => (plan && topologyRecord ? buildTrajectory(plan.path_cells, topologyRecord.payload) : []),
    [plan, topologyRecord],
  )
  const singleDuration = trajectory.at(-1)?.arrivalSeconds ?? 0
  const duration = demoMode === 'single' ? singleDuration : (multiPlan?.makespan_seconds ?? 0)
  const {
    state: playbackState,
    start: startPlayback,
    pause: pausePlayback,
    resume: resumePlayback,
    reset: resetPlayback,
    seek: seekPlayback,
    setRate: setPlaybackRate,
  } = usePathPlayback(duration)
  const sample = useMemo(
    () => sampleTrajectory(trajectory, playbackState.elapsedSeconds),
    [playbackState.elapsedSeconds, trajectory],
  )
  const vehicleSnapshots = useMemo(
    () => multiPlan ? sampleAllVehiclesAtTime(multiPlan, playbackState.elapsedSeconds) : [],
    [multiPlan, playbackState.elapsedSeconds],
  )
  const multiVehicleViews = useMemo(() => {
    if (demoMode === 'single' || !multiPlan || !topologyRecord) return []
    return buildMultiVehicleViews(multiPlan, topologyRecord.payload, playbackState.elapsedSeconds).map((view) => {
      const vehicle = multiPlan.vehicles.find((item) => item.vehicle_id === view.vehicleId)
      const snapshot = vehicleSnapshots.find((item) => item.vehicleId === view.vehicleId)
      return {
        ...view,
        statusText: liveVehicleStatusLabel(playbackState.status, snapshot, vehicle?.wait_time_seconds ?? 0),
      }
    })
  }, [demoMode, multiPlan, playbackState.elapsedSeconds, playbackState.status, topologyRecord, vehicleSnapshots])
  const nodeConflictEvents = useMemo(
    () => (multiPlan?.conflict_events ?? []).filter((event) => event.conflict_type === 'node'),
    [multiPlan],
  )
  const primaryConflictEvent = useMemo(() => primaryNodeConflict(nodeConflictEvents), [nodeConflictEvents])
  const selectedConflictEvent = nodeConflictEvents.find((event) => event.event_id === selectedConflictEventId)
    ?? primaryConflictEvent
  const showNodeConflictVisualization = demoMode === 'preset' && presetScenarioId === 'node-conflict' && Boolean(multiPlan)
  const selectedPlannedVehicleSample = demoMode !== 'single'
    ? (multiVehicleViews.find((vehicle) => vehicle.vehicleId === selectedVehicleId)?.sample ?? null)
    : sample
  const configuredStartPreview = useMemo(
    () => (
      demoMode === 'custom' && topologyRecord
        ? buildConfiguredVehicleStartSample(
            customVehicles.find((vehicle) => vehicle.vehicle_id === selectedVehicleId),
            roadCells,
            topologyRecord.payload,
          )
        : null
    ),
    [customVehicles, demoMode, roadCells, selectedVehicleId, topologyRecord],
  )
  const selectedVehicleSample = selectedPlannedVehicleSample ?? configuredStartPreview
  const hasFollowableTrajectory = Boolean(selectedPlannedVehicleSample)
  const activeConfigurationError = demoMode === 'single' ? '' : validateMultiVehicleConfiguration(activeVehicles, roadCells)
  const endpointMarkers = useMemo(
    () => (
      demoMode !== 'single' && topologyRecord
        ? buildEndpointMarkers(activeVehicles, roadCells, topologyRecord.payload)
        : []
    ),
    [activeVehicles, demoMode, roadCells, topologyRecord],
  )

  const invalidatePlan = () => {
    requestSequenceRef.current += 1
    setPlan(null)
    setMultiPlan(null)
    setPlanningStatus('idle')
    setPlanningError('')
    setCameraViewMode('free')
    setCameraActionRequest(null)
    resetPlayback()
  }

  useEffect(() => {
    if (!optionsQuery.data) return
    setStartCellId(optionsQuery.data.default_start.id)
    setGoalCellId(optionsQuery.data.default_goal.id)
    setPlan(null)
    setMultiPlan(null)
    setPlanningStatus('idle')
    setPlanningError('')
    setCameraViewMode('free')
    setCameraActionRequest(null)
    resetPlayback()
  }, [optionsQuery.data, resetPlayback])

  useEffect(() => {
    const scenarios = multiScenariosQuery.data?.scenarios
    if (!scenarios?.length) return
    const selected = scenarios.find((item) => item.id === presetScenarioId) ?? scenarios[0]
    const normalized = normalizeVehicles(selected.vehicles)
    setPresetScenarioId(selected.id)
    setPresetVehicles(normalized)
    setCustomVehicles((current) => current.length ? current : normalized.map((vehicle) => ({ ...vehicle })))
  }, [multiScenariosQuery.data, presetScenarioId])

  useEffect(() => {
    if (demoMode === 'single') {
      if (selectedVehicleId !== 'CTU-01') setSelectedVehicleId('CTU-01')
      return
    }
    if (!activeVehicles.length) return
    if (!activeVehicles.some((vehicle) => vehicle.vehicle_id === selectedVehicleId)) {
      setSelectedVehicleId(activeVehicles[0].vehicle_id)
    }
  }, [activeVehicles, demoMode, selectedVehicleId])

  useEffect(() => {
    if (!showNodeConflictVisualization) {
      setSelectedConflictEventId('')
      return
    }
    setSelectedConflictEventId((current) => (
      nodeConflictEvents.some((event) => event.event_id === current)
        ? current
        : (primaryConflictEvent?.event_id ?? '')
    ))
  }, [nodeConflictEvents, primaryConflictEvent, showNodeConflictVisualization])

  useEffect(() => {
    const effectiveRate = resolveConflictPlaybackRate(
      preferredPlaybackRate,
      autoSlowConflict && showNodeConflictVisualization,
      selectedConflictEvent,
      playbackState.elapsedSeconds,
      multiPlan?.time_step_seconds ?? 0,
    )
    if (playbackState.rate !== effectiveRate) setPlaybackRate(effectiveRate)
  }, [
    autoSlowConflict,
    multiPlan?.time_step_seconds,
    playbackState.elapsedSeconds,
    playbackState.rate,
    preferredPlaybackRate,
    selectedConflictEvent,
    setPlaybackRate,
    showNodeConflictVisualization,
  ])

  const requestCameraAction = (type: CameraActionRequest['type'], targetWorld?: [number, number, number]) => {
    setCameraActionRequest((current) => ({ type, targetWorld, sequence: (current?.sequence ?? 0) + 1 }))
  }

  const locateConflictEvent = (event: MultiPathConflictEvent, seekToEvent: boolean) => {
    if (!topologyRecord) return
    const floors = topologyRecord.payload.meta.floors?.length ? topologyRecord.payload.meta.floors : ['1F']
    const floorIndex = Math.max(0, floors.indexOf(event.cell.floor))
    const targetWorld: [number, number, number] = [
      worldX(event.cell.col, topologyRecord.payload.grid.cols),
      floorPlaneY(floorIndex) + 0.22,
      worldZ(event.cell.row, topologyRecord.payload.grid.rows),
    ]
    setSelectedConflictEventId(event.event_id)
    setCameraViewMode('free')
    requestCameraAction('focus-point', targetWorld)
    if (seekToEvent && multiPlan) {
      seekPlayback(conflictSeekTime(event, multiPlan.time_step_seconds))
    }
  }

  const requestPlan = async () => {
    if (!topologyId || (demoMode === 'single' && (!startCellId || !goalCellId)) || activeConfigurationError) return
    const requestId = requestSequenceRef.current + 1
    requestSequenceRef.current = requestId
    setPlanningStatus('planning')
    setPlanningError('')
    setPlan(null)
    setMultiPlan(null)
    setCameraViewMode('free')
    setCameraActionRequest(null)
    resetPlayback()
    try {
      if (demoMode === 'preset') {
        const result = await pathDemoApi.planMulti({ topologyId, algorithm, mode: 'preset', scenarioId: presetScenarioId })
        if (requestSequenceRef.current === requestId) setMultiPlan(result)
      } else if (demoMode === 'custom') {
        const result = await pathDemoApi.planMulti({ topologyId, vehicles: customVehicles, algorithm, mode: 'custom' })
        if (requestSequenceRef.current === requestId) setMultiPlan(result)
      } else {
        const result = await pathDemoApi.plan(topologyId, startCellId, goalCellId, algorithm)
        if (requestSequenceRef.current === requestId) setPlan(result)
      }
      if (requestSequenceRef.current === requestId) setPlanningStatus('success')
    } catch (error) {
      if (requestSequenceRef.current !== requestId) return
      setPlan(null)
      setMultiPlan(null)
      setPlanningStatus('failed')
      setPlanningError(error instanceof Error ? error.message : '路径规划失败')
    }
  }

  const setCustomEndpoint = (vehicleId: string, kind: RoadSelectionKind, cellId: string) => {
    const duplicate = customVehicles.some((vehicle) => (
      vehicle.vehicle_id !== vehicleId
      && (kind === 'start' ? vehicle.start_cell_id : vehicle.goal_cell_id) === cellId
    ))
    if (cellId && duplicate) {
      setSelectionError(`该${kind === 'start' ? '起点' : '终点'}已被其他车辆使用。`)
      return false
    }
    setCustomVehicles((current) => current.map((vehicle) => (
      vehicle.vehicle_id === vehicleId
        ? { ...vehicle, [kind === 'start' ? 'start_cell_id' : 'goal_cell_id']: cellId }
        : vehicle
    )))
    setSelectionError('')
    invalidatePlan()
    return true
  }

  const handleRoadCellSelect = (cell: PathDemoCell) => {
    if (!roadSelection) return
    if (setCustomEndpoint(roadSelection.vehicleId, roadSelection.kind, cell.id)) setRoadSelection(null)
  }

  const removeCustomVehicle = (vehicleId: string) => {
    if (customVehicles.length <= 1) return
    const nextVehicles = customVehicles.filter((vehicle) => vehicle.vehicle_id !== vehicleId)
    setCustomVehicles(nextVehicles)
    if (selectedVehicleId === vehicleId) setSelectedVehicleId(nextVehicles[0].vehicle_id)
    if (roadSelection?.vehicleId === vehicleId) setRoadSelection(null)
    invalidatePlan()
  }

  const optionsError = optionsQuery.error instanceof Error ? optionsQuery.error.message : ''
  const scenariosError = multiScenariosQuery.error instanceof Error ? multiScenariosQuery.error.message : ''
  const markerFloor = demoMode !== 'single'
    ? roadCells.find((cell) => cell.id === activeVehicles[0]?.start_cell_id)?.floor
    : undefined
  const activeFloor = multiPlan?.vehicles[0]?.start.floor ?? markerFloor ?? plan?.start.floor ?? optionsQuery.data?.default_start.floor ?? '1F'
  const canPlay = demoMode === 'single' ? trajectory.length > 1 : Boolean(multiPlan && multiPlan.makespan_seconds > 0)
  const totalDistance = multiPlan?.vehicles.reduce((sum, vehicle) => sum + vehicle.total_distance, 0)
  const totalWait = multiPlan?.vehicles.reduce((sum, vehicle) => sum + vehicle.wait_time_seconds, 0)
  const isShuttlePlanning = vehicleType === 'shuttle'
  const isShuttleTransfer = isShuttlePlanning && shuttleDemoMode === 'shuttle_stock_transfer'
  const selectedDemoMode: PathDemoMode = vehicleType === 'shuttle'
    ? shuttleDemoMode
    : demoMode === 'single'
      ? 'ctu_single'
      : demoMode === 'preset'
        ? 'ctu_multi_preset'
        : 'ctu_multi_custom'
  const selectDemoMode = (nextMode: PathDemoMode) => {
    invalidatePlan()
    setRoadSelection(null)
    setSelectionError('')
    if (nextMode.startsWith('shuttle_')) {
      setVehicleType('shuttle')
      setSearchParams((current) => {
        const next = new URLSearchParams(current)
        next.set('mode', nextMode)
        return next
      })
      return
    }
    setVehicleType('ctu')
    setDemoMode(nextMode === 'ctu_single' ? 'single' : nextMode === 'ctu_multi_preset' ? 'preset' : 'custom')
  }

  const setActiveTransferTaskId = (taskId: string | null) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (taskId) next.set('task', taskId)
      else next.delete('task')
      return next
    }, { replace: true })
  }

  return (
    <section className="path-demo-page">
      <header className="path-demo-header">
        <div>
          <p className="eyebrow">Gate C-3D-3 · deterministic space-time path playback</p>
          <h2>{isShuttleTransfer ? '四向车 SKU 货物搬运（单车演示）' : isShuttlePlanning ? '四向车单车 / 多车统一路径规划工作台' : demoMode === 'single' ? '真实拓扑单车 3D 轨迹演示' : '真实拓扑多车 3D 冲突避让演示'}</h2>
          <p>{isShuttleTransfer ? '使用独立 synthetic_demo 库存和真实 ShuttleGraph 轨迹演示取货、跨层运输与放货；不控制真实设备。' : isShuttlePlanning ? '统一播放后端 ShuttleGraph 单车或时空预约结果；跨层仅使用程序验证的 Lift-A / Lift-B，本页面不控制真实设备。' : '路径来自后端 A* / Dijkstra；多车模式使用 0.25 秒时间片的优先级时空规划，不创建真实设备控制命令。'}</p>
        </div>
        <span className="path-demo-mode">{isShuttleTransfer ? 'SHUTTLE · SKU 单车搬运演示' : isShuttlePlanning ? `SHUTTLE · ${shuttleDemoMode === 'shuttle_single' ? '单车' : '多车'}离线演示` : `CTU · ${demoMode === 'single' ? '单车演示模式' : '多车离线演示'}`}</span>
      </header>

      {isShuttlePlanning && topologyRecord ? (
        <ShuttleDemoWorkspace
          topologyRecord={topologyRecord}
          topologies={topologiesQuery.data ?? []}
          onTopologyChange={(nextTopologyId) => {
            setTopologyId(nextTopologyId)
            setActiveTransferTaskId(null)
            setStartCellId('')
            setGoalCellId('')
            setPresetVehicles([])
            setCustomVehicles([])
            setRoadSelection(null)
            invalidatePlan()
          }}
          demoMode={selectedDemoMode}
          onDemoModeChange={selectDemoMode}
          onReturnToPlanning={() => selectDemoMode(previousShuttlePlanningModeRef.current)}
          activeTransferTaskId={searchParams.get('task')}
          onActiveTransferTaskIdChange={setActiveTransferTaskId}
          onOpenAdvancedManagement={() => navigate('/topology')}
        />
      ) : (
      <div className="path-demo-layout">
        <aside className="path-demo-panel">
          <label>
            拓扑
            <select
              value={topologyId}
              disabled={topologiesQuery.isPending || topologyLoadFailed || topologyListEmpty}
              onChange={(event) => {
                setTopologyId(event.target.value)
                setStartCellId('')
                setGoalCellId('')
                setPresetVehicles([])
                setCustomVehicles([])
                setRoadSelection(null)
                invalidatePlan()
              }}
            >
              {!topologyRecord ? <option value="">{topologyPlaceholder}</option> : null}
              {topologyList.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <PathDemoModeSelect value={selectedDemoMode} onChange={selectDemoMode} />
          {vehicleType === 'ctu' ? (
            <>
          <label>
            路径算法
            <select value={algorithm} onChange={(event) => { setAlgorithm(event.target.value as PathDemoAlgorithm); invalidatePlan() }}>
              <option value="astar">A*</option>
              <option value="dijkstra">Dijkstra</option>
            </select>
          </label>

          {demoMode === 'single' ? (
            <>
              <label>
                起点（可行驶道路）
                <select value={startCellId} disabled={!optionsQuery.data} onChange={(event) => { setStartCellId(event.target.value); invalidatePlan() }}>
                  {(optionsQuery.data?.traversable_cells ?? []).filter((cell) => cell.type === 'road').map((cell) => (
                    <option key={cell.id} value={cell.id}>{cell.id} · {cell.floor} · {cell.component_id}</option>
                  ))}
                </select>
              </label>
              <label>
                终点（可行驶道路）
                <select value={goalCellId} disabled={!optionsQuery.data} onChange={(event) => { setGoalCellId(event.target.value); invalidatePlan() }}>
                  {(optionsQuery.data?.traversable_cells ?? []).filter((cell) => cell.type === 'road').map((cell) => (
                    <option key={cell.id} value={cell.id}>{cell.id} · {cell.floor} · {cell.component_id}</option>
                  ))}
                </select>
              </label>
            </>
          ) : null}

          {demoMode === 'preset' ? (
            <section className="path-demo-multi-config" aria-label="预设多车场景">
              <label>
                冲突演示场景
                <select
                  value={presetScenarioId}
                  disabled={!multiScenariosQuery.data}
                  onChange={(event) => {
                    const selected = multiScenariosQuery.data?.scenarios.find((item) => item.id === event.target.value)
                    if (!selected) return
                    const vehicles = normalizeVehicles(selected.vehicles)
                    setPresetScenarioId(selected.id)
                    setPresetVehicles(vehicles)
                    invalidatePlan()
                  }}
                >
                  {(multiScenariosQuery.data?.scenarios ?? []).map((scenario) => <option key={scenario.id} value={scenario.id}>{scenario.name}</option>)}
                </select>
              </label>
              {multiScenariosQuery.data?.scenarios.find((item) => item.id === presetScenarioId)?.description ? (
                <p className="path-demo-note">{multiScenariosQuery.data.scenarios.find((item) => item.id === presetScenarioId)?.description}</p>
              ) : null}
            </section>
          ) : null}

          {demoMode === 'custom' ? (
            <section className="path-demo-multi-config" aria-label="自定义多车配置">
              <div className="path-demo-config-toolbar">
                <strong>车辆配置（{customVehicles.length}/6）</strong>
                <button
                  className="ghost-button"
                  type="button"
                  disabled={customVehicles.length >= 6 || !roadCells.length}
                  onClick={() => {
                    const vehicle = createCustomVehicle(customVehicles, roadCells)
                    if (!vehicle) return
                    setCustomVehicles((current) => [...current, vehicle])
                    setSelectedVehicleId(vehicle.vehicle_id)
                    invalidatePlan()
                  }}
                >
                  添加车辆
                </button>
              </div>
              <p className="path-demo-note">点击“图选起点/终点”后单击道路格；拖动仍用于旋转视角。</p>
              {customVehicles.map((vehicle) => (
                <fieldset
                  key={vehicle.vehicle_id}
                  className={`path-demo-vehicle-config ${selectedVehicleId === vehicle.vehicle_id ? 'is-selected' : ''}`}
                  onFocus={() => setSelectedVehicleId(vehicle.vehicle_id)}
                >
                  <legend>
                    <button className="path-demo-vehicle-selector" type="button" onClick={() => setSelectedVehicleId(vehicle.vehicle_id)}>
                      <span className="path-demo-color-dot" style={{ backgroundColor: vehicle.display_color ?? undefined }} />
                      {vehicle.vehicle_id}
                    </button>
                  </legend>
                  <label>车辆 ID<input value={vehicle.vehicle_id} readOnly /></label>
                  <label>车辆类型<input value="CTU" readOnly /></label>
                  <label>
                    优先级
                    <input
                      aria-label={`${vehicle.vehicle_id} 优先级`}
                      type="number"
                      min={1}
                      max={100}
                      value={vehicle.priority}
                      onChange={(event) => {
                        const priority = Math.max(1, Math.min(100, Number(event.target.value) || 1))
                        setCustomVehicles((current) => current.map((item) => item.vehicle_id === vehicle.vehicle_id ? { ...item, priority } : item))
                        invalidatePlan()
                      }}
                    />
                  </label>
                  <label>
                    {vehicle.vehicle_id} 起点
                    <select value={vehicle.start_cell_id} onChange={(event) => setCustomEndpoint(vehicle.vehicle_id, 'start', event.target.value)}>
                      <option value="">请选择</option>
                      {roadCells.map((cell) => <option key={cell.id} value={cell.id}>{cell.id}</option>)}
                    </select>
                  </label>
                  <button
                    className={roadSelection?.vehicleId === vehicle.vehicle_id && roadSelection.kind === 'start' ? 'accent-button' : 'ghost-button'}
                    type="button"
                    onClick={() => { setSelectedVehicleId(vehicle.vehicle_id); setRoadSelection({ vehicleId: vehicle.vehicle_id, kind: 'start' }); setSelectionError('') }}
                  >图选起点</button>
                  <label>
                    {vehicle.vehicle_id} 终点
                    <select value={vehicle.goal_cell_id} onChange={(event) => setCustomEndpoint(vehicle.vehicle_id, 'goal', event.target.value)}>
                      <option value="">请选择</option>
                      {roadCells.map((cell) => <option key={cell.id} value={cell.id}>{cell.id}</option>)}
                    </select>
                  </label>
                  <button
                    className={roadSelection?.vehicleId === vehicle.vehicle_id && roadSelection.kind === 'goal' ? 'accent-button' : 'ghost-button'}
                    type="button"
                    onClick={() => { setSelectedVehicleId(vehicle.vehicle_id); setRoadSelection({ vehicleId: vehicle.vehicle_id, kind: 'goal' }); setSelectionError('') }}
                  >图选终点</button>
                  <label>
                    显示颜色
                    <input
                      aria-label={`${vehicle.vehicle_id} 显示颜色`}
                      type="color"
                      value={vehicle.display_color ?? '#e2e8f0'}
                      onChange={(event) => {
                        setCustomVehicles((current) => current.map((item) => item.vehicle_id === vehicle.vehicle_id ? { ...item, display_color: event.target.value } : item))
                        invalidatePlan()
                      }}
                    />
                  </label>
                  <div className="path-demo-config-actions">
                    <button className="ghost-button" type="button" onClick={() => setCustomEndpoint(vehicle.vehicle_id, 'start', '')}>清除起点</button>
                    <button className="ghost-button" type="button" onClick={() => setCustomEndpoint(vehicle.vehicle_id, 'goal', '')}>清除终点</button>
                    <button className="ghost-button danger" type="button" disabled={customVehicles.length <= 1} onClick={() => removeCustomVehicle(vehicle.vehicle_id)}>删除车辆</button>
                  </div>
                </fieldset>
              ))}
              {roadSelection ? (
                <div className="path-demo-selection-mode" role="status">
                  正在为 {roadSelection.vehicleId} 选择{roadSelection.kind === 'start' ? '起点' : '终点'}
                  <button className="ghost-button" type="button" onClick={() => setRoadSelection(null)}>退出地图选择</button>
                </div>
              ) : null}
              {selectionError ? <p className="path-demo-error" role="alert">{selectionError}</p> : null}
              {activeConfigurationError ? <p className="path-demo-error">{activeConfigurationError}</p> : null}
            </section>
          ) : null}

          <button
            className="accent-button path-demo-plan-button"
            type="button"
            disabled={planningStatus === 'planning' || optionsQuery.isFetching || Boolean(activeConfigurationError) || (demoMode === 'single' && (!startCellId || !goalCellId))}
            onClick={() => void requestPlan()}
          >
            {planningStatus === 'planning' ? '规划中…' : demoMode === 'single' ? '规划路径' : '开始多车规划'}
          </button>

          {optionsQuery.isLoading ? <p className="path-demo-note">正在分析可行驶连通分量…</p> : null}
          {optionsError ? <p className="path-demo-error">{optionsError}</p> : null}
          {scenariosError ? <p className="path-demo-error">{scenariosError}</p> : null}
          {planningError ? <p className="path-demo-error" role="alert">{planningError}</p> : null}

          <PathPlaybackController
            state={playbackState}
            canPlay={canPlay}
            onStart={startPlayback}
            onPause={pausePlayback}
            onResume={resumePlayback}
            onReset={resetPlayback}
            onRateChange={(rate) => {
              setPreferredPlaybackRate(rate)
              setPlaybackRate(rate)
            }}
          />
          {showNodeConflictVisualization && selectedConflictEvent ? (
            <div className="path-demo-auto-slow">
              <label>
                <input
                  type="checkbox"
                  checked={autoSlowConflict}
                  onChange={(event) => setAutoSlowConflict(event.target.checked)}
                />
                竞争事件自动慢放
              </label>
              <p>事件前一个时间片至等待结束后一个时间片使用 0.25x，随后恢复用户选择的 {preferredPlaybackRate}x；规划时间和等待指标不变。</p>
            </div>
          ) : null}
          <section className="path-demo-camera-controls" aria-label="视角控制">
            <div className="path-demo-camera-heading">
              <strong>视角控制</strong>
              <span className={`path-demo-view-badge is-${cameraViewMode}`}>当前视角：{cameraViewMode === 'follow' ? '跟随' : '自由'}</span>
            </div>
            {demoMode !== 'single' ? (
              <label>
                选择跟随车辆
                <select
                  value={selectedVehicleId}
                  disabled={!activeVehicles.length}
                  onChange={(event) => {
                    setSelectedVehicleId(event.target.value)
                    requestCameraAction(cameraViewMode === 'follow' ? 'switch-follow-target' : 'locate')
                  }}
                >
                  {activeVehicles.map((vehicle) => <option key={vehicle.vehicle_id} value={vehicle.vehicle_id}>{vehicle.vehicle_id}</option>)}
                </select>
              </label>
            ) : null}
            <div className="path-demo-camera-buttons">
              <button className="ghost-button" type="button" disabled={!selectedVehicleSample} onClick={() => { setCameraViewMode('free'); requestCameraAction('locate') }}>定位小车</button>
              <button
                className={cameraViewMode === 'follow' ? 'accent-button' : 'ghost-button'}
                type="button"
                disabled={!hasFollowableTrajectory}
                aria-pressed={cameraViewMode === 'follow'}
                onClick={() => setCameraViewMode((current) => current === 'follow' ? 'free' : 'follow')}
              >跟随小车：{cameraViewMode === 'follow' ? '开' : '关'}</button>
              <button className="ghost-button" type="button" disabled={cameraViewMode === 'free'} onClick={() => setCameraViewMode('free')}>自由视角</button>
              <button className="ghost-button" type="button" onClick={() => { setCameraViewMode('free'); requestCameraAction('overview') }}>仓库全景</button>
              {showNodeConflictVisualization ? (
                <button
                  className="ghost-button path-demo-conflict-camera"
                  type="button"
                  disabled={!selectedConflictEvent}
                  onClick={() => selectedConflictEvent && locateConflictEvent(selectedConflictEvent, false)}
                >查看竞争节点</button>
              ) : null}
            </div>
            <p className="path-demo-note">当前车辆：{demoMode === 'single' ? 'CTU-01' : selectedVehicleId}</p>
            {demoMode === 'custom' && !selectedPlannedVehicleSample && configuredStartPreview ? (
              <p className="path-demo-note">当前车辆尚未规划路径，可定位到已配置起点。</p>
            ) : null}
            {demoMode === 'custom' && !selectedPlannedVehicleSample && !configuredStartPreview ? (
              <p className="path-demo-note">当前车辆尚未配置合法起点，无法定位。</p>
            ) : null}
            {demoMode !== 'custom' && !selectedVehicleSample ? (
              <p className="path-demo-note">请先规划路径，生成车辆位置后再定位或跟随。</p>
            ) : null}
          </section>

          <dl className="path-demo-stats">
            <div><dt>规划状态</dt><dd>{planningStatusLabel[planningStatus]}</dd></div>
            <div><dt>连通分量</dt><dd>{optionsQuery.data?.components.length ?? '—'}</dd></div>
            <div><dt>最大分量</dt><dd>{optionsQuery.data?.components[0]?.cell_count ?? '—'} 格</dd></div>
            {demoMode !== 'single' ? (
              <>
                <div><dt>车辆数量</dt><dd>{multiPlan?.vehicles.length ?? activeVehicles.length}</dd></div>
                <div><dt>总距离</dt><dd>{totalDistance === undefined ? '—' : `${totalDistance} 格`}</dd></div>
                <div><dt>总等待时间</dt><dd>{totalWait === undefined ? '—' : `${totalWait.toFixed(2)} s`}</dd></div>
                <div><dt>总完成时间</dt><dd>{multiPlan ? `${multiPlan.makespan_seconds.toFixed(2)} s` : '—'}</dd></div>
                <div><dt>检测节点冲突</dt><dd>{multiPlan?.conflicts.detected_node_conflicts ?? '—'}</dd></div>
                <div><dt>检测边冲突</dt><dd>{multiPlan?.conflicts.detected_edge_conflicts ?? '—'}</dd></div>
                <div><dt>最终节点冲突</dt><dd>{multiPlan?.conflicts.remaining_node_conflicts ?? '—'}</dd></div>
                <div><dt>最终边冲突</dt><dd>{multiPlan?.conflicts.remaining_edge_conflicts ?? '—'}</dd></div>
                <div><dt>最终几何冲突</dt><dd>{multiPlan?.conflicts.remaining_geometry_conflicts ?? '—'}</dd></div>
                <div><dt>避让次数</dt><dd>{multiPlan?.avoidance_count ?? '—'}</dd></div>
                <div><dt>规划耗时</dt><dd>{multiPlan ? `${multiPlan.planning_time_ms.toFixed(2)} ms` : '—'}</dd></div>
              </>
            ) : (
              <>
                <div><dt>路径长度</dt><dd>{plan ? `${plan.total_distance} 格` : '—'}</dd></div>
                <div><dt>路径节点</dt><dd>{plan?.path_cells.length ?? '—'}</dd></div>
                <div><dt>访问节点</dt><dd>{plan?.visited_nodes ?? '—'}</dd></div>
                <div><dt>规划耗时</dt><dd>{plan ? `${plan.planning_time_ms.toFixed(2)} ms` : '—'}</dd></div>
                <div><dt>车辆位置</dt><dd>{sample ? `${sample.floor} · (${sample.row.toFixed(2)}, ${sample.col.toFixed(2)})` : '—'}</dd></div>
              </>
            )}
            <div><dt>运动状态</dt><dd>{statusLabel[playbackState.status]}</dd></div>
            <div><dt>演示时间</dt><dd>{playbackState.elapsedSeconds.toFixed(2)} / {duration.toFixed(2)} s</dd></div>
          </dl>
          {demoMode !== 'single' && multiPlan ? (
            <div className="path-demo-vehicle-results" aria-label="多车规划结果">
              {multiPlan.vehicles.map((vehicle) => (
                <div key={vehicle.vehicle_id}>
                  <strong>{vehicle.vehicle_id}</strong>
                  <span>{vehicle.total_distance} 格</span>
                  <span>等待 {vehicle.wait_time_seconds.toFixed(2)} s</span>
                  <span>{liveVehicleStatusLabel(
                    playbackState.status,
                    vehicleSnapshots.find((snapshot) => snapshot.vehicleId === vehicle.vehicle_id),
                    vehicle.wait_time_seconds,
                  )}</span>
                </div>
              ))}
            </div>
          ) : null}
          {showNodeConflictVisualization && multiPlan ? (
            <ConflictAvoidancePanel
              events={nodeConflictEvents}
              selectedEventId={selectedConflictEvent?.event_id}
              onLocate={(event) => locateConflictEvent(event, true)}
            />
          ) : null}
            </>
          ) : (
            <p className="path-demo-note">正在准备四向车四层规划工作台…</p>
          )}
        </aside>

        <div className="path-demo-viewport" aria-label="CTU 3D path demonstration">
          {topologyRecord ? (
            <Suspense fallback={<div className="path-demo-empty">正在加载 3D 场景…</div>}>
              <PathDemo3DScene
                topology={topologyRecord.payload}
                activeFloor={activeFloor}
                trajectory={trajectory}
                sample={sample}
                playbackStatus={playbackState.status}
                cameraViewMode={cameraViewMode}
                cameraActionRequest={cameraActionRequest}
                vehicles={multiVehicleViews}
                followedVehicleSample={selectedVehicleSample}
                selectedVehicleId={selectedVehicleId}
                roadCells={roadCells}
                roadSelectionEnabled={demoMode === 'custom' && roadSelection !== null}
                endpointMarkers={endpointMarkers}
                onRoadCellSelect={handleRoadCellSelect}
                onInvalidRoadSelection={() => setSelectionError('该位置不是当前道路连通分量中的可行驶道路格。')}
                conflictEvent={showNodeConflictVisualization ? selectedConflictEvent : null}
              />
            </Suspense>
          ) : (
            <div
              className={`path-demo-empty path-demo-topology-state${topologyLoadFailed ? ' is-error' : ''}`}
              role={topologyLoadFailed ? 'alert' : 'status'}
            >
              {topologiesQuery.isPending ? <p>正在读取拓扑…</p> : null}
              {topologyLoadFailed ? (
                <>
                  <strong>拓扑接口连接失败</strong>
                  <p>{topologiesQuery.error instanceof Error ? topologiesQuery.error.message : String(topologiesQuery.error)}</p>
                  <button className="ghost-button" type="button" onClick={() => void topologiesQuery.refetch()}>重新加载拓扑</button>
                </>
              ) : null}
              {topologyListEmpty ? (
                <>
                  <strong>没有可用拓扑</strong>
                  <p>请先在拓扑编辑器中创建或导入仓库拓扑。</p>
                </>
              ) : null}
              {!topologiesQuery.isPending && !topologyLoadFailed && !topologyListEmpty ? <p>正在选择默认拓扑…</p> : null}
            </div>
          )}
        </div>
      </div>
      )}
    </section>
  )
}
