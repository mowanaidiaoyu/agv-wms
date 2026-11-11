export type ZoneType = 'ctu' | 'shuttle' | 'port' | 'pick_station' | 'drop_zone'
export type CellType = 'road' | 'blocked' | 'conveyor'
export type ConveyorDirection = 'right' | 'left' | 'up' | 'down'
export type ConveyorSide = 'north' | 'east' | 'south' | 'west'
export type ConveyorDeckKey = 'lower' | 'upper'
export type ConveyorDeckRole = 'outbound' | 'return' | 'custom'
export type ConveyorRunMode = 'single' | 'round_trip' | 'independent'

export interface ConveyorDeckConfig {
  enabled: boolean
  /** 主入口（兼容旧拓扑与编辑器默认值）。 */
  inputSide: ConveyorSide
  /** 合流模块允许的全部入口；缺省时等价于仅包含 inputSide。 */
  inputSides?: ConveyorSide[]
  outputSide: ConveyorSide
  /** 分流模块允许的全部出口；缺省时等价于仅包含 outputSide。 */
  outputSides?: ConveyorSide[]
  role: ConveyorDeckRole
  speed?: number
  capacity?: number
}

export interface ConveyorDecks {
  lower: ConveyorDeckConfig
  upper?: ConveyorDeckConfig
}
export type NodeType = 'storage' | 'ctu_robot' | 'shuttle_robot' | 'lift_transfer' | 'turn_transfer' | 'shuttle_dock' | 'picking_handoff' | 'picking_arm'
export type EditorObjectType =
  | 'select'
  | 'batch'
  | 'erase'
  | 'road'
  | 'blocked'
  | 'conveyor'
  | 'ground_conveyor'
  | 'ctu_robot'
  | 'shuttle_robot'
  | 'lift_transfer'
  | 'turn_transfer'
  | 'shuttle_dock'
  | 'picking_handoff'
  | 'picking_arm'
export type RobotType = 'ctu' | 'shuttle'

export interface Zone {
  id: string
  label: string
  type: ZoneType
  floor?: string
  x: number
  y: number
  width: number
  height: number
  /** 业务区域与输送线端点的固定连接；避免运行时再按最近距离猜测。 */
  conveyorBinding?: {
    blockId: string
    side: ConveyorSide
  } | null
  /** 四向车库每层巷道位掩码：`"行,列"` → bit0..bit3 对应货架层 1..4 是否为巷道 */
  slotAisles?: Record<string, number>
  /** 四向车库被删除的 2×2 槽位（`"行,列"` 列表）：该位置不再渲染货架/巷道，视为空位 */
  removedSlots?: string[]
  /** 四向车库「升降通道（电梯）」2×2 槽位（`"行,列"` 列表）：四向车跨层必须经过该竖井 */
  slotLifts?: string[]
}

export interface GridConfig {
  rows: number
  cols: number
  cellSize: number
  originX: number
  originY: number
}

export interface TopologyCell {
  id: string
  floor?: string
  row: number
  col: number
  type: CellType
  zoneId?: string | null
  cost: number
  walkable: boolean
  conveyorDirection?: ConveyorDirection | null
  /** 上下层独立的物料入口/出口。只在 2×2 块锚点格保存，其余格通过 conveyorAnchor 解析。 */
  conveyorDecks?: ConveyorDecks | null
  conveyorMode?: ConveyorRunMode | null
  /** 物理输送线路标识。相邻但不同线路的块不会因为坐标接触而被误连接。 */
  conveyorLineId?: string | null
  levels?: number
  /** 所属 2×2 输送带块的左上锚点（奇数起点支持）。缺失时回退到 floor-到-偶。 */
  conveyorAnchor?: { row: number; col: number } | null
  /** 地面输送线（整箱，四向车出货用）：贴地单层带面；缺省为架空输送线（零散件，CTU 用） */
  conveyorGround?: boolean
}

export interface TopologyNode {
  id: string
  type: NodeType
  floor?: string
  row: number
  col: number
  zoneId?: string | null
  capacity: number
  label: string
  /** 转向移载机只安装在一个输送层；下层输送带不会因上层设备而被覆盖。 */
  transferDeck?: ConveyorDeckKey | null
  /** 转向移载机在所选层的手动入口和出口方向。 */
  transferInputSide?: ConveyorSide | null
  transferOutputSide?: ConveyorSide | null
  /** 四向车出货位（shuttle_dock）：取什么货物（商品目录 skuId） */
  skuId?: string | null
  skuName?: string | null
  largeCategory?: string | null
  /** 四向车出货位：取多少（箱，每箱一趟搬运任务） */
  quantity?: number | null
}

export interface TopologyPayload {
  id: string
  name: string
  meta: {
    version: string
    viewport: { x: number; y: number; zoom: number }
    tags: string[]
    floors?: string[]
  }
  grid: GridConfig
  zones: Zone[]
  cells: TopologyCell[]
  nodes: TopologyNode[]
}

export type LayoutAuditStatus = 'PASS' | 'PARTIAL' | 'BLOCKED'

export interface LayoutPosition {
  floor: string
  row: number
  col: number
  level: number
}

export interface LayoutReferenceNode {
  referenceId: string
  kind: string
  networkType: string
  position: LayoutPosition
  sourceId: string
  validationStatus: LayoutAuditStatus
  evidence: string[]
}

