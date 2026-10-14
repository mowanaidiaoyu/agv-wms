import { useState } from 'react'
import { Surface } from '../../components/Surface'
import { AbcClassificationPanel } from './AbcClassificationPanel'
import { HeatPredictionPanel } from './HeatPredictionPanel'
import type { ABCResponse } from './types'
import './SkuInventoryLab.css'

type InventoryTab = 'abc' | 'heat'

export function SkuInventoryLab() {
  const [activeTab, setActiveTab] = useState<InventoryTab>('abc')
  const [classification, setClassification] = useState<ABCResponse | null>(null)

  return (
    <Surface
      className="sku-inventory-lab"
      title="库存预测与优化"
      subtitle="动态 ABC 与下一自然日 SKU 出库数量预测；当前仅用于算法验证。"
    >
      <div className="sku-source-notice" role="note">
        <strong>合成演示数据</strong>
        <span>数据源：synthetic_demo</span>
        <p>当前结果不代表实际仓储订单、库存或库位热度，未与拓扑 666 的物理库位关联，也不会直接控制 CTU 搬货。</p>
      </div>
      <div className="sku-lab-tabs" role="tablist" aria-label="库存预测算法">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'abc'}
          className={activeTab === 'abc' ? 'is-active' : ''}
          onClick={() => setActiveTab('abc')}
        >
          动态 ABC 分类
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'heat'}
          className={activeTab === 'heat' ? 'is-active' : ''}
          onClick={() => setActiveTab('heat')}
        >
          SKU 次日热度预测
        </button>
      </div>
      <AbcClassificationPanel active={activeTab === 'abc'} onResult={setClassification} />
      <HeatPredictionPanel active={activeTab === 'heat'} classification={classification} />
    </Surface>
  )
}
