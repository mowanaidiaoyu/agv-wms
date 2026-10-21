import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ShuttleStorageAuditPanel } from './ShuttleStorageAuditPanel'
import type { ShuttleStorageAuditResponse } from './types'

const audit: ShuttleStorageAuditResponse = {
  topology_id: 'topology-6ceb4920',
  topology_version: '2.0.0',
  topology_revision: '2.0.0:hash',
  contract_version: 'shuttle-storage-contract/v1',
  audit_status: 'blocked_unverified_mappings',
  data_source: 'topology_shuttle_slot_candidate',
  execution_allowed: false,
  ctu_storage_node_count: 2496,
  shuttle_storage_node_count: 0,
  shuttle_zone_count: 31,
  rack_level_candidate_count: 4238,
  candidates_with_adjacent_track_count: 2204,
  declared_pick_face_count: 0,
  verified_access_mapping_count: 0,
  formal_dropoff_count: 0,
  blockers: ['missing formal Shuttle storage identity'],
  required_human_review: ['confirm pick face'],
  candidate_preview: [{
    location_id: 'shuttle-location:zone-shuttle:L2:r96:c43',
    topology_id: 'topology-6ceb4920',
    topology_revision: '2.0.0:hash',
    zone_id: 'zone-shuttle',
    floor: '1F',
    storage_level: 2,
    row: 96,
    col: 43,
    pick_face: 'unknown',
    access_track_node_id: null,
    potential_access_track_node_ids: ['L2-r96-c41'],
    access_mapping_status: 'unverified',
    review_status: 'draft',
    data_source: 'topology_shuttle_slot_candidate',
    execution_allowed: false,
    blocker_codes: ['missing_pick_face'],
  }],
}

describe('ShuttleStorageAuditPanel', () => {
  it('shows the real blocker and never presents visual candidates as executable inventory', () => {
    render(<ShuttleStorageAuditPanel audit={audit} loading={false} error={null} />)

    expect(screen.getByRole('region', { name: '四向车货位审计' })).toHaveTextContent('执行阻塞')
    expect(screen.getByText('Shuttle 正式库位').parentElement).toHaveTextContent('0')
    expect(screen.getByText('货架层候选').parentElement).toHaveTextContent('4238')
    expect(screen.getByText('已验证映射').parentElement).toHaveTextContent('0')
    expect(screen.getByText(/不能用于补货、预留、路径执行或库存扣减/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /创建取货任务|确认取货|补货/ })).not.toBeInTheDocument()
  })

  it('shows a scoped load failure without inventing a successful audit', () => {
    render(<ShuttleStorageAuditPanel loading={false} error="service unavailable" />)

    expect(screen.getByRole('alert')).toHaveTextContent('service unavailable')
    expect(screen.queryByText('可执行')).not.toBeInTheDocument()
  })
})
