import type { ScenarioPayload, TopologyPayload } from '../types'

export const emptyTopology = (): TopologyPayload => ({
  id: `topology-${crypto.randomUUID().slice(0, 8)}`,
  name: '新建网格拓扑',
  meta: {
    version: '2.0.0',
    viewport: { x: 0, y: 0, zoom: 1 },
    tags: ['grid'],
    floors: ['1F'],
  },
  grid: {
    rows: 12,
    cols: 20,
    cellSize: 32,
    originX: 0,
    originY: 0,
  },
  zones: [],
  cells: [],
  nodes: [],
})

export const emptyScenario = (topologyId = ''): ScenarioPayload => ({
  id: `scenario-${crypto.randomUUID().slice(0, 8)}`,
  name: '新建场景',
  topologyId,
  robots: [],
  orderGenerator: {
    ratePerMinute: 4,
    maxOrders: 12,
    linesPerOrderMin: 1,
    linesPerOrderMax: 3,
    urgentInsertionTick: 20,
  },
  dispatchPolicy: 'greedy',
  pathPolicy: 'astar',
  conveyorPolicy: {
    releaseThreshold: 0.8,
    fifo: true,
  },
  faultInjection: {
    robotFailureTick: 30,
    robotFailureId: null,
    conveyorJammedTick: 45,
    conveyorCellId: null,
  },
  seed: 7,
  durationTicks: 90,
})
