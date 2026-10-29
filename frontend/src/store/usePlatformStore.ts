import { create } from 'zustand'
import type { SimulationEvent, SimulationMetrics, SimulationState } from '../types'

interface PlatformStore {
  selectedTopologyId: string
  selectedRunId: string
  liveState: SimulationState | null
  liveMetrics: SimulationMetrics | null
  liveEvents: SimulationEvent[]
  setSelectedTopologyId: (id: string) => void
  setSelectedRunId: (id: string) => void
  setLiveState: (state: SimulationState | null) => void
  setLiveMetrics: (metrics: SimulationMetrics | null) => void
  appendLiveEvent: (event: SimulationEvent) => void
  clearLiveEvents: () => void
  resetRun: () => void
}

export const usePlatformStore = create<PlatformStore>((set) => ({
  selectedTopologyId: '',
  selectedRunId: '',
  liveState: null,
  liveMetrics: null,
  liveEvents: [],
  setSelectedTopologyId: (id) => set({ selectedTopologyId: id }),
  setSelectedRunId: (id) => set({ selectedRunId: id, liveState: null, liveMetrics: null, liveEvents: [] }),
  setLiveState: (state) => set({ liveState: state }),
  setLiveMetrics: (metrics) => set({ liveMetrics: metrics }),
  appendLiveEvent: (event) => set((prev) => ({ liveEvents: [...prev.liveEvents.slice(-49), event] })),
  clearLiveEvents: () => set({ liveEvents: [] }),
  resetRun: () =>
    set({
      selectedRunId: '',
      liveState: null,
      liveMetrics: null,
      liveEvents: [],
    }),
}))
