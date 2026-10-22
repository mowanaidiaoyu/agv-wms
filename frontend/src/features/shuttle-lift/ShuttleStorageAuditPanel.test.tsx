import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState, type ComponentProps } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { ShuttleStorageAuditPanel } from './ShuttleStorageAuditPanel'
import type { ShuttleStorageAuditResponse, ShuttleStorageCandidate, ShuttleStorageLocationRecord } from './types'

vi.mock('../../lib/api', () => ({
  api: {
    saveShuttleStorageDraft: vi.fn(), reviewShuttleStorageMapping: vi.fn(),
    getShuttleStorageReviewHistory: vi.fn(), saveShuttleDemoDropoffDraft: vi.fn(),
    reviewShuttleDemoDropoff: vi.fn(),
  },
}))

const candidate: ShuttleStorageCandidate = {
  location_id: 'shuttle-location:zone-shuttle:L2:r96:c43',
  topology_id: 'topology-6ceb4920', topology_revision: '2.0.0:hash', zone_id: 'zone-shuttle',
  floor: '1F', storage_level: 2, row: 96, col: 43, world_x: 3, world_y: 4, world_z: 5,
  pick_face: 'unknown', access_track_node_id: null,
  potential_access_track_node_ids: ['L2-r96-c41'], access_mapping_status: 'unverified',
  review_status: 'draft', data_source: 'topology_shuttle_slot_candidate', execution_allowed: false,
  blocker_codes: ['missing_pick_face'], persisted_status: null,
  access_options: [{
    track_node_id: 'L2-r96-c41', shuttle_level: 2, row: 96, col: 41,
    world_x: 1, world_y: 4, world_z: 5, required_pick_face: 'west', required_heading_radians: 0,
    connected_to_lift_network: true, vehicle_clearance_cells: 1.04, geometry_clear: true,
    validation_codes: [],
  }],
}

const audit: ShuttleStorageAuditResponse = {
  topology_id: candidate.topology_id, topology_version: '2.0.0', topology_revision: candidate.topology_revision,
  contract_version: 'shuttle-storage-contract/v1', audit_status: 'blocked_unverified_mappings',
  data_source: 'topology_shuttle_slot_candidate', execution_allowed: false,
  ctu_storage_node_count: 2496, shuttle_storage_node_count: 0, shuttle_zone_count: 31,
  rack_level_candidate_count: 4238, candidates_with_adjacent_track_count: 2204,
  declared_pick_face_count: 0, verified_access_mapping_count: 0, formal_dropoff_count: 0,
  blockers: ['missing formal Shuttle storage identity'], required_human_review: ['confirm pick face'],
  candidate_preview: [candidate],
}

const validation = {
  valid: true, location_id: candidate.location_id, access_track_node_id: 'L2-r96-c41' as string | null,
  pick_face: 'west' as const, topology_revision: candidate.topology_revision,
  topology_content_sha256: 'hash', shuttle_config_revision: 'shuttle-4l-lift-v2:hash',
  same_level: true, adjacent_track: true, pick_face_matches: true,
  geometry_clear: true, operation_clearance_cells: 1.04 as number | null,
  connected_to_lift_network: true, reachable: true, mapping_conflict: false, codes: [], reasons: [],
}

const mapping: ShuttleStorageLocationRecord = {
  location_id: candidate.location_id, topology_id: candidate.topology_id,
  topology_revision: candidate.topology_revision, topology_content_sha256: 'hash',
  shuttle_config_revision: 'shuttle-4l-lift-v2:hash',
  zone_id: candidate.zone_id, level: 2, row: candidate.row, col: candidate.col,
  geometry_source: 'topology_shuttle_slot_candidate', pick_face: 'west', access_track_node_id: 'L2-r96-c41',
  status: 'draft', effective_status: 'draft', execution_allowed: false, validation,
  reviewer: '研究生 B', review_note: null, created_at: '2026-10-22T01:00:00Z', updated_at: '2026-10-22T01:00:00Z',
}

const dropoff = {
  dropoff_id: 'shuttle-demo-dropoff:L2-r100-c15', topology_id: candidate.topology_id,
  topology_revision: candidate.topology_revision, topology_content_sha256: 'hash',
  shuttle_config_revision: 'shuttle-4l-lift-v2:hash', track_node_id: 'L2-r100-c15',
  label: '离线演示交接点', status: 'draft' as const, effective_status: 'draft' as const,
  execution_allowed: false, validation: {
    valid: true, track_node_id: 'L2-r100-c15', parking_space_clear: true,
    reachable_from_location_ids: [candidate.location_id], reasons: [],
  }, reviewer: '研究生 B', review_note: null,
  data_source: 'synthetic_demo' as const, created_at: '2026-10-22T01:00:00Z', updated_at: '2026-10-22T01:00:00Z',
}

