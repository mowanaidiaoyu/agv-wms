import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePlatformStore } from '../store/usePlatformStore'
import { api } from '../lib/api'
import type { ContractMetric, DeadlockLifecycleMetric, DeadlockLifecycleMetrics } from '../types'
import { AnalyticsPage } from './AnalyticsPage'

function metric(metricId: string, value: unknown, overrides: Partial<ContractMetric> = {}): ContractMetric {
  return {
    metricId,
    displayName: metricId,
    value,
    unit: 'task',
    sampleCount: typeof value === 'number' ? 1 : 0,
    implementationStatus: 'PARTIAL',
    dataQuality: 'GOOD',
    reason: null,
    acceptanceEligible: false,
    ...overrides,
  }
}

function deadlockMetric(metricId: string, value: number | null, overrides: Partial<DeadlockLifecycleMetric> = {}): DeadlockLifecycleMetric {
  return {
    metricId,
    displayName: metricId,
    value,
    unit: 'episode',
    sampleCount: value === null ? 0 : 1,
    dataQuality: 'GOOD',
    reason: null,
    acceptanceEligible: false,
    source: 'RUN_PAYLOAD.deadlockDiagnosticsV1',
    generation: 2,
    snapshotAsOfTick: 635,
    ...overrides,
  }
}

