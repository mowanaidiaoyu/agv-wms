import { useCallback, useEffect, useRef, useState } from 'react'
import { floorPlaneY, worldX, worldZ } from '../../lib/topology3d'
import type { TopologyPayload } from '../../types'
import type { PathDemoCell, PlaybackState, TrajectoryPoint, TrajectorySample } from './types'

export const DEMO_VEHICLE_ID = 'ctu-path-demo-01'
export const DEMO_SPEED_CELLS_PER_SECOND = 4

export function buildTrajectory(
  path: PathDemoCell[],
  topology: TopologyPayload,
  speedCellsPerSecond = DEMO_SPEED_CELLS_PER_SECOND,
): TrajectoryPoint[] {
  if (speedCellsPerSecond <= 0 || !Number.isFinite(speedCellsPerSecond)) {
    throw new Error('Demo speed must be a finite positive number')
  }
  const floors = topology.meta.floors?.length ? topology.meta.floors : ['1F']
  let arrivalSeconds = 0

  return path.map((cell, index) => {
    const floorIndex = Math.max(0, floors.indexOf(cell.floor))
    const world: [number, number, number] = [
      worldX(cell.col, topology.grid.cols),
      floorPlaneY(floorIndex) + 0.5,
      worldZ(cell.row, topology.grid.rows),
    ]
    if (index > 0) {
      const previous = path[index - 1]
      const distance = Math.hypot(cell.row - previous.row, cell.col - previous.col)
      arrivalSeconds += distance / speedCellsPerSecond
    }
    return {
      vehicleId: DEMO_VEHICLE_ID,
      topologyId: topology.id,
      floor: cell.floor,
      row: cell.row,
      col: cell.col,
      pathIndex: index,
      arrivalSeconds,
      world,
    }
  })
}
export function sampleTrajectory(trajectory: TrajectoryPoint[], elapsedSeconds: number): TrajectorySample | null {
  if (!trajectory.length) return null
  const finalPoint = trajectory[trajectory.length - 1]
  const elapsed = Math.max(0, Math.min(elapsedSeconds, finalPoint.arrivalSeconds))
  if (trajectory.length === 1 || finalPoint.arrivalSeconds === 0) {
    return { ...finalPoint, elapsedSeconds: elapsed, headingRadians: 0 }
  }

  let nextIndex = trajectory.findIndex((point) => point.arrivalSeconds >= elapsed)
  if (nextIndex <= 0) nextIndex = 1
  const previous = trajectory[nextIndex - 1]
  const next = trajectory[nextIndex]
  const segmentDuration = next.arrivalSeconds - previous.arrivalSeconds
  const alpha = segmentDuration > 0 ? (elapsed - previous.arrivalSeconds) / segmentDuration : 1
  const x = previous.world[0] + (next.world[0] - previous.world[0]) * alpha
  const y = previous.world[1] + (next.world[1] - previous.world[1]) * alpha
  const z = previous.world[2] + (next.world[2] - previous.world[2]) * alpha
  const row = previous.row + (next.row - previous.row) * alpha
  const col = previous.col + (next.col - previous.col) * alpha
  let headingStart = previous
  let headingEnd = next
  if (headingStart.world[0] === headingEnd.world[0] && headingStart.world[2] === headingEnd.world[2]) {
    for (let index = nextIndex - 1; index > 0; index -= 1) {
      if (
        trajectory[index].world[0] !== trajectory[index - 1].world[0]
        || trajectory[index].world[2] !== trajectory[index - 1].world[2]
      ) {
        headingStart = trajectory[index - 1]
        headingEnd = trajectory[index]
        break
      }
    }
    if (headingStart.world[0] === headingEnd.world[0] && headingStart.world[2] === headingEnd.world[2]) {
      for (let index = nextIndex; index < trajectory.length - 1; index += 1) {
        if (
          trajectory[index].world[0] !== trajectory[index + 1].world[0]
          || trajectory[index].world[2] !== trajectory[index + 1].world[2]
        ) {
          headingStart = trajectory[index]
          headingEnd = trajectory[index + 1]
          break
        }
      }
    }
  }
  const headingRadians = Math.atan2(
    headingEnd.world[0] - headingStart.world[0],
    headingEnd.world[2] - headingStart.world[2],
  )

  return {
    vehicleId: previous.vehicleId,
    topologyId: previous.topologyId,
    floor: previous.floor,
    row,
    col,
    pathIndex: elapsed >= finalPoint.arrivalSeconds ? finalPoint.pathIndex : previous.pathIndex,
    elapsedSeconds: elapsed,
    world: [x, y, z],
    headingRadians,
  }
}
export function initialPlaybackState(rate: 1 | 2 | 4 = 1): PlaybackState {
  return { status: 'idle', elapsedSeconds: 0, rate }
}

