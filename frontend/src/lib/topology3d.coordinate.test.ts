import { describe, expect, it } from 'vitest'
import { emptyTopology } from '../data/topologyDefaults'
import { paintCell, placeNode } from './topology'
import { buildSceneSpec, shuttleBlockWorldPosition, shuttleTrackWorldPosition } from './topology3d'

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

  it('centers the 2x2 vehicle body on its containing block', () => {
    // 块中心 = 四个格轨道中心的中点（车体完整覆盖块内四格）。
    const corners = [[92, 47], [92, 48], [93, 47], [93, 48]] as const
    const tracks = corners.map(([row, col]) => shuttleTrackWorldPosition(row, col, 1, 100, 216))
    const body = shuttleBlockWorldPosition(92, 47, 1, 100, 216)
    expect(body[0]).toBeCloseTo((Math.min(...tracks.map((t) => t[0])) + Math.max(...tracks.map((t) => t[0]))) / 2)
    expect(body[2]).toBeCloseTo((Math.min(...tracks.map((t) => t[2])) + Math.max(...tracks.map((t) => t[2]))) / 2)
    expect(body[1]).toBeCloseTo(tracks[0][1])
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
