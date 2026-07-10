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
      <div className="flex flex-col items-center justify-center h-64 gap-2" style={{ color: '#9ca3af' }}>
        <p className="text-sm">No performance data yet.</p>
        <p className="text-xs" style={{ color: '#c0ccd8' }}>
          Loading historical prices — this may take a moment on first load.
        </p>
      </div>
    )
  }

  const chartData = data.map((p) => ({
    date: p.date,
    Portfolio: p.portfolio_cumulative_pct != null ? +Number(p.portfolio_cumulative_pct).toFixed(3) : null,
    SPY: p.spy_cumulative_pct != null ? +Number(p.spy_cumulative_pct).toFixed(3) : null,
  }))

  // Dual Y-axes so SPY (~±10%) isn't flattened to the zero line when the
  // portfolio axis spans hundreds of percent.
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={chartData} margin={{ top: 5, right: 20, left: 5, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e8edf5" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={fmtDate}
          tick={{ fill: '#9ca3af', fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: '#e8edf5' }}
          interval="preserveStartEnd"
          minTickGap={60}
        />
        <YAxis
          yAxisId="portfolio"
          tickFormatter={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`}
          tick={{ fill: '#2563eb', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={58}
        />
        <YAxis
          yAxisId="spy"
          orientation="right"
          tickFormatter={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`}
          tick={{ fill: '#f97316', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={50}
        />
        <ReferenceLine yAxisId="portfolio" y={0} stroke="#d0dce8" strokeDasharray="4 2" />
        <Tooltip
          contentStyle={{
            backgroundColor: '#ffffff',
            border: '1px solid #d0dce8',
            borderRadius: 8,
            fontSize: 12,
            boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
          }}
          labelStyle={{ color: '#6b7a99', marginBottom: 4 }}
          formatter={(value: number, name: string) => [
            `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`,
            name,
          ]}
          labelFormatter={fmtDate}
        />
        <Legend wrapperStyle={{ fontSize: 12, color: '#6b7a99', paddingTop: 12 }} />
        <Line
          yAxisId="portfolio"
          type="monotone"
          dataKey="Portfolio"
          stroke="#2563eb"
          dot={false}
          strokeWidth={2}
          connectNulls
        />
        <Line
          yAxisId="spy"
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
