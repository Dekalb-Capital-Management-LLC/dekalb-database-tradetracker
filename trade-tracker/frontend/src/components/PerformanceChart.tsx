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
  benchmarkSymbol?: string
}

function fmtDate(d: string) {
  return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export default function PerformanceChart({ data, benchmarkSymbol = 'SPY' }: Props) {
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

  const hasWatch = data.some((p) => p.watchlist_cumulative_pct != null)

  const purchaseMarks = data.flatMap((p) =>
    (p.purchase_markers ?? []).map((symbol) => ({ date: p.date, symbol })),
  )

  const chartData = data.map((p) => ({
    date: p.date,
    Portfolio: p.portfolio_cumulative_pct != null ? +Number(p.portfolio_cumulative_pct).toFixed(3) : null,
    'My stocks': p.watchlist_cumulative_pct != null
      ? +Number(p.watchlist_cumulative_pct).toFixed(3)
      : null,
    Benchmark: p.spy_cumulative_pct != null ? +Number(p.spy_cumulative_pct).toFixed(3) : null,
  }))

  // Shared Y-axis so portfolio / my stocks / SPY are comparable.
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
          tickFormatter={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`}
          tick={{ fill: '#6b7a99', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={58}
        />
        <ReferenceLine y={0} stroke="#d0dce8" strokeDasharray="4 2" />
        {purchaseMarks.map((m) => (
          <ReferenceLine
            key={`${m.date}-${m.symbol}`}
            x={m.date}
            stroke="#059669"
            strokeDasharray="3 3"
            strokeOpacity={0.55}
            label={{
              value: m.symbol,
              position: 'insideTopRight',
              fill: '#059669',
              fontSize: 10,
            }}
          />
        ))}
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
          labelFormatter={(label: string) => {
            const buys = purchaseMarks.filter((m) => m.date === label).map((m) => m.symbol)
            const base = fmtDate(label)
            return buys.length ? `${base} · bought ${buys.join(', ')}` : base
          }}
        />
        <Legend wrapperStyle={{ fontSize: 12, color: '#6b7a99', paddingTop: 12 }} />
        <Line
          type="monotone"
          dataKey="Portfolio"
          stroke="#2563eb"
          dot={false}
          strokeWidth={2}
          connectNulls
        />
        {hasWatch && (
          <Line
            type="monotone"
            dataKey="My stocks"
            stroke="#059669"
            dot={false}
            strokeWidth={2}
            connectNulls
          />
        )}
        <Line
          type="monotone"
          dataKey="Benchmark"
          name={benchmarkSymbol}
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
