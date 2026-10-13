import { create } from 'zustand'
import type {
  MultiPathEvaluation,
  MultiPathPlanningConfig,
  MultiPathVehicleRequest,
  PathDemoAlgorithm,
} from './types'

export interface MultiPathCustomDraft {
  topologyId: string
  topologyVersion: string
  vehicles: MultiPathVehicleRequest[]
  planningConfig: MultiPathPlanningConfig
}

interface MultiPathExperimentStore {
  customDraft: MultiPathCustomDraft | null
  evaluation: MultiPathEvaluation | null
  viewedAlgorithm: PathDemoAlgorithm | null
  setCustomDraft: (draft: MultiPathCustomDraft | null) => void
  setEvaluation: (evaluation: MultiPathEvaluation | null) => void
  setViewedAlgorithm: (algorithm: PathDemoAlgorithm | null) => void
}

export const useMultiPathExperimentStore = create<MultiPathExperimentStore>((set) => ({
  customDraft: null,
  evaluation: null,
  viewedAlgorithm: null,
  setCustomDraft: (customDraft) => set({ customDraft }),
  setEvaluation: (evaluation) => set({ evaluation }),
  setViewedAlgorithm: (viewedAlgorithm) => set({ viewedAlgorithm }),
}))
