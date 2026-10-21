import { create } from 'zustand'
import type { PathDemoAlgorithm } from '../path-demo/types'
import type { ShuttleLevel } from './types'

export interface ShuttleWorkspaceDraft {
  topologyVersion: string
  startLevel: ShuttleLevel
  endLevel: ShuttleLevel
  startNodeId: string
  endNodeId: string
  algorithm: PathDemoAlgorithm
}

interface ShuttleWorkspaceState {
  drafts: Record<string, ShuttleWorkspaceDraft>
  saveDraft: (topologyId: string, draft: ShuttleWorkspaceDraft) => void
}

export const useShuttleWorkspaceStore = create<ShuttleWorkspaceState>((set) => ({
  drafts: {},
  saveDraft: (topologyId, draft) => set((state) => ({
    drafts: { ...state.drafts, [topologyId]: draft },
  })),
}))