export interface ProposedLayoutFacility {
  facilityId: string
  kind: string
  networkType: string
  position: LayoutPosition
  spansLevels: number[]
  status: 'proposed'
  validationStatus: LayoutAuditStatus
  capacity: number
  purpose: string
  existingRefIds: string[]
  occupancyPolicy: 'exclusive' | 'co_located_with_reference'
  conflicts: string[]
  evidence: string[]
}

export interface ProposedLayoutConnection {
  connectionId: string
  fromId: string
  toId: string
  networkType: string
  edgeType: string
  direction: string
  points: LayoutPosition[]
  status: LayoutAuditStatus
  capacity: number
  handoffAction?: string | null
  heightTransition?: Record<string, unknown> | null
  requiredCellIds: string[]
  conflicts: string[]
  evidence: string[]
}

export interface LayoutRoute {
  routeId: string
  side: 'left' | 'right' | 'shared'
  optionId: string
  status: LayoutAuditStatus
  facilityIds: string[]
  connectionIds: string[]
  blockingReasons: string[]
}

export interface LayoutOption {
  optionId: string
  label: string
  status: LayoutAuditStatus
  description: string
  facilityIds: string[]
  connectionIds: string[]
  assumptions: string[]
  blockingReasons: string[]
}

export interface PickingArmEndpoint {
  nodeId: string
  topologyCellId?: string | null
  position: LayoutPosition
  beltHeight: number
  deckIndex?: number | null
  direction?: string | null
  provenance: 'topology' | 'proposed'
  validationStatus: LayoutAuditStatus
  evidence: string[]
}

export interface PickingArmConveyorStep {
  cellId: string
  position: LayoutPosition
  direction: string
  beltHeight: number
  deviceIds: string[]
  transitionToNext: 'conveyor' | 'device_transfer' | 'proposed_direction' | 'endpoint'
  transferDeviceId?: string | null
  directionSource?: 'topology' | 'layout_plan'
}

export interface PickingArmKinematics {
  upperArmLength: number
  forearmLength: number
  shoulderHeight: number
  baseRadius: number
  upperArmThickness: number
  forearmThickness: number
  jointRadius: number
  gripperScale: number
  safeTravelHeight: number
  linkCollisionRadius: number
}

export interface PickingArmLayout {
  armId: string
  status: 'proposed'
  validationStatus: LayoutAuditStatus
  installation: LayoutPosition
  mountHeight: number
  workRadius: number
  kinematics: PickingArmKinematics
  pickup: PickingArmEndpoint
  dropoff: PickingArmEndpoint
  liftBId: string
  liftBPosition: LayoutPosition
  liftBClearanceCells: number
  downstreamCellId: string
  downstreamStatus: LayoutAuditStatus
  previewConveyorPath: PickingArmConveyorStep[]
  previewEndpointId: string
  previewPathStatus: LayoutAuditStatus
  previewStopReason: string
  previewTransferDeviceId?: string | null
  previewTransferStatus?: LayoutAuditStatus | null
  previewTransferEvidence: string[]
  actionSequence: string[]
  requiredAuthoritativeSignals: string[]
  canDeclareDelivery: boolean
  evidence: string[]
}

export interface TransportLayoutPlan {
  planId: string
  schemaVersion: string
  topologyId: string
  topologyRevision: string
  overallStatus: LayoutAuditStatus
  recommendedOptionId: string
  pickingArm?: PickingArmLayout | null
  references: LayoutReferenceNode[]
  facilities: ProposedLayoutFacility[]
  connections: ProposedLayoutConnection[]
  routes: LayoutRoute[]
  options: LayoutOption[]
  issues: Array<{
    code: string
    severity: 'INFO' | 'WARNING' | 'ERROR'
    sourceId?: string | null
    targetId?: string | null
    reason: string
    evidence: string[]
  }>
  summary: Record<string, unknown>
  assumptions: string[]
}

export interface StoredRecord<T> {
  id: string
  name: string
  payload: T
  created_at?: string
  updated_at?: string
}

export interface SimulationMetrics {
  throughput: number
  completedTasks: number
  pendingTasks: number
  activeRobots: number
  blockedRobots: number
  conveyorUtilization: number
  /** 0 means no active conveyor graph; missing means the field was not collected. */
  conveyorGraphNodes?: number
  deadlocksDetected: number
  congestionEvents: number
  urgentOrdersHandled: number
  avgEtaTicks: number
  avgCompletionTicks: number
  stationQueueDepth: number
  conveyorQueueDepth: number
  bufferOccupancy: number
}

export type MetricImplementationStatus = 'IMPLEMENTED' | 'PARTIAL' | 'UNAVAILABLE'
export type MetricDataQuality = 'GOOD' | 'DEGRADED' | 'MISSING_SOURCE' | 'SUCCESS_ONLY_SAMPLE' | 'INVALID'

export interface ContractMetric<T = unknown> {
  metricId: string
  displayName?: string
  value: T | null
  unit: string
  sampleCount: number | null
  implementationStatus: MetricImplementationStatus
  dataQuality: MetricDataQuality | string
  reason: string | null
  acceptanceEligible: boolean
  [key: string]: unknown
}

export interface TaskMetricSummary {
  runId: string
  metrics: ContractMetric[]
}

export interface MetricHistoryPoint extends Partial<SimulationMetrics> {
  tick: number
  dataQuality?: MetricDataQuality | string
  reason?: string | null
  missingFields?: string[]
  invalidFields?: string[]
}

