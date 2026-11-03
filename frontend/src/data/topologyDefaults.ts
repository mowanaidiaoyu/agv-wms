import type { TopologyPayload } from '../types'


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
