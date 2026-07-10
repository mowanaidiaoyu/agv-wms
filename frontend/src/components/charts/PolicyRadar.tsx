import { PolarAngleAxis, PolarGrid, Radar, RadarChart, ResponsiveContainer, Tooltip } from 'recharts'

interface RadarSeries {
  key: string
  name: string
  color: string
}

export function PolicyRadar({
  data,
  series,
}: {
  data: Array<Record<string, number | string>>
  series: RadarSeries[]
}) {
  return (
    <div className="chart-frame">
      <ResponsiveContainer width="100%" height={280}>
        <RadarChart data={data}>
          <PolarGrid stroke="#c8d3df" />
          <PolarAngleAxis dataKey="metric" tick={{ fill: '#334155', fontSize: 12 }} />
          {series.map((item) => (
            <Radar key={item.key} name={item.name} dataKey={item.key} stroke={item.color} fill={item.color} fillOpacity={0.2} />
          ))}
          <Tooltip />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}