export interface MetricHistoryStatistic {
  description: string
  unit: string
  minimum: number | null
  maximum: number | null
  maximumTick: number | null
  average: number | null
  validSampleCount: number
  missingSampleCount: number
  invalidSampleCount: number
  dataQuality: MetricDataQuality | string
  reason: string | null
  acceptanceEligible: boolean
}

export interface MetricHistoryQuality {
  runId: string
  dataQuality: MetricDataQuality | string
  reason: string | null
  collectionCompleteness: 'UNCONFIRMED' | 'MISSING' | string
  qualityIssues: string[]
  rawSampleCount: number
  uniqueTickCount: number
  duplicateSnapshotCount: number
  outputSampleCount: number
  invalidSnapshotCount: number
  points: MetricHistoryPoint[]
  statistics: Record<string, MetricHistoryStatistic>
  acceptanceEligible: boolean
}

export interface AssignmentContentionMetrics {
  runId: string
  eventCollectionCompleteness: string
  dataQuality: MetricDataQuality | string
  reason: string | null
  metrics: ContractMetric[]
  quality: {
    ctuParentTaskCount: number | null
    invalidTaskRecordCount: number
    conflictingTaskIdCount: number
    validAssignmentEventCount: number
    invalidAssignmentEventCount: number
    ignoredAssignedStateEventCount: number
    outOfScopeAssignmentEventCount: number
    validDeadlockEventCount: number
    invalidDeadlockEventCount: number
    validYieldEventCount: number
    invalidYieldEventCount: number
  }
  acceptanceEligible: boolean
}

export interface PathMetricGroup {
  runId: string
  algorithm: 'astar' | 'dijkstra'
  topologyRevision: string | null
  configRevision: string | null
  coordinateContractRevision: string | null
  sampleKind: string
}

export interface PathAggregateGroup {
  group: PathMetricGroup
  minimum: number
  maximum: number
  average: number
  validSampleCount: number
  samplePreview: Array<{ taskId: string; value: number }>
  previewSampleCount: number
  dataQuality: MetricDataQuality | string
  reason: string | null
}

export interface PathQuantileValue {
  value: number | null
  unit: 'ms' | string
  requiredSampleCount: number
  actualSampleCount: number
  reason: string | null
}

export interface PathQuantileGroup {
  group: PathMetricGroup
  sampleCount: number
  quantiles: {
    p50: PathQuantileValue
    p90: PathQuantileValue
    p99: PathQuantileValue
  }
  implementationStatus: MetricImplementationStatus
  dataQuality: MetricDataQuality | string
  reason: string | null
  acceptanceEligible: boolean
}

export interface PathPlanningMetrics {
  runId: string
  sampleKind: string
  dataQuality: MetricDataQuality | string
  reason: string | null
  metrics: ContractMetric[]
  quality: Record<string, number | null>
  acceptanceEligible: boolean
}

export interface ConveyorCoordinationMetrics {
  runId: string
  scope: string
  dataQuality: MetricDataQuality | string
  reason: string | null
  metrics: ContractMetric[]
  historyTrendSource: string
  acceptanceEligible: boolean
}

export interface DeadlockLifecycleMetric<T = number> {
  metricId: string
  displayName: string
  value: T | null
  unit: string
  sampleCount: number
  dataQuality: MetricDataQuality | string
  reason: string | null
  acceptanceEligible: boolean
  source: string
  generation: number | null
  snapshotAsOfTick: number | null
}

export interface DeadlockRobotBlocking {
  robotId: string
  blockerRobotId: string | null
  blockedCellId: string | null
  nextCellId: string | null
  metrics: DeadlockLifecycleMetric[]
  dataQuality: MetricDataQuality | string
  reason: string | null
  acceptanceEligible: boolean
  source: string
  generation: number | null
  snapshotAsOfTick: number | null
}

export interface DeadlockHotspot {
  cellId: string
  row: number | null
  col: number | null
  floor: string | null
  observedEpisodeCount: number
  unit: string
  involvedRobotCount: number
  sampleCount: number
  dataQuality: MetricDataQuality | string
  reason: string | null
  acceptanceEligible: boolean
  source: string
  generation: number | null
  snapshotAsOfTick: number | null
}

export interface DeadlockRecentEpisode {
  episodeId: string
  status: string | null
  firstObservedTick: number | null
  persistentAtTick: number | null
  resolvedAtTick: number | null
  lastObservedTick: number | null
  participantRobotIds: string[]
  involvedCellIds: string[]
  dataQuality: MetricDataQuality | string | null
  source: string
  generation: number | null
  snapshotAsOfTick: number | null
}

export interface DeadlockEventPersistenceWatermark {
  databaseEventId: number | null
  databaseEventTick: number | null
  lastTransitionKey: string | null
  uniqueTransitionCount: number
  duplicateTransitionCount: number
  conflictingTransitionCount: number
  invalidEventCount: number
  snapshotLastAcknowledgedTransitionKey: string | null
  snapshotPendingTransitionCount: number | null
}

