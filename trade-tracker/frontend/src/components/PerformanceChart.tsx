import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts'
import type { PerformancePoint } from '../types'

interface Props {
  data: PerformancePoint[]
}

function fmtDate(d: string) {
  return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export default function PerformanceChart({ data }: Props) {
  if (!data.length) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-gray-500 text-sm gap-2">
        <p>No performance data yet.</p>
        <p className="text-xs text-gray-600">
          Call <code className="text-gray-500">POST /portfolio/snapshots/generate</code> to generate your first snapshot.
        </p>
      </div>
    )
  }

  const chartData = data.map((p) => ({
    date: p.date,
    Portfolio: p.portfolio_cumulative_pct != null ? +Number(p.portfolio_cumulative_pct).toFixed(3) : null,
    SPY: p.spy_cumulative_pct != null ? +Number(p.spy_cumulative_pct).toFixed(3) : null,
  }))

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={chartData} margin={{ top: 5, right: 20, left: 5, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1a2030" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={fmtDate}
          tick={{ fill: '#475569', fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: '#1a2030' }}
          interval="preserveStartEnd"
          minTickGap={60}
        />
        <YAxis
          tickFormatter={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`}
          tick={{ fill: '#475569', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={58}
        />
        <ReferenceLine y={0} stroke="#1e293b" strokeDasharray="4 2" />
        <Tooltip
          contentStyle={{
            backgroundColor: '#0d1117',
            border: '1px solid #1a2030',
            borderRadius: 6,
            fontSize: 12,
          }}
          labelStyle={{ color: '#64748b', marginBottom: 4 }}
          formatter={(value: number, name: string) => [
            `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`,
            name,
          ]}
          labelFormatter={fmtDate}
        />
        <Legend wrapperStyle={{ fontSize: 12, color: '#64748b', paddingTop: 12 }} />
        <Line
          type="monotone"
          dataKey="Portfolio"
          stroke="#60a5fa"
          dot={false}
          strokeWidth={2}
          connectNulls
        />
        <Line
          type="monotone"
          dataKey="SPY"
          stroke="#f97316"
          dot={false}
          strokeWidth={1.5}
          strokeDasharray="5 3"
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
