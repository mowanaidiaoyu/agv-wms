import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { paintCell, updateConveyorBlockDeck } from '../../lib/topology'
import { usePlatformStore } from '../../store/usePlatformStore'
import { useTopologyEditorStore } from '../../store/useTopologyEditorStore'
import { TopologyEditorToolbar } from './TopologyEditorToolbar'

vi.mock('../../lib/api', () => ({
  api: {
    listTopologies: vi.fn(async () => []),
    getTopology: vi.fn(),
    saveTopology: vi.fn(),
    deleteTopology: vi.fn(),
  },
}))

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

describe('TopologyEditorToolbar simulation entry', () => {
  beforeEach(() => {
    useTopologyEditorStore.getState().beginNew()
    usePlatformStore.setState({
      selectedTopologyId: 'topology-6.6',
      selectedRunId: 'run-from-6.6',
      liveState: null,
      liveMetrics: null,
      liveEvents: [],
    })
    vi.mocked(api.listTopologies).mockResolvedValue([])
    vi.mocked(api.saveTopology).mockClear()
    vi.mocked(api.saveTopology).mockImplementation(async (payload) => ({
      id: payload.id,
      name: payload.name,
      payload,
    }))
  })

  it('clears the previous run and carries the newly saved topology into simulation', async () => {
    const draftId = useTopologyEditorStore.getState().draft.id
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/topology']}>
          <Routes>
            <Route path="/topology" element={<><TopologyEditorToolbar /><LocationProbe /></>} />
            <Route path="/simulation" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: '保存并仿真' }))

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(`/simulation?topologyId=${draftId}`)
    })
    expect(usePlatformStore.getState().selectedTopologyId).toBe(draftId)
    expect(usePlatformStore.getState().selectedRunId).toBe('')
  })

  it('shows the full conveyor validation reason and selects the failing block', async () => {
    let topology = useTopologyEditorStore.getState().draft
    topology = paintCell(topology, 'conveyor', 0, 0).topology
    topology = paintCell(topology, 'conveyor', 0, 2).topology
    const anchor = topology.cells.find((cell) => cell.row === 0 && cell.col === 0)!
    const nextAnchor = topology.cells.find((cell) => cell.row === 0 && cell.col === 2)!
    topology = updateConveyorBlockDeck(topology, anchor.id, 'lower', {
      inputSide: 'west',
      outputSide: 'east',
    })
    topology = updateConveyorBlockDeck(topology, nextAnchor.id, 'lower', {
      inputSide: 'north',
      outputSide: 'south',
    })
    useTopologyEditorStore.setState({ draft: topology, dirty: true })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/topology']}>
          <TopologyEditorToolbar />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: '保存拓扑' }))

    const notice = await screen.findByRole('status')
    expect(notice).toHaveTextContent('保存失败：输送模块 1F:0:0 下层')
    expect(notice).toHaveTextContent('东侧出口与下一块西侧入口不匹配')
    expect(notice).toHaveAttribute('title', notice.textContent)
    expect(useTopologyEditorStore.getState().selection).toEqual({ kind: 'cell', id: anchor.id })
    expect(api.saveTopology).not.toHaveBeenCalled()
  })
})
