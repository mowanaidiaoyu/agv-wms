import type { ScenarioPayload, TopologyPayload } from '../types'

export const emptyTopology = (): TopologyPayload => ({
  id: `topology-${crypto.randomUUID().slice(0, 8)}`,
  name: '新建拓扑',
  meta: {
    version: '1.0.0',
    viewport: { x: 0, y: 0, zoom: 1 },
    tags: ['draft'],
  },
  zones: [
    {
      id: 'zone-ctu',
      label: 'CTU 区',
      type: 'ctu',
      x: 40,
      y: 40,
      width: 240,
      height: 180,
    },
  ],
  nodes: [
    {
      id: 'node-1',
      type: 'storage',
      x: 80,
      y: 80,
      zoneId: 'zone-ctu',
      capacity: 1,
      label: 'Node-1',
    },
  ],
  edges: [],
  resources: [],
})

export const emptyScenario = (topologyId = ''): ScenarioPayload => ({
  id: `scenario-${crypto.randomUUID().slice(0, 8)}`,
  name: '新建场景',
  topologyId,
  robots: [
    {
      id: 'ctu-01',
      type: 'ctu',
      startNode: 'node-1',
      speed: 1.4,
      battery: 100,
      enabled: true,
    },
  ],
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
    robotFailureId: 'ctu-01',
    conveyorJammedTick: 45,
    conveyorEdgeId: null,
  },
  seed: 7,
  durationTicks: 90,
})
