import type { ColorRepresentation } from 'three'
import type { SceneBox, SceneCylinder, SceneEntity, SceneRack } from './topology3d'

export type TopologyInstancePart = {
  key: string
  sourceKey: string
  position: [number, number, number]
  size: [number, number, number]
  color: string
  /** 实例自身的旋转（弧度），用于滚筒等圆柱构件的轴向 */
  rotation?: [number, number, number]
  entity?: SceneEntity
}

export type TopologyInstanceStyle = {
  wire?: boolean
  opacity: number
  emissive?: ColorRepresentation
  emissiveIntensity?: number
  roughness: number
  metalness: number
}

export type TopologyInstanceGroup = {
  key: string
  style: TopologyInstanceStyle
  /** box 用单位立方体；cylinder 用单位圆柱（半径 0.5、高 1） */
  shape: 'box' | 'cylinder'
  parts: TopologyInstancePart[]
}

/** 将编辑器中的普通方块、货架构件与圆柱构件按材质归并成少量实例组。 */
export function buildTopologyInstanceGroups(
  boxes: SceneBox[],
  racks: SceneRack[],
  cylinders: SceneCylinder[] = [],
): TopologyInstanceGroup[] {
  const groups = new Map<string, TopologyInstanceGroup>()
  const add = (
    groupKey: string,
    style: TopologyInstanceStyle,
    part: TopologyInstancePart,
    shape: 'box' | 'cylinder' = 'box',
  ) => {
    const existing = groups.get(groupKey)
    if (existing) existing.parts.push(part)
    else groups.set(groupKey, { key: groupKey, style, shape, parts: [part] })
  }

  for (const box of boxes) {
    const opacity = box.wire ? 0.9 : (box.opacity ?? 1)
    const groupKey = box.wire
      ? 'box-wire'
      : `box-standard:${opacity}:${String(box.emissive ?? '')}`
    add(
      groupKey,
      {
        wire: box.wire,
        opacity,
        emissive: box.wire ? undefined : box.emissive,
        emissiveIntensity: box.emissive ? 0.25 : 0,
        roughness: 0.7,
        metalness: 0.15,
      },
      {
        key: box.key,
        sourceKey: box.key,
        position: box.position,
        size: box.size,
        color: box.color,
        entity: box.entity,
      },
    )
  }

  for (const rack of racks) {
    const rackHeight = rack.levels * rack.levelHeight
    const step = rack.slotSize || 1
    const left = rack.x - rack.cellsW / 2
    const right = left + rack.cellsW
    const top = rack.z + rack.cellsD / 2
    const bottom = rack.z - rack.cellsD / 2
    const xs: number[] = []
    const zs: number[] = []
    for (let i = 0; i <= rack.cellsW; i += step) xs.push(left + i)
    if (xs[xs.length - 1] !== right) xs.push(right)
    for (let i = 0; i <= rack.cellsD; i += step) zs.push(bottom + i)
    if (zs[zs.length - 1] !== top) zs.push(top)

    xs.forEach((x, xi) => {
      zs.forEach((z, zi) => {
        add(
          'rack-post',
          { opacity: 1, roughness: 0.7, metalness: 0.25 },
          {
            key: `p-${rack.key}-${xi}-${zi}`,
            sourceKey: rack.key,
            position: [x, rack.y + rackHeight / 2, z],
            size: [0.08, rackHeight, 0.08],
            color: rack.color,
            entity: rack.entity,
          },
        )
      })
    })

    for (let level = 1; level <= rack.levels; level += 1) {
      add(
        'rack-shelf',
        { opacity: 0.42, roughness: 0.85, metalness: 0 },
        {
          key: `s-${rack.key}-${level}`,
          sourceKey: rack.key,
          position: [rack.x, rack.y + level * rack.levelHeight - 0.05, rack.z],
          size: [rack.cellsW * 0.96, 0.06, rack.cellsD * 0.96],
          color: rack.levelColors?.[level - 1] ?? rack.color,
          entity: rack.entity,
        },
      )
    }
  }

  for (const cylinder of cylinders) {
    add(
      `cyl:${cylinder.color}`,
      { opacity: 1, roughness: 0.35, metalness: 0.65 },
      {
        key: cylinder.key,
        sourceKey: cylinder.key,
        position: cylinder.position,
        // 单位圆柱：半径 0.5、高 1，按筒径与筒长缩放
        size: [cylinder.radius * 2, cylinder.length, cylinder.radius * 2],
        rotation: cylinder.axis === 'x' ? [0, 0, Math.PI / 2] : [Math.PI / 2, 0, 0],
        color: cylinder.color,
        entity: cylinder.entity,
      },
      'cylinder',
    )
  }

  return [...groups.values()]
}