export interface DeadlockLifecycleMetrics {
  contractVersion: 'deadlock-lifecycle-metrics-v1' | string
  runId: string
  generation: number | null
  availableGenerations: number[]
  runStatus: string
  stopReason: string | null
  snapshotAsOfTick: number | null
  observationCompleteness: string
  dataQuality: MetricDataQuality | string
  reason: string | null
  qualityIssues: string[]
  acceptanceEligible: boolean
  eventPersistenceWatermark: DeadlockEventPersistenceWatermark
  metrics: DeadlockLifecycleMetric[]
  robotBlocking: DeadlockRobotBlocking[]
  hotspots: DeadlockHotspot[]
  recentEpisodes: DeadlockRecentEpisode[]
  limits: {
    hotspotLimit: number
    recentEpisodeLimit: number
    fullEpisodeHistoryReturned: boolean
  }
  semantics: {
    persistentCycleMeaning: string
    durationFormula: string
    blockedRobotTicksUnit: string
    thresholdCalibration: string
    legacyDeadlockDetectedUsed: boolean
  }
}

export interface MotionPhysicsContractInput {
  physicsContractVersion: string
  coordinateContractVersion: string
  metersPerCell: number
  shuttleLevelHeightMeters: number
  floorHeightMeters: number
  source?: 'USER_DEFINED_SIMULATION'
  calibrationStatus?: 'UNVERIFIED'
}

export interface MotionDistanceMetricValue {
  value: number | null
  unit: string
  sampleCount: number
  dataQuality: MetricDataQuality | string
  reason: string | null
  source: string
}

export interface MotionDistanceMovementTypeMetric {
  movementCount: number
  horizontalMeters: number | null
  verticalMeters: number | null
  unit: string
  dataQuality: MetricDataQuality | string
  source: string
}

export interface MotionDistanceRobotMetric {
  robotId: string
  robotType: string
  horizontalMeters: number | null
  verticalMeters: number | null
  movementCount: number
  yieldMeters: number | null
  backoffMeters: number | null
  returnToStandbyMeters: number | null
  observationCompleteness: string
  dataQuality: MetricDataQuality | string
}

export interface MotionDistanceMetrics {
  contractVersion: 'motion-distance-metrics-v1' | string
  runId: string
  generation: number | null
  runInstanceId: string | null
  physicsContractVersion: string | null
  metersPerCell: number | null
  physicsContractSource?: 'USER_DEFINED_SIMULATION' | null
  calibrationStatus?: 'UNVERIFIED' | null
  snapshotAsOfTick: number | null
  eventPersistenceWatermark: {
    movementId: string
    tick: number
    eventSequence: number
  } | null
  totalMetrics: Record<string, MotionDistanceMetricValue>
  vehicleTypeMetrics: Record<string, {
    horizontalMeters: MotionDistanceMetricValue
    movementCount: number
  }>
  movementTypeMetrics: Record<string, MotionDistanceMovementTypeMetric>
  perRobotMetrics: MotionDistanceRobotMetric[]
  perRobotMetricsLimit: number
  totalRobotCount: number
  perRobotMetricsTruncated: boolean
  evidenceSummary: Record<string, unknown>
  plannedHorizontalMeters: number | null
  extraDistanceMeters: number | null
  attributableReplanExtraMeters: number | null
  algorithmDistanceImprovementRate: number | null
  observationCompleteness: string
  dataQuality: MetricDataQuality | string
  reason: string | null
  acceptanceEligible: boolean
  semantics: Record<string, unknown>
}

export interface SyntheticExperimentBase {
  experimentId: string
  datasetVersion: 'analytics-synthetic-v1' | string
  seed: number
  dataSource: 'SYNTHETIC'
  inputDatasetHash: string
  validationStatus: string
  limitations: string[]
}

export interface SyntheticAbcItem {
  skuId: string
  abcClass: 'A' | 'B' | 'C'
  historicalDemandValue: number
  historicalDemandUnits: number
  contributionRate: number
  cumulativeContributionRate: number
  lookbackDays: number
  asOfDayIndex: number
  classificationVersion: string
}

export interface SyntheticAbcSnapshot {
  asOfDayIndex: number
  lookbackDays: number
  observedDayRange: { startDayIndex: number; endDayIndex: number }
  classCounts: Record<'A' | 'B' | 'C', number>
  classHistoricalDemandValueShare: Record<'A' | 'B' | 'C', number>
  changeFromPreviousSnapshot: {
    changedSkuCount: number | null
    reason: string | null
    transitionCounts: Record<string, number>
  }
  items: SyntheticAbcItem[]
}

export interface SyntheticForecastEvaluation {
  backtestStrategy: string
  evaluationWindow: { days: number; startDayIndex: number; endDayIndex: number }
  forecastMethod: string
  mae: number | null
  mape: number | null
  mapeReason: string | null
  mapeSampleCount: number
  predictionHorizon: string
  totalPredictionCount: number
  validPredictionCount: number
  zeroActualExcludedCount: number
  excludedSampleCount: number
}

export interface SyntheticAbcHeatResult extends SyntheticExperimentBase {
  algorithmVersion: string
  sampleCount: number
  abcSnapshots: SyntheticAbcSnapshot[]
  latestAbcSummary: {
    asOfDayIndex: number
    classCounts: Record<'A' | 'B' | 'C', number>
    classHistoricalDemandValueShare: Record<'A' | 'B' | 'C', number>
    topSkus: SyntheticAbcItem[]
  }
  skuForecasts: Array<{
    skuId: string
    predictions: Array<{
      dayIndex: number
      date: string
      actual: number
      sma7: number
      wma7: number
      historyDayIndices: number[]
      backtestStrategy: string
      predictionHorizon: string
    }>
  }>
  forecastEvaluation: {
    SMA7: SyntheticForecastEvaluation
    WMA7: SyntheticForecastEvaluation
    comparison: {
      lowerMaeMethod: string | null
      maeBaseline: string
      sameEvaluationSamples: boolean
      wma7RelativeMaeImprovementPercent: number | null
    }
  }
}

