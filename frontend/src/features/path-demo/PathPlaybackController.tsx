import type { PlaybackRate, PlaybackState } from './types'

export function PathPlaybackController({
  state,
  canPlay,
  onStart,
  onPause,
  onResume,
  onReset,
  onRateChange,
  rates = [0.25, 0.5, 1, 2, 4],
}: {
  state: PlaybackState
  canPlay: boolean
  onStart: () => void
  onPause: () => void
  onResume: () => void
  onReset: () => void
  onRateChange: (rate: PlaybackRate) => void
  rates?: readonly PlaybackRate[]
}) {
  return (
    <div className="path-demo-playback-controls">
      <button className="accent-button" type="button" disabled={!canPlay || state.status === 'running'} onClick={onStart}>开始运行</button>
      <button className="ghost-button" type="button" disabled={state.status !== 'running'} onClick={onPause}>暂停</button>
      <button className="ghost-button" type="button" disabled={state.status !== 'paused'} onClick={onResume}>继续</button>
      <button className="ghost-button" type="button" disabled={!canPlay} onClick={onReset}>重置</button>
      <label>
        倍速
        <select aria-label="播放速度" value={state.rate} onChange={(event) => onRateChange(Number(event.target.value) as PlaybackRate)}>
          {rates.map((rate) => <option key={rate} value={rate}>{rate}x</option>)}
        </select>
      </label>
    </div>
  )
}
