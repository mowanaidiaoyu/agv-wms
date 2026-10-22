import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type {
  ShuttleDemoDropoffRecord,
  ShuttlePickFace,
  ShuttleStorageAuditResponse,
  ShuttleStorageCandidate,
  ShuttleStorageLocationRecord,
  ShuttleStorageTrialArea,
  ShuttleTrackNode,
} from './types'

interface ShuttleStorageAuditPanelProps {
  topologyId: string
  audit?: ShuttleStorageAuditResponse
  loading: boolean
  error: string | null
  candidates: ShuttleStorageCandidate[]
  candidateTotal: number
  candidatesLoading: boolean
  candidatesError: string | null
  trialArea?: ShuttleStorageTrialArea
  mappings: ShuttleStorageLocationRecord[]
  dropoffs: ShuttleDemoDropoffRecord[]
  tracks: ShuttleTrackNode[]
  level: number
  search: string
  selectedLocationId: string | null
  selectedAccessTrackNodeId: string | null
  onLevelChange: (level: number) => void
  onSearchChange: (search: string) => void
  onCandidateSelect: (candidate: ShuttleStorageCandidate) => void
  onAccessTrackSelect: (trackNodeId: string) => void
  onLocateCandidate: (candidate: ShuttleStorageCandidate) => void
  onLocateTrack: (trackNodeId: string) => void
}

const faceLabels: Record<ShuttlePickFace, string> = {
  north: '北侧', south: '南侧', east: '东侧', west: '西侧',
}

const statusLabels: Record<string, string> = {
  draft: '审核中', simulation_approved: '已通过仿真几何验证', rejected: '已拒绝',
  physical_verified: '物理验证通过', topology_version_mismatch: '拓扑版本失效',
}