export interface SyntheticWaveDetail {
  waveId: string
  algorithm: string
  priority: string
  orderCount: number
  lineCount: number
  totalQuantity: number
  distanceCells: number
  ordersCapacityUsage: number
  linesCapacityUsage: number
  quantityCapacityUsage: number
  capacityValidation: string
}

export interface SyntheticWaveSummary {
  algorithm: string
  status: string
  waveCount: number
  totalDistanceCells: number
  capacityUtilization: {
    ordersCapacityUsage: number
    linesCapacityUsage: number
    quantityCapacityUsage: number
    unit: string
  }
  dataQuality: {
    inputOrderCount: number
    inputLineCount: number
    inputQuantity: number
    assignedOrderCount: number
    assignedLineCount: number
    assignedQuantity: number
    orderConservation: boolean
    lineConservation: boolean
    quantityConservation: boolean
    duplicateAssignmentCount: number
    unassignedOrderCount: number
  }
}

export interface SyntheticWaveOptimizationResult extends SyntheticExperimentBase {
  algorithmVersion: string
  distanceMetric: {
    name: string
    unit: string
    routeRule: string
    acceptanceEligibleAsRealWarehousePath: boolean
  }
  baseline: SyntheticWaveSummary
  optimized: SyntheticWaveSummary
  comparison: {
    baselineDistanceCells: number
    optimizedDistanceCells: number
    baselineWaveCount: number
    optimizedWaveCount: number
    distanceImprovementRate: number | null
    reason: string | null
    unit: string
  }
  dataQuality: {
    input: {
      orderCount: number
      lineCount: number
      quantityCount: number
      validationStatus: string
    }
    formalAcceptanceEligible: boolean
    sameDistanceCalculator: boolean
    sameInputOrders: boolean
  }
  waveDetails: {
    baseline: SyntheticWaveDetail[]
    optimized: SyntheticWaveDetail[]
  }
}

export interface SyntheticEtaEvaluation {
  sampleCount: number
  maeTicks: number | null
  mapePercent: number | null
  mapeSampleCount: number
  mapeReason: string | null
  zeroActualExcludedCount: number
  p90AbsoluteErrorTicks: number | null
  p90Reason: string | null
  p90RequiredSampleCount: number
  p90Method: string
}

export interface SyntheticEtaPredictionResult extends SyntheticExperimentBase {
  modelVersion: string
  trainSampleCount: number
  fitSampleCount: number
  testSampleCount: number
  featureNames: string[]
  trainingCutoff: {
    policy: string
    comparison: string
    firstTestCreatedTick: number
    nominalTrainSampleCount: number
    fitSampleCount: number
    excludedUnavailableTargetCount: number
    excludedTaskIds: string[]
    maxFitCompletedTick: number
    maxNominalTrainCompletedTick: number
    temporalLeakagePrevented: boolean
    deployabilityStatus: string
  }
  baselineModel: {
    modelVersion: string
    trainingSampleCount: number
    taskTypeMedians: Record<string, number>
    globalMedian: number
  }
  ridgeModel: {
    modelVersion: string
    trainingSampleCount: number
    ridgeLambda: number
    interceptPenalized: boolean
    solver: string
    negativePredictionClampedCount: number
    unknownCategorySampleCount: number
  }
  overallEvaluation: {
    baseline: SyntheticEtaEvaluation
    ridge: SyntheticEtaEvaluation
    maeImprovementPercent: number | null
    maeImprovementReason: string | null
    evaluationSampleIdentity: string
    durationUnit: string
  }
  taskTypeEvaluation: Record<string, {
    sampleCount: number
    baselineMAE: number | null
    modelMAE: number | null
    baselineMAPE: number | null
    modelMAPE: number | null
    baselineP90AbsoluteErrorTicks: number | null
    modelP90AbsoluteErrorTicks: number | null
    baselineP90Reason: string | null
    modelP90Reason: string | null
    zeroActualExcludedCount: number
    dataSource: 'SYNTHETIC'
  }>
  predictions: Array<{
    taskId: string
    taskType: string
    createdTick: number
    actualDurationTicks: number
    baselinePredictedTicks: number
    modelPredictedTicks: number
    baselineAbsoluteErrorTicks: number
    modelAbsoluteErrorTicks: number
    distanceCells: number
    loadQuantity: number
    queueLength: number
    congestionLevel: number
    assignedRobotType: string
    dataSource: 'SYNTHETIC'
  }>
  fallbackSampleCount: number
}