function responses(runId: string) {
  const taskMetrics = [
    metric('PARENT_COMPLETED_TASK_COUNT', runId === 'run-b' ? 3 : 9, { sampleCount: 9 }),
    metric('LEGACY_COMPLETED_TASKS', 7),
    metric('PARENT_COMPLETED_TASK_COUNT_DELTA', runId === 'run-b' ? -4 : 2),
    metric('PARENT_CREATE_TO_COMPLETE_AVG_TICKS', 359.2, { unit: 'Tick', sampleCount: 9 }),
  ]
  const history = {
    runId,
    dataQuality: 'DEGRADED',
    reason: 'COLLECTION_COMPLETENESS_UNCONFIRMED',
    collectionCompleteness: 'UNCONFIRMED',
    qualityIssues: ['COLLECTION_COMPLETENESS_UNCONFIRMED'],
    rawSampleCount: 2000,
    uniqueTickCount: 1999,
    duplicateSnapshotCount: 1,
    outputSampleCount: 2,
    invalidSnapshotCount: 0,
    points: [
      { tick: 1, completedTasks: 0, pendingTasks: 7, blockedRobots: 0, conveyorQueueDepth: 0, bufferOccupancy: 0, conveyorUtilization: 0 },
      { tick: 635, completedTasks: 7, pendingTasks: 2, blockedRobots: 5, conveyorQueueDepth: 2, bufferOccupancy: 1, conveyorUtilization: 12.5 },
    ],
    statistics: {
      blockedRobots: {
        description: '阻塞车辆数',
        unit: 'robot',
        minimum: 0,
        maximum: 5,
        maximumTick: 635,
        average: 2.5,
        validSampleCount: 1999,
        missingSampleCount: 0,
        invalidSampleCount: 0,
        dataQuality: 'DEGRADED',
        reason: 'COLLECTION_COMPLETENESS_UNCONFIRMED',
        acceptanceEligible: false,
      },
    },
    acceptanceEligible: false,
  }
  const contention = {
    runId,
    eventCollectionCompleteness: 'UNCONFIRMED',
    dataQuality: 'DEGRADED',
    reason: 'EVENT_COLLECTION_UNCONFIRMED',
    metrics: [
      metric('CTU_ASSIGNED_PARENT_TASK_COUNT', 9, { dataQuality: 'DEGRADED', reason: 'EVENT_COLLECTION_UNCONFIRMED' }),
      metric('CTU_FIRST_ASSIGN_WAIT_AVG_TICKS', 187, { unit: 'Tick', sampleCount: 9, dataQuality: 'DEGRADED', reason: 'EVENT_COLLECTION_UNCONFIRMED', missingAssignmentEvidenceCount: 3 }),
      metric('CTU_FIRST_ASSIGN_WAIT_MAX_TICKS', 841, { unit: 'Tick', sampleCount: 9, dataQuality: 'DEGRADED', reason: 'EVENT_COLLECTION_UNCONFIRMED' }),
      metric('CTU_FIRST_ASSIGN_WAIT_SAMPLE_COUNT', 9, { sampleCount: 9, dataQuality: 'DEGRADED', reason: 'EVENT_COLLECTION_UNCONFIRMED' }),
      metric('CTU_VEHICLE_TASK_ASSOCIATION_COUNT', [{ robotId: 'ctu-01', taskCount: 6 }, { robotId: 'ctu-02', taskCount: 3 }], { sampleCount: 9, dataQuality: 'DEGRADED', reason: 'EVENT_COLLECTION_UNCONFIRMED' }),
      metric('BLOCKED_ROBOT_COUNT_CURRENT', 0, { unit: 'robot', sampleCount: 4 }),
      metric('SUSPECTED_DEADLOCK_ALERT_COUNT', 4, { unit: 'event', sampleCount: 4, dataQuality: 'DEGRADED', reason: 'EVENT_COLLECTION_UNCONFIRMED' }),
      metric('ROBOT_YIELD_EVENT_COUNT', 515, { unit: 'event', sampleCount: 515, dataQuality: 'DEGRADED', reason: 'EVENT_COLLECTION_UNCONFIRMED' }),
      metric('TRUE_DEADLOCK_RATE', null, { unit: 'event_per_1000_tasks', implementationStatus: 'UNAVAILABLE', dataQuality: 'MISSING_SOURCE', reason: 'NO_CONFIRMED_WAIT_CYCLE_EVENT' }),
    ],
    quality: {
      ctuParentTaskCount: 12,
      invalidTaskRecordCount: 0,
      conflictingTaskIdCount: 0,
      validAssignmentEventCount: 9,
      invalidAssignmentEventCount: 0,
      ignoredAssignedStateEventCount: 0,
      outOfScopeAssignmentEventCount: 0,
      validDeadlockEventCount: 4,
      invalidDeadlockEventCount: 0,
      validYieldEventCount: 515,
      invalidYieldEventCount: 0,
    },
    acceptanceEligible: false,
  }
  const group = {
    runId,
    algorithm: 'astar',
    topologyRevision: 'topo-v1',
    configRevision: 'config-v1',
    coordinateContractRevision: 'coord-v1',
    sampleKind: 'successful_shuttle_transfer_plan',
  }
  const aggregate = [{ group, minimum: 1, maximum: 5, average: 3, validSampleCount: 5, samplePreview: [], previewSampleCount: 0, dataQuality: 'SUCCESS_ONLY_SAMPLE', reason: 'SUCCESS_ONLY_SAMPLE_BIAS' }]
  const path = {
    runId,
    sampleKind: 'successful_shuttle_transfer_plan',
    dataQuality: 'SUCCESS_ONLY_SAMPLE',
    reason: 'SUCCESS_ONLY_SAMPLE_BIAS',
    metrics: [
      metric('PATH_SUCCESS_SAMPLE_COUNT', 5, { unit: 'plan', sampleCount: 5, implementationStatus: 'IMPLEMENTED', dataQuality: 'SUCCESS_ONLY_SAMPLE', reason: 'SUCCESS_ONLY_SAMPLE_BIAS' }),
      metric('PATH_ALGORITHM', [{ algorithm: 'astar', sampleCount: 5 }], { unit: 'enum', sampleCount: 5, implementationStatus: 'IMPLEMENTED', dataQuality: 'SUCCESS_ONLY_SAMPLE' }),
      metric('PATH_PLANNING_TIME_MS', aggregate, { unit: 'ms', sampleCount: 5, implementationStatus: 'IMPLEMENTED', dataQuality: 'SUCCESS_ONLY_SAMPLE' }),
      metric('PATH_HORIZONTAL_DISTANCE_CELLS', aggregate, { unit: 'cell', sampleCount: 5, implementationStatus: 'IMPLEMENTED', dataQuality: 'SUCCESS_ONLY_SAMPLE' }),
      metric('PATH_VISITED_STATE_COUNT', aggregate, { unit: 'state', sampleCount: 5, implementationStatus: 'IMPLEMENTED', dataQuality: 'SUCCESS_ONLY_SAMPLE' }),
      metric('PATH_PLANNING_TIME_QUANTILES_MS', [{
        group,
        sampleCount: 5,
        quantiles: {
          p50: { value: 3, unit: 'ms', requiredSampleCount: 5, actualSampleCount: 5, reason: null },
          p90: { value: null, unit: 'ms', requiredSampleCount: 10, actualSampleCount: 5, reason: 'INSUFFICIENT_SAMPLE' },
          p99: { value: null, unit: 'ms', requiredSampleCount: 100, actualSampleCount: 5, reason: 'INSUFFICIENT_SAMPLE' },
        },
        implementationStatus: 'PARTIAL',
        dataQuality: 'SUCCESS_ONLY_SAMPLE',
        reason: 'SUCCESS_ONLY_SAMPLE_BIAS',
        acceptanceEligible: false,
      }], { unit: 'ms', sampleCount: 5, dataQuality: 'SUCCESS_ONLY_SAMPLE' }),
      metric('PATH_REPLAN_SUCCESS_RATE', null, { unit: '%', implementationStatus: 'UNAVAILABLE', dataQuality: 'MISSING_SOURCE', reason: 'NO_REPLAN_TRIGGER_AND_RESULT_EVENTS' }),
    ],
    quality: { successfulSampleCount: 5 },
    acceptanceEligible: false,
  }
  const conveyor = {
    runId,
    scope: 'current_persisted_final_metrics',
    dataQuality: 'GOOD',
    reason: null,
    metrics: [
      metric('CONVEYOR_NODE_OCCUPANCY_CURRENT', 12.5, { unit: '%', implementationStatus: 'IMPLEMENTED', alreadyPercent: true }),
      metric('CONVEYOR_WAIT_TASK_COUNT_CURRENT', 0, { implementationStatus: 'IMPLEMENTED' }),
      metric('BUFFER_WAIT_TASK_COUNT_CURRENT', 0, { implementationStatus: 'IMPLEMENTED' }),
      metric('STATION_QUEUE_TASK_COUNT_CURRENT', 2, { implementationStatus: 'IMPLEMENTED' }),
      metric('DEVICE_UTILIZATION', null, { unit: '%', implementationStatus: 'UNAVAILABLE', dataQuality: 'MISSING_SOURCE', reason: 'NO_PER_DEVICE_BUSY_AND_AVAILABLE_INTERVALS' }),
      metric('CONVEYOR_PHYSICAL_UTILIZATION', null, { unit: '%', implementationStatus: 'UNAVAILABLE', dataQuality: 'MISSING_SOURCE', reason: 'NO_CAPACITY_AND_TIME_WEIGHTED_OCCUPANCY' }),
    ],
    historyTrendSource: '/api/v1/simulations/{run_id}/metrics/history/quality',
    acceptanceEligible: false,
  }
  const deadlock: DeadlockLifecycleMetrics = {
    contractVersion: 'deadlock-lifecycle-metrics-v1',
    runId,
    generation: runId === 'run-b' ? 3 : 2,
    availableGenerations: runId === 'run-b' ? [3] : [1, 2],
    runStatus: 'completed',
    stopReason: null,
    snapshotAsOfTick: 635,
    observationCompleteness: 'COMPLETE',
    dataQuality: 'DEGRADED',
    reason: 'DUPLICATE_TRANSITION_KEY_DEDUPLICATED',
    qualityIssues: ['DUPLICATE_TRANSITION_KEY_DEDUPLICATED'],
    acceptanceEligible: false,
    eventPersistenceWatermark: {
      databaseEventId: 99,
      databaseEventTick: 635,
      lastTransitionKey: `${runId}:generation-2:ep-1:resolved`,
      uniqueTransitionCount: 3,
      duplicateTransitionCount: 1,
      conflictingTransitionCount: 0,
      invalidEventCount: 0,
      snapshotLastAcknowledgedTransitionKey: `${runId}:generation-2:ep-1:resolved`,
      snapshotPendingTransitionCount: 0,
    },
    metrics: [
      deadlockMetric('CUMULATIVE_BLOCKED_ROBOT_TICKS', runId === 'run-b' ? 5 : 41, { unit: 'robot·Tick', sampleCount: 635 }),
      deadlockMetric('BLOCK_EPISODE_COUNT', 6, { unit: 'event', sampleCount: 6 }),
      deadlockMetric('CURRENT_BLOCKED_ROBOT_COUNT', 0, { unit: 'robot', sampleCount: 4 }),
      deadlockMetric('MAX_CONTINUOUS_BLOCKED_TICKS', 12, { unit: 'Tick', sampleCount: 4 }),
      deadlockMetric('SUSPECTED_CYCLE_EPISODE_COUNT', 2, { sampleCount: 2 }),
      deadlockMetric('PERSISTENT_WAIT_CYCLE_COUNT', 1, { sampleCount: 1 }),
      deadlockMetric('OPEN_CYCLE_EPISODE_COUNT', 0, { sampleCount: 0 }),
      deadlockMetric('CONFIRMED_RESOLVED_EPISODE_COUNT', 1, { sampleCount: 1 }),
      deadlockMetric('UNRESOLVED_AT_RUN_END_COUNT', 0, { sampleCount: 0 }),
      deadlockMetric('ENDED_UNVERIFIED_EPISODE_COUNT', 0, { sampleCount: 0 }),
      deadlockMetric('AVERAGE_CONFIRMED_RESOLUTION_TICKS', 7, { unit: 'Tick', sampleCount: 1, source: 'simulation_events.lifecycle_transition_key_deduplicated' }),
      deadlockMetric('CONFIRMED_RESOLUTION_RATIO', 100, { unit: '%', sampleCount: 1, reason: 'THRESHOLDS_NOT_FIELD_CALIBRATED' }),
    ],
    robotBlocking: [],
    hotspots: [{
      cellId: 'cell-r10-c20',
      row: 10,
      col: 20,
      floor: null,
      observedEpisodeCount: 1,
      unit: 'episode',
      involvedRobotCount: 2,
      sampleCount: 1,
      dataQuality: 'DEGRADED',
      reason: 'FLOOR_NOT_RECORDED_IN_EPISODE',
      acceptanceEligible: false,
      source: 'simulation_events.lifecycle_transition_key_deduplicated.deadlock_episode_persistent',
      generation: 2,
      snapshotAsOfTick: 635,
    }],
    recentEpisodes: [],
    limits: { hotspotLimit: 20, recentEpisodeLimit: 20, fullEpisodeHistoryReturned: false },
    semantics: {
      persistentCycleMeaning: '仿真中达到持续阈值的循环等待，不等于物理设备不可恢复死锁',
      durationFormula: 'resolvedAtTick - persistentAtTick',
      blockedRobotTicksUnit: 'robot·Tick',
      thresholdCalibration: 'P2-1默认阈值尚未现场标定',
      legacyDeadlockDetectedUsed: false,
    },
  }
  const distanceMetric = (value: number | null, sampleCount = value === null ? 0 : 2) => ({
    value,
    unit: 'm',
    sampleCount,
    dataQuality: value === null ? 'UNAVAILABLE' : 'COMPLETE',
    reason: value === null ? 'PHYSICS_CONTRACT_UNAVAILABLE' : null,
    source: 'simulation_events.robot_movement_batch_v1',
  })
  const motion = {
    contractVersion: 'motion-distance-metrics-v1',
    runId,
    generation: runId === 'run-b' ? 3 : 2,
    runInstanceId: `${runId}:generation-${runId === 'run-b' ? 3 : 2}`,
    physicsContractVersion: 'motion-physics-v1',
    metersPerCell: 0.8,
    physicsContractSource: 'USER_DEFINED_SIMULATION',
    calibrationStatus: 'UNVERIFIED',
    snapshotAsOfTick: 635,
    eventPersistenceWatermark: { movementId: 'move-2', tick: 635, eventSequence: 2 },
    totalMetrics: {
      totalHorizontalMeters: distanceMetric(runId === 'run-b' ? 4.8 : 8.8),
      totalVerticalMeters: distanceMetric(3),
      ctuHorizontalMeters: distanceMetric(runId === 'run-b' ? 1.6 : 4),
      shuttleHorizontalMeters: distanceMetric(4.8),
      shuttleLiftVerticalMeters: distanceMetric(3, 1),
    },
    vehicleTypeMetrics: {},
    movementTypeMetrics: {
      NORMAL: { movementCount: 7, horizontalMeters: 5.6, verticalMeters: 0, unit: 'm', dataQuality: 'COMPLETE', source: 'simulation_events.robot_movement_batch_v1' },
      YIELD: { movementCount: 1, horizontalMeters: 0.8, verticalMeters: 0, unit: 'm', dataQuality: 'COMPLETE', source: 'simulation_events.robot_movement_batch_v1' },
      BACKOFF: { movementCount: 1, horizontalMeters: 0.8, verticalMeters: 0, unit: 'm', dataQuality: 'COMPLETE', source: 'simulation_events.robot_movement_batch_v1' },
      RETURN_TO_STANDBY: { movementCount: 2, horizontalMeters: 1.6, verticalMeters: 0, unit: 'm', dataQuality: 'COMPLETE', source: 'simulation_events.robot_movement_batch_v1' },
      LIFT: { movementCount: 1, horizontalMeters: 0, verticalMeters: 3, unit: 'm', dataQuality: 'COMPLETE', source: 'simulation_events.robot_movement_batch_v1' },
      UNKNOWN: { movementCount: 0, horizontalMeters: 0, verticalMeters: 0, unit: 'm', dataQuality: 'COMPLETE', source: 'simulation_events.robot_movement_batch_v1' },
    },
    perRobotMetrics: [
      { robotId: 'ctu-01', robotType: 'ctu', horizontalMeters: 4, verticalMeters: 0, movementCount: 5, yieldMeters: 0.8, backoffMeters: 0, returnToStandbyMeters: 0.8, observationCompleteness: 'COMPLETE', dataQuality: 'COMPLETE' },
      { robotId: 'shuttle-01', robotType: 'shuttle', horizontalMeters: 4.8, verticalMeters: 3, movementCount: 7, yieldMeters: 0, backoffMeters: 0.8, returnToStandbyMeters: 0.8, observationCompleteness: 'COMPLETE', dataQuality: 'COMPLETE' },
    ],
    perRobotMetricsLimit: 20,
    totalRobotCount: 2,
    perRobotMetricsTruncated: false,
    evidenceSummary: {},
    plannedHorizontalMeters: null,
    extraDistanceMeters: null,
    attributableReplanExtraMeters: null,
    algorithmDistanceImprovementRate: null,
    observationCompleteness: 'COMPLETE',
    dataQuality: 'COMPLETE',
    reason: null,
    acceptanceEligible: false,
    semantics: {},
  }
  return {
    '/metrics/tasks': { runId, metrics: taskMetrics },
    '/metrics/history/quality': history,
    '/metrics/ctu-contention': contention,
    '/metrics/path-planning': path,
    '/metrics/conveyor-coordination': conveyor,
    '/metrics/deadlock-lifecycle': deadlock,
    '/metrics/motion-distance': motion,
    '/metrics/run-performance': {
      contractVersion: 'run-performance-metrics-v1',
      runId,
      status: 'completed',
      motionSpeedContract: null,
      simulatedSeconds: null,
      wallClockSeconds: null,
      playbackMultiplier: 2,
      movementRobotSeconds: null,
      waitingRobotSeconds: null,
      averageEffectiveHorizontalSpeedMetersPerSecond: null,
      completedTaskCount: 9,
      totalTaskCount: 9,
      taskCompletionRatePercent: 100,
      dataQuality: 'UNAVAILABLE',
      reason: 'MOTION_SPEED_CONTRACT_UNAVAILABLE',
    },
    '/algorithm-experiments': { contractVersion: 'algorithm-experiment-list-v1', runs: [] },
  }
}

