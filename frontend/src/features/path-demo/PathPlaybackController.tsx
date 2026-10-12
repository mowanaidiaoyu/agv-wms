import type { PlaybackState } from './types'

export function PathPlaybackController({
  state,
  canPlay,
  onStart,
  onPause,
  onResume,
  onReset,
  onRateChange,
}: {
  state: PlaybackState
  canPlay: boolean
  onStart: () => void
  onPause: () => void
  onResume: () => void
  onReset: () => void
  onRateChange: (rate: 1 | 2 | 4) => void
}) {
  return (
    <div className="path-demo-playback-controls">
      <button className="accent-button" type="button" disabled={!canPlay || state.status === 'running'} onClick={onStart}>开始运行</button>
      <button className="ghost-button" type="button" disabled={state.status !== 'running'} onClick={onPause}>暂停</button>
      <button className="ghost-button" type="button" disabled={state.status !== 'paused'} onClick={onResume}>继续</button>
      <button className="ghost-button" type="button" disabled={!canPlay} onClick={onReset}>重置</button>
      <label>
        倍速
        <select value={state.rate} onChange={(event) => onRateChange(Number(event.target.value) as 1 | 2 | 4)}>
          <option value={1}>1x</option>
          <option value={2}>2x</option>
          <option value={4}>4x</option>
        </select>
      </label>
    </div>
  )
}