export interface SimulationRobot {
  id: string
  type: RobotType
  currentNode: string
  currentCellId: string
  currentFloor?: string
  currentRow: number
  currentCol: number
  /** 四向车当前所在货架层（1..4），CTU 恒为 1 */
  currentLayer?: number
  status: string
  battery: number
  path: string[]
  /** 与 path 等长的每格所在货架层 */
  pathLayers?: number[]
  pathStages?: string[]
  targetCellId: string
  targetLayer?: number
  currentTaskId: string | null
  waitTicks: number
  lastCellChangeTick?: number
  blockedByRobotId?: string | null
  deadlockGroupId?: string | null
  recoveryCount?: number
  activePortId?: string | null
  failed: boolean
  phaseLabel: string
  load: number
  capacity: number
  /** CTU 当前装载的货物箱（取货后从库位移到车上，交付后清空） */
  carrying?: GoodsSlot[]
  /** CTU 到达库位/出入口后的升降、伸叉和装箱动作；仅动作期间存在。 */
  handlingAction?: {
    type: 'pickup' | 'deliver'
    stage: 'lifting' | 'extending' | 'stowing'
    targetLayer: number
    slotIndex: number
    nodeId?: string
    cellId?: string
    approachDirection?: 'north' | 'south' | 'east' | 'west'
    skuId?: string
    skuName?: string
    verified?: boolean
  } | null
}

export interface SimulationTaskHistory {
  tick: number
  phase: string
  status: string
}

export interface SimulationTaskLine {
  nodeId: string
  cellId: string
  layer: number
  skuId?: string
  skuName?: string
  largeCategory?: string
  subclass?: string
  spec?: string
  grade?: string
  material?: string
  surfaceTreatment?: string
  quantity?: number
}

/** 四向车出货位运行时设置（可在「四向车搬运」工作台调整，存在 Run 状态里，不改拓扑）。 */
export interface ShuttleDockState {
  dockId: string
  label: string
  row: number
  col: number
  floor: string
  skuId?: string | null
  skuName?: string | null
  largeCategory?: string | null
  quantity: number
  priority: 'normal' | 'urgent'
  vehicleId?: string | null
  loop: boolean
  enabled: boolean
  cursor?: number
}

export interface ShuttleTrackNode {
  topology_id: string
  zone_id: string
  track_node_id: string
  shuttle_level: 1 | 2 | 3 | 4
  row: number
  col: number
  world_x: number
  world_y: number
  world_z: number
  track_type: 'straight' | 'intersection' | 'turn' | 'lift_entry' | 'lift_exit'
  traversable: boolean
  neighbors: string[]
}

export interface ShuttleLiftTransition {
  lift_id: string
  from_level: number
  to_level: number
  entrance_track_node: string
  exit_track_node: string
  enter_at_seconds: number
  lift_start_seconds: number
  arrive_level_seconds: number
  exit_at_seconds: number
  events: string[]
}

export interface ShuttlePlan {
  topologyId: string
  topologyRevision: string
  configRevision: string
  coordinateContractRevision: string
  algorithm: 'astar' | 'dijkstra'
  chosen_lift: string | null
  path_nodes: Array<ShuttleTrackNode & { arrival_seconds: number; phase: string }>
  path_edges: Array<{
    from_track_node: string
    to_track_node: string
    edge_type: 'track' | 'lift'
    lift_id?: string | null
    duration_seconds: number
  }>
  lift_transitions: ShuttleLiftTransition[]
  estimated_duration_seconds: number
  horizontal_distance_cells?: number
  planning_time_ms?: number
  visited_states?: number
  result_status: 'success' | 'failed'
  failure_reason?: string | null
  timing_source: 'synthetic_demo'
}

export interface ShuttleContract {
  topologyId: string
  topologyRevision: string
  configRevision: string
  coordinateContractRevision: string
  verificationStatus: string
  planningEnabled: boolean
  tracks: ShuttleTrackNode[]
  lifts: Array<{
    lift_id: string
    label: string
    row: number
    col: number
    supported_levels?: number[]
    verification_status: string
    timing_source: 'synthetic_demo'
  }>
  warnings: string[]
}

export interface ShuttleTransferInput {
  id: string
  vehicleId: string
  skuId: string
  skuName: string
  sourceLocationId: string
  destinationLocationId: string
  sourceTrackNode: string
  destinationTrackNode: string
  sourceLevel: 1 | 2 | 3 | 4
  destinationLevel: 1 | 2 | 3 | 4
  quantity: number
  algorithm: 'astar' | 'dijkstra'
  initialSourceQuantity: number
  initialDestinationQuantity: number
}

export interface TransferInventoryBalance {
  locationId: string
  trackNodeId: string
  level: number
  row: number
  col: number
  worldX: number
  worldY: number
  worldZ: number
  skuId: string
  skuName: string
  quantityOnHand: number
  quantityReserved: number
  quantityAvailable: number
  dataSource: 'run_isolated_simulation_snapshot'
}

export interface TransferInventoryEvent {
  eventId: string
  taskId: string
  tick: number
  eventType: 'reserve' | 'pick' | 'drop' | 'cancel_release' | 'cancel_return'
  locationId: string | null
  skuId: string
  quantity: number
  onHandDelta: number
  reservedDelta: number
  inTransitDelta: number
  quantityOnHandAfter?: number | null
  quantityReservedAfter?: number | null
  inTransitAfter?: number
}