function syntheticResponses() {
  const common = {
    datasetVersion: 'analytics-synthetic-v1',
    seed: 42,
    dataSource: 'SYNTHETIC',
    inputDatasetHash: 'input-sha256',
    validationStatus: 'PASS',
    limitations: ['synthetic only'],
  }
  const quality = {
    inputOrderCount: 240,
    inputLineCount: 684,
    inputQuantity: 2089,
    assignedOrderCount: 240,
    assignedLineCount: 684,
    assignedQuantity: 2089,
    orderConservation: true,
    lineConservation: true,
    quantityConservation: true,
    duplicateAssignmentCount: 0,
    unassignedOrderCount: 0,
  }
  const baseline = {
    algorithm: 'FIFO_CAPACITY_BASELINE',
    status: 'PASS',
    waveCount: 16,
    totalDistanceCells: 2112,
    capacityUtilization: { ordersCapacityUsage: 0.75, linesCapacityUsage: 0.7125, quantityCapacityUsage: 0.8704166667, unit: 'ratio_0_to_1' },
    dataQuality: quality,
  }
  const optimized = {
    algorithm: 'LOCATION_AWARE_GREEDY',
    status: 'PASS',
    waveCount: 15,
    totalDistanceCells: 1500,
    capacityUtilization: { ordersCapacityUsage: 0.8, linesCapacityUsage: 0.76, quantityCapacityUsage: 0.9284444444, unit: 'ratio_0_to_1' },
    dataQuality: quality,
  }
  const waveDetail = (algorithm: string, waveId: string) => ({ algorithm, waveId, priority: 'NORMAL', orderCount: 16, lineCount: 45, totalQuantity: 139, distanceCells: 100, ordersCapacityUsage: 0.8, linesCapacityUsage: 0.75, quantityCapacityUsage: 0.9267, capacityValidation: 'PASS' })
  const abc = {
    ...common,
    experimentId: 's2-1-abc-heat-seed-42',
    algorithmVersion: 'dynamic-abc-sma7-wma7-v1',
    sampleCount: 100,
    abcSnapshots: [
      { asOfDayIndex: 30, lookbackDays: 30, observedDayRange: { startDayIndex: 1, endDayIndex: 30 }, classCounts: { A: 26, B: 28, C: 46 }, classHistoricalDemandValueShare: { A: 0.701, B: 0.199, C: 0.1 }, changeFromPreviousSnapshot: { changedSkuCount: null, reason: 'NO_PREVIOUS_SNAPSHOT', transitionCounts: {} }, items: [] },
      { asOfDayIndex: 76, lookbackDays: 30, observedDayRange: { startDayIndex: 47, endDayIndex: 76 }, classCounts: { A: 26, B: 28, C: 46 }, classHistoricalDemandValueShare: { A: 0.7060312357, B: 0.1969453978, C: 0.0970233665 }, changeFromPreviousSnapshot: { changedSkuCount: 2, reason: null, transitionCounts: { 'B->C': 1, 'C->B': 1 } }, items: [] },
    ],
    latestAbcSummary: {
      asOfDayIndex: 76,
      classCounts: { A: 26, B: 28, C: 46 },
      classHistoricalDemandValueShare: { A: 0.7060312357, B: 0.1969453978, C: 0.0970233665 },
      topSkus: [{ skuId: 'SYN-SKU-0016', abcClass: 'A', historicalDemandValue: 573246.28, historicalDemandUnits: 956, contributionRate: 0.0473319419, cumulativeContributionRate: 0.0473319419, lookbackDays: 30, asOfDayIndex: 76, classificationVersion: 'dynamic-abc-demand-value-30d-v1' }],
    },
    skuForecasts: [{ skuId: 'SYN-SKU-0016', predictions: [{ dayIndex: 76, date: '2026-03-18', actual: 18, sma7: 20, wma7: 21, historyDayIndices: [69, 70, 71, 72, 73, 74, 75], backtestStrategy: 'rolling_one_day_ahead', predictionHorizon: 'next_day_24h_proxy' }, { dayIndex: 77, date: '2026-03-19', actual: 22, sma7: 19, wma7: 20, historyDayIndices: [70, 71, 72, 73, 74, 75, 76], backtestStrategy: 'rolling_one_day_ahead', predictionHorizon: 'next_day_24h_proxy' }]}],
    forecastEvaluation: {
      SMA7: { backtestStrategy: 'rolling_one_day_ahead', evaluationWindow: { days: 14, startDayIndex: 76, endDayIndex: 89 }, forecastMethod: 'SMA7', mae: 2.109286, mape: 23.43109, mapeReason: null, mapeSampleCount: 1389, predictionHorizon: 'next_day_24h_proxy', totalPredictionCount: 1400, validPredictionCount: 1400, zeroActualExcludedCount: 11, excludedSampleCount: 0 },
      WMA7: { backtestStrategy: 'rolling_one_day_ahead', evaluationWindow: { days: 14, startDayIndex: 76, endDayIndex: 89 }, forecastMethod: 'WMA7', mae: 2.339541, mape: 25.803738, mapeReason: null, mapeSampleCount: 1389, predictionHorizon: 'next_day_24h_proxy', totalPredictionCount: 1400, validPredictionCount: 1400, zeroActualExcludedCount: 11, excludedSampleCount: 0 },
      comparison: { lowerMaeMethod: 'SMA7', maeBaseline: 'SMA7', sameEvaluationSamples: true, wma7RelativeMaeImprovementPercent: -10.916253 },
    },
  }
  const wave = {
    ...common,
    experimentId: 's2-2-wave-optimization-seed-42',
    algorithmVersion: 'wave-fifo-location-aware-greedy-v1',
    distanceMetric: { name: 'MANHATTAN_2D_GRID_PROXY', unit: 'cell', routeRule: 'proxy', acceptanceEligibleAsRealWarehousePath: false },
    baseline,
    optimized,
    comparison: { baselineDistanceCells: 2112, optimizedDistanceCells: 1500, baselineWaveCount: 16, optimizedWaveCount: 15, distanceImprovementRate: 28.977273, reason: null, unit: '%' },
    dataQuality: { input: { orderCount: 240, lineCount: 684, quantityCount: 2089, validationStatus: 'PASS' }, formalAcceptanceEligible: false, sameDistanceCalculator: true, sameInputOrders: true },
    waveDetails: { baseline: [waveDetail('FIFO_CAPACITY_BASELINE', 'FIFO-001')], optimized: [waveDetail('LOCATION_AWARE_GREEDY', 'OPT-001')] },
  }
  const etaEvaluation = (maeTicks: number, mapePercent: number, p90: number) => ({ sampleCount: 72, maeTicks, mapePercent, mapeSampleCount: 72, mapeReason: null, zeroActualExcludedCount: 0, p90AbsoluteErrorTicks: p90, p90Reason: null, p90RequiredSampleCount: 10, p90Method: 'NEAREST_RANK_CEIL_NO_INTERPOLATION' })
  const eta = {
    ...common,
    experimentId: 's2-3-synthetic-eta-seed-42',
    validationStatus: 'PASS_WITH_STRICT_TARGET_AVAILABILITY_CUTOFF',
    modelVersion: 'synthetic-eta-comparison-v1',
    trainSampleCount: 288,
    fitSampleCount: 268,
    testSampleCount: 72,
    featureNames: ['distanceCells'],
    trainingCutoff: { policy: 'TARGET_AVAILABLE_AT_FIRST_TEST_CREATION', comparison: 'completedTick <= firstTestCreatedTick', firstTestCreatedTick: 1548, nominalTrainSampleCount: 288, fitSampleCount: 268, excludedUnavailableTargetCount: 20, excludedTaskIds: ['SYN-ETA-TASK-000262', 'SYN-ETA-TASK-000288'], maxFitCompletedTick: 1542, maxNominalTrainCompletedTick: 1660, temporalLeakagePrevented: true, deployabilityStatus: 'OFFLINE_SYNTHETIC_STRICT_CUTOFF' },
    baselineModel: { modelVersion: 'TASK_TYPE_MEDIAN_BASELINE', trainingSampleCount: 268, taskTypeMedians: { ctu_pick: 99.5 }, globalMedian: 98.5 },
    ridgeModel: { modelVersion: 'RIDGE_LINEAR_ETA_V1', trainingSampleCount: 268, ridgeLambda: 1, interceptPenalized: false, solver: 'NORMAL_EQUATIONS', negativePredictionClampedCount: 0, unknownCategorySampleCount: 0 },
    overallEvaluation: { baseline: etaEvaluation(27.708333, 31.667944, 50.5), ridge: etaEvaluation(5.053495, 5.355409, 9.266017), maeImprovementPercent: 81.761822, maeImprovementReason: null, evaluationSampleIdentity: 'SAME_72_TEST_TASKS', durationUnit: 'Tick' },
    taskTypeEvaluation: { ctu_pick: { sampleCount: 46, baselineMAE: 28.413, modelMAE: 3.9078, baselineMAPE: 33.8848, modelMAPE: 4.4481, baselineP90AbsoluteErrorTicks: 50.5, modelP90AbsoluteErrorTicks: 7.7871, baselineP90Reason: null, modelP90Reason: null, zeroActualExcludedCount: 0, dataSource: 'SYNTHETIC' } },
    predictions: [{ taskId: 'SYN-ETA-TASK-000289', taskType: 'ctu_pick', createdTick: 1548, actualDurationTicks: 100, baselinePredictedTicks: 99.5, modelPredictedTicks: 101, baselineAbsoluteErrorTicks: 0.5, modelAbsoluteErrorTicks: 1, distanceCells: 80, loadQuantity: 2, queueLength: 3, congestionLevel: 0.2, assignedRobotType: 'CTU', dataSource: 'SYNTHETIC' }],
    fallbackSampleCount: 0,
  }
  return {
    '/analytics-synthetic-v1/abc_heat_seed42.json': abc,
    '/analytics-synthetic-v1/wave_optimization_seed42.json': wave,
    '/analytics-synthetic-v1/eta_prediction_seed42.json': eta,
  }
}

