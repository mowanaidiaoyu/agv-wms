import { describe, expect, it } from 'vitest'
import { emptyTopology } from '../data/topologyDefaults'
import type { GoodsSlot } from '../types'
import { buildGoodsSceneBoxes, buildSceneSpec, type SceneBox, type SceneRack } from './topology3d'
import { buildTopologyInstanceGroups } from './topologyInstanceBatches'

describe('topology instance batches', () => {
  it('把方块与货架构件合批并保留拓扑对象映射', () => {
    const box: SceneBox = {
      kind: 'box',
      key: 'cell-r1-c1',
      position: [0, 0, 0],
      size: [1, 1, 1],
      color: '#ffffff',
      floor: '1F',
      entity: { kind: 'cell', id: 'cell-r1-c1', floor: '1F' },
    }
    const rack: SceneRack = {
      kind: 'rack',
      key: 'rack-a',
      x: 0,
      z: 0,
      y: 0,
      cellsW: 1,
      cellsD: 3,
      levels: 2,
      slotSize: 1,
      levelHeight: 0.5,
      color: '#0e7490',
      zoneType: 'ctu',
      label: 'A',
      floor: '1F',
      entity: { kind: 'zone', id: 'zone-a', floor: '1F' },
    }

    const groups = buildTopologyInstanceGroups([box], [rack])
    const parts = groups.flatMap((group) => group.parts)
    expect(groups).toHaveLength(3)
    expect(parts).toHaveLength(11)
    expect(parts.filter((part) => part.sourceKey === 'rack-a')).toHaveLength(10)
    expect(parts.filter((part) => part.entity?.id === 'zone-a')).toHaveLength(10)
    expect(parts.find((part) => part.sourceKey === box.key)?.entity).toEqual(box.entity)
  })

  it('商品层可独立派生，同时兼容仿真完整场景', () => {
    const topology = emptyTopology()
    topology.nodes.push({
      id: 'storage-1',
      type: 'storage',
      floor: '1F',
      row: 2,
      col: 3,
      zoneId: null,
      capacity: 1,
      label: '库位 1',
    })
    const goods: GoodsSlot[] = [
      { nodeId: 'storage-1', cellId: 'cell-r2-c3', layer: 1, largeCategory: '螺母' },
      { nodeId: 'storage-1', cellId: 'cell-r2-c3', layer: 2, largeCategory: '螺栓' },
    ]

    const goodsLayer = buildGoodsSceneBoxes(topology, goods)
    const fullSceneGoods = buildSceneSpec(topology, goods).items.filter(
      (item) => item.kind === 'box' && item.key.startsWith('goods-'),
    )
    expect(goodsLayer).toHaveLength(2)
    expect(fullSceneGoods).toEqual(goodsLayer)
  })
})