export interface SimulationTask {
  id: string
  orderId?: string | null
  dockId?: string | null
  requestedVehicleId?: string | null
  robotType: RobotType
  /** 任务种类：picking_box=单箱拣货子任务，picking_return=货箱回库子任务，placed_goods_delivery=已拣部分出货子任务 */
  taskKind?: string
  /** 父任务 id（拣货分裂产生的子任务才有） */
  parentId?: string | null
  /** 父任务已把货箱委托给单箱拣货子任务（前端跳过整单放置区渲染） */
  pickingDelegated?: boolean
  /** 拣货台所在输送块（如 1F:88:66，供分裂子任务定位起点） */
  stationBlockId?: string | null
  /** 升降注入块（如 1F:94:90，conveyor_to_lift 终点） */
  liftBlockId?: string | null
  /** 回库口输送块（conveyor_return/回库支线终点） */
  returnEndBlockId?: string | null
  mode: 'direct' | 'cross_zone' | 'shuttle_transfer'
  status: string
  phase: string
  phaseLabel: string
  sourceCellId: string
  sourceLayer: number
  /** 原始库位节点（货物拣出前的 nodeId，回库时放回同一库位） */
  sourceNodeId?: string | null
  targetCellId: string
  targetLayer: number
  /** 交付点（输送线出入口入口格），等于任务最终 targetCellId */
  deliveryCellId: string
  lines: SimulationTaskLine[]
  /** 实际交付到输送线上的货箱（用于按实载箱数渲染） */
  deliveredGoods?: SimulationTaskLine[]
  /** 机械臂放箱位置，以及货箱是否已到达独立上层输送路线终点。 */
  armPlacedCellId?: string | null
  armConveyorCompleted?: boolean
  armConveyorEndCellId?: string | null
  /** 转运中的货箱（人工拣货流程） */
  transitGoods?: SimulationTaskLine[]
  currentLineIndex: number
  picked: number
  priority: number
  etaTicks: number
  urgent: boolean
  assignedRobotId: string | null
  taskIndex?: number
  splitStrategy?: SimulationOrderDispatchMode
  sourceGroupLabel?: string
  assignedPortId?: string
  assignedPortLabel?: string
  portQueuePosition?: number
  portSelectionCost?: number
  portSelectionReason?: string
  lastProgressTick?: number
  deadlockGroupId?: string | null
  recoveryCount?: number
  remainingTicks: number
  /** 人工拣货位入口格（拓扑有 pick_station 时才有） */
  pickStationCellId?: string | null
  pickStationZoneId?: string | null
  /** 放置区入口格（拣货结束后把拣出货物放到 drop_zone 时才有） */
  dropZoneCellId?: string | null
  dropZoneZoneId?: string | null
  /** 拣出并放到放置区的货物快照（place_goods 结束后生成，供前端渲染放置区货物） */
  placedGoods?: SimulationTaskLine[]
  /** 人工拣货闭环各计时阶段的统一剩余 tick（下层输送/升降/上层输送/拣货/放置/回程共用） */
  remainingStageTicks?: number
  conveyorTransport?: {
    phase: string
    deck: ConveyorDeckKey
    route: string[]
    index: number
    currentBlockId?: string | null
    nextBlockId?: string | null
    waitingTicks: number
    movedTicks: number
    startedTick: number
  } | null
  /** 占块门控待上线的子箱 transport：路线首块空闲后才转正为 conveyorTransport（前端不渲染） */
  pendingTransport?: {
    phase: string
    deck: ConveyorDeckKey
    route: string[]
    index: number
    currentBlockId?: string | null
    nextBlockId?: string | null
    waitingTicks: number
    movedTicks: number
    startedTick: number
  } | null
  /** 回库时正在放置的订单行序号 */
  returnLineIndex?: number
  history: SimulationTaskHistory[]
  createdTick: number
  startedTick: number | null
  completedTick: number | null
  skuId?: string
  skuName?: string
  quantity?: number
  sourceLocationId?: string
  destinationLocationId?: string
  sourceTrackNode?: string
  destinationTrackNode?: string
  inTransitQuantity?: number
  plan?: ShuttlePlan
  failureReason?: string | null
}

export type SimulationOrderPriority = 'normal' | 'urgent'
export type SimulationOrderDispatchMode = 'compact' | 'balanced' | 'parallel'
export type SimulationOrderStatus =
  | 'ready'
  | 'waiting_dispatch'
  | 'executing'
  | 'returning'
  | 'cancelled'
  | 'completed'
  | 'exception'

export interface SimulationOrderLineInput {
  id: string
  skuId: string
  skuName: string
  spec?: string | null
  quantity: number
  /** 数量单位：pieces=按件、boxes=按箱（按箱时后端用 SKU 箱规 caseCnt 折算件数） */
  qtyMode?: 'pieces' | 'boxes'
}

export interface SimulationOrderInput {
  id: string
  priority: SimulationOrderPriority
  dispatchMode?: SimulationOrderDispatchMode
  maxParallelCtu?: number
  targetStation: string
  createdAt: string
  lines: SimulationOrderLineInput[]
}

