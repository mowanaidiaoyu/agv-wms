import { useEffect } from 'react'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'

export function useSimulationSocket(runId: string) {
  const setLiveState = usePlatformStore((state) => state.setLiveState)
  const setLiveMetrics = usePlatformStore((state) => state.setLiveMetrics)
  const appendLiveEvent = usePlatformStore((state) => state.appendLiveEvent)

  useEffect(() => {
    if (!runId) {
      return
    }
    const socket = new WebSocket(api.wsUrl(runId))
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as { type: string; tick: number; payload: Record<string, unknown> }
      appendLiveEvent(event)
      if (event.type === 'tick') {
        setLiveState(event.payload as never)
      }
      if (event.type === 'metrics_snapshot') {
        setLiveMetrics(event.payload as never)
      }
      if (event.type === 'simulation_completed') {
        void api.getSimulationState(runId).then(setLiveState)
      }
    }
    return () => {
      socket.close()
    }
  }, [appendLiveEvent, runId, setLiveMetrics, setLiveState])
}
