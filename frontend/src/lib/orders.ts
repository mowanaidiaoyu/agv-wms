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
  // 一张普通业务订单包含 14 箱货，并声明“并行优先”。后端会根据库存位置
  // 把它拆成 7 个 CTU 执行任务，多车联动由订单编排自然产生。
  return [{
    id: 'ORD-DEMO-001',
    priority: 'normal',
    dispatchMode: 'parallel',
    maxParallelCtu: 7,
    targetStation: '出库口 01',
    createdAt: now,
    lines: catalog.slice(0, 7).map((sku, index) => line(sku, index, 2)),
  }]
}