export function ShuttleStorageAuditPanel(props: ShuttleStorageAuditPanelProps) {
  const {
    topologyId, audit, loading, error, candidates, candidateTotal, candidatesLoading,
    candidatesError, trialArea, mappings, dropoffs, tracks, level, search,
    selectedLocationId, selectedAccessTrackNodeId, onLevelChange, onSearchChange,
    onCandidateSelect, onAccessTrackSelect, onLocateCandidate, onLocateTrack,
  } = props
  const queryClient = useQueryClient()
  const selected = candidates.find((candidate) => candidate.location_id === selectedLocationId)
    ?? trialArea?.candidates.find((candidate) => candidate.location_id === selectedLocationId)
    ?? null
  const mapping = mappings.find((item) => item.location_id === selectedLocationId) ?? null
  const selectedOption = selected?.access_options.find(
    (option) => option.track_node_id === selectedAccessTrackNodeId,
  ) ?? null
  const [pickFace, setPickFace] = useState<ShuttlePickFace | ''>('')
  const [reviewer, setReviewer] = useState('')
  const [reviewNote, setReviewNote] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [dropoffTrackNodeId, setDropoffTrackNodeId] = useState('')
  const [dropoffLabel, setDropoffLabel] = useState('离线演示交接点')
  const [selectedDropoffId, setSelectedDropoffId] = useState('')
  const eligibleDropoffTracks = useMemo(
    () => tracks.filter((track) => track.track_type === 'straight' && track.neighbors.length >= 2),
    [tracks],
  )

  useEffect(() => {
    setPickFace(mapping?.pick_face ?? '')
    if (mapping?.access_track_node_id) onAccessTrackSelect(mapping.access_track_node_id)
    setConfirmed(false)
  }, [mapping?.access_track_node_id, mapping?.pick_face, onAccessTrackSelect, selectedLocationId])

  const historyQuery = useQuery({
    queryKey: ['shuttle-storage-review-history', topologyId, selectedLocationId],
    queryFn: () => api.getShuttleStorageReviewHistory(topologyId, selectedLocationId!),
    enabled: Boolean(mapping && selectedLocationId),
  })

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['shuttle-storage-candidates', topologyId] }),
      queryClient.invalidateQueries({ queryKey: ['shuttle-storage-mappings', topologyId] }),
      queryClient.invalidateQueries({ queryKey: ['shuttle-storage-review-history', topologyId] }),
      queryClient.invalidateQueries({ queryKey: ['shuttle-demo-dropoffs', topologyId] }),
    ])
  }
  const draftMutation = useMutation({
    mutationFn: () => api.saveShuttleStorageDraft(topologyId, {
      location_id: selected!.location_id,
      pick_face: pickFace as ShuttlePickFace,
      access_track_node_id: selectedAccessTrackNodeId!,
      reviewer: reviewer.trim(),
      review_note: reviewNote.trim() || undefined,
    }),
    onSuccess: refresh,
  })
  const reviewMutation = useMutation({
    mutationFn: (decision: 'simulation_approved' | 'rejected') => api.reviewShuttleStorageMapping(
      topologyId, selected!.location_id,
      {
        decision, reviewer: reviewer.trim(), review_note: reviewNote.trim() || undefined,
        explicit_confirmation: confirmed,
      },
    ),
    onSuccess: async () => { setConfirmed(false); await refresh() },
  })
  const dropoffDraftMutation = useMutation({
    mutationFn: () => api.saveShuttleDemoDropoffDraft(topologyId, {
      track_node_id: dropoffTrackNodeId, label: dropoffLabel.trim(),
      reviewer: reviewer.trim(), review_note: reviewNote.trim() || undefined,
    }),
    onSuccess: async (result) => { setSelectedDropoffId(result.dropoff_id); await refresh() },
  })
  const dropoffReviewMutation = useMutation({
    mutationFn: (decision: 'simulation_approved' | 'rejected') => api.reviewShuttleDemoDropoff(
      topologyId, selectedDropoffId,
      {
        decision, reviewer: reviewer.trim(), review_note: reviewNote.trim() || undefined,
        explicit_confirmation: confirmed,
      },
    ),
    onSuccess: async () => { setConfirmed(false); await refresh() },
  })
  const mutationError = [draftMutation.error, reviewMutation.error, dropoffDraftMutation.error, dropoffReviewMutation.error]
    .find(Boolean)

  return (
    <section className="shuttle-storage-audit" aria-label="四向车货位配置">
      <div className="shuttle-storage-audit-heading">
        <strong>四向车货位配置</strong>
        <span className={mappings.some((item) => item.execution_allowed) ? 'is-approved' : 'is-blocked'}>
          {mappings.some((item) => item.execution_allowed) ? '存在仿真可用映射' : '等待人工审核'}
        </span>
      </div>
      {loading ? <p>正在核对 Shuttle 货架、层级与轨道关系…</p> : null}
      {error ? <p role="alert">四向车货位审计加载失败：{error}</p> : null}
      {audit ? (
        <dl>
          <div><dt>CTU 正式库位</dt><dd>{audit.ctu_storage_node_count}</dd></div>
          <div><dt>Shuttle 正式库位</dt><dd>{audit.shuttle_storage_node_count}</dd></div>
          <div><dt>货架层候选</dt><dd>{audit.rack_level_candidate_count}</dd></div>
          <div><dt>邻接轨道候选</dt><dd>{audit.candidates_with_adjacent_track_count}</dd></div>
          <div><dt>已保存草案/结论</dt><dd>{mappings.length}</dd></div>
          <div><dt>仿真可执行映射</dt><dd>{mappings.filter((item) => item.execution_allowed).length}</dd></div>
        </dl>
      ) : null}
      <p className="shuttle-storage-audit-warning">
        候选来自真实 3D 货架几何，但不是自动批准的库位。只有显式人工确认且全部校验通过，才可标记为 synthetic_demo 仿真可用。
      </p>

      <div className="shuttle-storage-filter-row">
        <label>货架层<select aria-label="四向车货位楼层" value={level} onChange={(event) => onLevelChange(Number(event.target.value))}>
          {[1, 2, 3, 4].map((item) => <option key={item} value={item}>L{item}</option>)}
        </select></label>
        <label>搜索<input aria-label="搜索四向车候选货位" value={search} onChange={(event) => onSearchChange(event.target.value)} placeholder="货位 ID / Zone" /></label>
      </div>
      {trialArea?.candidates.length ? (
        <div className="shuttle-trial-candidates" aria-label="最小取货试验区">
          <strong>最小试验区</strong>
          {trialArea.candidates.map((candidate) => (
            <button key={candidate.location_id} type="button" onClick={() => { onLevelChange(candidate.storage_level); onCandidateSelect(candidate); onLocateCandidate(candidate) }}>
              L{candidate.storage_level} · {candidate.zone_id} · r{candidate.row}/c{candidate.col}
            </button>
          ))}
          <small>仅为可审核草案候选，尚未获得研究生 B 的物理确认。</small>
        </div>
      ) : null}
      {candidatesLoading ? <p>正在读取当前层候选…</p> : null}
      {candidatesError ? <p role="alert">候选加载失败：{candidatesError}</p> : null}
      <div className="shuttle-storage-candidate-list" aria-label="四向车候选货位列表">
        <span>当前层 {candidateTotal} 个带邻接轨道候选（显示前 {candidates.length} 个）</span>
        {candidates.map((candidate) => (
          <button
            key={candidate.location_id}
            type="button"
            className={candidate.location_id === selectedLocationId ? 'is-selected' : ''}
            onClick={() => onCandidateSelect(candidate)}
          >
            <strong>{candidate.location_id}</strong>
            <span>{candidate.zone_id} · L{candidate.storage_level} · r{candidate.row}/c{candidate.col}</span>
            <span>{candidate.persisted_status ? statusLabels[candidate.persisted_status] : '未审核'} · 邻接轨道 {candidate.access_options.length}</span>
          </button>
        ))}
      </div>

      {selected ? (
        <section className="shuttle-storage-review-form" aria-label="取货映射审核">
          <div className="shuttle-storage-audit-heading"><strong>当前货位</strong><button type="button" className="ghost-button" onClick={() => onLocateCandidate(selected)}>定位货位</button></div>
          <code>{selected.location_id}</code>
          <span>{selected.zone_id} · L{selected.storage_level} · row {selected.row} / col {selected.col}</span>
          <label>Access Track Node<select aria-label="四向车取货停靠轨道" value={selectedAccessTrackNodeId ?? ''} onChange={(event) => { onAccessTrackSelect(event.target.value); onLocateTrack(event.target.value) }}>
            <option value="">请选择真实邻接轨道</option>
            {selected.access_options.map((option) => <option key={option.track_node_id} value={option.track_node_id}>{option.track_node_id} · 要求{faceLabels[option.required_pick_face]}</option>)}
          </select></label>
          <label>Pick Face<select aria-label="四向车取货面" value={pickFace} onChange={(event) => setPickFace(event.target.value as ShuttlePickFace | '')}>
            <option value="">请选择取货面</option>
            {(Object.keys(faceLabels) as ShuttlePickFace[]).map((face) => <option key={face} value={face}>{faceLabels[face]}</option>)}
          </select></label>
          {selectedOption ? (
            <div className={selectedOption.geometry_clear ? 'shuttle-geometry-check is-valid' : 'shuttle-geometry-check is-invalid'}>
              <strong>车体与操作空间：{selectedOption.geometry_clear ? '程序校验通过' : '不通过'}</strong>
              <span>净间距 {selectedOption.vehicle_clearance_cells.toFixed(2)} 格</span>
              <span>提升机网络：{selectedOption.connected_to_lift_network ? '可连接' : '不可连接'}</span>
              <span>要求取货面：{faceLabels[selectedOption.required_pick_face]}</span>
              <button type="button" className="ghost-button" onClick={() => onLocateTrack(selectedOption.track_node_id)}>查看停车包围盒</button>
            </div>
          ) : null}
          <label>审核人<input aria-label="四向车货位审核人" value={reviewer} onChange={(event) => setReviewer(event.target.value)} placeholder="研究生 B / 审核人姓名" /></label>
          <label>审核备注<textarea aria-label="四向车货位审核备注" value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} /></label>
          <button type="button" className="ghost-button" disabled={!pickFace || !selectedAccessTrackNodeId || !reviewer.trim() || draftMutation.isPending} onClick={() => draftMutation.mutate()}>保存审核草案</button>
          {mapping ? (
            <>
              <div className={`shuttle-mapping-result is-${mapping.effective_status}`}>
                <strong>{statusLabels[mapping.effective_status]}</strong>
                <span>{mapping.validation.valid ? '全部程序校验通过' : mapping.validation.reasons.join('；')}</span>
                <span>执行授权：{mapping.execution_allowed ? 'synthetic_demo 可用' : '不可执行'}</span>
              </div>
              <label className="shuttle-explicit-confirm"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />我已人工核对货位、取货面、停车空间与拓扑版本</label>
              <div className="shuttle-review-actions">
                <button type="button" className="accent-button" disabled={!confirmed || !reviewer.trim() || !mapping.validation.valid || reviewMutation.isPending} onClick={() => reviewMutation.mutate('simulation_approved')}>批准离线仿真映射</button>
                <button type="button" className="ghost-button danger" disabled={!confirmed || !reviewer.trim() || reviewMutation.isPending} onClick={() => reviewMutation.mutate('rejected')}>拒绝映射</button>
              </div>
              <details><summary>审核历史（{historyQuery.data?.length ?? 0}）</summary><ul>{historyQuery.data?.map((item) => <li key={item.review_id}>{item.created_at} · {item.reviewer} · {statusLabels[item.decision]}{item.review_note ? ` · ${item.review_note}` : ''}</li>)}</ul></details>
            </>
          ) : <p>当前候选尚未保存草案，不能执行取货规划。</p>}
        </section>
      ) : null}

      <details className="shuttle-dropoff-config">
        <summary>演示交接点配置（synthetic_demo）</summary>
        <label>真实轨道节点<select aria-label="四向车演示交接轨道" value={dropoffTrackNodeId} onChange={(event) => { setDropoffTrackNodeId(event.target.value); onLocateTrack(event.target.value) }}><option value="">请选择直线停车轨道</option>{eligibleDropoffTracks.map((track) => <option key={track.track_node_id} value={track.track_node_id}>{track.track_node_id}</option>)}</select></label>
        <label>交接点名称<input aria-label="四向车演示交接点名称" value={dropoffLabel} onChange={(event) => setDropoffLabel(event.target.value)} /></label>
        <button type="button" className="ghost-button" disabled={!dropoffTrackNodeId || !dropoffLabel.trim() || !reviewer.trim()} onClick={() => dropoffDraftMutation.mutate()}>保存交接点草案</button>
        {dropoffs.length ? <label>已保存交接点<select aria-label="已保存四向车演示交接点" value={selectedDropoffId} onChange={(event) => { setSelectedDropoffId(event.target.value); const item = dropoffs.find((dropoff) => dropoff.dropoff_id === event.target.value); if (item) onLocateTrack(item.track_node_id) }}><option value="">请选择</option>{dropoffs.map((dropoff) => <option key={dropoff.dropoff_id} value={dropoff.dropoff_id}>{dropoff.label} · {statusLabels[dropoff.effective_status]}</option>)}</select></label> : null}
        {selectedDropoffId ? <div className="shuttle-review-actions"><button type="button" className="accent-button" disabled={!confirmed || !reviewer.trim()} onClick={() => dropoffReviewMutation.mutate('simulation_approved')}>批准演示交接点</button><button type="button" className="ghost-button danger" disabled={!confirmed || !reviewer.trim()} onClick={() => dropoffReviewMutation.mutate('rejected')}>拒绝交接点</button></div> : null}
        <small>交接点只代表离线演示位置，不是正式 WMS 出库站台。</small>
      </details>
      {mutationError ? <p role="alert">保存或审核失败：{mutationError instanceof Error ? mutationError.message : String(mutationError)}</p> : null}
      <small>契约：shuttle-storage-contract/v1 · 候选来源：topology_shuttle_slot_candidate · 不修改 666.json</small>
    </section>
  )
}
