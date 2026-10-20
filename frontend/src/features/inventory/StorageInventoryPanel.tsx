import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, MapPin, PackageSearch, RefreshCw } from 'lucide-react'
import { api } from '../../lib/api'
import type { StorageInventoryLocation } from './types'

interface StorageInventoryPanelProps {
  topologyId: string
  selectedStorageNodeId: string | null
  onSelectStorageNode: (storageNodeId: string) => void
  onLocateStorageNode?: (location: StorageInventoryLocation) => void
  onLocateAccessCell?: (accessCellId: string) => void
}

function formatSource(source: string): string {
  if (source === 'synthetic_demo') return '确定性演示数据（synthetic_demo）'
  if (source === 'manual') return '人工录入（manual）'
  if (source === 'wms_import') return 'WMS 导入（wms_import）'
  return source
}

export function StorageInventoryPanel({
  topologyId,
  selectedStorageNodeId,
  onSelectStorageNode,
  onLocateStorageNode,
  onLocateAccessCell,
}: StorageInventoryPanelProps) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [zoneType, setZoneType] = useState<'all' | 'ctu' | 'shuttle'>('all')
  const [supplementCount, setSupplementCount] = useState(100)
  const [fillAll, setFillAll] = useState(false)

  const summaryQuery = useQuery({
    queryKey: ['inventory-summary', topologyId],
    queryFn: () => api.getInventorySummary(topologyId),
    enabled: Boolean(topologyId),
  })
  const locationsQuery = useQuery({
    queryKey: ['inventory-locations', topologyId],
    queryFn: () => api.listInventoryLocations(topologyId),
    enabled: Boolean(topologyId),
  })
  const detailQuery = useQuery({
    queryKey: ['inventory-location', topologyId, selectedStorageNodeId],
    queryFn: () => api.getInventoryLocation(topologyId, selectedStorageNodeId!),
    enabled: Boolean(topologyId && selectedStorageNodeId),
  })
  const seedMutation = useMutation({
    mutationFn: () => api.initializeSyntheticInventory(topologyId, 42),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inventory-summary', topologyId] })
      await queryClient.invalidateQueries({ queryKey: ['inventory-locations', topologyId] })
      await queryClient.invalidateQueries({ queryKey: ['inventory-location', topologyId] })
    },
  })
  const supplementPreviewQuery = useQuery({
    queryKey: ['inventory-supplement-preview', topologyId, supplementCount, fillAll],
    queryFn: () => api.previewSyntheticInventorySupplement(topologyId, fillAll ? null : supplementCount, fillAll),
    enabled: Boolean(topologyId) && supplementCount > 0,
  })
  const supplementMutation = useMutation({
    mutationFn: () => api.supplementSyntheticInventory(topologyId, fillAll ? null : supplementCount, fillAll, 42),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inventory-summary', topologyId] })
      await queryClient.invalidateQueries({ queryKey: ['inventory-locations', topologyId] })
      await queryClient.invalidateQueries({ queryKey: ['inventory-location', topologyId] })
      await queryClient.invalidateQueries({ queryKey: ['inventory-supplement-preview', topologyId] })
    },
  })

  const filteredLocations = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase()
    return (locationsQuery.data ?? []).filter((location) => {
      if (zoneType !== 'all' && location.zone_type !== zoneType) return false
      if (!needle) return true
      if (location.storage_node_id.toLocaleLowerCase().includes(needle)) return true
      return location.balances.some(
        (balance) => balance.sku.sku_id.toLocaleLowerCase().includes(needle)
          || balance.sku.sku_name.toLocaleLowerCase().includes(needle),
      )
    })
  }, [locationsQuery.data, search, zoneType])

  const summary = summaryQuery.data
  const detail = detailQuery.data
  const sources = summary?.data_sources ?? []

  return (
    <section className="inventory-panel" aria-label="库位库存">
      <div className="inventory-source-banner">
        <Database size={15} />
        <span>数据来源：{sources.length ? sources.map(formatSource).join('、') : '尚无库存数据'}</span>
      </div>

      {summary ? (
        <div className="inventory-summary-grid">
          <span>库位<strong>{summary.storage_location_count}</strong></span>
          <span>有记录<strong>{summary.stocked_location_count}</strong></span>
          <span>正库存<strong>{summary.positive_inventory_location_count}</strong></span>
          <span>未配置<strong>{summary.unconfigured_location_count}</strong></span>
          <span>零库存<strong>{summary.zero_inventory_location_count}</strong></span>
          <span>SKU<strong>{summary.sku_count}</strong></span>
          <span>现有<strong>{summary.quantity_on_hand}</strong></span>
          <span>预留<strong>{summary.quantity_reserved}</strong></span>
          <span>可用<strong>{summary.quantity_available}</strong></span>
        </div>
      ) : null}

      {summaryQuery.isError || locationsQuery.isError ? (
        <p className="inventory-warning" role="alert">
          库存加载失败：{String(summaryQuery.error ?? locationsQuery.error)}
        </p>
      ) : null}

      {summary && summary.stocked_location_count === 0 ? (
        <div className="inventory-empty-callout">
          <p>当前拓扑尚无库存记录。演示初始化只会关联真实 Storage Node，不会创建道路或库位。</p>
          <button
            type="button"
            className="secondary-button"
            disabled={seedMutation.isPending}
            onClick={() => seedMutation.mutate()}
          >
            <RefreshCw size={14} />
            {seedMutation.isPending ? '正在初始化…' : '初始化 synthetic_demo 库存'}
          </button>
        </div>
      ) : null}

      {summary && summary.unconfigured_location_count > 0 ? (
        <div className="inventory-supplement-card">
          <strong>补充演示库存</strong>
          <label>
            <input type="checkbox" checked={fillAll} onChange={(event) => setFillAll(event.target.checked)} />
            补充至全部现有 CTU Storage Node
          </label>
          {!fillAll ? (
            <label>
              指定补充库位数
              <input
                aria-label="指定补充库位数"
                type="number"
                min={1}
                value={supplementCount}
                onChange={(event) => setSupplementCount(Math.max(1, Math.floor(Number(event.target.value) || 1)))}
              />
            </label>
          ) : null}
          {supplementPreviewQuery.data ? (
            <p>
              当前可补充 {supplementPreviewQuery.data.eligible_location_count} 个；本次预计新增{' '}
              {supplementPreviewQuery.data.estimated_insert_count} 个，不覆盖已有记录。
            </p>
          ) : null}
          <button
            type="button"
            className="secondary-button"
            disabled={supplementMutation.isPending || !supplementPreviewQuery.data?.estimated_insert_count}
            onClick={() => supplementMutation.mutate()}
          >
            {supplementMutation.isPending ? '正在补充…' : '补充演示库存'}
          </button>
          {supplementMutation.data ? (
            <p className="inventory-access-ok" role="status">
              已新增 {supplementMutation.data.inserted_locations} 个库位；覆盖率{' '}
              {supplementMutation.data.after.stocked_location_count}/{supplementMutation.data.after.storage_location_count}。
            </p>
          ) : null}
          {supplementMutation.isError ? <p className="inventory-warning">补充失败：{String(supplementMutation.error)}</p> : null}
        </div>
      ) : null}

      <label className="inventory-search">
        <PackageSearch size={15} />
        <input
          aria-label="搜索 SKU 或库位"
          placeholder="搜索 SKU 或库位 ID"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>
      <div className="inventory-zone-filter" role="group" aria-label="库区筛选">
        {(['all', 'ctu', 'shuttle'] as const).map((value) => (
          <button
            type="button"
            key={value}
            className={zoneType === value ? 'active' : ''}
            onClick={() => setZoneType(value)}
          >
            {value === 'all' ? '全部' : value === 'ctu' ? 'CTU 库区' : '四向车库区'}
          </button>
        ))}
      </div>

      <div className="inventory-location-list" aria-label="库存库位列表">
        {filteredLocations.slice(0, 80).map((location) => (
          <button
            type="button"
            key={location.storage_node_id}
            className={selectedStorageNodeId === location.storage_node_id ? 'active' : ''}
            onClick={() => {
              onSelectStorageNode(location.storage_node_id)
              onLocateStorageNode?.(location)
            }}
          >
            <span>{location.storage_node_id}</span>
            <small>{location.balances.length ? `${location.balances.length} 种 SKU` : '暂无库存记录'}</small>
          </button>
        ))}
        {filteredLocations.length > 80 ? <p className="muted-copy">仅显示前 80 个结果，请继续搜索缩小范围。</p> : null}
        {!locationsQuery.isLoading && filteredLocations.length === 0 ? <p className="muted-copy">没有符合条件的库位。</p> : null}
      </div>

      <div className="inventory-location-detail" aria-live="polite">
        {!selectedStorageNodeId ? (
          <p className="muted-copy">请在 3D 场景或列表中选择一个真实库位。</p>
        ) : detailQuery.isLoading ? (
          <p className="muted-copy">正在读取库位库存…</p>
        ) : detail ? (
          <>
            <h3><MapPin size={16} />{detail.label}</h3>
            <dl>
              <div><dt>库位 ID</dt><dd>{detail.storage_node_id}</dd></div>
              <div><dt>库区</dt><dd>{detail.zone_label ?? detail.zone_id ?? '未分区'}</dd></div>
              <div><dt>位置</dt><dd>{detail.floor} · R{detail.row} C{detail.col}</dd></div>
            </dl>
            {detail.balances.length ? detail.balances.map((balance) => (
              <article className="inventory-balance-card" key={`${balance.storage_layer}-${balance.sku.sku_id}`}>
                <strong>{balance.sku.sku_name}</strong>
                <small>{balance.sku.sku_id} · {balance.sku.specification}</small>
                <div>
                  <span>现有 {balance.quantity_on_hand}</span>
                  <span>预留 {balance.quantity_reserved}</span>
                  <span>可用 {balance.quantity_available} {balance.sku.unit}</span>
                </div>
                <small>来源：{formatSource(balance.source)}</small>
              </article>
            )) : <p className="inventory-warning">暂无库存记录</p>}
            {detail.access_mapping.status === 'configured' ? (
              <div className="inventory-access-ok">
                <p>取货停靠点：{detail.access_mapping.access_cell_id}</p>
                {detail.access_mapping.access_cell_id && onLocateAccessCell ? (
                  <button type="button" className="ghost-button" onClick={() => onLocateAccessCell(detail.access_mapping.access_cell_id!)}>
                    查看停靠点
                  </button>
                ) : null}
              </div>
            ) : detail.access_mapping.status === 'topology_version_mismatch' ? (
              <p className="inventory-warning">停靠点映射对应旧拓扑版本，需要重新校验。</p>
            ) : (
              <p className="inventory-warning">尚未配置合法 CTU 取货停靠点，不能生成取货路径。</p>
            )}
          </>
        ) : (
          <p className="inventory-warning">库位详情加载失败：{String(detailQuery.error)}</p>
        )}
      </div>
    </section>
  )
}
