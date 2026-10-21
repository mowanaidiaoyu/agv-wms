import { describe, expect, it } from 'vitest'
import {
  SHUTTLE_RACK_LEVEL_COUNT,
  SHUTTLE_TRACK_DECK_THICKNESS,
  shuttleLevelToWorldY,
  shuttleRackTopWorldY,
} from '../../lib/topology3d'
import {
  SHUTTLE_ENDPOINT_MARKER_OFFSET,
  SHUTTLE_PATH_LINE_OFFSET,
  shuttleLiftShaftBounds,
  shuttleTrackMeshCenterY,
  shuttleVehicleBounds,
} from './shuttleGeometry'

describe('Shuttle internal rack geometry contract', () => {
  const surfaces = Array.from(
    { length: SHUTTLE_RACK_LEVEL_COUNT },
    (_, index) => shuttleLevelToWorldY(index + 1),
  )

  it('keeps every track deck top on its shared running surface', () => {
    for (const surface of surfaces) {
      expect(shuttleTrackMeshCenterY(surface) + SHUTTLE_TRACK_DECK_THICKNESS / 2).toBeCloseTo(surface)
      expect(SHUTTLE_PATH_LINE_OFFSET).toBeGreaterThan(SHUTTLE_TRACK_DECK_THICKNESS / 2)
      expect(SHUTTLE_ENDPOINT_MARKER_OFFSET).toBeGreaterThan(SHUTTLE_TRACK_DECK_THICKNESS / 2)
    }
  })

  it('puts the wheels on the track and keeps the complete L4 vehicle below the rack top', () => {
    for (const surface of surfaces) expect(shuttleVehicleBounds(surface).bottomY).toBeCloseTo(surface)
    const l4 = shuttleVehicleBounds(surfaces[3])
    expect(l4.topY).toBeLessThan(shuttleRackTopWorldY() - SHUTTLE_TRACK_DECK_THICKNESS / 2)
    expect(shuttleRackTopWorldY() - SHUTTLE_TRACK_DECK_THICKNESS / 2 - l4.topY).toBeGreaterThan(0.4)
  })

  it('keeps the four-level lift shaft inside the rack vertical envelope', () => {
    const shaft = shuttleLiftShaftBounds(surfaces)!
    expect(shaft.bottomY).toBeGreaterThanOrEqual(0)
    expect(shaft.topY).toBeLessThan(shuttleRackTopWorldY())
  })
})