function renderPanel(overrides: Partial<ComponentProps<typeof ShuttleStorageAuditPanel>> = {}) {
  const props: ComponentProps<typeof ShuttleStorageAuditPanel> = {
    topologyId: candidate.topology_id, audit, loading: false, error: null,
    candidates: [candidate], candidateTotal: 2204, candidatesLoading: false, candidatesError: null,
    trialArea: { topology_id: candidate.topology_id, topology_revision: candidate.topology_revision, candidates: [candidate], selection_rule: 'one per level', execution_allowed: false },
    mappings: [], dropoffs: [], tracks: [], level: 2, search: '',
    selectedLocationId: candidate.location_id, selectedAccessTrackNodeId: null,
    onLevelChange: vi.fn(), onSearchChange: vi.fn(), onCandidateSelect: vi.fn(),
    onAccessTrackSelect: vi.fn(), onLocateCandidate: vi.fn(), onLocateTrack: vi.fn(),
    ...overrides,
  }
  function Harness() {
    const [accessTrackNodeId, setAccessTrackNodeId] = useState(props.selectedAccessTrackNodeId)
    return <ShuttleStorageAuditPanel
      {...props}
      selectedAccessTrackNodeId={accessTrackNodeId}
      onAccessTrackSelect={(trackNodeId) => {
        setAccessTrackNodeId(trackNodeId)
        props.onAccessTrackSelect(trackNodeId)
      }}
    />
  }
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Harness />
    </QueryClientProvider>,
  )
}

describe('ShuttleStorageAuditPanel', () => {
  beforeEach(() => {
    vi.mocked(api.getShuttleStorageReviewHistory).mockResolvedValue([])
    vi.mocked(api.saveShuttleStorageDraft).mockResolvedValue(mapping)
    vi.mocked(api.reviewShuttleStorageMapping).mockResolvedValue({ ...mapping, status: 'simulation_approved', effective_status: 'simulation_approved', execution_allowed: true })
    vi.mocked(api.saveShuttleDemoDropoffDraft).mockResolvedValue(dropoff)
  })

  it('keeps geometry candidates unapproved and exposes the real pick-face review inputs', () => {
    renderPanel()
    expect(screen.getByRole('region', { name: '四向车货位配置' })).toHaveTextContent('4238')
    expect(screen.getAllByText(candidate.location_id).length).toBeGreaterThan(0)
    expect(screen.getByLabelText('四向车取货停靠轨道')).toHaveValue('')
    expect(screen.getByLabelText('四向车取货面')).toHaveValue('')
    expect(screen.getByText(/尚未保存草案/)).toBeInTheDocument()
  })

  it('saves only a draft with the explicitly selected access track, face and reviewer', async () => {
    renderPanel()
    fireEvent.change(screen.getByLabelText('四向车取货停靠轨道'), { target: { value: 'L2-r96-c41' } })
    fireEvent.change(screen.getByLabelText('四向车取货面'), { target: { value: 'west' } })
    fireEvent.change(screen.getByLabelText('四向车货位审核人'), { target: { value: '研究生 B' } })
    fireEvent.click(screen.getByRole('button', { name: '保存审核草案' }))
    await waitFor(() => expect(api.saveShuttleStorageDraft).toHaveBeenCalledWith(candidate.topology_id, {
      location_id: candidate.location_id, pick_face: 'west', access_track_node_id: 'L2-r96-c41',
      reviewer: '研究生 B', review_note: undefined,
    }))
  })

  it('requires an explicit confirmation before simulation approval', async () => {
    renderPanel({ mappings: [mapping], selectedAccessTrackNodeId: 'L2-r96-c41' })
    fireEvent.change(screen.getByLabelText('四向车货位审核人'), { target: { value: '研究生 B' } })
    const approve = screen.getByRole('button', { name: '批准离线仿真映射' })
    expect(approve).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(approve).toBeEnabled()
    fireEvent.click(approve)
    await waitFor(() => expect(api.reviewShuttleStorageMapping).toHaveBeenCalledWith(
      candidate.topology_id, candidate.location_id,
      expect.objectContaining({ decision: 'simulation_approved', explicit_confirmation: true, reviewer: '研究生 B' }),
    ))
  })

  it('shows a scoped load failure without inventing an executable mapping', () => {
    renderPanel({ audit: undefined, error: 'service unavailable', candidates: [], candidateTotal: 0, trialArea: undefined })
    expect(screen.getByRole('alert')).toHaveTextContent('service unavailable')
    expect(screen.queryByText('synthetic_demo 可用')).not.toBeInTheDocument()
  })

  it('creates only a synthetic-demo drop-off draft on a real straight track', async () => {
    renderPanel({
      mappings: [{ ...mapping, status: 'simulation_approved', effective_status: 'simulation_approved', execution_allowed: true }],
      tracks: [{
        topology_id: candidate.topology_id, zone_id: candidate.zone_id,
        track_node_id: 'L2-r100-c15', shuttle_level: 2, row: 100, col: 15,
        world_x: 4, world_y: 4, world_z: 5, track_type: 'straight', traversable: true,
        neighbors: ['L2-r100-c13', 'L2-r100-c17'],
      }],
    })
    fireEvent.change(screen.getByLabelText('四向车货位审核人'), { target: { value: '研究生 B' } })
    fireEvent.change(screen.getByLabelText('四向车演示交接轨道'), { target: { value: 'L2-r100-c15' } })
    fireEvent.click(screen.getByRole('button', { name: '保存交接点草案' }))
    await waitFor(() => expect(api.saveShuttleDemoDropoffDraft).toHaveBeenCalledWith(
      candidate.topology_id,
      expect.objectContaining({ track_node_id: 'L2-r100-c15', reviewer: '研究生 B' }),
    ))
  })
})
