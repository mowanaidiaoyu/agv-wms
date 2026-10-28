import { describe, expect, it } from 'vitest'
import { buildGroundBeltLookup, shuttleGroundSnapPosition, shuttleTrackWorldPosition } from './topology3d'

describe('shuttleTrackWorldPosition', () => {
  it('matches the backend shuttle-internal-levels-v1 coordinate contract', () => {
    const liftA = shuttleTrackWorldPosition(100, 19, 1, 129, 216)
    const liftB = shuttleTrackWorldPosition(188, 39, 4, 129, 216)

    expect(liftA[0]).toBe(-44.5)
    // 一层轨道面 = 基座 0.25 + 层内偏移 0.35 = 0.6，与地面输送线带面齐平
    expect(liftA[1]).toBeCloseTo(0.6)
    expect(liftA[2]).toBe(7)
    expect(liftB[0]).toBe(-24.5)
    expect(liftB[1]).toBeCloseTo(4.8)
    expect(liftB[2]).toBe(-81)
  })
})

describe('shuttleGroundSnapPosition', () => {
  // 横向地面线：块锚点 (74,17)，带面占 rows 74-75
  const cells = [
    { row: 74, col: 17, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 74, col: 17 } },
    { row: 74, col: 18, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 74, col: 17 } },
    { row: 75, col: 17, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 74, col: 17 } },
    { row: 75, col: 18, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 74, col: 17 } },
    { row: 74, col: 19, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 74, col: 19 } },
    { row: 74, col: 20, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 74, col: 19 } },
    { row: 75, col: 19, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 74, col: 19 } },
    { row: 75, col: 20, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 74, col: 19 } },
  ]
  const lookup = buildGroundBeltLookup(cells)

  it('snaps the cross-belt axis to the belt centerline on either row', () => {
    // 路径走在带面第一排 (74) 或第二排 (75)，横带坐标都必须落在 rows 74/75 的分界线上
    const onFirstRow = shuttleGroundSnapPosition(74, 20, lookup.get('74,20')!, 40, 100)
    const onSecondRow = shuttleGroundSnapPosition(75, 20, lookup.get('75,20')!, 40, 100)
    expect(onFirstRow[2]).toBe(100 / 2 - 75)
    expect(onSecondRow[2]).toBe(100 / 2 - 75)
    // 沿带方向保持逐格连续
    expect(onFirstRow[0]).toBe(shuttleTrackWorldPosition(74, 20, 1, 40, 100)[0])
    expect(onSecondRow[0]).toBe(shuttleTrackWorldPosition(75, 20, 1, 40, 100)[0])
  })

  it('detects belt direction from neighbouring blocks', () => {
    expect(lookup.get('74,20')!.horizontal).toBe(true)
  })

  it('snaps the cross-belt axis for vertical belts too', () => {
    // 纵向地面线：块锚点 (80,27)，带面占 cols 27-28
    const vertical = buildGroundBeltLookup([
      { row: 80, col: 27, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 80, col: 27 } },
      { row: 80, col: 28, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 80, col: 27 } },
      { row: 81, col: 27, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 80, col: 27 } },
      { row: 81, col: 28, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 80, col: 27 } },
      { row: 82, col: 27, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 82, col: 27 } },
      { row: 82, col: 28, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 82, col: 27 } },
      { row: 83, col: 27, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 82, col: 27 } },
      { row: 83, col: 28, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 82, col: 27 } },
    ])
    expect(vertical.get('80,27')!.horizontal).toBe(false)
    const onSecondCol = shuttleGroundSnapPosition(80, 28, vertical.get('80,28')!, 40, 100)
    // 横带坐标吸附到 cols 27/28 分界
    expect(onSecondCol[0]).toBe(28 - 40 / 2)
    expect(onSecondCol[2]).toBe(shuttleTrackWorldPosition(80, 28, 1, 40, 100)[2])
  })
})
