import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, MapPin, PackageSearch, RefreshCw } from 'lucide-react'
import { api } from '../../lib/api'
import type {
  CtuRoadSemanticAudit,
  StorageAccessAudit,
  StorageAccessCandidateList,
  StorageInventoryLocation,
} from './types'

interface StorageInventoryPanelProps {
  topologyId: string
  selectedStorageNodeId: string | null
  onSelectStorageNode: (storageNodeId: string) => void
  onLocateStorageNode?: (location: StorageInventoryLocation) => void
  onLocateAccessCell?: (accessCellId: string) => void
  accessAudit?: StorageAccessAudit
  accessCandidates?: StorageAccessCandidateList
  accessCandidatesLoading?: boolean
  accessCandidatesError?: string | null
  selectedAccessCellId?: string | null
  onSelectAccessCandidate?: (accessCellId: string) => void
  ctuRoadAudit?: CtuRoadSemanticAudit
  roadSemanticOverlayEnabled?: boolean
  onToggleRoadSemanticOverlay?: () => void
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
  accessAudit,
  accessCandidates,
  accessCandidatesLoading = false,
  accessCandidatesError = null,
  selectedAccessCellId = null,
  onSelectAccessCandidate,
  ctuRoadAudit,
  roadSemanticOverlayEnabled = false,
  onToggleRoadSemanticOverlay,
}: StorageInventoryPanelProps) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [zoneType, setZoneType] = useState<'all' | 'ctu' | 'shuttle'>('all')
  const [supplementCount, setSupplementCount] = useState(100)
  const [fillAll, setFillAll] = useState(false)
  const [reviewConfirmed, setReviewConfirmed] = useState(false)

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
  const configureAccessMutation = useMutation({
    mutationFn: (accessCellId: string) => api.configureStorageAccessMapping(topologyId, selectedStorageNodeId!, accessCellId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inventory-location', topologyId, selectedStorageNodeId] })
      await queryClient.invalidateQueries({ queryKey: ['inventory-locations', topologyId] })
      await queryClient.invalidateQueries({ queryKey: ['storage-access-audit', topologyId] })
      setReviewConfirmed(false)
    },
  })
  const draftsQuery = useQuery({
    queryKey: ['ctu-road-drafts', topologyId],
    queryFn: () => api.listCtuRoadDrafts(topologyId),
    enabled: Boolean(topologyId),
  })
  const createDraftMutation = useMutation({
    mutationFn: () => api.createCtuRoadDraft(topologyId, '最小 CTU 取货试验区，等待研究生 B 审核'),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['ctu-road-drafts', topologyId] })
      await queryClient.invalidateQueries({ queryKey: ['ctu-road-audit', topologyId] })
    },
  })
  const resetConfigureAccessMutation = configureAccessMutation.reset

  useEffect(() => {
    setReviewConfirmed(false)
    resetConfigureAccessMutation()
  }, [resetConfigureAccessMutation, selectedAccessCellId, selectedStorageNodeId])

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
  const selectedCandidate = accessCandidates?.candidates.find(
    (candidate) => candidate.access_cell_id === selectedAccessCellId,
  ) ?? null
  const auditExportHref = useMemo(() => {
    if (!ctuRoadAudit) return '#'
    const reviewPackage = {
      contract_version: 'ctu-road-pick-contract/v1',
      topology_id: ctuRoadAudit.topology_id,
      topology_revision: ctuRoadAudit.topology_revision,
      review_status: 'draft',
      eligible_for_planning: false,
      road_counts: ctuRoadAudit.road_counts,
      trial_area: ctuRoadAudit.trial_area,
      warnings: ctuRoadAudit.warnings,
      reviewer_questions: [
        '哪些蓝色巷道对应 CTU road？',
        '通道宽度及车辆占用约束是什么？',
        '每个货架应从哪一侧取货？',
        'CTU 停靠时占用哪些道路空间？',
        '哪些巷道属于同一个可连通区域？',
        '不同层库位是否共用平面停靠点？',
        '是否存在额外取货操作距离约束？',
        '是否需要增加站台或车辆起始节点？',
      ],
    }
    return `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(reviewPackage, null, 2))}`
  }, [ctuRoadAudit])

  return (
    <section className="inventory-panel" aria-label="库位库存">
      <div className="inventory-source-banner">
        <Database size={15} />
        <span>数据来源：{sources.length ? sources.map(formatSource).join('、') : '尚无库存数据'}</span>
      </div>

      {accessAudit ? (
        <div className="inventory-access-audit" aria-label="取货停靠点审计">
          <strong>取货停靠点审计</strong>
          <span>可验证 {accessAudit.eligible_location_count}</span>
          <span>待人工审核 {accessAudit.review_required_location_count}</span>
          <span>当前不可映射 {accessAudit.unmappable_location_count}</span>
          <span>已保存 {accessAudit.configured_mapping_count}</span>
        </div>
      ) : null}

      {ctuRoadAudit ? (
        <section className="inventory-road-audit" aria-label="CTU 道路语义审计">
          <div className="inventory-road-audit-heading">
            <strong>CTU 道路语义审计</strong>
            <span className="inventory-unverified">UNVERIFIED</span>
          </div>
          <p>颜色仅用于 3D 显示，不授予车辆通行权限。</p>
          <div className="inventory-road-counts">
            <span>CTU road <strong>{ctuRoadAudit.road_counts.ctu}</strong></span>
            <span>四向车 road <strong>{ctuRoadAudit.road_counts.shuttle}</strong></span>
            <span>未归属 road <strong>{ctuRoadAudit.road_counts.unassigned}</strong></span>
            <span>可用于 CTU <strong>{ctuRoadAudit.ctu_road_eligible_count}</strong></span>
          </div>
          <div className="inventory-road-legend" aria-label="道路语义图例">
            <span className="is-ctu">CTU road</span>
            <span className="is-shuttle">四向车 road</span>
            <span className="is-unassigned">未归属 road</span>
          </div>
          <button type="button" className="ghost-button" onClick={onToggleRoadSemanticOverlay}>
            {roadSemanticOverlayEnabled ? '关闭道路语义图层' : '高亮道路语义图层'}
          </button>
          {ctuRoadAudit.trial_area ? (
            <div className="inventory-trial-area">
              <strong>最小取货试验区草案</strong>
              <span>道路 {ctuRoadAudit.trial_area.road_cell_ids.length} 格 · 库位 {ctuRoadAudit.trial_area.storage_node_ids.length} 个</span>
              <span>起始 Cell：{ctuRoadAudit.trial_area.vehicle_start_cell_id}</span>
              <span>Pick Face：unknown · Access Cell：未指定</span>
              <p>该草案尚未经过研究生 B 审核，不能用于正式规划。</p>
              <div className="inventory-trial-actions">
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => onLocateAccessCell?.(ctuRoadAudit.trial_area!.vehicle_start_cell_id)}
                >
                  定位试验区起点
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={createDraftMutation.isPending}
                  onClick={() => createDraftMutation.mutate()}
                >
                  {createDraftMutation.isPending ? '正在保存草案…' : '保存独立审核草案'}
                </button>
                <a
                  className="ghost-button"
                  href={auditExportHref}
                  download={`ctu-road-review-${ctuRoadAudit.topology_revision}.json`}
                >
                  导出待 B 审核清单
                </a>
              </div>
              <small>状态说明：draft 待审核；verified 才可进入正式映射；rejected 禁止使用。</small>
              <small>已保存草案：{draftsQuery.data?.length ?? ctuRoadAudit.saved_draft_count}</small>
              {createDraftMutation.data ? <p className="inventory-access-ok" role="status">草案已隔离保存，状态仍为 draft。</p> : null}
              {createDraftMutation.isError ? <p className="inventory-warning">草案保存失败：{String(createDraftMutation.error)}</p> : null}
            </div>
          ) : <p className="inventory-warning">没有可由现有真实 Road Cell 组成的最小试验区草案。</p>}
        </section>
      ) : null}

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
            ) : detail.access_mapping.status === 'invalid' ? (
              <p className="inventory-warning">已保存停靠点不再合法：{detail.access_mapping.validation_message}</p>
            ) : (
              <p className="inventory-warning">尚未配置合法 CTU 取货停靠点，不能生成取货路径。</p>
            )}

            <section className="inventory-access-review" aria-label="候选停靠点审核">
              <h4>候选 CTU 停靠点</h4>
              <p className="muted-copy">候选只来自真实 road Cell；红色库位本身不会成为车辆终点。</p>
              {accessCandidatesLoading ? <p className="muted-copy">正在审计真实道路…</p> : null}
              {accessCandidatesError ? <p className="inventory-warning">候选读取失败：{accessCandidatesError}</p> : null}
              {accessCandidates ? (
                <>
                  <p className="inventory-access-counts">
                    可验证 {accessCandidates.eligible_count} · 待审核 {accessCandidates.review_required_count} · 已拒绝 {accessCandidates.rejected_count}
                  </p>
                  {accessCandidates.eligible_count + accessCandidates.review_required_count === 0 ? (
                    <p className="inventory-warning">当前拓扑没有可保存候选。请补充真实 CTU road、库区归属和取货面语义，不能用最近道路代替。</p>
                  ) : null}
                  <div className="inventory-access-candidates">
                    {accessCandidates.candidates.map((candidate) => (
                      <button
                        type="button"
                        key={candidate.access_cell_id}
                        className={selectedAccessCellId === candidate.access_cell_id ? 'active' : ''}
                        onClick={() => {
                          onSelectAccessCandidate?.(candidate.access_cell_id)
                          onLocateAccessCell?.(candidate.access_cell_id)
                        }}
                      >
                        <span>{candidate.access_cell_id}</span>
                        <small>
                          {candidate.status === 'eligible' ? '可验证' : candidate.status === 'review_required' ? '待人工审核' : '不可用'}
                          {' · '}距离 {candidate.distance_cells ?? '—'} 格
                        </small>
                      </button>
                    ))}
                  </div>
                </>
              ) : null}
              {selectedCandidate ? (
                <div className={`inventory-candidate-review is-${selectedCandidate.status}`}>
                  <strong>{selectedCandidate.access_cell_id}</strong>
                  <ul>{selectedCandidate.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                  {selectedCandidate.status !== 'rejected' ? (
                    <label>
                      <input
                        type="checkbox"
                        checked={reviewConfirmed}
                        onChange={(event) => setReviewConfirmed(event.target.checked)}
                      />
                      我已核对货架取货面、道路归属和现场通行空间
                    </label>
                  ) : null}
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={selectedCandidate.status === 'rejected' || !reviewConfirmed || configureAccessMutation.isPending}
                    onClick={() => configureAccessMutation.mutate(selectedCandidate.access_cell_id)}
                  >
                    {configureAccessMutation.isPending ? '正在校验保存…' : '审核并保存正式映射'}
                  </button>
                  {configureAccessMutation.data ? <p className="inventory-access-ok" role="status">正式映射已保存。</p> : null}
                  {configureAccessMutation.isError ? <p className="inventory-warning">保存失败：{String(configureAccessMutation.error)}</p> : null}
                </div>
              ) : null}
            </section>
          </>
        ) : (
          <p className="inventory-warning">库位详情加载失败：{String(detailQuery.error)}</p>
        )}
      </div>
    </section>
  )
}
