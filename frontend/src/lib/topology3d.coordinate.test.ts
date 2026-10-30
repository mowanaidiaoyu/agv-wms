import { describe, expect, it } from 'vitest'
import { emptyTopology } from '../data/topologyDefaults'
import { paintCell, placeNode } from './topology'
import { buildGroundBeltLookup, buildSceneSpec, shuttleGroundSnapPosition, shuttleTrackWorldPosition } from './topology3d'

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

  it('renders a turn transfer only on its selected conveyor deck', () => {
    let topology = paintCell(emptyTopology(), 'conveyor', 0, 0).topology
    topology = {
      ...topology,
      cells: topology.cells.map((cell) => ({ ...cell, levels: 2 })),
    }
    topology = placeNode(topology, 'turn_transfer', 0, 0, '1F')!.topology
    const transfer = topology.nodes.find((node) => node.type === 'turn_transfer')!

    const upperKeys = buildSceneSpec(topology).items.map((item) => item.key)
    expect(upperKeys).toContain(`${transfer.id}-frame-1`)
    expect(upperKeys).not.toContain(`${transfer.id}-frame-0`)
    expect(upperKeys).toContain(`${transfer.id}-input-direction`)
    expect(upperKeys).toContain(`${transfer.id}-output-direction`)

    transfer.transferDeck = 'lower'
    const lowerKeys = buildSceneSpec(topology).items.map((item) => item.key)
    expect(lowerKeys).toContain(`${transfer.id}-frame-0`)
    expect(lowerKeys).not.toContain(`${transfer.id}-frame-1`)
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

  it('treats shuttle_dock gates as snap structures via extra anchors', () => {
    // 出货位闸口 (9,8) 占 2×2，闸口块属于纵向结构（与 (7,8)/(5,8) 伪锚点相连时按纵向；
    // 单独闸口无 ±2 邻居时回退纵向）。闸口内四格的横坐标都吸附到 cols 8/9 分界。
    const dockCells = [
      { row: 9, col: 8, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 9, col: 8 } },
      { row: 9, col: 9, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 9, col: 8 } },
      { row: 10, col: 8, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 9, col: 8 } },
      { row: 10, col: 9, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 9, col: 8 } },
    ]
    const dockLookup = buildGroundBeltLookup(dockCells, [
      { row: 7, col: 8 },
      { row: 5, col: 8 },
    ])
    expect(dockLookup.get('9,8')!.horizontal).toBe(false)
    const onSecondCol = shuttleGroundSnapPosition(9, 9, dockLookup.get('9,9')!, 40, 100)
    expect(onSecondCol[0]).toBe(9 - 40 / 2)
    expect(onSecondCol[2]).toBe(shuttleTrackWorldPosition(9, 9, 1, 40, 100)[2])
  })

  it('snaps aisle slots so the body fully occupies the slot lane', () => {
    // 巷道槽位 (8,0)/(8,2) 横向相邻：跨巷方向吸附到槽位中心线，沿巷方向逐格连续。
    const aisleCells = [
      { row: 8, col: 0, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 8, col: 0 } },
      { row: 8, col: 1, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 8, col: 0 } },
      { row: 9, col: 0, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 8, col: 0 } },
      { row: 9, col: 1, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 8, col: 0 } },
      { row: 8, col: 2, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 8, col: 2 } },
      { row: 8, col: 3, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 8, col: 2 } },
      { row: 9, col: 2, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 8, col: 2 } },
      { row: 9, col: 3, type: 'conveyor', conveyorGround: true, conveyorAnchor: { row: 8, col: 2 } },
    ]
    const aisleLookup = buildGroundBeltLookup(aisleCells)
    expect(aisleLookup.get('8,0')!.horizontal).toBe(true)
    const onSecondRow = shuttleGroundSnapPosition(9, 0, aisleLookup.get('9,0')!, 40, 100)
    expect(onSecondRow[2]).toBe(100 / 2 - 9)
    const onFirstRow = shuttleGroundSnapPosition(8, 0, aisleLookup.get('8,0')!, 40, 100)
    expect(onFirstRow[2]).toBe(100 / 2 - 9)
  })
})
