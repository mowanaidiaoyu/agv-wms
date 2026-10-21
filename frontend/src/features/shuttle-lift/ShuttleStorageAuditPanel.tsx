import type { ShuttleStorageAuditResponse } from './types'

interface ShuttleStorageAuditPanelProps {
  audit?: ShuttleStorageAuditResponse
  loading: boolean
  error: string | null
}

export function ShuttleStorageAuditPanel({ audit, loading, error }: ShuttleStorageAuditPanelProps) {
  return (
    <section className="shuttle-storage-audit" aria-label="四向车货位审计">
      <div className="shuttle-storage-audit-heading">
        <strong>四向车货位审计</strong>
        <span className="is-blocked">执行阻塞</span>
      </div>
      {loading ? <p>正在核对 Shuttle 货架、层级与轨道关系…</p> : null}
      {error ? <p role="alert">四向车货位审计加载失败：{error}</p> : null}
      {audit ? (
        <>
          <p>
            当前 666 没有已审核的四向车货位与取货轨道映射。下列记录只是从真实货架几何派生的候选，
            不能用于补货、预留、路径执行或库存扣减。
          </p>
          <dl>
            <div><dt>CTU 正式库位</dt><dd>{audit.ctu_storage_node_count}</dd></div>
            <div><dt>Shuttle 正式库位</dt><dd>{audit.shuttle_storage_node_count}</dd></div>
            <div><dt>货架层候选</dt><dd>{audit.rack_level_candidate_count}</dd></div>
            <div><dt>邻接轨道候选</dt><dd>{audit.candidates_with_adjacent_track_count}</dd></div>
            <div><dt>已声明取货面</dt><dd>{audit.declared_pick_face_count}</dd></div>
            <div><dt>已验证映射</dt><dd>{audit.verified_access_mapping_count}</dd></div>
            <div><dt>正式交接站</dt><dd>{audit.formal_dropoff_count}</dd></div>
          </dl>
          <p className="shuttle-storage-audit-warning">
            需要研究生 B 确认真实货位身份、每层取货面、同层停靠轨道及交接站后，才能进入取货闭环开发。
          </p>
          <details>
            <summary>查看候选建模样例（{audit.candidate_preview.length}）</summary>
            <ul>
              {audit.candidate_preview.map((candidate) => (
                <li key={candidate.location_id}>
                  <code>{candidate.location_id}</code>
                  <span>
                    {candidate.potential_access_track_node_ids.length
                      ? `可能邻接：${candidate.potential_access_track_node_ids.join('、')}`
                      : '未发现同层正交邻接轨道'}
                  </span>
                </li>
              ))}
            </ul>
          </details>
          <small>契约：{audit.contract_version} · 数据来源：{audit.data_source} · 仅审计，不可执行</small>
        </>
      ) : null}
    </section>
  )
}
