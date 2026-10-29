import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { usePlatformStore } from '../store/usePlatformStore'
import type { GoodsSlot, SimulationEvent, SimulationState } from '../types'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function parseSimulationSocketEvent(data: unknown): SimulationEvent | null {
  if (typeof data !== 'string') return null
  try {
    const value: unknown = JSON.parse(data)
    if (!isRecord(value) || typeof value.type !== 'string' || !isRecord(value.payload)) return null
    return value as unknown as SimulationEvent
  } catch {
    return null
  }
}

export function mergeSimulationTick(
  previous: SimulationState | null,
  payload: Record<string, unknown>,
): SimulationState | null {
  const inventoryDelta = Array.isArray(payload.inventoryDelta) ? payload.inventoryDelta : []
  const previousInventory = previous?.inventory ?? []
  const nextInventory = Array.isArray(payload.inventory)
    ? payload.inventory as GoodsSlot[]
    : inventoryDelta.reduce<GoodsSlot[]>((inventory, change) => {
        if (!isRecord(change) || change.op !== 'remove') return inventory
        return inventory.filter((slot) => (
          slot.nodeId !== change.nodeId || slot.layer !== Number(change.layer)
        ))
      }, previousInventory)
  const merged = { ...(previous ?? {}), ...payload, inventory: nextInventory } as Partial<SimulationState>
  if (
    typeof merged.id !== 'string'
    || typeof merged.topologyId !== 'string'
    || typeof merged.tick !== 'number'
    || typeof merged.status !== 'string'
  ) return previous

  return {
    ...merged,
    robots: Array.isArray(merged.robots) ? merged.robots : [],
    tasks: Array.isArray(merged.tasks) ? merged.tasks : [],
    orders: Array.isArray(merged.orders) ? merged.orders : [],
    inventory: Array.isArray(merged.inventory) ? merged.inventory : [],
    transferInventory: Array.isArray(merged.transferInventory) ? merged.transferInventory : [],
    transferEvents: Array.isArray(merged.transferEvents) ? merged.transferEvents : [],
    alerts: Array.isArray(merged.alerts) ? merged.alerts : [],
    queues: merged.queues ?? { conveyor: [], station: [], buffer: [] },
    createdAt: merged.createdAt ?? '',
    updatedAt: merged.updatedAt ?? '',
  } as SimulationState
}

/**
 * WebSocket is authoritative only while it is connected. Once it closes, the
 * HTTP query must be allowed to replace a stale live frame, even after reset
 * when the authoritative tick can legitimately move backwards to zero.
 */
export function selectSimulationStateSource(
  liveState: SimulationState | null,
  polledState: SimulationState | undefined,
  socketConnected: boolean,
): SimulationState | null | undefined {
  return socketConnected ? (liveState ?? polledState) : (polledState ?? liveState)
}

export function useSimulationSocket(runId: string) {
  const [connected, setConnected] = useState(false)
  const [reconnectAttempt, setReconnectAttempt] = useState(0)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const setLiveState = usePlatformStore((state) => state.setLiveState)
  const setLiveMetrics = usePlatformStore((state) => state.setLiveMetrics)
  const appendLiveEvent = usePlatformStore((state) => state.appendLiveEvent)

  useEffect(() => {
    if (!runId) {
      setConnected(false)
      return
    }
    let active = true
    const socket = new WebSocket(api.wsUrl(runId))
    socket.onopen = () => {
      if (!active) return
      setConnected(true)
      // A reconnect always performs a complete HTTP resync as well as accepting
      // the server snapshot, so omitted inventory fields cannot leave stale UI.
      if (reconnectAttempt > 0) {
        void Promise.all([
          api.getSimulationState(runId).then(setLiveState),
          api.getSimulationMetrics(runId).then(setLiveMetrics),
        ]).catch((error: unknown) => console.error('仿真 WebSocket 重连同步失败', error))
      }
    }
    socket.onclose = () => {
      if (!active) return
      setConnected(false)
      reconnectTimer.current = setTimeout(() => {
        if (active) setReconnectAttempt((attempt) => attempt + 1)
      }, Math.min(5000, 500 * (reconnectAttempt + 1)))
    }
    socket.onerror = () => {
      setConnected(false)
      socket.close()
    }
    socket.onmessage = (message) => {
      const event = parseSimulationSocketEvent(message.data)
      if (!event) {
        console.warn('忽略无法解析的仿真 WebSocket 消息')
        return
      }
      appendLiveEvent(event)
      if (event.type === 'snapshot' || event.type === 'tick' || event.type === 'simulation_error') {
        const previous = usePlatformStore.getState().liveState
        setLiveState(mergeSimulationTick(previous, event.payload))
      }
      if (event.type === 'metrics_snapshot') {
        setLiveMetrics(event.payload as never)
      }
      if (event.type === 'simulation_completed') {
        void api.getSimulationState(runId).then(setLiveState).catch((error: unknown) => {
          console.error('读取仿真终态失败', error)
        })
      }
    }
    return () => {
      active = false
      setConnected(false)
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      socket.close()
    }
  }, [appendLiveEvent, reconnectAttempt, runId, setLiveMetrics, setLiveState])

  return connected
}
