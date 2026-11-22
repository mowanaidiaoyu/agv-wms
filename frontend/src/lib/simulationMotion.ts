export function advanceInterpolationElapsed(
  elapsed: number,
  delta: number,
  duration: number,
  paused: boolean,
): number {
  if (paused) return elapsed
  return Math.min(Math.max(duration, 0.001), elapsed + Math.max(0, delta))
}

export function interpolationProgress(elapsed: number, duration: number): number {
  const linear = Math.min(1, Math.max(0, elapsed / Math.max(duration, 0.001)))
  return linear * linear * (3 - 2 * linear)
}

export function interpolateScalar(from: number, to: number, progress: number): number {
  return from + (to - from) * Math.min(1, Math.max(0, progress))
}

export function conveyorPositionOnRoute(
  blocks: [number, number, number][],
  cursor: number,
): [number, number, number] {
  const index = Math.min(blocks.length - 1, Math.max(0, Math.floor(cursor)))
  const from = blocks[index]
  const to = blocks[index + 1] ?? from
  const progress = cursor - index
  return [
    interpolateScalar(from[0], to[0], progress),
    interpolateScalar(from[1], to[1], progress),
    interpolateScalar(from[2], to[2], progress),
  ]
}
