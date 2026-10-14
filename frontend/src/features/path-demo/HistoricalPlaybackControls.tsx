import { PathPlaybackController } from './PathPlaybackController'
import { stepPlaybackTime } from './pathPlayback'
import type { PlaybackState } from './types'
import './HistoricalPlaybackControls.css'

function formatSeconds(value: number) {
  return `${value.toFixed(2)} s`
}

export function HistoricalPlaybackControls({
  state,
  totalDuration,
  timeStepSeconds,
  onStart,
  onPause,
  onResume,
  onReset,
  onSeek,
  onRestart,
  onRateChange,
}: {
  state: PlaybackState
  totalDuration: number
  timeStepSeconds: number
  onStart: () => void
  onPause: () => void
  onResume: () => void
  onReset: () => void
  onSeek: (elapsedSeconds: number) => void
  onRestart: () => void
  onRateChange: (rate: 1 | 2 | 4) => void
}) {
  const canPlay = totalDuration > 0
  const progress = canPlay ? Math.min(100, (state.elapsedSeconds / totalDuration) * 100) : 0
  const canStep = canPlay && Number.isFinite(timeStepSeconds) && timeStepSeconds > 0
  const seekOneTick = (direction: -1 | 1) => {
    onPause()
    onSeek(stepPlaybackTime(state.elapsedSeconds, direction, timeStepSeconds, totalDuration))
  }

  return (
    <section className="historical-playback" aria-label="历史轨迹播放控制">
      <PathPlaybackController
        state={state}
        canPlay={canPlay}
        onStart={onStart}
        onPause={onPause}
        onResume={onResume}
        onReset={onReset}
        onRateChange={onRateChange}
      />
      <div className="historical-playback-heading">
        <strong>历史轨迹时间轴</strong>
        <span>{formatSeconds(state.elapsedSeconds)} / {formatSeconds(totalDuration)}</span>
      </div>
      <input
        className="historical-playback-slider"
        type="range"
        aria-label="历史轨迹时间轴"
        min={0}
        max={totalDuration || 0}
        step="any"
        value={Math.min(state.elapsedSeconds, totalDuration)}
        disabled={!canPlay}
        onPointerDown={onPause}
        onKeyDown={onPause}
        onChange={(event) => onSeek(Number(event.target.value))}
      />
      <div className="historical-playback-progress" aria-label="历史轨迹播放进度">
        <span style={{ width: `${progress}%` }} />
      </div>
      <div className="historical-playback-steps">
        <button type="button" className="ghost-button" disabled={!canStep || state.elapsedSeconds <= 0} onClick={() => seekOneTick(-1)}>上一个时间片</button>
        <button type="button" className="ghost-button" disabled={!canStep || state.elapsedSeconds >= totalDuration} onClick={() => seekOneTick(1)}>下一个时间片</button>
        <button type="button" className="ghost-button" disabled={!canPlay} onClick={onRestart}>重头播放</button>
        <button type="button" className="ghost-button" disabled={!canPlay || state.elapsedSeconds >= totalDuration} onClick={() => { onPause(); onSeek(totalDuration) }}>跳到终点</button>
      </div>
      <p className="path-demo-note">时间片：{Number.isFinite(timeStepSeconds) && timeStepSeconds > 0 ? formatSeconds(timeStepSeconds) : '不可用'}；拖动或逐片查看会自动暂停。</p>
    </section>
  )
}