export interface SimulationOrder extends SimulationOrderInput {
  status: SimulationOrderStatus
  statusLabel: string
  taskIds: string[]
  totalTasks: number
  completedTasks: number
  progress: number
  missingQuantity?: number
  startedTick?: number | null
  completedTick?: number | null
  cancelRequested?: boolean
  cancelRequestedTick?: number | null
  cancelledTick?: number | null
  cancelReason?: string | null
  decomposition?: {
    stage: 'inventory_matched' | 'tasks_generated' | 'dispatching' | 'executing' | 'returning' | 'cancelled' | 'completed' | 'exception'
    requestedQuantity: number
    allocatedQuantity: number
    missingQuantity: number
    /** 件数口径：需求/已拣/缺口；箱数口径见 requestedQuantity 等字段 */
    requestedPieces?: number
    allocatedPieces?: number
    missingPieces?: number
    pickedPieces?: number
    pickedBoxes?: number
    shuttleBoxes?: number
    ctuBoxes?: number
    shuttleTaskCount?: number
    ctuTaskCount?: number
    taskCount: number
    assignedTaskCount: number
    activeVehicleIds: string[]
    vehicleCapacity: number
    strategy: SimulationOrderDispatchMode
    maxParallelCtu: number
  }
  lines: Array<SimulationOrderLineInput & {
    allocated?: number
    picked?: number
    caseCnt?: number
    requestedPieces?: number
    requestedBoxes?: number
    allocatedPieces?: number
    pickedPieces?: number
    shuttleBoxes?: number
    ctuBoxes?: number
    missingPieces?: number
  }>
}

export interface GoodsCatalogSku {
  skuId: string
  name: string
  spec?: string | null
  grade?: string | null
  material?: string | null
  largeCategory?: string | null
  subclass?: string | null
  /** 箱规：每箱件数 */
  caseCnt?: number | null
}

export interface GoodsCatalog {
  version: number
  skus: GoodsCatalogSku[]
}

export interface SimulationAlert {
  category: string
  level: 'info' | 'warning' | 'error'
  message: string
  tick: number
  details: Record<string, unknown>
  suggestion: string
}

export interface SimulationQueues {
  conveyor: string[]
  station: string[]
  buffer: string[]
}

export interface GoodsSlot {
  nodeId: string
  cellId: string
  layer: number
  skuId?: string | null
  skuName?: string | null
  largeCategory?: string | null
  subclass?: string | null
  spec?: string | null
  grade?: string | null
  material?: string | null
  surfaceTreatment?: string | null
  qty?: number
}

export interface GoodsInventory {
  topologyId: string
  slots: GoodsSlot[]
}

export interface SimulationState {
  id: string
  status: 'idle' | 'running' | 'paused' | 'stopped' | 'completed'
  stopReason?: 'user' | 'safety_limit' | 'transfer_failed' | 'task_failed' | 'runtime_error' | null
  runtimeError?: {
    type: string
    message: string
    tick: number
  } | null
  orderDriven?: boolean
  tick: number
  speedMultiplier?: 1 | 2 | 4
  tickIntervalSeconds?: number
  eventCount?: number
  topologyId: string
  motionPhysicsContract?: MotionPhysicsContractInput | null
  robots: SimulationRobot[]
  tasks: SimulationTask[]
  orders?: SimulationOrder[]
  inventory?: GoodsSlot[]
  shuttleDocks?: ShuttleDockState[]
  transferInventory?: TransferInventoryBalance[]
  transferEvents?: TransferInventoryEvent[]
  alerts: SimulationAlert[]
  queues: SimulationQueues
  conveyorItems?: Array<{
    taskId: string
    orderId?: string | null
    deck: ConveyorDeckKey
    currentBlockId?: string | null
    nextBlockId?: string | null
    waitingTicks: number
    phase: string
    goodsCount: number
  }>
  pickingArm?: {
    status: string
    stage: string | null
    remainingTicks: number
    taskId: string | null
    robotId: string | null
  } | null
  armBeltBoxes?: Array<{
    nodeId?: string | null
    cellId: string
    layer?: number
    level?: number
    beltHeight?: number
    depth?: number
    skuId?: string | null
    skuName?: string | null
    largeCategory?: string | null
    qty?: number
    placedTick?: number
    [key: string]: unknown
  }>
  /** 侧向出货口理货机械臂状态（state.pickingArms）：各臂独立取放，把货箱搬进放置区。 */
  pickingArms?: Array<{
    armId: string
    nodeId: string
    status: string
    stage: string | null
    remainingTicks: number
    taskId: string | null
    cargoToken: string | null
    portCellId: string | null
    dropCellId: string | null
    cargo?: {
      orderId?: string | null
      skuId?: string | null
      skuName?: string | null
      largeCategory?: string | null
      qty?: number
      [key: string]: unknown
    } | null
  }>
  /** 机械臂搬进放置区的货箱（state.armDropBoxes）。 */
  armDropBoxes?: Array<{
    armId?: string
    taskId?: string | null
    cellId: string
    row?: number
    col?: number
    orderId?: string | null
    skuId?: string | null
    largeCategory?: string | null
    qty?: number
    placedTick?: number
    [key: string]: unknown
  }>
  trafficMonitor?: {
    waitForEdges: Array<{
      waiter: string
      owner: string
      resource: string
      kind: 'cell' | 'port'
    }>
    stalledTaskIds: string[]
    suspectedDeadlocks: Array<{
      id: string
      type: 'cyclic_wait' | 'livelock'
      robotIds: string[]
      taskIds: string[]
      firstTick: number
      lastTick: number
      consecutiveTicks: number
      level: 'suspected' | 'confirmed'
      reason: string
      recoveryStatus?: 'executing' | 'completed' | null
      priorityRobotId?: string
      actions?: Array<{
        robotId: string
        action: string
        cellId?: string
        taskId?: string
      }>
    }>
    activeRecoveries: Array<Record<string, unknown>>
  }
  createdAt: string
  updatedAt: string
}

export interface SimulationEvent {
  run_id?: string
  tick: number
  event_type?: string
  type?: string
  payload: Record<string, unknown>
  created_at?: string
}
