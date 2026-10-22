import type { GoodsCatalogSku, SimulationOrderInput, SimulationOrderLineInput } from '../types'

export function createDemoOrders(catalog: GoodsCatalogSku[]): SimulationOrderInput[] {
  if (!catalog.length) return []
  const now = new Date().toISOString()
  const line = (sku: GoodsCatalogSku, index: number, quantity = 1): SimulationOrderLineInput => ({
    id: `line-${index + 1}`,
    skuId: sku.skuId,
    skuName: sku.name,
    spec: sku.spec,
    quantity,
  })
  const first = catalog.slice(0, 3)
  const second = catalog.slice(5, 7)
  return [
    {
      id: 'ORD-DEMO-001',
      priority: 'normal',
      targetStation: '出库口 01',
      createdAt: now,
      lines: first.map((sku, index) => line(sku, index, index === 0 ? 2 : 1)),
    },
    {
      id: 'ORD-DEMO-002',
      priority: 'urgent',
      targetStation: '出库口 01',
      createdAt: now,
      lines: second.map((sku, index) => line(sku, index)),
    },
  ]
}
