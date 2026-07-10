export function HeatmapGrid({
  cells,
}: {
  cells: Array<{ label: string; value: number }>
}) {
  const max = Math.max(...cells.map((cell) => cell.value), 1)
  return (
    <div className="heatmap-grid">
      {cells.map((cell) => {
        const ratio = cell.value / max
        return (
          <div
            key={cell.label}
            className="heatmap-cell"
            style={{ background: `rgba(249, 115, 22, ${0.12 + ratio * 0.72})` }}
          >
            <span>{cell.label}</span>
            <strong>{cell.value}</strong>
          </div>
        )
      })}
    </div>
  )
}
