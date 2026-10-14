import type { MultiPathConflictEvent, PlaybackRate } from './types'

export const AUTO_SLOW_PLAYBACK_RATE: PlaybackRate = 0.25

export function primaryNodeConflict(events: MultiPathConflictEvent[]): MultiPathConflictEvent | null {
  return events
    .filter((event) => event.conflict_type === 'node')
    .sort((first, second) => first.time_step - second.time_step || first.event_id.localeCompare(second.event_id))[0] ?? null
}
export function conflictSeekTime(event: MultiPathConflictEvent, timeStepSeconds: number): number {
  return Math.max(0, event.time_seconds - timeStepSeconds)
}

export function isConflictSlowWindow(
  event: MultiPathConflictEvent | null,
  elapsedSeconds: number,
  timeStepSeconds: number,
): boolean {
  if (!event || timeStepSeconds <= 0) return false
  const start = Math.max(0, event.time_seconds - timeStepSeconds)
  const resolvedAt = event.wait_end_time_seconds ?? event.time_seconds
  const end = resolvedAt + timeStepSeconds
  return elapsedSeconds >= start && elapsedSeconds < end
}

export function resolveConflictPlaybackRate(
  preferredRate: PlaybackRate,
  autoSlowEnabled: boolean,
  event: MultiPathConflictEvent | null,
  elapsedSeconds: number,
  timeStepSeconds: number,
): PlaybackRate {
  return autoSlowEnabled && isConflictSlowWindow(event, elapsedSeconds, timeStepSeconds)
    ? AUTO_SLOW_PLAYBACK_RATE
    : preferredRate
}
