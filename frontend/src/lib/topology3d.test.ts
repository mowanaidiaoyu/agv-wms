import { describe, expect, it } from 'vitest'
import type { TopologyPayload } from '../types'
import {
  buildSceneSpec,
  rackDeckCenterY,
  SHUTTLE_RACK_LEVEL_COUNT,
  SHUTTLE_TRACK_DECK_THICKNESS,
  shuttleLevelToWorldY,
  shuttleRackTopWorldY,
} from './topology3d'

describe('topology3d road rendering contract', () => {
  it('derives road and rack colors from Cell and Zone data without inventing CTU permissions', () => {
    const topology: TopologyPayload = {
      id: 'topology-render-audit',
      name: 'Render Audit',
      meta: { version: '2.0.0', viewport: { x: 0, y: 0, zoom: 1 }, tags: [], floors: ['1F'] },
      grid: { rows: 4, cols: 4, cellSize: 32, originX: 0, originY: 0 },
      zones: [
        { id: 'zone-ctu', label: 'CTU rack', type: 'ctu', x: 0, y: 0, width: 1, height: 2 },
        { id: 'zone-shuttle', label: 'Shuttle', type: 'shuttle', x: 2, y: 0, width: 2, height: 2 },
      ],
      cells: [
        { id: 'cell-ctu-road', floor: '1F', row: 2, col: 0, type: 'road', zoneId: 'zone-ctu', cost: 1, walkable: true },
        { id: 'cell-shuttle-road', floor: '1F', row: 0, col: 2, type: 'road', zoneId: 'zone-shuttle', cost: 1, walkable: true },
      ],
      nodes: [
        { id: 'storage-a', type: 'storage', floor: '1F', row: 0, col: 0, zoneId: 'zone-ctu', capacity: 1, label: 'A' },
      ],
      resources: [],
    }

    const items = buildSceneSpec(topology).items
    const ctuRoad = items.find((item) => item.kind === 'box' && item.key === 'cell-ctu-road')
    const shuttleRoad = items.find((item) => item.kind === 'box' && item.key === 'cell-shuttle-road')
    const ctuRack = items.find((item) => item.kind === 'rack' && item.key === 'zone-zone-ctu-1F')

    expect(ctuRoad).toMatchObject({ kind: 'box', color: '#2e3a52' })
    expect(shuttleRoad).toMatchObject({ kind: 'box', color: '#3a4f6e' })
    expect(ctuRack).toMatchObject({ kind: 'rack', color: '#0e7490', zoneType: 'ctu' })
  })

  it('places four Shuttle decks on internal running surfaces and retains a higher structural top', () => {
    const rack = {
      kind: 'rack' as const,
      key: 'shuttle-rack', x: 0, y: 0, z: 0, cellsW: 2, cellsD: 2,
      levels: SHUTTLE_RACK_LEVEL_COUNT, slotSize: 2, levelHeight: 1.4,
      color: '#b45309', zoneType: 'shuttle' as const, label: 'Shuttle', floor: '1F',
    }
    const surfaces = Array.from({ length: rack.levels }, (_, index) => (
      rackDeckCenterY(rack, index) + SHUTTLE_TRACK_DECK_THICKNESS / 2
    ))

    expect(surfaces).toEqual(Array.from({ length: rack.levels }, (_, index) => shuttleLevelToWorldY(index + 1)))
    expect(surfaces.every((surface, index) => index === 0 || surface > surfaces[index - 1])).toBe(true)
    expect(surfaces[3]).toBeLessThan(shuttleRackTopWorldY())
  })
})