function installFetch(overrides?: (url: string, payload: unknown) => Response | undefined) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const staticMatch = Object.entries(syntheticResponses()).find(([suffix]) => url.includes(suffix))
    const runId = decodeURIComponent(url.match(/simulations\/([^/]+)/)?.[1] ?? 'run-a')
    const payloads = responses(runId)
    const match = staticMatch ?? Object.entries(payloads).find(([suffix]) => url.includes(suffix))
    if (!match) return new Response(JSON.stringify({ detail: 'unexpected URL' }), { status: 404 })
    const custom = overrides?.(url, match[1])
    if (custom) return custom
    if (staticMatch) expect(init?.method).toBe('GET')
    else expect(init?.method).toBeUndefined()
    return new Response(JSON.stringify(match[1]), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', mock)
  return mock
}

function Switcher() {
  const navigate = useNavigate()
  return <button type="button" onClick={() => navigate('/simulation/analytics?runId=run-b')}>切换 Run</button>
}

function renderPage(entry = '/simulation/analytics?runId=run-a', withSwitcher = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        {withSwitcher ? <Switcher /> : null}
        <Routes>
          <Route path="/simulation/analytics" element={<AnalyticsPage />} />
          <Route path="/simulation" element={<div>simulation-destination</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  usePlatformStore.getState().resetRun()
  usePlatformStore.getState().setSelectedTopologyId('')
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('algorithm performance analytics M2-5B', () => {
  it('keeps the friendly no-run state and sends no API request', () => {
    const fetchMock = installFetch()
    renderPage('/simulation/analytics')
    expect(screen.getByText(/尚无选中的 Run/)).toBeInTheDocument()
    expect(screen.queryByText('A. 运行概览')).not.toBeInTheDocument()
    expect(screen.queryByText('动态 ABC 与热度预测')).not.toBeInTheDocument()
    expect(screen.queryByText('动态参数自适应')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('treats a whitespace-only runId as invalid and sends no metrics request', () => {
    const fetchMock = installFetch()
    renderPage('/simulation/analytics?runId=%20%20')
    expect(screen.getByText(/尚无选中的 Run/)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('does not create real-Run placeholders for unavailable 6.1, 6.2, or 6.8 metrics', async () => {
    installFetch()
    renderPage()
    expect(await screen.findByText('已完成父任务数量')).toBeInTheDocument()
    expect(screen.queryByText('动态 ABC 与热度预测')).not.toBeInTheDocument()
    expect(screen.queryByText('波次聚类与组合优化')).not.toBeInTheDocument()
    expect(screen.queryByText('动态参数自适应')).not.toBeInTheDocument()
    expect(screen.queryByText(/正式指标缺失|尚不可计算|UNAVAILABLE/)).not.toBeInTheDocument()
  })

  it('requests the eight run-scoped metrics plus the read-only experiment list and performs no writes', async () => {
    const fetchMock = installFetch()
    renderPage()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(9))
    const urls = fetchMock.mock.calls.map(([input]) => String(input))
    expect(urls).toEqual(expect.arrayContaining([
      expect.stringContaining('/simulations/run-a/metrics/tasks'),
      expect.stringContaining('/simulations/run-a/metrics/history/quality?max_points=240'),
      expect.stringContaining('/simulations/run-a/metrics/ctu-contention'),
      expect.stringContaining('/simulations/run-a/metrics/path-planning'),
      expect.stringContaining('/simulations/run-a/metrics/conveyor-coordination'),
      expect.stringContaining('/simulations/run-a/metrics/deadlock-lifecycle'),
      expect.stringContaining('/simulations/run-a/metrics/motion-distance'),
      expect.stringContaining('/simulations/run-a/metrics/run-performance'),
      expect.stringContaining('/algorithm-experiments'),
    ]))
    expect(urls.every((url) => !url.endsWith('/state') && !url.includes('/start') && !url.includes('/pause') && !url.includes('/reset'))).toBe(true)
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
    expect(fetchMock.mock.calls.some(([, init]) => ['POST', 'PUT', 'PATCH', 'DELETE'].includes((init?.method ?? 'GET').toUpperCase()))).toBe(false)
  })

  it('shows verified same-scenario algorithm comparison and never uses synthetic wave distance', async () => {
    const runs = [
      { runId: 'exp-a', status: 'completed', scenarioId: 's1', scenarioVersion: '1', scenarioSignature: 'same', variableDimension: 'pathPolicy', pathPolicy: 'astar', assignmentPolicy: 'greedy', algorithmVersion: 'v1', simulatedSeconds: 10, tick: 20 },
      { runId: 'exp-b', status: 'completed', scenarioId: 's1', scenarioVersion: '1', scenarioSignature: 'same', variableDimension: 'pathPolicy', pathPolicy: 'dijkstra', assignmentPolicy: 'greedy', algorithmVersion: 'v1', simulatedSeconds: 10, tick: 20 },
    ]
    installFetch((url) => {
      if (url.includes('/algorithm-experiments/compare')) return new Response(JSON.stringify({
        contractVersion: 'algorithm-comparison-v1', comparable: true, comparisonReasons: [],
        baseline: { runId: 'exp-a', status: 'completed', pathPolicy: 'astar', assignmentPolicy: 'greedy', totalActualHorizontalMeters: 10, ctuHorizontalMeters: 10, shuttleHorizontalMeters: 0, liftVerticalMeters: 0, completedTaskCount: 1, totalTaskCount: 1, taskCompletionRatePercent: 100, simulatedSeconds: 10, planningDurationMs: 2, planningCallCount: 1, replanCount: 0, blockedRobotTicks: 0, persistentCycleEpisodeCount: 0, confirmedResolvedEpisodeCount: 0, dataComplete: true },
        candidate: { runId: 'exp-b', status: 'completed', pathPolicy: 'dijkstra', assignmentPolicy: 'greedy', totalActualHorizontalMeters: 8, ctuHorizontalMeters: 8, shuttleHorizontalMeters: 0, liftVerticalMeters: 0, completedTaskCount: 1, totalTaskCount: 1, taskCompletionRatePercent: 100, simulatedSeconds: 10, planningDurationMs: 3, planningCallCount: 1, replanCount: 0, blockedRobotTicks: 0, persistentCycleEpisodeCount: 0, confirmedResolvedEpisodeCount: 0, dataComplete: true },
        distanceSavedMeters: 2, distanceImprovementPercent: 20, simulatedSecondsSaved: 0, simulatedTimeImprovementPercent: 0, planningDurationSavedMs: -1, planningTimeImprovementPercent: -50, blockedRobotTicksChange: 0,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.endsWith('/algorithm-experiments')) return new Response(JSON.stringify({ contractVersion: 'algorithm-experiment-list-v1', runs }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      return undefined
    })
    renderPage()
    const baseline = await screen.findByLabelText('基线实验Run')
    fireEvent.change(baseline, { target: { value: 'exp-a' } })
    fireEvent.change(screen.getByLabelText('候选实验Run'), { target: { value: 'exp-b' } })
    const improvementLabel = await screen.findByText('距离改善率')
    expect(improvementLabel.parentElement).toHaveTextContent('20 %')
    expect(screen.getByText(/SYNTHETIC 波次距离不会进入本表/)).toBeInTheDocument()
  })

  it('shows valid real metrics, preserves true zero, and does not multiply percentages', async () => {
    installFetch()
    renderPage()
    await waitFor(() => expect(document.querySelector('[data-metric-id="PARENT_COMPLETED_TASK_COUNT"]')).not.toBeNull())
    const parentCard = document.querySelector('[data-metric-id="PARENT_COMPLETED_TASK_COUNT"]') as HTMLElement
    expect(within(parentCard).getByText('9')).toBeInTheDocument()
    expect(document.querySelector('[data-metric-id="LEGACY_COMPLETED_TASKS"]')).toBeNull()
    const blockedCard = document.querySelector('[data-metric-id="BLOCKED_ROBOT_COUNT_CURRENT"]') as HTMLElement
    expect(within(blockedCard).getByText('0')).toBeInTheDocument()
    const occupancyLabel = screen.getByText('当前输送节点占用代理')
    expect(occupancyLabel.parentElement).toHaveTextContent('12.5 %')
    expect(screen.queryByText('1250.0%')).not.toBeInTheDocument()
    expect(screen.getByText(/当前状态为0，不代表整个 Run 期间均为0/)).toBeInTheDocument()
  })

  it('uses persisted first-assignment evidence and historical robot-task associations', async () => {
    installFetch()
    renderPage()
    expect((await screen.findAllByText('187.0 Tick')).length).toBeGreaterThan(0)
    expect(screen.getByText(/不使用任务最终 startedTick/)).toBeInTheDocument()
    expect(screen.getAllByText('ctu-01').length).toBeGreaterThan(0)
    expect(screen.getByText('ctu-02')).toBeInTheDocument()
    expect(screen.getByText(/不同 robotId \+ taskId 去重/)).toBeInTheDocument()
    expect(screen.queryByText(/最近一次开始 Tick/)).not.toBeInTheDocument()
  })

  it('labels suspected deadlocks without rendering unavailable confirmed-deadlock metrics', async () => {
    installFetch()
    renderPage()
    const section = (await screen.findByText('死锁与拥堵诊断')).closest('.analytics-module') as HTMLElement
    expect(within(section).getByText('疑似死锁告警次数')).toBeInTheDocument()
    expect(within(section).getByText('4')).toBeInTheDocument()
    expect(within(section).getByText(/不等于确认死锁/)).toBeInTheDocument()
    expect(screen.queryByText(/真实死锁率|解锁成功率|平均解锁时长/)).not.toBeInTheDocument()
    expect(screen.getByText('任务生命周期')).toBeInTheDocument()
    expect(screen.getByText(/不是 ETA 预测准确率/)).toBeInTheDocument()
  })

  it('renders real deadlock lifecycle metrics without mixing legacy alerts and Episodes', async () => {
    installFetch()
    renderPage()
    const section = (await screen.findByText('死锁与拥堵诊断')).closest('.analytics-module') as HTMLElement
    expect(within(section).getByText('疑似死锁告警次数')).toBeInTheDocument()
    expect(within(section).getByText('疑似循环 Episode')).toBeInTheDocument()
    expect(within(section).getByText('持续循环等待 Episode')).toBeInTheDocument()
    expect(within(section).getByText('持续循环等待 Episode').parentElement).toHaveTextContent('1')
    expect(within(section).getByText(/不与新版 Episode 数相加/)).toBeInTheDocument()
    expect(within(section).getByText(/PERSISTENT_WAIT_CYCLE 不代表物理设备不可恢复死锁/)).toBeInTheDocument()
    expect(within(section).getByText('累计阻塞车辆Tick').parentElement).toHaveTextContent('41 robot·Tick')
    expect(within(section).getByText('平均确认恢复耗时').parentElement).toHaveTextContent('7.0 Tick')
    expect(within(section).getByText('确认恢复比例（非正式成功率）').parentElement).toHaveTextContent('100.0 %')
    expect(within(section).getByText('已确认恢复事件').parentElement).toHaveTextContent('1')
    expect(within(section).getByText('Run结束未解除事件').parentElement).toHaveTextContent('0')
    expect(within(section).getByText('无法确认事件').parentElement).toHaveTextContent('0')
    expect(within(section).getByLabelText('死锁生命周期统计合同')).toHaveTextContent(/generation\s*2/)
  })

  it('preserves lifecycle zero but hides null, non-finite, and unsampled averages', async () => {
    installFetch((url, payload) => {
      if (!url.includes('/metrics/deadlock-lifecycle')) return undefined
      const changed = structuredClone(payload) as ReturnType<typeof responses>['/metrics/deadlock-lifecycle']
      changed.metrics = changed.metrics.map((item) => {
        if (item.metricId === 'CUMULATIVE_BLOCKED_ROBOT_TICKS') return { ...item, value: 0, sampleCount: 0 }
        if (item.metricId === 'BLOCK_EPISODE_COUNT') return { ...item, value: Number.POSITIVE_INFINITY }
        if (item.metricId === 'MAX_CONTINUOUS_BLOCKED_TICKS') return { ...item, value: null, sampleCount: 0 }
        if (item.metricId === 'AVERAGE_CONFIRMED_RESOLUTION_TICKS') return { ...item, value: 0, sampleCount: 0 }
        if (item.metricId === 'CONFIRMED_RESOLUTION_RATIO') return { ...item, value: null, sampleCount: 0 }
        return item
      })
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => changed,
      } as Response
    })
    renderPage()
    const section = (await screen.findByText('死锁与拥堵诊断')).closest('.analytics-module') as HTMLElement
    expect(within(section).getByText('累计阻塞车辆Tick').parentElement).toHaveTextContent('0 robot·Tick')
    expect(within(section).queryByText('阻塞事件次数')).not.toBeInTheDocument()
    expect(within(section).queryByText('最长连续阻塞Tick')).not.toBeInTheDocument()
    expect(within(section).queryByText('平均确认恢复耗时')).not.toBeInTheDocument()
    expect(within(section).queryByText('确认恢复比例（非正式成功率）')).not.toBeInTheDocument()
  })

  it('keeps legacy Runs free of deadlock-lifecycle placeholder cards', async () => {
    installFetch((url, payload) => {
      if (!url.includes('/metrics/deadlock-lifecycle')) return undefined
      const changed = structuredClone(payload) as ReturnType<typeof responses>['/metrics/deadlock-lifecycle']
      changed.generation = null
      changed.availableGenerations = []
      changed.observationCompleteness = 'MISSING'
      changed.dataQuality = 'MISSING_SOURCE'
      changed.reason = 'DEADLOCK_DIAGNOSTICS_V1_NOT_AVAILABLE'
      changed.metrics = changed.metrics.map((item) => ({ ...item, value: null, sampleCount: 0, dataQuality: 'MISSING_SOURCE' }))
      changed.hotspots = []
      return new Response(JSON.stringify(changed), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    renderPage()
    const section = (await screen.findByText('死锁与拥堵诊断')).closest('.analytics-module') as HTMLElement
    expect(within(section).getByText('疑似死锁告警次数')).toBeInTheDocument()
    expect(within(section).queryByText('累计阻塞车辆Tick')).not.toBeInTheDocument()
    expect(within(section).queryByText('持续循环等待 Episode')).not.toBeInTheDocument()
    expect(within(section).queryByText(/generation/)).not.toBeInTheDocument()
    expect(screen.queryByText(/DEADLOCK_DIAGNOSTICS_V1_NOT_AVAILABLE/)).not.toBeInTheDocument()
  })

  it('shows hotspot grid coordinates without inventing a floor or 3D location', async () => {
    installFetch()
    renderPage()
    expect(await screen.findByText('row 10 · col 20')).toBeInTheDocument()
    expect(screen.getByText('未记录')).toBeInTheDocument()
    expect(screen.queryByText('1F')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /3D|定位/ })).not.toBeInTheDocument()
  })

  it('keeps watermark limitations in the default-collapsed quality details', async () => {
    installFetch()
    renderPage()
    const summary = await screen.findByText('数据质量与指标限制')
    const details = summary.closest('details') as HTMLDetailsElement
    expect(details).not.toHaveAttribute('open')
    expect(details).toHaveTextContent('P2-2B 死锁生命周期')
    expect(details).toHaveTextContent('数据库事件水位：ID 99 / Tick 635')
    expect(details).toHaveTextContent('重复 1；冲突 0')
    expect(details).toHaveTextContent('BEST_EFFORT_NOT_EXACTLY_ONCE')
    expect(details).toHaveTextContent('阈值尚未现场标定')
    expect(details).toHaveTextContent('正式验收：否')
  })

  it('isolates a deadlock endpoint failure while other real metrics stay visible', async () => {
    installFetch((url) => url.includes('/metrics/deadlock-lifecycle')
      ? new Response(JSON.stringify({ detail: 'deadlock lifecycle unavailable' }), { status: 503, statusText: 'Unavailable', headers: { 'Content-Type': 'application/json' } })
      : undefined)
    renderPage()
    expect(await screen.findByText(/死锁生命周期指标读取失败：deadlock lifecycle unavailable/)).toBeInTheDocument()
    expect(screen.getByText('已完成父任务数量')).toBeInTheDocument()
    expect(screen.getByText('疑似死锁告警次数')).toBeInTheDocument()
    expect(screen.queryByText('累计阻塞车辆Tick')).not.toBeInTheDocument()
  })

  it('encodes runId and appends generation only when explicitly requested', async () => {
    const fetchMock = installFetch()
    await api.getSimulationDeadlockLifecycleMetrics('run/a ?')
    await api.getSimulationDeadlockLifecycleMetrics('run/a ?', 3)
    await api.getSimulationMotionDistanceMetrics('run/a ?')
    await api.getSimulationMotionDistanceMetrics('run/a ?', 4)
    const urls = fetchMock.mock.calls.map(([input]) => String(input))
    expect(urls[0]).toContain('/simulations/run%2Fa%20%3F/metrics/deadlock-lifecycle')
    expect(urls[0]).not.toContain('generation=')
    expect(urls[1]).toContain('/simulations/run%2Fa%20%3F/metrics/deadlock-lifecycle?generation=3')
    expect(urls[2]).toContain('/simulations/run%2Fa%20%3F/metrics/motion-distance')
    expect(urls[2]).not.toContain('generation=')
    expect(urls[3]).toContain('/simulations/run%2Fa%20%3F/metrics/motion-distance?generation=4')
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
  })

  it('shows history quality counts and full-data peak independently from sampled charts', async () => {
    installFetch()
    renderPage()
    expect(await screen.findByText('2000')).toBeInTheDocument()
    expect(screen.getByText('1999')).toBeInTheDocument()
    expect(screen.getByText(/全量阻塞峰值为 5，峰值 Tick 为 635/)).toBeInTheDocument()
    expect(screen.getByText(/曲线来自后端按 Tick 去重后的安全抽样/)).toBeInTheDocument()
    expect(screen.getByText('UNCONFIRMED')).toBeInTheDocument()
  })

  it('renders valid planning samples and backend P50/P90/P99 without unavailable placeholders', async () => {
    installFetch()
    renderPage()
    expect(await screen.findByText('3.00 ms')).toBeInTheDocument()
    expect(screen.getByText('样本不足 5/10')).toBeInTheDocument()
    expect(screen.getByText('样本不足 5/100')).toBeInTheDocument()
    expect(screen.getAllByText(/SUCCESS_ONLY_SAMPLE/).length).toBeGreaterThan(0)
    expect(screen.getByText('路径规划与车辆实际里程')).toBeInTheDocument()
    expect(screen.getByText(/不是车辆实际运行里程/)).toBeInTheDocument()
    expect(screen.queryByText(/路径优化率|重规划样本|实际距离无数据/)).not.toBeInTheDocument()
  })

  it('renders real CTU, Shuttle, Lift and movement-type mileage with the frozen contract', async () => {
    installFetch()
    renderPage()
    const section = (await screen.findByText('路径规划与车辆实际里程')).closest('.analytics-module') as HTMLElement
    expect(within(section).getByText('CTU 总水平行驶距离').parentElement).toHaveTextContent('4.000 m')
    expect(within(section).getByText('Shuttle 总水平行驶距离').parentElement).toHaveTextContent('4.800 m')
    expect(within(section).getByText('Shuttle Lift 垂直位移').parentElement).toHaveTextContent('3.000 m')
    const movementTypes = section.querySelector('.analytics-motion-types') as HTMLElement
    expect(within(movementTypes).getByText('主动让行').parentElement).toHaveTextContent('0.800 m')
    expect(within(movementTypes).getByText('回退').parentElement).toHaveTextContent('0.800 m')
    expect(within(movementTypes).getByText('返回待命').parentElement).toHaveTextContent('1.600 m')
    expect(within(section).getByLabelText('物理距离合同')).toHaveTextContent(/0.800 m/)
    expect(within(section).getByLabelText('物理距离合同')).toHaveTextContent(/用户定义模拟参数\s*是/)
    expect(within(section).getByText(/Lift 数值是垂直运输位移/)).toBeInTheDocument()
  })

  it('keeps 6.6 visible from mileage when planning has zero samples and renders backend-ranked vehicles', async () => {
    installFetch((url, payload) => {
      if (!url.includes('/metrics/path-planning')) return undefined
      const changed = structuredClone(payload) as ReturnType<typeof responses>['/metrics/path-planning']
      changed.metrics = changed.metrics.map((item) => item.metricId === 'PATH_SUCCESS_SAMPLE_COUNT'
        ? { ...item, value: 0, sampleCount: 0 }
        : item)
      return new Response(JSON.stringify(changed), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    renderPage()
    const section = (await screen.findByText('路径规划与车辆实际里程')).closest('.analytics-module') as HTMLElement
    expect(within(section).queryByText('D. 规划诊断')).not.toBeInTheDocument()
    expect(within(section).getByText('ctu-01')).toBeInTheDocument()
    expect(within(section).getByText('shuttle-01')).toBeInTheDocument()
  })

  it('shows legal zero mileage but hides null mileage cards', async () => {
    installFetch((url, payload) => {
      if (!url.includes('/metrics/motion-distance')) return undefined
      const changed = structuredClone(payload) as ReturnType<typeof responses>['/metrics/motion-distance']
      changed.totalMetrics.ctuHorizontalMeters.value = 0
      changed.totalMetrics.shuttleHorizontalMeters.value = null
      return new Response(JSON.stringify(changed), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    renderPage()
    const section = (await screen.findByText('路径规划与车辆实际里程')).closest('.analytics-module') as HTMLElement
    expect(within(section).getByText('CTU 总水平行驶距离').parentElement).toHaveTextContent('0.000 m')
    expect(within(section).queryByText('Shuttle 总水平行驶距离')).not.toBeInTheDocument()
  })

  it('isolates a mileage endpoint error while keeping planning diagnostics', async () => {
    installFetch((url) => url.includes('/metrics/motion-distance')
      ? new Response(JSON.stringify({ detail: 'motion metrics unavailable' }), { status: 503, statusText: 'Unavailable', headers: { 'Content-Type': 'application/json' } })
      : undefined)
    renderPage()
    const section = (await screen.findByText('路径规划与车辆实际里程')).closest('.analytics-module') as HTMLElement
    expect(within(section).getByText(/车辆实际里程指标读取失败：motion metrics unavailable/)).toBeInTheDocument()
    expect(within(section).getByText('D. 规划诊断')).toBeInTheDocument()
    expect(within(section).queryByText('CTU 总水平行驶距离')).not.toBeInTheDocument()
  })

  it('hides the complete 6.6 module when neither planning nor mileage exists', async () => {
    installFetch((url, payload) => {
      if (url.includes('/metrics/path-planning')) {
        const changed = structuredClone(payload) as ReturnType<typeof responses>['/metrics/path-planning']
        changed.metrics = changed.metrics.map((item) => item.metricId === 'PATH_SUCCESS_SAMPLE_COUNT'
          ? { ...item, value: 0, sampleCount: 0, dataQuality: 'GOOD', reason: 'NO_SAMPLES' }
          : item.metricId === 'PATH_PLANNING_TIME_QUANTILES_MS'
            ? { ...item, value: null, sampleCount: 0, reason: 'NO_SAMPLES' }
            : item)
        return new Response(JSON.stringify(changed), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/metrics/motion-distance')) {
        const changed = structuredClone(payload) as ReturnType<typeof responses>['/metrics/motion-distance']
        Object.values(changed.totalMetrics).forEach((item) => { item.value = null })
        changed.perRobotMetrics = []
        return new Response(JSON.stringify(changed), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return undefined
    })
    renderPage()
    expect(await screen.findByText('已完成父任务数量')).toBeInTheDocument()
    expect(screen.queryByText('路径规划与车辆实际里程')).not.toBeInTheDocument()
    expect(screen.queryByText(/暂无跨层初始成功规划样本|系统没有路径规划能力/)).not.toBeInTheDocument()
  })

  it('hides a null metric instead of coercing it to zero or rendering a placeholder', async () => {
    installFetch((url, payload) => {
      if (!url.includes('/metrics/tasks')) return undefined
      const changed = structuredClone(payload) as ReturnType<typeof responses>['/metrics/tasks']
      changed.metrics = changed.metrics.map((item) => item.metricId === 'PARENT_CREATE_TO_COMPLETE_AVG_TICKS'
        ? { ...item, value: null, sampleCount: 0, dataQuality: 'DEGRADED', reason: 'NO_VALID_DURATION_SAMPLES' }
        : item)
      return new Response(JSON.stringify(changed), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    renderPage()
    expect(await screen.findByText('已完成父任务数量')).toBeInTheDocument()
    expect(screen.queryByText('父任务平均生命周期')).not.toBeInTheDocument()
    expect(screen.queryByText('任务生命周期')).not.toBeInTheDocument()
    expect(screen.queryByText('无有效耗时样本')).not.toBeInTheDocument()
    expect(screen.queryByText(/真实死锁率|UNAVAILABLE/)).not.toBeInTheDocument()
  })

  it('hides node occupancy when its conveyor denominator is missing and keeps valid zero queues', async () => {
    installFetch((url, payload) => {
      if (!url.includes('/metrics/conveyor-coordination')) return undefined
      const changed = structuredClone(payload) as ReturnType<typeof responses>['/metrics/conveyor-coordination']
      changed.dataQuality = 'MISSING_SOURCE'
      changed.metrics = changed.metrics.map((item) => item.metricId === 'CONVEYOR_NODE_OCCUPANCY_CURRENT'
        ? { ...item, value: null, sampleCount: 0, dataQuality: 'MISSING_SOURCE', reason: 'CONVEYOR_GRAPH_NODES_MISSING' }
        : item)
      return new Response(JSON.stringify(changed), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    renderPage()
    expect(await screen.findByText('输送协同')).toBeInTheDocument()
    expect(document.querySelector('[data-metric-id="CONVEYOR_NODE_OCCUPANCY_CURRENT"]')).toBeNull()
    expect(screen.queryByText('当前输送节点占用代理')).not.toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
    const waitCards = screen.getAllByText('当前输送等待任务数')
    expect(waitCards.length).toBeGreaterThan(0)
    expect(waitCards.some((label) => label.parentElement?.textContent?.includes('0'))).toBe(true)
  })

  it('reports endpoint errors without producing fake zero', async () => {
    installFetch((url) => url.includes('/metrics/tasks')
      ? new Response(JSON.stringify({ detail: 'task metrics unavailable' }), { status: 503, statusText: 'Unavailable', headers: { 'Content-Type': 'application/json' } })
      : undefined)
    renderPage()
    expect(await screen.findByText(/父子任务指标读取失败：task metrics unavailable/)).toBeInTheDocument()
    expect(screen.queryByText('已完成父任务数量')).not.toBeInTheDocument()
    expect(Array.from(document.querySelectorAll('.analytics-kpi strong')).some((node) => node.textContent === '—')).toBe(false)
    expect(screen.getByText('死锁与拥堵诊断')).toBeInTheDocument()
  })

  it('switches query keys by run without showing the previous run value', async () => {
    installFetch()
    renderPage('/simulation/analytics?runId=run-a', true)
    const parentLabel = await screen.findByText('已完成父任务数量')
    expect(await within(parentLabel.closest('.analytics-kpi') as HTMLElement).findByText('9')).toBeInTheDocument()
    expect(screen.getByText('累计阻塞车辆Tick').parentElement).toHaveTextContent('41 robot·Tick')
    fireEvent.click(screen.getByRole('button', { name: '切换 Run' }))
    await waitFor(() => {
      const card = screen.getByText('已完成父任务数量').closest('.analytics-kpi') as HTMLElement
      expect(within(card).getByText('3')).toBeInTheDocument()
      expect(within(card).queryByText('9')).not.toBeInTheDocument()
      expect(screen.getByText('累计阻塞车辆Tick').parentElement).toHaveTextContent('5 robot·Tick')
      expect(screen.getByLabelText('死锁生命周期统计合同')).toHaveTextContent(/generation\s*3/)
      expect(screen.queryByText('41 robot·Tick')).not.toBeInTheDocument()
    })
  })

  it('keeps the original return navigation and the truthful station proxy name', async () => {
    installFetch()
    renderPage()
    expect((await screen.findAllByText('当前站台阶段任务数（代理）')).length).toBeGreaterThan(0)
    expect(screen.queryByText('站台等待队列')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /返回仿真工作台/ }))
    expect(await screen.findByText('simulation-destination')).toBeInTheDocument()
  })

  it('keeps real Run mode as default and lazy-loads no synthetic JSON', async () => {
    const fetchMock = installFetch()
    renderPage()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(9))
    expect(screen.getByRole('button', { name: '真实 Run 指标' })).toHaveAttribute('aria-pressed', 'true')
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/analytics-synthetic-v1/'))).toBe(false)
    expect(screen.queryByText('SYNTHETIC 模拟数据')).not.toBeInTheDocument()
  })

  it('keeps concise source and calculation quality details collapsed without listing missing metrics', async () => {
    installFetch()
    renderPage()
    const summary = await screen.findByText('数据质量与指标限制')
    const details = summary.closest('details') as HTMLDetailsElement
    expect(details).not.toHaveAttribute('open')
    expect(details).toHaveTextContent('M2-1 任务状态')
    expect(details).toHaveTextContent('仅统计成功的 Shuttle 初始规划')
    expect(screen.queryByText(/正式指标缺口|尚不可计算|未实现的正式指标/)).not.toBeInTheDocument()
  })

  it('opens synthetic experiments without a runId and loads exactly three static JSON files by GET', async () => {
    const fetchMock = installFetch()
    renderPage('/simulation/analytics')
    expect(fetchMock).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '模拟算法实验' }))
    expect(await screen.findByText('SYNTHETIC 模拟数据')).toBeInTheDocument()
    expect(screen.getAllByText('analytics-synthetic-v1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('seed=42').length).toBeGreaterThan(0)
    expect(screen.getByText(/不属于真实 Run/)).toBeInTheDocument()
    expect(screen.getByText(/不能用于正式验收/)).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
    const calls = fetchMock.mock.calls
    expect(calls.map(([input]) => String(input))).toEqual(expect.arrayContaining([
      '/analytics-synthetic-v1/abc_heat_seed42.json',
      '/analytics-synthetic-v1/wave_optimization_seed42.json',
      '/analytics-synthetic-v1/eta_prediction_seed42.json',
    ]))
    expect(calls.every(([, init]) => init?.method === 'GET')).toBe(true)
    expect(calls.some(([input]) => String(input).includes('/simulations/'))).toBe(false)
  })

  it('renders ABC counts, value shares, forecast metrics, and rolling backtest metadata from JSON', async () => {
    installFetch()
    renderPage('/simulation/analytics')
    fireEvent.click(screen.getByRole('button', { name: '模拟算法实验' }))
    expect(await screen.findByText('6.1 动态 ABC 与次日热度预测')).toBeInTheDocument()
    const aClassCard = screen.getByText('A 类 SKU').closest('div') as HTMLElement
    expect(aClassCard).toHaveTextContent('26 个')
    expect(aClassCard).toHaveTextContent('70.6031%')
    expect(screen.getByText('B 类 SKU').closest('div')).toHaveTextContent('28 个')
    expect(screen.getByText('C 类 SKU').closest('div')).toHaveTextContent('46 个')
    expect(screen.getByText('2.109286')).toBeInTheDocument()
    expect(screen.getByText('23.431090 %')).toBeInTheDocument()
    expect(screen.getByText(/rolling_one_day_ahead/)).toBeInTheDocument()
    expect(screen.getByText(/Day 76 的 ABC 快照包含当天已观察需求/)).toBeInTheDocument()
  })

  it('renders wave comparison, capacity conservation, cell units, and proxy warning', async () => {
    installFetch()
    renderPage('/simulation/analytics')
    fireEvent.click(screen.getByRole('button', { name: '模拟算法实验' }))
    expect(await screen.findByText('6.2 波次聚类与组合优化')).toBeInTheDocument()
    expect(screen.getByText('240 笔')).toBeInTheDocument()
    expect(screen.getByText('684 行')).toBeInTheDocument()
    expect(screen.getByText('2089 件')).toBeInTheDocument()
    expect(screen.getByText('2,112 cell')).toBeInTheDocument()
    expect(screen.getByText('1,500 cell')).toBeInTheDocument()
    expect(screen.getByText(/MANHATTAN_2D_GRID_PROXY/)).toBeInTheDocument()
    expect(screen.getByText(/不得称为真实 AGV 路径优化率/)).toBeInTheDocument()
    expect(screen.getAllByText('PASS').length).toBeGreaterThan(0)
  })

  it('distinguishes nominal ETA training, strict fitted samples, excluded labels, and Tick errors', async () => {
    installFetch()
    renderPage('/simulation/analytics')
    fireEvent.click(screen.getByRole('button', { name: '模拟算法实验' }))
    expect(await screen.findByText('6.5 ETA 预测与误差评估')).toBeInTheDocument()
    expect(screen.getByText('总任务（分区合计）').closest('div')).toHaveTextContent('360')
    expect(screen.getByText('名义训练分区').closest('div')).toHaveTextContent('288')
    expect(screen.getByText('严格截止实际拟合').closest('div')).toHaveTextContent('268')
    expect(screen.getByText('截止时标签不可用').closest('div')).toHaveTextContent('20')
    expect(screen.getByText(/completedTick <= firstTestCreatedTick/)).toBeInTheDocument()
    expect(screen.getByText('27.708333 Tick')).toBeInTheDocument()
    expect(screen.getByText('5.053495 Tick')).toBeInTheDocument()
    expect(screen.getByText(/模拟测试集 MAE 改善率：81\.761822%/)).toBeInTheDocument()
    expect(screen.getByText(/P90 是绝对误差分位数，不是预测区间覆盖率/)).toBeInTheDocument()
  })

  it('plots all 72 ETA samples with finite visible coordinates, correct axes, and no coordinate deduplication', async () => {
    const etaPayload = syntheticResponses()['/analytics-synthetic-v1/eta_prediction_seed42.json']
    const basePrediction = etaPayload.predictions[0]
    const predictions = Array.from({ length: 72 }, (_, index) => {
      const valueIndex = index === 71 ? 0 : index
      const actualDurationTicks = 40 + valueIndex * 2
      const modelPredictedTicks = 80 + valueIndex
      return {
        ...basePrediction,
        taskId: `SYN-ETA-CHART-${String(index + 1).padStart(3, '0')}`,
        actualDurationTicks,
        modelPredictedTicks,
        modelAbsoluteErrorTicks: Math.abs(actualDurationTicks - modelPredictedTicks),
      }
    })
    installFetch((url, payload) => url.includes('eta_prediction_seed42.json')
      ? new Response(JSON.stringify({ ...(payload as object), predictions }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      : undefined)
    renderPage('/simulation/analytics')
    fireEvent.click(screen.getByRole('button', { name: '模拟算法实验' }))

    const chart = await screen.findByTestId('eta-scatter-chart')
    expect(screen.getByText('n=72')).toBeInTheDocument()
    const points = [...chart.querySelectorAll<SVGCircleElement>('.eta-scatter-point')]
    expect(points).toHaveLength(72)
    points.forEach((point) => {
      const cx = Number(point.getAttribute('cx'))
      const cy = Number(point.getAttribute('cy'))
      expect(Number.isFinite(cx)).toBe(true)
      expect(Number.isFinite(cy)).toBe(true)
      expect(cx).toBeGreaterThanOrEqual(62)
      expect(cx).toBeLessThanOrEqual(722)
      expect(cy).toBeGreaterThanOrEqual(24)
      expect(cy).toBeLessThanOrEqual(246)
    })

    const axisMaximum = Number(chart.getAttribute('data-axis-max'))
    const firstPoint = chart.querySelector<SVGCircleElement>('[data-task-id="SYN-ETA-CHART-001"]') as SVGCircleElement
    expect(firstPoint).toHaveAttribute('data-actual', '40')
    expect(firstPoint).toHaveAttribute('data-predicted', '80')
    expect(Number(firstPoint.getAttribute('cx'))).toBeCloseTo(62 + (40 / axisMaximum) * 660)
    expect(Number(firstPoint.getAttribute('cy'))).toBeCloseTo(246 - (80 / axisMaximum) * 222)

    const equalityLine = screen.getByTestId('eta-equality-line')
    expect(equalityLine).toHaveAttribute('x1', '62')
    expect(equalityLine).toHaveAttribute('y1', '246')
    expect(equalityLine).toHaveAttribute('x2', '722')
    expect(equalityLine).toHaveAttribute('y2', '24')
    expect(points[0]).toHaveAttribute('cx', points[71].getAttribute('cx'))
    expect(points[0]).toHaveAttribute('cy', points[71].getAttribute('cy'))
    expect(screen.getByText(/精确重合的额外样本：1/)).toBeInTheDocument()
    expect(screen.getByText('查看 72 条样本详情').closest('details')?.querySelectorAll('tbody tr')).toHaveLength(72)
  })

  it('shows a clear empty state when ETA prediction details are absent', async () => {
    installFetch((url, payload) => url.includes('eta_prediction_seed42.json')
      ? new Response(JSON.stringify({ ...(payload as object), predictions: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      : undefined)
    renderPage('/simulation/analytics')
    fireEvent.click(screen.getByRole('button', { name: '模拟算法实验' }))
    expect(await screen.findByText('无合法测试样本')).toBeInTheDocument()
    expect(screen.getByText('6.1 动态 ABC 与次日热度预测')).toBeInTheDocument()
    expect(screen.getByText('6.2 波次聚类与组合优化')).toBeInTheDocument()
  })

  it('does not mix synthetic cards into real Run mode and clears real Run content while synthetic mode is active', async () => {
    const fetchMock = installFetch()
    renderPage()
    expect(await screen.findByText('已完成父任务数量')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '模拟算法实验' }))
    expect(await screen.findByText('SYNTHETIC 模拟数据')).toBeInTheDocument()
    expect(screen.queryByText('已完成父任务数量')).not.toBeInTheDocument()
    expect(screen.queryByText('原累计 completedTasks')).not.toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(12))
    fireEvent.click(screen.getByRole('button', { name: '真实 Run 指标' }))
    expect(await screen.findByText('已完成父任务数量')).toBeInTheDocument()
    expect(screen.queryByText('SYNTHETIC 模拟数据')).not.toBeInTheDocument()
  })

  it('reports a missing static result without replacing it with zero', async () => {
    installFetch((url) => url.includes('abc_heat_seed42.json')
      ? new Response('', { status: 404, statusText: 'Not Found' })
      : undefined)
    renderPage('/simulation/analytics')
    fireEvent.click(screen.getByRole('button', { name: '模拟算法实验' }))
    expect(await screen.findByText(/6.1 ABC\/热度实验加载失败：404 Not Found/)).toBeInTheDocument()
    expect(screen.queryByText('6.1 动态 ABC 与次日热度预测')).not.toBeInTheDocument()
    expect(screen.queryByText('0 个')).not.toBeInTheDocument()
    expect(screen.getByText('6.2 波次聚类与组合优化')).toBeInTheDocument()
  })

  it('rejects invalid synthetic metadata and never issues a write or simulation control request', async () => {
    const fetchMock = installFetch((url, payload) => {
      if (!url.includes('eta_prediction_seed42.json')) return undefined
      return new Response(JSON.stringify({ ...(payload as object), dataSource: 'REAL_RUN' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    renderPage('/simulation/analytics')
    fireEvent.click(screen.getByRole('button', { name: '模拟算法实验' }))
    expect(await screen.findByText(/实验 JSON 元数据或质量状态不符合/)).toBeInTheDocument()
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
    const urls = fetchMock.mock.calls.map(([input]) => String(input))
    expect(urls.some((url) => /\/start|\/pause|\/reset|\/state/.test(url))).toBe(false)
  })
})

