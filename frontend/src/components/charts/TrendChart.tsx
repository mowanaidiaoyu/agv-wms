import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

export function TrendChart({
  data,
  dataKey,
  color = '#F97316',
}: {
  data: Array<Record<string, number | string>>
  dataKey: string
  color?: string
}) {
  return (
    <div className="chart-frame">
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#d7dee7" />
          <XAxis dataKey="tick" stroke="#64748B" />
          <YAxis stroke="#64748B" />
          <Tooltip />
          <Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