export function startPlayback(state: PlaybackState, totalDuration: number): PlaybackState {
  if (totalDuration <= 0) return state
  return {
    ...state,
    status: 'running',
    elapsedSeconds: state.status === 'completed' ? 0 : state.elapsedSeconds,
  }
}

export function pausePlayback(state: PlaybackState): PlaybackState {
  return state.status === 'running' ? { ...state, status: 'paused' } : state
}

export function resumePlayback(state: PlaybackState): PlaybackState {
  return state.status === 'paused' ? { ...state, status: 'running' } : state
}

export function resetPlayback(state: PlaybackState): PlaybackState {
  return { ...state, status: 'idle', elapsedSeconds: 0 }
}

export function clampPlaybackTime(elapsedSeconds: number, totalDuration: number): number {
  if (!Number.isFinite(elapsedSeconds) || !Number.isFinite(totalDuration) || totalDuration <= 0) return 0
  return Math.max(0, Math.min(elapsedSeconds, totalDuration))
}

export function seekPlayback(
  state: PlaybackState,
  elapsedSeconds: number,
  totalDuration: number,
): PlaybackState {
  const nextElapsedSeconds = clampPlaybackTime(elapsedSeconds, totalDuration)
  return {
    ...state,
    elapsedSeconds: nextElapsedSeconds,
    status: nextElapsedSeconds >= totalDuration && totalDuration > 0 ? 'completed' : 'paused',
  }
}

export function stepPlaybackTime(
  elapsedSeconds: number,
  direction: -1 | 1,
  timeStepSeconds: number,
  totalDuration: number,
): number {
  const current = clampPlaybackTime(elapsedSeconds, totalDuration)
  if (!Number.isFinite(timeStepSeconds) || timeStepSeconds <= 0 || totalDuration <= 0) return current
  const epsilon = 1e-9
  const tick = direction > 0
    ? Math.floor((current + epsilon) / timeStepSeconds) + 1
    : Math.ceil((current - epsilon) / timeStepSeconds) - 1
  return clampPlaybackTime(tick * timeStepSeconds, totalDuration)
}

export function advancePlayback(state: PlaybackState, deltaSeconds: number, totalDuration: number): PlaybackState {
  if (state.status !== 'running' || deltaSeconds <= 0) return state
  const elapsedSeconds = Math.min(totalDuration, state.elapsedSeconds + deltaSeconds * state.rate)
  return {
    ...state,
    elapsedSeconds,
    status: elapsedSeconds >= totalDuration ? 'completed' : 'running',
  }
}

export function usePathPlayback(totalDuration: number, timelineKey?: string) {
  const [state, setState] = useState<PlaybackState>(() => initialPlaybackState())
  const frameRef = useRef<number | null>(null)

  useEffect(() => {
    setState((current) => initialPlaybackState(current.rate))
  }, [timelineKey])

  useEffect(() => {
    if (state.status !== 'running') return undefined
    let previousTime: number | null = null
    const onFrame = (time: number) => {
      if (previousTime === null) previousTime = time
      const deltaSeconds = (time - previousTime) / 1000
      previousTime = time
      setState((current) => advancePlayback(current, deltaSeconds, totalDuration))
      frameRef.current = requestAnimationFrame(onFrame)
    }
    frameRef.current = requestAnimationFrame(onFrame)
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }
  }, [state.status, totalDuration])

  const start = useCallback(() => setState((current) => startPlayback(current, totalDuration)), [totalDuration])
  const pause = useCallback(() => setState(pausePlayback), [])
  const resume = useCallback(() => setState(resumePlayback), [])
  const reset = useCallback(() => setState(resetPlayback), [])
  const seek = useCallback(
    (elapsedSeconds: number) => setState((current) => seekPlayback(current, elapsedSeconds, totalDuration)),
    [totalDuration],
  )
  const restart = useCallback(() => {
    setState((current) => totalDuration > 0
      ? { ...current, status: 'running', elapsedSeconds: 0 }
      : resetPlayback(current))
  }, [totalDuration])
  const setRate = useCallback((rate: 1 | 2 | 4) => setState((current) => ({ ...current, rate })), [])

  return { state, start, pause, resume, reset, seek, restart, setRate }
}
