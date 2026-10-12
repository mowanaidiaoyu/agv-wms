import type { ConveyorDirection, EditorObjectType } from '../types'

type TopologyGlyphProps = {
  kind: EditorObjectType
  size?: 'card' | 'panel' | 'board'
  direction?: ConveyorDirection
  className?: string
}

export function TopologyGlyph({
  kind,
  size = 'board',
  direction = 'right',
  className = '',
}: TopologyGlyphProps) {
  return (
    <span
      className={`topology-glyph topology-glyph-${kind} topology-glyph-${size} ${className}`.trim()}
      data-direction={direction}
      aria-hidden="true"
    >
      {kind === 'select' ? (
        <>
          <span className="topology-glyph-line topology-glyph-line-top" />
          <span className="topology-glyph-line topology-glyph-line-right" />
          <span className="topology-glyph-line topology-glyph-line-bottom" />
          <span className="topology-glyph-line topology-glyph-line-left" />
          <span className="topology-glyph-center-dot" />
        </>
      ) : null}

      {kind === 'batch' ? (
        <>
          <span className="topology-glyph-line topology-glyph-line-top" />
          <span className="topology-glyph-line topology-glyph-line-right" />
          <span className="topology-glyph-line topology-glyph-line-bottom" />
          <span className="topology-glyph-line topology-glyph-line-left" />
        </>
      ) : null}

      {kind === 'erase' ? (
        <>
          <span className="topology-glyph-cross topology-glyph-cross-a" />
          <span className="topology-glyph-cross topology-glyph-cross-b" />
        </>
      ) : null}

      {kind === 'road' ? <span className="topology-glyph-road-lane" /> : null}

      {kind === 'blocked' ? (
        <>
          <span className="topology-glyph-cross topology-glyph-cross-a" />
          <span className="topology-glyph-cross topology-glyph-cross-b" />
          <span className="topology-glyph-block-bar" />
        </>
      ) : null}

      {kind === 'conveyor' ? (
        <>
          <span className="topology-glyph-conveyor-track" />
          <span className="topology-glyph-conveyor-arrow" />
        </>
      ) : null}

      {kind === 'storage' ? (
        <>
          <span className="topology-glyph-storage-frame" />
          <span className="topology-glyph-storage-slot topology-glyph-storage-slot-a" />
          <span className="topology-glyph-storage-slot topology-glyph-storage-slot-b" />
          <span className="topology-glyph-storage-slot topology-glyph-storage-slot-c" />
          <span className="topology-glyph-storage-slot topology-glyph-storage-slot-d" />
        </>
      ) : null}

      {kind === 'station' ? (
        <>
          <span className="topology-glyph-station-bay" />
          <span className="topology-glyph-station-deck" />
        </>
      ) : null}

      {kind === 'buffer' ? (
        <>
          <span className="topology-glyph-buffer-layer topology-glyph-buffer-layer-top" />
          <span className="topology-glyph-buffer-layer topology-glyph-buffer-layer-bottom" />
        </>
      ) : null}

      {kind === 'entry' ? (
        <>
          <span className="topology-glyph-entry-frame" />
          <span className="topology-glyph-entry-arrow" />
        </>
      ) : null}

      {kind === 'conveyor_gate' ? (
        <>
          <span className="topology-glyph-gate-post topology-glyph-gate-post-left" />
          <span className="topology-glyph-gate-post topology-glyph-gate-post-right" />
          <span className="topology-glyph-gate-bar" />
          <span className="topology-glyph-gate-arrow" />
        </>
      ) : null}

      {kind === 'intersection' ? (
        <>
          <span className="topology-glyph-intersection-bar topology-glyph-intersection-bar-x" />
          <span className="topology-glyph-intersection-bar topology-glyph-intersection-bar-y" />
        </>
      ) : null}

      {kind === 'ctu_robot' ? (
        <>
          <span className="topology-glyph-ctu-body" />
          <span className="topology-glyph-ctu-cabin" />
          <span className="topology-glyph-ctu-wheel topology-glyph-ctu-wheel-top" />
          <span className="topology-glyph-ctu-wheel topology-glyph-ctu-wheel-bottom" />
          <span className="topology-glyph-ctu-sensor" />
        </>
      ) : null}

      {kind === 'shuttle_robot' ? (
        <>
          <span className="topology-glyph-shuttle-rail topology-glyph-shuttle-rail-top" />
          <span className="topology-glyph-shuttle-rail topology-glyph-shuttle-rail-bottom" />
          <span className="topology-glyph-shuttle-body" />
          <span className="topology-glyph-shuttle-carriage" />
          <span className="topology-glyph-shuttle-indicator" />
        </>
      ) : null}

      {kind === 'lift' ? (
        <>
          <span className="topology-glyph-lift-shaft" />
          <span className="topology-glyph-lift-arrow topology-glyph-lift-arrow-up" />
          <span className="topology-glyph-lift-arrow topology-glyph-lift-arrow-down" />
        </>
      ) : null}

      {kind === 'lift_transfer' ? (
        <>
          <span className="topology-glyph-lift-transfer-deck" />
          <span className="topology-glyph-lift-transfer-arrow topology-glyph-lift-transfer-arrow-up" />
          <span className="topology-glyph-lift-transfer-arrow topology-glyph-lift-transfer-arrow-down" />
        </>
      ) : null}

      {kind === 'turn_transfer' ? (
        <>
          <span className="topology-glyph-turn-transfer-ring" />
          <span className="topology-glyph-turn-transfer-arm topology-glyph-turn-transfer-arm-a" />
          <span className="topology-glyph-turn-transfer-arm topology-glyph-turn-transfer-arm-b" />
        </>
      ) : null}
    </span>
  )
}
