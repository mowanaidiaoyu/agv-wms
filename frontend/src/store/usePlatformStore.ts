import { create } from 'zustand'
import type { AlgorithmEvaluationResult, SimulationEvent, SimulationMetrics, SimulationState } from '../types'

interface PlatformStore {
  selectedTopologyId: string
  selectedScenarioId: string
  selectedRunId: string
  liveState: SimulationState | null
  liveMetrics: SimulationMetrics | null
  liveEvents: SimulationEvent[]
  evaluation: AlgorithmEvaluationResult[]
  setSelectedTopologyId: (id: string) => void
  setSelectedScenarioId: (id: string) => void
  setSelectedRunId: (id: string) => void
  setLiveState: (state: SimulationState | null) => void
  setLiveMetrics: (metrics: SimulationMetrics | null) => void
  appendLiveEvent: (event: SimulationEvent) => void
  setEvaluation: (results: AlgorithmEvaluationResult[]) => void
  resetRun: () => void
}

export const usePlatformStore = create<PlatformStore>((set) => ({
  selectedTopologyId: '',
  selectedScenarioId: '',
  selectedRunId: '',
  liveState: null,
  liveMetrics: null,
  liveEvents: [],
  evaluation: [],
  setSelectedTopologyId: (id) => set({ selectedTopologyId: id }),
  setSelectedScenarioId: (id) => set({ selectedScenarioId: id }),
  setSelectedRunId: (id) => set({ selectedRunId: id, liveState: null, liveMetrics: null, liveEvents: [] }),
  setLiveState: (state) => set({ liveState: state }),
  setLiveMetrics: (metrics) => set({ liveMetrics: metrics }),
  appendLiveEvent: (event) => set((prev) => ({ liveEvents: [...prev.liveEvents.slice(-49), event] })),
  setEvaluation: (results) => set({ evaluation: results }),
  resetRun: () =>
    set({
      selectedRunId: '',
      liveState: null,
      liveMetrics: null,
      liveEvents: [],
    }),
}))
