import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'

export function useSimulationSocket(runId: string) {
  const [connected, setConnected] = useState(false)
  const setLiveState = usePlatformStore((state) => state.setLiveState)
  const setLiveMetrics = usePlatformStore((state) => state.setLiveMetrics)
  const appendLiveEvent = usePlatformStore((state) => state.appendLiveEvent)

  useEffect(() => {
    if (!runId) {
      setConnected(false)
      return
    }
    const socket = new WebSocket(api.wsUrl(runId))
    socket.onopen = () => setConnected(true)
    socket.onclose = () => setConnected(false)
    socket.onerror = () => setConnected(false)
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as { type: string; tick: number; payload: Record<string, unknown> }
      appendLiveEvent(event)
      if (event.type === 'tick') {
        const previous = usePlatformStore.getState().liveState
        const payload = event.payload as Record<string, unknown>
        setLiveState({
          ...(previous ?? {}),
          ...payload,
          inventory: payload.inventory ?? previous?.inventory ?? [],
        } as never)
      }
      if (event.type === 'metrics_snapshot') {
        setLiveMetrics(event.payload as never)
      }
      if (event.type === 'simulation_completed') {
        void api.getSimulationState(runId).then(setLiveState)
      }
    }
    return () => {
      setConnected(false)
      socket.close()
    }
  }, [appendLiveEvent, runId, setLiveMetrics, setLiveState])

  return connected
}
