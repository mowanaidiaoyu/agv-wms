import { describe, expect, it } from 'vitest'
import type { TopologyPayload, TransportLayoutPlan } from '../types'
import { assertTransportLayoutCompatible, selectLayoutPreview } from './transportLayout'

const topology: TopologyPayload = {
  id: 'topology-test',
  name: 'Topology',
  meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
  grid: { rows: 4, cols: 4, cellSize: 32, originX: 0, originY: 0 },
  zones: [],
  cells: [],
  nodes: [],
}

const plan: TransportLayoutPlan = {
  planId: 'plan-test',
  schemaVersion: '1',
  topologyId: topology.id,
  topologyRevision: '2.0.0:test',
  overallStatus: 'PARTIAL',
  recommendedOptionId: 'a',
  references: [],
  facilities: [{
    facilityId: 'facility-a',
    kind: 'pick_station',
    networkType: 'human_station',
    position: { floor: '1F', row: 1, col: 1, level: 1 },
    spansLevels: [1],
    status: 'proposed',
    validationStatus: 'PASS',
    capacity: 1,
    purpose: 'Preview fixture',
    existingRefIds: [],
    occupancyPolicy: 'exclusive',
    conflicts: [],
    evidence: [],
  }],
  connections: [],
  routes: [],
  options: [{
    optionId: 'a',
    label: 'A',
    status: 'PARTIAL',
    description: 'Fixture',
    facilityIds: ['facility-a'],
    connectionIds: [],
    assumptions: [],
    blockingReasons: [],
  }],
  issues: [],
  summary: {},
  assumptions: [],
}

describe('transport layout preview', () => {
  it('selects an isolated preview layer without mutating the formal topology', () => {
    const snapshot = structuredClone(topology)
    const selected = selectLayoutPreview(plan, 'a')
    expect(selected.facilities.map((item) => item.facilityId)).toEqual(['facility-a'])
    expect(topology).toEqual(snapshot)
  })

  it('rejects a topology or revision mismatch', () => {
    expect(() => assertTransportLayoutCompatible(plan, 'other')).toThrow(/topologyId/)
    expect(() => assertTransportLayoutCompatible(plan, topology.id, 'different')).toThrow(/topologyRevision/)
    expect(() => assertTransportLayoutCompatible(plan, topology.id, plan.topologyRevision)).not.toThrow()
  })
})
